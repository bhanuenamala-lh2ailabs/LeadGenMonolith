"""HubSpot -> hsraw_* sync (READ-ONLY towards HubSpot).

    python -m leadgen.hubspot.sync --account main|companyops|rat|all [--full] [--object deals,contacts,...] [--verify]

What it reads, per account (every call goes through ``leadgen.hubspot.client``, which only allows GET and search / batch-read POSTs):

    account      GET /account-info/v3/details            portal id is checked against config/accounts.yaml (mismatch aborts the account)
    owners       GET /crm/v3/owners (archived false+true)
    pipelines    GET /crm/v3/pipelines/deals             stages with metadata.isClosed / probability
    properties   GET /crm/v3/properties/{deals,contacts,companies}
    deals        list (full) or search hs_lastmodifieddate + batch read (incremental), propertiesWithHistory=dealstage,hubspot_owner_id,
                 pipeline, archived deals too (list ?archived=true)
    contacts / companies   list (full) or search (incremental), archived too
    notes, tasks, calls, meetings, emails   engagement objects; a 403 / missing scope is recorded in ops_dq_issue and skipped
    assoc        v4 association batch read: deal -> contacts / companies (all live deals), engagement -> deals / contacts / companies

Every object is written to ``hsraw_object`` (full API JSON; the trigger archives superseded payloads).  One ``ops_import_run`` row is
written per account per invocation; incremental cursors live in ``ops_sync_state`` (cursor = run start; the next run re-reads
everything modified since cursor - 24 h, history included).  Transactions are short (one page, <= 300 rows) because other
processes may write the same database.

Python 3.9 compatible.
"""
import argparse
import datetime
import hashlib
import json
import sqlite3
import sys
import time
import urllib.parse
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from leadgen import config as lgconfig
from leadgen import db as lgdb
from leadgen.hubspot.client import HubSpotClient, HubSpotError, MissingScope, dumps
from leadgen.norm import to_utc_ms

TOOL_VERSION = "hubspot-sync/1.0"
HISTORY_PROPS = ["dealstage", "hubspot_owner_id", "pipeline"]
KEEP = object()          # "leave the stored associations / history as they are"
BATCH_ROWS = 250         # rows per write transaction

# ---------------------------------------------------------------------------------------------- property selection
STD_PROPS = {
    "deals": ["dealname", "dealstage", "pipeline", "amount", "closedate", "createdate", "hs_lastmodifieddate", "hubspot_owner_id",
              "hs_v2_date_entered_current_stage", "hs_is_closed", "hs_is_closed_won", "deal_currency_code", "dealtype", "description",
              "hs_object_id", "num_associated_contacts"],
    "contacts": ["firstname", "lastname", "email", "phone", "mobilephone", "jobtitle", "company", "associatedcompanyid", "hubspot_owner_id",
                 "hs_linkedin_url", "createdate", "lastmodifieddate", "lifecyclestage", "hs_lead_status", "hs_email_optout", "country",
                 "city", "state", "website", "hs_object_id"],
    "companies": ["name", "domain", "website", "phone", "city", "state", "country", "industry", "numberofemployees", "annualrevenue",
                  "founded_year", "linkedin_company_page", "hubspot_owner_id", "createdate", "hs_lastmodifieddate", "lifecyclestage",
                  "hs_object_id"],
    "notes": ["hs_note_body", "hs_timestamp", "hubspot_owner_id", "hs_createdate", "hs_lastmodifieddate", "hs_attachment_ids", "hs_object_id"],
    "tasks": ["hs_task_subject", "hs_task_body", "hs_task_status", "hs_task_type", "hs_task_priority", "hs_timestamp", "hubspot_owner_id",
              "hs_createdate", "hs_lastmodifieddate", "hs_object_id"],
    "calls": ["hs_call_title", "hs_call_body", "hs_call_direction", "hs_call_disposition", "hs_call_duration", "hs_call_status",
              "hs_timestamp", "hubspot_owner_id", "hs_createdate", "hs_lastmodifieddate", "hs_object_id"],
    "meetings": ["hs_meeting_title", "hs_meeting_body", "hs_meeting_outcome", "hs_meeting_start_time", "hs_meeting_end_time",
                 "hs_timestamp", "hubspot_owner_id", "hs_createdate", "hs_lastmodifieddate", "hs_object_id"],
    "emails": ["hs_email_subject", "hs_email_text", "hs_email_direction", "hs_email_status", "hs_timestamp", "hubspot_owner_id",
               "hs_createdate", "hs_lastmodifieddate", "hs_object_id"],
}
DEFINED_TYPES = ("deals", "contacts", "companies")          # object types whose property definitions we store (and use to pick custom props)
ENGAGEMENTS = ("notes", "tasks", "calls", "meetings", "emails")
LASTMOD_PROP = {"contacts": "lastmodifieddate"}              # everything else: hs_lastmodifieddate
ASSOC_TARGETS = {"notes": ("deals", "contacts", "companies"), "tasks": ("deals", "contacts", "companies"),
                 "calls": ("deals", "contacts", "companies"), "meetings": ("deals", "contacts", "companies"),
                 "emails": ("deals", "contacts", "companies")}
