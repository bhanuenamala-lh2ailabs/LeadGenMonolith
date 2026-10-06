#!/usr/bin/env python3
"""Generate the verbatim ``legacy_*`` migration files from the legacy SQLite databases.

Reads ``sqlite_master`` of every legacy database (opened read-only + immutable, never modified) and writes one
migration per database to ``db/migrations/00NN_legacy_<key>.sql``:

* every table is re-created with a prefix (``legacy_tam_companies`` ...), keeping ALL original columns, types,
  defaults, CHECKs, UNIQUEs, primary keys (incl. AUTOINCREMENT) and foreign keys (rewritten to the prefixed parents);
  DDL is emitted WITHOUT "IF NOT EXISTS" (like every other migration: a pre-existing object must fail loudly);
* every table gets one extra column ``_import_run_id INTEGER REFERENCES ops_import_run(import_run_id)`` so a bad
  import can be traced / reversed, plus an index ``ix_<table>_run`` on it (without the index, reversing an import or
  deleting an ops_import_run row full-scans every legacy table);
* every explicit index is re-created with a prefixed name; views are re-created with prefixed names and rewritten
  FROM / JOIN targets;
* tables stay NON-STRICT on purpose: this layer is "verbatim, nothing dropped" and the legacy data is loosely typed.

The output is deterministic (no timestamps), so ``--check`` can prove the committed migrations are exactly what the
generator produces.  Because the legacy databases are *inputs of an already-applied migration*, never edit a generated
file by hand and never regenerate over an applied migration whose checksum changed: if a legacy schema ever changes,
add a NEW migration instead.

Usage::

    .venv/bin/python tools/gen_legacy_ddl.py --check    # DEFAULT: exit 1 if any file differs from the generated text
    .venv/bin/python tools/gen_legacy_ddl.py --write    # write the files that do not exist yet
    .venv/bin/python tools/gen_legacy_ddl.py --write --allow-change   # overwrite a file that differs (only before it was applied)
    .venv/bin/python tools/gen_legacy_ddl.py --list     # show the registry

``--write`` REFUSES to replace a file whose current checksum is recorded in schema_migration of db/leadgen.sqlite (it is applied: add a
NEW numbered migration instead) and, without ``--allow-change``, any file that differs from the generated text.

Deliberately NOT generated (see docs/discovery/data-inventory.md section 2):
    legacy/companyOps/tam/data/tam.db                          0-byte stub
    legacy/companyOps/tam/data/tam.sqlite3.bak-20260929-*      older subset (user_version 5) of tam.sqlite3 - same schema, archive only
    *-wal / *-shm side files                                    empty
Python 3.9 compatible.
"""
from __future__ import print_function

import argparse
import hashlib
import os
import re
import sqlite3
import sys
from typing import Dict, List, NamedTuple, Optional, Set, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATIONS_DIR = os.path.join(ROOT, "db", "migrations")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

IMPORT_COL = "_import_run_id INTEGER REFERENCES ops_import_run(import_run_id)"

# (migration version, key, table prefix, path relative to repo root, purpose)
SOURCES = [
    (6, "tam", "legacy_tam_", "legacy/companyOps/tam/data/tam.sqlite3",
     "companyOps TAM build: 13 India sector categories, company universe, classification, ICP score, sourcing funnel events, CRM push log, Apollo cost ledger"),
    (7, "itsvc", "legacy_itsvc_", "legacy/hubspot/itsvc-tam/itsvc.db",
     "India IT-services universe from Apollo org search (candidates -> entities, classifications incl. placeholder priors, raw record index, site text)"),
    (8, "corpus", "legacy_corpus_", "legacy/hubspot/TAMBuildSpecs/_corpus/data/tam_corpus.sqlite",
     "Generic multi-vertical corpus (uk_proptech, india_cobol_ip): companies, identifiers, EAV indicators, ICP score + components, source registry / health"),
    (9, "pipeline", "legacy_pipeline_", "legacy/hubspot/lh2-pipeline/data/pipeline.sqlite",
     "July 2026 GoodFirms / IT-services scrape: raw listings, companies + gate, people, SignalHire quota, API cache"),
    (10, "gmaps", "legacy_gmaps_", "legacy/hubspot/lh2-pipeline/data/gmaps_cache.sqlite",
     "Google Places tile cache + monthly call counter (spend signal)"),
    (11, "radar", "legacy_radar_", "legacy/hubspot/godown/shutdown-radar/data/radar.sqlite",
     "Indian startup shutdown radar: candidates, CIN entities, evidence, scoring, liveness, prequal"),
    (12, "resolver", "legacy_resolver_", "legacy/hubspot/godown/founder_id/resolver.sqlite",
     "Founder / LinkedIn resolver state: per-domain results, per-source daily counters, block walls"),
    (13, "searchledger", "legacy_searchledger_", "legacy/hubspot/godown/founder_id/search_ledger.db",
     "Ledger of LinkedIn-search API calls (quota / cost accounting) and events"),
    (14, "itdirs", "legacy_itdirs_", "legacy/hubspot/godown/itdirs/state/scrape_state.sqlite",
     "IT-directory scraper resume state (regenerable; kept so nothing is dropped)"),
]

