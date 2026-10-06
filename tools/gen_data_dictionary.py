#!/usr/bin/env python3
"""Generate docs/DATA_DICTIONARY.md: every table, view and column of the unified database, grouped by layer.

Sources (nothing is typed by hand twice):
  * the SCHEMA  - all db/migrations/*.sql applied to a throwaway in-memory database (PRAGMA table_xinfo / foreign_key_list /
                  index_list, sqlite_master), so types, nullability, defaults, keys, generated-column expressions and the number of
                  CHECKs are always the real ones;
  * the COMMENTS - the SQL comments of the migration files: the comment block above each CREATE TABLE / VIEW is the object's purpose,
                  the trailing ``-- text`` of a column line is the column's purpose.  Write good comments in the migrations and this
                  document improves; a generic glossary (created_at, is_archived, *_json ...) covers columns that are self-explanatory;
                  anything still undocumented is listed at the end ("Columns without a purpose comment") so the gap is visible.

Deterministic (no timestamps): ``--check`` (and tests/test_schema_hardening.py) fail when the committed file is stale.

    .venv/bin/python tools/gen_data_dictionary.py            # write docs/DATA_DICTIONARY.md
    .venv/bin/python tools/gen_data_dictionary.py --check    # exit 1 if it is stale
    .venv/bin/python tools/gen_data_dictionary.py --stdout

Python 3.9 compatible.
"""
import argparse
import os
import re
import sqlite3
import sys
from typing import Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from leadgen import db  # noqa: E402

OUT = os.path.join(ROOT, "docs", "DATA_DICTIONARY.md")

# ---------------------------------------------------------------------------------------------------------- layers
LAYERS = [
    ("ops", "Operations", "Migration ledger, import runs, sync cursors, data-quality findings, append-only audit log (`ops_*`, `schema_migration`)."),
    ("canonical", "Canonical", "Normalised, constrained, STRICT: the only layer report / app code reads (accounts, pipelines, stages and their canonical mapping, "
                               "companies, contacts, phones, deals, the stage-event store, engagements, verdicts, costs, suppression, provenance)."),
    ("hsraw", "HubSpot raw", "Full API JSON of every HubSpot object, so the canonical layer can be rebuilt without re-pulling (`hsraw_*`)."),
    ("rpt", "Report", "Persisted daily facts, report-run ledger, imported legacy snapshot JSON for cross-checks (`rpt_*`)."),
    ("ext", "External pulls", "Google Sheets and Gmail mirrors with FTS5 indexes (`ext_gsheet_*`, `ext_gmail_*`)."),
    ("legacy", "Legacy verbatim", "Verbatim copies of every old SQLite database (`legacy_<db>_*`): same columns / keys, plus `_import_run_id`.  Nothing dropped."),
]
LAYER_TITLE = {k: t for k, t, _ in LAYERS}

#: strictly-legacy sources, in migration order (key, legacy prefix, description is read from the generated migration header)
SHADOW_SUFFIXES = ("_data", "_idx", "_content", "_docsize", "_config")


def layer_of(name: str) -> str:
    if name.startswith("legacy_"):
        return "legacy"
    if name.startswith("hsraw_"):
        return "hsraw"
    if name.startswith("rpt_") or name.startswith("v_rpt_") or name == "v_legacy_snapshot_rows":
        return "rpt"
    if name.startswith("ext_") or name.startswith("v_gsheet_"):
        return "ext"
    if name.startswith("ops_") or name == "schema_migration" or name in ("v_dq_open", "v_sync_freshness"):
        return "ops"
    return "canonical"