ARCHIVED_TOO = ("deals", "contacts", "companies")
#: names accepted by --object, in execution order
ALL_OBJECTS = ("account", "owners", "pipelines", "properties", "deals", "contacts", "companies",
               "notes", "tasks", "calls", "meetings", "emails", "assoc")


def now_iso() -> str:
    return lgdb.utc_now_iso()


def iso_to_ms(iso: str) -> int:
    dt = datetime.datetime.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc)
    return int(dt.timestamp() * 1000)


def ms_to_iso(ms: int) -> str:
    return datetime.datetime.fromtimestamp(ms / 1000.0, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (ms % 1000)


def payload_hash(properties: Any, associations: Any, history: Any) -> str:
    return hashlib.sha256(dumps({"p": properties, "a": associations, "h": history}).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------------------------- db helpers
class Run:
    """ops_import_run bookkeeping + counters for one account."""

    def __init__(self, con: sqlite3.Connection, account: str, params: Dict[str, Any]):
        self.con = con
        self.account = account
        self.params = params
        self.counts = {}   # type: Dict[str, Dict[str, int]]
        self.errors = []   # type: List[str]
        self.scope_missing = []  # type: List[str]
        with lgdb.transaction(con):
            cur = con.execute(
                "INSERT INTO ops_import_run (kind, source_name, account_id, status, started_at, params_json, tool_version) "
                "VALUES ('hubspot_sync', ?, ?, 'running', ?, ?, ?)",
                ("hubspot:%s" % account, account, now_iso(), dumps(params), TOOL_VERSION))
            self.run_id = cur.lastrowid

    def bump(self, object_type: str, key: str, n: int = 1) -> None:
        c = self.counts.setdefault(object_type, {"read": 0, "inserted": 0, "updated": 0, "unchanged": 0})
        c[key] = c.get(key, 0) + n

    def finish(self, status: str, error: Optional[str] = None) -> None:
        tot = {"read": 0, "inserted": 0, "updated": 0, "unchanged": 0}
        for c in self.counts.values():
            for k in tot:
                tot[k] += c.get(k, 0)
        params = dict(self.params, counts=self.counts, scope_missing=self.scope_missing, errors=self.errors[:20])
        with lgdb.transaction(self.con):
            self.con.execute(
                "UPDATE ops_import_run SET status=?, finished_at=?, rows_read=?, rows_written=?, rows_skipped=?, rows_rejected=?, error=?, "
                "params_json=? WHERE import_run_id=?",
                (status, now_iso(), tot["read"], tot["inserted"] + tot["updated"], tot["unchanged"], len(self.errors),
                 (lgconfig.redact(error)[:2000] if error else None), dumps(params), self.run_id))


def dq(con: sqlite3.Connection, run_id: Optional[int], fingerprint: str, rule_code: str, severity: str, account: Optional[str],
       entity_type: Optional[str], entity_ref: Optional[str], message: str, details: Optional[Dict[str, Any]] = None) -> None:
    """Upsert a data-quality finding (idempotent on fingerprint; occurrences++ and last_seen_at on repeats).  Caller owns the transaction."""
    now = now_iso()
    con.execute(
        "INSERT INTO ops_dq_issue (fingerprint, rule_code, severity, account_id, entity_type, entity_ref, message, details_json, "
        "first_seen_at, last_seen_at, import_run_id) VALUES (?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(fingerprint) DO UPDATE SET occurrences = occurrences + 1, last_seen_at = excluded.last_seen_at, "
        "message = excluded.message, details_json = excluded.details_json, import_run_id = excluded.import_run_id",
        (fingerprint, rule_code, severity, account, entity_type, entity_ref, message[:1000], dumps(details or {}), now, now, run_id))


def get_cursor(con: sqlite3.Connection, account: str, object_type: str) -> Optional[str]:
    r = con.execute("SELECT cursor_value FROM ops_sync_state WHERE source_system='hubspot' AND scope=? AND object_type=? "
                    "AND cursor_name='hs_lastmodifieddate' AND last_status='ok'", (account, object_type)).fetchone()
    return r[0] if r and r[0] else None


def set_cursor(con: sqlite3.Connection, account: str, object_type: str, value: str, rows: int, run_id: int,
               status: str = "ok", error: Optional[str] = None) -> None:
    now = now_iso()
    with lgdb.transaction(con):
        if status == "ok":
            con.execute(
                "INSERT INTO ops_sync_state (source_system, scope, object_type, cursor_name, account_id, cursor_value, last_attempt_at, "
                "last_success_at, last_status, last_error, rows_synced, import_run_id) VALUES ('hubspot',?,?, 'hs_lastmodifieddate', ?,?,?,?, 'ok', NULL, ?, ?) "
                "ON CONFLICT(source_system, scope, object_type, cursor_name) DO UPDATE SET cursor_value=excluded.cursor_value, "
                "last_attempt_at=excluded.last_attempt_at, last_success_at=excluded.last_success_at, last_status='ok', last_error=NULL, "
                "rows_synced=excluded.rows_synced, import_run_id=excluded.import_run_id",
                (account, object_type, account, value, now, now, rows, run_id))
        else:
            con.execute(
                "INSERT INTO ops_sync_state (source_system, scope, object_type, cursor_name, account_id, last_attempt_at, last_status, last_error, import_run_id) "
                "VALUES ('hubspot',?,?, 'hs_lastmodifieddate', ?,?, 'error', ?, ?) "
                "ON CONFLICT(source_system, scope, object_type, cursor_name) DO UPDATE SET last_attempt_at=excluded.last_attempt_at, "
                "last_status='error', last_error=excluded.last_error, import_run_id=excluded.import_run_id",
                (account, object_type, account, now, lgconfig.redact(error or "")[:500], run_id))


def upsert_raw(con: sqlite3.Connection, run: Run, account: str, object_type: str, hs_id: str, properties: Dict[str, Any],
               associations: Any = KEEP, history: Any = KEEP, archived: bool = False, created: Optional[str] = None,
               updated: Optional[str] = None, history_error: Optional[str] = None) -> str:
    """Insert / update one hsraw_object row.  Returns 'inserted' | 'updated' | 'unchanged'.  Caller owns the transaction.
    ``KEEP`` for associations / history preserves what is stored (a list fetch must not wipe the association pass)."""
    row = con.execute("SELECT raw_id, payload_sha256, is_archived, associations_json, history_json, history_fetched_at, history_error "
                      "FROM hsraw_object WHERE account_id=? AND object_type=? AND hs_id=?", (account, object_type, hs_id)).fetchone()
    now = now_iso()
    if associations is KEEP:
        associations = json.loads(row["associations_json"]) if row and row["associations_json"] else None
    if history is KEEP:
        history = json.loads(row["history_json"]) if row and row["history_json"] else None
    sha = payload_hash(properties, associations, history)
    a_json = dumps(associations) if associations is not None else None
    h_json = dumps(history) if history is not None else None
    created_n = to_utc_ms(created) if created else None
    updated_n = to_utc_ms(updated) if updated else None
    if row is None:
        con.execute(
            "INSERT INTO hsraw_object (account_id, object_type, hs_id, properties_json, associations_json, history_json, history_fetched_at, "
            "history_error, is_archived, hs_created_at, hs_updated_at, fetched_at, payload_sha256, import_run_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (account, object_type, hs_id, dumps(properties), a_json, h_json, now if h_json is not None else None, history_error,
             1 if archived else 0, created_n, updated_n, now, sha, run.run_id))
        return "inserted"
    if row["payload_sha256"] == sha and bool(row["is_archived"]) == archived and row["history_error"] == history_error:
        return "unchanged"
    con.execute(
        "UPDATE hsraw_object SET properties_json=?, associations_json=?, history_json=?, history_fetched_at=?, history_error=?, is_archived=?, "
        "hs_created_at=?, hs_updated_at=?, fetched_at=?, payload_sha256=?, import_run_id=? WHERE raw_id=?",
        (dumps(properties), a_json, h_json, (now if h_json is not None else None), history_error, 1 if archived else 0, created_n, updated_n,
         now, sha, run.run_id, row["raw_id"]))
    return "updated"


def set_associations(con: sqlite3.Connection, run: Run, account: str, object_type: str, hs_id: str, assoc: Dict[str, Any]) -> str:
    row = con.execute("SELECT raw_id, payload_sha256, properties_json, history_json, associations_json FROM hsraw_object WHERE account_id=? AND object_type=? AND hs_id=?",
                      (account, object_type, hs_id)).fetchone()
    if row is None:
        return "missing"
    props = json.loads(row["properties_json"])
    hist = json.loads(row["history_json"]) if row["history_json"] else None
    sha = payload_hash(props, assoc, hist)
    if sha == row["payload_sha256"]:
        return "unchanged"
    con.execute("UPDATE hsraw_object SET associations_json=?, payload_sha256=?, fetched_at=?, import_run_id=? WHERE raw_id=?",
                (dumps(assoc), sha, now_iso(), run.run_id, row["raw_id"]))
    if row["associations_json"] is None:
        # the trigger archived the payload as it was BEFORE the first association read - the same object minus a field that was simply not
        # fetched yet.  That is not a real previous version: prune it (retention is an application decision, see 0003_hsraw.sql).
        con.execute("DELETE FROM hsraw_object_revision WHERE raw_id=? AND payload_sha256=?", (row["raw_id"], row["payload_sha256"]))
    return "updated"


def _chunks(seq: List[Any], n: int) -> Iterator[List[Any]]:
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


# ---------------------------------------------------------------------------------------------- the syncer
class AccountSync:
    def __init__(self, account: str, con: sqlite3.Connection, client: Optional[HubSpotClient] = None, full: bool = False,
                 objects: Optional[Iterable[str]] = None, log: Any = None, since_hours: int = 24):
        self.account = account
        self.cfg = lgconfig.get_account(account)
        self.con = con
        self.client = client or HubSpotClient(account)
        self.full = full
        self.objects = [o for o in ALL_OBJECTS if (objects is None or o in set(objects))]
        self.since_hours = since_hours
        self._log = log or (lambda m: print(m, flush=True))
        self.client.log = self.log
        self.run = None  # type: Optional[Run]
        self.defs = {}   # type: Dict[str, List[Dict[str, Any]]]
        self.touched = {}  # type: Dict[str, List[str]]
        self.started_iso = now_iso()

    def log(self, msg: str) -> None:
        self._log("[%s] %s" % (self.account, msg))

    # ------------------------------------------------------------------------------------------------------ driver
    def run_all(self) -> Dict[str, Any]:
        params = {"full": self.full, "objects": self.objects, "since_hours": self.since_hours}
        self.run = Run(self.con, self.account, params)
        status, error = "succeeded", None
        try:
            for ot in self.objects:
                try:
                    getattr(self, "sync_" + ("engagement" if ot in ENGAGEMENTS else ot))(*((ot,) if ot in ENGAGEMENTS else ()))
                except MissingScope as exc:
                    self.run.scope_missing.append(ot)
                    self.log("%s: missing scope, skipped (%s)" % (ot, str(exc)[:120]))
                    with lgdb.transaction(self.con):
                        dq(self.con, self.run.run_id, "hubspot_scope_missing|%s|%s" % (self.account, ot), "hubspot_scope_missing", "warn", self.account,
                           "object_type", ot, "HubSpot key for %s has no read scope for %s (HTTP 403 MISSING_SCOPES); not synced" % (self.account, ot),
                           {"path": exc.path, "status": exc.status, "category": exc.category})
                except HubSpotError as exc:
                    self.run.errors.append("%s: %s" % (ot, exc))
                    self.log("%s: ERROR %s" % (ot, exc))
                    status = "partial"
                    set_cursor(self.con, self.account, ot, "", 0, self.run.run_id, status="error", error=str(exc)) if ot in self._cursor_objects() else None
            self.reconcile()
            if self.run.errors:
                status = "partial"
        except BaseException as exc:                                          # noqa: BLE001 - recorded, then re-raised
            status, error = "failed", "%s: %s" % (type(exc).__name__, exc)
            self.run.finish(status, error)
            raise
        self.run.finish(status, error)
        self.log("done: status=%s calls=%d retries=%d counts=%s" % (status, self.client.calls, self.client.retries, dumps(self.run.counts)))
        return {"status": status, "run_id": self.run.run_id, "counts": self.run.counts, "scope_missing": self.run.scope_missing,
                "errors": self.run.errors}

    @staticmethod
    def _cursor_objects() -> Tuple[str, ...]:
        return ("deals", "contacts", "companies") + ENGAGEMENTS

    # ------------------------------------------------------------------------------------------------------ small objects
    def _store_blob(self, object_type: str, hs_id: str, payload: Dict[str, Any], archived: bool = False,
                    created: Optional[str] = None, updated: Optional[str] = None) -> None:
        with lgdb.transaction(self.con):
            r = upsert_raw(self.con, self.run, self.account, object_type, hs_id, payload, associations=None, history=None, archived=archived,
                           created=created, updated=updated)
        self.run.bump(object_type, "read")
        self.run.bump(object_type, r)

    def sync_account(self) -> None:
        d = self.client.get("/account-info/v3/details")
        pid = d.get("portalId")
        if int(pid) != int(self.cfg.portal_id):
            with lgdb.transaction(self.con):
                dq(self.con, self.run.run_id, "portal_mismatch|%s" % self.account, "portal_mismatch", "error", self.account, "account", self.account,
                   "token for %s resolves to portal %s but config says %s" % (self.account, pid, self.cfg.portal_id), {"portalId": pid})
            raise RuntimeError("portal id mismatch for %s: API says %s, config says %s" % (self.account, pid, self.cfg.portal_id))
        self._store_blob("properties", "account:details", d)
        self.log("account ok: portal %s tz=%s" % (pid, d.get("timeZone")))

    def sync_owners(self) -> None:
        seen = 0
        for archived in (False, True):
            for page in self.client.paginate_get("/crm/v3/owners", {"archived": "true" if archived else "false"}, limit=100):
                with lgdb.transaction(self.con):
                    for o in page:
                        r = upsert_raw(self.con, self.run, self.account, "owners", str(o["id"]), o, associations=None, history=None,
                                       archived=bool(o.get("archived")), created=o.get("createdAt"), updated=o.get("updatedAt"))
                        self.run.bump("owners", "read")
                        self.run.bump("owners", r)
                        seen += 1
        self.log("owners: %d" % seen)

    def sync_pipelines(self) -> None:
        d = self.client.get("/crm/v3/pipelines/deals")
        for p in d.get("results") or []:
            with lgdb.transaction(self.con):
                r = upsert_raw(self.con, self.run, self.account, "pipelines", str(p["id"]), p, associations=None, history=None,
                               archived=bool(p.get("archived")), created=p.get("createdAt"), updated=p.get("updatedAt"))
            self.run.bump("pipelines", "read")
            self.run.bump("pipelines", r)
        self.log("pipelines: %s" % ", ".join("%s(%d stages)" % (p["id"], len(p.get("stages") or [])) for p in d.get("results") or []))

    def sync_properties(self) -> None:
        for ot in DEFINED_TYPES:
            d = self.client.get("/crm/v3/properties/%s" % ot)
            self.defs[ot] = d.get("results") or []
            self._store_blob("properties", ot, {"results": self.defs[ot]})
        self.log("properties: " + ", ".join("%s=%d" % (ot, len(self.defs[ot])) for ot in DEFINED_TYPES))

    # ------------------------------------------------------------------------------------------------------ property lists
    def _defs(self, object_type: str) -> Optional[List[Dict[str, Any]]]:
        if object_type in self.defs:
            return self.defs[object_type]
        r = self.con.execute("SELECT properties_json FROM hsraw_object WHERE account_id=? AND object_type='properties' AND hs_id=?",
                             (self.account, object_type)).fetchone()
        if r:
            self.defs[object_type] = json.loads(r[0]).get("results") or []
            return self.defs[object_type]
        return None

    def props_for(self, object_type: str) -> List[str]:
        std = STD_PROPS[object_type]
        defs = self._defs(object_type) if object_type in DEFINED_TYPES else None
        if defs is None and object_type in DEFINED_TYPES:
            d = self.client.get("/crm/v3/properties/%s" % object_type)
            self.defs[object_type] = defs = d.get("results") or []
        if defs is None:
            return list(std)
        names = {p["name"] for p in defs}
        out = [n for n in std if n in names]
        out += sorted(p["name"] for p in defs if not p.get("hubspotDefined") and p["name"] not in out)
        return out

    # ------------------------------------------------------------------------------------------------------ CRM objects
    def sync_deals(self) -> None:
        self._sync_crm("deals", with_history=True)

    def sync_contacts(self) -> None:
        self._sync_crm("contacts", with_history=False)

    def sync_companies(self) -> None:
        self._sync_crm("companies", with_history=False)

    def sync_engagement(self, object_type: str) -> None:
        self._sync_crm(object_type, with_history=False)

    def _write_page(self, object_type: str, results: List[Dict[str, Any]], archived: bool, with_history: bool) -> None:
        for chunk in _chunks(results, BATCH_ROWS):
            with lgdb.transaction(self.con):
                for o in chunk:
                    props = dict(o.get("properties") or {})
                    is_arch = bool(o.get("archived")) or archived
                    if o.get("archivedAt"):
                        props["_archived_at"] = o["archivedAt"]
                    hist = o.get("propertiesWithHistory") if with_history else None
                    if with_history and hist is None:
                        hist = {}
                    r = upsert_raw(self.con, self.run, self.account, object_type, str(o["id"]), props, associations=KEEP,
                                   history=(hist if with_history else KEEP), archived=is_arch,
                                   created=o.get("createdAt"), updated=o.get("updatedAt"))
                    self.run.bump(object_type, "read")
                    self.run.bump(object_type, r)
                    if r != "unchanged":
                        self.touched.setdefault(object_type, []).append(str(o["id"]))

    def _sync_crm(self, object_type: str, with_history: bool) -> None:
        props = self.props_for(object_type)
        cursor = None if self.full else get_cursor(self.con, self.account, object_type)
        t0 = time.time()
        total = 0
        hist_param = ",".join(HISTORY_PROPS) if with_history else None
        lm_prop = LASTMOD_PROP.get(object_type, "hs_lastmodifieddate")
        started = self.started_iso
        if cursor is None:
            self.log("%s: FULL (props=%d)" % (object_type, len(props)))
            for archived in ((False, True) if object_type in ARCHIVED_TOO else (False,)):
                p = {"properties": ",".join(props), "archived": "true" if archived else "false"}
                if hist_param:
                    p["propertiesWithHistory"] = hist_param
                for page in self.client.paginate_get("/crm/v3/objects/%s" % object_type, p, limit=50 if with_history else 100):
                    self._write_page(object_type, page, archived, with_history)
                    total += len(page)
                    if total % 1000 < len(page):
                        self.log("  %s: %d read (%.0fs)" % (object_type, total, time.time() - t0))
        else:
            since_ms = iso_to_ms(cursor) - self.since_hours * 3600 * 1000
            end_ms = int(time.time() * 1000) + 86400000
            self.log("%s: INCREMENTAL since %s (cursor %s - %dh)" % (object_type, ms_to_iso(since_ms), cursor, self.since_hours))
            search_props = [lm_prop] if with_history else props
            ids = []  # type: List[str]
            for page in self.client.search_windowed(object_type, lm_prop, since_ms, end_ms, search_props):
                if with_history:
                    ids.extend(str(o["id"]) for o in page)
                else:
                    self._write_page(object_type, page, False, False)
                    total += len(page)
            if with_history and ids:
                for chunk in _chunks(ids, 50):
                    res = self.client.batch_read(object_type, chunk, props, HISTORY_PROPS)
                    self._write_page(object_type, res, False, True)
                    total += len(res)
            if object_type in ARCHIVED_TOO:                                # archived objects are not searchable: list them (cheap)
                p = {"properties": ",".join(props), "archived": "true"}
                if hist_param:
                    p["propertiesWithHistory"] = hist_param
                for page in self.client.paginate_get("/crm/v3/objects/%s" % object_type, p, limit=50 if with_history else 100):
                    self._write_page(object_type, page, True, with_history)
                    total += len(page)
        set_cursor(self.con, self.account, object_type, started, total, self.run.run_id)
        self.log("%s: %d read in %.0fs" % (object_type, total, time.time() - t0))
        if object_type in ENGAGEMENTS:
            self._engagement_assoc(object_type)

    # ------------------------------------------------------------------------------------------------------ associations
    def _ids(self, object_type: str, only_live: bool = True) -> List[str]:
        q = "SELECT hs_id FROM hsraw_object WHERE account_id=? AND object_type=?" + (" AND is_archived=0" if only_live else "")
        return [r[0] for r in self.con.execute(q + " ORDER BY raw_id", (self.account, object_type))]

    def _write_assoc(self, object_type: str, ids: List[str], targets: Tuple[str, ...]) -> Tuple[int, int]:
        """Fetch the v4 associations of ``ids`` towards ``targets`` and store them.  Returns (objects, changed)."""
        objs = changed = 0
        for chunk in _chunks(ids, 100):
            merged = {i: {} for i in chunk}  # type: Dict[str, Dict[str, Any]]
            for t in targets:
                res = self.client.batch_read_associations(object_type, t, chunk)
                for i in chunk:
                    merged[i][t] = res.get(i, [])
            with lgdb.transaction(self.con):
                for i in chunk:
                    r = set_associations(self.con, self.run, self.account, object_type, i, merged[i])
                    objs += 1
                    changed += 1 if r == "updated" else 0
        return objs, changed

    def sync_assoc(self) -> None:
        ids = self._ids("deals")
        t0 = time.time()
        objs, changed = self._write_assoc("deals", ids, ("contacts", "companies"))
        self.run.bump("deal_associations", "read", objs)
        self.run.bump("deal_associations", "updated", changed)
        self.run.bump("deal_associations", "unchanged", objs - changed)
        self.log("deal associations: %d deals, %d changed (%.0fs)" % (objs, changed, time.time() - t0))

    def _engagement_assoc(self, object_type: str) -> None:
        touched = set(self.touched.get(object_type, []))
        missing = {r[0] for r in self.con.execute("SELECT hs_id FROM hsraw_object WHERE account_id=? AND object_type=? AND associations_json IS NULL",
                                                  (self.account, object_type))}
        ids = sorted(touched | missing)
        if not ids:
            return
        objs, changed = self._write_assoc(object_type, ids, ASSOC_TARGETS[object_type])
        self.log("%s associations: %d objects, %d changed" % (object_type, objs, changed))

    # ------------------------------------------------------------------------------------------------------ reconcile
    def reconcile(self) -> None:
        """Compare live HubSpot totals (independent search count) with what hsraw holds for the object types of this run."""
        out = {}
        for ot in ("deals", "contacts", "companies") + ENGAGEMENTS:
            if ot not in self.objects or ot in self.run.scope_missing:
                continue
            try:
                api = self.client.search_total(ot)
            except HubSpotError:
                continue
            db = self.con.execute("SELECT COUNT(*) FROM hsraw_object WHERE account_id=? AND object_type=? AND is_archived=0",
                                  (self.account, ot)).fetchone()[0]
            out[ot] = {"api": api, "db": db}
            if api != db:
                with lgdb.transaction(self.con):
                    dq(self.con, self.run.run_id, "count_mismatch|%s|%s" % (self.account, ot), "count_mismatch", "warn", self.account, "object_type", ot,
                       "%s %s: HubSpot search total %d, hsraw non-archived %d (objects created/deleted during the run?)" % (self.account, ot, api, db),
                       {"api": api, "db": db})
        self.run.params["reconcile"] = out
        self.log("reconcile: " + dumps(out))


# ---------------------------------------------------------------------------------------------- independent verification
def verify_counts(account: str, con: sqlite3.Connection, client: Optional[HubSpotClient] = None, log: Any = print) -> Dict[str, Any]:
    """Independent count queries: live HubSpot search totals vs the canonical tables / hsraw (read-only).  Returns a dict with
    ``objects`` (api vs db per type), ``stages`` (api vs v_funnel_stage_counts per stage) and ``mismatches``."""
    client = client or HubSpotClient(account)
    res = {"objects": {}, "stages": [], "mismatches": []}  # type: Dict[str, Any]
    canon = {"deals": ("deal", "is_archived=0"), "contacts": ("contact", "is_archived=0"), "companies": ("company_account_link", "is_archived=0")}
    for ot in ("deals", "contacts", "companies", "notes"):
        api = client.search_total(ot)
        if ot == "notes":
            db = con.execute("SELECT COUNT(*) FROM engagement WHERE account_id=? AND kind='note' AND is_archived=0", (account,)).fetchone()[0]
        else:
            t, w = canon[ot]
            db = con.execute("SELECT COUNT(*) FROM %s WHERE account_id=? AND %s" % (t, w), (account,)).fetchone()[0]
        raw = con.execute("SELECT COUNT(*) FROM hsraw_object WHERE account_id=? AND object_type=? AND is_archived=0", (account, ot)).fetchone()[0]
        res["objects"][ot] = {"api": api, "canonical": db, "hsraw": raw}
        if not (api == db == raw):
            res["mismatches"].append("%s %s: api=%d canonical=%d hsraw=%d" % (account, ot, api, db, raw))
    api_arch = None
    for pid, in con.execute("SELECT pipeline_id FROM pipeline WHERE account_id=? AND is_reporting_funnel=1 ORDER BY display_order", (account,)).fetchall():
        for st in con.execute("SELECT stage_id, stage_label AS label, deals FROM v_funnel_stage_counts WHERE account_id=? AND pipeline_id=? ORDER BY stage_order, stage_id",
                              (account, pid)).fetchall():
            api = client.search_total("deals", [{"propertyName": "pipeline", "operator": "EQ", "value": pid},
                                                {"propertyName": "dealstage", "operator": "EQ", "value": st["stage_id"]}])
            res["stages"].append({"pipeline": pid, "stage_id": st["stage_id"], "label": st["label"], "api": api, "db": st["deals"]})
            if api != st["deals"]:
                res["mismatches"].append("%s %s stage %s (%s): api=%d db=%d" % (account, pid, st["stage_id"], st["label"], api, st["deals"]))
    return res


# ---------------------------------------------------------------------------------------------- CLI
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m leadgen.hubspot.sync", description="Read-only HubSpot -> hsraw_* sync")
    ap.add_argument("--account", required=True, help="main | companyops | rat | all")
    ap.add_argument("--full", action="store_true", help="ignore incremental cursors and re-read everything")
    ap.add_argument("--object", default=None, help="comma list of: " + ",".join(ALL_OBJECTS))
    ap.add_argument("--since-hours", type=int, default=24, help="overlap subtracted from the cursor (default 24)")
    ap.add_argument("--db", default=None)
    ap.add_argument("--verify", action="store_true", help="after the sync (or alone with --object none) print API vs DB counts")
    args = ap.parse_args(argv)
    accounts = sorted(lgconfig.get_accounts()) if args.account == "all" else [args.account]
    objs = None
    if args.object:
        objs = [o.strip() for o in args.object.split(",") if o.strip() and o.strip() != "none"]
        bad = [o for o in objs if o not in ALL_OBJECTS]
        if bad:
            ap.error("unknown --object %s" % bad)
    con = lgdb.connect(args.db, must_exist=True)
    rc = 0
    try:
        for acct in accounts:
            if args.object != "none":
                res = AccountSync(acct, con, full=args.full, objects=objs, since_hours=args.since_hours).run_all()
                if res["status"] != "succeeded":
                    rc = 1
            if args.verify:
                v = verify_counts(acct, con)
                print(json.dumps(v["objects"]), flush=True)
                print("stage mismatches: %s" % (v["mismatches"] or "none"), flush=True)
                if v["mismatches"]:
                    rc = 1
    finally:
        con.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
