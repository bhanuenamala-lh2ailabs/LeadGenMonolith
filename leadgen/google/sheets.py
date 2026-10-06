"""Google Sheets pull (READ-ONLY): catalog + cell data -> ext_gsheet_catalog / ext_gsheet_tab / ext_gsheet_row, plus CSV copies.

API surface used (and nothing else - see leadgen.google.auth.ALLOWED_CALLS, enforced by a guard):
    Drive files.list (metadata)  |  Sheets spreadsheets.get (tab list, fields= only)  |  Sheets values.batchGet (cell data)

Pipeline
    1. ``load_catalog``   data/gsheets_catalog.json  -> ext_gsheet_catalog + ext_gsheet_tab (metadata, no network).
    2. ``refresh_catalog_from_drive``  Drive files.list (1000 per page) -> fresh modifiedTime / title / new spreadsheets.
    3. ``build_plan``     decides which spreadsheets are pulled (policy below) and flips ext_gsheet_catalog.pull_enabled for them.
    4. ``pull``           one ``values.batchGet`` per spreadsheet (several for very large ones), UNFORMATTED_VALUE + FORMATTED_STRING dates,
                          empty rows dropped (the API already trims trailing empties, so a 1000x26 grid costs only its populated rows),
                          tabs of more than CHUNK_ROWS rows are read in row chunks.  Rows are diffed by sha256 and only changed rows are written
                          (short transactions, another process may be writing); a CSV per tab goes to data/gsheets/<spreadsheet_id>__<tab>.csv.
                          Incremental: ops_sync_state('gsheets', <drive account>, 'values', <spreadsheet_id>) holds the Drive modifiedTime that was
                          pulled; a spreadsheet whose modifiedTime has not moved is not read again.  Re-runs resume where the last one stopped.

Pull policy (docs/GOOGLE_PULLS.md has the reasoning and the numbers)
    * hard-coded : the spreadsheet ids that the legacy scripts used (HARD_CODED_SHEETS) - always pulled, except ...
    * excluded   : ext_gsheet_exclusion rows (applicant PII, credentials) - NEVER pulled, whatever else says so (the database refuses too).
    * flagged    : titles that look personal (``responses``, ``creds``, ``applicant`` ...) are marked pii_class='personal' and skipped.
    * human      : everything else not owned by the auto-generation account -> pulled.
    * bulk       : the sheets owned by BULK_OWNER (auto-generated, one tab, ~1000x24 each) are catalogued but pulled only with include_bulk.

Quota: Sheets allows 60 read requests / minute / user / project; ``--per-minute`` (default 50) spaces calls; 429 / 5xx back off exponentially.

Python 3.9 compatible.  This module never calls a mutating Google API method (tests/test_google.py scans the source).
"""
import argparse
import csv
import datetime
import hashlib
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
CATALOG_JSON = os.path.join(PROJECT_ROOT, "data", "gsheets_catalog.json")
DATA_DIR = os.path.join(PROJECT_ROOT, "data", "gsheets")
PULL_CONFIG = os.path.join(PROJECT_ROOT, "config", "gsheets_pull.yaml")

DRIVE_ACCOUNT = "bhanu.enamala@lh2.ai"      # ops_sync_state.scope for every gsheets cursor
BULK_OWNER = "purunjay.choudhary@lh2.ai"    # 1,145 auto-generated one-tab sheets
CHUNK_ROWS = 20000                          # tabs with more allocated rows than this are read in row chunks
REQUEST_CELL_BUDGET = 2500000               # allocated cells per batchGet request (keeps responses well under 100 MB)
REQUEST_MAX_RANGES = 20
DB_BATCH = 4000                             # rows per write transaction
DEFAULT_PER_MINUTE = 50                     # Sheets limit is 60 reads / min / user / project

#: the 11 spreadsheet ids hard-coded in the legacy scripts (docs/discovery/google-integrations.md section 1), pulled first
HARD_CODED_SHEETS = [
    ("1B9in9qK1V3IyjoyjYSqwn0gGRyoP9qVlMBGKKheoEhM", "Private Codebase Tracker (service-account-only; tab Pipeline Tracker + IT Services Firms)"),
    ("12BLV3nv1d9Is4UHN113YVhiTBNilHe-9phIMMCEKS4A", "Tracxn (feeds the CRM mirror)"),
    ("19PE9VroacFFaeATFgEqmj0jDuU2PRJSzxZyVypBPjLo", "ITservices_ScrapedLeads"),
    ("1bz5sZmG_7mfRefUZld2wErxRfV9AcPNBSS6eXmyxbLE", "master_companies"),
    ("1SI4GGdrAEvd-pfun2iULhz3yh46h8LYGkGTZCqy9VIE", "ceo leads"),
    ("17IkH_Ej1kAPk5q3EAZuiL5QTWg_hwDPfW-td4WKcMD0", "MidTier_Telehealth_India_BD_Nepal_SEA"),
    ("1xuoBloYzQOg3j27UQC0t6l9AXjFw9IxAGknlbYNsgOo", "Reachout list | LH2 AI"),
    ("1URrL2rrKo7ECS9UxMI9KRLIf3_1cpQiMahJ4RDoL8xE", "Antrhopic_PoC"),
    ("1CDgrOx72H9b9uw_A2OdzDWKAJMF-KwnFSOETbDoTtP0", "Outflo Reachout"),
    ("1aj-d_IlHFyHOUdnrgHYc5mb6gpAnIijORZt8Vyna5j4", "Supply Funnel SoP (a Google DOC, not a spreadsheet: not in the catalog)"),
    ("1IPA45kJ6yTsBo8d33DM0Ay_CIfBa6jrj_wrh3A4_uiQ", "Founder's Office automation-intern hiring sheet (HARD-EXCLUDED: applicant PII)"),
]

#: extra hard exclusions found by reviewing the catalog titles (inserted into ext_gsheet_exclusion; the intern sheet is seeded by 0005_ext.sql)
EXTRA_EXCLUSIONS = [
    ("1vqTi_obwF5hc5farSUK3DiZtCEk9XhZSsrTJoHjWaew", "'Automation Intern - LH2 AI Labs (Responses)' (owner hr@lh2holdings.com): form responses of intern applicants = personal data, same population as the excluded intern-hiring sheet"),
    ("__CREDS__", "Sheet titled 'Creds' (owner shobit.gupta): by its name it holds credentials; never copied into the database"),
]

