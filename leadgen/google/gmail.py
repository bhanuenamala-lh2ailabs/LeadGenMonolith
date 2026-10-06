"""Gmail pull (READ-ONLY, scope gmail.readonly): the FULL mailbox -> ext_gmail_account / label / thread / message / message_label / attachment.

    python -m leadgen.google.gmail --check             # token status, OFFLINE (no network); add --online to refresh + call users.getProfile
    python -m leadgen.google.gmail --pull              # full pull the first time, incremental (users.history.list) afterwards
    python -m leadgen.google.gmail --pull --full       # ignore the stored historyId and list everything again (existing ids are skipped)
    python -m leadgen.google.gmail --pull --max-messages 500    # stop after 500 NEW messages (staged first run; the next run resumes)

Token: secrets/gmail_readonly_token.json, created ONCE by ``tools/gmail_auth.py`` (browser consent, gmail.readonly only).

API calls used (the read-only guard in leadgen.google.auth lets nothing else through, and tests/test_google.py scans this file's source):
    users.getProfile | users.labels.list / get | users.messages.list / get (format=full, HTTP batches) / attachments.get | users.history.list

Design
    * FULL pull: messages.list (500 per page, includeSpamTrash) page by page; for every page the ids not yet stored are fetched with
      messages.get(format=full) in HTTP batches of BATCH_SIZE, parsed and stored in short transactions; after each page the next pageToken
      is checkpointed in ops_sync_state (gmail / <mailbox> / messages / full_page_token), so an interrupted run resumes at that page and
      already-stored ids are skipped.  The mailbox historyId read BEFORE listing is stored as full_start_history_id and becomes the
      incremental cursor (``historyId``) when the listing completes - anything that changes while the full pull runs is picked up by
      the first incremental run.
    * INCREMENTAL: users.history.list(startHistoryId=<cursor>) -> messageAdded ids are fetched in full, label changes re-read the labels,
      messageDeleted is counted (rows are kept: this is an archive).  A 404 (history expired, ~a week) falls back to a full listing.
    * Quota: a unit throttle keeps below the 250 units / second / user limit (messages.get = 5 units), per-request 429 / 5xx / rateLimit 403
      inside a batch are retried with exponential backoff, 404 (message vanished) is skipped.
    * Storage: parsed fields in ext_gmail_message (+ FTS via triggers), the complete API response of every message as
      data/gmail/raw/<id[:2]>/<id>.json.gz (raw_path / raw_sha256 - note: the schema comment says .eml.gz, but format=full is a JSON tree),
      attachments up to 25 MB as files under data/gmail/attachments/<messageId>/ (larger ones are skipped and recorded in ops_dq_issue).
    * Addresses are lower-case bare addresses, comma separated; Date header -> UTC; HTML-only mail keeps body_html and gets a text rendering in body_text
      so the FTS index finds it.

Python 3.9 compatible.
"""
import argparse
import base64
import datetime
import email.header
import email.utils
import gzip
import hashlib
import html
import html.parser
import json
import os
import re
import sqlite3
import sys
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from leadgen import db as ldb
from leadgen.google import auth

PROJECT_ROOT = auth.PROJECT_ROOT
GMAIL_DIR = os.path.join(PROJECT_ROOT, "data", "gmail")
TOKEN_FILE = auth.GMAIL_TOKEN_FILE

