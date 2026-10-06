"""Flat-file importer: the CSV / XLSX / JSON "excel-sheet databases" of the three legacy repos -> LeadGenMonolith.

Four independent passes (``--only``), each idempotent and each safe to re-run:

``snapshots``  legacy daily funnel snapshot JSON (RAT ``snapshots/rat_*.json``, hubspot ``crm_mirror/data/snapshots/*.json``,
               companyOps ``crm_mirror/data/snapshots/full_funnel_cluster{1,2}_*.json``) -> ``rpt_legacy_snapshot`` (verbatim
               ``payload_json`` + the parsed ``flow`` / ``current_state`` / ``dashboard_flow`` / ``cumulative`` /
               ``engaged_deal_ids`` columns).  Used ONLY to cross-check the recomputed report.  Unique key = (family, account,
               pipeline, owner, day): same file sha256 = skip, changed = update.
``whale``      the whale lists (``whales.csv``, ``Whale_List_30.csv``, ``Whale_List_Untouched_FINAL.csv``,
               ``whale_list_27_NEVER_PUSH_TO_HUBSPOT.csv``) -> ``suppression(kind='never_push')`` with domains, company names and
               (where present) contact LinkedIn / e-mail / phone normalised by :mod:`leadgen.norm`.  HARD RULE: these companies
               are never pushed anywhere; a deactivated row is re-activated by this pass.  Global (``account_id`` NULL).
``files``      every business CSV / XLSX (all sheets) under ``legacy/{companyOps,RapidActionTeam,hubspot}`` plus the stale HubSpot
               JSON mirrors (``crm_mirror/data/{deals,contacts,companies}.json`` and ``index/``) -> ``ext_file_catalog`` +
               ``ext_file_row`` (migration 0015).  Junk / vendor / credential-like / giant non-list files are catalogued with
               ``role='skipped'`` and a reason.  The JSON mirrors are staged only: they never touch canonical tables.
``link``       :func:`link_files`: attaches ``ext_file_row`` rows to golden companies / contacts through STRONG keys only
               (``company_identifier`` root_domain / linkedin_company, unique contact e-mail / person LinkedIn) and fills
               ``suppression.company_id`` for whale rows.  Re-runnable at any time (after the other importers have loaded
               companies); a full recompute, so merges are followed.

PII: rows contain personal data.  Nothing here prints cell values - only counts, file paths and exception class names.

CLI (never touches ``db/leadgen.sqlite`` unless ``--db`` / ``$LEADGEN_DB`` says so)::

    python -m leadgen.legacy_import.flatfiles [--db PATH] [--only snapshots|whale|files|link|all] [--dry-run]
                                              [--include SUBSTR] [--force] [--keep-duplicates]

Python 3.9 compatible.
"""
import argparse
import codecs
import csv
import datetime
import hashlib
import io
import json
import os
import re
import sqlite3
import sys
import time
import warnings
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple

from leadgen import db as ldb
from leadgen import norm

TOOL_VERSION = "flatfiles-1"
LEGACY_REPOS = ("companyOps", "RapidActionTeam", "hubspot")
#: only files up to this size are loaded unconditionally; bigger ones must look like a list of companies / contacts
BIG_FILE_BYTES = 50 * 1000 * 1000
BATCH = 2000
MAX_HEADER_SCAN = 8

_SKIP_DIRS = {"node_modules", ".venv", "venv", ".git", "__pycache__", "site-packages", "dist-packages", ".idea", ".vscode"}
_DATA_EXT = {".csv": "csv", ".tsv": "tsv", ".xlsx": "xlsx", ".xlsm": "xlsx", ".xls": "xls"}
_CREDENTIAL_NAME = re.compile(r"client_secret|credential|token|secret|password|api[_-]?key|service[_-]?account", re.I)

_GENERIC_HOSTS = (
    "linkedin.com", "facebook.com", "twitter.com", "x.com", "instagram.com", "youtube.com", "youtu.be", "google.com", "goo.gl", "g.page",
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.in", "outlook.com", "hotmail.com", "live.com", "rediffmail.com", "icloud.com",
    "crunchbase.com", "github.com", "bit.ly", "wa.me", "tracxn.com", "zoominfo.com", "apollo.io", "goodfirms.co", "clutch.co", "naukri.com",
    "indeed.com", "glassdoor.com", "wikipedia.org", "medium.com", "apple.com", "wixsite.com", "blogspot.com", "wordpress.com", "business.site",
    "weebly.com", "godaddysites.com", "canva.com", "linktr.ee", "tiktok.com", "pinterest.com", "justdial.com", "indiamart.com",
)
_ROLE_PRIORITY = {"source_of_truth": 0, "pool": 1, "mirror": 2, "export": 3, "regenerable": 4}
#: files whose "domain" column was guessed from the company name (e.g. "The French Gourmet, Inc." -> the.com): extracting it would link wrong companies
_GUESSED_DOMAIN_PATH = re.compile(r"shutdown-radar-us/|shutdown_companies_", re.I)
_FOREIGN_PHONE_PATH = re.compile(r"shutdown-radar-us|/romania/|uk_proptech|warn_", re.I)


# ======================================================================================== small helpers
def _now() -> str:
    return ldb.utc_now_iso()


def project_root() -> str:
    return ldb.PROJECT_ROOT


def _rel(path: str, legacy_root: Optional[str] = None) -> str:
    """Catalog path: relative to the directory that CONTAINS ``legacy/`` (the repo root), always '/'-separated."""
    base = os.path.dirname(os.path.abspath(legacy_root)) if legacy_root else project_root()
    return os.path.relpath(os.path.abspath(path), base).replace(os.sep, "/")


def _mtime_iso(path: str) -> Optional[str]:
    try:
        dt = datetime.datetime.utcfromtimestamp(os.path.getmtime(path))
    except OSError:
        return None
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (dt.microsecond // 1000)


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_and_encoding(path: str, want_encoding: bool) -> Tuple[str, Optional[str]]:
    """One pass over the bytes: sha256 and (for text files) the first of utf-8(-sig) / cp1252 / latin-1 that decodes the WHOLE file."""
    h = hashlib.sha256()
    dec = codecs.getincrementaldecoder("utf-8")(errors="strict")
    utf8_ok = True
    first = True
    bom = False
    bad_cp1252 = False
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
            if not want_encoding:
                continue
            if first:
                bom = chunk.startswith(codecs.BOM_UTF8)
                first = False
            if utf8_ok:
                try:
                    dec.decode(chunk, final=False)
                except UnicodeDecodeError:
                    utf8_ok = False
            if not utf8_ok and not bad_cp1252:
                # these five bytes are undefined in cp1252
                if any(b in chunk for b in (b"\x81", b"\x8d", b"\x8f", b"\x90", b"\x9d")):
                    bad_cp1252 = True
    if not want_encoding:
        return h.hexdigest(), None
    if utf8_ok:
        try:
            dec.decode(b"", final=True)
        except UnicodeDecodeError:
            utf8_ok = False
    if utf8_ok:
        return h.hexdigest(), "utf-8-sig" if bom else "utf-8"
    return h.hexdigest(), "latin-1" if bad_cp1252 else "cp1252"


def _json_default(o: Any) -> str:
    if isinstance(o, (datetime.datetime, datetime.date, datetime.time)):
        return o.isoformat()
    return str(o)


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=_json_default)


# ======================================================================================== classification
class Plan(object):
    """What to do with one data file (decided from its path and size, before any parsing)."""

    def __init__(self, path: str, abspath: str, repo: str, kind: str, role: str, size: int) -> None:
        self.path = path                  # repo-root relative, '/' separators
        self.abspath = abspath
        self.repo = repo
        self.kind = kind
        self.role = role
        self.size = size
        self.skip_reason = None           # type: Optional[str]
        self.never_push = False
        self.stale_mirror = False
        self.hints = {}                   # type: Dict[str, str]
        self.note = None                  # type: Optional[str]
        self.no_domain = False            # the file's domain column is a heuristic guess: kept in row_json, never extracted as a lookup key


#: (regex on the path below ``legacy/``, role).  First match wins.  Roles follow docs/discovery/data-inventory.md section 3.
_ROLE_RULES = [
    (r"(^|/)whales?[_.]", "pool"),                                              # whale lists (also flagged never_push)
    (r"held_pool", "pool"),
    (r"^hubspot/reserve/", "pool"),
    (r"^hubspot/crm_mirror/holding/", "pool"),
    (r"^RapidActionTeam/leads/", "pool"),
    (r"^hubspot/godown/[^/]+/prequal_out/lanes/", "pool"),
    (r"^hubspot/godown/.*/lanes/", "pool"),
    (r"^hubspot/godown/ceo_reality_check/", "mirror"),                          # full HubSpot Main extract of 2026-08-30
    (r"^hubspot/_archive/already_in_hubspot/(DEALS_|DEAD_LOST)", "mirror"),
    (r"^hubspot/_archive/", "export"),
    (r"^(companyOps/data/imports|hubspot/exports/source_files)/", "source_of_truth"),      # inbound files
    (r"^(RapidActionTeam|companyOps|hubspot)/audit/", "source_of_truth"),                 # push provenance
    (r"^hubspot/godown/shutdown-radar/data/manual/", "source_of_truth"),
    (r"^companyOps/tam/seeds/", "source_of_truth"),
    (r"^hubspot/godown/shutdown-radar-us/pipeline/(cache|raw)/", "regenerable"),         # state WARN downloads
    (r"^hubspot/godown/shutdown-radar/data/out/", "regenerable"),
    (r"^hubspot/godown/gujarat_qualify/apify_out", "regenerable"),
    (r"^hubspot/godown/[^/]+/(prequal_out|out)/", "regenerable"),
    (r"^companyOps/tam/exports/", "regenerable"),
    (r"^hubspot/TAMBuildSpecs/_corpus/data/", "regenerable"),
    (r"^hubspot/itsvc-tam/out/", "regenerable"),
    (r"^companyOps/pipeline/", "regenerable"),
    (r"^(companyOps|hubspot)/(data/)?exports/", "export"),
    (r"campaign-leads-export", "export"),
]
_ROLE_RULES_C = [(re.compile(p, re.I), r) for p, r in _ROLE_RULES]
_WHALE_NAME = re.compile(r"(^|/)whales?[_.]", re.I)