# ---------------------------------------------------------------------------------------------------------- glossary
GLOSSARY = {
    "created_at": "Row creation time (UTC, ms, `Z`).",
    "updated_at": "Last change (UTC); maintained by the `*_touch` trigger unless the writer sets it explicitly (e.g. to mirror HubSpot's lastmodified).",
    "import_run_id": "The `ops_import_run` that wrote the row (NULL after that run row is deleted).",
    "_import_run_id": "The `ops_import_run` that loaded this legacy row; reversal = `DELETE ... WHERE _import_run_id = ?`.",
    "account_id": "Our portal slug (`main` / `companyops` / `rat`): scopes every HubSpot key.",
    "is_archived": "1 = archived in HubSpot / retired here; the row is kept (history).",
    "is_placeholder": "1 = fake / prior value that must never be read as a fact (CHECKs keep it from masquerading as real data).",
    "is_primary": "1 = the primary row among its siblings (unique per parent).",
    "is_active": "0 = switched off, row kept.",
    "is_deleted": "1 = tombstone: gone from the HubSpot API but still referenced by history.",
    "is_human": "STORED generated: 1 when the event came from a person in the HubSpot UI (`source_type = 'CRM_UI'`).",
    "ist_day": "STORED generated: the IST (UTC+05:30) calendar day of the event time.",
    "hs_created_at": "HubSpot createdate (UTC).",
    "hs_updated_at": "HubSpot lastmodifieddate (UTC).",
    "source_system": "Which system the value came from (tam, itsvc, hubspot:main, ...).",
    "fetched_at": "When the row was fetched from the source (UTC).",
    "payload_json": "Verbatim source JSON.",
    "fingerprint": "Idempotency key of the finding.",
    "status": "Lifecycle state (see CHECK).",
    "note": "Free-text note.",
    "reason": "Free-text reason.",
    "label": "Human-readable label.",
    "name": "Display name.",
    "title": "Title / display name.",
    "sort_order": "Display / ladder order.",
    "display_order": "Display order within the parent.",
    "confidence": "0..1 confidence of the value / link.",
    "currency": "ISO-4217 code (USD).",
    "email_address": "Mailbox address, lower-case.",
    "thread_id": "Gmail thread id.",
    "message_id": "Gmail API message id.",
    "label_id": "Gmail label id (INBOX, SENT, Label_123 ...).",
    "history_id": "Gmail historyId (incremental-sync cursor).",
    "content_sha256": "sha256 of the stored bytes / text.",
    "row_sha256": "sha256 of the row content (change detection).",
    "payload_sha256": "sha256 of the canonical JSON payload (change detection).",
}


def glossary_for(table: str, col: str) -> Optional[str]:
    if col in GLOSSARY:
        return GLOSSARY[col]
    if col.endswith("_json"):
        return "JSON text (validated by CHECK json_valid)."
    if col.startswith("is_") or col.startswith("flag_"):
        return "Boolean flag (0/1)."
    if col.endswith("_at") or col.endswith("_time"):
        return "Timestamp (UTC, ms, `Z`)."
    if col.endswith("_sha256"):
        return "sha256 hex digest."
    return None


# ---------------------------------------------------------------------------------------------------------- comment parsing
_SEP = re.compile(r"^[-=\s]+$")
_COL_STOP = {"UNIQUE", "PRIMARY", "FOREIGN", "CHECK", "CONSTRAINT"}


def _strip_comment(line: str) -> Tuple[str, Optional[str]]:
    """Split ``code -- comment`` outside single-quoted strings."""
    in_q = False
    i = 0
    while i < len(line):
        c = line[i]
        if c == "'":
            in_q = not in_q
        elif not in_q and line.startswith("--", i):
            return line[:i], line[i + 2:].strip()
        i += 1
    return line, None


class Doc:
    def __init__(self) -> None:
        self.table_purpose = {}  # type: Dict[str, str]   (lines joined with \n)
        self.col_purpose = {}  # type: Dict[Tuple[str, str], str]
        self.view_purpose = {}  # type: Dict[str, str]
        self.trigger_purpose = {}  # type: Dict[str, str]
        self.legacy_source = {}  # type: Dict[str, Tuple[str, str]]   # prefix -> (source path, purpose)
        self.legacy_note = {}  # type: Dict[str, List[str]]


def _paragraph_for(block: List[str], name: str, known: List[str]) -> Optional[str]:
    """If a shared comment block has '<name>: ...' paragraphs, return the one for ``name``."""
    keys = set(known)
    cur = None
    parts = {}  # type: Dict[str, List[str]]
    for ln in block:
        m = re.match(r"^([a-z_0-9]+)(?:\s*/\s*[a-z_0-9]+)*:\s*(.*)$", ln)
        if m and m.group(1) in keys:
            cur = m.group(1)
            parts[cur] = [m.group(2)]
        elif cur is not None:
            parts[cur].append(ln)
    if name in parts:
        return "\n".join(p for p in parts[name] if p).strip()
    return None


