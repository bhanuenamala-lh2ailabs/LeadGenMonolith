"""Mail transport for the daily report: Gmail API ``users.messages.send`` (scope gmail.send).

DRY-RUN BY DEFAULT.  Nothing in this module sends unless :func:`send_raw` is called, and the only caller is ``daily --send``.
The only recipient allowed by default is the one in ``config/report.yaml mail.default_to`` (bhanu.enamala@lh2.ai); another address
needs an explicit ``--to``.  Tokens come from secrets/ (CONTEXT.md rule 1); only booleans and scopes are ever inspected or logged.
Tests drive :func:`send_raw` with a fake service (``service_factory``) - no network, no token read.
"""
import base64
import email.message
import email.utils
import re
from typing import Any, Callable, Dict, List, Optional, Sequence

from leadgen.google import auth

ADDR_RE = re.compile(r"^[A-Za-z0-9._%+\-']+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


class MailError(RuntimeError):
    pass


def resolve_recipients(cfg: Dict[str, Any], to: Optional[str]) -> List[str]:
    """No --to: [mail.default_to] only.  --to a@x,b@y names other recipients explicitly.  Every address is validated."""
    raw = to if to else cfg["mail"]["default_to"]
    addrs = [a.strip() for a in raw.split(",") if a.strip()]
    if not addrs:
        raise MailError("no recipient")
    for a in addrs:
        if not ADDR_RE.match(a):
            raise MailError("not an e-mail address: %r" % a)
    return addrs


def build_message(subject: str, to: Sequence[str], text: str, html_body: str) -> email.message.EmailMessage:
    msg = email.message.EmailMessage()
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    msg["Date"] = email.utils.formatdate(localtime=False)
    msg.set_content(text)
    msg.add_alternative(html_body, subtype="html")
    return msg


def encode_raw(msg: email.message.EmailMessage) -> str:
    return base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")


def pick_token(cfg: Dict[str, Any]) -> str:
    """First token file of mail.token_files that exists, has a refresh token and the gmail.send scope (offline check, nothing printed)."""
    scope = cfg["mail"]["scope"]
    problems = []
    for name in cfg["mail"]["token_files"]:
        st = auth.token_status(name, scope)
        if st.get("ok"):
            return name
        problems.append("%s: %s" % (name, st.get("problem", "unusable")))
    raise MailError("no usable gmail.send token in secrets/ (%s)" % "; ".join(problems))


def _real_service(token_file: str, scope: str) -> Any:
    from googleapiclient.discovery import build
    creds = auth.user_credentials(token_file, [scope])
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def send_raw(raw: str, cfg: Dict[str, Any], token_file: Optional[str] = None, service_factory: Optional[Callable[[str, str], Any]] = None) -> Dict[str, Any]:
    """Send one already-encoded message.  `service_factory(token_file, scope)` must return an object with
    ``users().messages().send(userId='me', body={'raw': ...}).execute()``; default builds the real Gmail service."""
    scope = cfg["mail"]["scope"]
    tok = token_file or pick_token(cfg)
    svc = (service_factory or _real_service)(tok, scope)
    resp = svc.users().messages().send(userId="me", body={"raw": raw}).execute()
    return {"token_file": tok, "message_id": (resp or {}).get("id"), "thread_id": (resp or {}).get("threadId")}