#: JSON files staged by the ``files`` pass (path below legacy/, role, is_stale_mirror, hints column -> role)
JSON_MIRRORS = [
    ("hubspot/crm_mirror/data/deals.json", "mirror", True, {}),
    ("hubspot/crm_mirror/data/contacts.json", "mirror", True, {}),
    ("hubspot/crm_mirror/data/companies.json", "mirror", True, {}),
    ("hubspot/crm_mirror/data/index/by_domain.json", "mirror", True, {"_key": "domain"}),
    ("hubspot/crm_mirror/data/index/by_linkedin.json", "mirror", True, {"_key": "linkedin"}),
    ("hubspot/crm_mirror/data/index/by_name.json", "mirror", True, {"_key": "company_name"}),
    ("hubspot/crm_mirror/data/index/deal_names.json", "mirror", True, {"_key": "company_name"}),
    ("hubspot/crm_mirror/data/index/hubspot_all_names.json", "mirror", True, {"_value": "company_name"}),
    ("hubspot/crm_mirror/data/index/sheet_worked_exclude.json", "source_of_truth", False, {}),
]


def role_for(rel_below_legacy: str) -> str:
    for rx, role in _ROLE_RULES_C:
        if rx.search(rel_below_legacy):
            return role
    return "export"


def discover(legacy_root: str, include: Optional[str] = None) -> List[Plan]:
    """All candidate data files (csv / tsv / xlsx / xls) and the JSON mirrors, sorted by path.  No file is parsed here."""
    plans = []  # type: List[Plan]
    root = os.path.abspath(legacy_root)
    for repo in LEGACY_REPOS:
        base = os.path.join(root, repo)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
            for fn in sorted(filenames):
                ext = os.path.splitext(fn)[1].lower()
                if ext not in _DATA_EXT:
                    continue
                ap = os.path.join(dirpath, fn)
                if os.path.islink(ap) or not os.path.isfile(ap):
                    continue
                below = os.path.relpath(ap, root).replace(os.sep, "/")
                if include and include not in below:
                    continue
                p = Plan(_rel(ap, legacy_root), ap, repo, _DATA_EXT[ext], role_for(below), os.path.getsize(ap))
                p.never_push = bool(_WHALE_NAME.search(below))
                if _GUESSED_DOMAIN_PATH.search(below):
                    p.no_domain = True
                    p.note = "domain column is a name-derived guess (e.g. 'The French Gourmet' -> the.com): not extracted as a lookup key"
                _decide_skip(p, below)
                plans.append(p)
    for rel, role, stale, hints in JSON_MIRRORS:
        ap = os.path.join(root, rel)
        if not os.path.isfile(ap) or (include and include not in rel):
            continue
        p = Plan(_rel(ap, legacy_root), ap, rel.split("/")[0], "json", role, os.path.getsize(ap))
        p.stale_mirror = stale
        p.hints = dict(hints)
        if stale:
            p.note = "stale mirror (frozen 2026-07-29): never overwrite live HubSpot-derived data with these rows"
        _decide_skip(p, rel)
        plans.append(p)
    plans.sort(key=lambda x: x.path)
    return plans


def _decide_skip(p: Plan, below: str) -> None:
    base = os.path.basename(below)
    if base.startswith("~$"):
        p.skip_reason = "office lock/temp file"
    elif p.size == 0:
        p.skip_reason = "empty file"
    elif _CREDENTIAL_NAME.search(base):
        p.skip_reason = "credential-like file name: never staged"
    elif p.kind == "xls":
        p.skip_reason = "legacy .xls workbook: no reader installed (xlrd)"
    if p.skip_reason:
        p.role = "skipped"


# ======================================================================================== table readers
def _clean_cell(v: Any) -> Any:
    """Cell -> JSON-safe value, or None when empty."""
    if v is None:
        return None
    if isinstance(v, str):
        s = v.replace("\x00", "").strip()
        return s if s else None
    if isinstance(v, bool):
        return v
    if isinstance(v, float):
        if v != v or v in (float("inf"), float("-inf")):
            return str(v)
        if v == int(v) and abs(v) < 1e15:
            return int(v)
        return v
    if isinstance(v, (int,)):
        return v
    if isinstance(v, (datetime.datetime, datetime.date, datetime.time)):
        return v.isoformat()
    s = str(v).strip()
    return s or None


def _header_names(cells: Sequence[Any]) -> List[str]:
    out = []  # type: List[str]
    seen = {}  # type: Dict[str, int]
    for i, c in enumerate(cells):
        v = _clean_cell(c)
        name = str(v) if v is not None else "col_%d" % (i + 1)
        n = seen.get(name, 0) + 1
        seen[name] = n
        out.append(name if n == 1 else "%s__%d" % (name, n))
    return out


def _is_numberish(v: Any) -> bool:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return True
    return isinstance(v, str) and bool(re.match(r"^[\d.,+\-]+$", v))


class SheetStream(object):
    """One table: ``name``, ``columns`` and an iterator ``rows()`` of (row_no, {header: value}) for NON-blank data rows."""

    def __init__(self, name: str, source: Iterator[Sequence[Any]]) -> None:
        self.name = name
        self._src = source
        self.columns = []  # type: List[str]
        self.blank_rows = 0
        self.ragged_rows = 0
        self.title_rows_skipped = 0
        self._first_data = []  # type: List[Sequence[Any]]
        self._detect_header()

    def _detect_header(self) -> None:
        buf = []  # type: List[Sequence[Any]]
        for raw in self._src:
            cells = [_clean_cell(c) for c in raw]
            while cells and cells[-1] is None:
                cells.pop()
            if not cells:
                continue                               # leading blank rows
            buf.append(cells)
            if len(buf) >= MAX_HEADER_SCAN:
                break
        if not buf:
            return
        idx = 0
        # leading rows with a single filled cell are titles when a real (2+ cell) header follows within the scan window
        for i, cells in enumerate(buf):
            if sum(1 for c in cells if c is not None) >= 2:
                idx = i
                break
        self.title_rows_skipped = idx
        head = buf[idx]
        nonempty = [c for c in head if c is not None]
        if nonempty and all(_is_numberish(c) for c in nonempty) and len(nonempty) > 1:
            self.columns = ["col_%d" % (i + 1) for i in range(len(head))]    # a data row, not a header
            self._first_data = buf[idx:]
        else:
            self.columns = _header_names(head)
            self._first_data = buf[idx + 1:]

    def rows(self) -> Iterator[Tuple[int, Dict[str, Any]]]:
        n = 0
        cols = self.columns
        chain = iter(self._first_data) if self._first_data else iter(())
        for raw in _chain(chain, self._src):
            n += 1
            cells = [_clean_cell(c) for c in raw]
            row = {}  # type: Dict[str, Any]
            for i, v in enumerate(cells):
                if v is None:
                    continue
                if i < len(cols):
                    row[cols[i]] = v
                else:
                    self.ragged_rows += 1
                    row["col_%d" % (i + 1)] = v
            if not row:
                self.blank_rows += 1
                continue
            yield n, row


def _chain(a: Iterator[Any], b: Iterator[Any]) -> Iterator[Any]:
    for x in a:
        yield x
    for x in b:
        yield x


def detect_delimiter(text_head: str) -> str:
    """Most frequent of , ; tab | in the first physical line (outside double quotes)."""
    line = text_head.split("\n", 1)[0]
    counts = {d: 0 for d in (",", ";", "\t", "|")}
    in_q = False
    for ch in line:
        if ch == '"':
            in_q = not in_q
        elif not in_q and ch in counts:
            counts[ch] += 1
    best = max(counts.items(), key=lambda kv: (kv[1], kv[0] == ","))
    return best[0] if best[1] > 0 else ","


def _set_csv_limit() -> None:
    limit = sys.maxsize
    while True:
        try:
            csv.field_size_limit(limit)
            return
        except OverflowError:
            limit = int(limit / 2)