PII_TITLE_RE = re.compile(r"\b(responses?|creds?|credentials?|passwords?|applicants?|resumes?)\b", re.I)

SHEETS_FIELDS_TABS = "spreadsheetId,properties(title,timeZone),sheets(properties(sheetId,title,index,hidden,gridProperties(rowCount,columnCount)))"
DRIVE_FIELDS = ("nextPageToken,files(id,name,owners(emailAddress),createdTime,modifiedTime,lastModifyingUser(emailAddress),"
                "capabilities(canEdit),driveId,webViewLink)")
SHEET_MIME = "application/vnd.google-apps.spreadsheet"


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# ------------------------------------------------------------------------------------------------- small helpers
def iso_ms(value: Optional[str]) -> Optional[str]:
    """Any RFC3339 / ISO instant -> the database format ``YYYY-MM-DDTHH:MM:SS.mmmZ`` (UTC)."""
    if not value:
        return None
    v = value.strip()
    m = re.match(r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$", v)
    if not m:
        return None
    frac = ((m.group(3) or ".0") + "000")[1:4]
    dt = datetime.datetime.strptime("%sT%s" % (m.group(1), m.group(2)), "%Y-%m-%dT%H:%M:%S")
    tz = m.group(4)
    if tz and tz != "Z":
        sign = 1 if tz[0] == "+" else -1
        digits = tz[1:].replace(":", "")
        dt -= sign * datetime.timedelta(hours=int(digits[:2]), minutes=int(digits[2:4]))
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + frac + "Z"


def col_letters(n: int) -> str:
    n = max(int(n), 1)
    out = ""
    while n:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


def quote_tab(title: str) -> str:
    """A1-notation reference to a whole tab."""
    return "'" + title.replace("'", "''") + "'"


def cell_text(v: Any) -> str:
    if v is None or v == "":
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float) and v == int(v) and abs(v) < 1e15:
        return str(int(v))
    return str(v)


def clean_row(cells: Sequence[Any]) -> List[Any]:
    """Row values with trailing empty cells removed (interior blanks stay as '' so columns keep their positions)."""
    out = list(cells)
    while out and (out[-1] is None or out[-1] == ""):
        out.pop()
    return out


