"""SQLite access layer: connect(), versioned migrations, status(), doctor(), reference-data seeding.

* ``connect()``  - one place that sets every PRAGMA from docs/ARCHITECTURE.md section 1
  (journal_mode=WAL, foreign_keys=ON, busy_timeout=30000, synchronous=NORMAL) plus recursive_triggers=ON (so INSERT OR REPLACE fires the
  delete triggers - the append-only audit log depends on it), trusted_schema=OFF, cell_size_check=ON and WAL size limits; refuses a SQLite
  older than 3.37 (STRICT tables, generated columns, PRAGMA table_list); sets the row factory and autocommit mode
  (``isolation_level=None``: transactions are explicit - see :func:`transaction`).  **Autocommit means every statement outside
  ``with transaction(con):`` commits on its own** - importers must wrap their writes (``executemany`` included) in the context manager.
* ``migrate()``  - applies pending ``db/migrations/NNNN_name.sql`` files strictly in order, each inside ONE transaction
  together with its ``schema_migration`` row and ``PRAGMA user_version``; stores the sha256 of the file; REFUSES to run
  if an already-applied migration's file changed, vanished, or was renamed, or if a new file would slot in below an
  applied one.  Never edit an applied migration: add the next-numbered file.  Before it touches a database that already has
  applied migrations it writes a ``VACUUM INTO`` backup to ``<db dir>/backup/`` (there are no down-migrations: the backup is the rollback).
  After every migration ``PRAGMA foreign_key_check`` must not show MORE violations than before it.  A migration whose FIRST line is
  ``-- migrate: foreign_keys=off`` is a table-REBUILD migration (create new / copy / drop / rename - the only way SQLite can change a
  CHECK, FK or generated column): it runs with ``foreign_keys=OFF`` (set outside the transaction, restored afterwards) so that
  DROP TABLE does not cascade into child tables, and is rolled back if the rebuild leaves new foreign-key violations.
* ``status()``   - what is applied / pending / broken, without raising.
* ``doctor()``   - integrity_check, foreign_key_check (+ orphan counts per FK), FTS integrity, schema drift against a freshly migrated
  in-memory schema, row counts, and health signals (unmapped / missing stages, open data-quality errors, stale running jobs,
  deal-state drift, near-duplicate events, ...).  Read-only: it never creates a database file.
* ``python -m leadgen.db {migrate,status,doctor,seed,analyze} [--db PATH]`` is a tiny CLI for the same functions.

The database file is db/leadgen.sqlite unless the env var LEADGEN_DB (or an explicit path) says otherwise.  Relative
paths from LEADGEN_DB / the default are resolved against the project root, so behaviour does not depend on the cwd.

Writers must never use INSERT OR REPLACE / REPLACE INTO (a REPLACE is DELETE + INSERT and fires ON DELETE cascades): use
``INSERT ... ON CONFLICT DO UPDATE`` (tests/test_schema.py greps leadgen/ for the idiom).

Python 3.9 compatible.
"""
import contextlib
import datetime
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from typing import Any, Dict, Iterator, List, NamedTuple, Optional, Tuple, Union

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(PACKAGE_DIR)
MIGRATIONS_DIR = os.path.join(PROJECT_ROOT, "db", "migrations")
DEFAULT_DB = os.path.join("db", "leadgen.sqlite")
ENV_DB = "LEADGEN_DB"

#: PRAGMAs applied to every connection, in order (busy_timeout first so the others can wait for a lock).
PRAGMAS = (
    ("busy_timeout", 30000),
    ("journal_mode", "WAL"),
    ("foreign_keys", "ON"),
    ("synchronous", "NORMAL"),
    ("recursive_triggers", "ON"),      # REPLACE fires delete triggers (audit log append-only); the *_touch triggers are written for it
)
#: safety / housekeeping on top of the spec (all per-connection)
SAFETY_PRAGMAS = (
    ("trusted_schema", "OFF"),         # views / triggers / CHECKs may only call innocuous SQL functions
    ("cell_size_check", "ON"),         # detect corrupt b-tree cells early
    ("journal_size_limit", 67108864),  # WAL file is truncated back to 64 MB after a checkpoint
    ("wal_autocheckpoint", 1000),
)
#: harmless tuning on top of the spec
TUNING_PRAGMAS = (("temp_store", "MEMORY"), ("cache_size", -65536))
#: STRICT tables (3.37), generated columns (3.31), PRAGMA table_list (3.37), FTS5, JSON1, UPSERT, window functions
MIN_SQLITE = (3, 37, 0)
#: a run / sync job still 'running' after this many hours is reported as stale by doctor()
STALE_RUNNING_HOURS = 6

_MIGRATION_RE = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")


class MigrationError(RuntimeError):
    """A migration could not be applied or the ledger is inconsistent with the files."""


class ChecksumMismatchError(MigrationError):
    """An applied migration file was modified after it was applied."""


class MigrationOrderError(MigrationError):
    """Migration files and the applied ledger are out of order (gap, or a new file below an applied one)."""


DbLike = Union[None, str, "os.PathLike[str]", sqlite3.Connection]