class _Opened(object):
    """Context for one open file: ``sheets()`` yields SheetStream objects one after another (streaming)."""

    def __init__(self, plan: Plan, encoding: Optional[str]) -> None:
        self.plan = plan
        self.encoding = encoding
        self.delimiter = None  # type: Optional[str]

    def sheets(self) -> Iterator[SheetStream]:
        k = self.plan.kind
        if k in ("csv", "tsv"):
            yield from self._csv()
        elif k == "xlsx":
            yield from self._xlsx()
        elif k == "json":
            yield from self._json()
        else:
            raise ValueError("unsupported kind %s" % k)

    def _csv(self) -> Iterator[SheetStream]:
        _set_csv_limit()
        enc = self.encoding or "utf-8"
        with io.open(self.plan.abspath, "r", encoding=enc, errors="replace", newline="") as fh:
            head = fh.read(65536)
            fh.seek(0)
            self.delimiter = "\t" if self.plan.kind == "tsv" else detect_delimiter(head.lstrip("﻿"))
            reader = csv.reader((ln.replace("\x00", "") for ln in fh), delimiter=self.delimiter, quotechar='"')
            ss = SheetStream("csv", _csv_rows(reader))
            yield ss

    def _xlsx(self) -> Iterator[SheetStream]:
        import openpyxl
        warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")
        wb = openpyxl.load_workbook(self.plan.abspath, read_only=True, data_only=True)
        try:
            for ws in wb.worksheets:
                try:
                    ws.reset_dimensions()          # read-only workbooks may carry a wrong <dimension>
                except Exception:
                    pass
                yield SheetStream(ws.title, ws.iter_rows(values_only=True))
        finally:
            wb.close()

    def _json(self) -> Iterator[SheetStream]:
        with io.open(self.plan.abspath, "r", encoding="utf-8-sig") as fh:
            obj = json.load(fh)
        rows, meta = json_records(obj)
        if meta:
            self.plan.note = ((self.plan.note + "; ") if self.plan.note else "") + "json meta: " + _dumps(meta)[:2000]
        cols = []  # type: List[str]
        seen = set()  # type: Set[str]
        for r in rows:
            for k in r:
                if k not in seen:
                    seen.add(k)
                    cols.append(k)
        ss = SheetStream.__new__(SheetStream)
        ss.name = "json"
        ss.columns = cols
        ss.blank_rows = ss.ragged_rows = ss.title_rows_skipped = 0
        ss._first_data = []
        ss._src = iter(())

        def gen() -> Iterator[Tuple[int, Dict[str, Any]]]:
            for i, r in enumerate(rows, 1):
                row = {}
                for k, v in r.items():
                    cv = v if isinstance(v, (dict, list)) else _clean_cell(v)
                    if cv is not None and cv != "" and cv != [] and cv != {}:
                        row[k] = cv
                if row:
                    yield i, row
                else:
                    ss.blank_rows += 1
        ss.rows = gen  # type: ignore[assignment]
        yield ss


def _csv_rows(reader: Iterator[List[str]]) -> Iterator[List[str]]:
    while True:
        try:
            yield next(reader)
        except StopIteration:
            return
        except csv.Error:
            yield []                       # a malformed physical row becomes a blank row (counted)


def json_records(obj: Any) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """JSON document -> (rows, meta).  list of objects = rows; list of scalars = {'_value': x}; object whose values are all
    scalars/lists = {'_key': k, '_value': v}; an object containing a list of objects = those rows with its scalar siblings as meta."""
    if isinstance(obj, list):
        rows = [x if isinstance(x, dict) else {"_value": x} for x in obj]
        return rows, None
    if isinstance(obj, dict):
        best = None
        tables = [k for k, v in obj.items() if isinstance(v, list) and v and all(isinstance(x, dict) for x in v)]
        if len(tables) == 1 and len(obj) <= 12:            # one table inside a small wrapper object; anything wider is a key -> value mapping
            best = tables[0]
        if best is not None:
            meta = {k: v for k, v in obj.items() if k != best and not isinstance(v, (list, dict))}
            return list(obj[best]), meta
        rows = []
        for k, v in obj.items():
            if isinstance(v, dict):
                r = {"_key": k}
                r.update(v)
                rows.append(r)
            else:
                rows.append({"_key": k, "_value": v})
        return rows, None
    return [{"_value": obj}], None


# ======================================================================================== key extraction
def _hdr_key(h: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(h).lower()).strip("_")


_DOMAIN_SET = {"domain", "domains", "company_domain", "company_domain_name", "website", "web_site", "websites", "company_website", "website_url",
               "root_domain", "primary_domain", "lh2_domain", "org_website", "organization_website", "company_url", "companies_website",
               "account_website", "homepage", "company_site", "corporate_website", "official_website", "site", "url", "web", "domain_name"}
_DOMAIN_RX = re.compile(r"^(company|org|organi[sz]ation|account|firm)_?(domain|website|url|site)(_name)?$")
_NAME_SET = {"company", "company_name", "organization", "organisation", "organization_name", "organisation_name", "account_name", "firm",
             "firm_name", "display_name", "legal_name", "dealname", "deal_name", "company_name_for_emails", "business_name", "account"}
_EMAIL_BAD = ("status", "sent", "verif", "source", "confid", "type", "quality", "count", "optout", "opt_out", "bounce", "valid", "score",
              "date", "found", "exists", "note")
_PHONE_BAD = ("is_", "status", "verif", "type", "source", "count", "flag", "indian", "valid", "found", "date", "e164_ok", "score", "note",
              "attempt", "call_", "_called")
_PHONE_RX = re.compile(r"(phone|mobile|whatsapp|telephone|cell|contact_no|contact_number|(^|_)tel($|_))")


class Columns(object):
    """Which columns of a sheet carry which lookup key (decided once per sheet from the header names)."""

    def __init__(self, headers: Sequence[str], hints: Optional[Dict[str, str]] = None) -> None:
        hints = hints or {}
        self.domain = []  # type: List[str]
        self.linkedin = []  # type: List[str]
        self.email = []  # type: List[str]
        self.phone = []  # type: List[str]
        self.name = []  # type: List[str]
        name_plain = []  # type: List[str]
        for h in headers:
            k = _hdr_key(h)
            role = hints.get(h)
            if role == "domain":
                self.domain.append(h)
            elif role == "linkedin":
                self.linkedin.append(h)
            elif role == "company_name":
                self.name.append(h)
            elif k in _DOMAIN_SET or _DOMAIN_RX.match(k):
                self.domain.append(h)
            elif "linkedin" in k or k in ("li_url", "li_profile") or k.startswith("li_"):
                self.linkedin.append(h)
            elif ("email" in k or k in ("mail", "e_mail") or k.endswith("_mail")) and not any(b in k for b in _EMAIL_BAD):
                self.email.append(h)
            elif _PHONE_RX.search(k) and not any(b in k for b in _PHONE_BAD):
                self.phone.append(h)
            elif k in _NAME_SET:
                self.name.append(h)
            elif k == "name":
                name_plain.append(h)
        # a plain 'name' column is a company name only in a company table (one that also has a domain column)
        if name_plain and self.domain and not self.name:
            self.name.extend(name_plain)
        # prefer unambiguous domain columns first
        self.domain.sort(key=lambda h: 0 if _hdr_key(h) in ("domain", "root_domain", "company_domain", "lh2_domain") else (2 if _hdr_key(h) == "url" else 1))


def _first_token(v: Any) -> str:
    return re.split(r"[\s,;|]+", str(v).strip(), 1)[0] if v is not None else ""


def domain_of(value: Any) -> Optional[str]:
    """Root domain for a domain / website cell, None for blanks, junk and generic hosts (linkedin.com, gmail.com ...)."""
    if value is None or isinstance(value, (dict, list)):
        return None
    d = norm.norm_domain(_first_token(value))
    if d is None:
        return None
    for g in _GENERIC_HOSTS:
        if d == g or d.endswith("." + g):
            return None
    return d


def extract_keys(cols: Columns, row: Dict[str, Any], phone_country: str = "IN") -> Tuple[Optional[str], ...]:
    """(company_domain_norm, linkedin_norm, person_linkedin_norm, email_norm, phone_e164, company_name_norm) for one row."""
    dom = li_c = li_p = email = phone = name = None
    for h in cols.domain:
        v = row.get(h)
        dom = domain_of(v)
        if dom:
            break
        if li_c is None and isinstance(v, str) and "linkedin." in v.lower():      # a company LinkedIn page typed into the website column
            li_c = norm.norm_linkedin_company(_first_token(v))
    for h in cols.linkedin:
        v = row.get(h)
        if v is None or isinstance(v, (dict, list)):
            continue
        for tok in re.split(r"[\s,;|]+", str(v)):
            if not tok:
                continue
            c = norm.norm_linkedin_company(tok)
            if c and li_c is None:
                li_c = c
                continue
            p = norm.norm_linkedin_person(tok)
            if p and li_p is None:
                li_p = p
    for h in cols.email:
        v = row.get(h)
        if v is None or isinstance(v, (dict, list)):
            continue
        for tok in re.split(r"[\s,;|]+", str(v)):
            e = norm.norm_email(tok) if tok else None
            if e:
                email = e
                break
        if email:
            break
    for h in cols.phone:
        v = row.get(h)
        if v is None or isinstance(v, (dict, list)):
            continue
        for tok in re.split(r"[,;/|]+", str(v)):
            ph = norm.norm_phone_e164(tok, default_country=phone_country) if tok.strip() else None
            if ph:
                phone = ph
                break
        if phone:
            break
    for h in cols.name:
        v = row.get(h)
        if isinstance(v, str):
            name = norm.norm_company_name(v)
            if name:
                break
    return dom, li_c, li_p, email, phone, name


# ======================================================================================== ops helpers
def _start_run(con: sqlite3.Connection, kind: str, source_name: str, fingerprint: Optional[str], params: Dict[str, Any]) -> int:
    with ldb.transaction(con):
        cur = con.execute("INSERT INTO ops_import_run (kind, source_name, source_path, source_fingerprint, params_json, tool_version) VALUES (?,?,?,?,?,?)",
                          (kind, source_name, "legacy", fingerprint, _dumps(params), TOOL_VERSION))
        return int(cur.lastrowid)


def _finish_run(con: sqlite3.Connection, run_id: int, status: str, read: int, written: int, skipped: int, rejected: int, error: Optional[str] = None) -> None:
    with ldb.transaction(con):
        con.execute("UPDATE ops_import_run SET status=?, finished_at=?, rows_read=?, rows_written=?, rows_skipped=?, rows_rejected=?, error=? WHERE import_run_id=?",
                    (status, _now(), read, written, skipped, rejected, error, run_id))


