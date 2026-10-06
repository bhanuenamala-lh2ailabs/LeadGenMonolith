"""Read-only HubSpot REST client (requests, Bearer auth).

HARD RULES enforced here, before any byte is sent
    * Only ``GET`` is ever allowed, plus ``POST`` to the read-only endpoints: ``.../search``, ``.../batch/read`` (v3 objects and v4
      associations).  ``assert_read_only()`` is the single gate; PATCH / PUT / DELETE / any other POST raise :class:`ReadOnlyViolation`
      without touching the network.  Paths must be relative API paths (``/crm/v3/...``), never absolute URLs.
    * The token is fetched lazily through ``leadgen.config.get_secret(<env key of the account>)`` and only ever placed in the
      Authorization header.  Error messages and log lines are passed through ``leadgen.config.redact`` and never contain the header.
    * Politeness: at most ``max_requests`` per ``window`` seconds (default 100 per 10 s) across all calls of the client; search
      endpoints are additionally capped (HubSpot allows ~5 search calls/s per account; we use 4).
    * 429 / 5xx / connection errors are retried with exponential backoff (honouring ``Retry-After``).

Python 3.9 compatible.
"""
import collections
import json
import re
import threading
import time
from typing import Any, Callable, Deque, Dict, Iterator, List, Optional

import requests

from leadgen import config as lgconfig

BASE_URL = "https://api.hubapi.com"

#: POST paths that only read: object search, v3 object batch read, v4 association batch read.
_READ_POST_RE = re.compile(
    r"^/crm/v3/objects/[a-z_0-9]+/(search|batch/read)$"
    r"|^/crm/v4/associations/[a-z_0-9]+/[a-z_0-9]+/batch/read$"
    r"|^/crm/v3/associations/[a-z_0-9]+/[a-z_0-9]+/batch/read$"
)
_PATH_RE = re.compile(r"^/[A-Za-z0-9_\-./:]*$")


class HubSpotError(RuntimeError):
    """A failed HubSpot call.  ``status`` is the HTTP status (None for transport errors); ``category`` is the HubSpot error category."""

    def __init__(self, message: str, status: Optional[int] = None, path: Optional[str] = None, category: Optional[str] = None):
        super().__init__(message)
        self.status = status
        self.path = path
        self.category = category


class ReadOnlyViolation(HubSpotError):
    """Raised BEFORE sending when a request would not be read-only."""


class MissingScope(HubSpotError):
    """403 with a MISSING_SCOPES category (the key may not read this object type)."""


def assert_read_only(method: str, path: str) -> None:
    """Raise ReadOnlyViolation unless (method, path) is a GET, or a POST to a search / batch-read endpoint."""
    if not isinstance(method, str) or not isinstance(path, str):
        raise ReadOnlyViolation("method and path must be strings")
    m = method.upper()
    bare = path.split("?", 1)[0]
    if "://" in path or not path.startswith("/") or "\\" in path or ".." in bare or not _PATH_RE.match(bare):
        raise ReadOnlyViolation("refusing request: path %r is not a plain relative API path" % path[:80])
    if m == "GET":
        return
    if m == "POST" and _READ_POST_RE.match(bare):
        return
    raise ReadOnlyViolation("refusing %s %s: the monolith is read-only towards HubSpot (GET, /search and batch/read only)" % (m, bare))


def is_search_path(path: str) -> bool:
    return path.split("?", 1)[0].endswith("/search")


