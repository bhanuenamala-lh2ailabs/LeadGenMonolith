"""Normalisers: the ONE place that turns raw source values into the normalised shapes the database CHECKs demand.

Every function returns the normalised string, or ``None`` when the value cannot be normalised (the caller then keeps the raw spelling
and writes an ``ops_dq_issue``).  They never raise on bad input except :func:`norm_hs_id` (an id that is not an id is a bug).
The rules mirror the CHECK constraints of db/migrations/0002_canonical.sql (company_identifier, contact, suppression, contact_phone ...)
and the measured quirks of the real data (docs/review/schema-decisions.md, "importer normalisation contract").

Python 3.9 compatible.  No third-party dependencies (libphonenumber, when available, belongs to the importer: this module only does
the deterministic, DB-shaped part).
"""
import datetime
import re
import unicodedata
from typing import Optional, Tuple, Union
from urllib.parse import unquote, urlsplit

_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿ "), None)
_QUOTES = "\"'`“”‘’"
_DOMAIN_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9-]{2,63}$")


def _clean(value: object) -> str:
    s = unicodedata.normalize("NFKC", str(value)).translate(_ZERO_WIDTH).strip()
    return s.strip(_QUOTES).strip()


# ------------------------------------------------------------------------------------------------ HubSpot ids
def norm_hs_id(value: object) -> str:
    """HubSpot object / owner / user id -> digits without leading zeros ('0123' and '123' are the same id).  Raises ValueError otherwise."""
    s = _clean(value)
    if not s or not s.isdigit():
        raise ValueError("not a HubSpot numeric id: %r" % (value,))
    return str(int(s))


# ------------------------------------------------------------------------------------------------ domains / urls / names
def norm_domain(value: object) -> Optional[str]:
    """'https://WWW.Acme.com/path?x' -> 'acme.com'.  Strips quotes / zero-width characters, scheme, credentials, port, path, a leading
    'www.', a trailing dot; IDN labels are punycoded.  None if the result is not a plausible host name."""
    if value is None:
        return None
    s = _clean(value).lower()
    if not s:
        return None
    if "://" in s:
        s = urlsplit(s).netloc
    s = s.split("/")[0].split("?")[0].split("#")[0].rsplit("@", 1)[-1]
    s = re.sub(r":\d+$", "", s).strip(".")
    if s.startswith("www."):
        s = s[4:]
    try:
        s = s.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    return s if _DOMAIN_RE.match(s) and ".." not in s else None