def row_json(cells: Sequence[Any]) -> str:
    return json.dumps(list(cells), ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def safe_name(text: str, limit: int = 80) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("._") or "tab"
    return s[:limit]


# ------------------------------------------------------------------------------------------------- import-run bookkeeping
def start_run(con: sqlite3.Connection, name: str, params: Dict[str, Any]) -> int:
    with ldb.transaction(con):
        cur = con.execute("INSERT INTO ops_import_run (kind, source_name, params_json, tool_version) VALUES ('gsheets_pull', ?, ?, 'leadgen.google.sheets')",
                          (name, json.dumps(params, default=str)))
        return int(cur.lastrowid)


def finish_run(con: sqlite3.Connection, run_id: int, status: str, read: int = 0, written: int = 0, skipped: int = 0, rejected: int = 0,
               error: Optional[str] = None) -> None:
    with ldb.transaction(con):
        con.execute("UPDATE ops_import_run SET status=?, finished_at=?, rows_read=?, rows_written=?, rows_skipped=?, rows_rejected=?, error=? WHERE import_run_id=?",
                    (status, ldb.utc_now_iso(), read, written, skipped, rejected, error, run_id))


def set_sync_state(con: sqlite3.Connection, object_type: str, cursor_name: str, value: Optional[str], status: str,
                   error: Optional[str] = None, rows: int = 0, run_id: Optional[int] = None) -> None:
    now = ldb.utc_now_iso()
    with ldb.transaction(con):
        con.execute(
            "INSERT INTO ops_sync_state (source_system, scope, object_type, cursor_name, cursor_value, last_attempt_at, last_success_at, last_status, last_error, rows_synced, import_run_id) "
            "VALUES ('gsheets', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (source_system, scope, object_type, cursor_name) DO UPDATE SET "
            "cursor_value = CASE WHEN excluded.last_status = 'ok' THEN excluded.cursor_value ELSE ops_sync_state.cursor_value END, "
            "last_attempt_at = excluded.last_attempt_at, "
            "last_success_at = CASE WHEN excluded.last_status = 'ok' THEN excluded.last_success_at ELSE ops_sync_state.last_success_at END, "
            "last_status = excluded.last_status, last_error = excluded.last_error, "
            "rows_synced = CASE WHEN excluded.last_status = 'ok' THEN excluded.rows_synced ELSE ops_sync_state.rows_synced END, "
            "import_run_id = excluded.import_run_id",
            (DRIVE_ACCOUNT, object_type, cursor_name, value, now, now if status == "ok" else None, status, error, rows, run_id))


def get_sync_cursor(con: sqlite3.Connection, object_type: str, cursor_name: str) -> Optional[str]:
    r = con.execute("SELECT cursor_value FROM ops_sync_state WHERE source_system='gsheets' AND scope=? AND object_type=? AND cursor_name=? AND last_status='ok'",
                    (DRIVE_ACCOUNT, object_type, cursor_name)).fetchone()
    return r[0] if r else None


# ------------------------------------------------------------------------------------------------- 1. catalog
def ensure_exclusions(con: sqlite3.Connection, catalog: Optional[Sequence[Dict[str, Any]]] = None) -> int:
    """Insert EXTRA_EXCLUSIONS (the 'Creds' sheet is resolved by title in the catalog).  Idempotent; returns rows added."""
    added = 0
    rows = []  # type: List[Tuple[str, str]]
    by_title = {}  # type: Dict[str, str]
    for s in (catalog or []):
        by_title.setdefault(s.get("name", ""), s["id"])
    for sid, reason in EXTRA_EXCLUSIONS:
        if sid == "__CREDS__":
            sid = by_title.get("Creds") or ""
            if not sid:
                r = con.execute("SELECT spreadsheet_id FROM ext_gsheet_catalog WHERE title = 'Creds'").fetchone()
                sid = r[0] if r else ""
        if sid:
            rows.append((sid, reason))
    with ldb.transaction(con):
        for sid, reason in rows:
            added += con.execute("INSERT INTO ext_gsheet_exclusion (spreadsheet_id, reason) VALUES (?, ?) ON CONFLICT (spreadsheet_id) DO NOTHING", (sid, reason)).rowcount
        # make the catalog rows consistent right away (the triggers only fire on catalog writes)
        con.execute("UPDATE ext_gsheet_catalog SET pii_class='excluded', pull_enabled=0 "
                    "WHERE spreadsheet_id IN (SELECT spreadsheet_id FROM ext_gsheet_exclusion) AND (pii_class <> 'excluded' OR pull_enabled <> 0)")
    return added


def load_catalog(con: sqlite3.Connection, path: str = CATALOG_JSON, run_id: Optional[int] = None) -> Dict[str, int]:
    """data/gsheets_catalog.json -> ext_gsheet_catalog / ext_gsheet_tab (upsert; pull_enabled / pii_class are never overwritten)."""
    with open(path, "r") as fh:
        doc = json.load(fh)
    sheets = doc["sheets"]
    fetched = iso_ms(doc.get("generated")) or ldb.utc_now_iso()
    n_new = n_upd = n_tabs = 0
    for i in range(0, len(sheets), 300):                      # short transactions
        with ldb.transaction(con):
            for s in sheets[i:i + 300]:
                owners = s.get("owners") or []
                existed = con.execute("SELECT 1 FROM ext_gsheet_catalog WHERE spreadsheet_id=?", (s["id"],)).fetchone() is not None
                con.execute(
                    "INSERT INTO ext_gsheet_catalog (spreadsheet_id, title, owner_email, owners_json, time_zone, created_time, modified_time, last_modifying_user, "
                    "can_edit, in_shared_drive, web_url, visible_to_json, tab_count, catalog_fetched_at, import_run_id) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT (spreadsheet_id) DO UPDATE SET title=excluded.title, owner_email=excluded.owner_email, owners_json=excluded.owners_json, "
                    "time_zone=excluded.time_zone, created_time=excluded.created_time, modified_time=excluded.modified_time, "
                    "last_modifying_user=excluded.last_modifying_user, can_edit=excluded.can_edit, in_shared_drive=excluded.in_shared_drive, "
                    "web_url=excluded.web_url, visible_to_json=excluded.visible_to_json, tab_count=excluded.tab_count, "
                    "catalog_fetched_at=excluded.catalog_fetched_at, import_run_id=excluded.import_run_id",
                    (s["id"], s.get("name") or "", owners[0].lower() if owners else None, json.dumps(owners), s.get("timeZone"),
                     iso_ms(s.get("createdTime")), iso_ms(s.get("modifiedTime")), s.get("lastModifyingUser"),
                     None if s.get("canEdit") is None else int(bool(s["canEdit"])), 1 if s.get("sharedDriveId") else 0,
                     s.get("url"), json.dumps(s.get("visible_to") or []), len(s.get("tabs") or []), fetched, run_id))
                n_upd += existed
                n_new += not existed
                for idx, t in enumerate(s.get("tabs") or []):
                    con.execute(
                        "INSERT INTO ext_gsheet_tab (spreadsheet_id, sheet_id, title, tab_index, is_hidden, grid_rows, grid_cols, import_run_id) VALUES (?,?,?,?,?,?,?,?) "
                        "ON CONFLICT (spreadsheet_id, sheet_id) DO UPDATE SET title=excluded.title, tab_index=excluded.tab_index, is_hidden=excluded.is_hidden, "
                        "grid_rows=excluded.grid_rows, grid_cols=excluded.grid_cols",
                        (s["id"], int(t["sheetId"]), t.get("title") or "", idx, 1 if t.get("hidden") else 0, t.get("rows"), t.get("cols"), run_id))
                    n_tabs += 1
    added = ensure_exclusions(con, sheets)
    # personal-looking titles (not auto-generated bulk): record the class so nothing pulls them by accident
    with ldb.transaction(con):
        flagged = 0
        for r in con.execute("SELECT spreadsheet_id, title FROM ext_gsheet_catalog WHERE pii_class = 'normal'").fetchall():
            if PII_TITLE_RE.search(r["title"]):
                flagged += con.execute("UPDATE ext_gsheet_catalog SET pii_class='personal', pull_enabled=0 WHERE spreadsheet_id=?", (r["spreadsheet_id"],)).rowcount
    return {"spreadsheets": len(sheets), "new": n_new, "updated": n_upd, "tabs": n_tabs, "exclusions_added": added, "flagged_personal": flagged}


def refresh_catalog_from_drive(con: sqlite3.Connection, drive: Any, cred_name: str, limiter: Optional[auth.RateLimiter] = None,
                               sleep: Callable[[float], None] = time.sleep) -> Dict[str, int]:
    """Drive files.list (metadata only) -> fresh title / modifiedTime / owners for known spreadsheets, rows for new ones.
    Marks ``cred_name`` as able to see each spreadsheet.  Does not touch pull_enabled / pii_class."""
    token = None  # type: Optional[str]
    seen = changed = new = 0
    now = ldb.utc_now_iso()
    while True:
        req = drive.files().list(q="mimeType='%s' and trashed=false" % SHEET_MIME, pageSize=1000, pageToken=token, fields=DRIVE_FIELDS,
                                 corpora="allDrives", includeItemsFromAllDrives=True, supportsAllDrives=True, orderBy="modifiedTime desc")
        resp = auth.execute(req, limiter, sleep=sleep)
        files = resp.get("files", [])
        with ldb.transaction(con):
            for f in files:
                seen += 1
                owners = [o.get("emailAddress", "").lower() for o in f.get("owners", []) if o.get("emailAddress")]
                mod = iso_ms(f.get("modifiedTime"))
                row = con.execute("SELECT modified_time, visible_to_json FROM ext_gsheet_catalog WHERE spreadsheet_id=?", (f["id"],)).fetchone()
                if row is None:
                    con.execute("INSERT INTO ext_gsheet_catalog (spreadsheet_id, title, owner_email, owners_json, created_time, modified_time, last_modifying_user, can_edit, "
                                "in_shared_drive, web_url, visible_to_json, catalog_fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                                (f["id"], f.get("name") or "", owners[0] if owners else None, json.dumps(owners), iso_ms(f.get("createdTime")), mod,
                                 (f.get("lastModifyingUser") or {}).get("emailAddress"), int(bool((f.get("capabilities") or {}).get("canEdit"))),
                                 1 if f.get("driveId") else 0, f.get("webViewLink"), json.dumps([cred_name]), now))
                    new += 1
                    continue
                vis = json.loads(row["visible_to_json"])
                if cred_name not in vis:
                    vis.append(cred_name)
                if row["modified_time"] != mod:
                    changed += 1
                con.execute("UPDATE ext_gsheet_catalog SET title=?, modified_time=?, last_modifying_user=?, visible_to_json=?, catalog_fetched_at=? WHERE spreadsheet_id=?",
                            (f.get("name") or "", mod, (f.get("lastModifyingUser") or {}).get("emailAddress"), json.dumps(vis), now, f["id"]))
        token = resp.get("nextPageToken")
        if not token:
            break
    return {"seen": seen, "modified_changed": changed, "new": new}