class RateLimiter:
    """Sliding-window limiter: at most ``max_requests`` calls per ``window`` seconds.  Thread safe."""

    def __init__(self, max_requests: int = 100, window: float = 10.0,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self.max_requests = max_requests
        self.window = window
        self._clock = clock
        self._sleep = sleep
        self._stamps = collections.deque()  # type: Deque[float]
        self._lock = threading.Lock()

    def acquire(self) -> float:
        """Block until a slot is free; returns the seconds waited."""
        waited = 0.0
        while True:
            with self._lock:
                now = self._clock()
                while self._stamps and now - self._stamps[0] >= self.window:
                    self._stamps.popleft()
                if len(self._stamps) < self.max_requests:
                    self._stamps.append(now)
                    return waited
                delay = self.window - (now - self._stamps[0]) + 0.001
            self._sleep(delay)
            waited += delay


class HubSpotClient:
    """Thin read-only client for one portal.

    ``account`` is a slug from config/accounts.yaml (the token variable NAME comes from there).  ``token`` may be passed for tests;
    ``session`` can be a fake with a ``request(method, url, **kw)`` method.
    """

    def __init__(self, account: str, token: Optional[str] = None, session: Any = None, base_url: str = BASE_URL,
                 max_requests: int = 100, window: float = 10.0, search_per_second: float = 4.0,
                 max_retries: int = 6, backoff_base: float = 1.0, backoff_cap: float = 60.0, timeout: float = 60.0,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self.account = account
        self._token = token
        self._session = session
        self.base_url = base_url.rstrip("/")
        self.limiter = RateLimiter(max_requests, window, clock, sleep)
        self.search_limiter = RateLimiter(max(1, int(search_per_second)), 1.0, clock, sleep)
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.backoff_cap = backoff_cap
        self.timeout = timeout
        self._sleep = sleep
        self.calls = 0
        self.retries = 0
        self.log = None  # type: Optional[Callable[[str], None]]

    # -------------------------------------------------------------------------------------------- plumbing
    def _tok(self) -> str:
        if self._token is None:
            self._token = lgconfig.get_account(self.account).token()
        return self._token

    def _sess(self) -> Any:
        if self._session is None:
            self._session = requests.Session()
        return self._session

    def _say(self, msg: str) -> None:
        if self.log:
            self.log(lgconfig.redact(msg))

    @staticmethod
    def _retry_after(resp: Any) -> Optional[float]:
        try:
            v = resp.headers.get("Retry-After")
            return float(v) if v is not None else None
        except (TypeError, ValueError, AttributeError):
            return None

    def _backoff(self, attempt: int, retry_after: Optional[float]) -> float:
        d = min(self.backoff_cap, self.backoff_base * (2 ** attempt))
        if retry_after is not None:
            d = max(d, min(retry_after, 120.0))
        return d

    def request(self, method: str, path: str, params: Optional[Dict[str, Any]] = None, json_body: Any = None) -> Dict[str, Any]:
        """One guarded, rate-limited, retried call.  Returns the decoded JSON ({} for an empty body)."""
        assert_read_only(method, path)                                  # <- raises BEFORE anything is sent
        method = method.upper()
        url = self.base_url + path
        attempt = 0
        while True:
            self.limiter.acquire()
            if is_search_path(path):
                self.search_limiter.acquire()
            headers = {"Authorization": "Bearer " + self._tok(), "Accept": "application/json"}
            kwargs = {"headers": headers, "timeout": self.timeout}  # type: Dict[str, Any]
            if params:
                kwargs["params"] = params
            if json_body is not None:
                kwargs["json"] = json_body
            self.calls += 1
            try:
                resp = self._sess().request(method, url, **kwargs)
            except requests.RequestException as exc:
                if attempt >= self.max_retries:
                    raise HubSpotError("transport error after %d retries on %s %s: %s" % (attempt, method, path, lgconfig.redact(str(exc))), None, path)
                self.retries += 1
                d = self._backoff(attempt, None)
                self._say("transport error on %s %s, retry in %.1fs" % (method, path, d))
                self._sleep(d)
                attempt += 1
                continue
            status = resp.status_code
            if status in (429, 500, 502, 503, 504):
                if attempt >= self.max_retries:
                    raise HubSpotError("HTTP %d on %s %s after %d retries" % (status, method, path, attempt), status, path)
                self.retries += 1
                d = self._backoff(attempt, self._retry_after(resp))
                self._say("HTTP %d on %s %s, retry in %.1fs" % (status, method, path, d))
                self._sleep(d)
                attempt += 1
                continue
            if status in (200, 201, 207):
                if not resp.content:
                    return {}
                try:
                    return resp.json()
                except ValueError:
                    raise HubSpotError("non-JSON body on %s %s" % (method, path), status, path)
            body = {}  # type: Any
            try:
                body = resp.json()
            except ValueError:
                pass
            cat = body.get("category") if isinstance(body, dict) else None
            msg = body.get("message") if isinstance(body, dict) else None
            text = lgconfig.redact("HTTP %d on %s %s [%s] %s" % (status, method, path, cat, (msg or "")[:300]))
            if status == 403 and cat in ("MISSING_SCOPES", "FORBIDDEN"):
                raise MissingScope(text, status, path, cat)
            if status == 403 and isinstance(msg, str) and "scope" in msg.lower():
                raise MissingScope(text, status, path, cat)
            raise HubSpotError(text, status, path, cat)

    # -------------------------------------------------------------------------------------------- verbs
    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self.request("GET", path, params=params)

    def post_read(self, path: str, body: Any) -> Dict[str, Any]:
        """POST to a search / batch-read endpoint (anything else raises ReadOnlyViolation)."""
        return self.request("POST", path, json_body=body)

    # -------------------------------------------------------------------------------------------- pagination
    def paginate_get(self, path: str, params: Optional[Dict[str, Any]] = None, limit: int = 100,
                     max_pages: Optional[int] = None) -> Iterator[List[Dict[str, Any]]]:
        """Yield one list of results per page of a GET list endpoint, following ``paging.next.after`` (no 10k cap)."""
        p = dict(params or {})
        p["limit"] = limit
        after = None
        pages = 0
        while True:
            if after is not None:
                p["after"] = after
            data = self.get(path, p)
            res = data.get("results") or []
            if res:
                yield res
            pages += 1
            nxt = (data.get("paging") or {}).get("next") or {}
            after = nxt.get("after")
            if not after or (max_pages is not None and pages >= max_pages):
                return

    def paginate_search(self, object_type: str, body: Dict[str, Any], limit: int = 100) -> Iterator[List[Dict[str, Any]]]:
        """Yield pages of a search.  HubSpot caps one search at 10,000 results: callers slice by date (see ``search_windowed``)."""
        b = dict(body)
        b["limit"] = limit
        after = None
        got = 0
        while True:
            if after is not None:
                b["after"] = after
            data = self.post_read("/crm/v3/objects/%s/search" % object_type, b)
            res = data.get("results") or []
            got += len(res)
            if res:
                yield res
            nxt = (data.get("paging") or {}).get("next") or {}
            after = nxt.get("after")
            if not after:
                return
            if got >= 10000:
                raise HubSpotError("search on %s hit the 10,000 result cap; slice the query by date" % object_type, None, object_type)

    def search_total(self, object_type: str, filters: Optional[List[Dict[str, Any]]] = None) -> int:
        """Independent count: ``total`` of a search with the given AND-ed filters (limit 1)."""
        body = {"limit": 1}  # type: Dict[str, Any]
        if filters:
            body["filterGroups"] = [{"filters": filters}]
        return int(self.post_read("/crm/v3/objects/%s/search" % object_type, body).get("total", 0))

    def search_windowed(self, object_type: str, prop: str, start_ms: int, end_ms: int, properties: List[str],
                        extra_filters: Optional[List[Dict[str, Any]]] = None, cap: int = 9500) -> Iterator[List[Dict[str, Any]]]:
        """Yield search result pages for ``start_ms <= prop < end_ms``, splitting the window in two while it holds more than ``cap`` hits
        (the search API cannot page past 10,000 results)."""
        flt = [{"propertyName": prop, "operator": "GTE", "value": str(start_ms)},
               {"propertyName": prop, "operator": "LT", "value": str(end_ms)}] + list(extra_filters or [])
        body = {"filterGroups": [{"filters": flt}], "properties": properties,
                "sorts": [{"propertyName": prop, "direction": "ASCENDING"}]}
        total = int(self.post_read("/crm/v3/objects/%s/search" % object_type, dict(body, limit=1)).get("total", 0))
        if total == 0:
            return
        if total > cap and end_ms - start_ms > 1:
            mid = (start_ms + end_ms) // 2
            for page in self.search_windowed(object_type, prop, start_ms, mid, properties, extra_filters, cap):
                yield page
            for page in self.search_windowed(object_type, prop, mid, end_ms, properties, extra_filters, cap):
                yield page
            return
        for page in self.paginate_search(object_type, body):
            yield page

    def batch_read(self, object_type: str, ids: List[str], properties: List[str], properties_with_history: Optional[List[str]] = None,
                   archived: bool = False) -> List[Dict[str, Any]]:
        """POST /crm/v3/objects/<type>/batch/read for up to 100 ids (50 when history is requested)."""
        per = 50 if properties_with_history else 100
        out = []  # type: List[Dict[str, Any]]
        for i in range(0, len(ids), per):
            chunk = ids[i:i + per]
            body = {"inputs": [{"id": str(x)} for x in chunk], "properties": properties, "archived": archived}  # type: Dict[str, Any]
            if properties_with_history:
                body["propertiesWithHistory"] = properties_with_history
            data = self.post_read("/crm/v3/objects/%s/batch/read" % object_type, body)
            out.extend(data.get("results") or [])
        return out

    def batch_read_associations(self, from_type: str, to_type: str, ids: List[str], per_call: int = 100) -> Dict[str, List[Dict[str, Any]]]:
        """v4 association batch read.  Returns {from_id: [{"id": to_id, "types": [{"category","typeId","label"}...]}, ...]} (ids absent
        from the response simply have no association)."""
        out = {}  # type: Dict[str, List[Dict[str, Any]]]
        path = "/crm/v4/associations/%s/%s/batch/read" % (from_type, to_type)
        for i in range(0, len(ids), per_call):
            chunk = ids[i:i + per_call]
            data = self.post_read(path, {"inputs": [{"id": str(x)} for x in chunk]})
            for r in data.get("results") or []:
                fid = str((r.get("from") or {}).get("id"))
                out[fid] = normalize_assoc([{"id": str(t.get("toObjectId")),
                                             "types": [{"category": a.get("category"), "typeId": a.get("typeId"), "label": a.get("label")}
                                                       for a in (t.get("associationTypes") or [])]}
                                            for t in (r.get("to") or [])])
        return out


def normalize_assoc(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deterministic order for an association list: HubSpot returns both the target objects and each target's association types in a
    varying order, which would otherwise look like a payload change on every refresh."""
    out = [{"id": i["id"], "types": sorted(i.get("types") or [], key=lambda t: (str(t.get("category")), t.get("typeId") or 0, str(t.get("label"))))}
           for i in items]
    out.sort(key=lambda i: (len(i["id"]), i["id"]))
    return out


def dumps(obj: Any) -> str:
    """Canonical JSON text (sorted keys, compact, non-ASCII kept) used for hashing and storage."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