#: facts a loader must know, emitted into the generated header (verified against the legacy copies with PRAGMA foreign_key_check)
LOAD_NOTES = {
    "radar": ["candidate_entity has 2 rows whose cin has no entity row (rowid 10783 and 10785; both match_method='override', is_confirmed=1 - human decisions):",
              "a verbatim load under foreign_keys=ON fails at COMMIT.  Load with foreign_keys=OFF and record both in ops_dq_issue(rule_code='legacy_fk_orphan')."],
}

# --------------------------------------------------------------------------------------------- tokenizer
_TOKEN = re.compile(
    r"""(?P<ws>\s+)
      | (?P<lc>--[^\n]*)
      | (?P<bc>/\*.*?\*/)
      | (?P<str>'(?:[^']|'')*')
      | (?P<qid>"(?:[^"]|"")*"|`[^`]*`|\[[^\]]*\])
      | (?P<word>[A-Za-z_][A-Za-z0-9_$]*)
      | (?P<num>[0-9][0-9.]*)
      | (?P<p>.)""",
    re.S | re.X,
)

_STOP_AFTER_TABLE = set("""LEFT RIGHT FULL INNER OUTER CROSS NATURAL JOIN ON USING WHERE GROUP ORDER LIMIT HAVING UNION EXCEPT
INTERSECT WINDOW INDEXED NOT RETURNING""".split())
_TABLE_CONSTRAINT_START = {"CONSTRAINT", "PRIMARY", "UNIQUE", "CHECK", "FOREIGN"}


class Tok(NamedTuple):
    kind: str
    text: str
    start: int
    end: int

    @property
    def upper(self) -> str:
        return self.text.upper()

    @property
    def is_ident(self) -> bool:
        return self.kind in ("word", "qid")

    @property
    def name(self) -> str:
        if self.kind == "word":
            return self.text
        if self.text[0] == '"':
            return self.text[1:-1].replace('""', '"')
        return self.text[1:-1]


def tokenize(sql: str) -> List[Tok]:
    return [Tok(m.lastgroup, m.group(), m.start(), m.end()) for m in _TOKEN.finditer(sql)]


def significant(toks: List[Tok]) -> List[Tok]:
    return [t for t in toks if t.kind not in ("ws", "lc", "bc")]


def _apply_edits(sql: str, edits: List[Tuple[int, int, str]]) -> str:
    for start, end, text in sorted(edits, key=lambda e: (e[0], e[1]), reverse=True):
        sql = sql[:start] + text + sql[end:]
    return sql


def _prefixed(tok: Tok, prefix: str) -> str:
    name = tok.name
    new = prefix + name
    return new if re.match(r"^[A-Za-z_][A-Za-z0-9_$]*$", new) else '"' + new.replace('"', '""') + '"'


def _if_not_exists_edit(sig: List[Tok], kw_index: int) -> Optional[Tuple[int, int, str]]:
    """Remove IF NOT EXISTS after the TABLE/INDEX/VIEW keyword if the source has it (migrations never use it)."""
    nxt = sig[kw_index + 1: kw_index + 4]
    if len(nxt) == 3 and [t.upper for t in nxt] == ["IF", "NOT", "EXISTS"]:
        return (sig[kw_index].end, nxt[2].end, "")
    return None