# ------------------------------------------------------------------------------------------------- 2. plan
def _config_allowlist(path: str = PULL_CONFIG) -> List[str]:
    try:
        import yaml
        with open(path, "r") as fh:
            data = yaml.safe_load(fh) or {}
    except (OSError, ImportError):
        return []
    return [str(x["id"]) for x in (data.get("spreadsheets") or []) if isinstance(x, dict) and x.get("id")]


def build_plan(con: sqlite3.Connection, include_bulk: bool = False, only: Optional[Sequence[str]] = None,
               extra_ids: Sequence[str] = ()) -> List[Dict[str, Any]]:
    """[{spreadsheet_id, title, owner, group, action ('pull'|'skip'), reason}] - hard-coded first, then human sheets, then bulk."""
    hard = {sid: note for sid, note in HARD_CODED_SHEETS}
    allow = set(_config_allowlist()) | set(extra_ids)
    excluded = {r[0]: r[1] for r in con.execute("SELECT spreadsheet_id, reason FROM ext_gsheet_exclusion")}
    rows = con.execute("SELECT spreadsheet_id, title, owner_email, pii_class, modified_time FROM ext_gsheet_catalog ORDER BY modified_time DESC").fetchall()
    cat = {r["spreadsheet_id"]: r for r in rows}
    plan = []  # type: List[Dict[str, Any]]

    def add(sid: str, title: str, owner: Optional[str], group: str, action: str, reason: str) -> None:
        plan.append({"spreadsheet_id": sid, "title": title, "owner": owner, "group": group, "action": action, "reason": reason})

    for sid, note in HARD_CODED_SHEETS:
        r = cat.get(sid)
        if sid in excluded:
            add(sid, r["title"] if r else note, r["owner_email"] if r else None, "hard_coded", "skip", "HARD-EXCLUDED: " + excluded[sid][:120])
        elif r is None:
            add(sid, note, None, "hard_coded", "skip", "not in the catalog (not a spreadsheet visible to our credentials)")
        else:
            add(sid, r["title"], r["owner_email"], "hard_coded", "pull", note)
    done = set(hard)
    humans, bulk = [], []
    for r in rows:
        sid = r["spreadsheet_id"]
        if sid in done:
            continue
        if sid in excluded:
            add(sid, r["title"], r["owner_email"], "excluded", "skip", "HARD-EXCLUDED: " + excluded[sid][:120])
        elif r["pii_class"] == "personal" or PII_TITLE_RE.search(r["title"] or ""):
            add(sid, r["title"], r["owner_email"], "flagged", "skip", "title looks personal (pii_class=personal): not pulled automatically")
        elif r["owner_email"] == BULK_OWNER and sid not in allow:
            bulk.append(r)
        else:
            humans.append(r)
    for r in humans:
        add(r["spreadsheet_id"], r["title"], r["owner_email"], "human", "pull", "human-owned sheet" if r["spreadsheet_id"] not in allow else "config/gsheets_pull.yaml allow-list")
    for r in bulk:
        add(r["spreadsheet_id"], r["title"], r["owner_email"], "bulk", "pull" if include_bulk else "skip",
            "auto-generated bulk (owner %s)%s" % (BULK_OWNER, "" if include_bulk else ": catalogued, not pulled (see docs/GOOGLE_PULLS.md)"))
    if only:
        want = set(only)
        plan = [p for p in plan if p["spreadsheet_id"] in want]
    return plan


def enable_pulls(con: sqlite3.Connection, plan: Sequence[Dict[str, Any]]) -> int:
    """pull_enabled = 1 for planned spreadsheets (never for an excluded / personal one - the database enforces that too)."""
    ids = [p["spreadsheet_id"] for p in plan if p["action"] == "pull"]
    n = 0
    with ldb.transaction(con):
        for sid in ids:
            n += con.execute("UPDATE ext_gsheet_catalog SET pull_enabled=1 WHERE spreadsheet_id=? AND pii_class <> 'excluded' "
                             "AND spreadsheet_id NOT IN (SELECT spreadsheet_id FROM ext_gsheet_exclusion) AND pull_enabled = 0", (sid,)).rowcount
    return n


# ------------------------------------------------------------------------------------------------- 3. reading
class SheetReader(object):
    """Calls Google (read-only) for one principal: tab list + batchGet."""

    def __init__(self, service: Any, limiter: Optional[auth.RateLimiter] = None, sleep: Callable[[float], None] = time.sleep,
                 on_retry: Optional[Callable[[int, float, BaseException], None]] = None):
        self.svc = auth.guard(service)
        self.limiter = limiter
        self.sleep = sleep
        self.on_retry = on_retry
        self.calls = 0

    def _exec(self, req: Any) -> Any:
        self.calls += 1
        return auth.execute(req, self.limiter, sleep=self.sleep, on_retry=self.on_retry)

    def tabs(self, spreadsheet_id: str) -> Dict[str, Any]:
        return self._exec(self.svc.spreadsheets().get(spreadsheetId=spreadsheet_id, fields=SHEETS_FIELDS_TABS, includeGridData=False))

    def batch_get(self, spreadsheet_id: str, ranges: Sequence[str]) -> List[Dict[str, Any]]:
        resp = self._exec(self.svc.spreadsheets().values().batchGet(
            spreadsheetId=spreadsheet_id, ranges=list(ranges), valueRenderOption="UNFORMATTED_VALUE",
            dateTimeRenderOption="FORMATTED_STRING", majorDimension="ROWS"))
        return resp.get("valueRanges", [])