# ------------------------------------------------------------------------------------------------- connection
def db_path(path: Optional[str] = None) -> str:
    """Resolve the database path: explicit argument (as given, cwd-relative) > $LEADGEN_DB > db/leadgen.sqlite
    (the last two relative to the project root).  ':memory:' is passed through."""
    if path is not None:
        p = os.fspath(path)
        return p if p == ":memory:" else os.path.abspath(p)
    p = os.environ.get(ENV_DB) or DEFAULT_DB
    return p if p == ":memory:" else (p if os.path.isabs(p) else os.path.join(PROJECT_ROOT, p))


def utc_now_iso() -> str:
    """Current UTC instant in the database's timestamp format (millisecond ISO-8601 ending in Z)."""
    now = datetime.datetime.utcnow()
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (now.microsecond // 1000)


def _set_wal(con: sqlite3.Connection, wait_seconds: float = 30.0) -> None:
    """``PRAGMA journal_mode = WAL`` without racing: switching a fresh database to WAL needs an exclusive lock and fails INSTANTLY with
    'database is locked' (busy_timeout is not honoured) when another connection is initialising at the same time.  Skip it when the
    database is already in WAL mode (the persistent, common case) and otherwise retry until ``wait_seconds`` have passed."""
    if str(con.execute("PRAGMA journal_mode").fetchone()[0]).lower() == "wal":
        return
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            con.execute("PRAGMA journal_mode = WAL")
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc) or time.monotonic() > deadline:
                raise
            time.sleep(0.05)


def connect(path: Optional[str] = None, readonly: bool = False, create_dir: bool = True, must_exist: bool = False) -> sqlite3.Connection:
    """Open the database with every PRAGMA from ARCHITECTURE.md, ``sqlite3.Row`` rows and explicit transactions.

    ``readonly=True`` opens with mode=ro (no file is created, no PRAGMA that writes is attempted).
    ``must_exist=True`` raises FileNotFoundError instead of creating a missing file (everything except :func:`migrate` uses it).
    """
    if sqlite3.sqlite_version_info < MIN_SQLITE:
        raise RuntimeError("SQLite %s is too old: leadgen needs >= %s (STRICT tables, generated columns, PRAGMA table_list)"
                           % (sqlite3.sqlite_version, ".".join(str(v) for v in MIN_SQLITE)))
    target = db_path(path)
    if target == ":memory:":
        con = sqlite3.connect(":memory:", isolation_level=None)
    elif readonly or must_exist:
        if not os.path.exists(target):
            raise FileNotFoundError("database not found: %s" % target)
        if readonly:
            con = sqlite3.connect("file:%s?mode=ro" % target, uri=True, isolation_level=None, timeout=30)
        else:
            con = sqlite3.connect(target, isolation_level=None, timeout=30)
    else:
        if create_dir:
            os.makedirs(os.path.dirname(target), exist_ok=True)
        con = sqlite3.connect(target, isolation_level=None, timeout=30)
    con.row_factory = sqlite3.Row
    for name, value in PRAGMAS + SAFETY_PRAGMAS:
        if readonly and name in ("journal_mode", "synchronous", "journal_size_limit", "wal_autocheckpoint"):
            continue
        if target == ":memory:" and name in ("journal_mode", "journal_size_limit", "wal_autocheckpoint"):
            continue
        if name == "journal_mode":
            _set_wal(con)
        else:
            con.execute("PRAGMA %s = %s" % (name, value))
    for name, value in TUNING_PRAGMAS:
        con.execute("PRAGMA %s = %s" % (name, value))
    if con.execute("PRAGMA foreign_keys").fetchone()[0] != 1:  # pragma: no cover - build without FK support
        con.close()
        raise RuntimeError("SQLite was built without foreign key support")
    return con


