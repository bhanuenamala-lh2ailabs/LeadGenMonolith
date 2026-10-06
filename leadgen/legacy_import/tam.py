"""Legacy importer for the companyOps TAM database (legacy/companyOps/tam/data/tam.sqlite3).

Three separable stages (``--only``), each idempotent and each recorded as its own ``ops_import_run``:

``verbatim``   every one of the 17 source tables -> ``legacy_tam_*`` (migration 0006), same columns / values + ``_import_run_id``.
               Streamed from a read-only / immutable connection, committed in batches (default 50k rows or 32 MB), resumable per table:
               a table that is complete for this source file's sha256 (``ops_sync_state``) is skipped; an unfinished one is emptied and
               reloaded.  Foreign keys are OFF during the load (outside any transaction), ``PRAGMA foreign_key_check`` afterwards, every
               orphan -> ``ops_dq_issue('legacy_fk_orphan')``.  Row counts AND a byte-length checksum of every table are verified.
``canonical``  companies -> company / company_identifier (strong-key auto-link, also to companies that already exist, e.g. from HubSpot);
               categories -> vertical; company_categories (+ classifications) -> tam_company_verdict (rejects kept); signals ->
               company_signal; cost_ledger -> cost_ledger (usd_est 0 -> NULL / 'unknown'); crm_pushes -> suppression (delivered /
               off_icp / too_big / duplicate); ``merged_into`` -> company.merged_into_company_id.  Every canonical row has an
               ``origin_ref`` (company_signal has no origin_ref entity type: its provenance is source_system + import_run_id).
``link``       :func:`link_crm_pushes` - crm_pushes -> HubSpot deals of the COMPANYOPS account that exist in ``deal``; origin_ref on the
               deal and on the golden company, ``merge_candidate`` when the deal's company is not the tam company, unmatched deal ids ->
               ``ops_dq_issue('tam_push_unmatched_deal')`` (resolved automatically by a later re-run).  Reads only the target database.

``funnel_events`` are NOT deal_stage_event rows (they are sourcing-funnel steps of a company, not HubSpot stage moves): they stay
in ``legacy_tam_funnel_events``; the per-(company, category) final funnel state is copied into ``tam_company_verdict.details_json``.

Run:  ``.venv/bin/python -m leadgen.legacy_import.tam [--only verbatim|canonical|link] [--dry-run] [--db PATH] [--source PATH]``
Never prints PII or secrets: logs carry counts and internal numeric ids only.  Python 3.9 compatible.
"""
import argparse
import datetime
import hashlib
import json
import logging
import os
import sqlite3
import sys
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from leadgen import db, norm

log = logging.getLogger("leadgen.legacy_import.tam")

SOURCE_SYSTEM = "tam"
PREFIX = "legacy_tam_"
DEFAULT_SOURCE = os.path.join("legacy", "companyOps", "tam", "data", "tam.sqlite3")
ACCOUNT = "companyops"
TOOL_VERSION = "import_tam/1.0"
#: sha256 of the source schema recorded in the header of migration 0006 (tools/gen_legacy_ddl.py fingerprint()).
EXPECTED_SCHEMA_SHA = "eff0a2328b8e975a56c5828fffa2be2bc4901c574114183753a787af066d47dd"
#: parents first (self / forward references need no ordering because foreign_keys is OFF during the load, but keep it tidy)
LOAD_ORDER = ["categories", "stage_runs", "companies", "category_sources", "company_categories", "classifications", "contacts", "signals",
              "raw_sources", "pages", "funnel_events", "crm_pushes", "cost_ledger", "outreach_batches", "salesnav_import",
              "signalhire_results", "async_jobs"]
DEFAULT_BATCH_ROWS = 50000
BATCH_BYTES = 32 * 1024 * 1024
FETCH_ROWS = 2000
#: tam crm_pushes.status -> suppression.kind (docs/DATA_DICTIONARY.md "Importer normalisation contract")
PUSH_SUPPRESSION = {"pushed": "delivered", "removed_off_icp": "off_icp", "removed_too_big": "too_big", "removed_duplicate": "duplicate"}
BUCKET = {"Fit": "fit", "Maybe": "maybe", "Out": "out"}
#: signals columns -> (signal_code, kind)
SIGNAL_COLUMNS = (("uses_jira", "bool"), ("uses_slack", "bool"), ("uses_asana", "bool"), ("uses_clickup", "bool"), ("uses_gworkspace", "bool"),
                  ("jira_scrum_mentions", "num"), ("open_jobs", "num"), ("mx_provider", "text"))
#: apollo phone_reveal burns 8 credits per call; the legacy ledger stored the credits in qty
CREDITS_PER_CALL = {"phone_reveal": 8.0}
STRONG_PRIORITY = ("root_domain", "linkedin_company", "google_place_id", "apollo_org", "cin", "llpin")


class ImportErrorTam(RuntimeError):
    """Pre-flight failure (bad source / target); nothing was written."""


# ------------------------------------------------------------------------------------------------------ small helpers
def _now() -> str:
    return db.utc_now_iso()


def _s(v: Any) -> Optional[str]:
    """str(v).strip() or None."""
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _ts(v: Any) -> Optional[str]:
    return norm.to_utc_ms(v) if v is not None else None