def plan_requests(tabs: Sequence[Dict[str, Any]], chunk_rows: int = CHUNK_ROWS, cell_budget: int = REQUEST_CELL_BUDGET,
                  max_ranges: int = REQUEST_MAX_RANGES) -> List[List[Dict[str, Any]]]:
    """Group per-tab read ranges into batchGet requests.  Each item: {tab, range, start_row}.  Tabs with more than ``chunk_rows``
    allocated rows are split into row chunks; requests are filled up to ``cell_budget`` allocated cells / ``max_ranges`` ranges."""
    items = []  # type: List[Tuple[int, Dict[str, Any]]]
    for t in tabs:
        rows = int(t.get("grid_rows") or 0)
        cols = int(t.get("grid_cols") or 26)
        q = quote_tab(t["title"])
        if rows > chunk_rows:
            last = col_letters(cols)
            for start in range(1, rows + 1, chunk_rows):
                end = min(start + chunk_rows - 1, rows)
                items.append((chunk_rows * cols, {"tab": t, "range": "%s!A%d:%s%d" % (q, start, last, end), "start_row": start}))
        else:
            items.append((max(rows * cols, 1), {"tab": t, "range": q, "start_row": 1}))
    groups, cur, cells = [], [], 0
    for cost, it in items:
        if cur and (cells + cost > cell_budget or len(cur) >= max_ranges):
            groups.append(cur)
            cur, cells = [], 0
        cur.append(it)
        cells += cost
    if cur:
        groups.append(cur)
    return groups


def digest_values(value_ranges: Iterable[Tuple[int, Sequence[Sequence[Any]]]]) -> Tuple[List[Tuple[int, str, str, str]], int]:
    """[(start_row, values)] -> ([(row_number, cells_json, text_flat, row_sha256)] with empty rows dropped, rows_seen)."""
    out = []
    seen = 0
    for start, values in value_ranges:
        for i, raw in enumerate(values or []):
            seen += 1
            cells = clean_row(raw)
            if not cells:
                continue
            cj = row_json(cells)
            out.append((start + i, cj, " ".join(t for t in (cell_text(c) for c in cells) if t), sha256_hex(cj)))
    return out, seen


def tab_hash(rows: Sequence[Tuple[int, str, str, str]]) -> str:
    h = hashlib.sha256()
    for n, cj, _t, _s in rows:
        h.update(("%d\t%s\n" % (n, cj)).encode("utf-8"))
    return h.hexdigest()


# ------------------------------------------------------------------------------------------------- 4. storing
def store_tab(con: sqlite3.Connection, tab_pk: int, rows: Sequence[Tuple[int, str, str, str]], fetched_at: str, header: Optional[List[Any]],
              run_id: Optional[int]) -> Dict[str, int]:
    """Diff-write one tab: only rows whose sha changed are upserted, rows that vanished are deleted.  Short transactions of DB_BATCH rows."""
    existing = {r[0]: r[1] for r in con.execute("SELECT row_number, row_sha256 FROM ext_gsheet_row WHERE tab_pk=?", (tab_pk,))}
    new_nums = set(r[0] for r in rows)
    gone = [n for n in existing if n not in new_nums]
    changed = [r for r in rows if existing.get(r[0]) != r[3]]
    for i in range(0, len(gone), DB_BATCH):
        with ldb.transaction(con):
            con.executemany("DELETE FROM ext_gsheet_row WHERE tab_pk=? AND row_number=?", [(tab_pk, n) for n in gone[i:i + DB_BATCH]])
    for i in range(0, len(changed), DB_BATCH):
        with ldb.transaction(con):
            con.executemany(
                "INSERT INTO ext_gsheet_row (tab_pk, row_number, cells_json, text_flat, row_sha256, fetched_at) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT (tab_pk, row_number) DO UPDATE SET cells_json=excluded.cells_json, text_flat=excluded.text_flat, "
                "row_sha256=excluded.row_sha256, fetched_at=excluded.fetched_at",
                [(tab_pk, n, cj, tx, sh, fetched_at) for (n, cj, tx, sh) in changed[i:i + DB_BATCH]])
    status = "ok" if rows else "empty"
    with ldb.transaction(con):
        con.execute("UPDATE ext_gsheet_tab SET pull_status=?, pull_error=NULL, pulled_rows=?, content_sha256=?, pulled_at=?, header_json=?, import_run_id=? WHERE tab_pk=?",
                    (status, len(rows), tab_hash(rows), fetched_at, json.dumps(header, ensure_ascii=False) if header else None, run_id, tab_pk))
    return {"written": len(changed), "deleted": len(gone), "unchanged": len(rows) - len(changed)}


def mark_tab_error(con: sqlite3.Connection, tab_pk: int, message: str) -> None:
    with ldb.transaction(con):
        con.execute("UPDATE ext_gsheet_tab SET pull_status='error', pull_error=? WHERE tab_pk=?", (message[:500], tab_pk))