ATTACHMENT_CAP = 25 * 1024 * 1024
BATCH_SIZE = 25                 # messages per HTTP batch (Google advises <= 50)
LIST_PAGE_SIZE = 500            # messages.list maximum
DEFAULT_UNITS_PER_SEC = 150.0   # per-user limit is 250 units / second
COST = {"get": 5, "list": 5, "attachment": 5, "label": 1, "profile": 1, "history": 2}


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def iso_ms_from_epoch_ms(value: Any) -> Optional[str]:
    try:
        ms = int(value)
    except (TypeError, ValueError):
        return None
    dt = datetime.datetime.utcfromtimestamp(ms // 1000)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (ms % 1000)


def iso_from_date_header(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        dt = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = (dt - dt.utcoffset()).replace(tzinfo=None)
    if not (1971 <= dt.year <= 2100):          # garbage Date headers
        return None
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (dt.microsecond // 1000)


def decode_header_text(value: Optional[str]) -> str:
    """RFC 2047 encoded-words -> text; never raises."""
    if not value:
        return ""
    try:
        return str(email.header.make_header(email.header.decode_header(value)))
    except (email.errors.HeaderParseError, UnicodeError, LookupError, ValueError):
        return value


def addresses(value: Optional[str]) -> List[str]:
    """Lower-case bare addresses of a header value (display names dropped), order kept, duplicates removed."""
    out = []  # type: List[str]
    for _name, addr in email.utils.getaddresses([value or ""]):
        a = addr.strip().lower().replace(",", "")
        if "@" in a and a not in out:
            out.append(a)
    if not out and "@" in (value or ""):                 # malformed list (stdlib's strict parser gives up): fall back to a plain pattern
        for a in re.findall(r"[A-Za-z0-9._%+'\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", value or ""):
            if a.lower() not in out:
                out.append(a.lower())
    return out


# ------------------------------------------------------------------------------------------------- MIME parsing
class _TextExtractor(html.parser.HTMLParser):
    SKIP = ("script", "style", "head", "title")

    def __init__(self) -> None:
        html.parser.HTMLParser.__init__(self, convert_charrefs=True)
        self.parts = []  # type: List[str]
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in self.SKIP:
            self._skip += 1
        elif tag in ("br", "p", "div", "tr", "li", "h1", "h2", "h3", "table"):
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(src: str) -> str:
    p = _TextExtractor()
    try:
        p.feed(src)
        p.close()
    except Exception:  # noqa: BLE001 - malformed HTML must not stop a pull
        return re.sub(r"<[^>]+>", " ", src)
    text = "".join(p.parts)
    return re.sub(r"[ \t\r\f\v]+", " ", re.sub(r"\n\s*\n+", "\n", text)).strip()


def b64url_decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _hdr(headers: Sequence[Dict[str, str]], name: str) -> Optional[str]:
    low = name.lower()
    for h in headers or []:
        if h.get("name", "").lower() == low:
            return h.get("value")
    return None


def _charset(content_type: Optional[str]) -> str:
    m = re.search(r"charset\s*=\s*\"?([A-Za-z0-9_.:-]+)", content_type or "", re.I)
    return m.group(1) if m else "utf-8"


def _decode_bytes(raw: bytes, charset: str) -> str:
    try:
        return raw.decode(charset, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def walk_parts(payload: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    """Depth-first over the nested MIME tree (multipart/* and message/rfc822 containers included)."""
    yield payload
    for p in payload.get("parts") or []:
        for q in walk_parts(p):
            yield q


def parse_message(msg: Dict[str, Any]) -> Dict[str, Any]:
    """messages.get(format=full) JSON -> flat dict of the columns we store + 'attachments' descriptors.
    Pure function: no network, no database."""
    payload = msg.get("payload") or {}
    headers = payload.get("headers") or []
    plain, htmls = [], []  # type: List[str], List[str]
    atts = []  # type: List[Dict[str, Any]]
    for part in walk_parts(payload):
        mime = (part.get("mimeType") or "").lower()
        body = part.get("body") or {}
        phdr = part.get("headers") or []
        if mime.startswith("multipart/") or (mime == "message/rfc822" and not body.get("attachmentId")):
            continue
        filename = part.get("filename") or ""
        att_id = body.get("attachmentId")
        data = body.get("data")
        if att_id or filename:
            disp = (_hdr(phdr, "Content-Disposition") or "").strip().lower()
            cid = (_hdr(phdr, "Content-ID") or "").strip().strip("<>") or None
            atts.append({
                "part_id": str(part.get("partId") or "%d" % len(atts)), "attachment_id": att_id, "filename": filename, "mime_type": mime or None,
                "content_id": cid, "disposition": "inline" if (disp.startswith("inline") or (not disp and cid and not filename)) else "attachment",
                "size": body.get("size"), "inline_data": b64url_decode(data) if (data and not att_id) else None,
            })
            continue
        if data is None:
            continue
        text = _decode_bytes(b64url_decode(data), _charset(_hdr(phdr, "Content-Type")))
        if mime == "text/plain":
            plain.append(text)
        elif mime == "text/html":
            htmls.append(text)
    labels = list(msg.get("labelIds") or [])
    from_list = addresses(_hdr(headers, "From"))
    body_text = "\n".join(plain).strip() if plain else None
    body_html = None
    if htmls and not plain:                      # schema: body_html is kept for HTML-only mail; text rendering makes it searchable
        body_html = "\n".join(htmls)
        body_text = html_to_text(body_html) or None
    lst = lambda name: ",".join(addresses(_hdr(headers, name))) or None
    return {
        "message_id": msg["id"], "thread_id": msg.get("threadId") or msg["id"], "history_id": msg.get("historyId"),
        "internal_at": iso_ms_from_epoch_ms(msg.get("internalDate")), "date_header_at": iso_from_date_header(_hdr(headers, "Date")),
        "rfc822_message_id": _hdr(headers, "Message-ID") or _hdr(headers, "Message-Id"),
        "in_reply_to": _hdr(headers, "In-Reply-To"), "references_hdr": _hdr(headers, "References"),
        "from_addr": from_list[0] if from_list else None, "to_addrs": lst("To"), "cc_addrs": lst("Cc"), "bcc_addrs": lst("Bcc"),
        "reply_to": lst("Reply-To"), "subject": decode_header_text(_hdr(headers, "Subject")) if _hdr(headers, "Subject") is not None else None,
        "snippet": html.unescape(msg.get("snippet") or "") or None, "body_text": body_text, "body_html": body_html,
        "headers_json": json.dumps([[h.get("name", ""), h.get("value", "")] for h in headers], ensure_ascii=False),
        "size_estimate": msg.get("sizeEstimate"), "label_ids": labels,
        "is_sent": int("SENT" in labels), "is_draft": int("DRAFT" in labels), "is_spam_trash": int("SPAM" in labels or "TRASH" in labels),
        "has_attachments": int(any(a["disposition"] == "attachment" for a in atts)), "attachments": atts,
    }


# ------------------------------------------------------------------------------------------------- API client
class UnitThrottle(object):
    """Keeps the average quota-unit spend below ``units_per_sec`` (clock / sleep injectable)."""

    def __init__(self, units_per_sec: float = DEFAULT_UNITS_PER_SEC, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.rate = float(units_per_sec)
        self.clock, self.sleep = clock, sleep
        self._next = 0.0

    def wait(self, units: float) -> None:
        now = self.clock()
        slot = max(now, self._next)
        self._next = slot + units / self.rate
        if slot > now:
            self.sleep(slot - now)


class GmailClient(object):
    """Thin read-only wrapper: every call goes through the guard, the throttle and the backoff."""

    def __init__(self, service: Any, throttle: Optional[UnitThrottle] = None, sleep: Callable[[float], None] = time.sleep,
                 retries: int = 8, base_delay: float = 5.0):
        self.svc = auth.guard(service)
        self.throttle = throttle or UnitThrottle(sleep=sleep)
        self.sleep = sleep
        self.retries = retries
        self.base_delay = base_delay
        self.calls = 0

    def _exec(self, request: Any, units: float) -> Any:
        self.calls += 1
        self.throttle.wait(units)
        return auth.execute(request, None, retries=self.retries, base=self.base_delay, sleep=self.sleep,
                            on_retry=lambda a, d, e: log("  quota/backoff: attempt %d, sleeping %.0fs (%s)" % (a + 1, d, type(e).__name__)))

    def profile(self) -> Dict[str, Any]:
        return self._exec(self.svc.users().getProfile(userId="me"), COST["profile"])

    def labels(self) -> List[Dict[str, Any]]:
        out = []
        for lab in self._exec(self.svc.users().labels().list(userId="me"), COST["label"]).get("labels", []):
            try:
                out.append(self._exec(self.svc.users().labels().get(userId="me", id=lab["id"]), COST["label"]))
            except Exception as exc:  # noqa: BLE001 - counts are a nicety
                log("  label %s detail unavailable: %s" % (lab.get("id"), type(exc).__name__))
                out.append(lab)
        return out

    def list_messages(self, page_token: Optional[str] = None, page_size: int = LIST_PAGE_SIZE) -> Dict[str, Any]:
        return self._exec(self.svc.users().messages().list(userId="me", maxResults=page_size, pageToken=page_token, includeSpamTrash=True), COST["list"])

    def history(self, start_history_id: str, page_token: Optional[str] = None) -> Dict[str, Any]:
        return self._exec(self.svc.users().history().list(
            userId="me", startHistoryId=start_history_id, pageToken=page_token, maxResults=500,
            historyTypes=["messageAdded", "messageDeleted", "labelAdded", "labelRemoved"]), COST["history"])

    def attachment(self, message_id: str, attachment_id: str) -> bytes:
        resp = self._exec(self.svc.users().messages().attachments().get(userId="me", messageId=message_id, id=attachment_id), COST["attachment"])
        return b64url_decode(resp.get("data") or "")

    def get_messages(self, ids: Sequence[str], fmt: str = "full") -> Tuple[Dict[str, Dict[str, Any]], Dict[str, int]]:
        """messages.get for many ids via HTTP batch requests.  Returns ({id: message}, {id: http status} for ids that could not be read:
        404 = deleted since it was listed).  Retryable per-request failures are retried with exponential backoff."""
        got, failed = {}, {}  # type: Dict[str, Dict[str, Any]], Dict[str, int]
        pending = list(ids)
        attempt = 0
        while pending:
            outcome = {}  # type: Dict[str, Tuple[Any, Any]]
            batch = self.svc.new_batch_http_request(callback=lambda rid, resp, exc: outcome.__setitem__(rid, (resp, exc)))
            for mid in pending:
                batch.add(self.svc.users().messages().get(userId="me", id=mid, format=fmt), request_id=mid)
            self.calls += 1
            self.throttle.wait(COST["get"] * len(pending))
            try:
                batch.execute()
            except BaseException as exc:  # noqa: B902 - whole-batch failure: retry when transient
                if not auth.is_retryable(exc) or attempt >= self.retries:
                    raise
                delay = auth.backoff_delay(attempt, base=self.base_delay)
                log("  batch failed (%s), sleeping %.0fs" % (type(exc).__name__, delay))
                self.sleep(delay)
                attempt += 1
                continue
            retry = []
            for mid in pending:
                resp, exc = outcome.get(mid, (None, RuntimeError("no response in batch")))
                if exc is None and resp is not None:
                    got[mid] = resp
                elif auth.is_retryable(exc) and attempt < self.retries:
                    retry.append(mid)
                else:
                    failed[mid] = auth.http_error_details(exc)[0] if hasattr(exc, "resp") else 0
            if retry:
                delay = auth.backoff_delay(attempt, base=self.base_delay)
                log("  %d of %d requests rate-limited, sleeping %.0fs" % (len(retry), len(pending), delay))
                self.sleep(delay)
                attempt += 1
            pending = retry
        return got, failed


# ------------------------------------------------------------------------------------------------- storage
def safe_filename(name: str, limit: int = 120) -> str:
    base = os.path.basename((name or "").replace("\\", "/"))
    s = re.sub(r"[^A-Za-z0-9._ -]+", "_", base).strip(" .") or "unnamed"
    return s[:limit]


def _gz_json(path: str, obj: Any) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    blob = gzip.compress(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), mtime=0)
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(blob)
    os.replace(tmp, path)
    return hashlib.sha256(blob).hexdigest()


class Store(object):
    """All database writes of the Gmail pull (each public method = one short transaction)."""

    def __init__(self, con: sqlite3.Connection, account_id: int, data_dir: str = GMAIL_DIR):
        self.con = con
        self.account_id = account_id
        self.data_dir = data_dir
        self._labels = {}  # type: Dict[str, int]
        self.load_labels()

    # labels -----------------------------------------------------------------------------------------
    def load_labels(self) -> None:
        self._labels = {r["label_id"]: r["label_pk"] for r in self.con.execute("SELECT label_id, label_pk FROM ext_gmail_label WHERE gmail_account_id=?", (self.account_id,))}

    def upsert_labels(self, labels: Sequence[Dict[str, Any]]) -> int:
        now = ldb.utc_now_iso()
        with ldb.transaction(self.con):
            for lab in labels:
                self.con.execute(
                    "INSERT INTO ext_gmail_label (gmail_account_id, label_id, name, label_type, messages_total, threads_total, fetched_at) VALUES (?,?,?,?,?,?,?) "
                    "ON CONFLICT (gmail_account_id, label_id) DO UPDATE SET name=excluded.name, label_type=excluded.label_type, "
                    "messages_total=excluded.messages_total, threads_total=excluded.threads_total, fetched_at=excluded.fetched_at",
                    (self.account_id, lab["id"], lab.get("name") or lab["id"], "system" if lab.get("type") == "system" else "user",
                     lab.get("messagesTotal"), lab.get("threadsTotal"), now))
        self.load_labels()
        return len(labels)

    def _label_pk(self, label_id: str) -> int:
        pk = self._labels.get(label_id)
        if pk is None:                            # a label id on a message that labels.list did not return (deleted label)
            self.con.execute("INSERT INTO ext_gmail_label (gmail_account_id, label_id, name, label_type, fetched_at) VALUES (?,?,?,'user',?) "
                             "ON CONFLICT (gmail_account_id, label_id) DO NOTHING", (self.account_id, label_id, label_id, ldb.utc_now_iso()))
            pk = self.con.execute("SELECT label_pk FROM ext_gmail_label WHERE gmail_account_id=? AND label_id=?", (self.account_id, label_id)).fetchone()[0]
            self._labels[label_id] = pk
        return pk

    # messages ---------------------------------------------------------------------------------------
    def known_ids(self, ids: Sequence[str]) -> set:
        known = set()  # type: set
        for i in range(0, len(ids), 500):
            chunk = list(ids[i:i + 500])
            for r in self.con.execute("SELECT message_id FROM ext_gmail_message WHERE gmail_account_id=? AND message_id IN (%s)" % ",".join("?" * len(chunk)),
                                      [self.account_id] + chunk):
                known.add(r[0])
        return known

    def store_messages(self, messages: Sequence[Dict[str, Any]], run_id: Optional[int] = None) -> List[Tuple[str, List[Dict[str, Any]]]]:
        """Parse + upsert a batch of API messages (one transaction).  Returns [(message_id, attachment descriptors)] for the download step."""
        now = ldb.utc_now_iso()
        out = []  # type: List[Tuple[str, List[Dict[str, Any]]]]
        prepared = []
        for m in messages:                                           # disk work happens outside the DB transaction
            p = parse_message(m)
            rel = os.path.join("raw", m["id"][:2], m["id"] + ".json.gz")
            p["raw_sha256"] = _gz_json(os.path.join(self.data_dir, rel), m)
            p["raw_path"] = os.path.relpath(os.path.join(self.data_dir, rel), PROJECT_ROOT)
            prepared.append(p)
        with ldb.transaction(self.con):
            thread_pks = set()  # type: set
            for p in prepared:
                self.con.execute("INSERT INTO ext_gmail_thread (gmail_account_id, thread_id, fetched_at) VALUES (?,?,?) ON CONFLICT (gmail_account_id, thread_id) DO NOTHING",
                                 (self.account_id, p["thread_id"], now))
                tpk = self.con.execute("SELECT thread_pk FROM ext_gmail_thread WHERE gmail_account_id=? AND thread_id=?", (self.account_id, p["thread_id"])).fetchone()[0]
                thread_pks.add(tpk)
                self.con.execute(
                    "INSERT INTO ext_gmail_message (gmail_account_id, thread_pk, message_id, rfc822_message_id, in_reply_to, references_hdr, history_id, internal_at, "
                    "date_header_at, from_addr, to_addrs, cc_addrs, bcc_addrs, reply_to, subject, snippet, body_text, body_html, headers_json, size_estimate, "
                    "is_sent, is_draft, is_spam_trash, has_attachments, raw_path, raw_sha256, fetched_at, import_run_id) "
                    "VALUES (:acc,:tpk,:message_id,:rfc822_message_id,:in_reply_to,:references_hdr,:history_id,:internal_at,:date_header_at,:from_addr,:to_addrs,"
                    ":cc_addrs,:bcc_addrs,:reply_to,:subject,:snippet,:body_text,:body_html,:headers_json,:size_estimate,:is_sent,:is_draft,:is_spam_trash,"
                    ":has_attachments,:raw_path,:raw_sha256,:now,:run) "
                    "ON CONFLICT (gmail_account_id, message_id) DO UPDATE SET thread_pk=excluded.thread_pk, rfc822_message_id=excluded.rfc822_message_id, "
                    "in_reply_to=excluded.in_reply_to, references_hdr=excluded.references_hdr, history_id=excluded.history_id, internal_at=excluded.internal_at, "
                    "date_header_at=excluded.date_header_at, from_addr=excluded.from_addr, to_addrs=excluded.to_addrs, cc_addrs=excluded.cc_addrs, "
                    "bcc_addrs=excluded.bcc_addrs, reply_to=excluded.reply_to, subject=excluded.subject, snippet=excluded.snippet, body_text=excluded.body_text, "
                    "body_html=excluded.body_html, headers_json=excluded.headers_json, size_estimate=excluded.size_estimate, is_sent=excluded.is_sent, "
                    "is_draft=excluded.is_draft, is_spam_trash=excluded.is_spam_trash, has_attachments=excluded.has_attachments, raw_path=excluded.raw_path, "
                    "raw_sha256=excluded.raw_sha256, fetched_at=excluded.fetched_at, import_run_id=excluded.import_run_id",
                    dict(p, acc=self.account_id, tpk=tpk, now=now, run=run_id))
                mpk = self.con.execute("SELECT message_pk FROM ext_gmail_message WHERE gmail_account_id=? AND message_id=?", (self.account_id, p["message_id"])).fetchone()[0]
                self._set_labels(mpk, p["label_ids"])
                for a in p["attachments"]:
                    self.con.execute(
                        "INSERT INTO ext_gmail_attachment (message_pk, part_id, attachment_id, filename, mime_type, content_id, disposition, size_bytes) VALUES (?,?,?,?,?,?,?,?) "
                        "ON CONFLICT (message_pk, part_id) DO UPDATE SET attachment_id=excluded.attachment_id, filename=excluded.filename, mime_type=excluded.mime_type, "
                        "content_id=excluded.content_id, disposition=excluded.disposition, size_bytes=excluded.size_bytes",
                        (mpk, a["part_id"], a["attachment_id"], a["filename"], a["mime_type"], a["content_id"], a["disposition"], a["size"]))
                out.append((p["message_id"], p["attachments"]))
            self._refresh_threads(thread_pks, now)
        return out

    def _set_labels(self, message_pk: int, label_ids: Sequence[str]) -> None:
        want = set(self._label_pk(l) for l in label_ids)
        have = set(r[0] for r in self.con.execute("SELECT label_pk FROM ext_gmail_message_label WHERE message_pk=?", (message_pk,)))
        for pk in have - want:
            self.con.execute("DELETE FROM ext_gmail_message_label WHERE message_pk=? AND label_pk=?", (message_pk, pk))
        for pk in want - have:
            self.con.execute("INSERT INTO ext_gmail_message_label (message_pk, label_pk) VALUES (?,?)", (message_pk, pk))

    def store_label_changes(self, minimal: Dict[str, Dict[str, Any]]) -> int:
        """format=minimal responses of messages we already hold: refresh label set, flags and historyId."""
        n = 0
        with ldb.transaction(self.con):
            for mid, m in minimal.items():
                r = self.con.execute("SELECT message_pk FROM ext_gmail_message WHERE gmail_account_id=? AND message_id=?", (self.account_id, mid)).fetchone()
                if r is None:
                    continue
                labels = list(m.get("labelIds") or [])
                self.con.execute("UPDATE ext_gmail_message SET history_id=?, is_sent=?, is_draft=?, is_spam_trash=? WHERE message_pk=?",
                                 (m.get("historyId"), int("SENT" in labels), int("DRAFT" in labels), int("SPAM" in labels or "TRASH" in labels), r[0]))
                self._set_labels(r[0], labels)
                n += 1
        return n

    def _refresh_threads(self, thread_pks: Iterable[int], now: str) -> None:
        for tpk in thread_pks:
            self.con.execute(
                "UPDATE ext_gmail_thread SET "
                "message_count=(SELECT COUNT(*) FROM ext_gmail_message m WHERE m.thread_pk=ext_gmail_thread.thread_pk), "
                "first_message_at=(SELECT MIN(internal_at) FROM ext_gmail_message m WHERE m.thread_pk=ext_gmail_thread.thread_pk), "
                "last_message_at=(SELECT MAX(internal_at) FROM ext_gmail_message m WHERE m.thread_pk=ext_gmail_thread.thread_pk), "
                "snippet=(SELECT snippet FROM ext_gmail_message m WHERE m.thread_pk=ext_gmail_thread.thread_pk ORDER BY internal_at DESC LIMIT 1), "
                "subject=(SELECT subject FROM ext_gmail_message m WHERE m.thread_pk=ext_gmail_thread.thread_pk ORDER BY internal_at ASC LIMIT 1), "
                "history_id=(SELECT CAST(MAX(CAST(history_id AS INTEGER)) AS TEXT) FROM ext_gmail_message m WHERE m.thread_pk=ext_gmail_thread.thread_pk), "
                "fetched_at=? WHERE thread_pk=?", (now, tpk))

    # attachments ------------------------------------------------------------------------------------
    def download_attachments(self, client: Optional["GmailClient"], items: Sequence[Tuple[str, List[Dict[str, Any]]]], cap: int = ATTACHMENT_CAP) -> Dict[str, int]:
        """Fetch + store the bytes of attachments (<= cap) under data/gmail/attachments/<messageId>/.  ``client`` None = metadata only."""
        stats = {"att_downloaded": 0, "att_skipped_large": 0, "att_skipped_existing": 0, "att_failed": 0}
        for mid, atts in items:
            for a in atts:
                row = self.con.execute(
                    "SELECT a.attachment_pk, a.stored_path FROM ext_gmail_attachment a JOIN ext_gmail_message m ON m.message_pk=a.message_pk "
                    "WHERE m.gmail_account_id=? AND m.message_id=? AND a.part_id=?", (self.account_id, mid, a["part_id"])).fetchone()
                if row is None:
                    continue
                if row["stored_path"] and os.path.exists(os.path.join(PROJECT_ROOT, row["stored_path"])):
                    stats["att_skipped_existing"] += 1
                    continue
                size = a.get("size")
                if size is not None and int(size) > cap:
                    stats["att_skipped_large"] += 1
                    self._dq_large(mid, a, int(size), cap)
                    continue
                data = a.get("inline_data")
                if data is None:
                    if client is None or not a.get("attachment_id"):
                        continue
                    try:
                        data = client.attachment(mid, a["attachment_id"])
                    except Exception as exc:  # noqa: BLE001
                        stats["att_failed"] += 1
                        log("  attachment %s/%s failed: %s" % (mid, a["part_id"], type(exc).__name__))
                        continue
                if len(data) > cap:
                    stats["att_skipped_large"] += 1
                    self._dq_large(mid, a, len(data), cap)
                    continue
                rel = os.path.join("attachments", mid, "%s_%s" % (a["part_id"].replace(".", "-"), safe_filename(a["filename"] or (a["mime_type"] or "part"))))
                path = os.path.join(self.data_dir, rel)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as fh:
                    fh.write(data)
                with ldb.transaction(self.con):
                    self.con.execute("UPDATE ext_gmail_attachment SET stored_path=?, content_sha256=?, size_bytes=?, fetched_at=? WHERE attachment_pk=?",
                                     (os.path.relpath(path, PROJECT_ROOT), hashlib.sha256(data).hexdigest(), len(data), ldb.utc_now_iso(), row["attachment_pk"]))
                stats["att_downloaded"] += 1
        return stats

    def _dq_large(self, mid: str, a: Dict[str, Any], size: int, cap: int) -> None:
        with ldb.transaction(self.con):
            self.con.execute(
                "INSERT INTO ops_dq_issue (fingerprint, rule_code, severity, entity_type, entity_ref, message, details_json) VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT (fingerprint) DO UPDATE SET occurrences = occurrences + 1, last_seen_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')",
                ("gmail_attachment_too_large|%s|%s|%s" % (self.account_id, mid, a["part_id"]), "gmail_attachment_too_large", "info", "gmail_attachment", "%s/%s" % (mid, a["part_id"]),
                 "attachment %r is %d bytes, over the %d byte cap: metadata stored, bytes not downloaded" % (a.get("filename"), size, cap),
                 json.dumps({"message_id": mid, "part_id": a["part_id"], "size": size, "cap": cap})))


# ------------------------------------------------------------------------------------------------- sync state
OBJ = "messages"


def _state(con: sqlite3.Connection, email_addr: str, cursor: str) -> Optional[str]:
    r = con.execute("SELECT cursor_value FROM ops_sync_state WHERE source_system='gmail' AND scope=? AND object_type=? AND cursor_name=?", (email_addr, OBJ, cursor)).fetchone()
    return r[0] if r else None


def _set_state(con: sqlite3.Connection, email_addr: str, cursor: str, value: Optional[str], status: str = "ok", error: Optional[str] = None,
               rows: int = 0, run_id: Optional[int] = None) -> None:
    now = ldb.utc_now_iso()
    with ldb.transaction(con):
        con.execute(
            "INSERT INTO ops_sync_state (source_system, scope, object_type, cursor_name, cursor_value, last_attempt_at, last_success_at, last_status, last_error, rows_synced, import_run_id) "
            "VALUES ('gmail', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (source_system, scope, object_type, cursor_name) DO UPDATE SET cursor_value=excluded.cursor_value, last_attempt_at=excluded.last_attempt_at, "
            "last_success_at=CASE WHEN excluded.last_status='ok' THEN excluded.last_success_at ELSE ops_sync_state.last_success_at END, "
            "last_status=excluded.last_status, last_error=excluded.last_error, rows_synced=excluded.rows_synced, import_run_id=excluded.import_run_id",
            (email_addr, OBJ, cursor, value, now, now if status == "ok" else None, status, error, rows, run_id))


def ensure_account(con: sqlite3.Connection, profile: Dict[str, Any], token_ref: str = TOKEN_FILE, scopes: Sequence[str] = (auth.SCOPE_GMAIL_RO,)) -> int:
    addr = profile["emailAddress"].strip().lower()
    with ldb.transaction(con):
        con.execute(
            "INSERT INTO ext_gmail_account (email_address, token_ref, scopes_json, history_id, messages_total, threads_total, profile_fetched_at) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT (email_address) DO UPDATE SET token_ref=excluded.token_ref, scopes_json=excluded.scopes_json, "
            "messages_total=excluded.messages_total, threads_total=excluded.threads_total, profile_fetched_at=excluded.profile_fetched_at",
            (addr, token_ref, json.dumps(list(scopes)), profile.get("historyId"), profile.get("messagesTotal"), profile.get("threadsTotal"), ldb.utc_now_iso()))
    return int(con.execute("SELECT gmail_account_id FROM ext_gmail_account WHERE email_address=?", (addr,)).fetchone()[0])


# ------------------------------------------------------------------------------------------------- sync
def _fetch_and_store(store: Store, client: GmailClient, ids: Sequence[str], run_id: Optional[int], summary: Dict[str, Any], attachments: bool,
                     fmt: str = "full") -> None:
    for i in range(0, len(ids), BATCH_SIZE):
        chunk = ids[i:i + BATCH_SIZE]
        got, failed = client.get_messages(chunk, fmt)
        for mid, status in failed.items():
            summary["missing" if status == 404 else "failed"] += 1
            if status != 404:
                log("  message %s could not be read (HTTP %s)" % (mid, status))
        ordered = [got[m] for m in chunk if m in got]
        if ordered:
            items = store.store_messages(ordered, run_id)
            summary["messages"] += len(ordered)
            st = store.download_attachments(client if attachments else None, items)
            for k, v in st.items():
                summary[k] = summary.get(k, 0) + v


def sync(con: sqlite3.Connection, client: GmailClient, full: bool = False, max_messages: Optional[int] = None, attachments: bool = True,
         data_dir: str = GMAIL_DIR, token_ref: str = TOKEN_FILE) -> Dict[str, Any]:
    """Pull / update the mailbox.  Returns a summary dict.  See the module docstring for the resume / incremental rules."""
    profile = client.profile()
    addr = profile["emailAddress"].strip().lower()
    account_id = ensure_account(con, profile, token_ref)
    store = Store(con, account_id, data_dir)
    with ldb.transaction(con):
        cur = con.execute("INSERT INTO ops_import_run (kind, source_name, params_json, tool_version) VALUES ('gmail_pull', ?, ?, 'leadgen.google.gmail')",
                          ("gmail:" + addr, json.dumps({"full": full, "max_messages": max_messages})))
        run_id = int(cur.lastrowid)
    summary = {"mailbox": addr, "mode": None, "run_id": run_id, "messages": 0, "missing": 0, "failed": 0, "labels": 0, "deleted_upstream": 0, "label_updates": 0,
               "messages_total": profile.get("messagesTotal"), "threads_total": profile.get("threadsTotal"), "complete": False}  # type: Dict[str, Any]
    status, err = "succeeded", None
    try:
        summary["labels"] = store.upsert_labels(client.labels())
        hist = _state(con, addr, "historyId")
        mode = "incremental" if (hist and not full) else "full"
        if mode == "incremental":
            ok = _incremental(con, client, store, addr, hist, run_id, summary, attachments)
            if not ok:
                log("startHistoryId %s is no longer valid: falling back to a full listing" % hist)
                _set_state(con, addr, "historyId", None, "error", "history expired", run_id=run_id)
                mode = "full"
        if mode == "full":
            _full(con, client, store, addr, profile, run_id, summary, max_messages, attachments)
        summary["mode"] = mode if not summary.get("fell_back") else "full(fallback)"
    except BaseException as exc:
        status, err = ("aborted" if isinstance(exc, KeyboardInterrupt) else "failed"), "%s: %s" % (type(exc).__name__, str(exc)[:300])
        raise
    finally:
        with ldb.transaction(con):
            con.execute("UPDATE ops_import_run SET status=?, finished_at=?, rows_read=?, rows_written=?, rows_rejected=?, error=? WHERE import_run_id=?",
                        ("partial" if (status == "succeeded" and not summary["complete"]) else status, ldb.utc_now_iso(), summary["messages"], summary["messages"], summary["failed"], err, run_id))
    return summary


def _full(con: sqlite3.Connection, client: GmailClient, store: Store, addr: str, profile: Dict[str, Any], run_id: int, summary: Dict[str, Any],
          max_messages: Optional[int], attachments: bool) -> None:
    start = _state(con, addr, "full_start_history_id")
    token = _state(con, addr, "full_page_token") or None
    if not start:                                    # fresh full pull: remember the history position BEFORE listing
        start = str(profile.get("historyId") or "")
        token = None
        _set_state(con, addr, "full_page_token", None, "ok", run_id=run_id)
        _set_state(con, addr, "full_start_history_id", start, "ok", run_id=run_id)
    summary["resumed_from_page_token"] = bool(token)
    page = 0
    while True:
        try:
            resp = client.list_messages(token)
        except Exception as exc:  # noqa: BLE001
            if token and getattr(getattr(exc, "resp", None), "status", 0) == 400:    # stale pageToken: start the listing again (stored ids are skipped)
                log("stored pageToken rejected, restarting the listing")
                token = None
                continue
            raise
        page += 1
        ids = [m["id"] for m in resp.get("messages", [])]
        known = store.known_ids(ids)
        new_all = [i for i in ids if i not in known]
        new = new_all
        if max_messages is not None:
            new = new_all[:max(max_messages - summary["messages"], 0)]
        _fetch_and_store(store, client, new, run_id, summary, attachments)
        nxt = resp.get("nextPageToken")
        log("page %d: listed %d, new %d, stored so far %d" % (page, len(ids), len(new), summary["messages"]))
        unfetched = len(new_all) - len(new)
        if max_messages is not None and (unfetched > 0 or (summary["messages"] >= max_messages and nxt)):
            # stopped by the cap: resume at this page if it still holds unfetched ids, else at the next one
            _set_state(con, addr, "full_page_token", token if unfetched > 0 else nxt, "ok", rows=summary["messages"], run_id=run_id)
            return
        if not nxt:
            break
        token = nxt
        _set_state(con, addr, "full_page_token", token, "ok", rows=summary["messages"], run_id=run_id)
    if summary["failed"]:                            # unreadable messages: keep the full pull open so the next run re-lists and retries them
        _set_state(con, addr, "full_page_token", None, "ok", rows=summary["messages"], run_id=run_id)
        return
    _set_state(con, addr, "historyId", start, "ok", rows=summary["messages"], run_id=run_id)
    _set_state(con, addr, "full_page_token", None, "ok", run_id=run_id)
    _set_state(con, addr, "full_start_history_id", None, "ok", run_id=run_id)
    with ldb.transaction(con):
        con.execute("UPDATE ext_gmail_account SET history_id=?, profile_fetched_at=? WHERE email_address=?", (start, ldb.utc_now_iso(), addr))
    summary["complete"] = True


def _incremental(con: sqlite3.Connection, client: GmailClient, store: Store, addr: str, start_id: str, run_id: int, summary: Dict[str, Any],
                 attachments: bool) -> bool:
    """Returns False when Google no longer knows ``start_id`` (404) - the caller falls back to a full listing."""
    added, relabel, deleted = [], [], set()  # type: List[str], List[str], set
    newest = start_id
    token = None
    while True:
        try:
            resp = client.history(start_id, token)
        except Exception as exc:  # noqa: BLE001
            if getattr(getattr(exc, "resp", None), "status", 0) == 404:
                summary["fell_back"] = True
                return False
            raise
        for h in resp.get("history", []):
            for k in ("messagesAdded",):
                for e in h.get(k, []):
                    added.append(e["message"]["id"])
            for k in ("labelsAdded", "labelsRemoved"):
                for e in h.get(k, []):
                    relabel.append(e["message"]["id"])
            for e in h.get("messagesDeleted", []):
                deleted.add(e["message"]["id"])
        if resp.get("historyId"):
            newest = str(resp["historyId"])
        token = resp.get("nextPageToken")
        if not token:
            break
    added = [i for i in dict.fromkeys(added) if i not in deleted]
    known = store.known_ids(added + relabel)
    to_fetch = [i for i in added if i not in known]
    _fetch_and_store(store, client, to_fetch, run_id, summary, attachments)
    relabel = [i for i in dict.fromkeys(relabel) if i in known and i not in deleted and i not in added]
    for i in range(0, len(relabel), BATCH_SIZE):
        got, failed = client.get_messages(relabel[i:i + BATCH_SIZE], "minimal")
        summary["label_updates"] += store.store_label_changes(got)
    summary["deleted_upstream"] = len(deleted)
    if summary["failed"]:                            # keep the old cursor: the same history window is read again next run
        return True
    _set_state(con, addr, "historyId", newest, "ok", rows=summary["messages"], run_id=run_id)
    with ldb.transaction(con):
        con.execute("UPDATE ext_gmail_account SET history_id=?, profile_fetched_at=? WHERE email_address=?", (newest, ldb.utc_now_iso(), addr))
    summary["complete"] = True
    return True


def refresh_labels(con: sqlite3.Connection, client: GmailClient, email_addr: str) -> int:
    """Re-read the label set of every stored message (format=minimal, 5 units each).  Use after a history-expiry fallback."""
    acc = con.execute("SELECT gmail_account_id FROM ext_gmail_account WHERE email_address=?", (email_addr.lower(),)).fetchone()
    if acc is None:
        raise KeyError("unknown mailbox " + email_addr)
    store = Store(con, acc[0])
    ids = [r[0] for r in con.execute("SELECT message_id FROM ext_gmail_message WHERE gmail_account_id=? ORDER BY internal_at DESC", (acc[0],))]
    n = 0
    for i in range(0, len(ids), BATCH_SIZE):
        got, _failed = client.get_messages(ids[i:i + BATCH_SIZE], "minimal")
        n += store.store_label_changes(got)
    return n


# ------------------------------------------------------------------------------------------------- CLI
def check(token_file: str = TOKEN_FILE, online: bool = False) -> Dict[str, Any]:
    """Token status without pulling anything.  Offline unless ``online`` (then: refresh + users.getProfile)."""
    st = auth.token_status(token_file, required_scope=auth.SCOPE_GMAIL_RO)
    out = {"token": st, "online_checked": False}  # type: Dict[str, Any]
    if not st.get("exists"):
        out["next_step"] = "run:  .venv/bin/python tools/gmail_auth.py   (browser consent, scope gmail.readonly; writes secrets/%s)" % TOKEN_FILE
        out["ready"] = False
        return out
    out["ready"] = bool(st.get("ok"))
    if not st.get("ok"):
        out["next_step"] = "token file is unusable (%s): re-run tools/gmail_auth.py" % st.get("problem")
    if online and st.get("ok"):
        out["online_checked"] = True
        try:
            creds = auth.user_credentials(token_file, required_scopes=[auth.SCOPE_GMAIL_RO])
            prof = GmailClient(auth.build_service("gmail", "v1", creds)).profile()
            out["profile"] = {"emailAddress": prof.get("emailAddress"), "messagesTotal": prof.get("messagesTotal"), "threadsTotal": prof.get("threadsTotal"),
                              "historyId": prof.get("historyId")}
        except auth.TokenError as exc:
            out["ready"] = False
            out["online_error"] = str(exc)
            out["next_step"] = "refresh failed: re-run tools/gmail_auth.py"
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m leadgen.google.gmail", description="Read-only Gmail pull (gmail.readonly)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="report token status (offline) and exit")
    g.add_argument("--pull", action="store_true", help="full pull, or incremental when a historyId is stored")
    g.add_argument("--refresh-labels", action="store_true", help="re-read labels of every stored message (after a history-expiry fallback)")
    ap.add_argument("--online", action="store_true", help="--check: also refresh the token and call users.getProfile")
    ap.add_argument("--full", action="store_true", help="--pull: ignore the stored historyId")
    ap.add_argument("--max-messages", type=int, help="--pull: stop after this many NEW messages (resume next run)")
    ap.add_argument("--no-attachments", action="store_true", help="--pull: do not download attachment bytes")
    ap.add_argument("--units-per-sec", type=float, default=DEFAULT_UNITS_PER_SEC)
    ap.add_argument("--token", default=TOKEN_FILE, help="token file under secrets/ (default %s)" % TOKEN_FILE)
    ap.add_argument("--db")
    a = ap.parse_args(argv)
    if a.check:
        rep = check(a.token, a.online)
        print(json.dumps(rep, indent=2, default=str))
        return 0 if rep.get("ready") else 1
    try:
        creds = auth.user_credentials(a.token, required_scopes=[auth.SCOPE_GMAIL_RO])
    except auth.TokenError as exc:
        print("ERROR: %s\nnext step: .venv/bin/python tools/gmail_auth.py" % exc, file=sys.stderr)
        return 2
    client = GmailClient(auth.build_service("gmail", "v1", creds), UnitThrottle(a.units_per_sec))
    con = ldb.connect(a.db, must_exist=True)
    try:
        if a.refresh_labels:
            addr = client.profile()["emailAddress"]
            print(json.dumps({"mailbox": addr, "label_updates": refresh_labels(con, client, addr)}))
            return 0
        out = sync(con, client, full=a.full, max_messages=a.max_messages, attachments=not a.no_attachments, token_ref=os.path.basename(a.token))
        print(json.dumps(out, indent=2, default=str))
        return 0 if out["complete"] else 3
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
