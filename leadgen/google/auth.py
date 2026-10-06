"""Google credentials, a READ-ONLY call guard, rate limiting and quota-aware retries for the Sheets / Drive / Gmail pulls.

Rules enforced here (docs/ARCHITECTURE.md section 3, docs/GOOGLE_PULLS.md):

* **Read only.**  Our OAuth tokens for Sheets carry the full ``spreadsheets`` scope (write capable) and we must never use it.  Every
  service the pulls use is wrapped in :class:`ReadOnlyGuard`, an ALLOW-list proxy: only the exact call chains listed in
  :data:`ALLOWED_CALLS` can be reached, any other attribute (a mutating method, a typo, a new API) raises :class:`WriteBlocked` before a
  request object even exists.  The guard is the second line of defence; the first is that the pull modules simply never name such methods.
* **No impersonation.**  The service account is used as itself (:func:`service_account_credentials` has no ``subject`` parameter).
* **Secrets never leave the secrets files.**  A refreshed access token is written back ONLY to the token file it came from (atomic
  replace, chmod 600).  Nothing here prints or logs a token, a refresh token or a client secret; :func:`token_status` reports booleans,
  scopes and expiry only.
* **Quota aware.**  :class:`RateLimiter` spaces calls (Sheets: 60 reads / min / user / project) and :func:`execute` retries 429 / 5xx /
  rate-limit 403s with exponential backoff + jitter (honouring ``Retry-After``).

Python 3.9 compatible.
"""
import datetime
import json
import os
import random
import socket
import ssl
import tempfile
import threading
import time
from typing import Any, Callable, Dict, FrozenSet, Iterable, List, Optional, Sequence, Tuple

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(PACKAGE_DIR))
SECRETS_DIR = os.path.join(PROJECT_ROOT, "secrets")

#: credential name -> file under secrets/ (the NAME is what ext_gsheet_catalog.visible_to_json stores, never the content)
SHEETS_CREDENTIALS = {
    "hubspot_sheets_token": "hubspot_sheets_token.json",
    "companyops_sheets_token": "companyops_sheets_token.json",
    "sa_lh2bot": "hubspot_service_account_lh2bot.json",
}
GMAIL_TOKEN_FILE = "gmail_readonly_token.json"

SCOPE_SHEETS_RO = "https://www.googleapis.com/auth/spreadsheets.readonly"
SCOPE_DRIVE_RO = "https://www.googleapis.com/auth/drive.readonly"
SCOPE_GMAIL_RO = "https://www.googleapis.com/auth/gmail.readonly"
#: scopes requested for the service account (it is only ever used read-only)
SA_SCOPES = (SCOPE_SHEETS_RO, SCOPE_DRIVE_RO)


class WriteBlocked(PermissionError):
    """A call outside the read-only allow-list was attempted."""


class TokenError(RuntimeError):
    """A credential file is missing / unusable / cannot be refreshed.  The message never contains a secret."""


# ------------------------------------------------------------------------------------------------- read-only guard
#: the ONLY API call chains the pulls may use.  Each entry is the attribute path from the service root; resource accessors
#: (``spreadsheets()``, ``values()`` ...) are implied by being a prefix of an entry.
ALLOWED_CALLS = frozenset([
    # Sheets v4
    ("spreadsheets", "get"),
    ("spreadsheets", "values", "get"),
    ("spreadsheets", "values", "batchGet"),
    # Drive v3 (metadata listing only)
    ("files", "list"),
    ("files", "get"),
    ("about", "get"),
    # Gmail v1 (gmail.readonly)
    ("users", "getProfile"),
    ("users", "labels", "list"),
    ("users", "labels", "get"),
    ("users", "messages", "list"),
    ("users", "messages", "get"),
    ("users", "messages", "attachments", "get"),
    ("users", "threads", "list"),
    ("users", "threads", "get"),
    ("users", "history", "list"),
    # transport helpers
    ("new_batch_http_request",),
    ("close",),
])  # type: FrozenSet[Tuple[str, ...]]