@contextlib.contextmanager
def transaction(con: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """``with transaction(con):`` - BEGIN IMMEDIATE ... COMMIT, ROLLBACK on any exception.  Not re-entrant.

    The ONLY supported way to write: IMMEDIATE takes the write lock up front so ``busy_timeout`` applies.  A deferred transaction that
    reads first and writes later fails INSTANTLY with 'database is locked' when another writer committed in between, whatever the
    timeout.  For consistent multi-query reads use :func:`read_transaction`."""
    con.execute("BEGIN IMMEDIATE")
    try:
        yield con
    except BaseException:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
    else:
        con.execute("COMMIT")


@contextlib.contextmanager
def read_transaction(con: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """``with read_transaction(con):`` - a deferred BEGIN for a consistent snapshot across several SELECTs (report generation).  Keep it
    short: an open read transaction blocks WAL checkpoints.  Writing inside it is a programming error (use :func:`transaction`)."""
    con.execute("BEGIN")
    try:
        yield con
    finally:
        if con.in_transaction:
            con.execute("ROLLBACK")


@contextlib.contextmanager
def _open(db: DbLike, create: bool = False) -> Iterator[sqlite3.Connection]:
    """Yield a connection for a path / Connection.  A missing database file is only created when ``create`` is True (migrate)."""
    if isinstance(db, sqlite3.Connection):
        yield db
    else:
        con = connect(db, must_exist=not create)
        try:
            yield con
        finally:
            try:
                if not con.in_transaction:
                    con.execute("PRAGMA optimize")           # cheap; keeps sqlite_stat1 fresh for the planner
            except sqlite3.Error:                            # pragma: no cover - read-only / locked database
                pass
            con.close()


# ------------------------------------------------------------------------------------------------ SQL splitting
def _has_code(chunk: str) -> bool:
    stripped = re.sub(r"/\*.*?\*/", "", chunk, flags=re.S)
    stripped = re.sub(r"--[^\n]*", "", stripped)
    return bool(stripped.strip())


def split_sql_statements(sql: str) -> List[str]:
    """Split a script into complete statements using SQLite's own sqlite3_complete (triggers, CASE...END and
    comments are handled).  Comment-only fragments are dropped.  Raises MigrationError on a truncated statement."""
    stmts, buf = [], []  # type: List[str], List[str]
    for line in sql.splitlines(keepends=True):
        buf.append(line)
        chunk = "".join(buf)
        if sqlite3.complete_statement(chunk):
            if _has_code(chunk):
                stmts.append(chunk.strip())
            buf = []
    rest = "".join(buf)
    if _has_code(rest):
        raise MigrationError("incomplete SQL statement at end of script: %r" % rest.strip()[:120])
    return stmts


# --------------------------------------------------------------------------------------------------- migrations
class Migration(NamedTuple):
    version: int
    name: str
    filename: str
    path: str
    sql: str
    checksum: str


def discover_migrations(directory: Optional[str] = None) -> List[Migration]:
    """Migration files sorted by version.  Validates the NNNN_name.sql pattern, unique and gap-free numbering from 1."""
    directory = directory or MIGRATIONS_DIR
    found = []  # type: List[Migration]
    for fn in sorted(os.listdir(directory)):
        if not fn.endswith(".sql"):
            continue
        m = _MIGRATION_RE.match(fn)
        if not m:
            raise MigrationError("bad migration filename %r (expected NNNN_snake_case.sql)" % fn)
        path = os.path.join(directory, fn)
        with open(path, "rb") as fh:
            raw = fh.read()
        found.append(Migration(int(m.group(1)), m.group(2), fn, path, raw.decode("utf-8"), hashlib.sha256(raw).hexdigest()))
    found.sort(key=lambda x: x.version)
    versions = [x.version for x in found]
    if len(versions) != len(set(versions)):
        raise MigrationOrderError("duplicate migration version numbers: %s" % versions)
    if versions and versions != list(range(1, len(versions) + 1)):
        raise MigrationOrderError("migration numbers must be contiguous from 0001, found %s" % versions)
    return found


def _has_ledger(con: sqlite3.Connection) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migration'").fetchone() is not None


def applied_versions(con: sqlite3.Connection) -> List[Dict[str, Any]]:
    if not _has_ledger(con):
        return []
    rows = con.execute("SELECT version, name, checksum_sha256, applied_at, execution_ms FROM schema_migration ORDER BY version").fetchall()
    return [dict(r) for r in rows]


def _ledger_problems(applied: List[Dict[str, Any]], files: List[Migration]) -> List[Tuple[str, str, int]]:
    """[(kind, message, version)] with kind in {'checksum', 'order', 'missing', 'name'}."""
    problems = []  # type: List[Tuple[str, str, int]]
    by_version = {f.version: f for f in files}
    for a in applied:
        f = by_version.get(a["version"])
        if f is None:
            problems.append(("missing", "applied migration %04d_%s has no file in the migrations directory" % (a["version"], a["name"]), a["version"]))
            continue
        if f.name != a["name"]:
            problems.append(("name", "migration %04d was applied as %r but the file is now named %r" % (a["version"], a["name"], f.name), a["version"]))
        if f.checksum != a["checksum_sha256"]:
            problems.append(("checksum", "migration %s was modified after it was applied (applied sha256 %s..., file sha256 %s...); "
                                         "never edit an applied migration, add a new one" % (f.filename, a["checksum_sha256"][:12], f.checksum[:12]), a["version"]))
    applied_set = {a["version"] for a in applied}
    if applied_set:
        top = max(applied_set)
        gaps = [f.version for f in files if f.version < top and f.version not in applied_set]
        if gaps:
            problems.append(("order", "migration(s) %s are numbered below the highest applied version %d but were never applied" % (gaps, top), top))
    return problems


def _raise_for(problems: List[Tuple[str, str, int]]) -> None:
    if not problems:
        return
    msg = "; ".join(m for _, m, _v in problems)
    kinds = {k for k, _m, _v in problems}
    if "checksum" in kinds:
        raise ChecksumMismatchError(msg)
    if "order" in kinds:
        raise MigrationOrderError(msg)
    raise MigrationError(msg)


def pending_migrations(con: sqlite3.Connection, directory: Optional[str] = None) -> List[Migration]:
    files = discover_migrations(directory)
    applied = applied_versions(con)
    _raise_for(_ledger_problems(applied, files))
    done = {a["version"] for a in applied}
    return [f for f in files if f.version not in done]


_REBUILD_RE = re.compile(r"\A\s*--\s*migrate:\s*foreign_keys\s*=\s*off\b", re.I)


def is_rebuild_migration(m: "Migration") -> bool:
    """True if the migration declares  ``-- migrate: foreign_keys=off``  as the first line of the file (see module docstring)."""
    return bool(_REBUILD_RE.match(m.sql.lstrip("\ufeff")))


def _fk_violation_counts(con: sqlite3.Connection) -> Dict[Tuple[str, str], int]:
    """{(child table, parent table): violations} from PRAGMA foreign_key_check."""
    agg = {}  # type: Dict[Tuple[str, str], int]
    for r in con.execute("PRAGMA foreign_key_check").fetchall():
        k = (r[0], r[2])
        agg[k] = agg.get(k, 0) + 1
    return agg


def _apply_one(con: sqlite3.Connection, m: Migration) -> bool:
    """Apply one migration atomically.  Returns True if it was applied, False if another process won the race."""
    started = time.monotonic()
    rebuild = is_rebuild_migration(m)
    if rebuild:
        if con.in_transaction:
            raise MigrationError("migration %s needs foreign_keys=OFF, which cannot be changed inside a transaction" % m.filename)
        con.execute("PRAGMA foreign_keys = OFF")
    try:
        con.execute("BEGIN IMMEDIATE")
        try:
            if _has_ledger(con) and con.execute("SELECT 1 FROM schema_migration WHERE version = ?", (m.version,)).fetchone():
                con.execute("ROLLBACK")          # another process won the race; nothing to do
                return False
            before = _fk_violation_counts(con)
            for stmt in split_sql_statements(m.sql):
                try:
                    con.execute(stmt)
                except (sqlite3.Warning, sqlite3.ProgrammingError) as exc:
                    if "one statement" in str(exc):
                        raise MigrationError("migration %s: two statements on one line (%r...) - put every statement on its own line"
                                             % (m.filename, stmt[:60])) from exc
                    raise
            after = _fk_violation_counts(con)
            worse = {k: n for k, n in after.items() if n > before.get(k, 0)}
            if worse:
                raise MigrationError("migration %s leaves new foreign-key violations: %s" % (m.filename, worse))
            con.execute(
                "INSERT INTO schema_migration (version, name, checksum_sha256, execution_ms) VALUES (?, ?, ?, ?)",
                (m.version, m.name, m.checksum, int((time.monotonic() - started) * 1000)),
            )
            con.execute("PRAGMA user_version = %d" % m.version)
            con.execute("COMMIT")
            return True
        except BaseException as exc:
            if con.in_transaction:
                try:
                    con.execute("ROLLBACK")
                except sqlite3.Error:  # pragma: no cover
                    pass
            if isinstance(exc, MigrationError):
                raise
            raise MigrationError("migration %s failed and was rolled back: %s: %s" % (m.filename, type(exc).__name__, exc)) from exc
    finally:
        if rebuild:
            con.execute("PRAGMA foreign_keys = ON")


class MigrationResult(list):
    """List of the migration filenames applied (or that WOULD be applied); ``.backup`` is the path of the pre-migration backup, if one was taken."""
    backup = None  # type: Optional[str]


def backup_database(con: sqlite3.Connection, tag: str, directory: Optional[str] = None) -> str:
    """``VACUUM INTO`` a timestamped copy under <db dir>/backup/ (the rollback for a migration; there are no down-migrations)."""
    main = con.execute("PRAGMA database_list").fetchone()["file"]
    if not main:
        raise MigrationError("cannot back up an in-memory database")
    directory = directory or os.path.join(os.path.dirname(main), "backup")
    os.makedirs(directory, exist_ok=True)
    dest = os.path.join(directory, "%s-%s-%s.sqlite" % (os.path.splitext(os.path.basename(main))[0], tag,
                                                          datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%S%f")))
    con.execute("VACUUM INTO ?", (dest,))
    return dest


def migrate(db: DbLike = None, migrations_dir: Optional[str] = None, target: Optional[int] = None,
            dry_run: bool = False, backup: bool = True) -> List[str]:
    """Apply every pending migration in order (up to ``target`` if given).  Returns the filenames THIS call applied (or that WOULD be
    applied with ``dry_run=True``; a dry run never creates the database).  Idempotent: a second call applies nothing.  Raises
    ChecksumMismatchError / MigrationOrderError / MigrationError without touching the database when the ledger disagrees with the
    files.  With ``backup=True`` (default) a database that already has applied migrations is first copied with VACUUM INTO."""
    if dry_run and not isinstance(db, sqlite3.Connection):
        p = db_path(db)
        if p != ":memory:" and not os.path.exists(p):
            files = discover_migrations(migrations_dir)
            return MigrationResult(f.filename for f in files if target is None or f.version <= target)
    with _open(db, create=True) as con:
        pending = pending_migrations(con, migrations_dir)
        if target is not None:
            pending = [m for m in pending if m.version <= target]
        if dry_run:
            return MigrationResult(m.filename for m in pending)
        done = MigrationResult()
        if pending and backup and applied_versions(con) and con.execute("PRAGMA database_list").fetchone()["file"]:
            done.backup = backup_database(con, "pre-%04d" % pending[0].version)
        for m in pending:
            if _apply_one(con, m):
                done.append(m.filename)
        if done:
            con.execute("PRAGMA optimize")
        return done


def analyze(db: DbLike = None) -> None:
    """Refresh planner statistics (sqlite_stat1).  Call after every bulk load / import-legacy; migrate() and every closed connection run
    the cheaper PRAGMA optimize."""
    with _open(db) as con:
        con.execute("ANALYZE")


def status(db: DbLike = None, migrations_dir: Optional[str] = None) -> Dict[str, Any]:
    """Migration status as a plain dict; never raises for ledger problems (they are listed under ``problems``)."""
    target = db_path(db) if not isinstance(db, sqlite3.Connection) else None
    if target is not None and target != ":memory:" and not os.path.exists(target):
        files = discover_migrations(migrations_dir)
        return {"path": target, "exists": False, "user_version": 0, "applied": [],
                "pending": [{"version": f.version, "name": f.filename} for f in files],
                "latest_available": files[-1].version if files else 0, "problems": [], "ok": False}
    with _open(db) as con:
        files = discover_migrations(migrations_dir)
        applied = applied_versions(con)
        problems = _ledger_problems(applied, files)
        bad = {v for k, _m, v in problems if k in ("checksum", "name", "missing")}
        done = {a["version"] for a in applied}
        pending = [f for f in files if f.version not in done]
        path = con.execute("PRAGMA database_list").fetchone()["file"] or ":memory:"
        return {
            "path": path,
            "exists": True,
            "user_version": con.execute("PRAGMA user_version").fetchone()[0],
            "applied": [dict(a, file_ok=a["version"] not in bad) for a in applied],
            "pending": [{"version": f.version, "name": f.filename} for f in pending],
            "latest_available": files[-1].version if files else 0,
            "problems": [m for _k, m, _v in problems],
            "ok": not problems and not pending,
        }


# --------------------------------------------------------------------------------------------------- doctor
def table_row_counts(con: sqlite3.Connection) -> Dict[str, int]:
    """Row count of every ordinary table (FTS virtual tables, their shadow tables and sqlite_* are skipped)."""
    out = {}
    for r in con.execute("PRAGMA table_list").fetchall():
        if r["schema"] == "main" and r["type"] == "table" and not r["name"].startswith("sqlite_"):
            out[r["name"]] = con.execute('SELECT COUNT(*) FROM "%s"' % r["name"]).fetchone()[0]
    return dict(sorted(out.items()))


def fk_orphan_counts(con: sqlite3.Connection, limit: int = 20) -> Dict[str, Any]:
    """Aggregate PRAGMA foreign_key_check: {'total': n, 'canonical': n, 'legacy': n, 'by_fk': [{child, parent, orphans}], 'examples': [...]}.

    ``legacy`` counts violations whose CHILD table is a verbatim legacy_* mirror: the source databases already contain orphans (radar:
    candidate_entity -> entity, 2 rows) that are loaded as they are and recorded in ops_dq_issue; they are warnings, not failures."""
    rows = con.execute("PRAGMA foreign_key_check").fetchall()
    agg = {}  # type: Dict[Tuple[str, str], int]
    for r in rows:
        k = (r[0], r[2])
        agg[k] = agg.get(k, 0) + 1
    legacy = sum(1 for r in rows if str(r[0]).startswith("legacy_"))
    return {
        "total": len(rows),
        "canonical": len(rows) - legacy,
        "legacy": legacy,
        "by_fk": [{"child": c, "parent": p, "orphans": n} for (c, p), n in sorted(agg.items(), key=lambda kv: -kv[1])],
        "examples": [{"child": r[0], "rowid": r[1], "parent": r[2], "fkid": r[3]} for r in rows[:limit]],
    }


def fts_integrity(con: sqlite3.Connection) -> Dict[str, str]:
    """{fts table: 'ok' or the error} using FTS5's own integrity-check against the content table."""
    out = {}
    for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND sql LIKE 'CREATE VIRTUAL TABLE%fts5%'").fetchall():
        name = r["name"]
        try:
            con.execute('INSERT INTO "%s"("%s") VALUES(\'integrity-check\')' % (name, name))
            out[name] = "ok"
        except sqlite3.Error as exc:
            out[name] = str(exc)
    return out


def _schema_objects(con: sqlite3.Connection) -> Dict[Tuple[str, str], str]:
    """{(type, name): sql} of every user schema object that has SQL (auto-indexes and sqlite_* internals excluded)."""
    return {(r[0], r[1]): r[2] for r in con.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\'")}


def schema_drift(con: sqlite3.Connection, migrations_dir: Optional[str] = None) -> List[str]:
    """Differences between the live schema and the schema produced by applying the SAME migration files (those recorded in the ledger)
    to an empty in-memory database.  Catches a hand-made ALTER / DROP / extra object, which the checksum ledger cannot see."""
    applied = {a["version"] for a in applied_versions(con)}
    if not applied:
        return []
    ref = connect(":memory:")
    try:
        ref.execute("PRAGMA foreign_keys = OFF")
        for m in discover_migrations(migrations_dir):
            if m.version in applied:
                for stmt in split_sql_statements(m.sql):
                    ref.execute(stmt)
        want, have = _schema_objects(ref), _schema_objects(con)
    finally:
        ref.close()
    out = []  # type: List[str]
    for k in sorted(set(want) | set(have)):
        if k not in have:
            out.append("%s %s: in the migrations, missing from the database" % k)
        elif k not in want:
            out.append("%s %s: in the database, not created by any migration" % k)
        elif want[k] != have[k]:
            out.append("%s %s: definition differs from the migrations" % k)
    return out


def _count(con: sqlite3.Connection, sql: str, *args: Any) -> int:
    return con.execute(sql, args).fetchone()[0]


def doctor(db: DbLike = None, quick: bool = False, migrations_dir: Optional[str] = None, with_counts: bool = True) -> Dict[str, Any]:
    """Health report.  ``ok`` is False if integrity / canonical foreign keys / FTS / migrations / schema drift are bad.  Everything else
    (unmapped stages, open data-quality errors, reference-data drift, legacy FK orphans, stale jobs, ...) is a ``warning``.
    ``quick=True`` uses quick_check and skips the expensive scans (schema drift, deal-state drift, near-duplicates).
    Never creates a database: a missing file gives ``exists: False, ok: False``."""
    if not isinstance(db, sqlite3.Connection):
        target = db_path(db)
        if target != ":memory:" and not os.path.exists(target):
            st = status(db, migrations_dir)
            return {"exists": False, "ok": False, "migrations": st, "warnings": ["database file does not exist: %s (run migrate)" % target]}
    with _open(db) as con:
        rep = {"exists": True}  # type: Dict[str, Any]
        pragma = "quick_check" if quick else "integrity_check"
        res = [r[0] for r in con.execute("PRAGMA %s" % pragma).fetchall()]
        rep["integrity_check"] = {"pragma": pragma, "ok": res == ["ok"], "messages": [] if res == ["ok"] else res[:50]}
        rep["foreign_key_check"] = fk_orphan_counts(con)
        rep["fts"] = fts_integrity(con)
        rep["migrations"] = status(con, migrations_dir)
        rep["pragmas"] = {
            "foreign_keys": con.execute("PRAGMA foreign_keys").fetchone()[0],
            "journal_mode": con.execute("PRAGMA journal_mode").fetchone()[0],
            "busy_timeout": con.execute("PRAGMA busy_timeout").fetchone()[0],
            "synchronous": con.execute("PRAGMA synchronous").fetchone()[0],
            "recursive_triggers": con.execute("PRAGMA recursive_triggers").fetchone()[0],
        }
        if with_counts:
            rep["row_counts"] = table_row_counts(con)
        warnings = []  # type: List[str]
        fk = rep["foreign_key_check"]
        if fk["legacy"]:
            warnings.append("%d foreign-key orphan(s) inside verbatim legacy_* tables (known: radar candidate_entity; recorded in ops_dq_issue "
                            "'legacy_fk_orphan'): %s" % (fk["legacy"], [b for b in fk["by_fk"] if b["child"].startswith("legacy_")]))
        names = {r[0]: r[1] for r in con.execute("SELECT name, type FROM sqlite_master WHERE type IN ('view','table')")}
        views = {n for n, t in names.items() if t == "view"}

        def view_count(view: str, label: str, key: str, flag_ok: bool = False) -> None:
            if view in views:
                n = _count(con, "SELECT COUNT(*) FROM %s" % view)
                rep[key] = n
                if n:
                    warnings.append("%d %s (see %s)" % (n, label, view))

        if "v_unmapped_stage" in views:
            n = _count(con, "SELECT COUNT(*) FROM v_unmapped_stage")
            rep["unmapped_stages"] = n
            if n:
                warnings.append("%d stage(s) have no stage_map row, directly or via stage_alias (see v_unmapped_stage)" % n)
        view_count("v_pipeline_without_stages", "reporting pipeline(s) with zero stage rows - every report on them is empty (run the HubSpot sync)", "pipelines_without_stages")
        if "v_dq_open" in views:
            n = _count(con, "SELECT COALESCE(SUM(issues),0) FROM v_dq_open WHERE severity = 'error'")
            rep["open_dq_errors"] = n
            if n:
                warnings.append("%d open data-quality error(s) (see v_dq_open)" % n)
        if "v_origin_coverage" in views:
            miss = {r["entity_type"]: r["rows_without_origin"] for r in con.execute("SELECT * FROM v_origin_coverage") if r["rows_without_origin"]}
            rep["rows_without_origin"] = miss
            if miss:
                warnings.append("canonical rows without origin_ref: %s" % miss)
        view_count("v_event_pipeline_guess", "event group(s) whose pipeline is a guess (deal_current/manual)", "event_pipeline_guesses")
        view_count("v_deal_company_drift", "deal(s) whose company_id disagrees with the primary deal_company link", "deal_company_drift")
        view_count("v_gsheet_excluded_rows", "spreadsheet(s) with stored rows although pull_enabled = 0", "gsheet_excluded_rows")
        view_count("v_phone_gate_review", "phone number(s) the +91 mobile gate rejected for lack of classification", "phone_gate_review")
        if not quick:
            view_count("v_deal_state_drift", "non-archived deal(s) whose stage/pipeline differs from their newest stage event", "deal_state_drift")
            view_count("v_dse_near_duplicate", "group(s) of stage events within the same second (CSV/API double import?)", "near_duplicate_events")
            rep["schema_drift"] = schema_drift(con, migrations_dir)
            if rep["schema_drift"]:
                warnings.append("%d schema difference(s) against the migrations (see schema_drift)" % len(rep["schema_drift"]))
        # jobs that started and never finished
        if "ops_import_run" in names:
            cutoff = "-%d hours" % STALE_RUNNING_HOURS
            stale = _count(con, "SELECT COUNT(*) FROM ops_import_run WHERE status = 'running' AND started_at < strftime('%Y-%m-%dT%H:%M:%fZ','now',?)", cutoff)
            stale += _count(con, "SELECT COUNT(*) FROM ops_sync_state WHERE last_status = 'running' AND last_attempt_at < strftime('%Y-%m-%dT%H:%M:%fZ','now',?)", cutoff)
            rep["stale_running_jobs"] = stale
            if stale:
                warnings.append("%d import run(s) / sync cursor(s) 'running' for more than %d h (crashed job?)" % (stale, STALE_RUNNING_HOURS))
        # planner statistics
        if "deal_stage_event" in names:
            has_stats = "sqlite_stat1" in {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            rep["has_planner_stats"] = has_stats
            if not has_stats and _count(con, "SELECT COUNT(*) FROM (SELECT 1 FROM deal_stage_event LIMIT 20000)") >= 20000:
                warnings.append("no sqlite_stat1 although deal_stage_event is large: run `python -m leadgen.db analyze`")
        # legacy rows nobody can trace or reverse
        unattributed = {}
        for n in sorted(names):
            if n.startswith("legacy_") and names[n] == "table":
                if con.execute('SELECT 1 FROM "%s" WHERE _import_run_id IS NULL LIMIT 1' % n).fetchone():
                    unattributed[n] = True
        rep["legacy_rows_without_import_run"] = sorted(unattributed)
        if unattributed:
            warnings.append("legacy table(s) holding rows without _import_run_id (cannot be reversed): %s" % sorted(unattributed))
        try:
            drift = reference_drift(con) if rep["migrations"]["applied"] else []
        except Exception as exc:  # config problem must not hide DB problems
            drift = ["reference data check failed: %s" % exc]
        rep["reference_drift"] = drift
        if drift:
            warnings.append("reference data differs from config/*.yaml: %d difference(s)" % len(drift))
        rep["warnings"] = warnings
        rep["ok"] = bool(rep["integrity_check"]["ok"] and fk["canonical"] == 0
                         and all(v == "ok" for v in rep["fts"].values()) and not rep["migrations"]["problems"]
                         and rep["pragmas"]["foreign_keys"] == 1 and not rep.get("schema_drift"))
        return rep


# ------------------------------------------------------------------------------------------ reference data
def _bool(v: Any) -> int:
    return 1 if v else 0


def _ref_rows(config_dir: Optional[str] = None) -> Dict[str, List[tuple]]:
    """The reference tables as the YAML defines them (same column order as the SELECTs in reference_drift)."""
    from leadgen import config

    accounts = config.get_accounts(config_dir)
    can = config.load_canonical_stages(config_dir)
    acc_rows, pipe_rows = [], []
    for a in accounts.values():
        acc_rows.append((a.slug, a.portal_id, a.name, a.env_key, a.timezone, a.ui_domain, a.currency))
        for p in a.all_pipelines:
            pipe_rows.append((a.slug, p.pipeline_id, p.label, p.funnel_slug, p.display_order, _bool(p.is_reporting_funnel),
                              p.cohort_start, p.cohort_exclude_migration_on, json.dumps(list(p.previous_labels))))
    stage_rows = []
    for i, s in enumerate(can["stages"], 1):
        f = s["flags"]
        stage_rows.append((s["code"], s["rank"], float(s["depth"]), i, s["label"], s["definition"], _bool(s["is_live"]), _bool(s["is_dead"]),
                           _bool(s["is_won"]), _bool(s["is_derived"]), _bool(s["is_entry"]), _bool(f["attempt"]), _bool(f["connected"]), _bool(f["interested"])))
    reason_rows = []
    for i, d in enumerate(can["dead_reasons"], 1):
        reason_rows.append((d["code"], i, d["label"], d["definition"], _bool(d["flags"]["attempt"]), _bool(d["flags"]["connected"]), _bool(d["counts_in_effort_kpis"])))
    return {"account": acc_rows, "pipeline": pipe_rows, "canonical_stage": stage_rows, "canonical_dead_reason": reason_rows}


_REF_SELECTS = {
    "account": "SELECT account_id, portal_id, name, env_key, hubspot_timezone, ui_domain, currency FROM account ORDER BY account_id",
    "pipeline": "SELECT account_id, pipeline_id, label, funnel_slug, display_order, is_reporting_funnel, cohort_start, cohort_exclude_migration_on, "
                "previous_labels_json FROM pipeline ORDER BY account_id, pipeline_id",
    "canonical_stage": "SELECT canonical_code, rank_label, depth, sort_order, label, definition, is_live, is_dead, is_won, is_derived, is_entry, "
                       "flag_attempt, flag_connected, flag_interested FROM canonical_stage ORDER BY sort_order",
    "canonical_dead_reason": "SELECT dead_reason_code, sort_order, label, definition, flag_attempt, flag_connected, counts_in_effort_kpis "
                             "FROM canonical_dead_reason ORDER BY sort_order",
}


def reference_drift(con: sqlite3.Connection, config_dir: Optional[str] = None) -> List[str]:
    """Differences between config/accounts.yaml + config/canonical_stages.yaml and the account / pipeline /
    canonical_stage / canonical_dead_reason tables.  Empty list = in sync (rows only in the DB are reported too, except
    pipelines the sync discovered, which are legitimately DB-only)."""
    out = []  # type: List[str]
    want = _ref_rows(config_dir)
    for table, sql in _REF_SELECTS.items():
        have = [tuple(r) for r in con.execute(sql).fetchall()]
        have_by_key = {(r[0], r[1]) if table == "pipeline" else r[0]: r for r in have}
        for row in want[table]:
            key = (row[0], row[1]) if table == "pipeline" else row[0]
            got = have_by_key.get(key)
            if got is None:
                out.append("%s %s: in YAML, missing from database" % (table, key))
            elif tuple(got) != tuple(row):
                diffs = [(i, a, b) for i, (a, b) in enumerate(zip(row, got)) if a != b]
                out.append("%s %s: differs (column index, yaml, db) %s" % (table, key, diffs))
        if table != "pipeline":
            want_keys = {r[0] for r in want[table]}
            for key in have_by_key:
                if key not in want_keys:
                    out.append("%s %s: in database, missing from YAML" % (table, key))
    return out


def seed_reference_data(db: DbLike = None, config_dir: Optional[str] = None) -> Dict[str, int]:
    """Upsert account / pipeline / canonical_stage / canonical_dead_reason from config/*.yaml (idempotent).  The same rows are
    seeded by the migrations; this call is how an edited YAML reaches an existing database.  Stage/pipeline labels that the
    HubSpot sync refreshed are NOT overwritten for pipelines (only config-owned columns are)."""
    want = _ref_rows(config_dir)
    counts = {}
    with _open(db) as con:
        with transaction(con):
            for r in want["account"]:
                con.execute(
                    "INSERT INTO account (account_id, portal_id, name, env_key, hubspot_timezone, ui_domain, currency) VALUES (?,?,?,?,?,?,?) "
                    "ON CONFLICT(account_id) DO UPDATE SET portal_id=excluded.portal_id, name=excluded.name, env_key=excluded.env_key, "
                    "hubspot_timezone=excluded.hubspot_timezone, ui_domain=excluded.ui_domain, currency=excluded.currency", r)
            for r in want["pipeline"]:
                con.execute(
                    "INSERT INTO pipeline (account_id, pipeline_id, label, funnel_slug, display_order, is_reporting_funnel, cohort_start, "
                    "cohort_exclude_migration_on, previous_labels_json) VALUES (?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(account_id, pipeline_id) DO UPDATE SET label=excluded.label, funnel_slug=excluded.funnel_slug, "
                    "display_order=excluded.display_order, is_reporting_funnel=excluded.is_reporting_funnel, cohort_start=excluded.cohort_start, "
                    "cohort_exclude_migration_on=excluded.cohort_exclude_migration_on, previous_labels_json=excluded.previous_labels_json", r)
            for r in want["canonical_stage"]:
                con.execute(
                    "INSERT INTO canonical_stage (canonical_code, rank_label, depth, sort_order, label, definition, is_live, is_dead, is_won, "
                    "is_derived, is_entry, flag_attempt, flag_connected, flag_interested) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(canonical_code) DO UPDATE SET rank_label=excluded.rank_label, depth=excluded.depth, sort_order=excluded.sort_order, "
                    "label=excluded.label, definition=excluded.definition, is_live=excluded.is_live, is_dead=excluded.is_dead, is_won=excluded.is_won, "
                    "is_derived=excluded.is_derived, is_entry=excluded.is_entry, flag_attempt=excluded.flag_attempt, "
                    "flag_connected=excluded.flag_connected, flag_interested=excluded.flag_interested", r)
            for r in want["canonical_dead_reason"]:
                con.execute(
                    "INSERT INTO canonical_dead_reason (dead_reason_code, sort_order, label, definition, flag_attempt, flag_connected, "
                    "counts_in_effort_kpis) VALUES (?,?,?,?,?,?,?) ON CONFLICT(dead_reason_code) DO UPDATE SET sort_order=excluded.sort_order, "
                    "label=excluded.label, definition=excluded.definition, flag_attempt=excluded.flag_attempt, flag_connected=excluded.flag_connected, "
                    "counts_in_effort_kpis=excluded.counts_in_effort_kpis", r)
        for k, v in want.items():
            counts[k] = len(v)
    return counts


# ------------------------------------------------------------------------------------------------------ CLI
def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="python -m leadgen.db", description="LeadGenMonolith database helper")
    ap.add_argument("command", choices=["migrate", "status", "doctor", "seed", "analyze"])
    ap.add_argument("--db", help="database path (default: $LEADGEN_DB or db/leadgen.sqlite)")
    ap.add_argument("--dry-run", action="store_true", help="migrate: list pending migrations without applying")
    ap.add_argument("--no-backup", action="store_true", help="migrate: skip the VACUUM INTO backup of a non-empty database")
    ap.add_argument("--quick", action="store_true", help="doctor: quick_check instead of integrity_check")
    args = ap.parse_args(argv)
    try:
        if args.command == "migrate":
            out = migrate(args.db, dry_run=args.dry_run, backup=not args.no_backup)
            print(json.dumps({"dry_run": args.dry_run, "applied" if not args.dry_run else "would_apply": list(out),
                              "backup": getattr(out, "backup", None)}, indent=2))
        elif args.command == "analyze":
            analyze(args.db)
            print(json.dumps({"analyzed": True}))
        elif args.command == "status":
            print(json.dumps(status(args.db), indent=2, default=str))
        elif args.command == "seed":
            print(json.dumps(seed_reference_data(args.db), indent=2))
        else:
            rep = doctor(args.db, quick=args.quick)
            print(json.dumps(rep, indent=2, default=str))
            return 0 if rep["ok"] else 1
    except (MigrationError, FileNotFoundError) as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
