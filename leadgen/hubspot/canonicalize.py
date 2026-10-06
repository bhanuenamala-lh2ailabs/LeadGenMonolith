"""hsraw_* -> canonical tables (owner, pipeline, stage, stage_map, company, contact, deal, deal_stage_event, ... ).

    python -m leadgen.hubspot.canonicalize [--account main|companyops|rat|all] [--all] [--db PATH]

Idempotent: every write is compare-then-write (a second run changes nothing), history rows use UNIQUE keys, nothing is ever
deleted except association rows that HubSpot no longer reports.  By default only raw rows whose payload changed since they were last
applied (``canonical_payload_sha256 IS NOT payload_sha256``) are re-projected; associations of all live deals are always re-linked
(cheap, diff based).  ``--all`` re-projects everything.

Rules that matter
    * Golden companies: a portal company links to an existing golden company ONLY through a STRONG key (root_domain, linkedin_company;
      free-mail domains are weak) - never by name.  Name-only twins become ``merge_candidate`` rows.  Nothing is merged automatically.
    * Stage ids seen only in deal history (deleted stages) become tombstone ``stage`` rows before any event references them.
    * ``deal_stage_event.pipeline_id`` is resolved per event (unique stage id -> pipeline property history -> deal's current pipeline,
      the last one disclosed in ``pipeline_basis``); an event that cannot be resolved is skipped with an ``ops_dq_issue``.
    * +91 mobile gate: ``phone_type = 'mobile'`` is only set for numbers that are certainly mobile (libphonenumber when installed,
      otherwise the 6-9 prefix rule minus the 79/80 metro-landline prefixes); everything unsure stays 'unknown' (gate closed).

Python 3.9 compatible.
"""
import argparse
import hashlib
import html
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from html.parser import HTMLParser
from typing import Any, Dict, Iterable, Iterator, List, Optional, Set, Tuple

from leadgen import config as lgconfig
from leadgen import db as lgdb
from leadgen import norm
from leadgen.hubspot.sync import dq, now_iso

TOOL_VERSION = "hubspot-canonicalize/1.0"
STAGE_MAP_FILE = "stage_map"
CHUNK = 200

FREEMAIL = frozenset("""gmail.com googlemail.com yahoo.com yahoo.in yahoo.co.in ymail.com rocketmail.com hotmail.com hotmail.co.uk outlook.com live.com
msn.com aol.com icloud.com me.com mac.com protonmail.com proton.me gmx.com gmx.net mail.com zoho.com zohomail.in rediffmail.com rediff.com
yandex.com yandex.ru qq.com 163.com 126.com sina.com web.de t-online.de naver.com hey.com fastmail.com tutanota.com inbox.com
linkedin.com facebook.com""".split())
COUNTRY_PREFIX = (("971", "AE"), ("353", "IE"), ("880", "BD"), ("977", "NP"), ("91", "IN"), ("44", "GB"), ("65", "SG"), ("61", "AU"),
                  ("49", "DE"), ("33", "FR"), ("34", "ES"), ("39", "IT"), ("31", "NL"), ("94", "LK"))
KIND = {"notes": "note", "tasks": "task", "calls": "call", "meetings": "meeting", "emails": "email"}
ENGAGEMENT_SUBJECT = {"tasks": "hs_task_subject", "calls": "hs_call_title", "meetings": "hs_meeting_title", "emails": "hs_email_subject"}
ENGAGEMENT_BODY = {"notes": "hs_note_body", "tasks": "hs_task_body", "calls": "hs_call_body", "meetings": "hs_meeting_body", "emails": "hs_email_text"}
ENGAGEMENT_STATUS = {"tasks": "hs_task_status", "calls": "hs_call_status", "meetings": "hs_meeting_outcome", "emails": "hs_email_status"}

try:                                                    # optional: exact mobile / landline classification
    import phonenumbers as _pn                         # type: ignore
except Exception:                                      # pragma: no cover - not installed in the default venv
    _pn = None