def _name_index(sig: List[Tok], kw_index: int) -> int:
    i = kw_index + 1
    if [t.upper for t in sig[i:i + 3]] == ["IF", "NOT", "EXISTS"]:
        i += 3
    return i


def rewrite_ddl(sql: str, kind: str, prefix: str, tables: Set[str]) -> str:
    """Rewrite one CREATE statement: prefix names, retarget REFERENCES / FROM / JOIN / ON, drop IF NOT EXISTS,
    and (for tables) append the _import_run_id column."""
    sql = sql.strip().rstrip(";").rstrip()
    toks = tokenize(sql)
    sig = significant(toks)
    edits = []  # type: List[Tuple[int, int, str]]

    # locate the object keyword (skip CREATE [TEMP] [UNIQUE])
    kw = 1
    while sig[kw].upper in ("TEMP", "TEMPORARY", "UNIQUE"):
        kw += 1
    if sig[0].upper != "CREATE" or sig[kw].upper != kind.upper():
        raise ValueError("unexpected statement for %s: %r" % (kind, sql[:80]))
    e = _if_not_exists_edit(sig, kw)
    if e:
        edits.append(e)
    ni = _name_index(sig, kw)
    name_tok = sig[ni]
    edits.append((name_tok.start, name_tok.end, _prefixed(name_tok, prefix)))

    if kind == "table":
        for i, t in enumerate(sig):
            if t.upper == "REFERENCES" and t.kind == "word":
                tgt = sig[i + 1]
                if tgt.is_ident and tgt.name in tables:
                    edits.append((tgt.start, tgt.end, _prefixed(tgt, prefix)))
        # body = first '(' after the name .. its matching ')'
        open_i = next(i for i in range(ni + 1, len(sig)) if sig[i].text == "(" and sig[i].kind == "p")
        depth = 0
        close_i = None
        elem_starts = [open_i + 1]
        for i in range(open_i, len(sig)):
            s = sig[i]
            if s.kind == "p" and s.text == "(":
                depth += 1
            elif s.kind == "p" and s.text == ")":
                depth -= 1
                if depth == 0:
                    close_i = i
                    break
            elif s.kind == "p" and s.text == "," and depth == 1:
                elem_starts.append(i + 1)
        assert close_i is not None
        constraint_at = None
        for st in elem_starts:
            if sig[st].kind == "word" and sig[st].upper in _TABLE_CONSTRAINT_START:
                constraint_at = sig[st]
                break
        if constraint_at is not None:
            edits.append((constraint_at.start, constraint_at.start, IMPORT_COL + ",\n  "))
        else:
            last = sig[close_i - 1]
            close_pos = sig[close_i].start
            mid = sql[last.end:close_pos]
            if not mid.endswith("\n"):
                mid = mid.rstrip(" \t") + "\n"
            edits.append((last.end, close_pos, "," + mid + "  " + IMPORT_COL + "\n"))
    elif kind == "index":
        on_i = next(i for i in range(ni + 1, len(sig)) if sig[i].upper == "ON")
        tgt = sig[on_i + 1]
        if tgt.name not in tables:
            raise ValueError("index on unknown table %r" % tgt.name)
        edits.append((tgt.start, tgt.end, _prefixed(tgt, prefix)))
    elif kind == "view":
        i = 0
        while i < len(sig):
            if sig[i].kind == "word" and sig[i].upper in ("FROM", "JOIN"):
                is_from = sig[i].upper == "FROM"
                j = i + 1
                while j < len(sig):
                    tok = sig[j]
                    if not tok.is_ident or tok.name not in tables:
                        break
                    edits.append((tok.start, tok.end, _prefixed(tok, prefix)))
                    nxt = sig[j + 1] if j + 1 < len(sig) else None
                    if nxt is not None and nxt.kind == "word" and nxt.upper == "AS":
                        k = j + 3
                    elif nxt is not None and nxt.is_ident and nxt.upper not in _STOP_AFTER_TABLE:
                        k = j + 2
                    else:
                        # no alias: keep the original name usable for qualified columns (table.col)
                        edits.append((tok.end, tok.end, " AS " + tok.text))
                        k = j + 1
                    if is_from and k < len(sig) and sig[k].kind == "p" and sig[k].text == ",":
                        j = k + 1
                        continue
                    break
            i += 1
    else:
        raise NotImplementedError("unsupported object kind %r" % kind)
    out = _apply_edits(sql, edits)
    # a trailing "-- comment" on the last line would swallow the terminating ';'
    return out + ("\n;" if toks and toks[-1].kind == "lc" else ";")