def parse_comments(files: List[Tuple[str, str]], table_names: List[str]) -> Doc:
    doc = Doc()
    for fname, sql in files:
        block = []  # type: List[str]
        last_block = []  # type: List[str]
        lines = sql.split("\n")
        # legacy header facts
        if "_legacy_" in fname:
            src = purpose = prefix = None
            notes = []
            for ln in lines[:40]:
                m = re.match(r"^-- Source database : (\S+)", ln)
                if m:
                    src = m.group(1)
                m = re.match(r"^-- Purpose\s+: (.*)$", ln)
                if m:
                    purpose = m.group(1)
                m = re.match(r"^-- Prefix\s+: (\S+)", ln)
                if m:
                    prefix = m.group(1)
                m = re.match(r"^-- LOAD NOTE: (.*)$", ln)
                if m:
                    notes.append(m.group(1))
            if prefix:
                doc.legacy_source[prefix] = (src or "", purpose or "")
                doc.legacy_note[prefix] = notes
        i = 0
        while i < len(lines):
            raw = lines[i]
            s = raw.strip()
            if s.startswith("--"):
                text = s[2:].strip()
                if text and not _SEP.match(text):          # banner separators ("-- -----") and empty comment lines are skipped
                    block.append(text)
                i += 1
                continue
            if s == "":
                if block:
                    last_block = block
                block = []
                i += 1
                continue
            code, _c = _strip_comment(raw)
            m = re.match(r"^\s*CREATE\s+(?:VIRTUAL\s+)?TABLE\s+(\w+)", code, re.I)
            if m:
                t = m.group(1)
                src_block = block or last_block
                purpose = _paragraph_for(src_block, t, table_names) if src_block else None
                if purpose is None and src_block:
                    purpose = "\n".join(src_block)
                    purpose = re.sub(r"^%s\s*:\s*" % re.escape(t), "", purpose)
                if purpose:
                    doc.table_purpose[t] = purpose
                block = []
                # column lines until the closing paren at depth 0 (a line that starts with ')')
                cur = None
                depth = 0
                i += 1
                while i < len(lines):
                    ln = lines[i]
                    c2, cm = _strip_comment(ln)
                    st = c2.strip()
                    if st.startswith(")") and depth <= 0:
                        break
                    first = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s+(\w+)", c2)
                    if depth <= 0 and first and first.group(1).upper() not in _COL_STOP and (len(ln) - len(ln.lstrip())) <= 3 \
                            and not st.startswith("(") and first.group(1) not in ("AND", "OR", "NOT"):
                        cur = first.group(1)
                    elif depth <= 0 and st.upper().startswith(tuple(_COL_STOP)):
                        cur = None
                    if cm and cur:
                        key = (t, cur)
                        doc.col_purpose[key] = (doc.col_purpose[key] + " " + cm) if key in doc.col_purpose else cm
                    depth += c2.count("(") - c2.count(")")
                    i += 1
                continue
            m = re.match(r"^\s*CREATE\s+VIEW\s+(\w+)", code, re.I)
            if m:
                src_block = block or last_block
                purpose = "\n".join(src_block)
                purpose = re.sub(r"^%s\s*:\s*" % re.escape(m.group(1)), "", purpose)
                if purpose:
                    doc.view_purpose[m.group(1)] = purpose
                block = []
                i += 1
                continue
            m = re.match(r"^\s*CREATE\s+TRIGGER\s+(\w+)", code, re.I)
            if m:
                if block:
                    doc.trigger_purpose[m.group(1)] = " ".join(block)
                i += 1
                continue
            # any other statement ends the block
            if block:
                last_block = block
            block = []
            i += 1
    return doc


# ---------------------------------------------------------------------------------------------------------- schema reading
def build_schema() -> Tuple[sqlite3.Connection, List[Tuple[str, str]]]:
    files = db.discover_migrations()
    con = db.connect(":memory:")
    con.execute("PRAGMA foreign_keys = OFF")
    for m in files:
        for stmt in db.split_sql_statements(m.sql):
            con.execute(stmt)
    return con, [(m.filename, m.sql) for m in files]


def _balanced(text: str, start: int) -> str:
    depth = 0
    for j in range(start, len(text)):
        if text[j] == "(":
            depth += 1
        elif text[j] == ")":
            depth -= 1
            if depth == 0:
                return text[start + 1:j]
    return text[start + 1:]


