"""Legacy importer for the eight SQLite databases of the ``hubspot`` repo (legacy/hubspot/**).

Two independent stages per database (``--only-verbatim`` / ``--only-canonical``), both idempotent and resumable.

VERBATIM  every table of every source database -> the matching ``legacy_<db>_<table>`` mirror (migrations 0007-0014), same columns and
          values, plus ``_import_run_id`` (-> ``ops_import_run``) and the SOURCE ROWID copied explicitly (so PK-less tables keep their
          identity: ``origin_ref.source_pk`` = rowid).  Sources are opened ``mode=ro&immutable=1``; rows are streamed in rowid order
          with keyset pagination and committed in batches.  Foreign keys are OFF for the load (set outside a transaction) because the
          sources themselves contain orphans (radar ``candidate_entity``: 2 rows); every ``PRAGMA foreign_key_check`` hit is loaded
          anyway and recorded as ``ops_dq_issue(rule_code='legacy_fk_orphan')``.  Idempotency / resume key: one ``ops_import_run`` per
          (source_name, sha256 of the source file).  A succeeded run whose per-table row counts and per-column non-NULL counts still
          match is reported as "already imported" and nothing is written; an unfinished run is resumed from ``max(rowid)`` of each
          table.  Views (corpus ``v_*``) are not data: they are recreated by the migration over the mirrored tables and verified by
          comparing row counts with the source views.  Virtual / FTS tables (none today) would be skipped and reported.

CANONICAL companies/entities -> ``company`` + ``company_identifier`` (root_domain, linkedin_company, cin/llpin, google_place_id,
          apollo_org, companies_house; normalised with ``leadgen.norm``).  A record is auto-linked to an existing golden company ONLY
          through a strong identifier; a key that several distinct source entities claim (>= 3 in one source, e.g. hyatt.com x 13) or a
          generic host is stored as a WEAK identifier and never links.  Records whose strong keys point at two different golden
          companies are linked by the highest-priority key and queued as ``merge_candidate(match_kind='identifier_conflict')``.  Name-only
          matches across sources become ``merge_candidate`` rows with a score and evidence - never merged.  People (pipeline.people,
          resolver founders, corpus.contact) -> ``contact`` + ``contact_phone`` (E.164, phone_type, +91 gate); company phones (itsvc
          candidates, Google Places) -> ``company_phone``; scores / classifications -> ``tam_company_verdict`` (placeholders are NOT
          materialised); radar distress tiers, corpus indicators, liveness, prequal -> ``company_signal``; quota / search / maps usage
          -> ``cost_ledger`` (usd NULL = unknown, never 0); suppression tables -> ``suppression``.  Every canonical row except
          ``company_signal`` (provenance = source_system + import_run_id) gets an ``origin_ref``.

Run (see docs/IMPORT_HUBSPOT_REPO_DBS.md)::

    .venv/bin/python -m leadgen.legacy_import.hubspot_repo_dbs [--db PATH] [--only itsvc,corpus,...] [--only-verbatim | --only-canonical]
                                                               [--dry-run] [--limit N] [--batch N] [--backup]

``--dry-run`` writes nothing: verbatim = plan (source vs loaded counts, schema compatibility); canonical = the whole mapping executed
inside one transaction that is rolled back (``--limit N`` restricts every source table to its first N rows).
Logs carry counts and internal ids only - never names, phones, e-mails or URLs.  Python 3.9 compatible.
"""
import argparse
import collections
import datetime
import hashlib
import json
import logging
import os
import re
import sqlite3
import sys
import time
import unicodedata
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from leadgen import db, norm

try:  # optional: real number classification when installed (requirements.txt does not pull it in)
    import phonenumbers  # type: ignore
except Exception:  # pragma: no cover - environment dependent
    phonenumbers = None

log = logging.getLogger("leadgen.legacy_import.hubspot_repo_dbs")

TOOL_VERSION = "import_hubspot_repo_dbs/1.0"
HUBSPOT_REPO_REL = os.path.join("legacy", "hubspot")
FETCH_ROWS = 2000
DEFAULT_BATCH_ROWS = 20000
BATCH_BYTES = 24 * 1024 * 1024
SHARED_THRESHOLD = 3          # >= this many distinct source entities claiming one key in ONE source -> the key is weak (shared)
NAME_GROUP_MAX = 6            # name-only groups larger than this are not queued (too generic)


class Source(object):
    def __init__(self, name: str, rel: str, purpose: str) -> None:
        self.name = name
        self.rel = rel
        self.purpose = purpose
        self.prefix = "legacy_%s_" % name


#: processing order = canonical order (earlier sources create the golden rows later ones link to)
SOURCES = [
    Source("itsvc", "itsvc-tam/itsvc.db", "India IT-services universe (Apollo org search)"),
    Source("corpus", "TAMBuildSpecs/_corpus/data/tam_corpus.sqlite", "multi-vertical corpus (uk_proptech, india_cobol_ip)"),
    Source("pipeline", "lh2-pipeline/data/pipeline.sqlite", "lh2 pipeline: goodfirms companies, SignalHire people, quotas"),
    Source("gmaps", "lh2-pipeline/data/gmaps_cache.sqlite", "Google Places tile cache + monthly usage"),
    Source("resolver", "godown/founder_id/resolver.sqlite", "founder / CIN resolver"),
    Source("radar", "godown/shutdown-radar/data/radar.sqlite", "Indian startup shutdown radar"),
    Source("searchledger", "godown/founder_id/search_ledger.db", "SignalHire searchByQuery call ledger"),
    Source("itdirs", "godown/itdirs/state/scrape_state.sqlite", "IT directory scraper state"),
]
SOURCE_BY_NAME = {s.name: s for s in SOURCES}

#: hosts that are never a company's own domain (stored as weak evidence at most)
GENERIC_DOMAINS = frozenset("""gmail.com yahoo.com yahoo.in outlook.com hotmail.com live.com rediffmail.com icloud.com proton.me aol.com
 linkedin.com facebook.com instagram.com twitter.com x.com youtube.com github.com gitlab.com medium.com wordpress.com blogspot.com wixsite.com
 weebly.com squarespace.com godaddysites.com business.site google.com play.google.com apps.apple.com goodfirms.co clutch.co justdial.com
 indiamart.com tracxn.com crunchbase.com zaubacorp.com""".split())

#: strong identifier types in link-priority order
PRIORITY = ["cin", "llpin", "companies_house", "google_place_id", "apollo_org", "linkedin_company", "root_domain"]
LINK_CONF = {"cin": 0.99, "llpin": 0.99, "companies_house": 0.99, "google_place_id": 0.99, "apollo_org": 0.98, "linkedin_company": 0.95,
             "root_domain": 0.9}


class ImportFailure(RuntimeError):
    """Pre-flight failure or parity violation."""


# ====================================================================================================== small helpers
def _qi(name: str) -> str:
    return '"%s"' % name.replace('"', '""')