# --------------------------------------------------------------------------------------------- schema reading
class SchemaObject(NamedTuple):
    type: str
    name: str
    tbl_name: str
    sql: str


def open_readonly(path: str) -> sqlite3.Connection:
    uri = "file:%s?mode=ro&immutable=1" % path
    return sqlite3.connect(uri, uri=True)


def read_schema(path: str) -> List[SchemaObject]:
    con = open_readonly(path)
    try:
        rows = con.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master "
            "WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()
    finally:
        con.close()
    return [SchemaObject(*r) for r in rows]


def fingerprint(objs: List[SchemaObject]) -> str:
    h = hashlib.sha256()
    for o in sorted(objs, key=lambda x: (x.type, x.name)):
        h.update(("%s|%s|%s|%s\n" % (o.type, o.name, o.tbl_name, o.sql)).encode("utf-8"))
    return h.hexdigest()


def render_migration(src: tuple) -> str:
    version, key, prefix, rel, purpose = src
    path = os.path.join(ROOT, rel)
    objs = read_schema(path)
    tables = {o.name for o in objs if o.type == "table"}
    t_objs = sorted((o for o in objs if o.type == "table"), key=lambda o: o.name)
    i_objs = sorted((o for o in objs if o.type == "index"), key=lambda o: o.name)
    v_objs = sorted((o for o in objs if o.type == "view"), key=lambda o: o.name)
    other = [o for o in objs if o.type not in ("table", "index", "view")]
    if other:
        raise NotImplementedError("unsupported schema objects in %s: %s" % (rel, [(o.type, o.name) for o in other]))

    out = []
    out.append("-- %04d_legacy_%s.sql" % (version, key))
    out.append("-- GENERATED by tools/gen_legacy_ddl.py from the legacy database schema - DO NOT EDIT BY HAND.")
    out.append("--   verify: .venv/bin/python tools/gen_legacy_ddl.py --check      (a file that is not applied yet may be refreshed with --write --allow-change)")
    out.append("--")
    out.append("-- Source database : %s   (opened read-only / immutable; never modified)" % rel)
    out.append("-- Purpose         : %s" % purpose)
    out.append("-- Source schema   : %d tables, %d indexes, %d views; sqlite_master sha256 = %s" % (len(t_objs), len(i_objs), len(v_objs), fingerprint(objs)))
    out.append("-- Prefix          : %s" % prefix)
    out.append("--")
    out.append("-- VERBATIM IMPORT LAYER.  Nothing is dropped: every table keeps its original columns, types, defaults, CHECKs, UNIQUEs,")
    out.append("-- primary keys (incl. AUTOINCREMENT) and foreign keys (retargeted to the prefixed parents).  Each table gets one extra")
    out.append("-- column _import_run_id (-> ops_import_run), indexed (ix_<table>_run), so an import can be traced and reversed (DELETE ... WHERE _import_run_id = ?).")
    out.append("-- Tables are NOT STRICT on purpose (legacy data is loosely typed); canonical tables are.  The declared foreign keys are kept, so")
    out.append("-- importers must load parents before children; legacy rows that already violate their own foreign keys must be loaded anyway (with")
    out.append("-- PRAGMA foreign_keys = OFF, set outside a transaction), listed by PRAGMA foreign_key_check and reported to ops_dq_issue")
    out.append("-- (rule_code 'legacy_fk_orphan'), never dropped.  ON DELETE actions are kept exactly as in the source: nothing under leadgen/ deletes from legacy_*.")
    out.append("-- Canonical rows link back to these rows through origin_ref(source_system, source_table, source_pk); source_table is the ORIGINAL name")
    out.append("-- (not the prefixed one) and tables without a primary key (see the table list) use their rowid as source_pk - copy rowid explicitly on load.")
    if key in LOAD_NOTES:
        out.append("--")
        for line in LOAD_NOTES[key]:
            out.append("-- LOAD NOTE: " + line)
    out.append("")
    for o in t_objs:
        out.append("-- table %s -> %s%s" % (o.name, prefix, o.name))
        out.append(rewrite_ddl(o.sql, "table", prefix, tables))
        out.append("")
    for o in i_objs:
        out.append(rewrite_ddl(o.sql, "index", prefix, tables))
    if i_objs:
        out.append("")
    out.append("-- _import_run_id indexes (import tracing / reversal / FK maintenance on ops_import_run)")
    for o in t_objs:
        out.append("CREATE INDEX ix_%s%s_run ON %s%s(_import_run_id);" % (prefix, o.name, prefix, o.name))
    out.append("")
    for o in v_objs:
        out.append("-- view %s -> %s%s" % (o.name, prefix, o.name))
        out.append(rewrite_ddl(o.sql, "view", prefix, tables))
        out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