class ReadOnlyGuard(object):
    """Allow-list proxy around a googleapiclient ``Resource`` (or a test double with the same call shape)."""

    __slots__ = ("_target", "_path", "_allowed")

    def __init__(self, target: Any, path: Tuple[str, ...] = (), allowed: Iterable[Tuple[str, ...]] = ALLOWED_CALLS):
        object.__setattr__(self, "_target", target)
        object.__setattr__(self, "_path", tuple(path))
        object.__setattr__(self, "_allowed", frozenset(allowed))

    def __getattr__(self, name: str) -> Any:
        path = object.__getattribute__(self, "_path") + (name,)
        allowed = object.__getattribute__(self, "_allowed")
        target = object.__getattribute__(self, "_target")
        if name.startswith("_") or not any(a[:len(path)] == path for a in allowed):
            raise WriteBlocked("blocked: %s is not in the read-only allow-list" % ".".join(path))
        attr = getattr(target, name)
        if path in allowed:                      # a leaf we may call (builds a request; no network yet)
            return attr
        def _descend(*args: Any, **kwargs: Any) -> "ReadOnlyGuard":
            return ReadOnlyGuard(attr(*args, **kwargs), path, allowed)
        return _descend

    def __setattr__(self, name: str, value: Any) -> None:
        raise WriteBlocked("the read-only guard cannot be modified")


def guard(service: Any) -> ReadOnlyGuard:
    """Wrap ``service`` (idempotent)."""
    return service if isinstance(service, ReadOnlyGuard) else ReadOnlyGuard(service)


# ------------------------------------------------------------------------------------------------- credential files
def secrets_path(name: str) -> str:
    """Absolute path of a file under secrets/ (``name`` may already be absolute)."""
    return name if os.path.isabs(name) else os.path.join(SECRETS_DIR, name)