def generated_expr(sql: str, col: str) -> Optional[str]:
    m = re.search(r"(?m)^\s*\"?%s\"?\s+\w+\s+GENERATED ALWAYS AS\s*\(" % re.escape(col), sql)
    if not m:
        return None
    return re.sub(r"\s+", " ", _balanced(sql, m.end() - 1)).strip()


def _quote(text: str) -> List[str]:
    """Markdown blockquote that keeps the comment's line structure (two trailing spaces = hard line break)."""
    return ["> " + ln.replace("|", "\\|") + "  " for ln in text.split("\n")]


def _esc(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


def table_section(con: sqlite3.Connection, name: str, doc: Doc, undocumented: List[str], kind: str) -> List[str]:
    sql = con.execute("SELECT sql FROM sqlite_master WHERE name = ?", (name,)).fetchone()[0] or ""
    xinfo = con.execute('PRAGMA table_xinfo("%s")' % name).fetchall()
    fks = con.execute('PRAGMA foreign_key_list("%s")' % name).fetchall()
    fk_by_col = {}  # type: Dict[str, List[str]]
    fk_groups = {}  # type: Dict[int, list]
    for r in fks:
        fk_groups.setdefault(r[0], []).append(r)
    composite_fks = []  # type: List[str]
    for gid, grp in sorted(fk_groups.items()):
        parent = grp[0][2]
        act = grp[0][6]
        suffix = " ON DELETE " + act if act and act != "NO ACTION" else ""
        if len(grp) == 1:
            fk_by_col.setdefault(grp[0][3], []).append("FK -> %s.%s%s" % (parent, grp[0][4] or "pk", suffix))
        else:
            composite_fks.append("(%s) -> `%s`(%s)%s" % (", ".join(g[3] for g in grp), parent, ", ".join(g[4] or "pk" for g in grp), suffix))
    pk_cols = [r[1] for r in sorted((r for r in xinfo if r[5]), key=lambda r: r[5])]
    uniq_cols = {}  # type: Dict[str, List[str]]
    composite_unique = []  # type: List[str]
    for r in con.execute('PRAGMA index_list("%s")' % name).fetchall():
        if r[2] and r[3] != "pk" and not r[4]:
            cols = [c[2] for c in con.execute('PRAGMA index_xinfo("%s")' % r[1]).fetchall() if c[5] == 1 and c[2]]
            if len(cols) == 1:
                uniq_cols.setdefault(cols[0], []).append("UNIQUE")
            elif cols:
                composite_unique.append("(%s)" % ", ".join(cols))
    out = []
    flags = []
    if kind == "table":
        if "STRICT" in sql.upper().split(")")[-1].upper() or sql.rstrip().upper().endswith("STRICT") or ") STRICT" in sql.upper():
            flags.append("STRICT")
        if "WITHOUT ROWID" in sql.upper():
            flags.append("WITHOUT ROWID")
        if "AUTOINCREMENT" in sql.upper():
            flags.append("AUTOINCREMENT")
    if sql.upper().startswith("CREATE VIRTUAL TABLE"):
        flags.append("FTS5 virtual table")
    n_checks = len(re.findall(r"\bCHECK\s*\(", sql, re.I))
    out.append("#### `%s`%s" % (name, ("  _(%s)_" % ", ".join(flags)) if flags else ""))
    purpose = doc.table_purpose.get(name) or doc.view_purpose.get(name)
    out.append("")
    out.extend(_quote(purpose) if purpose else ["_No purpose comment in the migration._"])
    out.append("")
    if sql.upper().startswith("CREATE VIRTUAL TABLE"):
        m = re.search(r"content\s*=\s*'(\w+)'", sql)
        out.append("External-content FTS5 index over `%s`; kept in sync by AFTER INSERT / UPDATE / DELETE triggers on that table.  Columns:" % (m.group(1) if m else "?"))
        out.append("")
    out.append("| Column | Type | Constraints | Purpose |")
    out.append("|---|---|---|---|")
    for r in xinfo:
        cid, cname, ctype, notnull, dflt, pk, hidden = r[0], r[1], r[2], r[3], r[4], r[5], r[6]
        cons = []
        if pk:
            cons.append("PK" + ("" if len(pk_cols) == 1 else " (part %d)" % pk))
        if notnull and not pk:
            cons.append("NOT NULL")
        if dflt is not None and hidden == 0:
            cons.append("DEFAULT %s" % dflt)
        if hidden in (2, 3):
            expr = generated_expr(sql, cname)
            cons.append("GENERATED %s%s" % ("STORED" if hidden == 3 else "VIRTUAL", (": `%s`" % expr) if expr else ""))
        cons.extend(fk_by_col.get(cname, []))
        cons.extend(uniq_cols.get(cname, [])[:1])
        purpose_c = doc.col_purpose.get((name, cname))
        if not purpose_c:
            purpose_c = glossary_for(name, cname)
        if not purpose_c:
            if name.startswith("legacy_"):
                purpose_c = "Verbatim legacy column."
            elif sql.upper().startswith("CREATE VIRTUAL TABLE"):
                purpose_c = "Indexed text column."
            else:
                undocumented.append("%s.%s" % (name, cname))
                purpose_c = ""
        out.append("| `%s` | %s | %s | %s |" % (cname, _esc(ctype or ""), _esc("; ".join(cons)), _esc(purpose_c)))
    out.append("")
    extra = []
    if pk_cols and len(pk_cols) > 1:
        extra.append("Primary key: (%s)." % ", ".join(pk_cols))
    if composite_fks:
        extra.append("Composite foreign keys: " + "; ".join(composite_fks) + ".")
    if composite_unique:
        extra.append("Unique keys: " + ", ".join(composite_unique) + ".")
    if n_checks:
        extra.append("%d CHECK constraint%s." % (n_checks, "" if n_checks == 1 else "s"))
    idx = []
    for r in con.execute('PRAGMA index_list("%s")' % name).fetchall():
        if r[3] == "pk" or r[1].startswith("sqlite_autoindex"):
            continue
        cols = [c[2] if c[2] else "<expr>" for c in con.execute('PRAGMA index_xinfo("%s")' % r[1]).fetchall() if c[5] == 1]
        idx.append("`%s`%s(%s)%s" % (r[1], " UNIQUE " if r[2] else " ", ", ".join(cols), " partial" if r[4] else ""))
    if idx and not name.startswith("legacy_"):
        extra.append("Indexes: " + "; ".join(idx) + ".")
    elif idx:
        extra.append("%d explicit index(es)." % len(idx))
    trg = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name = ? ORDER BY name", (name,))]
    if trg and kind == "table":
        extra.append("Triggers: " + ", ".join("`%s`" % t for t in trg) + ".")
    for e in extra:
        out.append(e)
        out.append("")
    return out


def view_section(con: sqlite3.Connection, name: str, doc: Doc, undocumented: List[str]) -> List[str]:
    cols = [r[1] for r in con.execute('PRAGMA table_info("%s")' % name)]
    purpose = doc.view_purpose.get(name)
    out = ["#### `%s`" % name, ""] + (_quote(purpose) if purpose else ["_No purpose comment in the migration._"]) + [""]
    out.append("Columns: " + ", ".join("`%s`" % c for c in cols))
    out.append("")
    return out


# ---------------------------------------------------------------------------------------------------------- static text
CONVENTIONS = """## Conventions

* **Timestamps** are UTC ISO-8601 text in one canonical shape: exactly 24 characters, millisecond precision, `Z` suffix (`2026-10-04T12:34:56.789Z`).
  The CHECK is `col IS strftime('%Y-%m-%dT%H:%M:%fZ', col) AND col NOT GLOB '*T24:*'`: the value must survive a round trip through SQLite's own parser, so impossible
  dates, lower-case `t`/`z`, offsets, second precision, extra fractional digits and junk are rejected and text order equals time order.  Importers pad to `.000Z`.
* **IST** (UTC+05:30, no DST) is derived, never stored as the primary value: `ist_day` columns are STORED generated `date(ts, '+330 minutes')`.
* **Calendar dates** are `YYYY-MM-DD`, validated with `length(col) = 10 AND date(col) IS col`.
* **Booleans** are INTEGER 0/1 with `CHECK (col IN (0,1))`.  **JSON** columns are TEXT with `CHECK (json_valid(col))` (and `json_type` where an object/array is required).
* **HubSpot ids** are TEXT (opaque; `default` is a pipeline id), digits-only without leading zeros where always numeric, and always scoped by `account_id`
  (`main`, `companyops`, `rat`); stages by `(account_id, pipeline_id, stage_id)` because three stage ids are shared by the two MAIN pipelines.
* **Money** is USD `REAL` with an explicit `usd_basis` (`actual` / `estimated` / `unknown`); NULL means unknown, never 0.  Aggregate with `ROUND(SUM(usd), 6)`.
* **History is never cascaded away**: event tables hang off `deal` with `ON DELETE RESTRICT`; deals are archived, not deleted.  Never write `INSERT OR REPLACE` (use `ON CONFLICT DO UPDATE`).
* **Surrogate keys** of referenced entities are `AUTOINCREMENT` (a deleted id is never reused).
* **Provenance**: every canonical row is reachable from `origin_ref(entity_type, entity_id) -> (source_system, source_table, source_pk)`; `source_table` is the ORIGINAL legacy table
  name and tables without a primary key use their `rowid` as `source_pk`.
* **Placeholders** (`is_placeholder = 1`) are never facts: itsvc `pre-classification-prior` verdicts, dummy phone numbers; they can never open the +91 gate or read as a real verdict.
* **The +91 mobile gate** (`contact_phone.is_indian_mobile`, `company_phone.is_indian_mobile`) is STORED GENERATED = valid `+91[6-9]xxxxxxxxx` E.164 **and** `phone_type = 'mobile'`; it fails closed.
"""

IMPORTER_CONTRACT = """## Importer normalisation contract

Measured on the real legacy data (docs/review/schema-critic-B.md).  The shared normalisers are in `leadgen/norm.py`; a value that cannot be normalised keeps its raw spelling
and produces an `ops_dq_issue` - it is never loaded un-normalised and never silently dropped.

| Source field | Real values | Rule |
|---|---|---|
| tam `companies.hq_country` | `India` (20,278), `IN` (458) | map country names to ISO-3166-1 alpha-2 before `company.hq_country` |
| tam `companies.root_domain` | `"google.com`, `<U+200B>google.com`, IDN `pegàsehealth.com` | `norm_domain`: strip quotes / zero-width chars, punycode; else DQ issue |
| itsvc `domain`, resolver `cin` | `''` | map `''` to NULL |
| radar `entity.cin` | 16 LLPINs (`AAE-7433`) | route to `identifier_type = 'llpin'` |
| LinkedIn company URLs | pct-encoded slugs, quoted slugs, `school/` URLs | `norm_linkedin_company`: decode, strip quotes, keep `school/` and `showcase/` |
| mirror `contact.linkedin` | 51 `/company/` URLs, 1 `/pub/` | company URLs -> `company_identifier`; `/pub/` stays in `linkedin_raw` only |
| phones | `+91 72593 18319`, `9742044482`, `+91-7042813998`, dummies (`+91 12345 67890`, `0123456789`) | `norm_phone_e164`; keep `phone_raw`; dummies `is_placeholder = 1`; set `phone_type` from libphonenumber (gate stays closed until `mobile`) |
| itsvc `candidates.phone_e164` | 31,616 of 31,621 are NOT E.164 | company-level numbers go to `company_phone` |
| corpus `icp_score.match_label` | `Unclear` 300,464, `Weak` 792, `Maybe`, `Fit` | `icp_bucket = 'unscored'` when the source has no bucket; verbatim label in `native_bucket` |
| corpus `company.status` | 11 UK Companies House values | map to the 7-value enum; original in `status_detail` |
| STAGE_TRANSITIONS.csv | IST wall clock to the second, stage labels and ids, old pipeline labels `Scraped`/`Campaign` | `to_utc_ms(..., assume='ist')`; labels via `stage_alias(kind='label')`; import only for deals without `hubspot_history` events or dedupe (see `v_dse_near_duplicate`) |
| tam `crm_pushes` | pipeline `2464812771` / `default`, no portal column | pin `account_id = 'companyops'`; `removed_off_icp` / `removed_too_big` / `removed_duplicate` / `pushed` -> `suppression` kinds `off_icp` / `too_big` / `duplicate` / `delivered` |
| tam `cost_ledger` | `usd_est = 0` for all 2,634 rows | `usd NULL`, `usd_basis = 'unknown'`, `is_placeholder = 0` (the quantity is real); `phone_reveal` = 8 credits per call -> `credits` |
| itsvc `classifications` | 61,593 `pre-classification-prior` rows | keep in `legacy_itsvc_classifications` only; do not materialise placeholder verdicts |
| radar `candidate_entity` | 2 rows with no `entity` parent (rowid 10783, 10785) | load with `foreign_keys = OFF`, report each to `ops_dq_issue(rule_code='legacy_fk_orphan')` |

**Timestamp spellings per source** (all become `YYYY-MM-DDTHH:MM:SS.mmmZ` via `leadgen.norm.to_utc_ms`):
tam `YYYY-MM-DD HH:MM:SS` (naive = UTC); itsvc `...T..:..:...ffffffZ` (microseconds); corpus `...+00:00`; pipeline `...ffffff+00:00`; radar naive `YYYY-MM-DDTHH:MM:SS`, plus date-only,
`YYYY-MM`, `YYYY` and RFC-822 HTTP dates (the last three are not convertible: keep the raw text); resolver / searchledger / itdirs epoch floats; `gsheets_catalog.json` `generated` is `+0530` local time.

**Stage history** needs `propertiesWithHistory=dealstage,hubspot_owner_id,pipeline`: the dealstage entry has no pipeline.  Resolution order for `deal_stage_event.pipeline_id`
(recorded in `pipeline_basis`): stage id unique inside the account -> `unique_stage_id`; else the `pipeline` property in effect at `entered_at` -> `pipeline_history`; else the deal's current
pipeline -> `deal_current` (a disclosed guess, `v_event_pipeline_guess`); else do NOT insert - write `ops_dq_issue(rule_code='ambiguous_stage_pipeline')`.
Deleted stage ids found in history (Cluster 2: 8 ids, 4,205 events) must exist as tombstone `stage` rows (`label_source = 'history'`, `is_deleted = 1`) before the events load; map them with
`stage_alias(kind = 'stage_id')` to their live successor or give them their own `stage_map` row.
"""


# ---------------------------------------------------------------------------------------------------------- render
def render() -> str:
    con, files = build_schema()
    try:
        tables_all = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\' ORDER BY name")]
        shadow = set()
        for t in tables_all:
            sql = con.execute("SELECT sql FROM sqlite_master WHERE name=?", (t,)).fetchone()[0] or ""
            if sql.upper().startswith("CREATE VIRTUAL TABLE"):
                shadow.update(t + s for s in SHADOW_SUFFIXES)
        tables = [t for t in tables_all if t not in shadow]
        views = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='view' ORDER BY name")]
        doc = parse_comments(files, tables)
        undocumented = []  # type: List[str]

        by_layer = {k: [] for k, _, _ in LAYERS}  # type: Dict[str, List[str]]
        for t in tables:
            by_layer[layer_of(t)].append(t)
        views_by_layer = {k: [] for k, _, _ in LAYERS}  # type: Dict[str, List[str]]
        for v in views:
            views_by_layer[layer_of(v)].append(v)
        n_triggers = con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='trigger'").fetchone()[0]
        n_indexes = con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND sql IS NOT NULL").fetchone()[0]

        out = ["# LeadGenMonolith data dictionary", "",
               "_Generated by `tools/gen_data_dictionary.py` from the migrations in `db/migrations/` (schema) and their SQL comments (purposes).  Do not edit by hand: "
               "improve the comments in the migration files and regenerate.  `tests/test_schema_hardening.py::test_data_dictionary_is_current` fails when this file is stale._", ""]
        out.append("Schema version: **%d migrations** (latest `%s`).  **%d tables, %d views, %d indexes, %d triggers.**" % (
            len(files), files[-1][0], len(tables), len(views), n_indexes, n_triggers))
        out.append("")
        out.append("## Tables and views per layer")
        out.append("")
        out.append("| Layer | Tables | Views | What it is |")
        out.append("|---|---|---|---|")
        for k, title, desc in LAYERS:
            out.append("| %s | %d | %d | %s |" % (title, len(by_layer[k]), len(views_by_layer[k]), desc))
        out.append("| **Total** | **%d** | **%d** | |" % (len(tables), len(views)))
        out.append("")
        out.append("Legacy layer by source database:")
        out.append("")
        out.append("| Prefix | Tables | Source database | Purpose |")
        out.append("|---|---|---|---|")
        for p in sorted(doc.legacy_source):
            n = len([t for t in by_layer["legacy"] if t.startswith(p)])
            out.append("| `%s*` | %d | `%s` | %s |" % (p, n, doc.legacy_source[p][0], _esc(doc.legacy_source[p][1])))
        out.append("")
        out.append(CONVENTIONS)
        out.append(IMPORTER_CONTRACT)
        out.append("## Layers")
        out.append("")
        for k, title, desc in LAYERS:
            out.append("### %s layer" % title)
            out.append("")
            out.append(desc)
            out.append("")
            out.append("%d table(s): %s" % (len(by_layer[k]), ", ".join("`%s`" % t for t in by_layer[k])))
            out.append("")
            if k == "legacy":
                for p in sorted(doc.legacy_source):
                    out.append("#### Source `%s`" % p)
                    out.append("")
                    out.append("Database `%s` - %s" % (doc.legacy_source[p][0], doc.legacy_source[p][1]))
                    for n in doc.legacy_note.get(p, []):
                        out.append("")
                        out.append("> LOAD NOTE: " + n)
                    out.append("")
                    for t in [t for t in by_layer["legacy"] if t.startswith(p)]:
                        sec = table_section(con, t, doc, undocumented, "table")
                        sec[0] = sec[0].replace("#### ", "##### ")
                        if "_No purpose comment in the migration._" in sec:
                            sec[sec.index("_No purpose comment in the migration._")] = "Verbatim copy of the source table `%s` (original name without the `%s` prefix)." % (t[len(p):], p)
                        out.extend(sec)
                    for v in [v for v in views_by_layer["legacy"] if v.startswith(p)]:
                        sec = view_section(con, v, doc, undocumented)
                        sec[0] = sec[0].replace("#### ", "##### ")
                        if "_No purpose comment in the migration._" in sec:
                            sec[sec.index("_No purpose comment in the migration._")] = "Verbatim copy of the source view `%s`." % v[len(p):]
                        out.extend(sec)
                continue
            for t in by_layer[k]:
                out.extend(table_section(con, t, doc, undocumented, "table"))
            if views_by_layer[k]:
                out.append("##### Views (%s layer)" % title)
                out.append("")
                for v in views_by_layer[k]:
                    out.extend(view_section(con, v, doc, undocumented))
        # triggers
        out.append("## Triggers")
        out.append("")
        out.append("| Trigger | Table | Purpose |")
        out.append("|---|---|---|")
        for name, tbl in con.execute("SELECT name, tbl_name FROM sqlite_master WHERE type='trigger' ORDER BY tbl_name, name"):
            purpose = doc.trigger_purpose.get(name, "")
            if not purpose:
                if name.endswith("_touch"):
                    purpose = "Maintains `updated_at`."
                elif name.endswith("_origin_cleanup"):
                    purpose = "Removes the row's `origin_ref` entries when it is deleted."
                elif "_fts_" in name:
                    purpose = "Keeps the FTS5 index in sync."
                elif name.startswith("trg_origin_ref_entity"):
                    purpose = "Validates the polymorphic `origin_ref` target exists."
            out.append("| `%s` | `%s` | %s |" % (name, tbl, _esc(purpose)))
        out.append("")
        out.append("## Columns without a purpose comment")
        out.append("")
        if undocumented:
            out.append("%d column(s) have neither an SQL comment nor a glossary entry (their name is meant to be self-explanatory). Add a trailing `-- comment` in the migration to document them:" % len(undocumented))
            out.append("")
            bytable = {}  # type: Dict[str, List[str]]
            for u in undocumented:
                t, c = u.split(".", 1)
                bytable.setdefault(t, []).append(c)
            for t in sorted(bytable):
                out.append("* `%s`: %s" % (t, ", ".join("`%s`" % c for c in bytable[t])))
        else:
            out.append("None.")
        out.append("")
        return "\n".join(out).rstrip("\n") + "\n"
    finally:
        con.close()


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="exit 1 if docs/DATA_DICTIONARY.md is stale")
    ap.add_argument("--stdout", action="store_true", help="print instead of writing")
    args = ap.parse_args(argv)
    text = render()
    if args.stdout:
        sys.stdout.write(text)
        return 0
    if args.check:
        have = open(OUT, encoding="utf-8").read() if os.path.exists(OUT) else None
        if have != text:
            print("STALE docs/DATA_DICTIONARY.md - run tools/gen_data_dictionary.py")
            return 1
        print("OK    docs/DATA_DICTIONARY.md")
        return 0
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(text)
    print("WROTE docs/DATA_DICTIONARY.md (%d bytes)" % len(text.encode("utf-8")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