def migration_path(src: tuple) -> str:
    return os.path.join(MIGRATIONS_DIR, "%04d_legacy_%s.sql" % (src[0], src[1]))


# --------------------------------------------------------------------------------------------- verification
def _table_info(con, table):
    return [(r[1], (r[2] or "").upper(), r[3], r[4], r[5]) for r in con.execute('PRAGMA table_info("%s")' % table)]


def _fk_list(con, table, prefix=""):
    rows = con.execute('PRAGMA foreign_key_list("%s")' % table).fetchall()
    out = []
    for r in rows:
        # id, seq, table, from, to, on_update, on_delete, match
        parent = r[2]
        if prefix and parent.startswith(prefix):
            parent = parent[len(prefix):]
        out.append((r[0], r[1], parent, r[3], r[4], r[5], r[6]))
    return sorted(out)


def _index_sig(con, table):
    sig = set()
    for r in con.execute('PRAGMA index_list("%s")' % table).fetchall():
        # seq, name, unique, origin, partial
        cols = tuple(c[2] for c in con.execute('PRAGMA index_xinfo("%s")' % r[1]).fetchall() if c[5] == 1)
        if cols == ("_import_run_id",):
            continue                                      # the generator's own tracing index
        sig.add((cols, r[2], r[3], r[4]))
    return sig