# ---------------------------------------------------------------------------------------------- small helpers
class _Text(HTMLParser):
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "ul", "ol"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts = []  # type: List[str]

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def html_to_text(s: Optional[str]) -> Optional[str]:
    """HubSpot note bodies are HTML: strip tags, keep line breaks, collapse blanks.  None for empty."""
    if not s:
        return None
    p = _Text()
    try:
        p.feed(s)
        p.close()
        text = "".join(p.parts)
    except Exception:
        text = re.sub(r"<[^>]+>", " ", html.unescape(s))
    text = re.sub(r"[ \t\r\f\v ]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text).strip()
    return text or None


def fnum(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        return x if x == x and x not in (float("inf"), float("-inf")) else None
    except (TypeError, ValueError):
        return None


def nz(v: Any) -> Optional[str]:
    """Empty / whitespace-only -> None, else the stripped string."""
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def lead_source_code(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_") or "unknown"


def ts(v: Any) -> Optional[str]:
    return norm.to_utc_ms(v) if v not in (None, "") else None


def ts_ms(v: Any) -> Optional[str]:
    """HubSpot date properties arrive as ISO text, 'YYYY-MM-DD', or epoch milliseconds."""
    if v in (None, ""):
        return None
    s = str(v).strip()
    if re.match(r"^\d{12,14}$", s):
        return norm.to_utc_ms(int(s) / 1000.0)
    return norm.to_utc_ms(s)


def classify_phone(raw: str) -> Dict[str, Any]:
    """{'e164','country','type','placeholder'} for one raw phone string (see module docstring for the gate policy)."""
    e164 = norm.norm_phone_e164(raw)
    out = {"e164": e164, "country": None, "type": "unknown", "placeholder": 0}
    if not e164:
        return out
    digits = e164[1:]
    for pre, iso in COUNTRY_PREFIX:
        if digits.startswith(pre):
            out["country"] = iso
            break
    if _pn is not None:
        try:
            num = _pn.parse(e164, None)
            if not _pn.is_possible_number(num):
                out["placeholder"] = 0
            t = _pn.number_type(num)
            out["type"] = {_pn.PhoneNumberType.MOBILE: "mobile", _pn.PhoneNumberType.FIXED_LINE: "landline",
                           _pn.PhoneNumberType.TOLL_FREE: "tollfree", _pn.PhoneNumberType.VOIP: "voip"}.get(t, "unknown")
        except Exception:
            pass
    if e164.startswith("+91"):
        nat = e164[3:]
        if len(nat) == 10 and (len(set(nat)) == 1 or nat in ("1234567890", "0123456789", "9876543210")):
            out["placeholder"] = 1
        if _pn is None:
            if len(nat) == 10 and nat[0] in "6789":
                out["type"] = "unknown" if nat.startswith(("79", "80")) else "mobile"
            elif len(nat) == 10 and nat[0] in "12345":
                out["type"] = "landline"
            elif len(nat) == 11 and nat.startswith("1800"):
                out["type"] = "tollfree"
    return out


def map_chain(con: sqlite3.Connection, company_id: int) -> int:
    """Follow merged_into_company_id to the survivor."""
    seen = set()
    while company_id not in seen:
        seen.add(company_id)
        r = con.execute("SELECT merged_into_company_id FROM company WHERE company_id=?", (company_id,)).fetchone()
        if not r or r[0] is None:
            return company_id
        company_id = r[0]
    return company_id


# ---------------------------------------------------------------------------------------------- the canonicalizer
class Canon:
    def __init__(self, account: str, con: sqlite3.Connection, all_rows: bool = False, log: Any = None):
        self.account = account
        self.con = con
        self.all_rows = all_rows
        self._log = log or (lambda m: print(m, flush=True))
        self.src = "hubspot:%s" % account
        self.stats = defaultdict(Counter)  # type: Dict[str, Counter]
        self.run_id = None  # type: Optional[int]
        self.custom = {}    # type: Dict[str, Set[str]]
        self.owner_by_hs = {}   # type: Dict[str, int]
        self.owner_by_user = {}  # type: Dict[str, int]
        self.lead_sources = {}  # type: Dict[str, int]
        self.dq_counts = Counter()  # type: Counter
        self.inferred_actors = Counter()  # type: Counter
        self.rejected_fps = set()  # type: Set[str]
        self.user_eq_owner = False
        self.owner_unlinked = {}  # type: Dict[str, int]
        self.unmapped = []  # type: List[Dict[str, Any]]
        self.stage_map_cfg = None  # type: Optional[Dict[str, Any]]

    def log(self, msg: str) -> None:
        self._log("[%s] %s" % (self.account, msg))

    # ------------------------------------------------------------------------------------------------ generic upsert
    def upsert(self, table: str, key: Dict[str, Any], values: Dict[str, Any], id_col: Optional[str] = None,
               on_insert: Optional[Dict[str, Any]] = None) -> Tuple[Optional[int], str]:
        """Compare-then-write.  Returns (id, 'inserted'|'updated'|'unchanged').  ``on_insert`` columns (e.g. import_run_id) are written
        only when the row is created, so a re-run does not touch an unchanged row."""
        where = " AND ".join("%s IS ?" % k for k in key)
        cols = list(values)
        row = self.con.execute("SELECT %s FROM %s WHERE %s" % (", ".join(([id_col] if id_col else []) + cols) or "1", table, where),
                               tuple(key.values())).fetchone()
        if row is None:
            allv = dict(key)
            allv.update(values)
            allv.update(on_insert or {})
            cur = self.con.execute("INSERT INTO %s (%s) VALUES (%s)" % (table, ", ".join(allv), ", ".join("?" * len(allv))), tuple(allv.values()))
            self.stats[table]["inserted"] += 1
            return (cur.lastrowid if id_col else None), "inserted"
        diff = {c: values[c] for c in cols if not _same(row[c], values[c])}
        rid = row[id_col] if id_col else None
        if not diff:
            self.stats[table]["unchanged"] += 1
            return rid, "unchanged"
        self.con.execute("UPDATE %s SET %s WHERE %s" % (table, ", ".join("%s=?" % c for c in diff), where), tuple(diff.values()) + tuple(key.values()))
        self.stats[table]["updated"] += 1
        return rid, "updated"

    def origin(self, entity_type: str, entity_id: int, table: str, pk: str, method: str = "hs_id") -> None:
        self.con.execute(
            "INSERT INTO origin_ref (entity_type, entity_id, source_system, source_table, source_pk, match_method, confidence, import_run_id) "
            "VALUES (?,?,?,?,?,?,1.0,?) ON CONFLICT DO NOTHING", (entity_type, entity_id, self.src, table, pk, method, self.run_id))

    def dq(self, fingerprint: str, rule: str, severity: str, etype: Optional[str], eref: Optional[str], message: str,
           details: Optional[Dict[str, Any]] = None) -> None:
        dq(self.con, self.run_id, fingerprint, rule, severity, self.account, etype, eref, message, details)
        self.dq_counts[rule] += 1

    def safe(self, kind: str, key: str, fn: Any, *args: Any) -> bool:
        """Run one row-level projection inside a SAVEPOINT: a constraint violation rejects THAT row (ops_dq_issue 'row_rejected') and the
        batch carries on.  A rejected row is not marked applied, so it is retried (and re-reported) on the next run."""
        self.con.execute("SAVEPOINT row_sp")
        try:
            fn(*args)
        except sqlite3.IntegrityError as exc:
            self.con.execute("ROLLBACK TO row_sp")
            self.con.execute("RELEASE row_sp")
            self.dq("row_rejected|%s|%s|%s" % (self.account, kind, key), "row_rejected", "error", kind, key,
                    "%s %s rejected by a database constraint: %s" % (kind, key, exc), {"error": str(exc)})
            self.stats["rejected"][kind] += 1
            self.rejected_fps.add("row_rejected|%s|%s|%s" % (self.account, kind, key))
            return False
        self.con.execute("RELEASE row_sp")
        return True

    # ------------------------------------------------------------------------------------------------ raw access
    def raw_ids(self, object_type: str, only_dirty: bool = True) -> List[int]:
        q = "SELECT raw_id FROM hsraw_object WHERE account_id=? AND object_type=?"
        if only_dirty and not self.all_rows:
            q += " AND canonical_payload_sha256 IS NOT payload_sha256"
        return [r[0] for r in self.con.execute(q + " ORDER BY raw_id", (self.account, object_type))]

    def raw_rows(self, ids: List[int]) -> List[sqlite3.Row]:
        return self.con.execute("SELECT * FROM hsraw_object WHERE raw_id IN (%s) ORDER BY raw_id" % ",".join("?" * len(ids)), ids).fetchall()

    def mark_applied(self, ids: List[int]) -> None:
        if ids:
            self.con.execute("UPDATE hsraw_object SET canonical_payload_sha256=payload_sha256, canonical_applied_at=? WHERE raw_id IN (%s)" % ",".join("?" * len(ids)),
                             [now_iso()] + ids)

    def chunks(self, object_type: str) -> Iterator[List[sqlite3.Row]]:
        ids = self.raw_ids(object_type)
        for i in range(0, len(ids), CHUNK):
            yield self.raw_rows(ids[i:i + CHUNK])

    # ------------------------------------------------------------------------------------------------ run wrapper
    def run(self) -> Dict[str, Any]:
        with lgdb.transaction(self.con):
            self.run_id = self.con.execute(
                "INSERT INTO ops_import_run (kind, source_name, account_id, status, started_at, params_json, tool_version) "
                "VALUES ('hubspot_sync', ?, ?, 'running', ?, ?, ?)",
                ("hubspot:%s:canonicalize" % self.account, self.account, now_iso(), json.dumps({"all": self.all_rows}), TOOL_VERSION)).lastrowid
        try:
            self.load_custom_names()
            with lgdb.transaction(self.con):
                self.mark_applied(self.raw_ids("properties"))           # consumed above (custom property names); nothing else to project
            self.do_owners()
            self.do_pipelines()
            self.do_stage_map()
            self.do_companies()
            self.do_contacts()
            self.do_deals()
            self.do_deal_assoc()
            self.do_engagements()
            self.resolve_unmapped_dq()
            self.resolve_rejected_dq()
        except BaseException as exc:
            with lgdb.transaction(self.con):
                self.con.execute("UPDATE ops_import_run SET status='failed', finished_at=?, error=? WHERE import_run_id=?",
                                 (now_iso(), lgconfig.redact("%s: %s" % (type(exc).__name__, exc))[:2000], self.run_id))
            raise
        tot = Counter()
        for name, c in self.stats.items():
            if name != "rejected":
                tot.update(c)
        rejected = sum(self.stats["rejected"].values()) if "rejected" in self.stats else 0
        with lgdb.transaction(self.con):
            self.con.execute("UPDATE ops_import_run SET status='succeeded', finished_at=?, rows_read=?, rows_written=?, rows_skipped=?, rows_rejected=?, params_json=? "
                             "WHERE import_run_id=?",
                             (now_iso(), tot["inserted"] + tot["updated"] + tot["unchanged"], tot["inserted"] + tot["updated"], tot["unchanged"],
                              rejected, json.dumps({"all": self.all_rows, "stats": {k: dict(v) for k, v in self.stats.items()},
                                                                       "unmapped": self.unmapped}, sort_keys=True), self.run_id))
        self.log("done: " + json.dumps({k: dict(v) for k, v in self.stats.items() if v["inserted"] or v["updated"]}, sort_keys=True))
        return {"stats": {k: dict(v) for k, v in self.stats.items()}, "unmapped": self.unmapped, "dq": dict(self.dq_counts)}

    def load_custom_names(self) -> None:
        for ot in ("deals", "contacts", "companies"):
            r = self.con.execute("SELECT properties_json FROM hsraw_object WHERE account_id=? AND object_type='properties' AND hs_id=?",
                                 (self.account, ot)).fetchone()
            defs = json.loads(r[0]).get("results") or [] if r else []
            self.custom[ot] = {p["name"] for p in defs if not p.get("hubspotDefined")}

    # ------------------------------------------------------------------------------------------------ owners
    def do_owners(self) -> None:
        for rows in self.chunks("owners"):
            ids = []
            with lgdb.transaction(self.con):
                for r in rows:
                    o = json.loads(r["properties_json"])
                    self.upsert_owner(o)
                    ids.append(r["raw_id"])
                self.mark_applied(ids)
        self.load_owner_maps()

    def load_owner_maps(self) -> None:
        self.owner_by_hs = {r[0]: r[1] for r in self.con.execute("SELECT hs_owner_id, owner_id FROM owner WHERE account_id=?", (self.account,))}
        self.owner_by_user = {r[0]: r[1] for r in self.con.execute("SELECT hs_user_id, owner_id FROM owner WHERE account_id=? AND hs_user_id IS NOT NULL", (self.account,))}
        # Deactivated users: the owners API (archived=true) returns them WITHOUT a userId, yet stage history still names them as the actor.
        # Fallback userId == ownerId, used only when every owner that exposes both ids has them equal in this portal (verified here, not assumed).
        both = self.con.execute("SELECT COUNT(*), SUM(hs_user_id = hs_owner_id) FROM owner WHERE account_id=? AND hs_user_id IS NOT NULL", (self.account,)).fetchone()
        self.user_eq_owner = bool(both[0]) and both[0] == both[1]
        self.owner_unlinked = {r[0]: r[1] for r in self.con.execute(
            "SELECT hs_owner_id, owner_id FROM owner WHERE account_id=? AND hs_user_id IS NULL AND (role IS NULL OR role NOT LIKE 'stub%')", (self.account,))}

    def actor_owner(self, uid: Optional[str]) -> Optional[int]:
        """Owner of the HubSpot USER id that made a change: exact (owner.hs_user_id) first; else, for a deactivated user with no userId on
        record, the owner whose id equals the user id when this portal has shown userId == ownerId for every owner exposing both."""
        if not uid:
            return None
        if uid in self.owner_by_user:
            return self.owner_by_user[uid]
        if self.user_eq_owner and uid in self.owner_unlinked:
            self.inferred_actors[uid] += 1
            return self.owner_unlinked[uid]
        return None

    def upsert_owner(self, o: Dict[str, Any]) -> int:
        hs_id = norm.norm_hs_id(o["id"])
        user = None
        if o.get("userId") not in (None, "", 0):
            try:
                user = norm.norm_hs_id(o["userId"])
            except ValueError:
                user = None
        if user:
            clash = self.con.execute("SELECT hs_owner_id FROM owner WHERE account_id=? AND hs_user_id=? AND hs_owner_id<>?", (self.account, user, hs_id)).fetchone()
            if clash:
                self.dq("owner_user_clash|%s|%s" % (self.account, user), "owner_user_clash", "warn", "owner", hs_id,
                        "owner %s and %s share HubSpot userId %s; userId kept on %s only" % (hs_id, clash[0], user, clash[0]))
                user = None
        first, last = nz(o.get("firstName")), nz(o.get("lastName"))
        email = norm.norm_email(o.get("email"))
        display = " ".join(x for x in (first, last) if x) or email or "owner %s" % hs_id
        oid, _ = self.upsert("owner", {"account_id": self.account, "hs_owner_id": hs_id},
                          {"hs_user_id": user, "email_norm": email, "first_name": first, "last_name": last, "display_name": display,
                           "role": None, "is_archived": 1 if o.get("archived") else 0}, id_col="owner_id")
        self.origin("owner", oid, "owners", hs_id)
        self.owner_by_hs[hs_id] = oid
        if user:
            self.owner_by_user[user] = oid
        return oid

    def ensure_owner(self, hs_owner_id: Any) -> Optional[int]:
        """Owner id of a HubSpot owner id; creates a stub (archived, role 'stub') for an id the owners API does not list."""
        if hs_owner_id in (None, ""):
            return None
        try:
            hs = norm.norm_hs_id(hs_owner_id)
        except ValueError:
            return None
        if hs in self.owner_by_hs:
            return self.owner_by_hs[hs]
        oid, _ = self.upsert("owner", {"account_id": self.account, "hs_owner_id": hs},
                          {"hs_user_id": None, "email_norm": None, "first_name": None, "last_name": None,
                           "display_name": "Unknown owner %s" % hs, "role": "stub: not listed by the owners API", "is_archived": 1}, id_col="owner_id")
        self.origin("owner", oid, "owners", hs, "stub")
        self.owner_by_hs[hs] = oid
        self.dq("unknown_owner|%s|%s" % (self.account, hs), "unknown_owner", "info", "owner", hs,
                "owner id %s is used by objects but is not in the owners API (deleted user?); stub owner row created" % hs)
        return oid

    # ------------------------------------------------------------------------------------------------ pipelines & stages
    def do_pipelines(self) -> None:
        with lgdb.transaction(self.con):
            ids = []
            for r in self.con.execute("SELECT * FROM hsraw_object WHERE account_id=? AND object_type='pipelines' ORDER BY raw_id", (self.account,)).fetchall():
                p = json.loads(r["properties_json"])
                pid = str(p["id"])
                pr = self.con.execute("SELECT label, previous_labels_json FROM pipeline WHERE account_id=? AND pipeline_id=?", (self.account, pid)).fetchone()
                prev = json.loads(pr["previous_labels_json"]) if pr else []
                if pr and pr["label"] != p["label"] and pr["label"] not in prev:
                    prev.append(pr["label"])
                if pr is None:
                    self.con.execute("INSERT INTO pipeline (account_id, pipeline_id, label, is_reporting_funnel, previous_labels_json) VALUES (?,?,?,0,'[]')",
                                     (self.account, pid, p["label"]))
                    self.stats["pipeline"]["inserted"] += 1
                    self.dq("unknown_pipeline|%s|%s" % (self.account, pid), "unknown_pipeline", "warn", "pipeline", pid,
                            "pipeline %s (%s) exists in HubSpot but not in config/accounts.yaml: stored as non-reporting" % (pid, p["label"]))
                self.upsert("pipeline", {"account_id": self.account, "pipeline_id": pid},
                         {"label": p["label"], "previous_labels_json": json.dumps(prev), "hs_created_at": ts(p.get("createdAt")),
                          "labels_refreshed_at": r["fetched_at"]})
                seen = set()
                for s in p.get("stages") or []:
                    sid = str(s["id"])
                    seen.add(sid)
                    md = s.get("metadata") or {}
                    ex = self.con.execute("SELECT label, previous_labels_json FROM stage WHERE account_id=? AND pipeline_id=? AND stage_id=?", (self.account, pid, sid)).fetchone()
                    sprev = json.loads(ex["previous_labels_json"]) if ex else []
                    if ex and ex["label"] != s["label"] and not ex["label"].startswith("Deleted stage ") and ex["label"] not in sprev:
                        sprev.append(ex["label"])
                    prob = fnum(md.get("probability"))
                    self.upsert("stage", {"account_id": self.account, "pipeline_id": pid, "stage_id": sid},
                             {"label": s["label"], "display_order": s.get("displayOrder"), "is_closed": 1 if str(md.get("isClosed")).lower() == "true" else 0,
                              "hs_probability": prob if prob is not None and 0 <= prob <= 1 else None, "is_deleted": 0, "label_source": "api",
                              "previous_labels_json": json.dumps(sprev), "last_seen_in_api_at": r["fetched_at"]})
                # stages we know from the API earlier but HubSpot no longer lists -> tombstones (history still references them)
                for g in self.con.execute("SELECT stage_id FROM stage WHERE account_id=? AND pipeline_id=? AND label_source='api' AND is_deleted=0", (self.account, pid)).fetchall():
                    if g[0] not in seen:
                        self.con.execute("UPDATE stage SET is_deleted=1 WHERE account_id=? AND pipeline_id=? AND stage_id=?", (self.account, pid, g[0]))
                        self.stats["stage"]["updated"] += 1
                ids.append(r["raw_id"])
            self.mark_applied(ids)

    # ------------------------------------------------------------------------------------------------ stage_map (+ aliases)
    def load_stage_map(self) -> Dict[str, Any]:
        if self.stage_map_cfg is None:
            self.stage_map_cfg = lgconfig.load_yaml(STAGE_MAP_FILE)
        return self.stage_map_cfg

    def do_stage_map(self) -> None:
        cfg = self.load_stage_map()
        canon = {r["canonical_code"]: r for r in self.con.execute("SELECT * FROM canonical_stage")}
        with lgdb.transaction(self.con):
            self.ensure_deleted_stages(cfg)
            entries = []  # (pipeline_id, stage entry)
            for pl in cfg.get("pipelines") or []:
                if pl["account"] == self.account:
                    entries.extend((str(pl["pipeline_id"]), s) for s in pl.get("stages") or [])
            for al in cfg.get("deleted_stages") or []:
                if al["account"] == self.account and al.get("canonical"):
                    entries.append((str(al["pipeline_id"]), al))
            for pid, s in entries:
                if True:
                    sid = str(s["id"])
                    st = self.con.execute("SELECT label, is_deleted FROM stage WHERE account_id=? AND pipeline_id=? AND stage_id=?", (self.account, pid, sid)).fetchone()
                    if st is None:
                        self.dq("stage_map_stale|%s|%s|%s" % (self.account, pid, sid), "stage_map_stale", "warn", "stage", sid,
                                "config/stage_map.yaml maps stage %s (%s) of pipeline %s but HubSpot / history never showed it" % (sid, s.get("label"), pid))
                        continue
                    if s.get("label") and st["label"].strip().lower() != str(s["label"]).strip().lower() and not st["is_deleted"]:
                        self.dq("stage_label_drift|%s|%s|%s" % (self.account, pid, sid), "stage_label_drift", "info", "stage", sid,
                                "stage %s was labelled %r when mapped, HubSpot now says %r (mapping is by id, still valid)" % (sid, s.get("label"), st["label"]))
                    code = s["canonical"]
                    c = canon.get(code)
                    if c is None:
                        raise ValueError("stage_map.yaml: unknown canonical code %r for stage %s" % (code, sid))
                    kind = "won" if c["is_won"] else ("dead" if c["is_dead"] else "live")
                    if s.get("kind") and s["kind"] != kind:
                        raise ValueError("stage_map.yaml: stage %s says kind=%s but canonical %s is %s" % (sid, s["kind"], code, kind))
                    self.upsert("stage_map", {"account_id": self.account, "pipeline_id": pid, "stage_id": sid},
                             {"canonical_code": code, "dead_reason_code": s.get("dead_reason"),
                              "depth_reached": s.get("depth_reached") if s.get("dead_reason") else None, "sub_row": s.get("sub_row"),
                              "flag_attempt": s.get("flag_attempt"), "flag_connected": s.get("flag_connected"), "flag_interested": s.get("flag_interested"),
                              "map_source": "config", "note": s.get("note")})
            for al in cfg.get("deleted_stages") or []:
                if al["account"] == self.account and al.get("alias_of"):
                    self.upsert("stage_alias", {"account_id": self.account, "pipeline_id": str(al["pipeline_id"]), "alias_kind": "stage_id", "alias_value": str(al["id"])},
                             {"stage_id": str(al["alias_of"]), "reason": al.get("reason"), "valid_from": str(al["deleted_on"]) if al.get("deleted_on") else None, "valid_to": str(al["deleted_on"]) if al.get("deleted_on") else None})
            for al in cfg.get("label_aliases") or []:
                if al["account"] == self.account:
                    self.upsert("stage_alias", {"account_id": self.account, "pipeline_id": str(al["pipeline_id"]), "alias_kind": "label", "alias_value": str(al["alias"]).strip().lower()},
                             {"stage_id": str(al["stage_id"]), "reason": al.get("reason"), "valid_from": None, "valid_to": None})
        self.report_unmapped()

    def ensure_deleted_stages(self, cfg: Dict[str, Any]) -> None:
        """Tombstones declared in config/stage_map.yaml (known deleted stage ids), created before any event refers to them."""
        for al in cfg.get("deleted_stages") or []:
            if al["account"] != self.account:
                continue
            self.ensure_stage(str(al["pipeline_id"]), str(al["id"]), al.get("label"), "alias_seed" if al.get("label") else "history")

    def ensure_stage(self, pipeline_id: str, stage_id: str, label: Optional[str] = None, label_source: str = "history") -> bool:
        if self.con.execute("SELECT 1 FROM pipeline WHERE account_id=? AND pipeline_id=?", (self.account, pipeline_id)).fetchone() is None:
            return False
        if self.con.execute("SELECT 1 FROM stage WHERE account_id=? AND pipeline_id=? AND stage_id=?", (self.account, pipeline_id, stage_id)).fetchone():
            return True
        self.con.execute("INSERT INTO stage (account_id, pipeline_id, stage_id, label, is_deleted, label_source) VALUES (?,?,?,?,1,?)",
                         (self.account, pipeline_id, stage_id, label or "Deleted stage %s" % stage_id, label_source))
        self.stats["stage"]["inserted"] += 1
        return True

    def report_unmapped(self) -> None:
        rows = self.con.execute("SELECT * FROM v_unmapped_stage WHERE account_id=?", (self.account,)).fetchall()
        self.unmapped = [{"pipeline_id": r["pipeline_id"], "stage_id": r["stage_id"], "label": r["stage_label"], "deals_now": r["deals_now"],
                          "events": r["events"], "deleted": r["is_deleted"]} for r in rows]
        with lgdb.transaction(self.con):
            for u in self.unmapped:
                self.dq("unmapped_stage|%s|%s|%s" % (self.account, u["pipeline_id"], u["stage_id"]), "unmapped_stage", "error", "stage", u["stage_id"],
                        "stage %s %r of pipeline %s has no stage_map row (deals now %s, events %s)" % (u["stage_id"], u["label"], u["pipeline_id"], u["deals_now"], u["events"]), u)

    def resolve_rejected_dq(self) -> None:
        """Close 'row_rejected' findings of rows that projected fine this time (a rejected row is retried on every run)."""
        with lgdb.transaction(self.con):
            for r in self.con.execute("SELECT dq_issue_id, fingerprint FROM ops_dq_issue WHERE rule_code='row_rejected' AND account_id=? AND status IN ('open','acknowledged')", (self.account,)).fetchall():
                if r["fingerprint"] not in self.rejected_fps:
                    self.con.execute("UPDATE ops_dq_issue SET status='resolved', resolved_at=? WHERE dq_issue_id=?", (now_iso(), r["dq_issue_id"]))

    def resolve_unmapped_dq(self) -> None:
        """Close unmapped_stage findings of stages that are mapped now."""
        self.report_unmapped()
        still = {"unmapped_stage|%s|%s|%s" % (self.account, u["pipeline_id"], u["stage_id"]) for u in self.unmapped}
        with lgdb.transaction(self.con):
            for r in self.con.execute("SELECT dq_issue_id, fingerprint FROM ops_dq_issue WHERE rule_code='unmapped_stage' AND account_id=? AND status IN ('open','acknowledged')", (self.account,)).fetchall():
                if r["fingerprint"] not in still:
                    self.con.execute("UPDATE ops_dq_issue SET status='resolved', resolved_at=? WHERE dq_issue_id=?", (now_iso(), r["dq_issue_id"]))

    # ------------------------------------------------------------------------------------------------ companies
    def link_for(self, hs_company_id: str) -> Optional[sqlite3.Row]:
        return self.con.execute("SELECT link_id, company_id FROM company_account_link WHERE account_id=? AND hs_company_id=?", (self.account, hs_company_id)).fetchone()

    def strong_owner(self, id_type: str, value: str) -> Optional[int]:
        r = self.con.execute("SELECT company_id FROM company_identifier WHERE identifier_type=? AND value_norm=? AND is_strong=1", (id_type, value)).fetchone()
        return map_chain(self.con, r[0]) if r else None

    def do_companies(self) -> None:
        for rows in self.chunks("companies"):
            ids = []
            with lgdb.transaction(self.con):
                for r in rows:
                    if self.safe("company", r["hs_id"], self.company_row, r):
                        ids.append(r["raw_id"])
                self.mark_applied(ids)

    def company_row(self, r: sqlite3.Row) -> None:
        p = json.loads(r["properties_json"])
        hs_id = norm.norm_hs_id(r["hs_id"])
        name = nz(p.get("name"))
        dom = norm.norm_domain(p.get("domain")) or norm.norm_domain(p.get("lh2_domain")) or norm.norm_domain(p.get("website"))
        lh2 = nz(p.get("lh2_domain"))
        lh2 = lh2.lower() if lh2 else None
        li = norm.norm_linkedin_company(p.get("linkedin_company_page"))
        dom_strong = bool(dom) and dom not in FREEMAIL
        if lh2:                                                  # HubSpot only enforces lh2_domain uniqueness among LIVE companies
            holder = self.con.execute("SELECT link_id, hs_company_id, is_archived FROM company_account_link WHERE account_id=? AND lh2_domain=? AND hs_company_id<>?",
                                      (self.account, lh2, hs_id)).fetchone()
            if holder is not None:
                if holder["is_archived"] and not r["is_archived"]:
                    self.con.execute("UPDATE company_account_link SET lh2_domain=NULL WHERE link_id=?", (holder["link_id"],))
                else:
                    lh2 = None
                self.dq("lh2_domain_duplicate|%s" % self.account, "lh2_domain_duplicate", "info", "company", hs_id,
                        "%s: several portal companies (archived duplicates) carry the same lh2_domain; the unique slot goes to the live one / the first seen, "
                        "the others keep lh2_domain NULL in company_account_link (raw value stays in hsraw)" % self.account)
        link = self.link_for(hs_id)
        method, conf = "hs_id", 1.0
        if link is not None:
            gid = map_chain(self.con, link["company_id"])
            method = self.con.execute("SELECT link_method FROM company_account_link WHERE link_id=?", (link["link_id"],)).fetchone()[0]
            creating = method == "new_golden"
        else:
            by_dom = self.strong_owner("root_domain", dom) if dom_strong else None
            by_li = self.strong_owner("linkedin_company", li) if li else None
            gid, creating = None, False
            if by_dom and by_li and by_dom != by_li:
                a, b = sorted((by_dom, by_li))
                self.con.execute("INSERT INTO merge_candidate (entity_type, left_id, right_id, match_kind, score, evidence_json, import_run_id) "
                                 "VALUES ('company',?,?,'strong_key_conflict',0.9,?,?) ON CONFLICT DO NOTHING",
                                 (a, b, json.dumps({"hs_company": "%s:%s" % (self.account, hs_id), "root_domain": dom, "linkedin_company": li}), self.run_id))
            if by_dom:
                gid, method = by_dom, "root_domain"
            elif by_li:
                gid, method = by_li, "linkedin_company"
            if gid is None:
                display = name or dom or "hs company %s:%s" % (self.account, hs_id)
                cc = norm.norm_country(p.get("country"))
                emp = fnum(p.get("numberofemployees"))
                fy = fnum(p.get("founded_year")) or fnum(p.get("incorp_year"))
                cur = self.con.execute(
                    "INSERT INTO company (canonical_name, name_norm, website, hq_city, hq_state, hq_country, india_hq, employee_count, headcount_band, founded_year, first_seen_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (display, (norm.norm_company_name(display) or display.lower()), nz(p.get("website")) or nz(p.get("domain")), nz(p.get("city")), nz(p.get("state")), cc,
                     "yes" if cc == "IN" else ("no" if cc else "unknown"), int(emp) if emp is not None and emp >= 0 else None, nz(p.get("size_bucket")),
                     int(fy) if fy is not None and 1800 <= fy <= 2100 else None, r["hs_created_at"] or now_iso()))
                gid = cur.lastrowid
                self.stats["company"]["inserted"] += 1
                method, creating = "new_golden", True
                if name:
                    nn = norm.norm_company_name(name)
                    for (other,) in self.con.execute("SELECT company_id FROM company WHERE name_norm=? AND company_id<>? AND merged_into_company_id IS NULL ORDER BY company_id LIMIT 20", (nn, gid)).fetchall():
                        a, b = sorted((gid, other))
                        self.con.execute("INSERT INTO merge_candidate (entity_type, left_id, right_id, match_kind, score, evidence_json, import_run_id) "
                                         "VALUES ('company',?,?,'name_norm',0.5,?,?) ON CONFLICT DO NOTHING",
                                         (a, b, json.dumps({"name_norm": nn, "source": "hubspot:%s:%s" % (self.account, hs_id)}), self.run_id))
            else:
                self.stats["company"]["linked"] += 1
        lid, _ = self.upsert("company_account_link", {"account_id": self.account, "hs_company_id": hs_id},
                          {"company_id": gid, "hs_name": name, "hs_domain": nz(p.get("domain")), "lh2_domain": lh2,
                           "hs_created_at": r["hs_created_at"], "hs_updated_at": r["hs_updated_at"], "is_archived": r["is_archived"],
                           "link_method": method, "confidence": conf}, id_col="link_id")
        self.origin("company_account_link", lid, "companies", hs_id)
        self.origin("company", gid, "companies", hs_id, method)
        # identifiers
        self.identifier(gid, "hs_company", "%s:%s" % (self.account, hs_id), hs_id, True)
        if dom:
            self.identifier(gid, "root_domain", dom, nz(p.get("domain")) or dom, dom_strong, primary=dom_strong and creating)
        if li:
            self.identifier(gid, "linkedin_company", li, nz(p.get("linkedin_company_page")), True)
        if creating:                                             # golden fields follow the link that created the golden record
            display = name or dom or "hs company %s:%s" % (self.account, hs_id)
            cc = norm.norm_country(p.get("country"))
            emp = fnum(p.get("numberofemployees"))
            fy = fnum(p.get("founded_year")) or fnum(p.get("incorp_year"))
            self.upsert("company", {"company_id": gid},
                     {"canonical_name": display, "name_norm": norm.norm_company_name(display) or display.lower(), "website": nz(p.get("website")) or nz(p.get("domain")),
                      "hq_city": nz(p.get("city")), "hq_state": nz(p.get("state")), "hq_country": cc, "india_hq": "yes" if cc == "IN" else ("no" if cc else "unknown"),
                      "employee_count": int(emp) if emp is not None and emp >= 0 else None, "headcount_band": nz(p.get("size_bucket")),
                      "founded_year": int(fy) if fy is not None and 1800 <= fy <= 2100 else None})
        ph = nz(p.get("phone"))
        if ph:
            self.phone("company_phone", "company_id", gid, ph, True)

    def identifier(self, gid: int, id_type: str, value: str, raw: Optional[str], strong: bool, primary: bool = False) -> None:
        if self.con.execute("SELECT 1 FROM company_identifier WHERE company_id=? AND identifier_type=? AND value_norm=?", (gid, id_type, value)).fetchone():
            self.stats["company_identifier"]["unchanged"] += 1
            return
        if strong:
            holder = self.con.execute("SELECT company_id FROM company_identifier WHERE identifier_type=? AND value_norm=? AND is_strong=1", (id_type, value)).fetchone()
            if holder and map_chain(self.con, holder[0]) != gid:
                a, b = sorted((gid, map_chain(self.con, holder[0])))
                self.con.execute("INSERT INTO merge_candidate (entity_type, left_id, right_id, match_kind, score, evidence_json, import_run_id) VALUES ('company',?,?,?,0.9,?,?) ON CONFLICT DO NOTHING",
                                 (a, b, "shared_%s" % id_type, json.dumps({"value": value}), self.run_id))
                strong = False
        has_primary = self.con.execute("SELECT 1 FROM company_identifier WHERE company_id=? AND identifier_type=? AND is_primary=1", (gid, id_type)).fetchone()
        self.con.execute("INSERT INTO company_identifier (company_id, identifier_type, value_norm, value_raw, confidence, is_strong, is_primary, source_system) VALUES (?,?,?,?,?,?,?,?)",
                         (gid, id_type, value, raw, 1.0 if strong else 0.3, 1 if strong else 0, 1 if (primary and strong and not has_primary) else 0, self.src))
        self.stats["company_identifier"]["inserted"] += 1

    def phone(self, table: str, owner_col: str, owner_id: int, raw: str, primary: bool) -> None:
        c = classify_phone(raw)
        key_val = c["e164"] or raw
        ex = self.con.execute("SELECT phone_id FROM %s WHERE %s=? AND COALESCE(phone_e164, phone_raw)=?" % (table, owner_col), (owner_id, key_val)).fetchone()
        if ex:
            self.upsert(table, {"phone_id": ex["phone_id"]}, {"phone_type": c["type"], "country_iso2": c["country"] if c["e164"] else None, "is_placeholder": c["placeholder"]})
            return
        has_primary = self.con.execute("SELECT 1 FROM %s WHERE %s=? AND is_primary=1" % (table, owner_col), (owner_id,)).fetchone()
        self.con.execute("INSERT INTO %s (%s, phone_raw, phone_e164, country_iso2, phone_type, is_primary, is_placeholder, source_system) VALUES (?,?,?,?,?,?,?,?)" % (table, owner_col),
                         (owner_id, raw, c["e164"], c["country"] if c["e164"] else None, c["type"], 0 if has_primary else (1 if primary else 0), c["placeholder"], self.src))
        self.stats[table]["inserted"] += 1
        if not c["e164"]:
            self.dq_counts["unparseable_phone"] += 1

    # ------------------------------------------------------------------------------------------------ contacts
    def do_contacts(self) -> None:
        links = {r[0]: r[1] for r in self.con.execute("SELECT hs_company_id, company_id FROM company_account_link WHERE account_id=?", (self.account,))}
        for rows in self.chunks("contacts"):
            ids = []
            with lgdb.transaction(self.con):
                for r in rows:
                    if self.safe("contact", r["hs_id"], self.contact_row, r, links):
                        ids.append(r["raw_id"])
                self.mark_applied(ids)

    def contact_row(self, r: sqlite3.Row, links: Dict[str, int]) -> None:
        p = json.loads(r["properties_json"])
        hs_id = norm.norm_hs_id(r["hs_id"])
        first, last = nz(p.get("firstname")), nz(p.get("lastname"))
        full = " ".join(x for x in (first, last) if x) or None
        email_raw = nz(p.get("email"))
        li_raw = nz(p.get("hs_linkedin_url")) or nz(p.get("linkedin_url"))
        cid = None
        ac = nz(p.get("associatedcompanyid"))
        if ac:
            try:
                cid = links.get(norm.norm_hs_id(ac))
            except ValueError:
                cid = None
        cid = map_chain(self.con, cid) if cid else None
        attrs = {}
        for k, v in p.items():
            if v in (None, ""):
                continue
            if k in self.custom.get("contacts", set()) and k not in ("linkedin_url", "contact_role", "spoc_type"):
                attrs[k] = v
        for k in ("lifecyclestage", "hs_lead_status", "hubspot_owner_id", "associatedcompanyid", "country", "city", "state", "website", "company"):
            if p.get(k) not in (None, ""):
                attrs[k] = p[k]
        spoc = nz(p.get("spoc_type"))
        dnc = 1 if (str(p.get("hs_email_optout")).lower() == "true" or str(p.get("call_outcome")) == "Do Not Contact") else 0
        id_, _ = self.upsert("contact", {"account_id": self.account, "hs_contact_id": hs_id},
                          {"company_id": cid, "first_name": first, "last_name": last, "full_name": full, "job_title": nz(p.get("jobtitle")),
                           "contact_role": nz(p.get("contact_role")), "spoc_type": spoc if spoc in ("Primary", "Secondary") else None,
                           "email_raw": email_raw, "email_norm": norm.norm_email(email_raw), "linkedin_raw": li_raw,
                           "linkedin_person_norm": norm.norm_linkedin_person(li_raw), "do_not_contact": dnc,
                           "attrs_json": json.dumps(attrs, sort_keys=True), "hs_created_at": r["hs_created_at"], "hs_updated_at": r["hs_updated_at"],
                           "is_archived": r["is_archived"], "source_system": self.src}, id_col="contact_id")
        self.origin("contact", id_, "contacts", hs_id)
        seen = []
        for k in ("phone", "mobilephone"):
            raw = nz(p.get(k))
            if raw:
                c = classify_phone(raw)
                seen.append(c["e164"] or raw)
                self.phone("contact_phone", "contact_id", id_, raw, k == "phone")
        # numbers HubSpot no longer reports for this contact (source = this portal) are dropped
        for ph in self.con.execute("SELECT phone_id, COALESCE(phone_e164, phone_raw) AS k FROM contact_phone WHERE contact_id=? AND source_system=?", (id_, self.src)).fetchall():
            if ph["k"] not in seen:
                self.con.execute("DELETE FROM contact_phone WHERE phone_id=?", (ph["phone_id"],))
                self.stats["contact_phone"]["deleted"] += 1

    # ------------------------------------------------------------------------------------------------ deals
    def stage_index(self) -> Dict[str, Set[str]]:
        idx = defaultdict(set)  # type: Dict[str, Set[str]]
        for r in self.con.execute("SELECT pipeline_id, stage_id FROM stage WHERE account_id=?", (self.account,)):
            idx[r[1]].add(r[0])
        return idx

    @staticmethod
    def pipeline_at(pipe_hist: List[Tuple[str, str]], at: str) -> Optional[str]:
        """The pipeline property value in effect at ``at`` (entries sorted ascending); the first entry covers a stage set a few ms
        before the pipeline property was first written (creation)."""
        cur = None
        for t, v in pipe_hist:
            if t <= at:
                cur = v
            else:
                break
        if cur is None and pipe_hist:
            cur = pipe_hist[0][1]
        return cur

    def resolve_pipeline(self, stage_id: str, at: str, cands: Set[str], pipe_hist: List[Tuple[str, str]], current: Optional[str]) -> Optional[Tuple[str, str]]:
        if len(cands) == 1:
            return next(iter(cands)), "unique_stage_id"
        hp = self.pipeline_at(pipe_hist, at)
        if len(cands) > 1:
            if hp in cands:
                return hp, "pipeline_history"
            if current in cands:
                return current, "deal_current"
            return None
        if hp:
            return hp, "pipeline_history"
        if current:
            return current, "deal_current"
        return None

    @staticmethod
    def hist_entries(history: Optional[Dict[str, Any]], key: str) -> List[Dict[str, Any]]:
        """History entries of one property, oldest first (the API returns newest first; ties keep the API's reversed order)."""
        raw = list(reversed((history or {}).get(key) or []))
        out = []
        for e in raw:
            t = ts(e.get("timestamp"))
            if t:
                out.append(dict(e, _t=t))
        out.sort(key=lambda e: e["_t"])
        return out

    def do_deals(self) -> None:
        cfg = self.load_stage_map()
        declared = {}  # deleted stage id -> pipeline id, from config
        for al in cfg.get("deleted_stages") or []:
            if al["account"] == self.account:
                declared[str(al["id"])] = str(al["pipeline_id"])
        lead_src = {r[1]: r[0] for r in self.con.execute("SELECT lead_source_id, code FROM lead_source")}
        verticals = {r[0]: r[1] for r in self.con.execute("SELECT hubspot_segment, vertical_id FROM vertical WHERE hubspot_segment IS NOT NULL")}
        n_events = n_deals = 0
        skipped = Counter()
        for rows in self.chunks("deals"):
            ids = []
            with lgdb.transaction(self.con):
                # ---- pass 1: stages that only history knows -> tombstones
                idx = self.stage_index()
                pending = defaultdict(Counter)  # stage_id -> Counter(pipeline)
                for r in rows:
                    p = json.loads(r["properties_json"])
                    h = json.loads(r["history_json"]) if r["history_json"] else {}
                    cur = nz(p.get("pipeline"))
                    pipe_hist = [(e["_t"], str(e["value"])) for e in self.hist_entries(h, "pipeline") if e.get("value")]
                    stages = [(e["_t"], str(e["value"])) for e in self.hist_entries(h, "dealstage") if e.get("value")]
                    if nz(p.get("dealstage")):
                        stages.append((r["hs_updated_at"] or now_iso(), str(p["dealstage"])))
                    for t, sid in stages:
                        if sid in idx:
                            continue
                        if sid in declared:
                            pending[sid][declared[sid]] += 1
                            continue
                        res = self.resolve_pipeline(sid, t, set(), pipe_hist, cur)
                        if res:
                            pending[sid][res[0]] += 1
                for sid, cnt in pending.items():
                    pid = cnt.most_common(1)[0][0]
                    if self.ensure_stage(pid, sid, None, "history"):
                        self.dq("stage_seen_only_in_history|%s|%s|%s" % (self.account, pid, sid), "stage_seen_only_in_history", "info", "stage", sid,
                                "stage id %s appears in deal history but not in the pipelines API; tombstone created under pipeline %s (resolved from deal pipeline history / current pipeline)" % (sid, pid),
                                {"pipelines_seen": dict(cnt)})
                idx = self.stage_index()
                # ---- pass 2: deals, events, owner history
                for r in rows:
                    box = []  # type: List[bool]
                    if self.safe("deal", r["hs_id"], lambda rr=r: box.append(self.deal_row(rr, idx, lead_src, verticals, skipped))) and box and box[0]:
                        ids.append(r["raw_id"])
                    n_deals += 1
                self.mark_applied(ids)
        if skipped:
            self.log("deals: skipped/notes %s" % dict(skipped))
        if self.inferred_actors:
            with lgdb.transaction(self.con):
                self.dq("actor_owner_inferred|%s" % self.account, "actor_owner_inferred", "info", "owner", None,
                        "%s: stage-history actors %s are deactivated users (archived owners carry no userId); resolved to the owner with the same numeric id "
                        "(userId == ownerId holds for every owner that exposes both)" % (self.account, sorted(self.inferred_actors)),
                        {"user_ids": dict(self.inferred_actors)})

    def lead_source_id(self, label: Optional[str], cache: Dict[str, int]) -> Optional[int]:
        if not label:
            return None
        code = lead_source_code(label)
        if code not in cache:
            cur = self.con.execute("INSERT INTO lead_source (code, label) VALUES (?,?)", (code, label.strip()))
            cache[code] = cur.lastrowid
            self.stats["lead_source"]["inserted"] += 1
        return cache[code]

    def deal_row(self, r: sqlite3.Row, idx: Dict[str, Set[str]], lead_src: Dict[str, int], verticals: Dict[str, int], skipped: Counter) -> bool:
        p = json.loads(r["properties_json"])
        hist = json.loads(r["history_json"]) if r["history_json"] else None
        hs_id = norm.norm_hs_id(r["hs_id"])
        pipeline, stage = nz(p.get("pipeline")), nz(p.get("dealstage"))
        if not pipeline or not stage or self.con.execute("SELECT 1 FROM stage WHERE account_id=? AND pipeline_id=? AND stage_id=?", (self.account, pipeline, stage)).fetchone() is None:
            if pipeline and stage and self.ensure_stage(pipeline, stage, None, "history"):
                self.dq("stage_seen_only_in_history|%s|%s|%s" % (self.account, pipeline, stage), "stage_seen_only_in_history", "info", "stage", stage,
                        "current stage %s of deal %s is not in pipeline %s per the API; tombstone created" % (stage, hs_id, pipeline))
                idx[stage].add(pipeline)
            else:
                self.dq("deal_stage_unresolved|%s|%s" % (self.account, hs_id), "deal_stage_unresolved", "error", "deal", hs_id,
                        "deal %s has pipeline %r / stage %r that cannot be stored" % (hs_id, pipeline, stage))
                skipped["deal_stage_unresolved"] += 1
                return False
        owner_id = self.ensure_owner(p.get("hubspot_owner_id"))
        seg = nz(p.get("segment")) or nz(p.get("scraped_type")) or nz(p.get("lead_category"))
        ls = nz(p.get("lead_source"))
        lsid = self.lead_source_id(ls, lead_src) if ls else None
        tam = nz(p.get("tam_source")) or nz(p.get("pipeline_source")) or nz(p.get("source_tab"))
        cost, amount = fnum(p.get("cost")), fnum(p.get("amount"))
        cur = nz(p.get("deal_currency_code"))
        cur = cur.upper() if cur and len(cur) == 3 else "USD"
        lh2 = nz(p.get("lh2_domain"))
        li = norm.norm_linkedin_company(p.get("linkedin_url"))
        props = {k: v for k, v in p.items() if v not in (None, "") and k in self.custom.get("deals", set())}
        arch = bool(r["is_archived"])
        archived_at = ts(p.get("_archived_at")) if arch else None
        deal_id, _ = self.upsert("deal", {"account_id": self.account, "hs_deal_id": hs_id},
                              {"pipeline_id": pipeline, "stage_id": stage, "owner_id": owner_id, "dealname": nz(p.get("dealname")), "lead_source_id": lsid,
                               "vertical_id": verticals.get(seg) if seg else None, "segment": seg, "tam_source": tam, "lh2_domain": lh2.lower() if lh2 else None,
                               "linkedin_company_norm": li, "cost_usd": cost if cost is not None and cost >= 0 else None,
                               "amount": amount if amount is not None and amount >= 0 else None, "currency": cur, "is_archived": 1 if arch else 0,
                               "archived_at": archived_at, "hs_created_at": r["hs_created_at"], "hs_updated_at": r["hs_updated_at"],
                               "hs_closed_at": ts_ms(p.get("closedate")), "entered_stage_at": ts_ms(p.get("hs_v2_date_entered_current_stage")),
                               "props_json": json.dumps(props, sort_keys=True)}, id_col="deal_id")
        self.origin("deal", deal_id, "deals", hs_id)
        if hist is None:
            skipped["no_history"] += 1
            self.dq("deal_without_history|%s" % self.account, "deal_without_history", "warn", "deal", hs_id,
                    "%s: deals exist in hsraw without propertiesWithHistory (first example %s); stage events could not be built" % (self.account, hs_id))
            return True
        self.deal_events(deal_id, hs_id, p, hist, idx, skipped)
        self.deal_owner_events(deal_id, hist)
        return True

    def deal_events(self, deal_id: int, hs_id: str, p: Dict[str, Any], hist: Dict[str, Any], idx: Dict[str, Set[str]], skipped: Counter) -> None:
        pipe_hist = [(e["_t"], str(e["value"])) for e in self.hist_entries(hist, "pipeline") if e.get("value")]
        cur = nz(p.get("pipeline"))
        prev = None  # type: Optional[Tuple[str, str]]
        n = 0
        for e in self.hist_entries(hist, "dealstage"):
            sid = nz(e.get("value"))
            if not sid:
                continue
            sid = str(sid)
            res = self.resolve_pipeline(sid, e["_t"], set(idx.get(sid, set())), pipe_hist, cur)
            if res is None:
                skipped["ambiguous_stage_pipeline"] += 1
                self.dq("ambiguous_stage_pipeline|%s|%s|%s" % (self.account, hs_id, sid), "ambiguous_stage_pipeline", "warn", "deal", hs_id,
                        "deal %s: stage %s entered at %s cannot be assigned to a pipeline; event skipped" % (hs_id, sid, e["_t"]))
                continue
            pid, basis = res
            if prev == (pid, sid):
                skipped["repeat_same_stage"] += 1
                continue
            uid = None
            if e.get("updatedByUserId") not in (None, "", 0):
                try:
                    uid = norm.norm_hs_id(e["updatedByUserId"])
                except ValueError:
                    uid = None
            st = nz(e.get("sourceType"))
            self.upsert("deal_stage_event", {"deal_id": deal_id, "entered_at": e["_t"], "pipeline_id": pid, "to_stage_id": sid},
                     {"account_id": self.account, "from_pipeline_id": prev[0] if prev else None, "from_stage_id": prev[1] if prev else None,
                      "source_type": (st or "UNKNOWN").upper(), "source_id": nz(e.get("sourceId")), "actor_user_id": uid,
                      "actor_owner_id": self.actor_owner(uid), "pipeline_basis": basis, "event_origin": "hubspot_history"},
                     on_insert={"import_run_id": self.run_id})
            prev = (pid, sid)
            n += 1

    def deal_owner_events(self, deal_id: int, hist: Dict[str, Any]) -> None:
        for e in self.hist_entries(hist, "hubspot_owner_id"):
            uid = None
            if e.get("updatedByUserId") not in (None, "", 0):
                try:
                    uid = norm.norm_hs_id(e["updatedByUserId"])
                except ValueError:
                    uid = None
            st = nz(e.get("sourceType"))
            self.upsert("deal_owner_event", {"deal_id": deal_id, "assigned_at": e["_t"]},
                     {"account_id": self.account, "owner_id": self.ensure_owner(e.get("value")) if nz(e.get("value")) else None,
                      "source_type": st.upper() if st else None, "actor_user_id": uid, "event_origin": "hubspot_history"},
                     on_insert={"import_run_id": self.run_id})

    # ------------------------------------------------------------------------------------------------ deal associations
    def do_deal_assoc(self) -> None:
        deals = {r[0]: (r[1], r[2]) for r in self.con.execute("SELECT hs_deal_id, deal_id, company_id FROM deal WHERE account_id=?", (self.account,))}
        contacts = {r[0]: r[1] for r in self.con.execute("SELECT hs_contact_id, contact_id FROM contact WHERE account_id=?", (self.account,))}
        links = {r[0]: (r[1], r[2]) for r in self.con.execute("SELECT hs_company_id, link_id, company_id FROM company_account_link WHERE account_id=?", (self.account,))}
        link_company = {v[0]: v[1] for v in links.values()}
        ids = [r[0] for r in self.con.execute("SELECT raw_id FROM hsraw_object WHERE account_id=? AND object_type='deals' AND associations_json IS NOT NULL ORDER BY raw_id", (self.account,))]
        missing = Counter()
        for i in range(0, len(ids), CHUNK):
            rows = self.raw_rows(ids[i:i + CHUNK])
            with lgdb.transaction(self.con):
                for r in rows:
                    d = deals.get(r["hs_id"])
                    if d is None:
                        continue
                    a = json.loads(r["associations_json"])
                    deal_id = d[0]
                    # contacts
                    want = {}
                    for c in a.get("contacts") or []:
                        cid = contacts.get(c["id"])
                        if cid is None:
                            missing["contact"] += 1
                            continue
                        label = next((t["label"] for t in c.get("types") or [] if t.get("label")), None)
                        want[cid] = label
                    have = {x[0]: x[1] for x in self.con.execute("SELECT contact_id, assoc_label FROM deal_contact WHERE deal_id=?", (deal_id,))}
                    for cid in set(have) - set(want):
                        self.con.execute("DELETE FROM deal_contact WHERE deal_id=? AND contact_id=?", (deal_id, cid))
                        self.stats["deal_contact"]["deleted"] += 1
                    for cid, label in want.items():
                        if cid not in have:
                            self.con.execute("INSERT INTO deal_contact (deal_id, contact_id, account_id, is_primary, assoc_label) VALUES (?,?,?,0,?)", (deal_id, cid, self.account, label))
                            self.stats["deal_contact"]["inserted"] += 1
                        elif have[cid] != label:
                            self.con.execute("UPDATE deal_contact SET assoc_label=? WHERE deal_id=? AND contact_id=?", (label, deal_id, cid))
                            self.stats["deal_contact"]["updated"] += 1
                        else:
                            self.stats["deal_contact"]["unchanged"] += 1
                    # companies
                    wl = {}  # link_id -> (is_primary, label)
                    comp = a.get("companies") or []
                    for c in comp:
                        lk = links.get(c["id"])
                        if lk is None:
                            missing["company"] += 1
                            continue
                        types = c.get("types") or []
                        prim = any(t.get("typeId") == 5 or t.get("label") == "Primary" for t in types)
                        wl[lk[0]] = (1 if prim else 0, next((t["label"] for t in types if t.get("label") and t["label"] != "Primary"), None))
                    if wl and not any(v[0] for v in wl.values()) and len(wl) == 1:
                        k = next(iter(wl))
                        wl[k] = (1, wl[k][1])
                    prim_link = next((k for k, v in wl.items() if v[0]), None)
                    prim_company = None
                    if prim_link is not None:
                        prim_company = map_chain(self.con, link_company[prim_link])
                    have_l = {x[0]: (x[1], x[2]) for x in self.con.execute("SELECT link_id, is_primary, assoc_label FROM deal_company WHERE deal_id=?", (deal_id,))}
                    # 1) drop / demote what must not stay primary, 2) set deal.company_id, 3) add / promote
                    for lid in set(have_l) - set(wl):
                        self.con.execute("DELETE FROM deal_company WHERE deal_id=? AND link_id=?", (deal_id, lid))
                        self.stats["deal_company"]["deleted"] += 1
                    for lid, (hp, hl) in have_l.items():
                        if lid in wl and hp and not wl[lid][0]:
                            self.con.execute("UPDATE deal_company SET is_primary=0 WHERE deal_id=? AND link_id=?", (deal_id, lid))
                    if (d[1] or None) != prim_company:
                        if prim_company is None:
                            self.con.execute("UPDATE deal SET company_id=NULL WHERE deal_id=?", (deal_id,))
                        else:
                            self.con.execute("UPDATE deal SET company_id=? WHERE deal_id=?", (prim_company, deal_id))
                        deals[r["hs_id"]] = (deal_id, prim_company)
                        self.stats["deal"]["updated"] += 1
                    for lid, (pm, label) in wl.items():
                        if lid not in have_l:
                            self.con.execute("INSERT INTO deal_company (deal_id, link_id, account_id, is_primary, assoc_label) VALUES (?,?,?,?,?)", (deal_id, lid, self.account, pm, label))
                            self.stats["deal_company"]["inserted"] += 1
                        elif have_l[lid][0] != pm or have_l[lid][1] != label:
                            self.con.execute("UPDATE deal_company SET is_primary=?, assoc_label=? WHERE deal_id=? AND link_id=?", (pm, label, deal_id, lid))
                            self.stats["deal_company"]["updated"] += 1
                        else:
                            self.stats["deal_company"]["unchanged"] += 1
        for k, n in missing.items():
            with lgdb.transaction(self.con):
                self.dq("assoc_target_missing|%s|deal_%s" % (self.account, k), "assoc_target_missing", "info", "deal", None,
                        "%s: %d deal -> %s association(s) point at objects not present in the canonical layer (archived / not synced)" % (self.account, n, k), {"count": n})

    # ------------------------------------------------------------------------------------------------ engagements
    def do_engagements(self) -> None:
        contacts = {r[0]: r[1] for r in self.con.execute("SELECT hs_contact_id, contact_id FROM contact WHERE account_id=?", (self.account,))}
        deals = {r[0]: r[1] for r in self.con.execute("SELECT hs_deal_id, deal_id FROM deal WHERE account_id=?", (self.account,))}
        links = {r[0]: r[1] for r in self.con.execute("SELECT hs_company_id, link_id FROM company_account_link WHERE account_id=?", (self.account,))}
        for ot, kind in KIND.items():
            for rows in self.chunks(ot):
                ids = []
                with lgdb.transaction(self.con):
                    for r in rows:
                        if self.safe(kind, r["hs_id"], self.engagement_row, r, ot, kind, deals, contacts, links):
                            ids.append(r["raw_id"])
                    self.mark_applied(ids)

    def engagement_row(self, r: sqlite3.Row, ot: str, kind: str, deals: Dict[str, int], contacts: Dict[str, int], links: Dict[str, int]) -> None:
        p = json.loads(r["properties_json"])
        hs_id = norm.norm_hs_id(r["hs_id"])
        occurred = ts_ms(p.get("hs_timestamp")) or ts_ms(p.get("hs_createdate")) or r["hs_created_at"]
        if not occurred:
            self.dq("engagement_no_time|%s|%s" % (self.account, ot), "engagement_no_time", "warn", "engagement", hs_id, "%s %s has no timestamp; skipped" % (ot, hs_id))
            return
        body = html_to_text(p.get(ENGAGEMENT_BODY[ot]))
        subj_key = ENGAGEMENT_SUBJECT.get(ot)
        eid, _ = self.upsert("engagement", {"account_id": self.account, "kind": kind, "hs_engagement_id": hs_id},
                          {"owner_id": self.ensure_owner(p.get("hubspot_owner_id")), "occurred_at": occurred,
                           "subject": nz(p.get(subj_key)) if subj_key else None, "body_text": body,
                           "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest() if body else None,
                           "status": nz(p.get(ENGAGEMENT_STATUS[ot])) if ot in ENGAGEMENT_STATUS else None,
                           "is_archived": r["is_archived"], "hs_created_at": r["hs_created_at"], "hs_updated_at": r["hs_updated_at"]}, id_col="engagement_id")
        self.origin("engagement", eid, ot, hs_id)
        a = json.loads(r["associations_json"]) if r["associations_json"] else None
        if a is None:
            return
        want = set()
        for t, m, col in (("deals", deals, "deal_id"), ("contacts", contacts, "contact_id"), ("companies", links, "link_id")):
            for x in a.get(t) or []:
                if x["id"] in m:
                    want.add((col, m[x["id"]]))
        have = {(c, v) for c in ("deal_id", "contact_id", "link_id")
                for (v,) in self.con.execute("SELECT %s FROM engagement_assoc WHERE engagement_id=? AND %s IS NOT NULL" % (c, c), (eid,))}
        for col, v in have - want:
            self.con.execute("DELETE FROM engagement_assoc WHERE engagement_id=? AND %s=?" % col, (eid, v))
            self.stats["engagement_assoc"]["deleted"] += 1
        for col, v in want - have:
            self.con.execute("INSERT INTO engagement_assoc (engagement_id, account_id, %s) VALUES (?,?,?)" % col, (eid, self.account, v))
            self.stats["engagement_assoc"]["inserted"] += 1


def _same(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return float(a) == float(b)
    return a == b


# ---------------------------------------------------------------------------------------------- CLI
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m leadgen.hubspot.canonicalize", description="hsraw_* -> canonical tables (idempotent)")
    ap.add_argument("--account", default="all", help="main | companyops | rat | all (default)")
    ap.add_argument("--all", action="store_true", help="re-project every raw row, not only the changed ones")
    ap.add_argument("--db", default=None)
    args = ap.parse_args(argv)
    accounts = sorted(lgconfig.get_accounts()) if args.account == "all" else [args.account]
    con = lgdb.connect(args.db, must_exist=True)
    try:
        for a in accounts:
            res = Canon(a, con, all_rows=args.all).run()
            if res["unmapped"]:
                print("[%s] UNMAPPED stages: %s" % (a, json.dumps(res["unmapped"])), flush=True)
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