def _s(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _num(v: Any) -> Optional[float]:
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None


def _int(v: Any) -> Optional[int]:
    f = _num(v)
    return None if f is None else int(f)


def _ts(v: Any) -> Optional[str]:
    try:
        return norm.to_utc_ms(v) if v not in (None, "") else None
    except Exception:
        return None


def _jd(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)


def _jl(v: Any) -> Any:
    """Parse a JSON text column; None / '' / invalid -> None."""
    if v is None or v == "":
        return None
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return None


def _date(v: Any) -> Optional[str]:
    s = _s(v)
    if not s or len(s) < 10:
        return None
    s = s[:10]
    try:
        datetime.datetime.strptime(s, "%Y-%m-%d")
        return s
    except ValueError:
        return None


def _year(v: Any) -> Optional[int]:
    y = _int(v)
    return y if y is not None and 1800 <= y <= 2100 else None


def _rel(path: str) -> str:
    try:
        r = os.path.relpath(path, db.PROJECT_ROOT)
        return r if not r.startswith("..") else path
    except ValueError:
        return path


def open_source(path: str) -> sqlite3.Connection:
    if not os.path.exists(path):
        raise ImportFailure("source database not found: %s" % path)
    con = sqlite3.connect("file:%s?mode=ro&immutable=1" % path, uri=True)
    con.execute("PRAGMA query_only = ON")
    return con


def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def source_tables(src: sqlite3.Connection) -> Tuple[List[Tuple[str, List[str]]], List[str], List[str]]:
    """([(table, [columns])], [views], [skipped virtual / shadow tables])."""
    tables, views, skipped = [], [], []
    rows = src.execute("SELECT type, name, sql FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
    virtual = [n for t, n, sql in rows if t == "table" and sql and sql.upper().startswith("CREATE VIRTUAL")]
    for t, n, sql in rows:
        if t == "view":
            views.append(n)
        elif n in virtual or any(n.startswith(v + "_") for v in virtual):
            skipped.append(n)
        else:
            tables.append((n, [r[1] for r in src.execute("PRAGMA table_info(%s)" % _qi(n))]))
    return tables, views, skipped


def _load_order(con: sqlite3.Connection, names: List[str], prefix: str) -> List[str]:
    """Parents before children (Kahn) using the FK graph of the target mirrors; ties alphabetical."""
    deps = {}
    for n in names:
        parents = set()
        for fk in con.execute("PRAGMA foreign_key_list(%s)" % _qi(prefix + n)).fetchall():
            p = fk[2]
            if p.startswith(prefix) and p[len(prefix):] in names and p[len(prefix):] != n:
                parents.add(p[len(prefix):])
        deps[n] = parents
    out, done = [], set()
    while len(out) < len(names):
        ready = sorted(n for n in names if n not in done and deps[n] <= done)
        if not ready:  # cycle: fall back to alphabetical
            ready = sorted(n for n in names if n not in done)[:1]
        for n in ready:
            out.append(n)
            done.add(n)
    return out


class Tally(object):
    """Counts + a few internal ids per (rule, source); flushed to ops_dq_issue once per run (never one row per source row)."""

    def __init__(self) -> None:
        self.count = collections.Counter()  # type: collections.Counter
        self.examples = collections.defaultdict(list)  # type: Dict[Tuple[str, str], List[str]]
        self.meta = {}  # type: Dict[str, Tuple[str, str]]

    def add(self, rule: str, source: str, ref: Any = None, severity: str = "warn", message: Optional[str] = None) -> None:
        k = (rule, source)
        self.count[k] += 1
        self.meta.setdefault(rule, (severity, message or rule))
        if ref is not None and len(self.examples[k]) < 5:
            self.examples[k].append(str(ref))

    def flush(self, con: sqlite3.Connection, run_id: Optional[int]) -> int:
        n = 0
        now = db.utc_now_iso()
        for (rule, source), cnt in sorted(self.count.items()):
            sev, msg = self.meta[rule]
            con.execute(
                "INSERT INTO ops_dq_issue (fingerprint, rule_code, severity, entity_type, entity_ref, message, details_json, occurrences, "
                " first_seen_at, last_seen_at, import_run_id) VALUES (?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(fingerprint) DO UPDATE SET occurrences = MAX(occurrences, excluded.occurrences), last_seen_at = excluded.last_seen_at, "
                " details_json = CASE WHEN excluded.occurrences >= occurrences THEN excluded.details_json ELSE details_json END, "
                " import_run_id = excluded.import_run_id",
                ("%s|hubspot_repo|%s" % (rule, source), rule, sev, "source", source, "%s (%s): %d row(s)" % (msg, source, cnt),
                 _jd({"count": cnt, "example_source_pks": self.examples[(rule, source)]}), cnt, now, now, run_id))
            n += 1
        self.count.clear()
        self.examples.clear()
        return n


# ====================================================================================================== phones
#: 3-digit STD codes starting 6/7/8 that belong to fixed lines (the 6-8 mobile series overlap them); 79 (Ahmedabad) and 80 (Bangalore) are 2-digit
_AMBIG_STD3 = frozenset("""612 621 631 641 651 661 671 674 680 712 721 731 734 741 751 755 761 771 788 821 824 831 832 836 861 863 866 870 877 884
 891""".split())


def classify_phone(e164: Optional[str]) -> Tuple[str, Optional[str]]:
    """(phone_type, country_iso2) for a validated E.164 string.

    With ``phonenumbers`` installed its number type is used.  Without it a deterministic, deliberately conservative fallback applies for
    +91 numbers: national 9xxxxxxxxx -> mobile; 6/7/8xxxxxxxxx -> mobile unless it starts with a fixed-line STD code that overlaps the mobile
    series (79, 80, a short list of 3-digit codes) -> unknown; 1-5xxxxxxxxx -> landline.  Everything that is not +91 is 'unknown' (the gate is
    +91 only).  The gate fails closed: 'unknown' never opens it and shows up in v_phone_gate_review."""
    if not e164:
        return "unknown", None
    country = "IN" if e164.startswith("+91") else None
    if phonenumbers is not None:  # pragma: no cover - not installed here
        try:
            n = phonenumbers.parse(e164, None)
            t = phonenumbers.number_type(n)
            P = phonenumbers.PhoneNumberType
            kind = {P.MOBILE: "mobile", P.FIXED_LINE: "landline", P.TOLL_FREE: "tollfree", P.VOIP: "voip"}.get(t, "unknown")
            return kind, country
        except Exception:
            return "unknown", country
    if not e164.startswith("+91"):
        return "unknown", None
    nat = e164[3:]
    if len(nat) != 10 or not nat.isdigit():
        return "unknown", "IN"
    d = nat[0]
    if d == "9":
        return "mobile", "IN"
    if d in "678":
        if nat[:2] in ("79", "80") or nat[:3] in _AMBIG_STD3:
            return "unknown", "IN"
        return "mobile", "IN"
    if d in "12345":
        return "landline", "IN"
    return "unknown", "IN"


def is_dummy_phone(raw: Optional[str]) -> bool:
    """Obvious placeholder numbers ('+91 12345 67890', '0123456789', 9999999999 ...)."""
    digits = re.sub(r"\D", "", raw or "")
    nat = digits[-10:] if len(digits) >= 10 else digits
    if len(nat) < 7:
        return True
    return len(set(nat)) == 1 or nat in ("1234567890", "0123456789", "9876543210", "0987654321") or bool(re.match(r"^(0?1234567|9876543)", nat))


def phone_fields(raw: Any) -> Optional[Dict[str, Any]]:
    """Normalise one raw number -> column values for contact_phone / company_phone, or None when there is nothing to store."""
    r = _s(raw)
    if not r:
        return None
    e164 = norm.norm_phone_e164(r)
    dummy = is_dummy_phone(r)
    ptype, country = classify_phone(e164) if not dummy else ("unknown", None)
    if e164 and country is None and e164.startswith("+91"):
        country = "IN"
    return {"raw": r, "e164": e164, "type": ptype, "country": country if e164 else None, "placeholder": 1 if dummy else 0}


# ====================================================================================================== names / merge candidates
_LEGAL_TOKENS = frozenset("ltd limited pvt private llp inc incorporated plc corp corporation llc co company gmbh pte opc lp sa bv ag the and".split())


def name_key(name_norm: str) -> str:
    s = unicodedata.normalize("NFKD", name_norm or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    toks = [t for t in re.findall(r"[a-z0-9]+", s.lower()) if t not in _LEGAL_TOKENS]
    return " ".join(toks)


_STATUS_RULES = (("active - proposal to strike off", "active"), ("live but receiver", "active"), ("voluntary arrangement", "active"),
                 ("administrative receiver", "administration"), ("in administration", "administration"), ("receivership", "administration"),
                 ("administration", "administration"), ("liquidation", "liquidation"), ("struck off", "struck_off"), ("strike off", "struck_off"),
                 ("dissolved", "dissolved"), ("amalgamated", "acquired"), ("active", "active"))


def map_status(raw: Any) -> str:
    s = (_s(raw) or "").lower()
    if not s:
        return "unknown"
    for needle, code in _STATUS_RULES:
        if needle in s:
            return code
    return "unknown"


# ====================================================================================================== engine
class Key(object):
    __slots__ = ("type", "norm", "raw", "strong", "conf", "primary")

    def __init__(self, type_: str, norm_: str, raw: Any, strong: bool = True, conf: float = 1.0, primary: bool = False) -> None:
        self.type, self.norm, self.raw, self.strong, self.conf, self.primary = type_, norm_, _s(raw), strong, conf, primary


class Engine(object):
    """Holds the caches and the insert helpers of the canonical stage.  One engine per invocation, ``begin_source`` per database."""

    def __init__(self, con: sqlite3.Connection, tally: Tally, dry: bool = False, limit: Optional[int] = None, batch: int = 2000) -> None:
        self.con, self.tally, self.dry, self.limit, self.batch = con, tally, dry, limit, batch
        self.system = ""
        self.run_id = None  # type: Optional[int]
        self.stats = collections.Counter()  # type: collections.Counter
        self._undo = []  # type: List[Tuple[dict, Any, bool, Any]]
        self._omaps = {}  # type: Dict[Tuple[str, str, str], Dict[str, int]]
        self.shared = collections.defaultdict(set)  # type: Dict[str, Set[str]]
        self._verticals = {}  # type: Dict[str, int]
        self._cur_verdict = None  # type: Optional[Dict[Tuple[int, int], Tuple[int, str]]]
        self._contacts = None  # type: Optional[Dict[Tuple, int]]
        self._primary_seen = {}  # type: Dict[Tuple[int, str], int]
        t = time.time()
        self.ident = {}  # type: Dict[Tuple[str, str], int]
        for r in con.execute("SELECT identifier_type, value_norm, company_id FROM company_identifier WHERE is_strong = 1"):
            self.ident[(r[0], r[1])] = r[2]
        log.info("engine: %d strong identifiers preloaded (%.1fs)", len(self.ident), time.time() - t)

    # ---------------------------------------------------------------- bookkeeping
    def begin_source(self, system: str, run_id: Optional[int]) -> None:
        self.system, self.run_id = system, run_id
        self.shared = collections.defaultdict(set)

    def stat(self, name: str, n: int = 1) -> None:
        self.stats["%s.%s" % (self.system, name)] += n

    def _cset(self, d: dict, k: Any, v: Any) -> None:
        """Cache write that a rejected row (ROLLBACK TO savepoint) can undo, restoring the previous value."""
        self._undo.append((d, k, k in d, d.get(k)))
        d[k] = v

    def _rollback_caches(self) -> None:
        for d, k, had, old in reversed(self._undo):
            if had:
                d[k] = old
            else:
                d.pop(k, None)
        self._undo = []

    def omap(self, etype: str, table: str, system: Optional[str] = None) -> Dict[str, int]:
        key = (etype, system or self.system, table)
        m = self._omaps.get(key)
        if m is None:
            m = {}
            for r in self.con.execute("SELECT source_pk, entity_id FROM origin_ref WHERE entity_type = ? AND source_system = ? AND source_table = ?", key):
                m[r[0]] = r[1]
            self._omaps[key] = m
        return m

    def origin(self, etype: str, eid: int, table: str, pk: Any, method: Optional[str], conf: Optional[float]) -> None:
        self.con.execute(
            "INSERT INTO origin_ref (entity_type, entity_id, source_system, source_table, source_pk, match_method, confidence, import_run_id) "
            "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING", (etype, eid, self.system, table, str(pk), method, conf, self.run_id))
        m = self._omaps.get((etype, self.system, table))
        if m is not None:  # a map that was never read is not loaded just to be updated (it would be read fresh from the table)
            self._cset(m, str(pk), eid)

    # ---------------------------------------------------------------- per-source driver
    def rows(self, label: str, cursor: Iterable[Sequence[Any]], handler: Callable[[Sequence[Any]], None], total: Optional[int] = None) -> int:
        """Run ``handler`` over the rows: batches of ``self.batch`` per transaction, one SAVEPOINT per row (an IntegrityError rejects only that row)."""
        n = 0
        t0 = time.time()
        buf = []  # type: List[Sequence[Any]]

        def flush() -> None:
            if not buf:
                return
            if self.dry:
                for r in buf:
                    self._one(handler, r, label)
            else:
                with db.transaction(self.con):
                    for r in buf:
                        self._one(handler, r, label)
            buf.clear()

        for row in cursor:
            buf.append(row)
            n += 1
            if len(buf) >= self.batch:
                flush()
                if n % (self.batch * 10) == 0:
                    log.info("[%s] %-18s %9d%s rows (%.0f rows/s)", self.system, label, n, "/%d" % total if total else "", n / max(time.time() - t0, 1e-6))
        flush()
        log.info("[%s] %-18s %9d rows done in %.1fs", self.system, label, n, time.time() - t0)
        self.stat("rows_read.%s" % label, n)
        return n

    def _one(self, handler: Callable[[Sequence[Any]], None], row: Sequence[Any], label: str) -> None:
        self.con.execute("SAVEPOINT r")
        try:
            handler(row)
            self.con.execute("RELEASE r")
            self._undo = []
        except sqlite3.IntegrityError as exc:
            self.con.execute("ROLLBACK TO r")
            self.con.execute("RELEASE r")
            self._rollback_caches()
            self.stat("rows_rejected.%s" % label)
            self.tally.add("canonical_row_rejected", self.system, "%s:%s" % (label, row[0]), "error", "row rejected by a canonical constraint")
            log.debug("rejected %s row: %s", label, str(exc)[:120])

    # ---------------------------------------------------------------- verticals
    def vertical(self, slug: str, name: str, region: Optional[str] = None, country: Optional[str] = None, origin: Optional[Tuple[str, Any]] = None) -> int:
        vid = self._verticals.get(slug)
        if vid is None:
            self.con.execute("INSERT INTO vertical (slug, name, region, country_code) VALUES (?,?,?,?) ON CONFLICT(slug) DO NOTHING", (slug, name, region, country))
            vid = self.con.execute("SELECT vertical_id FROM vertical WHERE slug = ?", (slug,)).fetchone()[0]
            self._cset(self._verticals, slug, vid)
        if origin:
            self.origin("vertical", vid, origin[0], origin[1], "slug", 1.0)
        return vid

    # ---------------------------------------------------------------- company
    def keys(self, source_pk: Any, domain: Any = None, linkedin: Any = None, cin: Any = None, extra: Sequence[Key] = (),
             domain_conf: float = 1.0) -> List[Key]:
        out = []  # type: List[Key]
        seen = set()  # type: Set[Tuple[str, str]]

        def add(k: Key) -> None:
            if (k.type, k.norm) not in seen:
                seen.add((k.type, k.norm))
                out.append(k)

        if _s(domain):
            d = norm.norm_domain(domain)
            if d is None:
                self.tally.add("identifier_unnormalisable", self.system, source_pk, "warn", "a domain could not be normalised (kept out of company_identifier)")
            else:
                strong = d not in GENERIC_DOMAINS and d not in self.shared["root_domain"] and domain_conf >= 0.9
                add(Key("root_domain", d, domain, strong, domain_conf, primary=strong))
        if _s(linkedin):
            li = norm.norm_linkedin_company(linkedin)
            if li is None:
                self.tally.add("identifier_unnormalisable", self.system, source_pk, "warn", "a linkedin company url could not be normalised (kept out of company_identifier)")
            else:
                add(Key("linkedin_company", li, linkedin, li not in self.shared["linkedin_company"], 1.0, primary=li not in self.shared["linkedin_company"]))
        if _s(cin):
            c = norm.norm_cin(cin)
            l = norm.norm_llpin(cin) if c is None else None
            if c:
                add(Key("cin", c, cin, True, 1.0, True))
            elif l:
                add(Key("llpin", l, cin, True, 1.0, True))
            else:
                self.tally.add("identifier_unnormalisable", self.system, source_pk, "warn", "a CIN/LLPIN could not be normalised (kept out of company_identifier)")
        for k in extra:
            add(k)
        return out

    def _merge_candidate(self, a: int, b: int, kind: str, score: float, evidence: Dict[str, Any], entity: str = "company") -> None:
        if a == b:
            return
        l, r = (a, b) if a < b else (b, a)
        cur = self.con.execute(
            "INSERT INTO merge_candidate (entity_type, left_id, right_id, match_kind, score, evidence_json, import_run_id) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(entity_type, left_id, right_id, match_kind) DO NOTHING", (entity, l, r, kind, score, _jd(evidence), self.run_id))
        if cur.rowcount:
            self.stat("merge_candidate.%s" % kind)

    def upsert_company(self, table: str, pk: Any, name: Any, keys: List[Key], fields: Dict[str, Any], first_seen: Optional[str] = None) -> Optional[Tuple[int, str, bool]]:
        """Idempotent: (company_id, how, created).  how = origin | new | <identifier type that linked it>.  None when the row has no usable name."""
        pk = str(pk)
        om = self.omap("company", table)
        cid = om.get(pk)
        if cid is not None:
            self.stat("company.skipped_existing")
            return cid, "origin", False
        cname = _s(name)
        nn = norm.norm_company_name(cname) if cname else None
        if not nn:
            self.tally.add("company_without_name", self.system, "%s:%s" % (table, pk), "warn", "source entity has no usable name; not imported to company")
            return None
        # ---- resolve through strong keys
        hits = []  # type: List[Tuple[int, str, int]]
        for k in keys:
            if k.strong:
                owner = self.ident.get((k.type, k.norm))
                if owner is not None:
                    hits.append((PRIORITY.index(k.type) if k.type in PRIORITY else 99, k.type, owner))
        hits.sort()
        created = False
        if not hits:
            cid, how = self._insert_company(cname, nn, fields, first_seen), "new"
            created = True
            self.stat("company.created")
        else:
            cid, how = hits[0][2], hits[0][1]
            self._fill_company(cid, fields)
            self.stat("company.linked.%s" % how)
            others = sorted(set(h[2] for h in hits if h[2] != cid))
            for o in others:
                self._merge_candidate(cid, o, "identifier_conflict", 0.9,
                                      {"source": self.system, "source_table": table, "source_pk": pk, "keys": [h[1] for h in hits]})
        self.origin("company", cid, table, pk, how, 1.0 if created else LINK_CONF.get(how, 0.9))
        for k in keys:
            self._ensure_ident(cid, k, created, table, pk)
        return cid, how, created

    def _insert_company(self, cname: str, nn: str, f: Dict[str, Any], first_seen: Optional[str]) -> int:
        now = db.utc_now_iso()
        cur = self.con.execute(
            "INSERT INTO company (canonical_name, name_norm, legal_name, website, hq_city, hq_state, hq_country, india_hq, employee_count, headcount_band, "
            " founded_year, funding_stage, total_funding_usd, status, status_detail, status_as_of, first_seen_at, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (cname, nn, f.get("legal_name"), f.get("website"), f.get("hq_city"), f.get("hq_state"), f.get("hq_country"), f.get("india_hq") or "unknown",
             f.get("employee_count"), f.get("headcount_band"), f.get("founded_year"), f.get("funding_stage"), f.get("total_funding_usd"),
             f.get("status") or "unknown", f.get("status_detail"), f.get("status_as_of"), first_seen or now, now, now))
        return cur.lastrowid

    def _fill_company(self, cid: int, f: Dict[str, Any]) -> None:
        """Fill NULL golden fields from a linked record; never overwrite."""
        cols = [c for c in ("legal_name", "website", "hq_city", "hq_state", "hq_country", "employee_count", "headcount_band", "founded_year",
                            "funding_stage", "total_funding_usd", "status_detail", "status_as_of") if f.get(c) is not None]
        sets = ["%s = COALESCE(%s, ?)" % (c, c) for c in cols]
        args = [f[c] for c in cols]
        if f.get("india_hq") in ("yes", "no"):
            sets.append("india_hq = CASE WHEN india_hq = 'unknown' THEN ? ELSE india_hq END")
            args.append(f["india_hq"])
        if f.get("status") and f["status"] != "unknown":
            sets.append("status = CASE WHEN status = 'unknown' THEN ? ELSE status END")
            args.append(f["status"])
        if sets:
            self.con.execute("UPDATE company SET %s, updated_at = ? WHERE company_id = ?" % ", ".join(sets), args + [db.utc_now_iso(), cid])

    def _ensure_ident(self, cid: int, k: Key, new_company: bool, table: str, pk: str) -> None:
        iid = None
        if not new_company:
            r = self.con.execute("SELECT identifier_id FROM company_identifier WHERE company_id = ? AND identifier_type = ? AND value_norm = ?", (cid, k.type, k.norm)).fetchone()
            iid = r[0] if r else None
        strong = k.strong
        if iid is None:
            if strong:
                owner = self.ident.get((k.type, k.norm))
                if owner is not None and owner != cid:   # owned by another golden company: keep as weak evidence + queue the pair
                    strong = False
                    self._merge_candidate(cid, owner, "identifier_conflict", 0.9, {"source": self.system, "source_table": table, "source_pk": pk, "key": k.type})
                    self.stat("identifier.conflict")
            primary = 0
            if strong and k.primary and (cid, k.type) not in self._primary_seen and (new_company or not self.con.execute(
                    "SELECT 1 FROM company_identifier WHERE company_id = ? AND identifier_type = ? AND is_primary = 1", (cid, k.type)).fetchone()):
                primary = 1
                self._cset(self._primary_seen, (cid, k.type), 1)
            cur = self.con.execute(
                "INSERT INTO company_identifier (company_id, identifier_type, value_norm, value_raw, confidence, is_strong, is_primary, source_system) "
                "VALUES (?,?,?,?,?,?,?,?)", (cid, k.type, k.norm, k.raw, k.conf, 1 if strong else 0, primary, self.system))
            iid = cur.lastrowid
            if strong:
                self._cset(self.ident, (k.type, k.norm), cid)
            self.stat("identifier.%s.%s" % (k.type, "strong" if strong else "weak"))
        self.origin("company_identifier", iid, table, "%s|%s" % (pk, k.type), "identifier", k.conf)

    # ---------------------------------------------------------------- company phones
    def add_company_phone(self, cid: int, raw: Any, table: str, pk: Any, primary: bool = True) -> None:
        pf = phone_fields(raw)
        if pf is None:
            return
        cur = self.con.execute(
            "INSERT INTO company_phone (company_id, phone_raw, phone_e164, country_iso2, phone_type, is_primary, is_placeholder, source_system) "
            "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
            (cid, pf["raw"], pf["e164"], pf["country"], pf["type"], 0, pf["placeholder"], self.system))
        if cur.rowcount:
            if primary and not pf["placeholder"] and not self.con.execute("SELECT 1 FROM company_phone WHERE company_id = ? AND is_primary = 1", (cid,)).fetchone():
                self.con.execute("UPDATE company_phone SET is_primary = 1 WHERE phone_id = ?", (cur.lastrowid,))
            self.origin("company_phone", cur.lastrowid, table, pk, "phone", 1.0)
            self.stat("company_phone.%s%s" % (pf["type"], ".placeholder" if pf["placeholder"] else ""))
            if pf["e164"] is None:
                self.tally.add("phone_not_e164", self.system, "%s:%s" % (table, pk), "info", "phone kept raw (could not be normalised to E.164)")

    # ---------------------------------------------------------------- verdicts
    def _verdicts(self) -> Dict[Tuple[int, int], Tuple[int, str]]:
        if self._cur_verdict is None:
            self._cur_verdict = {}
            for r in self.con.execute("SELECT company_id, COALESCE(vertical_id, 0), verdict_id, decided_at FROM tam_company_verdict WHERE is_current = 1"):
                self._cur_verdict[(r[0], r[1])] = (r[2], r[3])
        return self._cur_verdict

    def add_verdict(self, cid: int, vertical_id: Optional[int], table: str, pk: Any, bucket: str, method: str, decided_at: Optional[str], **kw: Any) -> Optional[int]:
        om = self.omap("tam_company_verdict", table)
        if str(pk) in om:
            self.stat("verdict.skipped_existing")
            return om[str(pk)]
        decided_at = decided_at or db.utc_now_iso()
        cur_map = self._verdicts()
        key = (cid, vertical_id or 0)
        cur = cur_map.get(key)
        is_current = 1
        if cur is not None:
            if decided_at >= cur[1]:
                self.con.execute("UPDATE tam_company_verdict SET is_current = 0, updated_at = ? WHERE verdict_id = ?", (db.utc_now_iso(), cur[0]))
                self.stat("verdict.superseded_previous")
            else:
                is_current = 0
                self.stat("verdict.stored_as_history")
        details = kw.get("details") or {}
        c = self.con.execute(
            "INSERT INTO tam_company_verdict (company_id, vertical_id, segment, icp_bucket, icp_band, priority_tier, score, confidence, reason_code, reason, "
            " evidence_url, evidence_quote, native_bucket, details_json, verdict_method, model, prompt_version, is_placeholder, is_current, decided_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?)",
            (cid, vertical_id, kw.get("segment"), bucket, kw.get("band"), kw.get("tier"), kw.get("score"), kw.get("confidence"), kw.get("reason_code"),
             kw.get("reason"), kw.get("evidence_url"), kw.get("evidence_quote"), kw.get("native_bucket"), _jd(details), method, kw.get("model"),
             kw.get("prompt_version"), is_current, decided_at))
        vid = c.lastrowid
        if is_current:
            self._cset(cur_map, key, (vid, decided_at))
        self.origin("tam_company_verdict", vid, table, pk, "import", 1.0)
        self.stat("verdict.%s" % bucket)
        return vid

    # ---------------------------------------------------------------- signals
    def add_signal(self, cid: int, code: str, *, value_bool: Optional[int] = None, value_num: Optional[float] = None, value_text: Optional[str] = None,
                   confidence: Optional[float] = None, snippet: Optional[str] = None, url: Optional[str] = None, observed_at: Optional[str] = None) -> bool:
        code = re.sub(r"[^a-z0-9_.]+", "_", (code or "").lower()).strip("_")
        if not code or (value_bool is None and value_num is None and _s(value_text) is None):
            return False
        cur = self.con.execute(
            "INSERT INTO company_signal (company_id, signal_code, value_bool, value_num, value_text, confidence, evidence_snippet, evidence_url, observed_at, "
            " source_system, import_run_id) VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
            (cid, code, value_bool, value_num, _s(value_text), confidence, _s(snippet), _s(url), observed_at, self.system, self.run_id))
        self.stat("signal.inserted" if cur.rowcount else "signal.duplicate_ignored")
        return bool(cur.rowcount)

    # ---------------------------------------------------------------- costs
    def add_cost(self, table: str, pk: Any, vendor: str, unit: str, qty: float, occurred_at: Optional[str], credits: Optional[float] = None,
                 run_ref: Optional[str] = None, note: Optional[str] = None, usd: Optional[float] = None) -> None:
        om = self.omap("cost_ledger", table)
        if str(pk) in om:
            self.stat("cost.skipped_existing")
            return
        if occurred_at is None:
            self.tally.add("cost_without_time", self.system, "%s:%s" % (table, pk), "warn", "cost row without a usable timestamp; not imported to cost_ledger")
            return
        known = usd is not None and usd > 0          # a source 0 is "not recorded", never a price
        cur = self.con.execute(
            "INSERT INTO cost_ledger (vendor, unit, qty, credits, usd, usd_basis, occurred_at, run_ref, note, is_placeholder, source_system) "
            "VALUES (?,?,?,?,?,?,?,?,?,0,?)", (vendor, unit, max(qty, 0.0), credits, usd if known else None, "actual" if known else "unknown", occurred_at,
                                                run_ref, note, self.system))
        self.origin("cost_ledger", cur.lastrowid, table, pk, "import", 1.0)
        self.stat("cost.inserted")
        if not known:
            self.tally.add("cost_usd_unknown", self.system, None, "info", "cost rows loaded with usd NULL / usd_basis unknown (vendor price not recorded in the source)")

    # ---------------------------------------------------------------- contacts
    def _contact_keys(self) -> Dict[Tuple, int]:
        if self._contacts is None:
            self._contacts = {}
            for r in self.con.execute("SELECT contact_id, company_id, linkedin_person_norm, email_norm FROM contact"):
                if r[2]:
                    self._contacts.setdefault(("li", r[1], r[2]), r[0])
                    self._contacts.setdefault(("li*", r[2]), r[0])
                if r[3]:
                    self._contacts.setdefault(("em", r[1], r[3]), r[0])
        return self._contacts

    def add_contact(self, table: str, pk: Any, company_id: Optional[int], *, full_name: Any = None, job_title: Any = None, seniority: Any = None,
                    email: Any = None, email_status: Any = None, linkedin: Any = None, do_not_contact: int = 0, attrs: Optional[Dict[str, Any]] = None,
                    phones: Sequence[Any] = (), first_seen: Optional[str] = None, phone_type_hint: Optional[str] = None) -> Optional[int]:
        om = self.omap("contact", table)
        if str(pk) in om:
            self.stat("contact.skipped_existing")
            return om[str(pk)]
        em, li = norm.norm_email(email), norm.norm_linkedin_person(linkedin)
        if _s(email) and em is None:
            self.tally.add("email_unnormalisable", self.system, "%s:%s" % (table, pk), "info", "an e-mail could not be normalised (raw kept, email_norm NULL)")
        keys = self._contact_keys()
        existing = (keys.get(("li", company_id, li)) if li else None) or (keys.get(("em", company_id, em)) if em else None)
        if existing:
            self.origin("contact", existing, table, pk, "linkedin_person" if li and keys.get(("li", company_id, li)) else "email", 0.95)
            self.stat("contact.linked_existing")
            cid_ = existing
        else:
            other = keys.get(("li*", li)) if li else None
            cur = self.con.execute(
                "INSERT INTO contact (company_id, full_name, job_title, seniority, email_raw, email_norm, email_status, linkedin_raw, linkedin_person_norm, "
                " do_not_contact, attrs_json, source_system) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (company_id, _s(full_name), _s(job_title), _s(seniority), _s(email), em, _s(email_status), _s(linkedin), li, 1 if do_not_contact else 0,
                 _jd(attrs or {}), self.system))
            cid_ = cur.lastrowid
            if li:
                self._cset(keys, ("li", company_id, li), cid_)
                self._cset(keys, ("li*", li), keys.get(("li*", li), cid_))
            if em:
                self._cset(keys, ("em", company_id, em), cid_)
            self.origin("contact", cid_, table, pk, "new", 1.0)
            self.stat("contact.created")
            if other and other != cid_:
                self._merge_candidate(other, cid_, "linkedin_person", 0.9, {"source": self.system, "source_table": table}, entity="contact")
        first = True
        for raw in phones:
            pf = phone_fields(raw)
            if pf is None:
                continue
            ptype = pf["type"]
            if phone_type_hint in ("mobile", "landline", "voip", "tollfree") and pf["e164"] and not pf["placeholder"] and pf["e164"].startswith("+91"):
                ptype = phone_type_hint
            cur = self.con.execute(
                "INSERT INTO contact_phone (contact_id, phone_raw, phone_e164, country_iso2, phone_type, is_primary, is_placeholder, source_system) "
                "VALUES (?,?,?,?,?,0,?,?) ON CONFLICT DO NOTHING", (cid_, pf["raw"], pf["e164"], pf["country"], ptype, pf["placeholder"], self.system))
            if cur.rowcount:
                if first and not pf["placeholder"] and not self.con.execute("SELECT 1 FROM contact_phone WHERE contact_id = ? AND is_primary = 1", (cid_,)).fetchone():
                    self.con.execute("UPDATE contact_phone SET is_primary = 1 WHERE phone_id = ?", (cur.lastrowid,))
                first = False
                self.origin("contact_phone", cur.lastrowid, table, pk, "phone", 1.0)
                self.stat("contact_phone.%s%s" % (ptype, ".placeholder" if pf["placeholder"] else ""))
                if pf["e164"] is None:
                    self.tally.add("phone_not_e164", self.system, "%s:%s" % (table, pk), "info", "phone kept raw (could not be normalised to E.164)")
        return cid_

    # ---------------------------------------------------------------- suppression
    def add_suppression(self, table: str, pk: Any, kind: str, match_type: str, value: Any, company_id: Optional[int], reason: Any, list_name: str,
                        added_at: Optional[str]) -> None:
        fn = {"domain": norm.norm_domain, "email": norm.norm_email, "phone": norm.norm_phone_e164, "linkedin_company": norm.norm_linkedin_company,
              "linkedin_person": norm.norm_linkedin_person, "cin": norm.norm_cin, "company_name": norm.norm_company_name}.get(match_type)
        v = fn(value) if fn else (str(company_id) if match_type == "company_id" and company_id else None)
        pk_s = "%s|%s" % (pk, match_type)
        if v is None:
            self.tally.add("suppression_unnormalisable", self.system, "%s:%s" % (table, pk_s), "error",
                           "a suppression value could not be normalised: NOT imported (review manually: fail-closed lists must be complete)")
            return
        om = self.omap("suppression", table)
        if pk_s in om:
            return
        at = added_at or db.utc_now_iso()
        self.con.execute(
            "INSERT INTO suppression (kind, match_type, match_value_norm, company_id, reason, list_name, added_at) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT DO NOTHING", (kind, match_type, v, company_id, _s(reason), list_name, at))
        r = self.con.execute("SELECT suppression_id FROM suppression WHERE kind = ? AND match_type = ? AND match_value_norm = ? AND COALESCE(account_id,'') = ''",
                             (kind, match_type, v)).fetchone()
        self.origin("suppression", r[0], table, pk_s, "import", 1.0)
        self.stat("suppression.inserted")


def scan_shared(values: Iterable[Optional[str]]) -> Set[str]:
    c = collections.Counter(v for v in values if v)
    return set(v for v, n in c.items() if n >= SHARED_THRESHOLD)


_STATE_RE = re.compile(r",\s*([A-Za-z][A-Za-z &.]*?)\s+\d{6},\s*India\s*$")
_SUPPRESSION_KINDS = (("whale", "never_push"), ("never", "never_push"), ("dnc", "dnc"), ("do not call", "dnc"), ("do-not-call", "dnc"),
                      ("competitor", "competitor"), ("unsub", "unsubscribed"), ("opt", "unsubscribed"), ("duplicate", "duplicate"),
                      ("off_icp", "off_icp"), ("off icp", "off_icp"), ("too_big", "too_big"), ("too big", "too_big"), ("deliver", "delivered"),
                      ("bounce", "bad_contact"), ("bad", "bad_contact"))


def suppression_kind(reason: Any, kind: Any = None) -> str:
    k = (_s(kind) or "").lower()
    if k in ("never_push", "dnc", "competitor", "unsubscribed", "duplicate", "off_icp", "too_big", "delivered", "sheet_worked", "bad_contact", "other"):
        return k
    text = ("%s %s" % (_s(reason) or "", k)).lower()
    for needle, code in _SUPPRESSION_KINDS:
        if needle in text:
            return code
    return "other"


def infer_match_type(value: Any, hint: Any = None) -> Optional[str]:
    h = (_s(hint) or "").lower()
    if h in ("domain", "email", "phone", "linkedin_company", "linkedin_person", "cin", "company_name"):
        return h
    h = {"linkedin": None, "company": "company_name", "name": "company_name", "mobile": "phone"}.get(h, None)
    if h:
        return h
    v = _s(value) or ""
    if "@" in v:
        return "email"
    if "linkedin.com/in/" in v.lower():
        return "linkedin_person"
    if "linkedin.com/" in v.lower():
        return "linkedin_company"
    if re.match(r"^\+?[0-9][0-9 ()-]{6,}$", v):
        return "phone"
    if norm.norm_cin(v):
        return "cin"
    if norm.norm_domain(v):
        return "domain"
    return None


# ====================================================================================================== per-source canonical mappings
def canon_itsvc(E: Engine, src: sqlite3.Connection) -> None:
    lim = " LIMIT %d" % E.limit if E.limit else ""
    vid = E.vertical("india_it_services", "India IT services", "India", "IN")
    cands = {}  # type: Dict[Tuple[Any, Any], Tuple]
    for r in src.execute("SELECT linkedin_url, name, id, website, phone_e164, extra_json, gstin, pan, legal_name FROM candidates"):
        cands[(r[0], r[1])] = r
    sites = {}  # type: Dict[str, Tuple]
    for r in src.execute("SELECT entity_id, status, http_code, cin, fetched_at FROM site_text"):
        sites[r[0]] = r
    cls = collections.defaultdict(list)  # type: Dict[str, List[Tuple]]
    n_place = 0
    for r in src.execute("SELECT entity_id, model, prompt_version, builds_software, dev_intensity, subsegments_json, tags_json, exclusion_flags_json, tech_stack_json, "
                         "erp_platforms_json, export_focus, evidence_json, confidence, rules_applied_json, created_at, rowid FROM classifications"):
        if r[1] == "pre-classification-prior":
            n_place += 1
        else:
            cls[r[0]].append(r)
    if n_place:
        E.tally.add("placeholder_not_imported", "itsvc", None, "info", "itsvc 'pre-classification-prior' rows are placeholders: kept in legacy_itsvc_classifications only")
        E.stat("placeholder_verdicts_skipped", n_place)
    ents = src.execute("SELECT entity_id, canonical_name, legal_name, cin, llpin, domain, linkedin_url, city, state, headcount_band, first_seen FROM entities ORDER BY entity_id" + lim).fetchall()
    E.shared["root_domain"] = scan_shared(norm.norm_domain(e[5]) for e in ents)
    E.shared["linkedin_company"] = scan_shared(norm.norm_linkedin_company(e[6]) for e in ents)
    E.tally.add("shared_key_weak", "itsvc", None, "info", "keys claimed by >= %d distinct entities are stored as weak evidence" % SHARED_THRESHOLD)
    E.stat("shared_domains", len(E.shared["root_domain"]))
    E.stat("shared_linkedin", len(E.shared["linkedin_company"]))

    def handle(e: Sequence[Any]) -> None:
        eid = e[0]
        c = cands.get((e[6], e[1]))
        extra = []  # type: List[Key]
        year = None
        if c is not None:
            ej = _jl(c[5]) or {}
            if isinstance(ej, dict):
                if _s(ej.get("apollo_id")):
                    extra.append(Key("apollo_org", str(ej["apollo_id"]).strip().lower(), ej["apollo_id"], True, 1.0, True))
                year = _year(ej.get("founded_year"))
            if _s(c[6]):
                g = re.sub(r"\s+", "", c[6]).upper()
                if len(g) == 15 and g.isalnum():
                    extra.append(Key("gstin", g, c[6], True, 1.0))
            if _s(c[7]) and re.match(r"^[A-Z]{5}[0-9]{4}[A-Z]$", re.sub(r"\s+", "", c[7]).upper()):
                extra.append(Key("pan", re.sub(r"\s+", "", c[7]).upper(), c[7], True, 1.0))
        st = sites.get(eid)
        if st is not None and _s(st[3]) and norm.norm_cin(st[3]):
            extra.append(Key("cin", norm.norm_cin(st[3]), st[3], True, 0.8))
        keys = E.keys(eid, domain=e[5], linkedin=e[6], cin=e[3] or e[4], extra=extra)
        fields = {"legal_name": _s(e[2]) or (_s(c[8]) if c else None), "website": _s(c[3]) if c else None, "hq_city": _s(e[7]), "hq_state": _s(e[8]),
                  "headcount_band": _s(e[9]), "founded_year": year}
        res = E.upsert_company("entities", eid, e[1], keys, fields, _ts(e[10]))
        if res is None:
            return
        cid = res[0]
        if c is not None and _s(c[4]):
            E.add_company_phone(cid, c[4], "candidates", c[2])
        for r in cls.get(eid, ()):
            ra = _jl(r[13]) or {}
            subs = _jl(r[5]) or []
            E.add_verdict(cid, vid, "classifications", "%s|%s" % (eid, r[15]), "unscored", "rules" if r[1] == "rules" else "llm", _ts(r[14]),
                          segment=(subs[0] if subs else None), band=None, tier=_s(ra.get("band")) if isinstance(ra, dict) else None,
                          score=_num(ra.get("score")) if isinstance(ra, dict) else None, confidence=_num(r[12]), model=r[1], prompt_version=r[2],
                          reason=("; ".join(ra.get("reasons", [])) if isinstance(ra, dict) and ra.get("reasons") else None),
                          details={"builds_software": r[3], "dev_intensity": r[4], "subsegments": subs, "tags": _jl(r[6]), "exclusion_flags": _jl(r[7]),
                                   "tech_stack": _jl(r[8]), "erp_platforms": _jl(r[9]), "export_focus": r[10], "evidence": _jl(r[11]),
                                   "rules": ra, "note": "itsvc has no ICP bucket: unscored by rule"})
        if st is not None:
            E.add_signal(cid, "site_fetch_status", value_text=st[1], value_num=None, observed_at=_ts(st[4]))
            if st[2]:
                E.add_signal(cid, "site_http_code", value_num=float(st[2]), observed_at=_ts(st[4]))

    E.rows("entities", ents, handle, len(ents))
    # suppression (0 rows today; mapped for when it is populated)
    sup = src.execute("SELECT id, entity_id, domain, email, phone_e164, reason, ts FROM suppression").fetchall()
    om = E.omap("company", "entities")

    def hs(r: Sequence[Any]) -> None:
        cid = om.get(r[1]) if r[1] else None
        kind = suppression_kind(r[5])
        typed = [(mt, val) for mt, val in (("domain", r[2]), ("email", r[3]), ("phone", r[4])) if _s(val)]
        for mt, val in typed:
            E.add_suppression("suppression", r[0], kind, mt, val, cid, r[5], "itsvc.suppression", _ts(r[6]))
        if not typed and cid:
            E.add_suppression("suppression", r[0], kind, "company_id", None, cid, r[5], "itsvc.suppression", _ts(r[6]))

    E.rows("suppression", sup, hs, len(sup))
    costs = src.execute("SELECT id, provider, units, usd, ts, task_key FROM cost_ledger").fetchall()   # 0 rows today

    def hcost(r: Sequence[Any]) -> None:
        E.add_cost("cost_ledger", r[0], (_s(r[1]) or "unknown").lower(), "unit", _num(r[2]) or 0.0, _ts(r[4]), run_ref=r[5], usd=_num(r[3]))

    E.rows("cost_ledger", costs, hcost, len(costs))


def canon_corpus(E: Engine, src: sqlite3.Connection) -> None:
    lim = " LIMIT %d" % E.limit if E.limit else ""
    vmap = {}  # type: Dict[int, int]
    for v in src.execute("SELECT id, slug, display_name, region, country_code FROM vertical"):
        vmap[v[0]] = E.vertical(re.sub(r"[^a-z0-9_]", "_", v[1].lower()), v[2], v[3], v[4], origin=("vertical", v[0]))
    idents = collections.defaultdict(list)  # type: Dict[int, List[Tuple]]
    for r in src.execute("SELECT company_id, id_type, id_value, is_primary, confidence FROM company_identifier ORDER BY company_id, id"):
        idents[r[0]].append(r)
    rows = src.execute("SELECT id, vertical_id, display_name, legal_name, name_norm, root_domain, website, linkedin_url, country_code, region, headcount_est, "
                       "headcount_band, founded_year, status, status_detail, status_as_of, first_seen_at FROM company ORDER BY id" + lim)
    E.shared["root_domain"] = scan_shared(norm.norm_domain(r[0]) for r in src.execute("SELECT root_domain FROM company WHERE root_domain IS NOT NULL AND root_domain <> ''"))
    E.shared["linkedin_company"] = scan_shared(norm.norm_linkedin_company(r[0]) for r in src.execute("SELECT linkedin_url FROM company WHERE linkedin_url IS NOT NULL AND linkedin_url <> ''"))
    E.stat("shared_domains", len(E.shared["root_domain"]))
    E.stat("shared_linkedin", len(E.shared["linkedin_company"]))
    total = src.execute("SELECT count(*) FROM company").fetchone()[0]

    def handle(r: Sequence[Any]) -> None:
        cc = norm.norm_country(r[8])
        extra = []  # type: List[Key]
        dom = r[5]
        for i in idents.get(r[0], ()):
            t, v = i[1], i[2]
            if t == "companies_house" and _s(v):
                extra.append(Key("companies_house", str(v).strip().upper(), v, True, _num(i[4]) or 1.0, bool(i[3])))
            elif t == "apollo_org" and _s(v):
                extra.append(Key("apollo_org", str(v).strip().lower(), v, True, _num(i[4]) or 1.0, bool(i[3])))
            elif t == "domain" and _s(v):
                d = norm.norm_domain(v)
                if d and not _s(dom):
                    dom = v
                elif d and d != norm.norm_domain(dom):
                    strong = (_num(i[4]) or 1.0) >= 0.9 and d not in GENERIC_DOMAINS and d not in E.shared["root_domain"]
                    extra.append(Key("root_domain", d, v, strong, _num(i[4]) or 1.0, False))
        keys = E.keys(r[0], domain=dom, linkedin=r[7], extra=extra)
        fields = {"legal_name": _s(r[3]), "website": _s(r[6]), "hq_country": cc, "hq_state": _s(r[9]), "india_hq": "yes" if cc == "IN" else ("no" if cc else "unknown"),
                  "employee_count": _int(r[10]), "headcount_band": _s(r[11]), "founded_year": _year(r[12]), "status": map_status(r[13]),
                  "status_detail": _s(r[14]) or _s(r[13]), "status_as_of": _date(r[15])}
        if fields["employee_count"] is not None and fields["employee_count"] < 0:
            fields["employee_count"] = None
        E.upsert_company("company", r[0], r[2] or r[4], keys, fields, _ts(r[16]))

    E.rows("company", rows, handle, total)
    comp = E.omap("company", "company")
    # ---- verdicts (icp_score + components)
    comps = src.execute("SELECT icp_score_id, rule_slug, points, detail FROM icp_score_component ORDER BY icp_score_id, id")
    pending = [next(comps, None)]

    def components_for(sid: int) -> List[Dict[str, Any]]:
        out = []
        c = pending[0]
        while c is not None and c[0] < sid:
            c = next(comps, None)
        while c is not None and c[0] == sid:
            out.append({"rule": c[1], "points": c[2], "detail": c[3]})
            c = next(comps, None)
        pending[0] = c
        return out

    bucket_of = {"fit": "fit", "maybe": "maybe", "weak": "unscored", "unclear": "unscored"}
    scores = src.execute("SELECT id, company_id, vertical_id, score, score_raw, band, match_label, confidence, segments, scored_by, model_id, prompt_version, "
                         "rationale, scored_at FROM icp_score ORDER BY id" + lim)
    total = src.execute("SELECT count(*) FROM icp_score").fetchone()[0]

    def hv(r: Sequence[Any]) -> None:
        comps_ = components_for(r[0]) if not E.limit else []
        cid = comp.get(str(r[1]))
        if cid is None:
            E.tally.add("verdict_company_missing", "corpus", r[0], "warn", "icp_score row whose company was not imported")
            return
        label = (_s(r[6]) or "").lower()
        bucket = bucket_of.get(label, "unscored")
        segs = _jl(r[8]) or []
        by = (_s(r[9]) or "").lower()
        method = "llm" if ("llm" in by or "haiku" in by) else ("manual" if "manual" in by else "rules")
        E.add_verdict(cid, vmap.get(r[2]), "icp_score", r[0], bucket, method, _ts(r[13]), segment=(segs[0] if isinstance(segs, list) and segs else None),
                      band=r[5] if r[5] in ("P1", "P2", "P3", "P4") else None, score=_num(r[3]), confidence=_num(r[7]), reason=_s(r[12]),
                      native_bucket=_s(r[6]), model=_s(r[10]), prompt_version=_s(r[11]),
                      details={"score_raw": _num(r[4]), "segments": segs, "scored_by": r[9], "components": comps_})

    E.rows("icp_score", scores, hv, total)
    # ---- indicators -> signals
    defs = {}  # type: Dict[int, Tuple[str, str]]
    for d in src.execute("SELECT id, slug, value_type FROM indicator_def"):
        defs[d[0]] = (d[1], d[2])
    ind = src.execute("SELECT id, company_id, indicator_id, value_bool, value_num, value_text, confidence, evidence_snippet, evidence_url, observed_at FROM indicator ORDER BY id" + lim)
    total = src.execute("SELECT count(*) FROM indicator").fetchone()[0]

    def hi(r: Sequence[Any]) -> None:
        cid = comp.get(str(r[1]))
        d = defs.get(r[2])
        if cid is None or d is None:
            E.tally.add("indicator_orphan", "corpus", r[0], "warn", "indicator whose company or definition is missing")
            return
        E.add_signal(cid, d[0], value_bool=None if r[3] is None else (1 if r[3] else 0), value_num=_num(r[4]), value_text=r[5], confidence=_num(r[6]),
                     snippet=r[7], url=r[8], observed_at=_ts(r[9]))

    E.rows("indicator", ind, hi, total)
    # ---- contacts (0 rows today)
    cts = src.execute("SELECT id, company_id, full_name, title, title_bucket, linkedin_url, work_email, email_status, phone_e164, phone_type, phone_source, subscriber_type, "
                      "dnc_checked, dnc_registered, dnc_checked_at, dnc_provider_ref, opted_out, legal_basis_ref, obtained_at FROM contact").fetchall()

    def hc(r: Sequence[Any]) -> None:
        E.add_contact("contact", r[0], comp.get(str(r[1])), full_name=r[2], job_title=r[3], seniority=r[4], email=r[6], email_status=r[7], linkedin=r[5],
                      do_not_contact=1 if (r[16] or r[13]) else 0, phones=[r[8]] if _s(r[8]) else [], phone_type_hint=_s(r[9]),
                      attrs={"subscriber_type": r[11], "dnc_checked": r[12], "dnc_registered": r[13], "dnc_checked_at": r[14], "dnc_provider_ref": r[15],
                             "opted_out": r[16], "legal_basis_ref": r[17], "phone_source": r[10]})

    E.rows("contact", cts, hc, len(cts))
    # ---- suppression (0 rows today)
    sup = src.execute("SELECT id, kind, value, reason, added_at FROM suppression").fetchall()

    def hs(r: Sequence[Any]) -> None:
        mt = infer_match_type(r[2], r[1])
        if mt is None:
            E.tally.add("suppression_unnormalisable", "corpus", r[0], "error", "a suppression value could not be typed: NOT imported (review manually)")
            return
        E.add_suppression("suppression", r[0], suppression_kind(r[3], r[1]), mt, r[2], None, r[3], "corpus.suppression", _ts(r[4]))

    E.rows("suppression", sup, hs, len(sup))
    budget = src.execute("SELECT id, provider, vertical_id, units, qty, cost_usd, stage, run_id, created_at FROM budget_ledger").fetchall()  # 0 rows today

    def hb(r: Sequence[Any]) -> None:
        E.add_cost("budget_ledger", r[0], (r[1] or "unknown").lower(), r[3] or "unit", _num(r[4]) or 0.0, _ts(r[8]), run_ref=r[7], note=r[6], usd=_num(r[5]))

    E.rows("budget_ledger", budget, hb, len(budget))


def canon_pipeline(E: Engine, src: sqlite3.Connection) -> None:
    lim = " LIMIT %d" % E.limit if E.limit else ""
    vid = E.vertical("india_it_services", "India IT services", "India", "IN")
    rows = src.execute("SELECT domain, company_name, website, city, state, hq_country, founded_year, size_band, size_bucket, size_source, founded_source, segment, status, "
                       "sources_json, gate_pass, gate_reason, created_at, updated_at FROM companies ORDER BY domain" + lim).fetchall()

    def handle(r: Sequence[Any]) -> None:
        cc = norm.norm_country(r[5])
        keys = E.keys(r[0], domain=r[0])
        fields = {"website": _s(r[2]), "hq_city": _s(r[3]), "hq_state": _s(r[4]), "hq_country": cc, "india_hq": "yes" if cc == "IN" else ("no" if cc else "unknown"),
                  "headcount_band": _s(r[7]), "founded_year": _year(r[6])}
        res = E.upsert_company("companies", r[0], r[1] or r[0], keys, fields, _ts(r[16]))
        if res is None:
            return
        srcs = _jl(r[13]) or []
        names = sorted(set(s.get("source") for s in srcs if isinstance(s, dict) and s.get("source"))) if isinstance(srcs, list) else []
        passed = r[14]
        E.add_verdict(res[0], vid, "companies", r[0], "unscored" if passed else "out", "rules", _ts(r[17]) or _ts(r[16]),
                      reason_code=None if passed else "gate_fail", reason=_s(r[15]), native_bucket="gate_pass" if passed else "gate_fail",
                      details={"size_bucket": r[8], "size_source": r[9], "founded_source": r[10], "listing_sources": names,
                               "note": "pipeline gate is a size/age screen, not an ICP bucket: pass -> unscored, fail -> out with the gate reason"})

    E.rows("companies", rows, handle, len(rows))
    comp = E.omap("company", "companies")
    people = src.execute("SELECT id, domain, name, role, name_source, linkedin_url, linkedin_source, linkedin_confirmed, phone, phone_source, email, email_source, "
                         "is_primary, confidence, notes FROM people ORDER BY id" + lim).fetchall()

    def hp(r: Sequence[Any]) -> None:
        cid = comp.get(r[1])
        E.add_contact("people", r[0], cid, full_name=r[2], job_title=r[3], email=r[10], linkedin=r[5], phones=[r[8]] if _s(r[8]) else [],
                      attrs={"name_source": r[4], "linkedin_source": r[6], "linkedin_confirmed": r[7], "phone_source": r[9], "email_source": r[11],
                             "is_primary": r[12], "confidence": r[13], "notes": r[14]})
        if cid is None:
            E.tally.add("contact_without_company", "pipeline", r[0], "warn", "person whose domain has no company")

    E.rows("people", people, hp, len(people))
    quota = src.execute("SELECT provider, metric, window_key, used, limit_value, updated_at FROM quota").fetchall()

    def hq(r: Sequence[Any]) -> None:
        unit = {"search": "search_call", "credits": "credit"}.get(r[1], r[1])
        E.add_cost("quota", "%s|%s|%s" % (r[0], r[1], r[2]), (r[0] or "unknown").lower(), unit, _num(r[3]) or 0.0, _ts(r[2]),
                   credits=(_num(r[3]) if r[1] == "credits" else None), run_ref=r[2], note="daily meter; limit %s" % (r[4],))

    E.rows("quota", quota, hq, len(quota))
    # crm_feedback (0 rows today): call outcomes are engagement-level data, not mapped
    n = src.execute("SELECT count(*) FROM crm_feedback").fetchone()[0]
    if n:
        E.tally.add("not_mapped_crm_feedback", "pipeline", None, "info", "pipeline.crm_feedback rows have no canonical home yet (stay in legacy_pipeline_crm_feedback)")


def canon_gmaps(E: Engine, src: sqlite3.Connection) -> None:
    tiles = src.execute("SELECT k, places, at FROM tiles ORDER BY at, k").fetchall()
    flat = []  # type: List[Tuple]
    for t in tiles:
        for p in _jl(t[1]) or []:
            if isinstance(p, dict) and _s(p.get("id")):
                flat.append(("%s|%s" % (t[0], p["id"]), p, t[2]))
    # a domain used by >= 3 DISTINCT places is shared (chains)
    per_dom = collections.defaultdict(set)  # type: Dict[str, Set[str]]
    for pk, p, at in flat:
        d = norm.norm_domain(p.get("websiteUri")) if p.get("websiteUri") else None
        if d:
            per_dom[d].add(p["id"])
    E.shared["root_domain"] = set(d for d, ids in per_dom.items() if len(ids) >= SHARED_THRESHOLD)
    seen_places = set()  # type: Set[str]

    def handle(row: Sequence[Any]) -> None:
        pk, p, at = row[0], row[1], row[2]
        dn = p.get("displayName") or {}
        name = dn.get("text") if isinstance(dn, dict) else None
        addr = _s(p.get("formattedAddress")) or ""
        india = addr.rstrip().endswith("India")
        m = _STATE_RE.search(addr)
        keys = E.keys(pk, domain=p.get("websiteUri"), extra=[Key("google_place_id", p["id"], p["id"], True, 1.0, True)])
        fields = {"website": _s(p.get("websiteUri")), "hq_country": "IN" if india else None, "india_hq": "yes" if india else "unknown",
                  "hq_state": m.group(1) if m else None}
        res = E.upsert_company("tiles", pk, name, keys, fields, _ts(at))
        if res is None:
            return
        if _s(p.get("nationalPhoneNumber")):
            E.add_company_phone(res[0], p["nationalPhoneNumber"], "tiles", pk, primary=True)
        if p["id"] in seen_places:      # the same place shows up in several overlapping tiles: one observation is enough
            return
        seen_places.add(p["id"])
        if p.get("rating") is not None:
            E.add_signal(res[0], "gmaps_rating", value_num=_num(p.get("rating")), observed_at=_ts(at))
        if p.get("userRatingCount") is not None:
            E.add_signal(res[0], "gmaps_review_count", value_num=_num(p.get("userRatingCount")), observed_at=_ts(at))

    items = flat[:E.limit] if E.limit else flat
    E.rows("places", items, handle, len(items))
    usage = src.execute("SELECT month, calls FROM usage").fetchall()

    def hu(r: Sequence[Any]) -> None:
        E.add_cost("usage", r[0], "google_maps", "places_call", _num(r[1]) or 0.0, _ts("%s-01" % r[0]) if r[0] and len(r[0]) == 7 else _ts(r[0]),
                   run_ref=r[0], note="monthly total from gmaps_cache.usage; occurred_at = first day of the month")

    E.rows("usage", usage, hu, len(usage))


def canon_resolver(E: Engine, src: sqlite3.Connection) -> None:
    lim = " LIMIT %d" % E.limit if E.limit else ""
    rows = src.execute("SELECT domain, status, cin, last_stage, data FROM companies ORDER BY domain" + lim).fetchall()
    E.shared["root_domain"] = scan_shared(norm.norm_domain(r[0]) for r in rows)

    def handle(r: Sequence[Any]) -> None:
        d = _jl(r[4]) if isinstance(_jl(r[4]), dict) else {}
        cin = norm.norm_cin(r[2]) or norm.norm_cin(d.get("cin"))
        keys = E.keys(r[0], domain=r[0], cin=cin or r[2] or d.get("cin"))
        fields = {"hq_country": "IN" if cin else None, "india_hq": "yes" if cin else "unknown"}
        res = E.upsert_company("companies", r[0], _s(d.get("company")) or r[0], keys, fields)
        if res is None:
            return
        cid = res[0]
        E.add_signal(cid, "resolver_stage", value_text=r[3])
        E.add_signal(cid, "resolver_status", value_text=r[1])
        if _s(d.get("founder_name")):
            E.add_contact("companies", r[0], cid, full_name=d.get("founder_name"), job_title=d.get("title"), linkedin=d.get("linkedin_url"),
                          attrs={"din": d.get("din"), "identity_status": d.get("identity_status"), "resolver_confidence": d.get("confidence"),
                                 "resolver_status": r[1], "source_1_url": d.get("source_1_url"), "source_1_quote": d.get("source_1_quote"),
                                 "source_2_url": d.get("source_2_url"), "li_source": d.get("li_source"), "li_confidence": d.get("li_confidence"),
                                 "role": "founder_resolver"})

    E.rows("companies", rows, handle, len(rows))


def canon_radar(E: Engine, src: sqlite3.Connection) -> None:
    lim = " LIMIT %d" % E.limit if E.limit else ""
    ents = {}
    for r in src.execute("SELECT cin, legal_name, company_status, company_class, date_of_registration, registered_state, fetched_at FROM entity"):
        ents[r[0]] = r
    ce = collections.defaultdict(list)  # type: Dict[int, List[Tuple]]
    for r in src.execute("SELECT candidate_id, cin, match_score, match_method, is_confirmed FROM candidate_entity ORDER BY rowid"):
        ce[r[0]].append(r)
    sc = {r[0]: r for r in src.execute("SELECT candidate_id, confidence, tier, shutdown_date, shutdown_year, reason, sector, city, funding_usd, investors, rationale, scored_at FROM scored")}
    pq = {r[0]: r for r in src.execute("SELECT candidate_id, prequal_total, prequal_max_possible, prequal_pct, prequal_routing, prequal_note, github_org, github_url, scored_at FROM prequal")}
    lv = {r[0]: r for r in src.execute("SELECT candidate_id, domain, dns_resolves, http_status, ssl_expired, rdap_status, liveness_score, checked_at FROM liveness")}
    ev = collections.defaultdict(list)  # type: Dict[int, List[Tuple]]
    for r in src.execute("SELECT candidate_id, kind, source_url, published_at, as_of_date, snippet, fetched_at FROM evidence ORDER BY candidate_id, id"):
        ev[r[0]].append(r)
    cands = src.execute("SELECT id, brand_name, first_seen_at, discovery_source FROM candidate ORDER BY id" + lim).fetchall()
    E.shared["root_domain"] = scan_shared(norm.norm_domain(v[1]) for v in lv.values())
    used_cin = set()  # type: Set[str]

    def handle(c: Sequence[Any]) -> None:
        cid_src = c[0]
        extra = []  # type: List[Key]
        primary_ent = None
        for e in ce.get(cid_src, ()):
            ent = ents.get(e[1])
            confirmed = bool(e[4]) or e[3] in ("exact", "exact_api", "override")
            raw = e[1]
            cn, ll = norm.norm_cin(raw), norm.norm_llpin(raw)
            used_cin.add(raw)
            if cn:
                extra.append(Key("cin", cn, raw, confirmed, (_num(e[2]) or 100.0) / 100.0, confirmed))
            elif ll:
                extra.append(Key("llpin", ll, raw, confirmed, (_num(e[2]) or 100.0) / 100.0, confirmed))
            if confirmed and ent is not None and primary_ent is None:
                primary_ent = ent
        l = lv.get(cid_src)
        keys = E.keys(cid_src, domain=l[1] if l else None, extra=extra, domain_conf=0.9)
        s = sc.get(cid_src)
        reg = primary_ent
        fields = {"legal_name": _s(reg[1]) if reg else None, "hq_state": _s(reg[5]) if reg else None, "hq_city": _s(s[7]) if s else None,
                  "hq_country": "IN" if reg else None, "india_hq": "yes" if reg else "unknown",
                  "founded_year": _year((_s(reg[4]) or "")[:4]) if reg else None, "status": map_status(reg[2]) if reg else "unknown",
                  "status_detail": _s(reg[2]) if reg else None, "status_as_of": _date(reg[6]) if reg else None,
                  "total_funding_usd": _num(s[8]) if s and _num(s[8]) is not None and _num(s[8]) >= 0 else None}
        res = E.upsert_company("candidate", cid_src, c[1], keys, fields, _ts(c[2]))
        if res is None:
            return
        cid = res[0]
        for k in keys:
            if k.type in ("cin", "llpin") and k.raw:
                if k.strong:   # the MCA entity row is a second origin of the same golden company (not a link decision)
                    E.origin("company", cid, "entity", k.raw, "entity", 1.0)
        E.add_signal(cid, "discovery_source", value_text=c[3], observed_at=_ts(c[2]))
        if s:
            ts_ = _ts(s[11])
            E.add_signal(cid, "distress_tier", value_text=s[2], confidence=_num(s[1]), snippet=s[10], observed_at=ts_)
            E.add_signal(cid, "shutdown_date", value_text=s[3], observed_at=ts_)
            E.add_signal(cid, "shutdown_year", value_num=_num(s[4]), observed_at=ts_)
            E.add_signal(cid, "shutdown_reason", value_text=s[5], observed_at=ts_)
            E.add_signal(cid, "sector", value_text=s[6], observed_at=ts_)
            E.add_signal(cid, "investors", value_text=s[9], observed_at=ts_)
        p = pq.get(cid_src)
        if p:
            ts_ = _ts(p[8])
            E.add_signal(cid, "prequal_pct", value_num=_num(p[3]), observed_at=ts_)
            E.add_signal(cid, "prequal_total", value_num=_num(p[1]), observed_at=ts_)
            E.add_signal(cid, "prequal_routing", value_text=p[4], snippet=p[5], observed_at=ts_)
            E.add_signal(cid, "github_org", value_text=p[6], url=p[7], observed_at=ts_)
        if l:
            ts_ = _ts(l[7])
            E.add_signal(cid, "liveness_score", value_num=_num(l[6]), observed_at=ts_)
            E.add_signal(cid, "dns_resolves", value_bool=None if l[2] is None else (1 if l[2] else 0), observed_at=ts_)
            E.add_signal(cid, "http_status", value_num=_num(l[3]), observed_at=ts_)
            E.add_signal(cid, "ssl_expired", value_bool=None if l[4] is None else (1 if l[4] else 0), observed_at=ts_)
        byk = collections.defaultdict(list)  # type: Dict[str, List[Tuple]]
        for e in ev.get(cid_src, ()):
            byk[e[1]].append(e)
        for kind, lst in byk.items():
            best = lst[-1]
            obs = _ts(best[3]) or _ts(best[4]) or _ts(best[6])
            E.add_signal(cid, "evidence_%s" % kind, value_num=float(len(lst)), snippet=best[5], url=best[2], observed_at=obs)

    E.rows("candidate", cands, handle, len(cands))
    # legal entities no candidate points at (13 today)
    orphans = [e for k, e in ents.items() if k not in used_cin]

    def hent(e: Sequence[Any]) -> None:
        keys = E.keys(e[0], cin=e[0])
        E.upsert_company("entity", e[0], e[1], keys, {"legal_name": _s(e[1]), "hq_country": "IN", "india_hq": "yes", "hq_state": _s(e[5]),
                                                       "status": map_status(e[2]), "status_detail": _s(e[2]), "status_as_of": _date(e[6]),
                                                       "founded_year": _year((_s(e[4]) or "")[:4])}, _ts(e[6]))

    if not E.limit:
        E.rows("entity_unmatched", orphans, hent, len(orphans))


def canon_searchledger(E: Engine, src: sqlite3.Connection) -> None:
    lim = " LIMIT %d" % E.limit if E.limit else ""
    rows = src.execute("SELECT id, ts_utc, http_status, profiles_returned, size_used, run_id FROM calls ORDER BY id" + lim).fetchall()

    def handle(r: Sequence[Any]) -> None:
        E.add_cost("calls", r[0], "signalhire", "search_call", 1.0, _ts(r[1]), run_ref=r[5],
                   note="searchByQuery http=%s profiles=%s size=%s (every call counts against the daily cap)" % (r[2], r[3], r[4]))

    E.rows("calls", rows, handle, len(rows))


def canon_itdirs(E: Engine, src: sqlite3.Connection) -> None:
    E.tally.add("no_canonical_mapping", "itdirs", None, "info", "itdirs holds scraper state (pages, counters, redirects) only: verbatim layer only, no canonical rows")


CANON = {"itsvc": canon_itsvc, "corpus": canon_corpus, "pipeline": canon_pipeline, "gmaps": canon_gmaps, "resolver": canon_resolver, "radar": canon_radar,
         "searchledger": canon_searchledger, "itdirs": canon_itdirs}


# ====================================================================================================== name-only candidates + overlap
def propose_name_merges(con: sqlite3.Connection, run_id: Optional[int], tally: Tally) -> Dict[str, int]:
    """Name-only matches between golden companies from DIFFERENT source systems -> merge_candidate (never merged)."""
    t0 = time.time()
    systems = collections.defaultdict(set)  # type: Dict[int, Set[str]]
    for r in con.execute("SELECT entity_id, source_system FROM origin_ref WHERE entity_type = 'company'"):
        systems[r[0]].add(r[1])
    groups = collections.defaultdict(list)  # type: Dict[str, List[Tuple]]
    for r in con.execute("SELECT company_id, name_norm, hq_country, hq_city FROM company WHERE merged_into_company_id IS NULL"):
        k = name_key(r[1])
        if len(k) >= 4:
            groups[k].append(r)
    made = skipped_big = 0
    for k, mem in groups.items():
        if len(mem) < 2:
            continue
        if len(mem) > NAME_GROUP_MAX:
            if len(set().union(*[systems.get(m[0], set()) for m in mem])) >= 2:
                skipped_big += 1
            continue
        for i in range(len(mem)):
            for j in range(i + 1, len(mem)):
                a, b = mem[i], mem[j]
                sa, sb = systems.get(a[0], set()), systems.get(b[0], set())
                if not sa or not sb or (len(sa | sb) < 2):
                    continue
                if a[2] and b[2] and a[2] != b[2]:
                    continue
                score = 0.60 + (0.15 if a[1] == b[1] else 0.0) + (0.10 if (a[3] and b[3] and a[3].lower() == b[3].lower()) else 0.0) + (0.10 if a[2] and a[2] == b[2] else 0.0)
                l, r_ = (a[0], b[0]) if a[0] < b[0] else (b[0], a[0])
                cur = con.execute(
                    "INSERT INTO merge_candidate (entity_type, left_id, right_id, match_kind, score, evidence_json, import_run_id) VALUES ('company',?,?,'name_norm',?,?,?) "
                    "ON CONFLICT(entity_type, left_id, right_id, match_kind) DO NOTHING",
                    (l, r_, min(score, 0.95), _jd({"name_key": k, "names_equal": a[1] == b[1], "sources": sorted(sa | sb), "country": a[2] or b[2],
                                                   "note": "name-only match: needs a human decision, never auto-merged"}), run_id))
                made += cur.rowcount
    if skipped_big:
        tally.add("name_group_too_large", "all", None, "info", "name groups larger than %d spanning several sources were not queued" % NAME_GROUP_MAX)
    log.info("name-only merge candidates: %d new, %d oversized groups skipped (%.1fs)", made, skipped_big, time.time() - t0)
    return {"name_merge_candidates_new": made, "name_groups_skipped_too_large": skipped_big}


def overlap_report(con: sqlite3.Connection) -> Dict[str, Any]:
    """Cross-store overlap actually achieved (read-only SQL over origin_ref / company_identifier)."""
    rep = {}  # type: Dict[str, Any]
    rep["companies"] = con.execute("SELECT COUNT(*) FROM company WHERE merged_into_company_id IS NULL").fetchone()[0]
    rep["companies_by_number_of_source_systems"] = {
        str(r[0]): r[1] for r in con.execute(
            "SELECT n, COUNT(*) FROM (SELECT entity_id, COUNT(DISTINCT source_system) n FROM origin_ref WHERE entity_type = 'company' GROUP BY entity_id) GROUP BY n ORDER BY n")}
    rep["source_pair_overlap"] = {
        "%s x %s" % (r[0], r[1]): r[2] for r in con.execute(
            "WITH s AS (SELECT DISTINCT entity_id, source_system FROM origin_ref WHERE entity_type = 'company') "
            "SELECT a.source_system, b.source_system, COUNT(*) FROM s a JOIN s b ON a.entity_id = b.entity_id AND a.source_system < b.source_system "
            "GROUP BY 1, 2 ORDER BY 3 DESC")}
    rep["links_by_key"] = {
        "%s <- %s" % (r[0], r[1]): r[2] for r in con.execute(
            "SELECT source_system, match_method, COUNT(*) FROM origin_ref WHERE entity_type = 'company' AND match_method NOT IN ('new', 'origin', 'entity') "
            "GROUP BY 1, 2 ORDER BY 3 DESC")}
    rep["identifiers_confirmed_by_2plus_sources"] = {
        r[0]: r[1] for r in con.execute(
            "SELECT i.identifier_type, COUNT(*) FROM company_identifier i WHERE i.is_strong = 1 AND (SELECT COUNT(DISTINCT o.source_system) FROM origin_ref o "
            "WHERE o.entity_type = 'company_identifier' AND o.entity_id = i.identifier_id) >= 2 GROUP BY 1 ORDER BY 2 DESC")}
    rep["merge_candidates_open"] = {r[0]: r[1] for r in con.execute("SELECT entity_type || ':' || match_kind, COUNT(*) FROM merge_candidate WHERE status = 'open' GROUP BY 1")}
    return rep


# ====================================================================================================== runs
def _start_run(con: sqlite3.Connection, source_name: str, path: str, fingerprint: str, params: Dict[str, Any]) -> int:
    cur = con.execute("INSERT INTO ops_import_run (kind, source_name, source_path, source_fingerprint, status, params_json, tool_version) VALUES ('legacy_import',?,?,?,'running',?,?)",
                      (source_name, _rel(path), fingerprint, _jd(params), TOOL_VERSION))
    return cur.lastrowid


def _finish_run(con: sqlite3.Connection, run_id: int, status: str, read: int, written: int, skipped: int, rejected: int, error: Optional[str], params: Dict[str, Any]) -> None:
    con.execute("UPDATE ops_import_run SET status = ?, finished_at = ?, rows_read = ?, rows_written = ?, rows_skipped = ?, rows_rejected = ?, error = ?, params_json = ? "
                "WHERE import_run_id = ?", (status, db.utc_now_iso(), read, written, skipped, rejected, error, _jd(params), run_id))


def _audit(con: sqlite3.Connection, run_id: int, action: str, after: Dict[str, Any]) -> None:
    con.execute("INSERT INTO ops_audit_log (actor, action, entity_type, entity_ref, after_json, import_run_id) VALUES (?, ?, 'import_run', ?, ?, ?)",
                ("cli:" + TOOL_VERSION.split("/")[0], action, str(run_id), _jd(after), run_id))


# ---------------------------------------------------------------------------------------------------- verbatim stage
def _col_counts_sql(table: str, cols: Sequence[str], where: str = "") -> str:
    return "SELECT COUNT(*), COALESCE(SUM(rowid),0)%s FROM %s%s" % ("".join(", COUNT(%s)" % _qi(c) for c in cols), _qi(table), where)


def _is_rowid_alias(con: sqlite3.Connection, table: str) -> bool:
    """True when the table has an INTEGER PRIMARY KEY column (an alias of rowid: naming both in an INSERT would be ambiguous)."""
    info = con.execute("PRAGMA table_info(%s)" % _qi(table)).fetchall()
    pks = [r for r in info if r[5]]
    return len(pks) == 1 and (pks[0][2] or "").upper() == "INTEGER"


def verbatim_stage(con: sqlite3.Connection, sd: Source, path: str, dry_run: bool, batch_rows: int, fail_after_rows: Optional[int] = None) -> Dict[str, Any]:
    """Load every table of one source DB.  Returns the per-table report.  ``fail_after_rows`` is a test hook that simulates a crash."""
    src = open_source(path)
    try:
        return _verbatim(con, src, sd, path, dry_run, batch_rows, fail_after_rows)
    finally:
        src.close()


def _verbatim(con: sqlite3.Connection, src: sqlite3.Connection, sd: Source, path: str, dry_run: bool, batch_rows: int,
              fail_after_rows: Optional[int]) -> Dict[str, Any]:
    tables, views, skipped = source_tables(src)
    colmap = dict(tables)
    names = [t[0] for t in tables]
    for n, cols in tables:
        tcols = [r[1] for r in con.execute("PRAGMA table_info(%s)" % _qi(sd.prefix + n))]
        if not tcols:
            raise ImportFailure("target table %s%s does not exist (migration missing?)" % (sd.prefix, n))
        miss = set(cols) - set(tcols)
        if miss or "_import_run_id" not in tcols:
            raise ImportFailure("schema drift for %s%s: source columns missing in the mirror: %s" % (sd.prefix, n, sorted(miss)))
    for v in views:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE type = 'view' AND name = ?", (sd.prefix + v,)).fetchone():
            raise ImportFailure("mirror view %s%s does not exist (migration missing?)" % (sd.prefix, v))
    fp = file_sha256(path)
    source_name = "hubspot_repo:%s:verbatim" % sd.name
    prev = con.execute("SELECT import_run_id, status FROM ops_import_run WHERE kind = 'legacy_import' AND source_name = ? AND source_fingerprint = ? "
                       "ORDER BY import_run_id DESC LIMIT 1", (source_name, fp)).fetchone()
    report = {"db": sd.name, "fingerprint": fp, "tables": {}, "views": {}, "skipped_virtual": skipped, "run_id": None, "resumed": False,
              "already_imported": False, "fk_orphans": 0}  # type: Dict[str, Any]
    order = _load_order(con, names, sd.prefix)
    run_id = prev[0] if prev else None
    for n in order:   # never stack a second import of a different file (or hand-made rows) on top of a mirror
        other = con.execute("SELECT COUNT(*) FROM %s WHERE _import_run_id IS NULL OR _import_run_id <> ?" % _qi(sd.prefix + n), (run_id if run_id else -1,)).fetchone()[0]
        if other:
            raise ImportFailure("%s%s already holds %d row(s) that do not belong to the run for this source file (different fingerprint or foreign rows): "
                                "refusing to duplicate; reverse the old run first (DELETE ... WHERE _import_run_id = ?)" % (sd.prefix, n, other))
    plan = {}  # type: Dict[str, Tuple[int, int]]
    for n in names:
        sc = src.execute("SELECT COUNT(*) FROM %s" % _qi(n)).fetchone()[0]
        lc = con.execute("SELECT COUNT(*) FROM %s WHERE _import_run_id = ?" % _qi(sd.prefix + n), (run_id,)).fetchone()[0] if run_id else 0
        plan[n] = (sc, lc)
    if prev and prev[1] == "succeeded" and all(sc == lc for sc, lc in plan.values()):
        report["already_imported"], report["run_id"] = True, prev[0]
        for n, (sc, lc) in plan.items():
            report["tables"][n] = {"source": sc, "loaded": lc, "skipped_resume": lc, "inserted_now": 0, "content_ok": True}
        log.info("[%s] already imported by run %s (%d tables, row counts match): nothing to do", sd.name, prev[0], len(plan))
        return report
    if dry_run:
        for n, (sc, lc) in plan.items():
            report["tables"][n] = {"source": sc, "loaded": lc, "would_insert": sc - lc}
        report["views"] = {v: None for v in views}
        report["skipped_virtual"] = skipped
        return report

    params = {"stage": "verbatim", "db": sd.name, "batch_rows": batch_rows, "tables": len(tables)}   # type: Dict[str, Any]
    if run_id is None:
        run_id = _start_run(con, source_name, path, fp, params)
    else:
        report["resumed"] = True
        con.execute("UPDATE ops_import_run SET status = 'running', finished_at = NULL, error = NULL WHERE import_run_id = ?", (run_id,))
    report["run_id"] = run_id
    totals = {"read": 0, "written": 0, "skipped": 0}
    status, err, bad = "failed", None, []   # type: str, Optional[str], List[str]
    budget = [fail_after_rows]
    try:
        con.execute("PRAGMA foreign_keys = OFF")   # outside any transaction: the sources contain orphans that must load as they are
        for n in order:
            cols = colmap[n]
            tgt = sd.prefix + n
            sc, lc = plan[n]
            alias = _is_rowid_alias(src, n)
            last = con.execute("SELECT COALESCE(MAX(rowid), -9223372036854775807) FROM %s WHERE _import_run_id = ?" % _qi(tgt), (run_id,)).fetchone()[0]
            ins_cols = ([] if alias else ["rowid"]) + [_qi(c) if c != "rowid" else "rowid" for c in cols]
            ins = "INSERT INTO %s (%s, _import_run_id) VALUES (%s)" % (_qi(tgt), ", ".join(ins_cols), ", ".join(["?"] * (len(ins_cols) + 1)))
            sel = "SELECT rowid, %s FROM %s WHERE rowid > ? ORDER BY rowid LIMIT ?" % (", ".join(_qi(c) for c in cols), _qi(n))
            inserted, t0 = 0, time.time()
            while True:
                chunk = src.execute(sel, (last, batch_rows)).fetchall()
                if not chunk:
                    break
                if budget[0] is not None:
                    if budget[0] <= 0:
                        raise KeyboardInterrupt("simulated crash")
                    chunk = chunk[:budget[0]]
                    budget[0] -= len(chunk)
                with db.transaction(con):
                    con.executemany(ins, [(tuple(r[1:]) if alias else tuple(r)) + (run_id,) for r in chunk])
                last = chunk[-1][0]
                inserted += len(chunk)
            now_loaded = con.execute("SELECT COUNT(*) FROM %s WHERE _import_run_id = ?" % _qi(tgt), (run_id,)).fetchone()[0]
            content_ok = True
            if now_loaded == sc:   # content check: sum(rowid) + non-NULL count of every column, source vs mirror
                content_ok = tuple(src.execute(_col_counts_sql(n, cols)).fetchone()) == tuple(
                    con.execute(_col_counts_sql(tgt, cols, " WHERE _import_run_id = %d" % run_id)).fetchone())
            report["tables"][n] = {"source": sc, "loaded": now_loaded, "skipped_resume": lc, "inserted_now": inserted, "content_ok": content_ok}
            totals["read"] += sc
            totals["written"] += inserted
            totals["skipped"] += lc
            log.info("[%s] verbatim %-22s source=%-8d loaded=%-8d (+%d now, %d already there) %.1fs%s", sd.name, n, sc, now_loaded, inserted, lc,
                     time.time() - t0, "" if content_ok and now_loaded == sc else "  ** MISMATCH **")
        for n in order:   # source FK orphans: loaded as they are, reported
            for h in con.execute("PRAGMA foreign_key_check(%s)" % _qi(sd.prefix + n)).fetchall():
                report["fk_orphans"] += 1
                con.execute(
                    "INSERT INTO ops_dq_issue (fingerprint, rule_code, severity, entity_type, entity_ref, message, details_json, import_run_id) VALUES (?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(fingerprint) DO UPDATE SET last_seen_at = strftime('%Y-%m-%dT%H:%M:%fZ','now'), import_run_id = excluded.import_run_id",
                    ("legacy_fk_orphan|%s|%s|rowid=%s|%s" % (sd.name, n, h[1], h[2]), "legacy_fk_orphan", "warn", sd.prefix + n, "rowid=%s" % h[1],
                     "%s%s rowid %s has no parent row in %s (the source already violated its own foreign key; loaded anyway)" % (sd.prefix, n, h[1], h[2]),
                     _jd({"table": sd.prefix + n, "rowid": h[1], "parent": h[2], "fkid": h[3]}), run_id))
        if report["fk_orphans"]:
            log.warning("[%s] %d foreign-key orphan row(s) loaded as in the source and recorded in ops_dq_issue(legacy_fk_orphan)", sd.name, report["fk_orphans"])
        for v in views:   # views are recreated by the migration over the mirrored tables: compare row counts with the source views
            a = src.execute("SELECT COUNT(*) FROM %s" % _qi(v)).fetchone()[0]
            b = con.execute("SELECT COUNT(*) FROM %s" % _qi(sd.prefix + v)).fetchone()[0]
            report["views"][v] = {"source": a, "mirror": b, "ok": a == b}
            log.info("[%s] view %-18s source=%d mirror=%d %s", sd.name, v, a, b, "ok" if a == b else "** MISMATCH **")
        bad = [n for n, t in report["tables"].items() if t["loaded"] != t["source"] or not t["content_ok"]] + [v for v, t in report["views"].items() if not t["ok"]]
        status = "partial" if bad else "succeeded"
        err = ("parity mismatch: %s" % ", ".join(bad)) if bad else None
    except BaseException as exc:
        status = "aborted" if isinstance(exc, KeyboardInterrupt) else "failed"
        err = "%s: %s" % (type(exc).__name__, str(exc)[:300])
        raise
    finally:
        con.execute("PRAGMA foreign_keys = ON")
        if con.in_transaction:
            con.execute("ROLLBACK")
        report["status"] = status
        params.update({"per_table": {n: [t["source"], t["loaded"]] for n, t in report["tables"].items()}, "resumed": report["resumed"]})
        with db.transaction(con):
            _finish_run(con, run_id, status, totals["read"], totals["written"], totals["skipped"], 0, err, params)
            if status in ("succeeded", "partial"):
                _audit(con, run_id, "import.verbatim", {"db": sd.name, "tables": len(tables), "rows_inserted_now": totals["written"], "status": status})
    if bad:
        raise ImportFailure("[%s] verbatim parity failure: %s" % (sd.name, ", ".join(bad)))
    return report


# ---------------------------------------------------------------------------------------------------- canonical stage
def canonical_stage(con: sqlite3.Connection, E: Engine, sd: Source, path: str, dry_run: bool) -> Dict[str, Any]:
    src = open_source(path)
    fp = file_sha256(path)
    params = {"stage": "canonical", "db": sd.name, "limit": E.limit, "dry_run": dry_run}   # type: Dict[str, Any]
    run_id = _start_run(con, "hubspot_repo:%s:canonical" % sd.name, path, fp, params)     # (dry run: inside the outer, rolled-back transaction)
    E.begin_source(sd.name, run_id)
    before = collections.Counter(E.stats)
    t0 = time.time()

    def delta() -> Dict[str, int]:
        return {k[len(sd.name) + 1:]: v - before.get(k, 0) for k, v in E.stats.items() if k.startswith(sd.name + ".") and v - before.get(k, 0)}

    try:
        CANON[sd.name](E, src)
    except BaseException as exc:
        if not dry_run:
            if con.in_transaction:
                con.execute("ROLLBACK")
            with db.transaction(con):
                _finish_run(con, run_id, "aborted" if isinstance(exc, KeyboardInterrupt) else "failed", 0, 0, 0, 0, "%s: %s" % (type(exc).__name__, str(exc)[:300]),
                            dict(params, stats=delta()))
        raise
    finally:
        src.close()
    stat = delta()
    rejected = sum(v for k, v in stat.items() if k.startswith("rows_rejected."))
    written = sum(v for k, v in stat.items() if k in ("company.created", "contact.created", "verdict.fit", "verdict.maybe", "verdict.out", "verdict.unscored",
                                                       "signal.inserted", "cost.inserted", "suppression.inserted"))
    skipped = sum(v for k, v in stat.items() if k.endswith("skipped_existing"))
    read = sum(v for k, v in stat.items() if k.startswith("rows_read."))
    params["stats"], params["seconds"] = stat, round(time.time() - t0, 1)

    def finish() -> None:
        E.tally.flush(con, run_id)
        _finish_run(con, run_id, "partial" if rejected else "succeeded", read, written, skipped, rejected, None, params)
        if not dry_run:
            _audit(con, run_id, "import.canonical", {"db": sd.name, "stats": stat})

    if dry_run:
        finish()
    else:
        with db.transaction(con):
            finish()
    return {"db": sd.name, "run_id": None if dry_run else run_id, "stats": stat, "seconds": params["seconds"]}


# ---------------------------------------------------------------------------------------------------- driver
def run(db_path: Optional[str] = None, only: Optional[Sequence[str]] = None, do_verbatim: bool = True, do_canonical: bool = True, dry_run: bool = False,
        limit: Optional[int] = None, batch_rows: int = DEFAULT_BATCH_ROWS, legacy_root: Optional[str] = None, backup: bool = False,
        fail_after_rows: Optional[int] = None) -> Dict[str, Any]:
    names = [s.name for s in SOURCES]
    sel = list(only) if only else names
    bad = [n for n in sel if n not in SOURCE_BY_NAME]
    if bad:
        raise ImportFailure("unknown database name(s): %s (choose from %s)" % (", ".join(bad), ", ".join(names)))
    sel = [n for n in names if n in sel]
    root = legacy_root or os.path.join(db.PROJECT_ROOT, HUBSPOT_REPO_REL)
    con = db.connect(db_path, must_exist=True)
    report = {"db": db.db_path(db_path), "dry_run": dry_run, "verbatim": {}, "canonical": {}, "overlap": None}  # type: Dict[str, Any]
    try:
        st = db.status(con)
        if st.get("pending"):
            raise ImportFailure("database has pending migrations (%s): run `python -m leadgen.db migrate` first" % ", ".join(p["name"] for p in st["pending"]))
        if backup and not dry_run:
            report["backup"] = db.backup_database(con, "pre-hubspot-repo-dbs")
            log.info("backup written: %s", report["backup"])
        paths = {n: os.path.join(root, SOURCE_BY_NAME[n].rel) for n in sel}
        missing = [n for n, p in paths.items() if not os.path.exists(p)]
        if missing:
            raise ImportFailure("source file(s) missing: %s" % ", ".join("%s (%s)" % (n, _rel(paths[n])) for n in missing))
        if do_verbatim:
            for n in sel:
                log.info("=== verbatim: %s (%s) ===", n, SOURCE_BY_NAME[n].purpose)
                report["verbatim"][n] = verbatim_stage(con, SOURCE_BY_NAME[n], paths[n], dry_run, batch_rows, fail_after_rows)
        if do_canonical:
            tally = Tally()
            if dry_run:
                con.execute("BEGIN IMMEDIATE")
            E = Engine(con, tally, dry=dry_run, limit=limit)
            try:
                for n in sel:
                    log.info("=== canonical: %s ===", n)
                    if not dry_run and do_verbatim is False:
                        loaded = con.execute("SELECT COUNT(*) FROM ops_import_run WHERE source_name = ? AND status = 'succeeded'",
                                             ("hubspot_repo:%s:verbatim" % n,)).fetchone()[0]
                        if not loaded:
                            log.warning("[%s] canonical-only run but no succeeded verbatim run exists: origin_ref rows will point at legacy rows that are not loaded yet", n)
                    report["canonical"][n] = canonical_stage(con, E, SOURCE_BY_NAME[n], paths[n], dry_run)
                run_id = con.execute("SELECT MAX(import_run_id) FROM ops_import_run").fetchone()[0]
                if dry_run:
                    report["canonical"]["_name_merges"] = propose_name_merges(con, run_id, tally)
                    report["overlap"] = overlap_report(con)
                    report["counts"] = _canonical_counts(con)
                else:
                    with db.transaction(con):
                        report["canonical"]["_name_merges"] = propose_name_merges(con, run_id, tally)
                        tally.flush(con, run_id)
                    report["overlap"] = overlap_report(con)
                    report["counts"] = _canonical_counts(con)
            finally:
                if dry_run and con.in_transaction:
                    con.execute("ROLLBACK")
            report["stats"] = dict(E.stats)
    finally:
        con.close()
    return report


def _canonical_counts(con: sqlite3.Connection) -> Dict[str, int]:
    out = {}
    for t in ("company", "company_identifier", "contact", "contact_phone", "company_phone", "tam_company_verdict", "company_signal", "cost_ledger", "suppression",
              "merge_candidate", "origin_ref", "vertical"):
        out[t] = con.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
    return out


def format_report(rep: Dict[str, Any]) -> str:
    lines = ["hubspot_repo_dbs import report%s  db=%s" % (" (DRY RUN - nothing written)" if rep["dry_run"] else "", rep["db"])]
    for n, v in rep["verbatim"].items():
        lines.append("verbatim %-12s run=%s %s" % (n, v.get("run_id"), "ALREADY IMPORTED" if v.get("already_imported") else ("resumed" if v.get("resumed") else "")))
        for t, c in v["tables"].items():
            lines.append("    %-24s source=%-8s loaded=%-8s %s" % (t, c.get("source"), c.get("loaded"), ("would_insert=%s" % c["would_insert"]) if "would_insert" in c else
                                                                  ("+%s now" % c.get("inserted_now"))))
        for t, c in (v.get("views") or {}).items():
            if c:
                lines.append("    view %-19s source=%-8s mirror=%-8s %s" % (t, c["source"], c["mirror"], "ok" if c["ok"] else "MISMATCH"))
        if v.get("fk_orphans"):
            lines.append("    foreign-key orphans loaded + reported: %s" % v["fk_orphans"])
    for n, v in rep["canonical"].items():
        if n.startswith("_"):
            lines.append("canonical %s: %s" % (n, v))
        else:
            lines.append("canonical %-12s %.1fs  %s" % (n, v["seconds"], json.dumps(v["stats"], sort_keys=True)))
    if rep.get("counts"):
        lines.append("canonical table counts: %s" % json.dumps(rep["counts"], sort_keys=True))
    if rep.get("overlap"):
        lines.append("overlap: %s" % json.dumps(rep["overlap"], sort_keys=True))
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m leadgen.legacy_import.hubspot_repo_dbs", description=__doc__.split("\n")[0])
    ap.add_argument("--db", help="target database (default: $LEADGEN_DB or db/leadgen.sqlite)")
    ap.add_argument("--only", action="append", help="database name(s), comma separated or repeated: %s" % ", ".join(s.name for s in SOURCES))
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--only-verbatim", action="store_true", help="verbatim legacy_* layer only")
    g.add_argument("--only-canonical", action="store_true", help="canonical mapping only")
    ap.add_argument("--dry-run", action="store_true", help="write nothing (verbatim: plan; canonical: executed inside a rolled-back transaction)")
    ap.add_argument("--limit", type=int, help="canonical only: first N rows of each source table (smoke tests)")
    ap.add_argument("--batch", type=int, default=DEFAULT_BATCH_ROWS, help="verbatim rows per transaction (default %(default)s)")
    ap.add_argument("--legacy-root", help="directory that holds the hubspot repo databases (default legacy/hubspot)")
    ap.add_argument("--backup", action="store_true", help="VACUUM INTO db/backup/... before writing")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S", stream=sys.stderr)
    only = [x.strip() for v in (a.only or []) for x in v.split(",") if x.strip()] or None
    try:
        rep = run(a.db, only, not a.only_canonical, not a.only_verbatim, a.dry_run, a.limit, a.batch, a.legacy_root, a.backup)
    except (ImportFailure, db.MigrationError, FileNotFoundError) as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2
    print(json.dumps(rep, indent=2, sort_keys=True, default=str) if a.json else format_report(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