def dq_issue(con: sqlite3.Connection, run_id: Optional[int], rule: str, severity: str, fingerprint: str, message: str,
             details: Optional[Dict[str, Any]] = None, entity_type: Optional[str] = None, entity_ref: Optional[str] = None,
             account_id: Optional[str] = None) -> None:
    """Idempotent data-quality finding (upsert on fingerprint; a repeat only refreshes last_seen_at / details).  Never put cell values in here."""
    now = _now()
    con.execute(
        "INSERT INTO ops_dq_issue (fingerprint, rule_code, severity, account_id, entity_type, entity_ref, message, details_json, import_run_id, first_seen_at, last_seen_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(fingerprint) DO UPDATE SET last_seen_at=excluded.last_seen_at, details_json=excluded.details_json, "
        "import_run_id=excluded.import_run_id, message=excluded.message",
        (fingerprint, rule, severity, account_id, entity_type, entity_ref, message, _dumps(details or {}), run_id, now, now))


def _open_db(path: Optional[str], dry_run: bool) -> Optional[sqlite3.Connection]:
    target = ldb.db_path(path)
    if dry_run:
        if target == ":memory:" or not os.path.exists(target):
            return None
        return ldb.connect(path, readonly=True)
    return ldb.connect(path, must_exist=True)


def _require(con: Optional[sqlite3.Connection], *tables: str) -> None:
    missing = [t for t in tables if not _has_table(con, t)]
    if missing:
        raise RuntimeError("missing table(s) %s: run `python -m leadgen.db migrate` (migration 0015_flatfile_import) first" % ", ".join(missing))


def _has_table(con: Optional[sqlite3.Connection], name: str) -> bool:
    if con is None:
        return False
    return con.execute("SELECT 1 FROM sqlite_master WHERE name = ? AND type IN ('table','view')", (name,)).fetchone() is not None


# ======================================================================================== pass 1: snapshots
_D = r"(\d{4}-\d{2}-\d{2})"
#: (source_repo, dir below legacy/<repo>, filename regex, family, account, pipeline_id or None, owner group index or None)
SNAPSHOT_SOURCES = [
    ("RapidActionTeam", "snapshots", re.compile(r"^rat_%s\.json$" % _D), "rat", "rat", "2575252183", False),
    ("hubspot", "crm_mirror/data/snapshots", re.compile(r"^coding_funnel_%s\.json$" % _D), "main_coding", "main", "default", False),
    ("hubspot", "crm_mirror/data/snapshots", re.compile(r"^full_funnel_coopsglobal_%s\.json$" % _D), "main_coopsglobal", "main", "2425754306", False),
    ("hubspot", "crm_mirror/data/snapshots", re.compile(r"^full_funnel_owner_([A-Za-z0-9]+)_%s\.json$" % _D), "main_owner", "main", None, True),
    ("hubspot", "crm_mirror/data/snapshots", re.compile(r"^full_funnel_%s\.json$" % _D), "main_full", "main", None, False),
    ("hubspot", "crm_mirror/data/snapshots", re.compile(r"^hubspot_%s\.json$" % _D), "main_hubspot_mirror", "main", None, False),
    ("companyOps", "crm_mirror/data/snapshots", re.compile(r"^full_funnel_cluster1_%s\.json$" % _D), "companyops_cluster", "companyops", "default", False),
    ("companyOps", "crm_mirror/data/snapshots", re.compile(r"^full_funnel_cluster2_%s\.json$" % _D), "companyops_cluster", "companyops", "2464812771", False),
]
_METRIC_BLOCKS = ("flow", "current_state", "dashboard_flow", "cumulative")