def norm_email(value: object) -> Optional[str]:
    if value is None:
        return None
    s = _clean(value).lower()
    return s if re.match(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$", s) else None


def norm_company_name(value: object) -> Optional[str]:
    """Lower-case, whitespace-collapsed name (the shape suppression.company_name and company.name_norm require)."""
    if value is None:
        return None
    s = re.sub(r"\s+", " ", _clean(value).lower())
    return s or None


def norm_linkedin_company(value: object) -> Optional[str]:
    """'https://in.linkedin.com/company/63-Moons-%E2%84%A2/?trk=x' -> 'company/63-moons-tm'-style slug; keeps school/ and showcase/.
    Percent-escapes are decoded, quotes stripped.  None for person URLs or anything without a slug."""
    if value is None:
        return None
    s = unquote(_clean(value)).lower()
    m = re.search(r"(?:^|/)(company|school|showcase)/([^/?#\s]+)", s)
    if not m:
        return None
    slug = m.group(2).strip(_QUOTES + "/ ")
    return "%s/%s" % (m.group(1), slug) if slug else None


def norm_linkedin_person(value: object) -> Optional[str]:
    """'https://www.linkedin.com/in/Some-One/' -> 'in/some-one'.  /pub/ and /company/ URLs return None (keep the raw value only)."""
    if value is None:
        return None
    s = unquote(_clean(value)).lower()
    m = re.search(r"(?:^|/)in/([^/?#\s]+)", s)
    if not m:
        return None
    slug = m.group(1).strip(_QUOTES + "/ ")
    return "in/%s" % slug if slug else None


def norm_cin(value: object) -> Optional[str]:
    """Company Identification Number: 21 upper-case alphanumerics, else None ('' and LLPINs are not CINs)."""
    s = _clean(value or "").upper().replace(" ", "")
    return s if len(s) == 21 and s.isalnum() else None


def norm_llpin(value: object) -> Optional[str]:
    """LLP identification number 'AAE-7433'."""
    s = _clean(value or "").upper()
    return s if re.match(r"^[A-Z]{3}-[0-9]{4}$", s) else None


_COUNTRY = {"india": "IN", "bharat": "IN", "united kingdom": "GB", "uk": "GB", "u.k.": "GB", "great britain": "GB", "england": "GB", "scotland": "GB",
            "wales": "GB", "northern ireland": "GB", "united states": "US", "united states of america": "US", "usa": "US", "u.s.a.": "US",
            "u.s.": "US", "america": "US", "united arab emirates": "AE", "uae": "AE", "singapore": "SG", "canada": "CA", "australia": "AU",
            "germany": "DE", "france": "FR", "netherlands": "NL", "ireland": "IE", "sri lanka": "LK", "bangladesh": "BD", "nepal": "NP"}


def norm_country(value: object) -> Optional[str]:
    """Country name or code -> ISO-3166-1 alpha-2 ('India' -> 'IN', 'uk' -> 'GB', 'in' -> 'IN'); None if unknown (keep the raw text, raise a DQ issue)."""
    if value is None:
        return None
    s = re.sub(r"\s+", " ", _clean(value).lower())
    if s in _COUNTRY:
        return _COUNTRY[s]
    return s.upper() if re.match(r"^[a-z]{2}$", s) else None


# ------------------------------------------------------------------------------------------------ phones
def norm_phone_e164(raw: object, default_country: str = "IN") -> Optional[str]:
    """Deterministic E.164 for the spellings seen in the data: '+91 98765 43210', '+91-7042813998', '0091 98765 43210', '09876543210',
    '9742044482' (10-digit national, assumed India).  Returns None when unsure (the caller keeps phone_raw and a NULL e164).  It does NOT
    classify mobile vs landline: set contact_phone.phone_type from libphonenumber; the +91 gate stays closed until it says 'mobile'."""
    if raw is None:
        return None
    s = _clean(raw)
    digits = re.sub(r"[^0-9]", "", s)
    if not digits:
        return None
    if s.startswith("+"):
        e164 = "+" + digits
    elif digits.startswith("00"):
        e164 = "+" + digits[2:]
    elif default_country == "IN" and len(digits) == 12 and digits.startswith("91"):
        e164 = "+" + digits
    elif default_country == "IN" and len(digits) == 11 and digits.startswith("0"):
        e164 = "+91" + digits[1:]
    elif default_country == "IN" and len(digits) == 10:
        e164 = "+91" + digits
    else:
        return None
    return e164 if re.match(r"^\+[1-9][0-9]{7,14}$", e164) else None


# ------------------------------------------------------------------------------------------------ timestamps
_IST = datetime.timedelta(hours=5, minutes=30)


def to_utc_ms(value: Union[str, int, float, datetime.datetime], assume: str = "utc") -> Optional[str]:
    """Any of the timestamp spellings in the legacy sources -> the canonical 'YYYY-MM-DDTHH:MM:SS.mmmZ' (UTC, 24 chars).

    Accepts: naive 'YYYY-MM-DD HH:MM:SS' (tam, SQLite datetime('now'): UTC) or ISO 'T' form (radar); microseconds ('...123456Z', itsvc);
    '+00:00' / '+05:30' / '+0530' offsets (corpus, pipeline, the gsheets catalog 'generated'); epoch seconds (resolver, searchledger);
    date-only 'YYYY-MM-DD' (midnight).  ``assume='ist'`` interprets NAIVE values as IST wall clock (STAGE_TRANSITIONS.csv).
    Returns None for anything else ('YYYY-MM', 'YYYY', RFC-822 dates: the caller decides)."""
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        dt = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        dt = datetime.datetime(1970, 1, 1) + datetime.timedelta(seconds=float(value))
        return _fmt(dt)
    else:
        s = str(value).strip()
        if not s:
            return None
        if re.match(r"^[0-9]{9,11}(\.[0-9]+)?$", s):
            return to_utc_ms(float(s))
        m = re.match(r"^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?)?\s*(Z|z|[+-]\d{2}:?\d{2})?$", s)
        if not m:
            return None
        y, mo, d, hh, mi, ss, frac, tz = m.groups()
        try:
            dt = datetime.datetime(int(y), int(mo), int(d), int(hh or 0), int(mi or 0), int(ss or 0), int((frac or "0")[:6].ljust(6, "0")))
        except ValueError:
            return None
        if tz and tz not in ("Z", "z"):
            sign = 1 if tz[0] == "+" else -1
            t = tz[1:].replace(":", "")
            dt = dt - sign * datetime.timedelta(hours=int(t[:2]), minutes=int(t[2:]))
        elif not tz and assume == "ist":
            dt = dt - _IST
        return _fmt(dt)
    if dt.tzinfo is not None:
        dt = (dt - dt.utcoffset()).replace(tzinfo=None)
    return _fmt(dt)


def _fmt(dt: datetime.datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (dt.microsecond // 1000)


def split_pair(value: str) -> Tuple[str, str]:
    """('a|b' -> ('a','b')): origin_ref.source_pk for composite keys joins with '|'."""
    a, _, b = value.partition("|")
    return a, b