def _read_json(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        raise TokenError("credential file not found: %s" % os.path.relpath(path, PROJECT_ROOT))
    except ValueError:
        raise TokenError("credential file is not valid JSON: %s" % os.path.relpath(path, PROJECT_ROOT))
    if not isinstance(data, dict):
        raise TokenError("credential file is not a JSON object: %s" % os.path.relpath(path, PROJECT_ROOT))
    return data


def _parse_expiry(value: Any) -> Optional[datetime.datetime]:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.datetime.strptime(value.rstrip("Z").split("+")[0][:26], "%Y-%m-%dT%H:%M:%S.%f" if "." in value else "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def token_status(path: str, required_scope: Optional[str] = None, now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """OFFLINE description of an authorized-user token file: no network, no secret values (booleans / scopes / expiry only)."""
    path = secrets_path(path)
    rel = os.path.relpath(path, PROJECT_ROOT)
    out = {"file": rel, "exists": os.path.exists(path)}  # type: Dict[str, Any]
    if not out["exists"]:
        out["ok"] = False
        out["problem"] = "missing"
        return out
    mode = os.stat(path).st_mode & 0o777
    out["mode"] = oct(mode)
    out["mode_ok"] = (mode & 0o077) == 0
    try:
        data = _read_json(path)
    except TokenError as exc:
        out.update(ok=False, problem=str(exc))
        return out
    scopes = data.get("scopes") or ([data["scope"]] if isinstance(data.get("scope"), str) else [])
    if isinstance(scopes, str):
        scopes = scopes.split()
    out["account"] = data.get("account") or None
    out["scopes"] = list(scopes)
    out["has_refresh_token"] = bool(data.get("refresh_token"))
    out["has_client"] = bool(data.get("client_id")) and bool(data.get("client_secret"))
    exp = _parse_expiry(data.get("expiry"))
    now = now or datetime.datetime.utcnow()
    out["access_token_cached"] = bool(data.get("token"))
    out["access_token_expiry"] = exp.isoformat() + "Z" if exp else None
    out["access_token_valid_now"] = bool(data.get("token")) and bool(exp and exp > now)
    if required_scope is not None:
        out["has_required_scope"] = required_scope in scopes
    problems = []
    if not out["has_refresh_token"]:
        problems.append("no refresh_token")
    if not out["has_client"]:
        problems.append("client_id / client_secret missing")
    if required_scope is not None and not out["has_required_scope"]:
        problems.append("scope %s not granted" % required_scope)
    if not out["mode_ok"]:
        problems.append("file permissions are wider than 0600")
    out["ok"] = not problems
    if problems:
        out["problem"] = "; ".join(problems)
    return out


def persist_refreshed_token(path: str, token: Optional[str], expiry: Optional[datetime.datetime]) -> None:
    """Write a refreshed access token (and expiry) back into ``path`` - the file it was loaded from, nothing else - keeping every
    other key (refresh token, client, scopes, account ...) byte-for-byte as it was, atomically, with mode 0600."""
    path = secrets_path(path)
    data = _read_json(path)
    data["token"] = token
    if expiry is not None:
        data["expiry"] = expiry.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    fd, tmp = tempfile.mkstemp(prefix=".tok-", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    os.chmod(path, 0o600)


def user_credentials(token_file: str, required_scopes: Optional[Sequence[str]] = None, refresh: bool = True,
                     request_factory: Optional[Callable[[], Any]] = None) -> Any:
    """Load an authorized-user token file, refresh the access token IN MEMORY when it is stale and persist the new access token to the
    same file.  Raises :class:`TokenError` (never including secret values) when the file is unusable or the refresh grant fails."""
    path = secrets_path(token_file)
    st = token_status(path)
    if not st["exists"]:
        raise TokenError("token file missing: %s" % st["file"])
    from google.oauth2.credentials import Credentials
    from google.auth.exceptions import RefreshError, TransportError
    try:
        creds = Credentials.from_authorized_user_file(path)
    except (ValueError, KeyError) as exc:
        raise TokenError("token file %s is incomplete (%s)" % (st["file"], type(exc).__name__))
    for sc in required_scopes or ():
        if st.get("scopes") and sc not in st["scopes"]:
            raise TokenError("token %s lacks scope %s" % (st["file"], sc))
    if refresh and (not creds.valid):
        if request_factory is None:
            from google.auth.transport.requests import Request
            request_factory = Request
        try:
            creds.refresh(request_factory())
        except RefreshError as exc:
            raise TokenError("refresh grant rejected for %s: %s" % (st["file"], _short_refresh_error(exc)))
        except TransportError as exc:
            raise TokenError("network error refreshing %s: %s" % (st["file"], type(exc).__name__))
        persist_refreshed_token(path, creds.token, creds.expiry)
    return creds


def _short_refresh_error(exc: Exception) -> str:
    """'invalid_grant' etc. from a RefreshError without echoing the response body."""
    text = str(exc)
    for code in ("invalid_grant", "invalid_client", "unauthorized_client", "invalid_scope", "access_denied"):
        if code in text:
            return code
    return type(exc).__name__


def service_account_credentials(key_file: str, scopes: Sequence[str] = SA_SCOPES) -> Any:
    """The service account AS ITSELF, read-only scopes.  There is deliberately no way to pass a subject / delegate."""
    from google.oauth2 import service_account
    path = secrets_path(key_file)
    if not os.path.exists(path):
        raise TokenError("service account key missing: %s" % os.path.relpath(path, PROJECT_ROOT))
    try:
        return service_account.Credentials.from_service_account_file(path, scopes=list(scopes))
    except (ValueError, KeyError) as exc:
        raise TokenError("service account key unusable (%s)" % type(exc).__name__)


def sheets_credentials(name: str) -> Any:
    """Credentials for one of :data:`SHEETS_CREDENTIALS` (user token or the service account)."""
    if name not in SHEETS_CREDENTIALS:
        raise TokenError("unknown credential name %r" % name)
    if name == "sa_lh2bot":
        return service_account_credentials(SHEETS_CREDENTIALS[name])
    return user_credentials(SHEETS_CREDENTIALS[name])


def build_service(api: str, version: str, creds: Any) -> ReadOnlyGuard:
    """``googleapiclient`` service wrapped in the read-only guard, with a 120 s socket timeout and no discovery cache file."""
    import google_auth_httplib2
    import httplib2
    from googleapiclient.discovery import build
    http = google_auth_httplib2.AuthorizedHttp(creds, http=httplib2.Http(timeout=120))
    return guard(build(api, version, http=http, cache_discovery=False))


# ------------------------------------------------------------------------------------------------- rate limit / retries
class RateLimiter(object):
    """Minimum spacing between calls: ``per_minute`` calls per minute at most (thread safe; clock / sleep injectable for tests)."""

    def __init__(self, per_minute: float, clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self.interval = 60.0 / float(per_minute) if per_minute and per_minute > 0 else 0.0
        self._clock = clock
        self._sleep = sleep
        self._next = 0.0
        self._lock = threading.Lock()
        self.calls = 0

    def wait(self) -> float:
        """Block until the next slot; returns the seconds slept."""
        with self._lock:
            now = self._clock()
            slot = max(now, self._next)
            self._next = slot + self.interval
            self.calls += 1
        delay = slot - now
        if delay > 0:
            self._sleep(delay)
        return max(delay, 0.0)

    def penalty(self, seconds: float) -> None:
        """After a 429: push the next slot out."""
        with self._lock:
            self._next = max(self._next, self._clock() + seconds)


RETRY_STATUS = frozenset([429, 500, 502, 503, 504])
RETRY_403_REASONS = frozenset(["rateLimitExceeded", "userRateLimitExceeded", "sharingRateLimitExceeded", "quotaExceeded", "RATE_LIMIT_EXCEEDED"])
TRANSPORT_ERRORS = (socket.timeout, TimeoutError, ConnectionError, ssl.SSLError, BrokenPipeError)


def http_error_details(exc: Any) -> Tuple[int, str, str]:
    """(status, first reason, message) of a googleapiclient HttpError, tolerant of odd bodies."""
    status = int(getattr(getattr(exc, "resp", None), "status", 0) or 0)
    reason, message = "", ""
    try:
        body = json.loads(getattr(exc, "content", b"") or b"{}")
        err = body.get("error", {}) if isinstance(body, dict) else {}
        if isinstance(err, dict):
            message = str(err.get("message", ""))
            errs = err.get("errors") or []
            if errs and isinstance(errs[0], dict):
                reason = str(errs[0].get("reason", ""))
            reason = reason or str(err.get("status", ""))
    except (ValueError, AttributeError):
        pass
    return status, reason, message


def is_retryable(exc: BaseException) -> bool:
    from googleapiclient.errors import HttpError
    if isinstance(exc, HttpError):
        status, reason, _ = http_error_details(exc)
        return status in RETRY_STATUS or (status == 403 and reason in RETRY_403_REASONS)
    try:
        import httplib2
        if isinstance(exc, httplib2.HttpLib2Error):
            return True
    except ImportError:  # pragma: no cover
        pass
    return isinstance(exc, TRANSPORT_ERRORS)


def backoff_delay(attempt: int, base: float = 5.0, cap: float = 90.0, retry_after: Optional[float] = None,
                  rand: Callable[[], float] = random.random) -> float:
    """Exponential backoff with +-25 % jitter: base * 2**attempt, capped; a server ``Retry-After`` wins when larger."""
    d = min(cap, base * (2 ** attempt))
    d = d * (0.75 + 0.5 * rand())
    return max(d, retry_after or 0.0)


def execute(request: Any, limiter: Optional[RateLimiter] = None, retries: int = 8, base: float = 5.0, cap: float = 90.0,
            sleep: Callable[[float], None] = time.sleep, on_retry: Optional[Callable[[int, float, BaseException], None]] = None) -> Any:
    """``request.execute()`` through the rate limiter, retrying quota / transient errors with exponential backoff.
    Non-retryable errors (400/401/403 permission/404 ...) propagate immediately."""
    attempt = 0
    while True:
        if limiter is not None:
            limiter.wait()
        try:
            return request.execute()
        except BaseException as exc:  # noqa: B902 - re-raised unless retryable
            if not is_retryable(exc) or attempt >= retries:
                raise
            ra = None
            hdr = getattr(getattr(exc, "resp", None), "get", None)
            if callable(hdr):
                try:
                    ra = float(hdr("retry-after")) if hdr("retry-after") else None
                except (TypeError, ValueError):
                    ra = None
            delay = backoff_delay(attempt, base=base, cap=cap, retry_after=ra)
            if limiter is not None:
                limiter.penalty(delay)
            if on_retry is not None:
                on_retry(attempt, delay, exc)
            sleep(delay)
            attempt += 1
