"""Official-email finder (STANDING RULE, user 2026-10-05: always try to get the official email).

Free and polite: reads only the company's OWN public website (home, contact, about pages), honours robots.txt, identifies itself as
LH2-Research-Bot/1.0, one request per second per host, never touches LinkedIn / logins / CAPTCHAs, spends no vendor credits.
Only addresses on the company's own domain are accepted.  The user wants the POC's (the person's) email, so only an address that carries the contact's name is returned as poc_email;
generic role addresses (info@, contact@ ...) are listed separately and never pushed.  Returns None when nothing credible is found - the
caller then pushes the lead without an email (never guesses a pattern address).  Python 3.9."""
import html
import re
import time
import urllib.robotparser
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urljoin, urlparse

import requests

UA = "LH2-Research-Bot/1.0 (+business research; contact bhanu.enamala@lh2.ai)"
PATHS = ["/", "/contact", "/contact-us", "/contactus", "/about", "/about-us", "/reach-us"]
ROLE_RANK = ["info", "contact", "hello", "enquiry", "enquiries", "inquiry", "sales", "business", "support", "care", "admin", "hr", "careers", "office", "mail"]
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
BAD_LOCAL = ("noreply", "no-reply", "donotreply", "example", "sentry", "wixpress", "user", "name", "yourname", "email", "test")
BAD_TLD = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js")


def _cf_decode(hexs: str) -> str:
    try:
        b = bytes.fromhex(hexs); k = b[0]
        return "".join(chr(c ^ k) for c in b[1:])
    except Exception:
        return ""


def _extract(text: str) -> List[str]:
    out = set()
    for m in re.finditer(r'data-cfemail="([0-9a-f]+)"', text):
        e = _cf_decode(m.group(1))
        if "@" in e: out.add(e)
    t = html.unescape(text)
    t = re.sub(r"\s*\[\s*at\s*\]\s*|\s*\(\s*at\s*\)\s*", "@", t, flags=re.I)
    for m in EMAIL_RE.findall(t):
        out.add(m)
    return [e.strip(".").lower() for e in out]


def _allowed(host_cache: Dict[str, Any], base: str, path: str) -> bool:
    rp = host_cache.get(base)
    if rp is None:
        rp = urllib.robotparser.RobotFileParser()
        try:
            r = requests.get(urljoin(base, "/robots.txt"), headers={"User-Agent": UA}, timeout=8)
            rp.parse(r.text.splitlines() if r.status_code == 200 else [])
        except Exception:
            rp.parse([])
        host_cache[base] = rp
    return rp.can_fetch(UA, urljoin(base, path))


def _score(email: str, first: str, last: str) -> int:
    local = email.split("@")[0]
    f, l = re.sub(r"[^a-z]", "", (first or "").lower()), re.sub(r"[^a-z]", "", (last or "").lower())
    lc = re.sub(r"[^a-z]", "", local)
    if f and l and (lc in (f + l, f + "." + l, f[0] + l, l + f, f + l[0], f) or (f in lc and l in lc)): return 0
    if f and len(f) > 2 and lc == f: return 1
    for i, r in enumerate(ROLE_RANK):
        if lc == r or lc.startswith(r): return 10 + i
    return 40


def find_one(domain: str, first: str = "", last: str = "", pause: float = 1.0, timeout: int = 10) -> Dict[str, Any]:
    cache = {}  # type: Dict[str, Any]
    cands = {}  # type: Dict[str, str]
    errors = 0
    for scheme in ("https://", "http://"):
        base = scheme + domain
        try:
            requests.head(base, headers={"User-Agent": UA}, timeout=8, allow_redirects=True)
        except Exception:
            errors += 1
            continue
        for path in PATHS:
            if not _allowed(cache, base, path): continue
            try:
                r = requests.get(base + path, headers={"User-Agent": UA}, timeout=timeout, allow_redirects=True)
            except Exception:
                time.sleep(pause); continue
            time.sleep(pause)
            if r.status_code != 200 or "text" not in r.headers.get("Content-Type", "text"): continue
            host = urlparse(r.url).hostname or domain
            for e in _extract(r.text[:600000]):
                ed = e.split("@")[1]
                if not (ed == domain or ed.endswith("." + domain) or domain.endswith("." + ed)): continue
                if any(b in e.split("@")[0] for b in BAD_LOCAL) or e.endswith(BAD_TLD): continue
                cands.setdefault(e, r.url)
            if len(cands) >= 6: break
        if cands or errors == 0: break
    ranked = sorted(cands, key=lambda e: (_score(e, first, last), len(e)))
    poc = [e for e in ranked if _score(e, first, last) <= 1]          # an address that is clearly THIS person's (name in the local part)
    return {"domain": domain, "poc_email": poc[0] if poc else None, "source": cands.get(poc[0]) if poc else None,
            "company_addresses": [e for e in ranked if e not in poc][:4]}   # generic ones are recorded for reference, never pushed


def find_many(leads: Iterable[Dict[str, Any]], workers: int = 10) -> Dict[str, Dict[str, Any]]:
    """leads: dicts with domain, first_name, last_name.  Returns {domain: result}."""
    leads = list(leads)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        res = list(ex.map(lambda l: find_one(l["domain"], l.get("first_name", ""), l.get("last_name", "")), leads))
    return {r["domain"]: r for r in res}