def discover_snapshots(legacy_root: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    """(snapshot descriptors, unrecognised json file paths).  Each descriptor: path, abspath, family, account, pipeline, owner, file_day."""
    found = []  # type: List[Dict[str, Any]]
    other = []  # type: List[str]
    seen_dirs = {}  # type: Dict[Tuple[str, str], List[str]]
    for repo, sub, _rx, _f, _a, _p, _o in SNAPSHOT_SOURCES:
        d = os.path.join(legacy_root, repo, sub)
        if (repo, sub) not in seen_dirs:
            seen_dirs[(repo, sub)] = sorted(fn for fn in os.listdir(d) if fn.endswith(".json")) if os.path.isdir(d) else []
    claimed = set()  # type: Set[Tuple[str, str, str]]
    for repo, sub, rx, family, account, pipeline, has_owner in SNAPSHOT_SOURCES:
        for fn in seen_dirs[(repo, sub)]:
            if (repo, sub, fn) in claimed:
                continue
            m = rx.match(fn)
            if not m:
                continue
            claimed.add((repo, sub, fn))
            ap = os.path.join(legacy_root, repo, sub, fn)
            found.append({"path": _rel(ap, legacy_root), "abspath": ap, "repo": repo, "family": family, "account": account, "pipeline": pipeline,
                          "owner": m.group(1) if has_owner else None, "file_day": m.group(2) if has_owner else m.group(1)})
    for (repo, sub), fns in seen_dirs.items():
        for fn in fns:
            if (repo, sub, fn) not in claimed:
                other.append(_rel(os.path.join(legacy_root, repo, sub, fn), legacy_root))
    found.sort(key=lambda d: (d["family"], d["account"], d["pipeline"] or "", d["owner"] or "", d["file_day"]))
    return found, sorted(other)


def _numeric_problems(block: Any) -> int:
    n = 0
    if isinstance(block, dict):
        for v in block.values():
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                n += 1
    return n


def parse_snapshot(desc: Dict[str, Any]) -> Dict[str, Any]:
    """Read one snapshot file -> column values (no DB access)."""
    with io.open(desc["abspath"], "rb") as fh:
        raw = fh.read()
    sha = hashlib.sha256(raw).hexdigest()
    payload = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("snapshot is not a JSON object")
    day = payload.get("date") or payload.get("generated") or desc["file_day"]
    day = str(day)[:10]
    issues = []  # type: List[str]
    if day != desc["file_day"]:
        issues.append("date_mismatch")
    owner = desc["owner"]
    if desc["family"] == "main_owner":
        pid = payload.get("owner_id")
        if pid is not None and str(pid) != str(owner):
            issues.append("owner_mismatch")
    bad = sum(_numeric_problems(payload.get(b)) for b in _METRIC_BLOCKS)
    if bad:
        issues.append("non_numeric_metric")
    eng = payload.get("engaged_deal_ids")
    if eng is not None and not isinstance(eng, list):
        issues.append("engaged_not_list")
        eng = None
    out = {"sha": sha, "payload": payload, "day": day, "issues": issues, "bad_values": bad,
           "is_bootstrap": 1 if payload.get("bootstrap") else 0, "is_seeded": 1 if payload.get("seeded") else 0}
    for b in _METRIC_BLOCKS:
        v = payload.get(b)
        out[b + "_json"] = _dumps(v) if isinstance(v, dict) else None
    out["engaged_json"] = _dumps(eng) if eng is not None else None
    return out


def import_snapshots(db_path_arg: Optional[str] = None, dry_run: bool = False, legacy_root: Optional[str] = None,
                     con: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    legacy_root = legacy_root or os.path.join(project_root(), "legacy")
    own = con is None
    if own:
        con = _open_db(db_path_arg, dry_run)
    descs, other = discover_snapshots(legacy_root)
    summary = {"pass": "snapshots", "dry_run": dry_run, "files": len(descs), "inserted": 0, "updated": 0, "unchanged": 0, "failed": 0,
               "by_family": {}, "unrecognised_json": other, "dq": {}}  # type: Dict[str, Any]
    try:
        run_id = None
        if not dry_run:
            _require(con, "rpt_legacy_snapshot", "ops_import_run")
            fp = hashlib.sha256("|".join(d["path"] for d in descs).encode()).hexdigest()
            run_id = _start_run(con, "snapshot_import", "legacy_funnel_snapshots", fp, {"files": len(descs)})
        dq = {}  # type: Dict[str, List[str]]
        written = skipped = rejected = 0
        try:
            for d in descs:
                key = "%s/%s" % (d["family"], d["pipeline"]) if d["family"] == "companyops_cluster" else d["family"]
                try:
                    p = parse_snapshot(d)
                except Exception as exc:
                    summary["failed"] += 1
                    rejected += 1
                    dq.setdefault("unreadable", []).append(d["path"])
                    continue
                summary["by_family"][key] = summary["by_family"].get(key, 0) + 1
                for iss in p["issues"]:
                    dq.setdefault(iss, []).append(d["path"])
                where = (d["family"], d["account"], d["pipeline"] or "", d["owner"] or "", p["day"])
                existing = None
                if _has_table(con, "rpt_legacy_snapshot"):
                    existing = con.execute("SELECT snapshot_id, source_sha256 FROM rpt_legacy_snapshot WHERE source_family=? AND account_id=? "
                                           "AND COALESCE(pipeline_id,'')=? AND COALESCE(owner_hs_id,'')=? AND snapshot_day=?", where).fetchone()
                if existing is not None and existing["source_sha256"] == p["sha"]:
                    summary["unchanged"] += 1
                    skipped += 1
                    continue
                if existing is None:
                    summary["inserted"] += 1
                else:
                    summary["updated"] += 1
                written += 1
                if dry_run:
                    continue
                with ldb.transaction(con):
                    con.execute(
                        "INSERT INTO rpt_legacy_snapshot (source_family, account_id, pipeline_id, owner_hs_id, snapshot_day, source_path, source_sha256, "
                        "is_bootstrap, is_seeded, flow_json, current_state_json, dashboard_flow_json, cumulative_json, engaged_deal_ids_json, payload_json, import_run_id) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                        "ON CONFLICT (source_family, account_id, COALESCE(pipeline_id, ''), COALESCE(owner_hs_id, ''), snapshot_day) DO UPDATE SET "
                        "source_path=excluded.source_path, source_sha256=excluded.source_sha256, is_bootstrap=excluded.is_bootstrap, is_seeded=excluded.is_seeded, "
                        "flow_json=excluded.flow_json, current_state_json=excluded.current_state_json, dashboard_flow_json=excluded.dashboard_flow_json, "
                        "cumulative_json=excluded.cumulative_json, engaged_deal_ids_json=excluded.engaged_deal_ids_json, payload_json=excluded.payload_json, "
                        "import_run_id=excluded.import_run_id",
                        (d["family"], d["account"], d["pipeline"], d["owner"], p["day"], d["path"], p["sha"], p["is_bootstrap"], p["is_seeded"],
                         p["flow_json"], p["current_state_json"], p["dashboard_flow_json"], p["cumulative_json"], p["engaged_json"], _dumps(p["payload"]), run_id))
            summary["dq"] = {k: len(v) for k, v in sorted(dq.items())}
            if not dry_run:
                with ldb.transaction(con):
                    for rule, paths in dq.items():
                        dq_issue(con, run_id, "legacy_snapshot_" + rule, "error" if rule == "unreadable" else "warn", "legacy_snapshot_%s|flatfiles" % rule,
                                 "%d legacy snapshot file(s): %s" % (len(paths), rule), {"count": len(paths), "paths": paths[:50]}, "rpt_legacy_snapshot", None)
                _finish_run(con, run_id, "succeeded" if not summary["failed"] else "partial", len(descs), written, skipped, rejected)
        except BaseException as exc:
            if run_id is not None:
                _finish_run(con, run_id, "failed", len(descs), written, skipped, rejected, type(exc).__name__)
            raise
    finally:
        if own and con is not None:
            con.close()
    return summary


# ======================================================================================== pass 2: whale list
WHALE_GLOBS = [
    "hubspot/reserve/whale_list_27_NEVER_PUSH_TO_HUBSPOT.csv",
    "hubspot/temporary/Whale_List_Untouched_FINAL.csv",
    "hubspot/temporary/Whale_List_30.csv",
    "hubspot/godown/prequal/prequal_out/whales.csv",
]
WHALE_REASON = "Whale list: standing rule, NEVER push to HubSpot or any outbound system (reserve/README.md)"
_SUPP_COLS = {"domain": ("domain", norm.norm_domain), "name": ("company_name", norm.norm_company_name)}


def find_whale_files(legacy_root: str) -> List[str]:
    """The known whale files plus any other file under legacy/ whose name says whale (csv)."""
    found = {}  # type: Dict[str, str]
    for rel in WHALE_GLOBS:
        ap = os.path.join(legacy_root, rel)
        if os.path.isfile(ap):
            found[os.path.abspath(ap)] = rel
    for repo in LEGACY_REPOS:
        for dirpath, dirnames, filenames in os.walk(os.path.join(legacy_root, repo)):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for fn in filenames:
                if fn.lower().endswith(".csv") and re.match(r"^whales?[_.]", fn, re.I):
                    found.setdefault(os.path.abspath(os.path.join(dirpath, fn)), "")
    return sorted(found)


def _whale_values(row: Dict[str, Any]) -> List[Tuple[str, str]]:
    out = []  # type: List[Tuple[str, str]]
    lk = {_hdr_key(k): v for k, v in row.items()}
    d = norm.norm_domain(_first_token(lk.get("domain")))
    if d:
        out.append(("domain", d))
    n = norm.norm_company_name(lk.get("name") or lk.get("company") or lk.get("company_name"))
    if n:
        out.append(("company_name", n))
    li = lk.get("contact_linkedin")
    if li:
        p = norm.norm_linkedin_person(str(li))
        c = norm.norm_linkedin_company(str(li))
        if p:
            out.append(("linkedin_person", p))
        elif c:
            out.append(("linkedin_company", c))
    e = norm.norm_email(lk.get("contact_email")) if lk.get("contact_email") else None
    if e:
        out.append(("email", e))
    ph = norm.norm_phone_e164(lk.get("contact_phone")) if lk.get("contact_phone") else None
    if ph:
        out.append(("phone", ph))
    return out


def read_whale_file(abspath: str, legacy_root: Optional[str] = None) -> Tuple[List[Tuple[int, List[Tuple[str, str]]]], int]:
    """([(row_no, [(match_type, value_norm)])], rows read).  Values are normalised; no cell is returned raw."""
    plan = Plan(_rel(abspath, legacy_root), abspath, "hubspot", "csv", "pool", os.path.getsize(abspath))
    _sha, enc = sha256_and_encoding(abspath, True)
    op = _Opened(plan, enc)
    out = []  # type: List[Tuple[int, List[Tuple[str, str]]]]
    n = 0
    for ss in op.sheets():
        for row_no, row in ss.rows():
            n += 1
            out.append((row_no, _whale_values(row)))
    return out, n


def import_whales(db_path_arg: Optional[str] = None, dry_run: bool = False, legacy_root: Optional[str] = None,
                  con: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    legacy_root = legacy_root or os.path.join(project_root(), "legacy")
    own = con is None
    if own:
        con = _open_db(db_path_arg, dry_run)
    files = find_whale_files(legacy_root)
    summary = {"pass": "whale", "dry_run": dry_run, "files": [_rel(f, legacy_root) for f in files], "rows_read": 0, "values_total": 0, "distinct_values": 0,
               "inserted": 0, "already_present": 0, "reactivated": 0, "rejected": 0, "rows_without_any_value": 0, "by_type": {}}  # type: Dict[str, Any]
    try:
        # value -> (list names, [(file, row_no, type)])
        agg = {}  # type: Dict[Tuple[str, str], Dict[str, Any]]
        for f in files:
            rows, n = read_whale_file(f, legacy_root)
            summary["rows_read"] += n
            base = os.path.basename(f)
            for row_no, vals in rows:
                if not any(t in ("domain", "company_name") for t, _ in vals):
                    summary["rows_without_any_value"] += 1
                for t, v in vals:
                    summary["values_total"] += 1
                    e = agg.setdefault((t, v), {"lists": set(), "origins": []})
                    e["lists"].add(base)
                    e["origins"].append((base, row_no))
        summary["distinct_values"] = len(agg)
        for (t, _v) in agg:
            summary["by_type"][t] = summary["by_type"].get(t, 0) + 1
        if dry_run:
            if con is not None and _has_table(con, "suppression"):
                for (t, v) in agg:
                    r = con.execute("SELECT is_active FROM suppression WHERE kind='never_push' AND match_type=? AND match_value_norm=? AND account_id IS NULL", (t, v)).fetchone()
                    if r is None:
                        summary["inserted"] += 1
                    elif r[0]:
                        summary["already_present"] += 1
                    else:
                        summary["reactivated"] += 1
            else:
                summary["inserted"] = len(agg)
            return summary
        _require(con, "suppression", "origin_ref", "ops_import_run")
        fp = hashlib.sha256("|".join(sorted(f for f in files)).encode()).hexdigest()
        run_id = _start_run(con, "legacy_import", "whale_lists", fp, {"files": [_rel(f, legacy_root) for f in files]})
        rejected = 0
        try:
            with ldb.transaction(con):
                for (t, v), e in sorted(agg.items()):
                    list_name = ";".join(sorted(e["lists"]))
                    try:
                        con.execute("SAVEPOINT w")
                        row = con.execute("SELECT suppression_id, is_active, list_name FROM suppression WHERE kind='never_push' AND match_type=? AND match_value_norm=? "
                                          "AND account_id IS NULL", (t, v)).fetchone()
                        if row is None:
                            cur = con.execute("INSERT INTO suppression (kind, match_type, match_value_norm, reason, list_name, is_active) VALUES ('never_push',?,?,?,?,1)",
                                              (t, v, WHALE_REASON, list_name))
                            sid = int(cur.lastrowid)
                            summary["inserted"] += 1
                        else:
                            sid = int(row["suppression_id"])
                            if not row["is_active"]:
                                con.execute("UPDATE suppression SET is_active = 1, expires_at = NULL WHERE suppression_id = ?", (sid,))
                                summary["reactivated"] += 1
                                dq_issue(con, run_id, "whale_reactivated", "warn", "whale_reactivated|%d" % sid,
                                         "a deactivated never_push row was re-activated by the whale import (hard rule)", {"suppression_id": sid, "match_type": t},
                                         "suppression", str(sid))
                            else:
                                summary["already_present"] += 1
                            if row["list_name"] != list_name:
                                merged = ";".join(sorted(set((row["list_name"] or "").split(";")) | e["lists"] - {""}))
                                con.execute("UPDATE suppression SET list_name = ? WHERE suppression_id = ?", (merged, sid))
                        for base, row_no in e["origins"]:
                            con.execute("INSERT INTO origin_ref (entity_type, entity_id, source_system, source_table, source_pk, match_method, confidence, import_run_id) "
                                        "VALUES ('suppression',?,?,?,?,?,1.0,?) ON CONFLICT (entity_type, entity_id, source_system, source_table, source_pk) DO NOTHING",
                                        (sid, "csv:" + base, "csv", "%d:%s" % (row_no, t), t, run_id))
                        con.execute("RELEASE w")
                    except sqlite3.IntegrityError as exc:
                        con.execute("ROLLBACK TO w")
                        con.execute("RELEASE w")
                        rejected += 1
                        dq_issue(con, run_id, "whale_value_rejected", "error", "whale_value_rejected|%s|%s" % (t, hashlib.sha256(v.encode()).hexdigest()[:16]),
                                 "a whale value violates the suppression CHECKs (%s); the company stays blocked only by its other keys" % type(exc).__name__,
                                 {"match_type": t, "lists": sorted(e["lists"])}, "suppression", None)
                if summary["inserted"] or summary["reactivated"]:
                    con.execute("INSERT INTO ops_audit_log (actor, action, entity_type, entity_ref, after_json, note, import_run_id) VALUES (?,?,?,?,?,?,?)",
                                ("cli:flatfiles", "suppression.add", "suppression", "never_push:whale_lists",
                                 _dumps({"inserted": summary["inserted"], "reactivated": summary["reactivated"], "by_type": summary["by_type"]}),
                                 "whale lists imported as never_push (counts only)", run_id))
            summary["rejected"] = rejected
            _finish_run(con, run_id, "succeeded" if not rejected else "partial", summary["rows_read"], summary["inserted"] + summary["reactivated"],
                        summary["already_present"], rejected)
        except BaseException as exc:
            _finish_run(con, run_id, "failed", summary["rows_read"], 0, 0, rejected, type(exc).__name__)
            raise
    finally:
        if own and con is not None:
            con.close()
    return summary


# ======================================================================================== pass 3: files
def _probe_header(p: Plan, enc: Optional[str]) -> List[str]:
    """Column names of the first sheet (for the >50 MB 'is it a list of companies/contacts' decision)."""
    try:
        op = _Opened(p, enc)
        for ss in op.sheets():
            return list(ss.columns)
    except Exception:
        return []
    return []


def looks_like_entity_list(columns: Sequence[str]) -> bool:
    c = Columns(columns)
    return bool(c.domain or c.linkedin or c.email or c.name or c.phone)


def _phone_country(path: str) -> str:
    return "XX" if _FOREIGN_PHONE_PATH.search(path) else "IN"


def _catalog_row(con: sqlite3.Connection, path: str) -> Optional[sqlite3.Row]:
    if not _has_table(con, "ext_file_catalog"):
        return None
    return con.execute("SELECT * FROM ext_file_catalog WHERE path = ?", (path,)).fetchone()


def import_files(db_path_arg: Optional[str] = None, dry_run: bool = False, legacy_root: Optional[str] = None, include: Optional[str] = None,
                 force: bool = False, keep_duplicates: bool = False, con: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    legacy_root = legacy_root or os.path.join(project_root(), "legacy")
    own = con is None
    if own:
        con = _open_db(db_path_arg, dry_run)
    plans = discover(legacy_root, include)
    t0 = time.time()
    summary = {"pass": "files", "dry_run": dry_run, "files_seen": len(plans), "loaded": 0, "unchanged": 0, "duplicates": 0, "skipped": 0, "failed": 0,
               "rows_loaded": 0, "rows_parsed": 0, "by_role": {}, "by_repo": {}, "skipped_files": [], "failed_files": [], "duplicate_files": [],
               "files": []}  # type: Dict[str, Any]
    run_id = None
    try:
        if not dry_run:
            _require(con, "ext_file_catalog", "ext_file_row", "ops_import_run")
            fp = hashlib.sha256("|".join(p.path for p in plans).encode()).hexdigest()
            run_id = _start_run(con, "legacy_import", "flatfiles", fp, {"include": include, "force": force, "files": len(plans)})
        infos = {}  # type: Dict[str, Tuple[str, Optional[str]]]
        for p in plans:
            if p.role != "skipped":
                infos[p.path] = sha256_and_encoding(p.abspath, p.kind in ("csv", "tsv"))
        canonical = {}  # type: Dict[str, str]        # sha -> path whose rows are stored (best role, no "(1)" copy marker, then path)
        if not keep_duplicates:
            best = {}  # type: Dict[str, Plan]
            for p in plans:
                if p.path not in infos:
                    continue
                sha = infos[p.path][0]
                key = (_ROLE_PRIORITY.get(p.role, 9), bool(re.search(r" \(\d+\)", os.path.basename(p.path))), p.path)
                if sha not in best or key < (_ROLE_PRIORITY.get(best[sha].role, 9), bool(re.search(r" \(\d+\)", os.path.basename(best[sha].path))), best[sha].path):
                    best[sha] = p
            canonical = {sha: pl.path for sha, pl in best.items()}
        # canonical copies first so that a duplicate can point at the catalog row that holds the rows
        ordered = sorted(plans, key=lambda x: (x.path in infos and canonical.get(infos[x.path][0]) != x.path, x.path))
        for p in ordered:
            try:
                _import_one(con, p, run_id, dry_run, force, keep_duplicates, infos.get(p.path), canonical, summary)
            except Exception as exc:
                summary["failed"] += 1
                summary["failed_files"].append({"path": p.path, "error": type(exc).__name__})
                if not dry_run:
                    # the file's own transaction was rolled back; record why it is not loaded
                    try:
                        _catalog_skipped(con, p, run_id, "unreadable: %s" % type(exc).__name__)
                    except Exception:
                        pass
        if not dry_run:
            with ldb.transaction(con):
                guessed = [p.path for p in plans if p.no_domain and p.path in infos]
                if guessed:
                    dq_issue(con, run_id, "flatfile_guessed_domain", "info", "flatfile_guessed_domain|flatfiles",
                             "%d file(s) carry a name-derived domain guess; the domain stays in row_json but is not extracted as a lookup key" % len(guessed),
                             {"count": len(guessed), "paths": guessed[:30]}, "ext_file_catalog", None)
                if summary["duplicate_files"]:
                    dq_issue(con, run_id, "flatfile_duplicate_copies", "info", "flatfile_duplicate_copies|flatfiles",
                             "%d byte-identical copies are catalogued (dup_of_file_id) without rows" % len(summary["duplicate_files"]),
                             {"count": len(summary["duplicate_files"]), "examples": summary["duplicate_files"][:20]}, "ext_file_catalog", None)
                if summary["failed_files"]:
                    dq_issue(con, run_id, "flatfile_unreadable", "error", "flatfile_unreadable|flatfiles",
                             "%d file(s) could not be parsed and are catalogued as skipped" % len(summary["failed_files"]),
                             {"files": summary["failed_files"][:50]}, "ext_file_catalog", None)
            if summary["rows_loaded"]:
                con.execute("ANALYZE ext_file_row")           # fresh planner statistics after a bulk load
            _finish_run(con, run_id, "succeeded" if not summary["failed"] else "partial", summary["rows_parsed"], summary["rows_loaded"],
                        summary["unchanged"] + summary["duplicates"], summary["failed"])
    except BaseException as exc:
        if run_id is not None:
            _finish_run(con, run_id, "failed", summary["rows_parsed"], summary["rows_loaded"], 0, summary["failed"], type(exc).__name__)
        raise
    finally:
        if own and con is not None:
            con.close()
    summary["seconds"] = round(time.time() - t0, 1)
    return summary


def _tally(summary: Dict[str, Any], p: Plan, role: str, rows: Optional[int], status: str, loaded: int, extra: Optional[Dict[str, Any]] = None) -> None:
    summary["by_role"][role] = summary["by_role"].get(role, 0) + 1
    summary["by_repo"][p.repo] = summary["by_repo"].get(p.repo, 0) + 1
    e = {"path": p.path, "role": role, "kind": p.kind, "status": status, "rows": rows, "rows_loaded": loaded}
    if extra:
        e.update(extra)
    summary["files"].append(e)


def _catalog_skipped(con: sqlite3.Connection, p: Plan, run_id: Optional[int], reason: str) -> None:
    sha = sha256_file(p.abspath)
    with ldb.transaction(con):
        con.execute("INSERT INTO ext_file_catalog (path, source_repo, sha256, size_bytes, mtime_at, kind, role, skip_reason, rows, rows_loaded, import_run_id, "
                    "is_never_push, is_stale_mirror, imported_at) VALUES (?,?,?,?,?,?, 'skipped', ?, NULL, 0, ?, ?, ?, ?) "
                    "ON CONFLICT(path) DO UPDATE SET sha256=excluded.sha256, size_bytes=excluded.size_bytes, mtime_at=excluded.mtime_at, kind=excluded.kind, "
                    "role='skipped', skip_reason=excluded.skip_reason, rows=NULL, rows_loaded=0, dup_of_file_id=NULL, import_run_id=excluded.import_run_id, "
                    "is_never_push=excluded.is_never_push, is_stale_mirror=excluded.is_stale_mirror, imported_at=excluded.imported_at",
                    (p.path, p.repo, sha, p.size, _mtime_iso(p.abspath), p.kind, reason, run_id, int(p.never_push), int(p.stale_mirror), _now()))
        # a skipped file keeps no rows
        fid = con.execute("SELECT file_id FROM ext_file_catalog WHERE path = ?", (p.path,)).fetchone()[0]
        con.execute("DELETE FROM ext_file_row WHERE file_id = ?", (fid,))


def _import_one(con: Optional[sqlite3.Connection], p: Plan, run_id: Optional[int], dry_run: bool, force: bool, keep_dups: bool,
                info: Optional[Tuple[str, Optional[str]]], canonical: Dict[str, str], summary: Dict[str, Any]) -> None:
    if p.role == "skipped" or info is None:
        summary["skipped"] += 1
        summary["skipped_files"].append({"path": p.path, "reason": p.skip_reason})
        _tally(summary, p, "skipped", None, "skipped", 0, {"reason": p.skip_reason})
        if not dry_run:
            _catalog_skipped(con, p, run_id, p.skip_reason or "skipped")
        return
    sha, enc = info
    # giant files must look like a list of companies / contacts
    if p.size > BIG_FILE_BYTES:
        cols = _probe_header(p, enc)
        if not looks_like_entity_list(cols):
            p.role, p.skip_reason = "skipped", "larger than 50 MB and not a list of companies / contacts (regenerable scrape intermediate)"
            summary["skipped"] += 1
            summary["skipped_files"].append({"path": p.path, "reason": p.skip_reason})
            _tally(summary, p, "skipped", None, "skipped", 0, {"reason": p.skip_reason})
            if not dry_run:
                _catalog_skipped(con, p, run_id, p.skip_reason)
            return
    first_path = canonical.get(sha)
    is_dup = first_path is not None and first_path != p.path and not keep_dups
    existing = _catalog_row(con, p.path) if con is not None else None
    dup_of_id = None
    if is_dup:
        r = _catalog_row(con, first_path) if con is not None else None
        dup_of_id = int(r["file_id"]) if r is not None else None
        if dup_of_id is None and not dry_run:
            is_dup = False                      # the file whose rows should be stored failed: store this copy instead
    # unchanged: same bytes, same duplicate state, previously loaded
    if existing is not None and not force and existing["sha256"] == sha and existing["role"] != "skipped":
        state_ok = (existing["dup_of_file_id"] is not None) == is_dup and (is_dup is False or existing["dup_of_file_id"] == dup_of_id)
        if state_ok:
            summary["unchanged"] += 1
            _tally(summary, p, p.role, existing["rows"], "unchanged", existing["rows_loaded"])
            if not dry_run:
                with ldb.transaction(con):
                    con.execute("UPDATE ext_file_catalog SET source_repo=?, role=?, is_never_push=?, is_stale_mirror=?, kind=? WHERE file_id=? AND "
                                "(source_repo IS NOT ? OR role IS NOT ? OR is_never_push IS NOT ? OR is_stale_mirror IS NOT ? OR kind IS NOT ?)",
                                (p.repo, p.role, int(p.never_push), int(p.stale_mirror), p.kind, existing["file_id"],
                                 p.repo, p.role, int(p.never_push), int(p.stale_mirror), p.kind))
            return
    op = _Opened(p, enc)
    columns = {}  # type: Dict[str, List[str]]
    notes = {}  # type: Dict[str, Any]
    total = 0
    loaded = 0
    pcountry = _phone_country(p.path)
    if dry_run:
        for ss in op.sheets():
            n = 0
            columns[ss.name] = ss.columns
            for _ in ss.rows():
                n += 1
            total += n
        summary["rows_parsed"] += total
        if is_dup:
            summary["duplicates"] += 1
            summary["duplicate_files"].append({"path": p.path, "dup_of": first_path})
            _tally(summary, p, p.role, total, "duplicate", 0, {"dup_of": first_path})
        else:
            summary["loaded"] += 1
            summary["rows_loaded"] += total
            _tally(summary, p, p.role, total, "would_load" if existing is None or existing["sha256"] != sha else "would_reload", total)
        return
    with ldb.transaction(con):
        cur = con.execute("SELECT file_id FROM ext_file_catalog WHERE path = ?", (p.path,)).fetchone()
        if cur is not None:
            con.execute("DELETE FROM ext_file_row WHERE file_id = ?", (cur[0],))
        rows_buf = []  # type: List[Tuple[Any, ...]]
        file_id = None  # type: Optional[int]
        # catalogue first (rows need file_id); counts are corrected at the end of the same transaction
        con.execute(
            "INSERT INTO ext_file_catalog (path, source_repo, sha256, size_bytes, mtime_at, kind, role, skip_reason, rows, rows_loaded, columns_json, encoding, "
            "delimiter, dup_of_file_id, is_never_push, is_stale_mirror, notes, import_run_id, imported_at) VALUES (?,?,?,?,?,?,?,NULL,0,0,'{}',?,?,?,?,?,?,?,?) "
            "ON CONFLICT(path) DO UPDATE SET source_repo=excluded.source_repo, sha256=excluded.sha256, size_bytes=excluded.size_bytes, mtime_at=excluded.mtime_at, "
            "kind=excluded.kind, role=excluded.role, skip_reason=NULL, rows=0, rows_loaded=0, columns_json='{}', encoding=excluded.encoding, "
            "delimiter=excluded.delimiter, dup_of_file_id=excluded.dup_of_file_id, is_never_push=excluded.is_never_push, is_stale_mirror=excluded.is_stale_mirror, "
            "notes=excluded.notes, import_run_id=excluded.import_run_id, imported_at=excluded.imported_at",
            (p.path, p.repo, sha, p.size, _mtime_iso(p.abspath), p.kind, p.role, enc, None, dup_of_id, int(p.never_push), int(p.stale_mirror), p.note, run_id, _now()))
        file_id = int(con.execute("SELECT file_id FROM ext_file_catalog WHERE path = ?", (p.path,)).fetchone()[0])
        for ss in op.sheets():
            columns[ss.name] = ss.columns
            cols = Columns(ss.columns, p.hints)
            if p.no_domain:
                cols.domain = []
            n = 0
            for row_no, row in ss.rows():
                n += 1
                if is_dup:
                    continue
                dom, lic, lip, em, ph, nm = extract_keys(cols, row, pcountry)
                rows_buf.append((file_id, ss.name, row_no, _dumps(row), dom, lic, lip, em, ph, nm))
                if len(rows_buf) >= BATCH:
                    _flush(con, rows_buf)
                    loaded += len(rows_buf)
                    rows_buf = []
            if rows_buf:
                _flush(con, rows_buf)
                loaded += len(rows_buf)
                rows_buf = []
            total += n
            for k, v in (("blank_rows", ss.blank_rows), ("ragged_rows", ss.ragged_rows), ("title_rows_skipped", ss.title_rows_skipped)):
                if v:
                    notes.setdefault(ss.name, {})[k] = v
        note_txt = p.note
        if notes:
            note_txt = ((note_txt + "; ") if note_txt else "") + "sheet notes: " + _dumps(notes)
        con.execute("UPDATE ext_file_catalog SET rows=?, rows_loaded=?, columns_json=?, delimiter=?, notes=? WHERE file_id=?",
                    (total, loaded, _dumps(columns), op.delimiter, note_txt, file_id))
    summary["rows_parsed"] += total
    if is_dup:
        summary["duplicates"] += 1
        summary["duplicate_files"].append({"path": p.path, "dup_of": first_path})
        _tally(summary, p, p.role, total, "duplicate", 0, {"dup_of": first_path})
    else:
        summary["loaded"] += 1
        summary["rows_loaded"] += loaded
        _tally(summary, p, p.role, total, "loaded", loaded)


def _flush(con: sqlite3.Connection, rows: List[Tuple[Any, ...]]) -> None:
    con.executemany("INSERT INTO ext_file_row (file_id, sheet_name, row_no, row_json, company_domain_norm, linkedin_norm, person_linkedin_norm, email_norm, "
                    "phone_e164, company_name_norm) VALUES (?,?,?,?,?,?,?,?,?,?)", rows)


# ======================================================================================== pass 4: link
def link_files(db_path_arg: Optional[str] = None, dry_run: bool = False, con: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    """Attach staged rows to golden companies / contacts through strong keys.  A full recompute (safe to re-run after any importer):

    * company: ``company_identifier`` (is_strong = 1) ``root_domain`` <- ``company_domain_norm`` and ``linkedin_company`` <- ``linkedin_norm``,
      followed through merge chains (``v_identifier_survivor``).  When both keys resolve to DIFFERENT companies the row stays unlinked
      (ambiguous) and is counted; one key alone is enough.
    * contact: ``contact.linkedin_person_norm`` <- ``person_linkedin_norm`` and ``contact.email_norm`` <- ``email_norm``, only when exactly
      one contact owns the value; conflicting keys leave the row unlinked.
    * whale rows: ``suppression.company_id`` is filled for never_push domain rows whose domain is a strong company key.
    """
    own = con is None
    if own:
        con = _open_db(db_path_arg, dry_run)
    out = {"pass": "link", "dry_run": dry_run, "rows_with_keys": 0, "company_linked": 0, "company_ambiguous": 0, "company_unlinked_with_keys": 0,
           "company_by_method": {}, "contact_linked": 0, "contact_ambiguous": 0, "contact_by_method": {}, "changed": 0, "suppression_company_filled": 0}  # type: Dict[str, Any]
    if con is None or not _has_table(con, "ext_file_row"):
        out["note"] = "no database / migration 0015 not applied"
        return out
    try:
        out["rows_with_keys"] = con.execute("SELECT COUNT(*) FROM ext_file_row WHERE company_domain_norm IS NOT NULL OR linkedin_norm IS NOT NULL "
                                            "OR person_linkedin_norm IS NOT NULL OR email_norm IS NOT NULL").fetchone()[0]
        con.execute("DROP TABLE IF EXISTS temp._lk_ident")
        con.execute("DROP TABLE IF EXISTS temp._lk_cand")
        con.execute("DROP TABLE IF EXISTS temp._lk_contact")
        con.execute("DROP TABLE IF EXISTS temp._lk_ccand")
        con.execute("CREATE TEMP TABLE _lk_ident AS SELECT identifier_type AS t, value_norm AS v, survivor_company_id AS c FROM v_identifier_survivor "
                    "WHERE is_strong = 1 AND identifier_type IN ('root_domain', 'linkedin_company') AND survivor_company_id IS NOT NULL")
        con.execute("CREATE INDEX temp._lk_ident_ix ON _lk_ident(t, v)")
        con.execute("CREATE TEMP TABLE _lk_cand AS SELECT r.row_id AS row_id, d.c AS dc, l.c AS lc FROM ext_file_row r "
                    "LEFT JOIN _lk_ident d ON d.t = 'root_domain' AND d.v = r.company_domain_norm "
                    "LEFT JOIN _lk_ident l ON l.t = 'linkedin_company' AND l.v = r.linkedin_norm "
                    "WHERE d.c IS NOT NULL OR l.c IS NOT NULL")
        con.execute("CREATE INDEX temp._lk_cand_ix ON _lk_cand(row_id)")
        out["company_ambiguous"] = con.execute("SELECT COUNT(*) FROM _lk_cand WHERE dc IS NOT NULL AND lc IS NOT NULL AND dc <> lc").fetchone()[0]
        for method, cond in (("root_domain", "dc IS NOT NULL AND lc IS NULL"), ("linkedin_company", "dc IS NULL AND lc IS NOT NULL"),
                             ("root_domain+linkedin_company", "dc IS NOT NULL AND lc = dc")):
            out["company_by_method"][method] = con.execute("SELECT COUNT(*) FROM _lk_cand WHERE " + cond).fetchone()[0]
        out["company_linked"] = sum(out["company_by_method"].values())
        out["company_unlinked_with_keys"] = con.execute(
            "SELECT COUNT(*) FROM ext_file_row r WHERE (r.company_domain_norm IS NOT NULL OR r.linkedin_norm IS NOT NULL) "
            "AND r.row_id NOT IN (SELECT row_id FROM _lk_cand WHERE NOT (dc IS NOT NULL AND lc IS NOT NULL AND dc <> lc))").fetchone()[0]
        # contacts: a value owned by exactly one contact
        con.execute("CREATE TEMP TABLE _lk_contact AS SELECT 'linkedin_person' AS t, linkedin_person_norm AS v, MIN(contact_id) AS c FROM contact "
                    "WHERE linkedin_person_norm IS NOT NULL GROUP BY linkedin_person_norm HAVING COUNT(*) = 1 "
                    "UNION ALL SELECT 'email', email_norm, MIN(contact_id) FROM contact WHERE email_norm IS NOT NULL GROUP BY email_norm HAVING COUNT(*) = 1")
        con.execute("CREATE INDEX temp._lk_contact_ix ON _lk_contact(t, v)")
        con.execute("CREATE TEMP TABLE _lk_ccand AS SELECT r.row_id AS row_id, p.c AS pc, e.c AS ec FROM ext_file_row r "
                    "LEFT JOIN _lk_contact p ON p.t = 'linkedin_person' AND p.v = r.person_linkedin_norm "
                    "LEFT JOIN _lk_contact e ON e.t = 'email' AND e.v = r.email_norm WHERE p.c IS NOT NULL OR e.c IS NOT NULL")
        con.execute("CREATE INDEX temp._lk_ccand_ix ON _lk_ccand(row_id)")
        out["contact_ambiguous"] = con.execute("SELECT COUNT(*) FROM _lk_ccand WHERE pc IS NOT NULL AND ec IS NOT NULL AND pc <> ec").fetchone()[0]
        for method, cond in (("linkedin_person", "pc IS NOT NULL AND ec IS NULL"), ("email", "pc IS NULL AND ec IS NOT NULL"),
                             ("linkedin_person+email", "pc IS NOT NULL AND ec = pc")):
            out["contact_by_method"][method] = con.execute("SELECT COUNT(*) FROM _lk_ccand WHERE " + cond).fetchone()[0]
        out["contact_linked"] = sum(out["contact_by_method"].values())
        if dry_run:
            return out
        now = _now()
        with ldb.transaction(con):
            before = con.total_changes
            # clear links that no longer resolve (unmapped, merged away, ambiguous), then set the resolvable ones
            con.execute("UPDATE ext_file_row SET company_id = NULL, company_link_method = NULL, linked_at = ? WHERE company_id IS NOT NULL AND row_id NOT IN "
                        "(SELECT row_id FROM _lk_cand WHERE NOT (dc IS NOT NULL AND lc IS NOT NULL AND dc <> lc))", (now,))
            con.execute("WITH c AS (SELECT row_id, COALESCE(dc, lc) AS cid, CASE WHEN dc IS NOT NULL AND lc IS NOT NULL THEN 'root_domain+linkedin_company' "
                        "WHEN dc IS NOT NULL THEN 'root_domain' ELSE 'linkedin_company' END AS m FROM _lk_cand WHERE NOT (dc IS NOT NULL AND lc IS NOT NULL AND dc <> lc)) "
                        "UPDATE ext_file_row SET company_id = (SELECT cid FROM c WHERE c.row_id = ext_file_row.row_id), "
                        "company_link_method = (SELECT m FROM c WHERE c.row_id = ext_file_row.row_id), linked_at = ? "
                        "WHERE row_id IN (SELECT row_id FROM c) AND (company_id IS NOT (SELECT cid FROM c WHERE c.row_id = ext_file_row.row_id) "
                        "OR company_link_method IS NOT (SELECT m FROM c WHERE c.row_id = ext_file_row.row_id))", (now,))
            con.execute("UPDATE ext_file_row SET contact_id = NULL, contact_link_method = NULL, linked_at = ? WHERE contact_id IS NOT NULL AND row_id NOT IN "
                        "(SELECT row_id FROM _lk_ccand WHERE NOT (pc IS NOT NULL AND ec IS NOT NULL AND pc <> ec))", (now,))
            con.execute("WITH c AS (SELECT row_id, COALESCE(pc, ec) AS cid, CASE WHEN pc IS NOT NULL AND ec IS NOT NULL THEN 'linkedin_person+email' "
                        "WHEN pc IS NOT NULL THEN 'linkedin_person' ELSE 'email' END AS m FROM _lk_ccand WHERE NOT (pc IS NOT NULL AND ec IS NOT NULL AND pc <> ec)) "
                        "UPDATE ext_file_row SET contact_id = (SELECT cid FROM c WHERE c.row_id = ext_file_row.row_id), "
                        "contact_link_method = (SELECT m FROM c WHERE c.row_id = ext_file_row.row_id), linked_at = ? "
                        "WHERE row_id IN (SELECT row_id FROM c) AND (contact_id IS NOT (SELECT cid FROM c WHERE c.row_id = ext_file_row.row_id) "
                        "OR contact_link_method IS NOT (SELECT m FROM c WHERE c.row_id = ext_file_row.row_id))", (now,))
            out["changed"] = con.total_changes - before
            # whale suppression rows: remember the golden company (never changes the block itself)
            if _has_table(con, "suppression"):
                cur = con.execute("UPDATE suppression SET company_id = (SELECT c FROM _lk_ident i WHERE i.t = 'root_domain' AND i.v = suppression.match_value_norm) "
                                  "WHERE kind = 'never_push' AND match_type = 'domain' AND company_id IS NULL AND EXISTS "
                                  "(SELECT 1 FROM _lk_ident i WHERE i.t = 'root_domain' AND i.v = suppression.match_value_norm)")
                out["suppression_company_filled"] = cur.rowcount
        return out
    finally:
        for t in ("_lk_ident", "_lk_cand", "_lk_contact", "_lk_ccand"):
            try:
                con.execute("DROP TABLE IF EXISTS temp.%s" % t)
            except sqlite3.Error:
                pass
        if own:
            con.close()


# ======================================================================================== CLI
def run(only: str = "all", db_path_arg: Optional[str] = None, dry_run: bool = False, legacy_root: Optional[str] = None, include: Optional[str] = None,
        force: bool = False, keep_duplicates: bool = False) -> Dict[str, Any]:
    out = {}  # type: Dict[str, Any]
    steps = ["snapshots", "whale", "files", "link"] if only == "all" else [only]
    for s in steps:
        if s == "snapshots":
            out[s] = import_snapshots(db_path_arg, dry_run, legacy_root)
        elif s == "whale":
            out[s] = import_whales(db_path_arg, dry_run, legacy_root)
        elif s == "files":
            out[s] = import_files(db_path_arg, dry_run, legacy_root, include, force, keep_duplicates)
        elif s == "link":
            out[s] = link_files(db_path_arg, dry_run)
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m leadgen.legacy_import.flatfiles",
                                 description="Import legacy flat files (funnel snapshots, whale list, CSV/XLSX/JSON data files) into LeadGenMonolith.")
    ap.add_argument("--db", help="database path (default: $LEADGEN_DB or db/leadgen.sqlite)")
    ap.add_argument("--only", choices=["snapshots", "whale", "files", "link", "all"], default="all")
    ap.add_argument("--dry-run", action="store_true", help="parse everything, report counts, write nothing")
    ap.add_argument("--legacy-root", help="directory holding companyOps/ RapidActionTeam/ hubspot/ (default: <repo>/legacy)")
    ap.add_argument("--include", help="files pass: only paths containing this substring")
    ap.add_argument("--force", action="store_true", help="files pass: reload even when the sha256 is unchanged")
    ap.add_argument("--keep-duplicates", action="store_true", help="files pass: store rows of byte-identical copies too")
    ap.add_argument("--full", action="store_true", help="print the per-file list as well")
    args = ap.parse_args(argv)
    try:
        res = run(args.only, args.db, args.dry_run, args.legacy_root, args.include, args.force, args.keep_duplicates)
    except (FileNotFoundError, ldb.MigrationError, sqlite3.Error) as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)[:300]}), file=sys.stderr)
        return 2
    if not args.full and "files" in res:
        res["files"] = {k: v for k, v in res["files"].items() if k != "files"}
    print(json.dumps(res, indent=2, default=str))
    return 0


if __name__ == "__main__":      # pragma: no cover
    sys.exit(main())