def verify(src: tuple, text: str) -> List[str]:
    """Apply the generated migration to an in-memory DB and compare its structure with the source database."""
    from leadgen.db import split_sql_statements

    version, key, prefix, rel, _ = src
    problems = []  # type: List[str]
    gen = sqlite3.connect(":memory:", isolation_level=None)
    gen.execute("PRAGMA foreign_keys = OFF")
    gen.execute("CREATE TABLE ops_import_run (import_run_id INTEGER PRIMARY KEY)")
    try:
        for stmt in split_sql_statements(text):
            gen.execute(stmt)
    except sqlite3.Error as exc:
        return ["generated SQL failed to execute: %s" % exc]
    srcdb = open_readonly(os.path.join(ROOT, rel))
    try:
        s_tables = [r[0] for r in srcdb.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        for t in s_tables:
            g = prefix + t
            s_cols, g_cols = _table_info(srcdb, t), _table_info(gen, g)
            if not g_cols:
                problems.append("table %s missing" % g)
                continue
            if not g_cols or g_cols[-1][0] != "_import_run_id":
                problems.append("table %s: _import_run_id is not the last column" % g)
            elif g_cols[:-1] != s_cols:
                problems.append("table %s: column definitions differ" % g)
            s_fk = _fk_list(srcdb, t)
            g_fk = [f for f in _fk_list(gen, g, prefix) if f[3] != "_import_run_id"]
            if [f[1:] for f in s_fk] != [f[1:] for f in g_fk]:
                problems.append("table %s: foreign keys differ (%s vs %s)" % (g, s_fk, g_fk))
            if _index_sig(srcdb, t) != _index_sig(gen, g):
                problems.append("table %s: indexes / unique constraints differ" % g)
            sql_s = srcdb.execute("SELECT sql FROM sqlite_master WHERE name=?", (t,)).fetchone()[0]
            if "AUTOINCREMENT" in sql_s.upper() and "AUTOINCREMENT" not in gen.execute("SELECT sql FROM sqlite_master WHERE name=?", (g,)).fetchone()[0].upper():
                problems.append("table %s: AUTOINCREMENT lost" % g)
        s_views = [r[0] for r in srcdb.execute("SELECT name FROM sqlite_master WHERE type='view'")]
        for v in s_views:
            g = prefix + v
            if [c[0] for c in _table_info(srcdb, v)] != [c[0] for c in _table_info(gen, g)]:
                problems.append("view %s: column list differs" % g)
            try:
                gen.execute('SELECT * FROM "%s" LIMIT 1' % g).fetchall()
            except sqlite3.Error as exc:
                problems.append("view %s does not run on an empty database: %s" % (g, exc))
        s_idx = {r[0] for r in srcdb.execute("SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL")}
        g_idx = {r[0][len(prefix):] for r in gen.execute("SELECT name FROM sqlite_master WHERE type='index' AND name LIKE ? ESCAPE '\\'",
                                                         (prefix.replace("_", "\\_") + "%",))}
        if s_idx != g_idx:
            problems.append("explicit index names differ: missing %s extra %s" % (sorted(s_idx - g_idx), sorted(g_idx - s_idx)))
    finally:
        srcdb.close()
        gen.close()
    return problems


# --------------------------------------------------------------------------------------------- CLI
def _is_applied(text: str, real: Optional[str] = None) -> bool:
    """True if the real database exists and has recorded the sha256 of this exact file text (read-only; never creates the database)."""
    real = real or os.path.join(ROOT, "db", "leadgen.sqlite")
    if not os.path.exists(real):
        return False
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    try:
        con = sqlite3.connect("file:%s?mode=ro" % real, uri=True)
        try:
            return con.execute("SELECT 1 FROM schema_migration WHERE checksum_sha256 = ?", (sha,)).fetchone() is not None
        finally:
            con.close()
    except sqlite3.Error:
        return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="write the migration files that do not exist yet (see --allow-change)")
    mode.add_argument("--check", action="store_true", help="exit 1 if a migration file differs from the generated text (the default)")
    mode.add_argument("--list", action="store_true", help="show the source registry and exit")
    ap.add_argument("--no-verify", action="store_true", help="skip the structural comparison against the source databases")
    ap.add_argument("--only", help="comma-separated keys (e.g. tam,itsvc)")
    ap.add_argument("--allow-change", action="store_true", help="with --write: overwrite an existing file whose text differs (never an applied one)")
    args = ap.parse_args(argv)
    if not args.write and not args.list:
        args.check = True

    only = set(args.only.split(",")) if args.only else None
    srcs = [s for s in SOURCES if only is None or s[1] in only]
    if args.list:
        for s in srcs:
            print("%04d  %-13s %-22s %s" % (s[0], s[1], s[2], s[3]))
        return 0

    rc = 0
    for src in srcs:
        rel = src[3]
        if not os.path.exists(os.path.join(ROOT, rel)):
            print("SKIP  %-13s source database not found: %s" % (src[1], rel))
            if args.check:
                rc = 1
            continue
        text = render_migration(src)
        if not args.no_verify:
            problems = verify(src, text)
            if problems:
                rc = 1
                print("FAIL  %-13s structural verification" % src[1])
                for p in problems:
                    print("        - " + p)
                continue
        path = migration_path(src)
        if args.check:
            have = open(path).read() if os.path.exists(path) else None
            if have != text:
                rc = 1
                print("DIFF  %-13s %s differs from generated text" % (src[1], os.path.relpath(path, ROOT)))
            else:
                print("OK    %-13s %s" % (src[1], os.path.relpath(path, ROOT)))
        else:
            have = open(path).read() if os.path.exists(path) else None
            if have == text:
                print("SAME  %-13s %s" % (src[1], os.path.relpath(path, ROOT)))
                continue
            if have is not None and _is_applied(have):
                rc = 1
                print("REFUSED %-11s %s is already APPLIED to db/leadgen.sqlite: add a NEW numbered migration instead of regenerating" % (src[1], os.path.relpath(path, ROOT)))
                continue
            if have is not None and not args.allow_change:
                rc = 1
                print("REFUSED %-11s %s exists and differs from the generated text; pass --allow-change if it was never applied" % (src[1], os.path.relpath(path, ROOT)))
                continue
            os.makedirs(MIGRATIONS_DIR, exist_ok=True)
            with open(path, "w") as fh:
                fh.write(text)
            print("WROTE %-13s %s (%d bytes)" % (src[1], os.path.relpath(path, ROOT), len(text)))
    return rc


if __name__ == "__main__":
    sys.exit(main())