def write_csv(path: str, rows: Sequence[Tuple[int, str, str, str]]) -> None:
    """One CSV per tab, row numbers preserved (skipped empty rows become blank lines).  Atomic."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        nxt = 1
        for n, cj, _t, _s in rows:
            while nxt < n:
                w.writerow([])
                nxt += 1
            w.writerow([cell_text(c) for c in json.loads(cj)])
            nxt = n + 1
    os.replace(tmp, path)


def sync_tabs(con: sqlite3.Connection, spreadsheet_id: str, meta: Dict[str, Any], run_id: Optional[int]) -> None:
    """Refresh ext_gsheet_tab from a spreadsheets.get response (tab list changed / catalog had none)."""
    tz = (meta.get("properties") or {}).get("timeZone")
    with ldb.transaction(con):
        keep = []
        for s in meta.get("sheets", []):
            p = s.get("properties", {})
            g = p.get("gridProperties", {})
            keep.append(int(p["sheetId"]))
            con.execute(
                "INSERT INTO ext_gsheet_tab (spreadsheet_id, sheet_id, title, tab_index, is_hidden, grid_rows, grid_cols, import_run_id) VALUES (?,?,?,?,?,?,?,?) "
                "ON CONFLICT (spreadsheet_id, sheet_id) DO UPDATE SET title=excluded.title, tab_index=excluded.tab_index, is_hidden=excluded.is_hidden, "
                "grid_rows=excluded.grid_rows, grid_cols=excluded.grid_cols",
                (spreadsheet_id, int(p["sheetId"]), p.get("title") or "", p.get("index"), 1 if p.get("hidden") else 0, g.get("rowCount"), g.get("columnCount"), run_id))
        if keep:                                                       # tabs that no longer exist at Google go (their rows cascade)
            con.execute("DELETE FROM ext_gsheet_tab WHERE spreadsheet_id=? AND sheet_id NOT IN (%s)" % ",".join("?" * len(keep)), [spreadsheet_id] + keep)
        con.execute("UPDATE ext_gsheet_catalog SET tab_count=?, time_zone=COALESCE(?, time_zone) WHERE spreadsheet_id=?", (len(keep), tz, spreadsheet_id))


class WriteDenied(RuntimeError):
    pass


def _stored_tabs(con: sqlite3.Connection, spreadsheet_id: str) -> List[Dict[str, Any]]:
    return [dict(r) for r in con.execute(
        "SELECT tab_pk, sheet_id, title, grid_rows, grid_cols FROM ext_gsheet_tab WHERE spreadsheet_id=? ORDER BY tab_index, sheet_id", (spreadsheet_id,))]


def pull_spreadsheet(con: sqlite3.Connection, reader: SheetReader, spreadsheet_id: str, run_id: Optional[int] = None,
                     data_dir: Optional[str] = DATA_DIR, now: Callable[[], str] = ldb.utc_now_iso) -> Dict[str, Any]:
    """Read every tab of one spreadsheet and store it.  Guards: the catalog row must have pull_enabled = 1 and must not be excluded.
    Returns {tabs, rows, bytes, written, errors:[...], calls}."""
    cat = con.execute("SELECT title, pull_enabled, pii_class, modified_time FROM ext_gsheet_catalog WHERE spreadsheet_id=?", (spreadsheet_id,)).fetchone()
    if cat is None:
        raise KeyError("spreadsheet %s is not in the catalog" % spreadsheet_id)
    excluded = con.execute("SELECT 1 FROM ext_gsheet_exclusion WHERE spreadsheet_id=?", (spreadsheet_id,)).fetchone()
    if excluded or cat["pii_class"] == "excluded" or not cat["pull_enabled"]:
        raise WriteDenied("%s is excluded / not pull_enabled: refusing to read it" % spreadsheet_id)
    calls0 = reader.calls
    tabs = _stored_tabs(con, spreadsheet_id)
    if not tabs:
        sync_tabs(con, spreadsheet_id, reader.tabs(spreadsheet_id), run_id)
        tabs = _stored_tabs(con, spreadsheet_id)
    result = {"tabs": 0, "rows": 0, "bytes": 0, "written": 0, "errors": [], "calls": 0}  # type: Dict[str, Any]
    fetched_at = now()
    by_pk = {t["tab_pk"]: t for t in tabs}
    values = {}  # type: Dict[int, List[Tuple[int, Sequence[Sequence[Any]]]]]
    failed = set()  # type: set
    refreshed = False
    pending = plan_requests(tabs)
    while pending:
        group = pending.pop(0)
        try:
            vrs = reader.batch_get(spreadsheet_id, [g["range"] for g in group])
        except Exception as exc:  # noqa: BLE001 - per-spreadsheet failure is reported, not fatal
            status, reason, msg = auth.http_error_details(exc) if hasattr(exc, "resp") else (0, "", str(exc))
            if status == 400 and "Unable to parse range" in msg and not refreshed:
                refreshed = True                                             # a tab was renamed / deleted since the catalog: re-read the tab list
                sync_tabs(con, spreadsheet_id, reader.tabs(spreadsheet_id), run_id)
                tabs = _stored_tabs(con, spreadsheet_id)
                by_pk = {t["tab_pk"]: t for t in tabs}
                values.clear()
                pending = plan_requests(tabs)
                continue
            for g in group:
                failed.add(g["tab"]["tab_pk"])
                mark_tab_error(con, g["tab"]["tab_pk"], "HTTP %s %s: %s" % (status, reason, msg) if status else "%s: %s" % (type(exc).__name__, str(exc)[:200]))
                result["errors"].append("%s: %s" % (g["tab"]["title"], (msg or str(exc))[:200]))
            if status in (401, 403, 404):
                result["access_error"] = status
            continue
        for g, vr in zip(group, vrs):
            values.setdefault(g["tab"]["tab_pk"], []).append((g["start_row"], vr.get("values") or []))
    for pk, t in by_pk.items():
        if pk in failed:
            continue
        rows, _seen = digest_values(sorted(values.get(pk, []), key=lambda x: x[0]))
        header = None
        if rows and rows[0][0] == 1:
            header = json.loads(rows[0][1])
        st = store_tab(con, pk, rows, fetched_at, header, run_id)
        if data_dir:
            write_csv(os.path.join(data_dir, "%s__%s.csv" % (spreadsheet_id, safe_name(t["title"]))) if not _name_clash(tabs, t)
                      else os.path.join(data_dir, "%s__%s_%d.csv" % (spreadsheet_id, safe_name(t["title"]), t["sheet_id"])), rows)
        result["tabs"] += 1
        result["rows"] += len(rows)
        result["bytes"] += sum(len(r[1]) for r in rows)
        result["written"] += st["written"]
    result["calls"] = reader.calls - calls0
    if not failed:
        with ldb.transaction(con):
            con.execute("UPDATE ext_gsheet_catalog SET data_pulled_at=?, import_run_id=COALESCE(?, import_run_id) WHERE spreadsheet_id=?", (fetched_at, run_id, spreadsheet_id))
    return result


def _name_clash(tabs: Sequence[Dict[str, Any]], t: Dict[str, Any]) -> bool:
    return sum(1 for x in tabs if safe_name(x["title"]) == safe_name(t["title"])) > 1


# ------------------------------------------------------------------------------------------------- 5. orchestration
class ReaderPool(object):
    """Lazily built SheetReaders per credential name; user tokens share one limiter (same user + project quota)."""

    def __init__(self, per_minute: float = DEFAULT_PER_MINUTE, sleep: Callable[[float], None] = time.sleep,
                 factory: Optional[Callable[[str], Any]] = None):
        self.per_minute = per_minute
        self.sleep = sleep
        self.factory = factory or self._real
        self.user_limiter = auth.RateLimiter(per_minute, sleep=sleep)
        self.sa_limiter = auth.RateLimiter(per_minute, sleep=sleep)
        self.readers = {}  # type: Dict[str, Optional[SheetReader]]
        self.errors = {}  # type: Dict[str, str]

    @staticmethod
    def _real(name: str) -> Any:
        return auth.build_service("sheets", "v4", auth.sheets_credentials(name))

    def get(self, name: str) -> Optional[SheetReader]:
        if name not in self.readers:
            try:
                svc = self.factory(name)
                self.readers[name] = SheetReader(svc, self.sa_limiter if name == "sa_lh2bot" else self.user_limiter, sleep=self.sleep,
                                                 on_retry=lambda a, d, e: log("  quota/backoff: attempt %d, sleeping %.0fs (%s)" % (a + 1, d, type(e).__name__)))
            except auth.TokenError as exc:
                self.readers[name] = None
                self.errors[name] = str(exc)
                log("credential %s unavailable: %s" % (name, exc))
        return self.readers[name]

    def candidates(self, visible_to: Sequence[str]) -> List[str]:
        """Credential names that can see a spreadsheet, preferred first.  Readers are built lazily (``get``), so a fallback credential
        is only loaded - and its token only refreshed - when the preferred one failed."""
        return [n for n in ("hubspot_sheets_token", "companyops_sheets_token", "sa_lh2bot") if n in visible_to]

    def choose(self, visible_to: Sequence[str]) -> List[SheetReader]:
        return [r for r in (self.get(n) for n in self.candidates(visible_to)) if r is not None]


def needs_pull(con: sqlite3.Connection, spreadsheet_id: str, force: bool = False) -> bool:
    if force:
        return True
    r = con.execute("SELECT modified_time FROM ext_gsheet_catalog WHERE spreadsheet_id=?", (spreadsheet_id,)).fetchone()
    cur = get_sync_cursor(con, "values", spreadsheet_id)
    if cur is None or r is None or r["modified_time"] is None or cur != r["modified_time"]:
        return True
    bad = con.execute("SELECT 1 FROM ext_gsheet_tab WHERE spreadsheet_id=? AND pull_status IN ('pending','error') LIMIT 1", (spreadsheet_id,)).fetchone()
    return bad is not None


def run_pull(con: sqlite3.Connection, pool: ReaderPool, plan: Sequence[Dict[str, Any]], force: bool = False, max_sheets: Optional[int] = None,
             max_runtime: Optional[float] = None, data_dir: Optional[str] = DATA_DIR, clock: Callable[[], float] = time.monotonic) -> Dict[str, Any]:
    """Pull every ``action == 'pull'`` spreadsheet of the plan; resumable (per-spreadsheet modifiedTime cursor)."""
    todo = [p for p in plan if p["action"] == "pull"]
    enable_pulls(con, todo)
    run_id = start_run(con, "gsheets:pull", {"force": force, "max_sheets": max_sheets, "planned": len(todo)})
    t0 = clock()
    summary = {"run_id": run_id, "planned": len(todo), "pulled": 0, "unchanged": 0, "failed": 0, "rows": 0, "bytes": 0, "tabs": 0, "calls": 0,
               "failures": [], "stopped_early": False}  # type: Dict[str, Any]
    status = "succeeded"
    try:
        for i, p in enumerate(todo):
            sid = p["spreadsheet_id"]
            if max_runtime is not None and clock() - t0 > max_runtime or (max_sheets is not None and summary["pulled"] + summary["failed"] >= max_sheets):
                summary["stopped_early"] = True
                break
            if not needs_pull(con, sid, force):
                summary["unchanged"] += 1
                continue
            cat = con.execute("SELECT modified_time, visible_to_json FROM ext_gsheet_catalog WHERE spreadsheet_id=?", (sid,)).fetchone()
            names = pool.candidates(json.loads(cat["visible_to_json"]))
            set_sync_state(con, "values", sid, None, "running", run_id=run_id)
            res, err = None, "no usable credential"
            for name in names:                           # fall back to the next credential only on an access error
                reader = pool.get(name)
                if reader is None:
                    continue
                try:
                    res = pull_spreadsheet(con, reader, sid, run_id, data_dir)
                except Exception as exc:  # noqa: BLE001
                    res, err = None, "%s: %s" % (type(exc).__name__, str(exc)[:300])
                    break
                if not res["errors"]:
                    break
                err = "; ".join(res["errors"])[:400]
                if res.get("access_error") not in (403, 404):
                    break
            if res is not None and not res["errors"]:
                set_sync_state(con, "values", sid, cat["modified_time"], "ok", rows=res["rows"], run_id=run_id)
                summary["pulled"] += 1
                for k in ("rows", "bytes", "tabs", "calls"):
                    summary[k] += res[k]
                log("[%d/%d] ok   %-48s tabs=%d rows=%d calls=%d" % (i + 1, len(todo), (p["title"] or "")[:48], res["tabs"], res["rows"], res["calls"]))
            else:
                set_sync_state(con, "values", sid, None, "error", error=err, run_id=run_id)
                summary["failed"] += 1
                summary["failures"].append((sid, p["title"], err))
                if res is not None:
                    summary["rows"] += res["rows"]
                    summary["bytes"] += res["bytes"]
                    summary["calls"] += res["calls"]
                log("[%d/%d] FAIL %-48s %s" % (i + 1, len(todo), (p["title"] or "")[:48], err))
        if summary["failed"]:
            status = "partial"
    except KeyboardInterrupt:
        status = "aborted"
        summary["stopped_early"] = True
        raise
    except BaseException:
        status = "failed"
        raise
    finally:
        finish_run(con, run_id, status, read=summary["rows"], written=summary["rows"], skipped=summary["unchanged"], rejected=summary["failed"])
    return summary


def sample_sheets(reader: SheetReader, spreadsheet_ids: Sequence[str], rows: int = 4) -> List[Dict[str, Any]]:
    """Read the first rows of the first tab of some spreadsheets WITHOUT storing anything (used to judge whether the bulk is human data)."""
    out = []
    for sid in spreadsheet_ids:
        vr = reader.batch_get(sid, ["A1:ZZ%d" % rows])
        vals = (vr[0].get("values") if vr else None) or []
        out.append({"spreadsheet_id": sid, "rows": [[cell_text(c)[:60] for c in clean_row(r)[:12]] for r in vals[:rows]]})
    return out


# ------------------------------------------------------------------------------------------------- CLI
def status_report(con: sqlite3.Connection) -> Dict[str, Any]:
    q = lambda sql: [dict(r) for r in con.execute(sql)]
    return {
        "catalog": q("SELECT COUNT(*) spreadsheets, SUM(pull_enabled) pull_enabled, SUM(pii_class='excluded') excluded, SUM(pii_class='personal') personal, "
                     "SUM(data_pulled_at IS NOT NULL) pulled FROM ext_gsheet_catalog")[0],
        "tabs": q("SELECT pull_status, COUNT(*) n, COALESCE(SUM(pulled_rows),0) rows FROM ext_gsheet_tab GROUP BY pull_status"),
        "rows_stored": con.execute("SELECT COUNT(*) FROM ext_gsheet_row").fetchone()[0],
        "sync_state": q("SELECT last_status, COUNT(*) n FROM ops_sync_state WHERE source_system='gsheets' GROUP BY last_status"),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m leadgen.google.sheets", description="Read-only Google Sheets pull")
    ap.add_argument("command", choices=["catalog", "plan", "pull", "sample", "status"])
    ap.add_argument("--db", help="database path (default db/leadgen.sqlite)")
    ap.add_argument("--include-bulk", action="store_true", help="also pull the auto-generated bulk owned by %s" % BULK_OWNER)
    ap.add_argument("--only", action="append", default=[], help="restrict to this spreadsheet id (repeatable)")
    ap.add_argument("--force", action="store_true", help="re-read even if modifiedTime has not changed")
    ap.add_argument("--per-minute", type=float, default=DEFAULT_PER_MINUTE)
    ap.add_argument("--max-sheets", type=int)
    ap.add_argument("--max-runtime", type=float, help="seconds; stop starting new spreadsheets after this")
    ap.add_argument("--no-drive-refresh", action="store_true", help="pull: skip the Drive files.list modifiedTime refresh")
    ap.add_argument("--no-csv", action="store_true")
    ap.add_argument("-n", type=int, default=10, help="sample: how many bulk sheets")
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args(argv)
    con = ldb.connect(args.db, must_exist=True)
    try:
        if args.command == "catalog":
            rid = start_run(con, "gsheets:catalog", {"path": CATALOG_JSON})
            try:
                out = load_catalog(con, CATALOG_JSON, rid)
                finish_run(con, rid, "succeeded", read=out["spreadsheets"], written=out["new"] + out["updated"])
                set_sync_state(con, "catalog", "file", ldb.utc_now_iso(), "ok", rows=out["spreadsheets"], run_id=rid)
            except BaseException as exc:
                finish_run(con, rid, "failed", error=str(exc)[:300])
                raise
            print(json.dumps(out, indent=2))
        elif args.command == "plan":
            plan = build_plan(con, args.include_bulk, args.only)
            groups = {}  # type: Dict[Tuple[str, str], int]
            for p in plan:
                groups[(p["group"], p["action"])] = groups.get((p["group"], p["action"]), 0) + 1
            print(json.dumps({"%s/%s" % k: v for k, v in sorted(groups.items())}, indent=2))
            for p in plan:
                if p["group"] in ("hard_coded", "excluded", "flagged"):
                    print("%-10s %-5s %s  %s | %s" % (p["group"], p["action"], p["spreadsheet_id"], (p["title"] or "")[:40], p["reason"][:90]))
        elif args.command == "pull":
            pool = ReaderPool(args.per_minute)
            if not args.no_drive_refresh:
                for name in ("hubspot_sheets_token", "sa_lh2bot"):
                    try:
                        creds = auth.sheets_credentials(name)
                        drive = auth.build_service("drive", "v3", creds)
                        r = refresh_catalog_from_drive(con, drive, name, auth.RateLimiter(120))
                        log("drive refresh via %s: %s" % (name, r))
                        set_sync_state(con, "catalog", "drive_modified_time:" + name, ldb.utc_now_iso(), "ok", rows=r["seen"])
                    except auth.TokenError as exc:
                        log("drive refresh via %s skipped: %s" % (name, exc))
            plan = build_plan(con, args.include_bulk, args.only)
            summary = run_pull(con, pool, plan, force=args.force, max_sheets=args.max_sheets, max_runtime=args.max_runtime,
                               data_dir=None if args.no_csv else DATA_DIR)
            print(json.dumps(summary, indent=2, default=str))
            return 0 if not summary["failed"] else 3
        elif args.command == "sample":
            import random
            ids = [r[0] for r in con.execute("SELECT spreadsheet_id FROM ext_gsheet_catalog WHERE owner_email=? AND pii_class='normal' ORDER BY spreadsheet_id", (BULK_OWNER,))]
            random.Random(args.seed).shuffle(ids)
            reader = ReaderPool(args.per_minute).get("hubspot_sheets_token")
            if reader is None:
                print("no credential available", file=sys.stderr)
                return 2
            print(json.dumps(sample_sheets(reader, ids[:args.n]), indent=1, ensure_ascii=False))
        else:
            print(json.dumps(status_report(con), indent=2, default=str))
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