def _jdump(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def _maybe_json(v: Any) -> Any:
    """A text column that holds JSON is stored parsed; anything else stays text."""
    if isinstance(v, str) and v[:1] in ("{", "["):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _rel(path: str) -> str:
    p = os.path.abspath(path)
    try:
        r = os.path.relpath(p, db.PROJECT_ROOT)
    except ValueError:  # pragma: no cover - other drive on Windows
        return p
    return p if r.startswith("..") else r


def _qi(name: str) -> str:
    return '"%s"' % name.replace('"', '""')


class Tally(object):
    """Counters + a few internal ids per data-quality rule (ids only, never values)."""

    def __init__(self) -> None:
        self.n = {}  # type: Dict[str, int]
        self.ids = {}  # type: Dict[str, List[str]]

    def add(self, key: str, ident: Any = None, keep: int = 25) -> None:
        self.n[key] = self.n.get(key, 0) + 1
        if ident is not None:
            lst = self.ids.setdefault(key, [])
            if len(lst) < keep:
                lst.append(str(ident))


# ------------------------------------------------------------------------------------------------------ source / run plumbing
def open_source(path: str) -> sqlite3.Connection:
    """The legacy file, strictly read-only AND immutable (sqlite never writes, never touches -wal/-shm)."""
    p = os.path.abspath(path)
    if not os.path.isfile(p) or os.path.getsize(p) == 0:
        raise ImportErrorTam("source database missing or empty: %s" % _rel(p))
    con = sqlite3.connect("file:%s?mode=ro&immutable=1" % p, uri=True)
    con.text_factory = str
    return con


def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(4 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def schema_sha(src: sqlite3.Connection) -> str:
    h = hashlib.sha256()
    for typ, name, tbl, sql in src.execute("SELECT type, name, tbl_name, sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' "
                                           "ORDER BY type, name"):
        h.update(("%s|%s|%s|%s\n" % (typ, name, tbl, sql)).encode("utf-8"))
    return h.hexdigest()


def source_tables(src: sqlite3.Connection) -> Dict[str, List[Tuple[str, str, int]]]:
    """{table: [(column, declared type, pk position)]} of the source (sqlite_% excluded)."""
    out = {}
    for (name,) in src.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall():
        out[name] = [(r[1], r[2] or "", r[5]) for r in src.execute("PRAGMA table_info(%s)" % _qi(name))]
    return out


def _rowid_alias(cols: List[Tuple[str, str, int]]) -> bool:
    """True when the table has an INTEGER PRIMARY KEY (a rowid alias).  'INT PRIMARY KEY' and composite keys are NOT aliases."""
    pks = [c for c in cols if c[2]]
    return len(pks) == 1 and pks[0][1].upper() == "INTEGER"


def _require_schema(con: sqlite3.Connection, stage: str) -> None:
    need = {"verbatim": ["legacy_tam_companies", "ops_import_run", "ops_sync_state"],
            "canonical": ["company", "company_identifier", "tam_company_verdict", "origin_ref", "vertical", "cost_ledger"],
            "link": ["deal", "origin_ref", "ops_dq_issue", "legacy_tam_crm_pushes"]}[stage]
    have = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    missing = [t for t in need if t not in have]
    if missing:
        raise ImportErrorTam("target database is not migrated far enough for stage '%s' (missing %s): run `python -m leadgen.db migrate`" % (stage, missing))


def start_run(con: sqlite3.Connection, source_name: str, source_path: str, fingerprint: str, params: Dict[str, Any]) -> int:
    with db.transaction(con):
        cur = con.execute("INSERT INTO ops_import_run (kind, source_name, source_path, source_fingerprint, account_id, status, params_json, tool_version) "
                          "VALUES ('legacy_import', ?, ?, ?, NULL, 'running', ?, ?)",
                          (source_name, _rel(source_path), fingerprint, _jdump(params), TOOL_VERSION))
        return int(cur.lastrowid)


def finish_run(con: sqlite3.Connection, run_id: int, status: str, read: int = 0, written: int = 0, skipped: int = 0, rejected: int = 0,
               error: Optional[str] = None) -> None:
    with db.transaction(con):
        con.execute("UPDATE ops_import_run SET status = ?, finished_at = ?, rows_read = ?, rows_written = ?, rows_skipped = ?, rows_rejected = ?, error = ? "
                    "WHERE import_run_id = ?", (status, _now(), read, written, skipped, rejected, error, run_id))


def audit(con: sqlite3.Connection, run_id: int, action: str, after: Dict[str, Any], note: Optional[str] = None) -> None:
    with db.transaction(con):
        con.execute("INSERT INTO ops_audit_log (actor, action, entity_type, entity_ref, after_json, note, import_run_id) VALUES ('cli:import-tam', ?, 'import_run', ?, ?, ?, ?)",
                    (action, str(run_id), _jdump(after), note, run_id))


def dq(con: sqlite3.Connection, run_id: Optional[int], fingerprint: str, rule_code: str, severity: str, message: str,
       entity_type: Optional[str] = None, entity_ref: Optional[str] = None, details: Optional[Dict[str, Any]] = None) -> None:
    """Upsert one ops_dq_issue on its fingerprint (occurrences++ on a repeat).  Callers inside a transaction pass the same connection."""
    con.execute(
        "INSERT INTO ops_dq_issue (fingerprint, rule_code, severity, entity_type, entity_ref, message, details_json, import_run_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(fingerprint) DO UPDATE SET occurrences = occurrences + 1, last_seen_at = excluded.last_seen_at, import_run_id = excluded.import_run_id, "
        "details_json = excluded.details_json, message = excluded.message",
        (fingerprint, rule_code, severity, entity_type, entity_ref, message, _jdump(details or {}), run_id))
    # last_seen_at default is 'now' on insert; on conflict excluded.last_seen_at is the column default too (evaluated for the proposed row)


def flush_tally(con: sqlite3.Connection, run_id: int, tally: Tally, rules: Dict[str, Tuple[str, str]]) -> Dict[str, int]:
    """One aggregated ops_dq_issue per rule that fired (fingerprint 'tam_import|<rule>': a re-run updates it instead of adding rows)."""
    out = {}
    with db.transaction(con):
        for key, (severity, message) in sorted(rules.items()):
            n = tally.n.get(key, 0)
            if not n:
                continue
            out[key] = n
            dq(con, run_id, "tam_import|%s" % key, key, severity, message, "tam_import", key,
               {"count": n, "sample_source_ids": tally.ids.get(key, [])})
    return out


# ------------------------------------------------------------------------------------------------------ stage 1: verbatim
def _checksum_sql(table: str, columns: Sequence[str]) -> str:
    parts = " + ".join("COALESCE(LENGTH(CAST(%s AS BLOB)), 0)" % _qi(c) for c in columns)
    return "SELECT COUNT(*), COALESCE(SUM(%s), 0) FROM %s" % (parts, _qi(table))


def _state_get(con: sqlite3.Connection, table: str) -> Optional[sqlite3.Row]:
    return con.execute("SELECT cursor_value, last_status, rows_synced FROM ops_sync_state WHERE source_system = 'other' AND scope = 'tam' "
                       "AND object_type = ? AND cursor_name = 'verbatim_sha256'", (table,)).fetchone()


def _state_set(con: sqlite3.Connection, table: str, status: str, cursor: Optional[str], rows: int, run_id: int, error: Optional[str] = None) -> None:
    now = _now()
    with db.transaction(con):
        con.execute(
            "INSERT INTO ops_sync_state (source_system, scope, object_type, cursor_name, cursor_value, last_attempt_at, last_success_at, last_status, last_error, rows_synced, import_run_id, updated_at) "
            "VALUES ('other', 'tam', ?, 'verbatim_sha256', ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(source_system, scope, object_type, cursor_name) DO UPDATE SET cursor_value = excluded.cursor_value, last_attempt_at = excluded.last_attempt_at, "
            "last_success_at = COALESCE(excluded.last_success_at, last_success_at), last_status = excluded.last_status, last_error = excluded.last_error, "
            "rows_synced = excluded.rows_synced, import_run_id = excluded.import_run_id, updated_at = excluded.updated_at",
            (table, cursor, now, now if status == "ok" else None, status, error, rows, run_id, now))


def _table_pairs(con: sqlite3.Connection, src: sqlite3.Connection) -> Dict[str, Dict[str, Any]]:
    """Pre-flight: every source table has its legacy_tam_ twin and the column lists agree (source columns + _import_run_id)."""
    stables = source_tables(src)
    dest_have = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'legacy\\_tam\\_%' ESCAPE '\\'")}
    unknown = sorted(set(stables) - set(LOAD_ORDER))
    if unknown:
        raise ImportErrorTam("source has table(s) this importer does not know: %s" % unknown)
    plan = {}
    for t in LOAD_ORDER:
        if t not in stables:
            raise ImportErrorTam("source table missing: %s" % t)
        if PREFIX + t not in dest_have:
            raise ImportErrorTam("target table missing: %s%s (migration 0006)" % (PREFIX, t))
        scols = [c[0] for c in stables[t]]
        dcols = [r[1] for r in con.execute("PRAGMA table_info(%s)" % _qi(PREFIX + t))]
        if dcols != scols + ["_import_run_id"]:
            raise ImportErrorTam("column mismatch for %s: source %s vs target %s" % (t, scols, dcols))
        plan[t] = {"columns": scols, "alias": _rowid_alias(stables[t])}
    return plan


def load_table(con: sqlite3.Connection, src: sqlite3.Connection, table: str, columns: List[str], alias: bool, run_id: int,
               batch_rows: int = DEFAULT_BATCH_ROWS) -> int:
    """Stream one table; returns rows written.  Tables without a rowid alias (composite / 'INT' keys) get their source rowid copied explicitly."""
    dest = PREFIX + table
    sel = ", ".join(_qi(c) for c in columns)
    ins_cols = ", ".join(_qi(c) for c in columns) + ", _import_run_id"
    if not alias:
        sel = "rowid, " + sel
        ins_cols = "rowid, " + ins_cols
    marks = ",".join("?" * (len(columns) + (0 if alias else 1) + 1))
    insert = "INSERT INTO %s (%s) VALUES (%s)" % (_qi(dest), ins_cols, marks)
    total = int(src.execute("SELECT COUNT(*) FROM %s" % _qi(table)).fetchone()[0])
    cur = src.execute("SELECT %s FROM %s" % (sel, _qi(table)))
    written = 0
    done = False
    t0 = time.time()
    while not done:
        with db.transaction(con):
            n = 0
            nbytes = 0
            while n < batch_rows and nbytes < BATCH_BYTES:
                rows = cur.fetchmany(FETCH_ROWS)
                if not rows:
                    done = True
                    break
                nbytes += sum(len(v) for r in rows for v in r if isinstance(v, (str, bytes)))
                con.executemany(insert, [r + (run_id,) for r in rows])
                n += len(rows)
            written += n
        if n:
            log.info("verbatim %-18s %9d / %d rows (%.0fs)", table, written, total, time.time() - t0)
    return written


def verbatim_stage(con: sqlite3.Connection, src: sqlite3.Connection, run_id: int, fingerprint: str, batch_rows: int = DEFAULT_BATCH_ROWS,
                   reload: bool = False, tables: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    plan = _table_pairs(con, src)
    report = {"tables": {}, "loaded": 0, "skipped_tables": 0, "fk_orphans": 0}  # type: Dict[str, Any]
    wanted = [t for t in LOAD_ORDER if tables is None or t in tables]
    con.execute("PRAGMA foreign_keys = OFF")          # outside any transaction (the pragma is a no-op inside one)
    try:
        for t in wanted:
            p = plan[t]
            src_n, src_sum = src.execute(_checksum_sql(t, p["columns"])).fetchone()
            st = _state_get(con, t)
            dest_n = int(con.execute("SELECT COUNT(*) FROM %s" % _qi(PREFIX + t)).fetchone()[0])
            entry = {"source_rows": src_n, "loaded_rows": dest_n, "action": ""}
            if not reload and st is not None and st["last_status"] == "ok" and st["cursor_value"] == fingerprint and dest_n == src_n:
                entry["action"] = "skipped (complete for this source sha256)"
                report["skipped_tables"] += 1
                report["tables"][t] = entry
                log.info("verbatim %-18s complete for this source - skipped (%d rows)", t, dest_n)
                continue
            _state_set(con, t, "running", None, 0, run_id)
            try:
                if dest_n:                                   # unfinished / forced: delete and reload
                    with db.transaction(con):
                        con.execute("DELETE FROM %s" % _qi(PREFIX + t))
                written = load_table(con, src, t, p["columns"], p["alias"], run_id, batch_rows)
                got_n, got_sum = con.execute(_checksum_sql(PREFIX + t, p["columns"])).fetchone()
                if got_n != src_n or got_sum != src_sum:
                    raise ImportErrorTam("verification failed for %s: rows %s vs %s, byte checksum %s vs %s" % (t, got_n, src_n, got_sum, src_sum))
            except BaseException as exc:
                _state_set(con, t, "error", None, 0, run_id, "%s: %s" % (type(exc).__name__, str(exc)[:300]))
                raise
            _state_set(con, t, "ok", fingerprint, written, run_id)
            entry.update({"loaded_rows": written, "action": "loaded", "verified": True})
            report["loaded"] += written
            report["tables"][t] = entry
        report["fk_orphans"] = _fk_orphans_to_dq(con, run_id, wanted)
    finally:
        con.execute("PRAGMA foreign_keys = ON")
    return report


def _fk_orphans_to_dq(con: sqlite3.Connection, run_id: int, tables: Sequence[str]) -> int:
    total = 0
    cap = 1000
    for t in tables:
        rows = con.execute("PRAGMA foreign_key_check(%s)" % _qi(PREFIX + t)).fetchall()
        total += len(rows)
        if not rows:
            continue
        with db.transaction(con):
            for r in rows[:cap]:
                dq(con, run_id, "legacy_fk_orphan|%s|%s|%s" % (r[0], r[1], r[3]), "legacy_fk_orphan", "warn",
                   "legacy_tam row references a missing parent (loaded anyway, never dropped)", r[0], "%s rowid %s" % (r[0], r[1]),
                   {"parent": r[2], "fk_index": r[3]})
            if len(rows) > cap:
                dq(con, run_id, "legacy_fk_orphan|%s|overflow" % (PREFIX + t), "legacy_fk_orphan", "warn", "more orphans than recorded individually", PREFIX + t, None,
                   {"orphans": len(rows)})
    return total


# ------------------------------------------------------------------------------------------------------ stage 2: canonical
def _origin(con: sqlite3.Connection, entity_type: str, entity_id: int, table: str, pk: str, method: Optional[str], conf: Optional[float], run_id: int) -> None:
    con.execute("INSERT INTO origin_ref (entity_type, entity_id, source_system, source_table, source_pk, match_method, confidence, import_run_id) "
                "VALUES (?, ?, 'tam', ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING", (entity_type, entity_id, table, pk, method, conf, run_id))


def _origin_map(con: sqlite3.Connection, entity_type: str, table: str) -> Dict[str, int]:
    return {r[0]: int(r[1]) for r in con.execute("SELECT source_pk, entity_id FROM origin_ref WHERE entity_type = ? AND source_system = 'tam' AND source_table = ?",
                                                (entity_type, table))}


def _chunks(cur: sqlite3.Cursor, size: int) -> Iterable[List[tuple]]:
    while True:
        rows = cur.fetchmany(size)
        if not rows:
            return
        yield rows


def _verticals(con: sqlite3.Connection, src: sqlite3.Connection, run_id: int, rep: Dict[str, int]) -> Dict[int, int]:
    """categories -> vertical.  An existing vertical with the same slug, else the same hubspot_segment, is reused (never duplicated)."""
    omap = _origin_map(con, "vertical", "categories")
    out = {}  # tam category id -> vertical_id
    with db.transaction(con):
        for cid, key, name, status, seg, created in src.execute("SELECT id, key, name, status, hubspot_segment, created_at FROM categories ORDER BY id"):
            pk = str(cid)
            if pk in omap:
                out[cid] = omap[pk]
                rep["verticals_existing"] += 1
                continue
            slug = "".join(ch if (ch.isascii() and ch.isalnum()) else "_" for ch in (_s(key) or "").lower()).strip("_") or "category_%d" % cid
            seg = _s(seg)
            row = con.execute("SELECT vertical_id FROM vertical WHERE slug = ?", (slug,)).fetchone()
            method = "slug"
            if row is None and seg:
                row = con.execute("SELECT vertical_id FROM vertical WHERE hubspot_segment = ?", (seg,)).fetchone()
                method = "hubspot_segment"
            if row is None:
                ts = _ts(created) or _now()
                cur = con.execute("INSERT INTO vertical (slug, name, country_code, hubspot_segment, description, is_active, created_at, updated_at) VALUES (?, ?, 'IN', ?, ?, ?, ?, ?)",
                                  (slug, _s(name) or slug, seg, "companyOps TAM category (tam.categories.key=%s)" % key, 1 if status == "active" else 0, ts, ts))
                vid = int(cur.lastrowid)
                method = "new"
                rep["verticals_created"] += 1
            else:
                vid = int(row[0])
                rep["verticals_linked_existing"] += 1
            _origin(con, "vertical", vid, "categories", pk, method, 1.0, run_id)
            out[cid] = vid
    return out


def _identifiers(r: Dict[str, Any], tally: Tally) -> List[Dict[str, Any]]:
    """Normalised identifier candidates of one tam company row (strong first, in STRONG_PRIORITY order, then weak alt domains)."""
    cid = r["id"]
    out = []
    conf = r["domain_confidence"]
    conf = float(conf) if isinstance(conf, (int, float)) and 0 <= conf <= 1 else 1.0
    if _s(r["root_domain"]):
        nd = norm.norm_domain(r["root_domain"])
        if nd is None:
            tally.add("tam_domain_unnormalisable", cid)
        else:
            out.append({"type": "root_domain", "norm": nd, "raw": r["root_domain"], "conf": conf, "strong": 1})
    if _s(r["linkedin_url"]):
        nl = norm.norm_linkedin_company(r["linkedin_url"])
        if nl is None:
            tally.add("tam_linkedin_unnormalisable", cid)
        else:
            out.append({"type": "linkedin_company", "norm": nl, "raw": r["linkedin_url"], "conf": 1.0, "strong": 1})
    if _s(r["google_place_id"]):
        out.append({"type": "google_place_id", "norm": _s(r["google_place_id"]), "raw": r["google_place_id"], "conf": 1.0, "strong": 1})
    if _s(r["apollo_org_id"]):
        out.append({"type": "apollo_org", "norm": _s(r["apollo_org_id"]), "raw": r["apollo_org_id"], "conf": 1.0, "strong": 1})
    if _s(r["cin"]):
        cin = norm.norm_cin(r["cin"])
        llpin = None if cin else norm.norm_llpin(r["cin"])
        if cin:
            out.append({"type": "cin", "norm": cin, "raw": r["cin"], "conf": 1.0, "strong": 1})
        elif llpin:
            out.append({"type": "llpin", "norm": llpin, "raw": r["cin"], "conf": 1.0, "strong": 1})
        else:
            tally.add("tam_cin_unnormalisable", cid)
    alt = _s(r["alt_domains"])
    if alt:
        parsed = _maybe_json(alt)
        parts = parsed if isinstance(parsed, list) else [p for p in alt.replace(";", ",").split(",")]
        for p in parts:
            nd = norm.norm_domain(p)
            if nd and not any(i["norm"] == nd and i["type"] == "root_domain" for i in out):
                out.append({"type": "root_domain", "norm": nd, "raw": str(p), "conf": 0.5, "strong": 0})
    return out


COMPANY_COLS = ("id", "root_domain", "linkedin_url", "name", "name_norm", "alt_domains", "hq_city", "hq_state", "hq_country", "india_hq", "employee_count",
                "headcount_band", "founded_year", "funding_stage", "total_funding_usd", "office_phone", "google_place_id", "apollo_org_id", "final_url",
                "first_seen_at", "last_seen_at", "legal_name", "cin", "domain_confidence", "merged_into")


def _company_values(r: Dict[str, Any], tally: Tally, run_started: str) -> Dict[str, Any]:
    cid = r["id"]
    name = _s(r["name"])
    if name is None:
        name = _s(r["name_norm"]) or "tam company %d" % cid
        tally.add("tam_company_without_name", cid)
    name_norm = norm.norm_company_name(r["name_norm"]) or norm.norm_company_name(name) or "tam company %d" % cid
    country = None
    if _s(r["hq_country"]):
        country = norm.norm_country(r["hq_country"])
        if country is None:
            tally.add("tam_country_unmapped", cid)
    ec = r["employee_count"]
    fy = r["founded_year"]
    tf = r["total_funding_usd"]
    first = _ts(r["first_seen_at"]) or run_started
    return {
        "canonical_name": name, "name_norm": name_norm, "legal_name": _s(r["legal_name"]), "website": _s(r["final_url"]),
        "hq_city": _s(r["hq_city"]), "hq_state": _s(r["hq_state"]), "hq_country": country,
        "india_hq": r["india_hq"] if r["india_hq"] in ("yes", "no", "unknown") else "unknown",
        "employee_count": int(ec) if isinstance(ec, (int, float)) and ec >= 0 else None,
        "headcount_band": _s(r["headcount_band"]),
        "founded_year": int(fy) if isinstance(fy, (int, float)) and 1800 <= fy <= 2100 else None,
        "funding_stage": _s(r["funding_stage"]),
        "total_funding_usd": float(tf) if isinstance(tf, (int, float)) and tf >= 0 else None,
        "first_seen_at": first, "created_at": first, "updated_at": _ts(r["last_seen_at"]) or first,
    }


def _companies(con: sqlite3.Connection, src: sqlite3.Connection, run_id: int, rep: Dict[str, int], tally: Tally, batch: int = 5000) -> Dict[str, int]:
    """companies -> company + company_identifier (+ origin_ref).  Returns {tam company id (str) -> golden company_id}."""
    omap = _origin_map(con, "company", "companies")
    strong = {(r[0], r[1]): int(r[2]) for r in con.execute("SELECT identifier_type, value_norm, company_id FROM company_identifier WHERE is_strong = 1")}
    started = _now()
    created_here = set()  # type: Set[int]
    cur = src.execute("SELECT %s FROM companies ORDER BY id" % ", ".join(COMPANY_COLS))
    done = 0
    for rows in _chunks(cur, batch):
        with db.transaction(con):
            for tup in rows:
                r = dict(zip(COMPANY_COLS, tup))
                pk = str(r["id"])
                if pk in omap:
                    rep["companies_already_imported"] += 1
                    continue
                idents = _identifiers(r, tally)
                golden = None
                method = "new"
                conf = 1.0
                for ident in idents:
                    if ident["strong"] and (ident["type"], ident["norm"]) in strong:
                        golden = strong[(ident["type"], ident["norm"])]
                        method, conf = ident["type"], ident["conf"]
                        break
                created = golden is None
                if created:
                    v = _company_values(r, tally, started)
                    cur2 = con.execute(
                        "INSERT INTO company (canonical_name, name_norm, legal_name, website, hq_city, hq_state, hq_country, india_hq, employee_count, headcount_band, "
                        "founded_year, funding_stage, total_funding_usd, status, first_seen_at, created_at, updated_at) "
                        "VALUES (:canonical_name, :name_norm, :legal_name, :website, :hq_city, :hq_state, :hq_country, :india_hq, :employee_count, :headcount_band, "
                        ":founded_year, :funding_stage, :total_funding_usd, 'unknown', :first_seen_at, :created_at, :updated_at)", v)
                    golden = int(cur2.lastrowid)
                    created_here.add(golden)
                    rep["companies_created"] += 1
                    if not any(i["strong"] for i in idents) and not r["merged_into"]:
                        tally.add("tam_company_without_strong_identifier", r["id"])
                else:
                    if golden in created_here:
                        rep["companies_folded_into_earlier_tam_row"] += 1
                        tally.add("tam_rows_share_strong_identifier", r["id"])
                    else:
                        rep["companies_linked_to_existing"] += 1
                primary_done = set()  # type: Set[str]
                for ident in idents:
                    key = (ident["type"], ident["norm"])
                    is_strong = ident["strong"]
                    if is_strong:
                        owner = strong.get(key)
                        if owner is not None and owner != golden:
                            is_strong = 0                             # another golden company owns this strong key: keep as weak evidence + queue a human merge
                            a, b = sorted((golden, owner))
                            con.execute("INSERT INTO merge_candidate (entity_type, left_id, right_id, match_kind, score, evidence_json, import_run_id) "
                                        "VALUES ('company', ?, ?, 'strong_identifier_conflict', 0.9, ?, ?) ON CONFLICT DO NOTHING",
                                        (a, b, _jdump({"identifier_type": ident["type"], "tam_company_id": r["id"]}), run_id))
                            tally.add("tam_identifier_conflict", r["id"])
                    primary = 1 if (is_strong and created and ident["type"] not in primary_done) else 0
                    if is_strong and primary:
                        primary_done.add(ident["type"])
                    cur3 = con.execute(
                        "INSERT INTO company_identifier (company_id, identifier_type, value_norm, value_raw, confidence, is_strong, is_primary, source_system, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, 'tam', ?, ?) ON CONFLICT(company_id, identifier_type, value_norm) DO NOTHING",
                        (golden, ident["type"], ident["norm"], ident["raw"], ident["conf"], is_strong, primary, started, started))
                    if cur3.rowcount:
                        iid = int(cur3.lastrowid)
                        rep["identifiers_created"] += 1
                        rep["identifiers_created_" + ident["type"] + ("" if is_strong else "_weak")] += 1
                    else:
                        iid = int(con.execute("SELECT identifier_id FROM company_identifier WHERE company_id = ? AND identifier_type = ? AND value_norm = ?",
                                              (golden, ident["type"], ident["norm"])).fetchone()[0])
                    if is_strong and key not in strong:
                        strong[key] = golden
                    _origin(con, "company_identifier", iid, "companies", pk, ident["type"], ident["conf"], run_id)
                _origin(con, "company", golden, "companies", pk, method, conf, run_id)
                omap[pk] = golden
                if _s(r["office_phone"]):
                    tally.add("tam_office_phone_not_imported", r["id"])
        done += len(rows)
        log.info("canonical companies: %d processed (%d created, %d linked to existing, %d folded)", done, rep["companies_created"],
                 rep["companies_linked_to_existing"], rep["companies_folded_into_earlier_tam_row"])
    return omap


def _merge_pointers(con: sqlite3.Connection, src: sqlite3.Connection, golden: Dict[str, int], rep: Dict[str, int], tally: Tally, run_id: int) -> None:
    """tam companies.merged_into -> company.merged_into_company_id (tombstones are kept; chains are followed by v_company_survivor)."""
    started = _now()
    rows = src.execute("SELECT id, merged_into, last_seen_at, first_seen_at FROM companies WHERE merged_into IS NOT NULL ORDER BY id").fetchall()
    already = {int(r[0]) for r in con.execute("SELECT company_id FROM company WHERE merged_into_company_id IS NOT NULL")}
    for i in range(0, len(rows), 5000):
        with db.transaction(con):
            for tid, parent, last_seen, first_seen in rows[i:i + 5000]:
                child = golden.get(str(tid))
                par = golden.get(str(parent))
                if child is None or par is None:
                    tally.add("tam_merge_target_missing", tid)
                    continue
                if child in already:
                    rep["merge_pointers_already_set"] += 1
                    continue
                if child == par:
                    tally.add("tam_merge_same_golden", tid)
                    continue
                shared = con.execute("SELECT 1 FROM origin_ref WHERE entity_type = 'company' AND entity_id = ? AND NOT (source_system = 'tam' AND source_table = 'companies' "
                                     "AND source_pk = ?) LIMIT 1", (child, str(tid))).fetchone()
                if shared or con.execute("SELECT 1 FROM company_identifier WHERE company_id = ? LIMIT 1", (child,)).fetchone():
                    tally.add("tam_merge_source_has_identity", tid)       # the tombstone's golden company carries identity of its own: do not tombstone it
                    continue
                try:
                    con.execute("UPDATE company SET merged_into_company_id = ?, merged_at = ?, updated_at = ? WHERE company_id = ?",
                                (par, _ts(last_seen) or _ts(first_seen) or started, started, child))
                except sqlite3.IntegrityError:
                    tally.add("tam_merge_cycle", tid)
                    continue
                already.add(child)
                rep["merge_pointers_set"] += 1
    log.info("canonical merge pointers: %d set, %d already set", rep["merge_pointers_set"], rep["merge_pointers_already_set"])


def _verdict_details(r: Dict[str, Any]) -> Dict[str, Any]:
    cls = {}
    for k in ("keyword_score", "keyword_hits", "segments", "primary_segment", "cl_bucket", "has_own_tech_product", "cl_india_hq", "company_type", "pm_tooling_signals", "notes"):
        v = r[k]
        if v is not None and v != "":
            cls[k.replace("cl_", "classification_")] = _maybe_json(v)
    d = {}  # type: Dict[str, Any]
    for k in ("funnel_stage", "funnel_bucket", "funnel_updated_at", "regulated_status", "regulatory_sensitivity", "parked_as"):
        if r[k] not in (None, ""):
            d[k] = r[k]
    fr = _maybe_json(r["funnel_reason"]) if r["funnel_reason"] else None
    if isinstance(fr, dict):
        d["score_components"] = fr
    if cls:
        d["classification"] = cls
    d["tam"] = {"company_id": r["company_id"], "category_id": r["category_id"], "first_seen_at": r["first_seen_at"]}
    return d


VERDICT_SQL = """
SELECT cc.company_id, cc.category_id, cc.segment, cc.icp_bucket, cc.confidence, cc.score, cc.priority_tier, cc.first_seen_at, cc.updated_at,
       cc.regulated_status, cc.regulatory_sensitivity, cc.parked_as, cc.funnel_stage, cc.funnel_bucket, cc.funnel_reason, cc.funnel_updated_at,
       cl.keyword_score, cl.keyword_hits, cl.segments, cl.primary_segment, cl.icp_bucket AS cl_bucket, cl.confidence AS cl_confidence,
       cl.has_own_tech_product, cl.india_hq AS cl_india_hq, cl.company_type, cl.evidence_quote, cl.evidence_url, cl.pm_tooling_signals, cl.notes,
       cl.model, cl.prompt_version, cl.classified_at
  FROM company_categories cc
  LEFT JOIN classifications cl ON cl.company_id = cc.company_id AND cl.category_id = cc.category_id
 ORDER BY cc.company_id, cc.category_id"""


def _verdicts(con: sqlite3.Connection, src: sqlite3.Connection, golden: Dict[str, int], vertical: Dict[int, int], run_id: int, rep: Dict[str, int],
              tally: Tally, batch: int = 5000) -> None:
    """company_categories (+ classifications) -> tam_company_verdict.  Every bucket (Out / Maybe / Fit / none) is kept: rejects are never dropped."""
    done_pk = set(_origin_map(con, "tam_company_verdict", "company_categories"))
    current = {(int(r[0]), int(r[1])) for r in con.execute("SELECT company_id, COALESCE(vertical_id, 0) FROM tam_company_verdict WHERE is_current = 1")}
    started = _now()
    cur = src.execute(VERDICT_SQL)
    cols = [d[0] for d in cur.description]
    n = 0
    for rows in _chunks(cur, batch):
        with db.transaction(con):
            for tup in rows:
                r = dict(zip(cols, tup))
                pk = "%s|%s" % (r["company_id"], r["category_id"])
                if pk in done_pk:
                    rep["verdicts_already_imported"] += 1
                    continue
                g = golden.get(str(r["company_id"]))
                if g is None:
                    tally.add("tam_verdict_company_missing", pk)
                    continue
                vid = vertical.get(r["category_id"])
                native = _s(r["icp_bucket"])
                bucket = BUCKET.get(native, "unscored") if native else "unscored"
                if native and native not in BUCKET:
                    tally.add("tam_unknown_bucket", pk)
                conf = r["confidence"] if r["confidence"] is not None else r["cl_confidence"]
                if conf is not None and not (isinstance(conf, (int, float)) and 0 <= conf <= 1):
                    tally.add("tam_confidence_out_of_range", pk)
                    conf = None
                fr = _s(r["funnel_reason"])
                reason = fr if (fr and not fr.startswith("{")) else _s(r["notes"])
                method = "llm" if r["cl_bucket"] else "rules"
                decided = _ts(r["classified_at"]) or _ts(r["updated_at"]) or _ts(r["first_seen_at"]) or started
                key = (g, vid or 0)
                is_current = 0 if key in current else 1
                if not is_current:
                    tally.add("tam_verdict_not_current_duplicate_key", pk)
                cur2 = con.execute(
                    "INSERT INTO tam_company_verdict (company_id, vertical_id, segment, icp_bucket, priority_tier, score, confidence, reason_code, reason, evidence_url, "
                    "evidence_quote, native_bucket, details_json, verdict_method, model, prompt_version, is_placeholder, is_current, decided_at, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)",
                    (g, vid, _s(r["segment"]) or _s(r["primary_segment"]), bucket, _s(r["priority_tier"]), r["score"], conf, _s(r["funnel_bucket"]), reason,
                     _s(r["evidence_url"]), _s(r["evidence_quote"]), native, _jdump(_verdict_details(r)), method, _s(r["model"]), _s(r["prompt_version"]),
                     is_current, decided, _ts(r["first_seen_at"]) or decided, decided))
                current.add(key)
                _origin(con, "tam_company_verdict", int(cur2.lastrowid), "company_categories", pk, "company_category", 1.0, run_id)
                rep["verdicts_created"] += 1
                rep["verdicts_" + bucket] += 1
        n += len(rows)
        log.info("canonical verdicts: %d processed (%d created)", n, rep["verdicts_created"])


def _signals(con: sqlite3.Connection, src: sqlite3.Connection, golden: Dict[str, int], run_id: int, rep: Dict[str, int], tally: Tally, batch: int = 5000) -> None:
    sel = "SELECT company_id, updated_at, %s FROM signals ORDER BY company_id" % ", ".join(c for c, _ in SIGNAL_COLUMNS)
    cur = src.execute(sel)
    for rows in _chunks(cur, batch):
        with db.transaction(con):
            for tup in rows:
                g = golden.get(str(tup[0]))
                if g is None:
                    tally.add("tam_signal_company_missing", tup[0])
                    continue
                observed = _ts(tup[1])
                for (code, kind), v in zip(SIGNAL_COLUMNS, tup[2:]):
                    if v is None or (isinstance(v, str) and not v.strip()):
                        continue
                    b = n = t = None
                    if kind == "bool":
                        b = 1 if v else 0
                    elif kind == "num":
                        n = float(v)
                    else:
                        t = str(v).strip()
                    cur2 = con.execute("INSERT INTO company_signal (company_id, signal_code, value_bool, value_num, value_text, observed_at, source_system, import_run_id) "
                                       "VALUES (?, ?, ?, ?, ?, ?, 'tam', ?) ON CONFLICT DO NOTHING", (g, code, b, n, t, observed, run_id))
                    rep["signals_created" if cur2.rowcount else "signals_already_imported"] += 1


def _costs(con: sqlite3.Connection, src: sqlite3.Connection, vertical: Dict[int, int], run_id: int, rep: Dict[str, int], tally: Tally) -> None:
    done_pk = set(_origin_map(con, "cost_ledger", "cost_ledger"))
    started = _now()
    rows = src.execute("SELECT id, run_id, category_id, vendor, unit, qty, usd_est, note, at FROM cost_ledger ORDER BY id").fetchall()
    for i in range(0, len(rows), 5000):
        with db.transaction(con):
            for cid, run, cat, vendor, unit, qty, usd_est, note, at in rows[i:i + 5000]:
                pk = str(cid)
                if pk in done_pk:
                    rep["costs_already_imported"] += 1
                    continue
                vendor = (_s(vendor) or "").lower()
                unit = _s(unit)
                if not vendor or not unit or not isinstance(qty, (int, float)) or qty < 0:
                    tally.add("tam_cost_row_rejected", cid)
                    continue
                per = CREDITS_PER_CALL.get(unit)
                credits = float(qty)
                calls = credits / per if per else float(qty)
                usd = float(usd_est) if isinstance(usd_est, (int, float)) and usd_est > 0 else None   # 0 / NULL = unknown, never a real zero price
                occurred = _ts(at)
                if occurred is None:
                    tally.add("tam_cost_bad_timestamp", cid)
                    occurred = started
                cur = con.execute(
                    "INSERT INTO cost_ledger (vendor, unit, qty, credits, usd, usd_basis, occurred_at, vertical_id, run_ref, note, is_placeholder, source_system, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'tam', ?)",
                    (vendor, unit, calls, credits, usd, "estimated" if usd is not None else "unknown", occurred, vertical.get(cat),
                     "stage_run:%s" % run if run is not None else None, _s(note), occurred))
                _origin(con, "cost_ledger", int(cur.lastrowid), "cost_ledger", pk, "cost_row", 1.0, run_id)
                rep["costs_created"] += 1
                if usd is None:
                    rep["costs_usd_unknown"] += 1


def _suppressions(con: sqlite3.Connection, src: sqlite3.Connection, golden: Dict[str, int], run_id: int, rep: Dict[str, int], tally: Tally) -> None:
    """crm_pushes statuses -> suppression (delivered / off_icp / too_big / duplicate), pinned to the COMPANYOPS account."""
    done_pk = set(_origin_map(con, "suppression", "crm_pushes"))
    names = {r[0]: (r[1], r[2], r[3]) for r in src.execute("SELECT id, root_domain, linkedin_url, name_norm FROM companies")}
    rows = src.execute("SELECT id, company_id, status, note, pushed_at, deal_id FROM crm_pushes ORDER BY id").fetchall()
    with db.transaction(con):
        for pid, cid, status, note, pushed_at, deal_id in rows:
            kind = PUSH_SUPPRESSION.get(status)
            if kind is None:
                rep["push_rows_without_suppression_kind"] += 1
                continue
            pk = str(pid)
            if pk in done_pk:
                rep["suppressions_already_imported"] += 1
                continue
            mt = mv = None
            if cid is not None and cid in names:
                dom, li, nn = names[cid]
                if _s(dom) and norm.norm_domain(dom):
                    mt, mv = "domain", norm.norm_domain(dom)
                elif _s(li) and norm.norm_linkedin_company(li):
                    mt, mv = "linkedin_company", norm.norm_linkedin_company(li)
                elif norm.norm_company_name(nn):
                    mt, mv = "company_name", norm.norm_company_name(nn)
            elif _s(note) and _s(note) != "None" and norm.norm_company_name(note):
                mt, mv = "company_name", norm.norm_company_name(note)     # older back-filled pushes carry only the company name
            if mt is None:
                tally.add("tam_push_not_suppressible", pid)
                continue
            added = _ts(pushed_at) or _now()
            g = golden.get(str(cid)) if cid is not None else None
            fresh = con.execute("INSERT INTO suppression (kind, match_type, match_value_norm, company_id, account_id, reason, list_name, added_at) VALUES (?, ?, ?, ?, ?, ?, 'tam.crm_pushes', ?) "
                                "ON CONFLICT DO NOTHING", (kind, mt, mv, g, ACCOUNT, "tam crm_pushes status=%s" % status, added)).rowcount
            sid = int(con.execute("SELECT suppression_id FROM suppression WHERE kind = ? AND match_type = ? AND match_value_norm = ? AND COALESCE(account_id, '') = ?",
                                  (kind, mt, mv, ACCOUNT)).fetchone()[0])
            _origin(con, "suppression", sid, "crm_pushes", pk, "crm_push_status", 1.0, run_id)
            rep["suppressions_created"] += fresh
            rep["suppressions_%s" % kind] += fresh
            rep["suppression_origin_refs"] += 1


CANONICAL_DQ_RULES = {
    "tam_domain_unnormalisable": ("warn", "tam root_domain cannot be normalised; the company was imported without that identifier (raw value stays in legacy_tam_companies)"),
    "tam_linkedin_unnormalisable": ("warn", "tam linkedin_url is not a company URL (person / empty slug); company imported without that identifier"),
    "tam_cin_unnormalisable": ("warn", "tam cin is neither a CIN nor an LLPIN"),
    "tam_country_unmapped": ("warn", "tam hq_country not in leadgen.norm country table; hq_country left NULL"),
    "tam_company_without_name": ("warn", "tam company with an empty name"),
    "tam_company_without_strong_identifier": ("info", "tam company (not merged away) has no domain / LinkedIn / place id / apollo id: name-only, needs resolution"),
    "tam_rows_share_strong_identifier": ("info", "several tam rows normalise to the same strong identifier (e.g. quoted / zero-width spelling of one domain): imported as ONE golden company"),
    "tam_identifier_conflict": ("warn", "a tam strong identifier is owned by a different golden company than the rest of the row: kept as weak evidence, merge_candidate queued"),
    "tam_merge_target_missing": ("warn", "tam merged_into target has no golden company"),
    "tam_merge_same_golden": ("info", "tam tombstone and its survivor are the same golden company: no pointer needed"),
    "tam_merge_source_has_identity": ("warn", "tam tombstone's golden company carries identifiers / other origins: not tombstoned"),
    "tam_merge_cycle": ("warn", "merge pointer would create a cycle in company.merged_into_company_id: skipped"),
    "tam_office_phone_not_imported": ("info", "tam office_phone present but company_phone import is not part of this importer (value kept in legacy_tam_companies)"),
    "tam_verdict_company_missing": ("error", "company_categories row whose company has no golden company"),
    "tam_unknown_bucket": ("warn", "company_categories.icp_bucket outside Fit/Maybe/Out: imported as unscored"),
    "tam_confidence_out_of_range": ("warn", "tam confidence outside 0..1: NULL in the verdict"),
    "tam_verdict_not_current_duplicate_key": ("info", "second tam verdict for the same (golden company, vertical): kept with is_current = 0"),
    "tam_signal_company_missing": ("error", "signals row whose company has no golden company"),
    "tam_cost_row_rejected": ("warn", "cost_ledger row without vendor / unit / non-negative qty: not imported (kept in legacy_tam_cost_ledger)"),
    "tam_cost_bad_timestamp": ("warn", "cost_ledger.at not parseable: import time used"),
    "tam_push_not_suppressible": ("warn", "crm_push with a suppressing status but no domain / LinkedIn / name to match on"),
}


def canonical_stage(con: sqlite3.Connection, src: sqlite3.Connection, run_id: int) -> Dict[str, Any]:
    rep = _Counter()
    tally = Tally()
    vertical = _verticals(con, src, run_id, rep)
    golden = _companies(con, src, run_id, rep, tally)
    _merge_pointers(con, src, golden, rep, tally, run_id)
    _verdicts(con, src, golden, vertical, run_id, rep, tally)
    _signals(con, src, golden, run_id, rep, tally)
    _costs(con, src, vertical, run_id, rep, tally)
    _suppressions(con, src, golden, run_id, rep, tally)
    orphan_cls = src.execute("SELECT COUNT(*) FROM classifications c WHERE NOT EXISTS (SELECT 1 FROM company_categories cc WHERE cc.company_id = c.company_id "
                             "AND cc.category_id = c.category_id)").fetchone()[0]
    rep["classifications_without_company_category"] = orphan_cls
    rep["dq_rules_fired"] = flush_tally(con, run_id, tally, CANONICAL_DQ_RULES)
    return dict(rep)


class _Counter(dict):
    def __missing__(self, key: str) -> int:
        return 0


# ------------------------------------------------------------------------------------------------------ stage 3: link
def _survivor(con: sqlite3.Connection, company_id: int) -> int:
    cur = company_id
    for _ in range(20):
        row = con.execute("SELECT merged_into_company_id FROM company WHERE company_id = ?", (cur,)).fetchone()
        if row is None or row[0] is None:
            return cur
        cur = int(row[0])
    return cur


def plan_links(con: sqlite3.Connection) -> Dict[str, Any]:
    """Read-only: match legacy_tam_crm_pushes to COMPANYOPS deals.  Used by both the dry run and the real pass."""
    pushes = con.execute("SELECT id, company_id, deal_id, status, pipeline, crm FROM legacy_tam_crm_pushes ORDER BY id").fetchall()
    deals = {r[0]: (int(r[1]), r[2]) for r in con.execute("SELECT hs_deal_id, deal_id, company_id FROM deal WHERE account_id = ?", (ACCOUNT,))}
    golden = _origin_map(con, "company", "companies")
    plan = {"pushes": len(pushes), "matched": [], "unmatched": [], "no_deal_id": [], "other_crm": 0}  # type: Dict[str, Any]
    for pid, cid, deal_id, status, pipeline, crm in pushes:
        if crm not in (None, "hubspot"):
            plan["other_crm"] += 1
            continue
        if deal_id is None or not str(deal_id).strip():
            plan["no_deal_id"].append(pid)
            continue
        try:
            hs = norm.norm_hs_id(deal_id)
        except ValueError:
            plan["no_deal_id"].append(pid)
            continue
        hit = deals.get(hs)
        if hit is None:
            plan["unmatched"].append((pid, hs, status, pipeline))
        else:
            plan["matched"].append((pid, hs, hit[0], hit[1], golden.get(str(cid)) if cid is not None else None))
    return plan


def link_crm_pushes(con: sqlite3.Connection, run_id: Optional[int] = None, dry_run: bool = False) -> Dict[str, Any]:
    """Second pass, safe to re-run any time (e.g. after the HubSpot sync filled ``deal``).

    For every tam push whose deal id exists in ``deal`` (account ``companyops``): origin_ref(deal <- tam.crm_pushes), origin_ref(golden company <-
    tam.crm_pushes) and, when the deal's own company is a different golden company than the tam company, a ``merge_candidate`` (never an auto-merge).
    Pushes whose deal is not (yet) in ``deal`` are logged to ops_dq_issue 'tam_push_unmatched_deal' (never dropped); a later run resolves those issues.
    The deal table is never written (the HubSpot sync owns it)."""
    _require_schema(con, "link")
    plan = plan_links(con)
    rep = _Counter()
    rep.update({"pushes": plan["pushes"], "matched_deals": len(plan["matched"]), "unmatched_deal_ids": len(plan["unmatched"]),
                "pushes_without_deal_id": len(plan["no_deal_id"]), "pushes_other_crm": plan["other_crm"]})
    if dry_run:
        return dict(rep)
    own = run_id is None
    if own:
        run_id = start_run(con, "tam:link", DEFAULT_SOURCE, "legacy_tam_crm_pushes", {"stage": "link"})
    assert run_id is not None
    try:
        with db.transaction(con):
            for pid, hs, deal_pk, deal_company, tam_golden in plan["matched"]:
                pk = str(pid)
                n = con.execute("INSERT INTO origin_ref (entity_type, entity_id, source_system, source_table, source_pk, match_method, confidence, import_run_id) "
                                "VALUES ('deal', ?, 'tam', 'crm_pushes', ?, 'hs_deal_id', 1.0, ?) ON CONFLICT DO NOTHING", (deal_pk, pk, run_id)).rowcount
                rep["deal_origin_refs_created"] += n
                company = tam_golden if tam_golden is not None else deal_company
                if company is None:
                    rep["deal_without_any_company"] += 1
                else:
                    n = con.execute("INSERT INTO origin_ref (entity_type, entity_id, source_system, source_table, source_pk, match_method, confidence, import_run_id) "
                                    "VALUES ('company', ?, 'tam', 'crm_pushes', ?, 'crm_push_deal', 1.0, ?) ON CONFLICT DO NOTHING", (company, pk, run_id)).rowcount
                    rep["company_origin_refs_created"] += n
                if tam_golden is None:
                    rep["push_company_unknown"] += 1
                elif deal_company is None:
                    rep["deal_company_unknown"] += 1
                elif _survivor(con, tam_golden) == _survivor(con, deal_company):
                    rep["company_agrees"] += 1
                else:
                    a, b = sorted((_survivor(con, tam_golden), _survivor(con, deal_company)))
                    n = con.execute("INSERT INTO merge_candidate (entity_type, left_id, right_id, match_kind, score, evidence_json, import_run_id) "
                                    "VALUES ('company', ?, ?, 'tam_crm_push_deal', 0.9, ?, ?) ON CONFLICT DO NOTHING",
                                    (a, b, _jdump({"tam_push_id": pid, "hs_deal_id": hs, "account": ACCOUNT}), run_id)).rowcount
                    rep["company_conflicts_merge_candidates"] += n
                con.execute("UPDATE ops_dq_issue SET status = 'resolved', resolved_at = ? WHERE fingerprint = ? AND status = 'open'",
                            (_now(), "tam_push_unmatched_deal|%s" % pid))
            for pid, hs, status, pipeline in plan["unmatched"]:
                dq(con, run_id, "tam_push_unmatched_deal|%s" % pid, "tam_push_unmatched_deal", "warn",
                   "tam crm_push deal id not found in deal (account companyops); kept in legacy_tam_crm_pushes", "deal", hs,
                   {"tam_push_id": pid, "status": status, "pipeline": pipeline, "account": ACCOUNT})
            for pid in plan["no_deal_id"]:
                dq(con, run_id, "tam_push_no_deal_id|%s" % pid, "tam_push_no_deal_id", "warn", "tam crm_push without a usable HubSpot deal id", "crm_push", str(pid), {})
        if own:
            finish_run(con, run_id, "succeeded", read=plan["pushes"], written=rep["deal_origin_refs_created"] + rep["company_origin_refs_created"],
                       skipped=rep["matched_deals"] - rep["deal_origin_refs_created"], rejected=len(plan["unmatched"]) + len(plan["no_deal_id"]))
            audit(con, run_id, "import.tam.link", dict(rep))
    except BaseException as exc:
        if own:
            finish_run(con, run_id, "failed", error="%s: %s" % (type(exc).__name__, str(exc)[:300]))
        raise
    return dict(rep)


# ------------------------------------------------------------------------------------------------------ orchestration
def run(db_path: Optional[str] = None, source: Optional[str] = None, only: Optional[str] = None, dry_run: bool = False,
        batch_rows: int = DEFAULT_BATCH_ROWS, reload: bool = False) -> Dict[str, Any]:
    """Run the requested stages and return the report dict.  ``only`` in (None, 'verbatim', 'canonical', 'link')."""
    stages = [only] if only else ["verbatim", "canonical", "link"]
    src_path = os.path.abspath(source if source else os.path.join(db.PROJECT_ROOT, DEFAULT_SOURCE))
    report = {"source": _rel(src_path), "dry_run": dry_run, "stages": {}}  # type: Dict[str, Any]
    need_source = any(s in ("verbatim", "canonical") for s in stages)
    src = open_source(src_path) if need_source else None
    try:
        fp = None
        if src is not None:
            fp = file_sha256(src_path)
            report["source_sha256"] = fp
            report["source_schema_sha256_matches_migration_0006"] = (schema_sha(src) == EXPECTED_SCHEMA_SHA)
            if not report["source_schema_sha256_matches_migration_0006"]:
                log.warning("source schema differs from the one migration 0006 was generated from (column check still applies)")
        if dry_run:
            _dry_run(db_path, src, stages, report, batch_rows)
            return report
        con = db.connect(db_path, must_exist=True)
        try:
            for stage in stages:
                _require_schema(con, stage)
                if stage == "link":
                    report["stages"]["link"] = link_crm_pushes(con)
                    continue
                assert src is not None and fp is not None
                run_id = start_run(con, "tam" if stage == "verbatim" else "tam:canonical", src_path, fp, {"stage": stage, "batch_rows": batch_rows, "reload": reload})
                t0 = time.time()
                try:
                    if stage == "verbatim":
                        rep = verbatim_stage(con, src, run_id, fp, batch_rows, reload)
                        written = rep["loaded"]
                        skipped = sum(t["source_rows"] for t in rep["tables"].values() if t["action"].startswith("skipped"))
                        read = sum(t["source_rows"] for t in rep["tables"].values())
                        finish_run(con, run_id, "succeeded", read, written, skipped, 0)
                    else:
                        rep = canonical_stage(con, src, run_id)
                        written = sum(v for k, v in rep.items() if isinstance(v, int) and k.endswith("_created"))
                        finish_run(con, run_id, "succeeded", 0, written, 0, 0)
                except BaseException as exc:
                    finish_run(con, run_id, "failed", error="%s: %s" % (type(exc).__name__, str(exc)[:300]))
                    raise
                rep["import_run_id"] = run_id
                rep["seconds"] = round(time.time() - t0, 1)
                audit(con, run_id, "import.tam.%s" % stage, {k: v for k, v in rep.items() if k != "tables"})
                report["stages"][stage] = rep
        finally:
            con.close()
    finally:
        if src is not None:
            src.close()
    return report


def _dry_run(db_path: Optional[str], src: Optional[sqlite3.Connection], stages: List[str], report: Dict[str, Any], batch_rows: int) -> None:
    """Writes nothing to the target.  verbatim: source counts + what the target already has (read-only).  canonical: the real code runs against a
    throw-away in-memory database with the same schema.  link: the read-only match plan."""
    target = db.db_path(db_path)
    ro = db.connect(db_path, readonly=True) if os.path.exists(target) else None
    try:
        if "verbatim" in stages and src is not None:
            counts = {}
            for t in LOAD_ORDER:
                n = src.execute("SELECT COUNT(*) FROM %s" % _qi(t)).fetchone()[0]
                state = "unknown"
                if ro is not None:
                    have = ro.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (PREFIX + t,)).fetchone()
                    if not have:
                        state = "target table missing (migrate first)"
                    else:
                        dn = ro.execute("SELECT COUNT(*) FROM %s" % _qi(PREFIX + t)).fetchone()[0]
                        st = ro.execute("SELECT cursor_value, last_status FROM ops_sync_state WHERE source_system = 'other' AND scope = 'tam' AND object_type = ? "
                                        "AND cursor_name = 'verbatim_sha256'", (t,)).fetchone()
                        done = st is not None and st[1] == "ok" and st[0] == report.get("source_sha256") and dn == n
                        state = "already complete - would skip" if done else ("would reload (%d rows present)" % dn if dn else "would load")
                counts[t] = {"source_rows": n, "target": state}
            report["stages"]["verbatim"] = {"tables": counts, "total_source_rows": sum(c["source_rows"] for c in counts.values())}
        if "canonical" in stages and src is not None:
            mem = db.connect(":memory:")
            try:
                db.migrate(mem)
                rid = start_run(mem, "tam:canonical", "dry-run", report.get("source_sha256", ""), {"stage": "canonical", "dry_run": True})
                rep = canonical_stage(mem, src, rid)
                rep["note"] = "simulated on an empty in-memory canonical schema; matches against companies already in the target database are not simulated"
                if ro is not None and ro.execute("SELECT 1 FROM sqlite_master WHERE name = 'company_identifier'").fetchone():
                    rep["target_company_identifier_rows_now"] = ro.execute("SELECT COUNT(*) FROM company_identifier").fetchone()[0]
                    rep["target_company_rows_now"] = ro.execute("SELECT COUNT(*) FROM company").fetchone()[0]
                report["stages"]["canonical"] = rep
            finally:
                mem.close()
        if "link" in stages:
            if ro is None:
                report["stages"]["link"] = {"note": "target database does not exist"}
            else:
                _require_schema(ro, "link")
                p = plan_links(ro)
                report["stages"]["link"] = {"pushes": p["pushes"], "matched_deals": len(p["matched"]), "unmatched_deal_ids": len(p["unmatched"]),
                                            "pushes_without_deal_id": len(p["no_deal_id"])}
    finally:
        if ro is not None:
            ro.close()


def format_report(rep: Dict[str, Any]) -> str:
    lines = ["tam importer report%s" % ("  (DRY RUN - nothing written)" if rep.get("dry_run") else ""), "  source: %s" % rep["source"]]
    if "source_sha256" in rep:
        lines.append("  source sha256: %s  schema == migration 0006: %s" % (rep["source_sha256"], rep["source_schema_sha256_matches_migration_0006"]))
    for stage, s in rep["stages"].items():
        lines.append("== %s" % stage)
        if stage == "verbatim":
            for t, e in s["tables"].items():
                lines.append("   %-20s source %9s  loaded %9s  %s" % (t, e["source_rows"], e.get("loaded_rows", "-"), e.get("action", e.get("target", ""))))
            lines.append("   fk orphans reported: %s" % s.get("fk_orphans", "-"))
        else:
            for k, v in sorted(s.items()):
                lines.append("   %-46s %s" % (k, v))
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m leadgen.legacy_import.tam", description="Import legacy companyOps tam.sqlite3 (verbatim + canonical + crm-push linking).")
    ap.add_argument("--db", help="target database (default $LEADGEN_DB or db/leadgen.sqlite)")
    ap.add_argument("--source", help="path of tam.sqlite3 (default %s)" % DEFAULT_SOURCE)
    ap.add_argument("--only", choices=["verbatim", "canonical", "link"], help="run a single stage (default: all three in order)")
    ap.add_argument("--dry-run", action="store_true", help="print counts only; write nothing")
    ap.add_argument("--batch-rows", type=int, default=DEFAULT_BATCH_ROWS, help="verbatim rows per transaction (default %d)" % DEFAULT_BATCH_ROWS)
    ap.add_argument("--reload-verbatim", action="store_true", help="delete and reload tables even when they are complete for this source sha256")
    ap.add_argument("--report-json", help="also write the report as JSON to this path")
    ap.add_argument("-q", "--quiet", action="store_true", help="no progress logging")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING if a.quiet else logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
    try:
        rep = run(a.db, a.source, a.only, a.dry_run, a.batch_rows, a.reload_verbatim)
    except (ImportErrorTam, FileNotFoundError) as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2
    print(format_report(rep))
    if a.report_json:
        with open(a.report_json, "w") as fh:
            json.dump(rep, fh, indent=2, sort_keys=True, default=str)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
