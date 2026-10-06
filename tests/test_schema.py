"""Schema-layer tests: migrate a throwaway database from scratch and exercise the constraints, triggers, views,
FTS indexes and the migration runner.

Every test runs on its own temp database (never db/leadgen.sqlite).  To keep all throwaway files inside the repo's scratch
area run:   .venv/bin/python -m pytest tests --basetemp=db/_scratch/pytest
"""
import importlib.util
import json
import os
import shutil
import sqlite3
import time

import pytest

from leadgen import config, db

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL_DB = os.path.join(ROOT, "db", "leadgen.sqlite")
LEGACY_PREFIXES = ("legacy_",)


# ------------------------------------------------------------------------------------------------ fixtures
@pytest.fixture
def path(tmp_path):
    p = os.path.abspath(str(tmp_path / "t.sqlite"))
    assert p != REAL_DB, "tests must never touch the real database"
    return p


@pytest.fixture
def con(path):
    db.migrate(path)
    c = db.connect(path)
    yield c
    c.close()


def x(con, sql, *args):
    """execute and return the cursor"""
    return con.execute(sql, args)


def val(con, sql, *args):
    r = con.execute(sql, args).fetchone()
    return None if r is None else r[0]


def rows(con, sql, *args):
    return [tuple(r) for r in con.execute(sql, args).fetchall()]


def bad(con, sql, *args):
    """the statement must be rejected by a constraint / trigger"""
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(sql, args)


# canonical sample world --------------------------------------------------------------------------------------------
def make_world(con):
    """main/default (Coding) with 6 stages + mappings, companyops cluster2 with 2 stages, 2 owners per account."""
    x(con, "INSERT INTO owner (account_id, hs_owner_id, hs_user_id, email_norm, display_name) VALUES ('main','111','911','alice@lh2.ai','Alice')")
    x(con, "INSERT INTO owner (account_id, hs_owner_id, hs_user_id, email_norm, display_name) VALUES ('main','112','912','bob@lh2.ai','Bob')")
    x(con, "INSERT INTO owner (account_id, hs_owner_id, hs_user_id, email_norm, display_name) VALUES ('companyops','111','911','alice@lh2.ai','Alice')")
    stages = [("c1", "Cold Call", 1, "SOURCED", None, None),
              ("np", "No Pickup", 2, "NO_ANSWER", None, None),
              ("i1", "Interested", 3, "INTERESTED", None, None),
              ("i2", "Interested follow up", 4, "INTERESTED", None, None),
              ("w", "Closed/Won", 5, "WON", None, None),
              ("d1", "Dead/ColdCall/Not Interested", 6, "DEAD", "NOT_INTERESTED", 3.0)]
    for sid, label, order, canon, reason, depth in stages:
        x(con, "INSERT INTO stage (account_id, pipeline_id, stage_id, label, display_order, is_closed) VALUES ('main','default',?,?,?,?)",
          sid, label, order, 1 if canon in ("WON", "DEAD") else 0)
        x(con, "INSERT INTO stage_map (account_id, pipeline_id, stage_id, canonical_code, dead_reason_code, depth_reached) VALUES ('main','default',?,?,?,?)",
          sid, canon, reason, depth)
    # same stage ids in the other MAIN pipeline (HubSpot reuses ids across pipelines)
    for sid, label in (("i1", "Interested"), ("w", "Closed/Won")):
        x(con, "INSERT INTO stage (account_id, pipeline_id, stage_id, label) VALUES ('main','2425754306',?,?)", sid, label)
    for sid, label in (("k1", "Cold called assigned"), ("k2", "Replied")):
        x(con, "INSERT INTO stage (account_id, pipeline_id, stage_id, label) VALUES ('companyops','2464812771',?,?)", sid, label)
    x(con, "INSERT INTO stage_map (account_id, pipeline_id, stage_id, canonical_code) VALUES ('companyops','2464812771','k1','SOURCED')")
    # k2 deliberately left UNMAPPED


def add_deal(con, account, hs_id, pipeline, stage, owner_hs=None, created="2026-10-01T05:00:00.000Z", archived=0, name=None):
    owner_id = None
    if owner_hs:
        owner_id = val(con, "SELECT owner_id FROM owner WHERE account_id=? AND hs_owner_id=?", account, owner_hs)
    x(con, "INSERT INTO deal (account_id, hs_deal_id, pipeline_id, stage_id, owner_id, dealname, hs_created_at, is_archived) VALUES (?,?,?,?,?,?,?,?)",
      account, hs_id, pipeline, stage, owner_id, name or ("deal " + hs_id), created, archived)
    return val(con, "SELECT deal_id FROM deal WHERE account_id=? AND hs_deal_id=?", account, hs_id)


def add_event(con, deal_id, account, pipeline, to_stage, at, source="CRM_UI", actor_user=None, actor_owner_hs=None, from_stage=None,
              basis="unique_stage_id", origin="hubspot_history"):
    actor_owner = None
    if actor_owner_hs:
        actor_owner = val(con, "SELECT owner_id FROM owner WHERE account_id=? AND hs_owner_id=?", account, actor_owner_hs)
    x(con, "INSERT INTO deal_stage_event (deal_id, account_id, pipeline_id, to_stage_id, from_pipeline_id, from_stage_id, entered_at, source_type, "
           "actor_user_id, actor_owner_id, pipeline_basis, event_origin) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
      deal_id, account, pipeline, to_stage, pipeline if from_stage else None, from_stage, at, source, actor_user, actor_owner, basis, origin)


# ------------------------------------------------------------------------------------------------ migration runner
def test_migrate_from_scratch_is_complete_and_idempotent(path):
    files = db.discover_migrations()
    assert [f.version for f in files] == list(range(1, len(files) + 1))
    assert len(files) >= 14
    applied = db.migrate(path)
    assert applied == [f.filename for f in files]
    assert db.migrate(path) == []                                   # second run: nothing pending
    st = db.status(path)
    assert st["ok"] and st["user_version"] == files[-1].version and not st["pending"] and not st["problems"]
    with db.connect(path) as c:
        got = {r["version"]: r["checksum_sha256"] for r in c.execute("SELECT * FROM schema_migration")}
    assert got == {f.version: f.checksum for f in files}           # sha256 of the file bytes is stored
    assert db.status(os.path.join(os.path.dirname(path), "nope.sqlite"))["exists"] is False
    assert not os.path.exists(os.path.join(os.path.dirname(path), "nope.sqlite"))   # status() never creates a database


def test_connect_sets_all_pragmas(con):
    assert val(con, "PRAGMA foreign_keys") == 1
    assert val(con, "PRAGMA journal_mode") == "wal"
    assert val(con, "PRAGMA busy_timeout") == 30000
    assert val(con, "PRAGMA synchronous") == 1                       # NORMAL
    assert con.row_factory is sqlite3.Row


def test_db_path_resolution(monkeypatch):
    monkeypatch.delenv(db.ENV_DB, raising=False)
    assert db.db_path() == os.path.join(ROOT, "db", "leadgen.sqlite")
    monkeypatch.setenv(db.ENV_DB, "db/_scratch/x.sqlite")
    assert db.db_path() == os.path.join(ROOT, "db", "_scratch", "x.sqlite")
    assert db.db_path("/tmp/abs.sqlite") == "/tmp/abs.sqlite"


def test_doctor_clean_on_fresh_database(con):
    rep = db.doctor(con)
    assert rep["ok"], rep
    assert rep["integrity_check"]["ok"] and rep["foreign_key_check"]["total"] == 0
    assert set(rep["fts"]) == {"ext_gsheet_row_fts", "ext_gmail_message_fts", "ext_gmail_attachment_fts"}
    assert all(v == "ok" for v in rep["fts"].values())
    assert rep["reference_drift"] == [] and rep["unmapped_stages"] == 0
    assert rep["row_counts"]["account"] == 3 and rep["row_counts"]["canonical_stage"] == 17
    assert "legacy_tam_companies" in rep["row_counts"] and not any(n.endswith("_fts_data") for n in rep["row_counts"])
    assert val(con, "PRAGMA integrity_check") == "ok"
    assert rows(con, "PRAGMA foreign_key_check") == []


def test_doctor_reports_fk_orphans(con):
    con.execute("PRAGMA foreign_keys = OFF")
    x(con, "INSERT INTO stage_alias (account_id, pipeline_id, alias_kind, alias_value, stage_id) VALUES ('main','default','label','ghost','nope')")
    con.execute("PRAGMA foreign_keys = ON")
    rep = db.doctor(con)
    assert not rep["ok"] and rep["foreign_key_check"]["total"] == 1
    assert rep["foreign_key_check"]["by_fk"] == [{"child": "stage_alias", "parent": "stage", "orphans": 1}]


def _copy_migrations(tmp_path, upto=None):
    d = tmp_path / "mig"
    d.mkdir()
    for f in db.discover_migrations():
        if upto is None or f.version <= upto:
            shutil.copy(f.path, d / f.filename)
    return str(d)


def test_checksum_tamper_is_refused(path, tmp_path):
    mig = _copy_migrations(tmp_path)
    db.migrate(path, migrations_dir=mig)
    target = os.path.join(mig, "0002_canonical.sql")
    with open(target, "a") as fh:
        fh.write("\n-- innocent looking edit\n")
    with db.connect(path) as c:
        before = rows(c, "SELECT version, checksum_sha256 FROM schema_migration")
    with pytest.raises(db.ChecksumMismatchError) as ei:
        db.migrate(path, migrations_dir=mig)
    assert "0002_canonical.sql" in str(ei.value) and "never edit an applied migration" in str(ei.value)
    st = db.status(path, migrations_dir=mig)
    assert not st["ok"] and st["problems"] and [a["file_ok"] for a in st["applied"]].count(False) == 1
    assert not db.doctor(path, migrations_dir=mig)["ok"]
    with db.connect(path) as c:
        assert rows(c, "SELECT version, checksum_sha256 FROM schema_migration") == before    # ledger untouched


def test_tamper_blocks_even_when_new_migrations_are_pending(path, tmp_path):
    mig = _copy_migrations(tmp_path, upto=3)
    db.migrate(path, migrations_dir=mig)
    with open(os.path.join(mig, "0001_foundation.sql"), "a") as fh:
        fh.write("\n-- tamper\n")
    with open(os.path.join(mig, "0004_extra.sql"), "w") as fh:
        fh.write("CREATE TABLE should_not_exist (a INTEGER);\n")
    with pytest.raises(db.ChecksumMismatchError):
        db.migrate(path, migrations_dir=mig)
    with db.connect(path) as c:
        assert val(c, "SELECT COUNT(*) FROM sqlite_master WHERE name='should_not_exist'") == 0


def test_missing_applied_file_and_gaps_are_refused(path, tmp_path):
    mig = _copy_migrations(tmp_path, upto=3)
    db.migrate(path, migrations_dir=mig)
    os.remove(os.path.join(mig, "0003_hsraw.sql"))
    with pytest.raises(db.MigrationError):
        db.migrate(path, migrations_dir=mig)
    mig2 = _copy_migrations(tmp_path / "x" if (tmp_path / "x").mkdir() is None else tmp_path, upto=2)   # fresh dir
    with open(os.path.join(mig2, "0005_gap.sql"), "w") as fh:
        fh.write("SELECT 1;")
    with pytest.raises(db.MigrationOrderError):
        db.discover_migrations(mig2)


def test_bad_filename_is_refused(tmp_path):
    d = tmp_path / "m"
    d.mkdir()
    (d / "0001_ok.sql").write_text("SELECT 1;")
    (d / "2_bad.sql").write_text("SELECT 1;")
    with pytest.raises(db.MigrationError):
        db.discover_migrations(str(d))


def test_failed_migration_rolls_back_atomically(path, tmp_path):
    d = tmp_path / "m"
    d.mkdir()
    shutil.copy(db.discover_migrations()[0].path, d / "0001_foundation.sql")
    (d / "0002_broken.sql").write_text("CREATE TABLE half_done (a INTEGER);\nINSERT INTO half_done VALUES (1);\nINSERT INTO no_such_table VALUES (1);\n")
    with pytest.raises(db.MigrationError) as ei:
        db.migrate(path, migrations_dir=str(d))
    assert "0002_broken.sql" in str(ei.value) and "rolled back" in str(ei.value)
    with db.connect(path) as c:
        assert val(c, "SELECT COUNT(*) FROM sqlite_master WHERE name='half_done'") == 0
        assert rows(c, "SELECT version FROM schema_migration") == [(1,)]
        assert val(c, "PRAGMA user_version") == 1
    (d / "0002_broken.sql").write_text("CREATE TABLE fixed (a INTEGER);\n")      # fixing the not-yet-applied file is fine
    assert db.migrate(path, migrations_dir=str(d)) == ["0002_broken.sql"]


def test_migrate_target_and_dry_run(path):
    assert db.migrate(path, dry_run=True)[0] == "0001_foundation.sql"
    assert not os.path.exists(path) or val(db.connect(path), "SELECT COUNT(*) FROM sqlite_master") == 0
    assert db.migrate(path, target=2) == ["0001_foundation.sql", "0002_canonical.sql"]
    assert db.status(path)["user_version"] == 2
    assert len(db.migrate(path)) == len(db.discover_migrations()) - 2


def test_split_sql_statements_handles_triggers_and_comments():
    sql = """-- header; with semicolon
CREATE TABLE a (x INTEGER); -- trailing ; comment
CREATE TRIGGER t AFTER INSERT ON a BEGIN
  SELECT CASE WHEN NEW.x > 1 THEN 1 ELSE 0 END;
  UPDATE a SET x = x WHERE 0;
END;
/* just a comment; */
INSERT INTO a VALUES ('semi;colon');
"""
    parts = db.split_sql_statements(sql)
    assert len(parts) == 3 and parts[1].startswith("CREATE TRIGGER") and parts[1].rstrip().endswith("END;")
    with pytest.raises(db.MigrationError):
        db.split_sql_statements("SELECT 1; SELECT 2")


# ------------------------------------------------------------------------------------------------ structure
def test_canonical_layer_is_strict(con):
    tl = con.execute("PRAGMA table_list").fetchall()
    non_strict = sorted(r["name"] for r in tl if r["schema"] == "main" and r["type"] == "table" and not r["strict"]
                        and not r["name"].startswith("sqlite_"))
    assert non_strict and all(n.startswith(LEGACY_PREFIXES) for n in non_strict), [n for n in non_strict if not n.startswith(LEGACY_PREFIXES)]
    strict = {r["name"] for r in tl if r["strict"]}
    for t in ("account", "owner", "pipeline", "stage", "stage_alias", "canonical_stage", "stage_map", "company", "company_identifier",
              "company_account_link", "contact", "contact_phone", "deal", "deal_stage_event", "deal_contact", "deal_company", "engagement",
              "lead_source", "vertical", "tam_company_verdict", "cost_ledger", "suppression", "merge_candidate", "origin_ref",
              "hsraw_object", "rpt_funnel_daily", "rpt_report_run", "rpt_legacy_snapshot", "ext_gsheet_row", "ext_gmail_message",
              "ops_import_run", "ops_sync_state", "ops_dq_issue", "ops_audit_log", "schema_migration"):
        assert t in strict, t


def test_all_legacy_tables_have_import_run_id_and_keep_pks(con):
    legacy = [r["name"] for r in con.execute("PRAGMA table_list") if r["name"].startswith("legacy_") and r["type"] == "table"]
    assert len(legacy) >= 40
    for t in legacy:
        cols = rows(con, 'PRAGMA table_info("%s")' % t)
        assert cols[-1][1] == "_import_run_id" and cols[-1][2] == "INTEGER", t
    # a few original primary keys survived
    assert [c[1] for c in rows(con, 'PRAGMA table_info("legacy_tam_company_categories")') if c[5]] == ["company_id", "category_id"]
    assert [c[1] for c in rows(con, 'PRAGMA table_info("legacy_resolver_companies")') if c[5]] == ["domain"]
    assert "AUTOINCREMENT" in val(con, "SELECT sql FROM sqlite_master WHERE name='legacy_pipeline_people'").upper()
    # FKs were retargeted to prefixed parents, including the ops_import_run hook
    fks = {(r[2], r[3]) for r in rows(con, 'PRAGMA foreign_key_list("legacy_tam_company_categories")')}
    assert ("legacy_tam_companies", "company_id") in fks and ("ops_import_run", "_import_run_id") in fks
    # indexes and views exist with prefixes
    assert val(con, "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name='legacy_tam_ux_crm_deal'") == 1
    assert rows(con, "SELECT * FROM legacy_corpus_v_corpus_summary") == []
    x(con, "INSERT INTO ops_import_run (kind, source_name) VALUES ('legacy_import','tam')")
    x(con, "INSERT INTO legacy_tam_categories (id, key, name, _import_run_id) VALUES (1,'adtech','Adtech',1)")
    bad(con, "INSERT INTO legacy_tam_categories (id, key, name) VALUES (2,'adtech','Dup')")          # original UNIQUE kept
    bad(con, "INSERT INTO legacy_tam_categories (id, key, name, _import_run_id) VALUES (3,'x','X',999)")  # FK to ops_import_run


def _load_gen():
    spec = importlib.util.spec_from_file_location("gen_legacy_ddl", os.path.join(ROOT, "tools", "gen_legacy_ddl.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_generated_legacy_migrations_are_reproducible():
    gen = _load_gen()
    checked = 0
    for src in gen.SOURCES:
        if not os.path.exists(os.path.join(ROOT, src[3])):
            continue                                                 # legacy copy not present on this machine
        text = gen.render_migration(src)
        assert open(gen.migration_path(src)).read() == text, "run tools/gen_legacy_ddl.py --write"
        assert gen.verify(src, text) == []
        checked += 1
    if checked == 0:
        pytest.skip("legacy databases not available")


def test_rewriter_handles_views_aliases_and_constraints():
    gen = _load_gen()
    tables = {"a", "b"}
    out = gen.rewrite_ddl("CREATE TABLE a (id INTEGER PRIMARY KEY, b_id INT REFERENCES b(id), -- note\n  UNIQUE(b_id))", "table", "p_", tables)
    assert out.startswith("CREATE TABLE p_a") and "REFERENCES p_b(id)" in out
    assert out.index("_import_run_id") < out.index("UNIQUE(b_id)")           # column goes before table constraints
    out = gen.rewrite_ddl("CREATE TABLE b (id INTEGER PRIMARY KEY, v TEXT -- trailing\n)", "table", "p_", tables)
    assert out.rstrip(";").endswith("\n)") and "TEXT," in out
    v = gen.rewrite_ddl("CREATE VIEW v AS SELECT a.id, b.v FROM a JOIN b ON b.id = a.b_id LEFT JOIN a x ON x.id = 1", "view", "p_", tables)
    assert "FROM p_a AS a JOIN p_b AS b ON" in v and "LEFT JOIN p_a x" in v
    assert gen.rewrite_ddl("CREATE UNIQUE INDEX ix ON a(id) WHERE id > 1", "index", "p_", tables) == "CREATE UNIQUE INDEX p_ix ON p_a(id) WHERE id > 1;"


# ------------------------------------------------------------------------------------------------ seeds
def test_seeded_reference_data_matches_yaml(con):
    assert db.reference_drift(con) == []
    assert rows(con, "SELECT account_id, portal_id FROM account ORDER BY account_id") == [("companyops", 246897735), ("main", 246754894), ("rat", 247485022)]
    assert val(con, "SELECT COUNT(*) FROM pipeline WHERE is_reporting_funnel = 1") == 5
    assert val(con, "SELECT is_reporting_funnel FROM pipeline WHERE account_id='rat' AND pipeline_id='default'") == 0
    assert rows(con, "SELECT funnel_slug FROM pipeline WHERE is_reporting_funnel=1 ORDER BY account_id, display_order") == \
        [("cluster1",), ("cluster2",), ("coding",), ("coops_global",), ("rat",)]
    assert rows(con, "SELECT cohort_start, cohort_exclude_migration_on FROM pipeline WHERE funnel_slug='cluster2'") == [("2026-09-15", "2026-09-15")]
    assert [r[0] for r in rows(con, "SELECT canonical_code FROM canonical_stage ORDER BY sort_order")][:3] == ["SOURCED", "LI_SENT", "NO_ANSWER"]
    assert val(con, "SELECT COUNT(*) FROM canonical_dead_reason") == 10


def test_seed_reference_data_repairs_drift_and_is_idempotent(con):
    x(con, "UPDATE canonical_stage SET label = 'tampered' WHERE canonical_code = 'WON'")
    x(con, "UPDATE pipeline SET cohort_start = NULL WHERE funnel_slug = 'cluster2'")
    drift = db.reference_drift(con)
    assert len(drift) == 2 and any("canonical_stage" in d for d in drift) and any("pipeline" in d for d in drift)
    assert db.seed_reference_data(con)["pipeline"] == 6
    assert db.reference_drift(con) == []
    db.seed_reference_data(con)
    assert db.reference_drift(con) == []


def test_canonical_stage_yaml_is_consistent():
    can = config.load_canonical_stages()
    depths = [s["depth"] for s in can["stages"]]
    assert depths == sorted(depths) and can["stages"][-1]["code"] == "DEAD"
    assert [s["code"] for s in can["stages"] if s["is_derived"]] == ["MEETING_HELD", "EVALUATED"]
    assert [s["code"] for s in can["stages"] if s["is_won"]] == ["WON"]
    for s in can["stages"]:
        f = s["flags"]
        assert f["interested"] <= f["connected"] <= f["attempt"], s["code"]
        assert s["definition"].strip()


# ------------------------------------------------------------------------------------------------ constraints
def test_account_constraints(con):
    bad(con, "INSERT INTO account (account_id, portal_id, name, env_key, hubspot_timezone) VALUES ('Bad Slug', 1, 'n', 'ENV_A', 'UTC')")
    bad(con, "INSERT INTO account (account_id, portal_id, name, env_key, hubspot_timezone) VALUES ('ok', 246754894, 'dup portal', 'ENV_B', 'UTC')")
    bad(con, "INSERT INTO account (account_id, portal_id, name, env_key, hubspot_timezone) VALUES ('ok2', 5, 'n', 'pat-na2-looks-like-a-token', 'UTC')")
    x(con, "INSERT INTO account (account_id, portal_id, name, env_key, hubspot_timezone) VALUES ('ok3', 5, 'n', 'HUBSPOT_KEY_OK3', 'UTC')")


def test_keys_are_scoped_by_account_and_pipeline(con):
    make_world(con)
    # same stage id in two pipelines of one portal is legal; twice in the same pipeline is not
    assert val(con, "SELECT COUNT(*) FROM stage WHERE stage_id = 'i1'") == 2
    bad(con, "INSERT INTO stage (account_id, pipeline_id, stage_id, label) VALUES ('main','default','i1','dup')")
    # a stage needs an existing (account, pipeline)
    bad(con, "INSERT INTO stage (account_id, pipeline_id, stage_id, label) VALUES ('main','nope','z','x')")
    bad(con, "INSERT INTO stage (account_id, pipeline_id, stage_id, label) VALUES ('rat','2425754306','z','wrong portal')")
    # same HubSpot deal id in two portals is two deals; duplicate within a portal is rejected
    d1 = add_deal(con, "main", "5001", "default", "c1")
    d2 = add_deal(con, "companyops", "5001", "2464812771", "k1")
    assert d1 != d2
    bad(con, "INSERT INTO deal (account_id, hs_deal_id, pipeline_id, stage_id) VALUES ('main','5001','default','c1')")
    # a deal cannot sit in a stage of another pipeline / portal
    bad(con, "INSERT INTO deal (account_id, hs_deal_id, pipeline_id, stage_id) VALUES ('main','5002','default','k1')")
    bad(con, "INSERT INTO deal (account_id, hs_deal_id, pipeline_id, stage_id) VALUES ('companyops','5003','default','c1')")
    # nor be owned by another portal's owner (composite FK)
    other_owner = val(con, "SELECT owner_id FROM owner WHERE account_id='companyops'")
    bad(con, "INSERT INTO deal (account_id, hs_deal_id, pipeline_id, stage_id, owner_id) VALUES ('main','5004','default','c1',?)", other_owner)
    # hs ids are digits only
    bad(con, "INSERT INTO deal (account_id, hs_deal_id, pipeline_id, stage_id) VALUES ('main','12a','default','c1')")


def test_owner_user_id_and_owner_id_are_separate_namespaces(con):
    make_world(con)
    bad(con, "INSERT INTO owner (account_id, hs_owner_id, hs_user_id, display_name) VALUES ('main','999','911','dup user id')")
    bad(con, "INSERT INTO owner (account_id, hs_owner_id, display_name) VALUES ('main','111','dup owner id')")
    x(con, "INSERT INTO owner (account_id, hs_owner_id, hs_user_id, display_name) VALUES ('rat','111','911','same ids, other portal')")
    x(con, "INSERT INTO owner (account_id, hs_owner_id, display_name) VALUES ('rat','112','no user id yet')")
    x(con, "INSERT INTO owner (account_id, hs_owner_id, display_name) VALUES ('rat','113','also no user id')")      # NULL userIds do not collide
    bad(con, "INSERT INTO owner (account_id, hs_owner_id, email_norm, display_name) VALUES ('rat','114','Mixed@Case.com','x')")


def test_stage_alias_tombstones_and_history_only_stages(con):
    make_world(con)
    # deleted stage id kept as a tombstone because history still references it
    x(con, "INSERT INTO stage (account_id, pipeline_id, stage_id, label, is_deleted, label_source) VALUES ('companyops','2464812771','4080987861','LinkedIn sent',1,'alias_seed')")
    x(con, "INSERT INTO stage_alias (account_id, pipeline_id, alias_kind, alias_value, stage_id, reason) VALUES "
           "('companyops','2464812771','stage_id','4080987861','k1','deleted in v5 restructure')")
    x(con, "INSERT INTO stage_alias (account_id, pipeline_id, alias_kind, alias_value, stage_id) VALUES ('main','default','label','call attempted','np')")
    bad(con, "INSERT INTO stage_alias (account_id, pipeline_id, alias_kind, alias_value, stage_id) VALUES ('main','default','label','call attempted','i1')")  # unique
    bad(con, "INSERT INTO stage_alias (account_id, pipeline_id, alias_kind, alias_value, stage_id) VALUES ('main','default','label','Mixed Case','np')")       # labels normalised
    bad(con, "INSERT INTO stage_alias (account_id, pipeline_id, alias_kind, alias_value, stage_id) VALUES ('main','default','label','x','ghost')")               # target must exist
    # an event may enter a tombstoned stage but not a stage that was never recorded
    d = add_deal(con, "companyops", "7001", "2464812771", "k1")
    add_event(con, d, "companyops", "2464812771", "4080987861", "2026-09-20T05:00:00.000Z", "INTEGRATION")
    with pytest.raises(sqlite3.IntegrityError):
        add_event(con, d, "companyops", "2464812771", "9999999", "2026-09-20T06:00:00.000Z")
    # the tombstone has no stage_map row of its own but inherits its successor's through the alias: it is NOT unmapped, and the
    # canonical roll-up shows the event on the successor's rung (SOURCED), never as UNMAPPED
    assert ("4080987861",) not in rows(con, "SELECT stage_id FROM v_unmapped_stage WHERE account_id='companyops'")
    assert rows(con, "SELECT canonical_code, resolved_via_alias_stage_id, native_group_stage_id FROM v_stage_effective WHERE stage_id='4080987861'") == [("SOURCED", "k1", "k1")]
    assert rows(con, "SELECT canonical_code, entries_total FROM v_funnel_entries_daily_canonical WHERE pipeline_id='2464812771'") == [("SOURCED", 1)]
    assert rows(con, "SELECT stage_id, entries_total FROM v_funnel_entries_daily WHERE pipeline_id='2464812771'") == [("k1", 1)]   # native rows merge into the successor
    # without the alias the same tombstone IS a loud failure
    x(con, "DELETE FROM stage_alias WHERE alias_value = '4080987861'")
    assert ("4080987861",) in rows(con, "SELECT stage_id FROM v_unmapped_stage WHERE account_id='companyops'")
    assert rows(con, "SELECT canonical_code FROM v_funnel_entries_daily_canonical WHERE pipeline_id='2464812771'") == [("UNMAPPED",)]


def test_stage_map_integrity_triggers(con):
    make_world(con)
    sm = "INSERT INTO stage_map (account_id, pipeline_id, stage_id, canonical_code, dead_reason_code, depth_reached) VALUES ('companyops','2464812771','k2',?,?,?)"
    bad(con, sm, "DEAD", None, None)                       # DEAD needs a reason
    bad(con, sm, "REPLIED", "WRONG_FIT", None)             # a live rung cannot carry a reason
    bad(con, sm, "MEETING_HELD", None, None)               # derived rungs are not mapping targets
    bad(con, sm, "NOPE", None, None)                       # unknown canonical stage
    bad(con, sm, "REPLIED", None, 3.0)                     # depth_reached only for dead stages
    x(con, sm, "REPLIED", None, None)
    with pytest.raises(sqlite3.IntegrityError):
        x(con, "UPDATE stage_map SET dead_reason_code = 'NO_SHOW' WHERE stage_id = 'k2'")
    x(con, "UPDATE stage_map SET canonical_code='DEAD', dead_reason_code='NO_SHOW', depth_reached=4 WHERE stage_id='k2'")


def test_canonical_stage_checks(con):
    bad(con, "INSERT INTO canonical_stage (canonical_code, rank_label, depth, sort_order, label, definition, is_live, is_dead, is_won) VALUES ('X1','x',50,50,'l','d',1,1,0)")
    bad(con, "INSERT INTO canonical_stage (canonical_code, rank_label, depth, sort_order, label, definition, is_live, is_dead, is_won, flag_interested) VALUES ('X2','y',51,51,'l','d',1,0,0,1)")
    bad(con, "INSERT INTO canonical_stage (canonical_code, rank_label, depth, sort_order, label, definition, is_live, is_dead, is_won) VALUES ('lower','z',52,52,'l','d',1,0,0)")


def test_company_identifier_uniqueness_and_normalisation(con):
    x(con, "INSERT INTO company (canonical_name, name_norm) VALUES ('Acme Pvt Ltd', 'acme pvt ltd')")
    x(con, "INSERT INTO company (canonical_name, name_norm) VALUES ('Acme Global', 'acme global')")
    a, b = [r[0] for r in rows(con, "SELECT company_id FROM company ORDER BY company_id")]
    ins = "INSERT INTO company_identifier (company_id, identifier_type, value_norm) VALUES (?,?,?)"
    x(con, ins, a, "root_domain", "acme.com")
    bad(con, ins, b, "root_domain", "acme.com")                              # unique on (type, value_norm), across companies
    x(con, ins, b, "other", "acme.com")                                      # same text, different type: fine
    for t, v in (("root_domain", "WWW.acme.com"), ("root_domain", "www.acme.com"), ("root_domain", "https://acme.com"), ("root_domain", "acme"),
                 ("root_domain", "acme.com/"), ("root_domain", "ACME.IO"),
                 ("linkedin_company", "in/someone"), ("linkedin_company", "company/Acme"), ("linkedin_company", "company/acme/"),
                 ("cin", "U12345MH2020PTC123"), ("cin", "u12345mh2020ptc123456"),
                 ("hs_company", "123"), ("hs_company", "main:abc"), ("root_domain", ""), ("nonsense", "x")):
        bad(con, ins, b, t, v)
    x(con, ins, b, "cin", "U12345MH2020PTC123456")
    x(con, ins, b, "hs_company", "main:123")
    x(con, ins, b, "hs_company", "companyops:123")                           # same HubSpot id in another portal is a different identifier
    x(con, "INSERT INTO company_identifier (company_id, identifier_type, value_norm, is_primary) VALUES (?,?,?,1)", a, "linkedin_company", "company/acme")
    bad(con, "INSERT INTO company_identifier (company_id, identifier_type, value_norm, is_primary) VALUES (?,?,?,1)", a, "linkedin_company", "company/acme-two")  # one primary per type
    bad(con, ins, 99999, "google_place_id", "ChIJ")                          # FK


def test_company_checks_and_merge_tombstones(con):
    bad(con, "INSERT INTO company (canonical_name, name_norm) VALUES ('X', 'Upper')")
    bad(con, "INSERT INTO company (canonical_name, name_norm, hq_country) VALUES ('X', 'x', 'IND')")
    bad(con, "INSERT INTO company (canonical_name, name_norm, founded_year) VALUES ('X', 'x', 1200)")
    bad(con, "INSERT INTO company (canonical_name, name_norm, status) VALUES ('X', 'x', 'bankrupt')")
    x(con, "INSERT INTO company (canonical_name, name_norm, hq_country, india_hq) VALUES ('A', 'a', 'IN', 'yes')")
    x(con, "INSERT INTO company (canonical_name, name_norm) VALUES ('B', 'b')")
    x(con, "INSERT INTO company (canonical_name, name_norm) VALUES ('C', 'c')")
    a, b, c = [r[0] for r in rows(con, "SELECT company_id FROM company ORDER BY company_id")]
    bad(con, "UPDATE company SET merged_into_company_id = ?, merged_at = '2026-10-04T00:00:00.000Z' WHERE company_id = ?", a, a)   # not into itself
    bad(con, "UPDATE company SET merged_into_company_id = ? WHERE company_id = ?", a, b)                                          # needs merged_at
    x(con, "UPDATE company SET merged_into_company_id = ?, merged_at = '2026-10-04T00:00:00.000Z' WHERE company_id = ?", a, b)
    x(con, "UPDATE company SET merged_into_company_id = ?, merged_at = '2026-10-04T01:00:00.000Z' WHERE company_id = ?", b, c)
    assert rows(con, "SELECT company_id, survivor_company_id, hops FROM v_company_survivor ORDER BY company_id") == [(a, a, 0), (b, a, 1), (c, a, 2)]
    assert [r[0] for r in rows(con, "SELECT company_id FROM v_company_golden")] == [a]


def test_company_account_link_and_cross_portal_company(con):
    x(con, "INSERT INTO company (canonical_name, name_norm) VALUES ('Shared Co', 'shared co')")
    cid = val(con, "SELECT company_id FROM company")
    ins = "INSERT INTO company_account_link (company_id, account_id, hs_company_id, hs_domain) VALUES (?,?,?,?)"
    x(con, ins, cid, "main", "301", "shared.com")
    x(con, ins, cid, "companyops", "301", "shared.com")                      # same company in two portals: allowed, kept as two rows
    bad(con, ins, cid, "main", "301", "shared.com")
    bad(con, ins, cid, "main", "abc", "shared.com")
    x(con, "INSERT INTO company_identifier (company_id, identifier_type, value_norm, is_primary) VALUES (?, 'root_domain', 'shared.com', 1)", cid)
    g = rows(con, "SELECT root_domain, accounts, portal_links FROM v_company_golden")
    assert g == [("shared.com", "companyops,main", 2)]


def test_contact_phone_e164_and_indian_mobile_gate(con):
    x(con, "INSERT INTO contact (first_name, full_name) VALUES ('A','A One')")
    cid = val(con, "SELECT contact_id FROM contact")
    ins = "INSERT INTO contact_phone (contact_id, phone_raw, phone_e164, phone_type) VALUES (?,?,?,?)"
    for raw, e164 in (("0123", "919876543210"), ("x", "+0123456789"), ("x", "+91 98765 43210"), ("x", "+1"), ("x", "+9198765432101234")):
        bad(con, ins, cid, raw, e164, "mobile")
    x(con, ins, cid, "+91 98765 43210", "+919876543210", "mobile")
    x(con, ins, cid, "044 2345 6789", "+914423456789", "landline")           # Indian landline: valid E.164, NOT a mobile
    x(con, ins, cid, "+1 415 555 0100", "+14155550100", "mobile")            # foreign mobile
    x(con, ins, cid, "+91 5xxxx", "+915123456789", "unknown")                # +91 5... is not a mobile range
    x(con, ins, cid, "unparseable", None, "unknown")                         # raw only
    assert rows(con, "SELECT phone_e164, is_indian_mobile FROM contact_phone ORDER BY phone_id") == \
        [("+919876543210", 1), ("+914423456789", 0), ("+14155550100", 0), ("+915123456789", 0), (None, 0)]
    bad(con, ins, cid, "dup", "+919876543210", "mobile")                     # same number twice for one contact
    bad(con, ins, cid, "unparseable", None, "unknown")                       # same raw (no e164) twice
    with pytest.raises(sqlite3.OperationalError):                            # generated: cannot be written
        x(con, "UPDATE contact_phone SET is_indian_mobile = 1 WHERE phone_id = 2")
    bad(con, "INSERT INTO contact_phone (contact_id, phone_raw, phone_type) VALUES (?, 'p', 'satellite')", cid)
    x(con, "UPDATE contact_phone SET is_primary = 1 WHERE phone_id = 1")
    bad(con, "UPDATE contact_phone SET is_primary = 1 WHERE phone_id = 2")  # one primary per contact
    assert rows(con, "SELECT has_indian_mobile, best_mobile_e164, phone_count FROM v_contact_mobile_gate") == [(1, "+919876543210", 5)]
    # placeholders never open the gate
    x(con, "INSERT INTO contact (first_name) VALUES ('B')")
    c2 = val(con, "SELECT MAX(contact_id) FROM contact")
    x(con, "INSERT INTO contact_phone (contact_id, phone_raw, phone_e164, phone_type, is_placeholder) VALUES (?,?,?,?,1)", c2, "9999999999", "+919999999999", "mobile")
    assert val(con, "SELECT has_indian_mobile FROM v_contact_mobile_gate WHERE contact_id = ?", c2) == 0


def test_contact_constraints(con):
    bad(con, "INSERT INTO contact (account_id, hs_contact_id) VALUES ('main', NULL)")                          # both or neither
    bad(con, "INSERT INTO contact (hs_contact_id) VALUES ('1')")
    bad(con, "INSERT INTO contact (email_norm) VALUES ('UPPER@X.COM')")
    bad(con, "INSERT INTO contact (linkedin_person_norm) VALUES ('company/acme')")
    bad(con, "INSERT INTO contact (spoc_type) VALUES ('Tertiary')")
    x(con, "INSERT INTO contact (account_id, hs_contact_id, email_norm, linkedin_person_norm) VALUES ('main','900','a@b.com','in/a-b')")
    bad(con, "INSERT INTO contact (account_id, hs_contact_id) VALUES ('main','900')")
    x(con, "INSERT INTO contact (account_id, hs_contact_id) VALUES ('rat','900')")                            # same id, other portal
    x(con, "INSERT INTO contact (email_norm) VALUES ('no-portal@x.com')")                                    # not yet in any portal
    x(con, "INSERT INTO contact (email_norm) VALUES ('no-portal2@x.com')")


def test_timestamps_json_and_booleans_are_validated(con):
    make_world(con)
    did = add_deal(con, "main", "6001", "default", "c1")
    bad(con, "UPDATE deal SET hs_created_at = '2026-10-04 12:00:00' WHERE deal_id = ?", did)                  # not ISO-T-Z
    bad(con, "UPDATE deal SET hs_created_at = 'yesterday' WHERE deal_id = ?", did)
    bad(con, "UPDATE deal SET props_json = '{not json' WHERE deal_id = ?", did)
    bad(con, "UPDATE deal SET props_json = '[1,2]' WHERE deal_id = ?", did)                                   # must be an object
    bad(con, "UPDATE deal SET is_archived = 2 WHERE deal_id = ?", did)
    bad(con, "UPDATE deal SET cost_usd = -1 WHERE deal_id = ?", did)
    bad(con, "UPDATE deal SET archived_at = '2026-10-04T00:00:00.000Z' WHERE deal_id = ?", did)               # archived_at only when archived
    x(con, "UPDATE deal SET props_json = ?, cost_usd = 5000.5 WHERE deal_id = ?", json.dumps({"gmeet1_link": "https://meet"}), did)
    bad(con, "INSERT INTO ops_import_run (kind, source_name, params_json) VALUES ('seed','x','nope')")
    bad(con, "INSERT INTO ops_import_run (kind, source_name) VALUES ('mystery','x')")
    bad(con, "INSERT INTO hsraw_object (account_id, object_type, hs_id, properties_json, fetched_at, payload_sha256) VALUES ('main','deals','1','{bad','2026-10-04T00:00:00.000Z',?)", "a" * 64)


def test_deal_stage_event_ist_day_human_flag_and_uniqueness(con):
    make_world(con)
    d = add_deal(con, "main", "6101", "default", "c1")
    add_event(con, d, "main", "default", "c1", "2026-10-03T18:29:59.999Z", "CRM_UI", "911", "111")        # 23:59:59.999 IST same day
    add_event(con, d, "main", "default", "np", "2026-10-03T18:30:00.000Z", "INTEGRATION", None, None, "c1")   # 00:00 IST next day
    add_event(con, d, "main", "default", "i1", "2026-10-04T02:00:00.000Z", "CRM_UI", "912", "112", "np")
    assert rows(con, "SELECT to_stage_id, ist_day, is_human FROM deal_stage_event ORDER BY entered_at") == \
        [("c1", "2026-10-03", 1), ("np", "2026-10-04", 0), ("i1", "2026-10-04", 1)]
    with pytest.raises(sqlite3.IntegrityError):
        add_event(con, d, "main", "default", "i1", "2026-10-04T02:00:00.000Z")                          # idempotent re-import key
    # same instant, different pipeline stage ids that collide by name are distinct keys
    add_event(con, d, "main", "2425754306", "i1", "2026-10-04T02:00:00.000Z")
    for kw in ({"source": "crm_ui"}, {"source": ""}, {"source": " CRM_UI"}):
        with pytest.raises(sqlite3.IntegrityError):
            add_event(con, d, "main", "default", "w", "2026-10-05T00:00:00.000Z", **kw)
    with pytest.raises(sqlite3.IntegrityError):
        add_event(con, d, "main", "default", "w", "2026-10-05 00:00:00")                                # bad timestamp
    with pytest.raises(sqlite3.IntegrityError):                                                          # actor owner of another portal
        x(con, "INSERT INTO deal_stage_event (deal_id, account_id, pipeline_id, to_stage_id, entered_at, source_type, actor_owner_id) "
               "VALUES (?, 'main','default','w','2026-10-06T00:00:00.000Z','CRM_UI', (SELECT owner_id FROM owner WHERE account_id='companyops'))", d)
    with pytest.raises(sqlite3.IntegrityError):                                                          # event for a deal of another portal
        x(con, "INSERT INTO deal_stage_event (deal_id, account_id, pipeline_id, to_stage_id, entered_at, source_type) "
               "VALUES (?, 'companyops','2464812771','k1','2026-10-06T00:00:00.000Z','CRM_UI')", d)
    with pytest.raises(sqlite3.IntegrityError):                                                          # from_stage without from_pipeline
        x(con, "INSERT INTO deal_stage_event (deal_id, account_id, pipeline_id, to_stage_id, from_stage_id, entered_at, source_type) "
               "VALUES (?, 'main','default','w','c1','2026-10-07T00:00:00.000Z','CRM_UI')", d)
    # history is never cascaded away: a deal with events cannot be deleted (it is archived instead)
    with pytest.raises(sqlite3.IntegrityError):
        x(con, "DELETE FROM deal WHERE deal_id = ?", d)
    assert val(con, "SELECT COUNT(*) FROM deal_stage_event") == 3 + 1


def test_event_indexes_exist_for_report_queries(con):
    idx = {r[0]: r[1] for r in rows(con, "SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name='deal_stage_event'")}
    assert "account_id, pipeline_id, to_stage_id, entered_at" in idx["ix_dse_stage_time"]
    plan = " ".join(r[3] for r in rows(con, "EXPLAIN QUERY PLAN SELECT COUNT(*) FROM deal_stage_event WHERE account_id='main' AND pipeline_id='default' "
                                            "AND to_stage_id='c1' AND entered_at >= '2026-10-01T00:00:00.000Z'"))
    assert "ix_dse_stage_time" in plan
    plan = " ".join(r[3] for r in rows(con, "EXPLAIN QUERY PLAN SELECT * FROM deal_stage_event WHERE deal_id = 5 ORDER BY entered_at"))
    assert "USING" in plan and "SCAN" not in plan.replace("SEARCH", "")
    plan = " ".join(r[3] for r in rows(con, "EXPLAIN QUERY PLAN SELECT COUNT(*) FROM deal WHERE account_id='main' AND pipeline_id='default' AND stage_id='c1' AND is_archived=0"))
    assert "ix_deal_stage" in plan
    assert val(con, "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name='ux_company_identifier_primary'") == 1


def test_engagement_associations(con):
    make_world(con)
    d = add_deal(con, "main", "6201", "default", "c1")
    x(con, "INSERT INTO engagement (account_id, hs_engagement_id, kind, occurred_at, body_text) VALUES ('main','8001','note','2026-10-03T19:00:00.000Z','called, no pickup')")
    e = val(con, "SELECT engagement_id FROM engagement")
    assert val(con, "SELECT ist_day FROM engagement") == "2026-10-04"
    x(con, "INSERT INTO engagement_assoc (engagement_id, account_id, deal_id) VALUES (?, 'main', ?)", e, d)
    bad(con, "INSERT INTO engagement_assoc (engagement_id, account_id, deal_id) VALUES (?, 'main', ?)", e, d)                 # one association per (note, deal)
    bad(con, "INSERT INTO engagement_assoc (engagement_id, account_id) VALUES (?, 'main')", e)                                # exactly one target
    bad(con, "INSERT INTO engagement_assoc (engagement_id, account_id, deal_id, contact_id) VALUES (?, 'main', ?, 1)", e, d)
    d2 = add_deal(con, "companyops", "6202", "2464812771", "k1")
    bad(con, "INSERT INTO engagement_assoc (engagement_id, account_id, deal_id) VALUES (?, 'main', ?)", e, d2)               # deal of another portal
    bad(con, "INSERT INTO engagement (account_id, hs_engagement_id, kind, occurred_at) VALUES ('main','8002','blog','2026-10-03T19:00:00.000Z')")
    assert rows(con, "SELECT hs_engagement_id, note_bucket FROM v_note") == [("8001", None)]


def test_deal_contact_and_company_associations_are_account_scoped(con):
    make_world(con)
    d = add_deal(con, "main", "6301", "default", "c1")
    x(con, "INSERT INTO contact (account_id, hs_contact_id) VALUES ('main','700')")
    x(con, "INSERT INTO contact (account_id, hs_contact_id) VALUES ('rat','700')")
    c_main, c_rat = [r[0] for r in rows(con, "SELECT contact_id FROM contact ORDER BY contact_id")]
    x(con, "INSERT INTO deal_contact (deal_id, contact_id, account_id, is_primary) VALUES (?,?, 'main', 1)", d, c_main)
    bad(con, "INSERT INTO deal_contact (deal_id, contact_id, account_id) VALUES (?,?, 'main')", d, c_rat)
    x(con, "INSERT INTO contact (email_norm) VALUES ('floating@x.com')")
    bad(con, "INSERT INTO deal_contact (deal_id, contact_id, account_id) VALUES (?,?, 'main')", d, val(con, "SELECT MAX(contact_id) FROM contact"))
    x(con, "INSERT INTO company (canonical_name, name_norm) VALUES ('Co', 'co')")
    cid = val(con, "SELECT company_id FROM company")
    x(con, "INSERT INTO company_account_link (company_id, account_id, hs_company_id) VALUES (?, 'rat', '55')", cid)
    bad(con, "INSERT INTO deal_company (deal_id, link_id, account_id) VALUES (?, (SELECT link_id FROM company_account_link), 'main')", d)
    x(con, "DELETE FROM contact WHERE contact_id = ?", c_main)
    assert val(con, "SELECT COUNT(*) FROM deal_contact") == 0


def test_verdict_cost_suppression_and_merge_candidate_rules(con):
    x(con, "INSERT INTO company (canonical_name, name_norm) VALUES ('A','a')")
    x(con, "INSERT INTO company (canonical_name, name_norm) VALUES ('B','b')")
    a, b = [r[0] for r in rows(con, "SELECT company_id FROM company ORDER BY 1")]
    x(con, "INSERT INTO vertical (slug, name) VALUES ('adtech','Adtech')")
    v = val(con, "SELECT vertical_id FROM vertical")
    ver = "INSERT INTO tam_company_verdict (company_id, vertical_id, icp_bucket, verdict_method, is_placeholder, is_current, reason) VALUES (?,?,?,?,?,?,?)"
    x(con, ver, a, v, "out", "rules", 0, 1, "too small")                       # a reject is kept, with its reason
    bad(con, ver, a, v, "fit", "rules", 0, 1, "second current verdict")        # one current verdict per (company, vertical)
    x(con, ver, a, v, "fit", "llm", 0, 0, "history row")
    x(con, ver, a, None, "maybe", "rules", 0, 1, "no vertical")
    bad(con, ver, b, v, "fit", "placeholder", 1, 1, "prior")                   # placeholder must be unscored ...
    bad(con, ver, b, v, "unscored", "rules", 1, 1, "x")                        # ... and use method placeholder
    x(con, ver, b, v, "unscored", "placeholder", 1, 1, "pre-classification-prior")
    bad(con, ver, b, v, "excellent", "rules", 0, 0, "x")
    with pytest.raises(sqlite3.IntegrityError):
        x(con, "DELETE FROM company WHERE company_id = ?", a)                  # rejects are never cascaded away

    cl = "INSERT INTO cost_ledger (vendor, unit, qty, usd, usd_basis, occurred_at, is_placeholder) VALUES (?,?,?,?,?,'2026-09-29T10:00:00.000Z',?)"
    x(con, cl, "apollo", "org_enrich", 236, None, "unknown", 1)                # legacy usd_est = 0 -> NULL + placeholder, never 0
    x(con, cl, "apollo", "org_enrich", 1, 0.05, "estimated", 0)
    bad(con, cl, "apollo", "org_enrich", 1, 0.0, "unknown", 0)                 # usd present requires a basis
    bad(con, cl, "apollo", "org_enrich", 1, None, "actual", 0)
    bad(con, cl, "apollo", "org_enrich", 1, 0.0, "estimated", 1)               # placeholder cannot carry a price
    bad(con, cl, "Apollo", "org_enrich", 1, None, "unknown", 0)
    bad(con, cl, "apollo", "org_enrich", -1, None, "unknown", 0)

    sp = "INSERT INTO suppression (kind, match_type, match_value_norm, reason, list_name) VALUES (?,?,?,?,?)"
    x(con, sp, "never_push", "domain", "whale.com", "whale list", "whale_list_27_NEVER_PUSH_TO_HUBSPOT.csv")
    bad(con, sp, "never_push", "domain", "whale.com", "dup", "x")
    x(con, sp, "dnc", "domain", "whale.com", "different kind", "x")
    for t, vv in (("domain", "Whale.com"), ("domain", "www.whale.com"), ("email", "A@B.com"), ("phone", "9876543210"), ("cin", "short"), ("company_id", "1"), ("domain", "x")):
        bad(con, sp, "dnc", t, vv, "r", "l")
    bad(con, sp, "whale", "domain", "ok.com", "r", "l")
    assert rows(con, "SELECT kind, match_value_norm FROM v_suppression_active ORDER BY kind") == [("dnc", "whale.com"), ("never_push", "whale.com")]
    x(con, "UPDATE suppression SET is_active = 0 WHERE kind = 'dnc'")
    assert rows(con, "SELECT kind FROM v_suppression_active") == [("never_push",)]
    x(con, "INSERT INTO suppression (kind, match_type, match_value_norm, account_id, added_at, expires_at) VALUES ('dnc','email','x@y.com','rat','2020-01-01T00:00:00.000Z','2020-02-01T00:00:00.000Z')")
    assert val(con, "SELECT COUNT(*) FROM v_suppression_active WHERE match_value_norm = 'x@y.com'") == 0      # expired

    mc = "INSERT INTO merge_candidate (entity_type, left_id, right_id, match_kind, score) VALUES (?,?,?,?,?)"
    x(con, mc, "company", a, b, "name_norm", 0.6)
    bad(con, mc, "company", a, b, "name_norm", 0.6)
    bad(con, mc, "company", b, a, "phone", 0.6)                                # canonical order left < right
    bad(con, mc, "company", a, 99999, "phone", 0.6)                            # must exist
    bad(con, mc, "contact", a, b, "email", 0.6)                                # no such contacts
    bad(con, mc, "company", a, b, "x", 1.5)
    bad(con, "UPDATE merge_candidate SET status = 'accepted'")                 # a decision needs decided_at
    x(con, "UPDATE merge_candidate SET status='accepted', decided_at='2026-10-04T00:00:00.000Z', decided_by='bhanu'")


def test_origin_ref_integrity_and_provenance_view(con):
    x(con, "INSERT INTO company (canonical_name, name_norm) VALUES ('A','a')")
    cid = val(con, "SELECT company_id FROM company")
    oi = "INSERT INTO origin_ref (entity_type, entity_id, source_system, source_table, source_pk, match_method) VALUES (?,?,?,?,?,?)"
    assert dict((r[0], r[2]) for r in rows(con, "SELECT * FROM v_origin_coverage"))["company"] == 1          # no origin yet
    x(con, oi, "company", cid, "tam", "companies", "41", "root_domain")
    x(con, oi, "company", cid, "itsvc", "entities", "e-9", "root_domain")                                    # many origins per canonical row
    bad(con, oi, "company", cid, "tam", "companies", "41", "root_domain")
    with pytest.raises(sqlite3.IntegrityError):
        x(con, oi, "company", 424242, "tam", "companies", "42", None)                                        # no such canonical row
    bad(con, oi, "spaceship", cid, "tam", "companies", "43", None)
    assert dict((r[0], r[2]) for r in rows(con, "SELECT * FROM v_origin_coverage"))["company"] == 0
    x(con, "DELETE FROM company WHERE company_id = ?", cid)
    assert val(con, "SELECT COUNT(*) FROM origin_ref") == 0                                                  # cleaned up with the row


def test_audit_log_is_append_only(con):
    x(con, "INSERT INTO ops_audit_log (actor, action, entity_type, entity_ref, after_json) VALUES ('cli','suppression.add','suppression','1','{\"a\":1}')")
    with pytest.raises(sqlite3.IntegrityError):
        x(con, "UPDATE ops_audit_log SET actor = 'someone else'")
    with pytest.raises(sqlite3.IntegrityError):
        x(con, "DELETE FROM ops_audit_log")
    bad(con, "INSERT INTO ops_audit_log (actor, action, after_json) VALUES ('cli','x','not json')")
    assert val(con, "SELECT COUNT(*) FROM ops_audit_log") == 1


def test_ops_tables_rules(con):
    x(con, "INSERT INTO ops_import_run (kind, source_name, account_id) VALUES ('hubspot_sync','hubspot:main:deals','main')")
    bad(con, "INSERT INTO ops_import_run (kind, source_name, status, finished_at) VALUES ('seed','s','running','2026-10-04T00:00:00.000Z')")   # running has no end
    ss = "INSERT INTO ops_sync_state (source_system, scope, object_type, cursor_name, account_id, last_status, last_success_at) VALUES (?,?,?,?,?,?,?)"
    x(con, ss, "hubspot", "main", "deals", "hs_lastmodifieddate", "main", "ok", "2026-10-04T10:00:00.000Z")
    bad(con, ss, "hubspot", "main", "deals", "hs_lastmodifieddate", "main", "ok", "2026-10-04T10:00:00.000Z")        # one cursor per key
    bad(con, ss, "hubspot", "main", "contacts", "default", None, "never", None)                                      # hubspot scope needs account
    bad(con, ss, "hubspot", "rat", "contacts", "default", "main", "never", None)                                     # scope must equal account
    bad(con, ss, "gmail", "bhanu@lh2.ai", "messages", "historyId", None, "ok", None)                                 # ok needs a success time
    x(con, ss, "gmail", "bhanu@lh2.ai", "messages", "historyId", None, "never", None)
    assert val(con, "SELECT hours_since_success FROM v_sync_freshness WHERE scope='main'") is not None
    dq = "INSERT INTO ops_dq_issue (fingerprint, rule_code, severity, message, status, resolved_at) VALUES (?,?,?,?,?,?)"
    x(con, dq, "unmapped_stage|companyops|2464812771|k2", "unmapped_stage", "error", "stage k2 has no stage_map row", "open", None)
    bad(con, dq, "unmapped_stage|companyops|2464812771|k2", "unmapped_stage", "error", "dup fingerprint", "open", None)
    bad(con, dq, "f2", "r", "catastrophic", "m", "open", None)
    bad(con, dq, "f3", "r", "warn", "m", "resolved", None)                                                           # resolved needs resolved_at
    assert rows(con, "SELECT rule_code, severity, issues FROM v_dq_open") == [("unmapped_stage", "error", 1)]
    assert db.doctor(con)["open_dq_errors"] == 1 and db.doctor(con)["warnings"]


def test_updated_at_trigger_and_explicit_override(con):
    x(con, "INSERT INTO company (canonical_name, name_norm) VALUES ('A','a')")
    before = val(con, "SELECT updated_at FROM company")
    time.sleep(0.01)
    x(con, "UPDATE company SET canonical_name = 'A2'")
    after = val(con, "SELECT updated_at FROM company")
    assert after > before and after.endswith("Z")
    x(con, "UPDATE company SET canonical_name = 'A3', updated_at = '2026-01-01T00:00:00.000Z'")
    assert val(con, "SELECT updated_at FROM company") == "2026-01-01T00:00:00.000Z"                                   # explicit value wins


# ------------------------------------------------------------------------------------------------ hsraw
def test_hsraw_object_and_revision_trigger(con):
    ins = ("INSERT INTO hsraw_object (account_id, object_type, hs_id, properties_json, history_json, history_fetched_at, fetched_at, payload_sha256) "
           "VALUES ('main','deals','5001',?,?,?,?,?)")
    x(con, ins, '{"dealstage":"c1"}', '{"dealstage":[{"value":"c1"}]}', "2026-10-04T10:00:00.000Z", "2026-10-04T10:00:00.000Z", "a" * 64)
    bad(con, ins, '{"dealstage":"c1"}', None, None, "2026-10-04T10:00:00.000Z", "b" * 64)                             # unique (account, type, id)
    bad(con, "INSERT INTO hsraw_object (account_id, object_type, hs_id, properties_json, history_json, fetched_at, payload_sha256) "
             "VALUES ('main','deals','5002','{}','[]','2026-10-04T10:00:00.000Z',?)", "c" * 64)                           # history needs history_fetched_at
    bad(con, "INSERT INTO hsraw_object (account_id, object_type, hs_id, properties_json, fetched_at, payload_sha256) VALUES ('main','widgets','1','{}','2026-10-04T10:00:00.000Z',?)", "d" * 64)
    bad(con, "INSERT INTO hsraw_object (account_id, object_type, hs_id, properties_json, fetched_at, payload_sha256) VALUES ('main','deals','1','{}','2026-10-04T10:00:00.000Z','XYZ')")
    assert val(con, "SELECT COUNT(*) FROM hsraw_object WHERE canonical_payload_sha256 IS NOT payload_sha256") == 1     # dirty until projected
    x(con, "UPDATE hsraw_object SET canonical_payload_sha256 = payload_sha256")
    assert val(con, "SELECT COUNT(*) FROM hsraw_object WHERE canonical_payload_sha256 IS NOT payload_sha256") == 0
    x(con, "UPDATE hsraw_object SET properties_json = '{\"dealstage\":\"i1\"}', payload_sha256 = ?, fetched_at = '2026-10-04T11:00:00.000Z'", "e" * 64)
    assert rows(con, "SELECT payload_sha256, json_extract(properties_json,'$.dealstage') FROM hsraw_object_revision") == [("a" * 64, "c1")]
    x(con, "UPDATE hsraw_object SET fetched_at = '2026-10-04T12:00:00.000Z'")                                         # no hash change -> no revision
    assert val(con, "SELECT COUNT(*) FROM hsraw_object_revision") == 1
    x(con, "DELETE FROM hsraw_object")
    assert val(con, "SELECT COUNT(*) FROM hsraw_object_revision") == 0


# ------------------------------------------------------------------------------------------------ views
def test_every_view_runs_on_empty_and_populated_database(con):
    views = [r[0] for r in rows(con, "SELECT name FROM sqlite_master WHERE type='view' AND name NOT LIKE 'legacy_%' ORDER BY name")]
    assert {"v_deal_current", "v_funnel_stage_counts", "v_funnel_canonical_counts", "v_funnel_entries_daily", "v_stage_event_enriched",
            "v_unmapped_stage", "v_deal_cohort", "v_legacy_snapshot_rows"} <= set(views)
    for v in views:
        con.execute('SELECT * FROM "%s"' % v).fetchall()
    make_world(con)
    d = add_deal(con, "main", "6401", "default", "i1", "111")
    add_event(con, d, "main", "default", "i1", "2026-10-03T19:00:00.000Z", "CRM_UI", "911", "111")
    for v in views:
        con.execute('SELECT * FROM "%s"' % v).fetchall()


def test_funnel_views_counts_dedup_and_human_split(con):
    make_world(con)
    # deals: 3 in Cold Call (1 archived), 1 Interested, 1 Won, 1 Dead
    a = add_deal(con, "main", "1", "default", "c1", "111")
    b = add_deal(con, "main", "2", "default", "c1", "112")
    add_deal(con, "main", "3", "default", "c1", "111", archived=1)
    c = add_deal(con, "main", "4", "default", "i1", "111")
    d = add_deal(con, "main", "5", "default", "w", "112")
    e = add_deal(con, "main", "6", "default", "d1", None)
    other = add_deal(con, "main", "7", "2425754306", "i1", None)           # SAME stage id, other pipeline
    # events on IST day 2026-10-04 (UTC 2026-10-03T18:30Z .. 2026-10-04T18:29Z)
    T = "2026-10-04T%s:00.000Z"
    add_event(con, a, "main", "default", "c1", T % "03:00", "INTEGRATION")                        # auto entry
    add_event(con, b, "main", "default", "c1", T % "03:01", "API")                                # auto entry
    add_event(con, c, "main", "default", "i1", T % "04:00", "CRM_UI", "911", "111", "np")         # human, native stage i1
    add_event(con, c, "main", "default", "i2", T % "05:00", "CRM_UI", "911", "111", "i1")         # human, same canonical rung (INTERESTED), same actor
    add_event(con, c, "main", "default", "i1", T % "06:00", "CRM_UI", "912", "112", "i2")         # another actor re-enters i1
    add_event(con, c, "main", "default", "i1", T % "07:00", "CRM_UI", "911", "111", "i2")         # same actor again: de-duplicated
    add_event(con, d, "main", "default", "w", T % "08:00", "CRM_UI", "912", "112")
    add_event(con, e, "main", "default", "d1", T % "09:00", "CRM_UI", "911", "111")
    add_event(con, other, "main", "2425754306", "i1", T % "09:30", "CRM_UI", "911", "111")        # must not leak into the Coding numbers
    # a late-night UTC event belongs to the next IST day
    add_event(con, a, "main", "default", "np", "2026-10-04T19:00:00.000Z", "CRM_UI", "911", "111", "c1")

    # occupancy now (archived excluded; pipelines separated even though stage ids collide)
    occ = {r[0]: r[1] for r in rows(con, "SELECT stage_id, deals FROM v_funnel_stage_counts WHERE funnel_slug = 'coding'")}
    assert occ == {"c1": 2, "np": 0, "i1": 1, "i2": 0, "w": 1, "d1": 1}
    assert rows(con, "SELECT deals FROM v_funnel_stage_counts WHERE funnel_slug='coops_global' AND stage_id='i1'") == [(1,)]
    assert rows(con, "SELECT stage_id FROM v_funnel_stage_counts WHERE funnel_slug='coding' ORDER BY stage_order")[0] == ("c1",)
    canon = {(r[0], r[1]): r[2] for r in rows(con, "SELECT canonical_code, dead_reason_code, deals FROM v_funnel_canonical_counts WHERE funnel_slug='coding'")}
    assert canon[("SOURCED", "")] == 2 and canon[("INTERESTED", "")] == 1 and canon[("WON", "")] == 1 and canon[("DEAD", "NOT_INTERESTED")] == 1
    # native entries per stage for the IST day
    ent = {r[0]: r[1:] for r in rows(con, "SELECT stage_id, entries_human, entries_auto, entries_total, deals_entered_human, deals_entered_all "
                                          "FROM v_funnel_entries_daily WHERE ist_day='2026-10-04' AND pipeline_id='default'")}
    assert ent["c1"] == (0, 2, 2, 0, 2)                  # both entries automated: shown in the Automated column, not dropped
    assert ent["i1"] == (2, 0, 2, 1, 1)                  # actors 911 and 912 once each; the repeat by 911 is de-duplicated; ONE distinct deal
    assert ent["i2"] == (1, 0, 1, 1, 1)
    assert ent["w"] == (1, 0, 1, 1, 1) and ent["d1"] == (1, 0, 1, 1, 1)
    assert val(con, "SELECT entries_total FROM v_funnel_entries_daily WHERE ist_day='2026-10-04' AND pipeline_id='2425754306'") == 1
    assert rows(con, "SELECT stage_id, entries_human FROM v_funnel_entries_daily WHERE ist_day='2026-10-05'") == [("np", 1)]      # 19:00Z -> next IST day
    # canonical ladder: i1 + i2 of one deal and actor collapse into ONE entry for INTERESTED
    can = {r[0]: r[1:] for r in rows(con, "SELECT canonical_code, entries_human, entries_auto, entries_total, deals_entered_human, deals_entered_all "
                                         "FROM v_funnel_entries_daily_canonical WHERE ist_day='2026-10-04' AND funnel_slug='coding'")}
    assert can["INTERESTED"] == (2, 0, 2, 1, 1)           # (c,911) and (c,912)
    assert can["SOURCED"] == (0, 2, 2, 0, 2)
    assert can["DEAD"] == (1, 0, 1, 1, 1)
    # event store joined to the mapping
    assert val(con, "SELECT COUNT(*) FROM v_stage_event_enriched WHERE canonical_code IS NULL AND funnel_slug = 'coding'") == 0
    assert val(con, "SELECT flag_interested FROM v_stage_event_enriched WHERE stage_id='i1' AND funnel_slug='coding' LIMIT 1") == 1
    # materialised facts roll up consistently with the view
    x(con, "INSERT INTO rpt_report_run (report_kind, report_day) VALUES ('rebuild','2026-10-04')")
    x(con, "INSERT INTO rpt_funnel_daily (ist_day, account_id, pipeline_id, stage_id, canonical_code, entries_human, entries_auto, deals_entered_human, deals_entered_all, occupancy_eod, report_run_id) "
           "SELECT v.ist_day, v.account_id, v.pipeline_id, v.stage_id, e.canonical_code, v.entries_human, v.entries_auto, v.deals_entered_human, v.deals_entered_all, NULL, 1 "
           "FROM v_funnel_entries_daily v JOIN v_stage_effective e ON e.account_id=v.account_id AND e.pipeline_id=v.pipeline_id AND e.stage_id=v.stage_id")
    assert rows(con, "SELECT SUM(entries_total) FROM rpt_funnel_daily WHERE ist_day='2026-10-04'")[0][0] == val(con, "SELECT SUM(entries_total) FROM v_funnel_entries_daily WHERE ist_day='2026-10-04'")
    assert val(con, "SELECT entries_total FROM v_rpt_funnel_daily_canonical WHERE canonical_code='INTERESTED' AND pipeline_id='default'") == 3   # native sum (2 + 1); the view above is the exact one


def test_deal_current_view_resolves_names(con):
    make_world(con)
    d = add_deal(con, "main", "9", "default", "d1", "111")
    row = con.execute("SELECT * FROM v_deal_current WHERE deal_id = ?", (d,)).fetchone()
    assert row["funnel_slug"] == "coding" and row["pipeline_label"] == "Coding" and row["stage_label"].startswith("Dead")
    assert row["canonical_code"] == "DEAD" and row["is_dead"] == 1 and row["dead_reason_code"] == "NOT_INTERESTED" and row["depth_reached"] == 3
    assert row["owner_name"] == "Alice" and row["is_mapped"] == 1 and row["created_ist_day"] == "2026-10-01"


def test_unmapped_stage_view_and_doctor_warning(con):
    make_world(con)
    assert [r[:3] for r in rows(con, "SELECT account_id, pipeline_id, stage_id FROM v_unmapped_stage ORDER BY 1,2,3")] == \
        [("companyops", "2464812771", "k2"), ("main", "2425754306", "i1"), ("main", "2425754306", "w")]
    d = add_deal(con, "companyops", "10", "2464812771", "k2")
    add_event(con, d, "companyops", "2464812771", "k2", "2026-10-04T03:00:00.000Z")
    assert rows(con, "SELECT deals_now, events FROM v_unmapped_stage WHERE stage_id='k2'") == [(1, 1)]
    assert any("no stage_map" in w for w in db.doctor(con)["warnings"])
    # unmapped stages are never silently dropped from the canonical roll-up
    assert ("UNMAPPED", 1) in rows(con, "SELECT canonical_code, deals FROM v_funnel_canonical_counts WHERE funnel_slug='cluster2' AND deals > 0")


def test_cohort_view_applies_cluster2_rule(con):
    make_world(con)
    x(con, "INSERT INTO stage_map (account_id, pipeline_id, stage_id, canonical_code) VALUES ('companyops','2464812771','k2','REPLIED')")
    cases = {  # hs id: (created, joined, expected in_scope)
        "20": ("2026-09-10T05:00:00.000Z", "2026-09-15T05:00:00.000Z", 0),    # bulk-migrated: joined on 09-15 having been created earlier
        "21": ("2026-09-16T05:00:00.000Z", "2026-09-16T06:00:00.000Z", 1),    # created and joined after the cutoff
        "22": ("2026-09-01T05:00:00.000Z", "2026-09-14T06:00:00.000Z", 0),    # joined before the cohort start
        "23": ("2026-09-15T02:00:00.000Z", "2026-09-15T03:00:00.000Z", 1),    # created ON the cutoff day: genuine new deal
    }
    ids = {}
    for hs, (created, joined, _) in cases.items():
        ids[hs] = add_deal(con, "companyops", hs, "2464812771", "k1", created=created)
        add_event(con, ids[hs], "companyops", "2464812771", "k1", joined, "INTEGRATION")
        add_event(con, ids[hs], "companyops", "2464812771", "k2", "2026-09-20T05:00:00.000Z", "CRM_UI")
    got = {r[0]: r[1] for r in rows(con, "SELECT d.hs_deal_id, c.in_scope FROM v_deal_cohort c JOIN deal d ON d.deal_id = c.deal_id")}
    assert got == {hs: v[2] for hs, v in cases.items()}
    all_k2 = val(con, "SELECT entries_total FROM v_funnel_entries_daily WHERE stage_id='k2' AND ist_day='2026-09-20'")
    scoped = val(con, "SELECT entries_total FROM v_funnel_entries_daily_in_scope WHERE stage_id='k2' AND ist_day='2026-09-20'")
    assert (all_k2, scoped) == (4, 2)                                        # both 'all history' and 'in scope' are available
    # a pipeline without cohort config keeps everything
    d = add_deal(con, "main", "30", "default", "c1")
    add_event(con, d, "main", "default", "c1", "2026-01-01T00:00:00.000Z")
    assert val(con, "SELECT in_scope FROM v_deal_cohort WHERE deal_id = ?", d) == 1


def test_first_human_event_and_dwell_views(con):
    make_world(con)
    d = add_deal(con, "main", "40", "default", "i1")
    add_event(con, d, "main", "default", "c1", "2026-10-01T03:00:00.000Z", "INTEGRATION")
    add_event(con, d, "main", "default", "np", "2026-10-02T03:00:00.000Z", "CRM_UI", "911", "111", "c1")
    add_event(con, d, "main", "default", "i1", "2026-10-03T03:00:00.000Z", "CRM_UI", "911", "111", "np")
    assert rows(con, "SELECT first_human_at, first_human_ist_day FROM v_deal_first_human_event") == [("2026-10-02T03:00:00.000Z", "2026-10-02")]
    dw = rows(con, "SELECT stage_id, exited_at, dwell_hours FROM v_deal_stage_dwell ORDER BY entered_at")
    assert dw == [("c1", "2026-10-02T03:00:00.000Z", 24.0), ("np", "2026-10-03T03:00:00.000Z", 24.0), ("i1", None, None)]


def test_owner_assignment_history(con):
    make_world(con)
    d = add_deal(con, "main", "50", "default", "c1")
    oid = val(con, "SELECT owner_id FROM owner WHERE account_id='main' AND hs_owner_id='111'")
    ins = "INSERT INTO deal_owner_event (deal_id, account_id, owner_id, assigned_at, source_type) VALUES (?, 'main', ?, ?, ?)"
    x(con, ins, d, oid, "2026-10-01T03:00:00.000Z", "CRM_UI")
    x(con, ins, d, None, "2026-10-02T03:00:00.000Z", "API")                      # unassigned
    bad(con, ins, d, oid, "2026-10-01T03:00:00.000Z", "CRM_UI")
    bad(con, ins, d, val(con, "SELECT owner_id FROM owner WHERE account_id='companyops'"), "2026-10-03T03:00:00.000Z", "CRM_UI")


# ------------------------------------------------------------------------------------------------ rpt tables
def test_rpt_tables_constraints_and_legacy_snapshot_import(con):
    make_world(con)
    x(con, "INSERT INTO rpt_report_run (report_kind, report_day, mail_mode) VALUES ('daily','2026-10-04','dry_run')")
    bad(con, "INSERT INTO rpt_report_run (report_kind, report_day, mail_mode) VALUES ('daily','2026-10-04','sent')")          # sending needs recipient + time
    bad(con, "INSERT INTO rpt_report_run (report_kind, report_day) VALUES ('daily','04/10/2026')")
    x(con, "INSERT INTO rpt_report_run (report_kind, report_day, mail_mode, mail_to, mail_sent_at) VALUES ('daily','2026-10-04','sent','bhanu.enamala@lh2.ai','2026-10-04T13:00:00.000Z')")
    f = "INSERT INTO rpt_funnel_daily (ist_day, account_id, pipeline_id, stage_id, entries_human, entries_auto, deals_entered_human, deals_entered_all) VALUES ('2026-10-04','main','default','c1',?,?,?,?)"
    x(con, f, 3, 2, 3, 5)
    bad(con, f, 3, 2, 3, 5)                                                                                                   # PK
    bad(con, "INSERT INTO rpt_funnel_daily (ist_day, account_id, pipeline_id, stage_id, entries_human, deals_entered_human) VALUES ('2026-10-04','main','default','np',1,2)")   # deals > entries
    bad(con, "INSERT INTO rpt_funnel_daily (ist_day, account_id, pipeline_id, stage_id) VALUES ('2026-10-04','main','default','nope')")   # stage must exist
    assert val(con, "SELECT entries_total FROM rpt_funnel_daily") == 5
    oid = val(con, "SELECT owner_id FROM owner WHERE account_id='main' AND hs_owner_id='111'")
    od = "INSERT INTO rpt_owner_daily (ist_day, account_id, pipeline_id, owner_id, metric_code, canonical_code, events_human) VALUES ('2026-10-04','main','default',?,?,?,1)"
    x(con, od, oid, "stage_entry", "INTERESTED")
    bad(con, od, oid, "stage_entry", "INTERESTED")
    x(con, od, None, "stage_entry", "INTERESTED")                                                                             # unassigned bucket
    bad(con, od, None, "stage_entry", "INTERESTED")
    bad(con, od, oid, "Bad Metric", None)
    d = add_deal(con, "main", "60", "default", "c1")
    x(con, "INSERT INTO rpt_engaged_daily (ist_day, owner_id, deal_id, account_id, pipeline_id, via) VALUES ('2026-10-04',?,?, 'main','default','stage')", oid, d)
    bad(con, "INSERT INTO rpt_engaged_daily (ist_day, owner_id, deal_id, account_id, pipeline_id, via) VALUES ('2026-10-04',?,?, 'main','default','telepathy')", oid, d)
    # legacy snapshot import + flattening view
    snap = {"date": "2026-10-01", "dashboard_flow": {"Leads Assigned": 59, "Dead": 4}, "current_state": {"No Pickup": 10}, "cumulative": {"Leads Assigned": 1117}, "engaged_deal_ids": ["1", "2", "3"], "seeded": True}
    s = "INSERT INTO rpt_legacy_snapshot (source_family, account_id, pipeline_id, snapshot_day, source_path, is_seeded, dashboard_flow_json, current_state_json, cumulative_json, engaged_deal_ids_json, payload_json) VALUES ('rat','rat','2575252183','2026-10-01','RapidActionTeam/snapshots/rat_2026-10-01.json',1,?,?,?,?,?)"
    x(con, "INSERT OR IGNORE INTO pipeline (account_id, pipeline_id, label) VALUES ('rat','2575252183','Rapid Action Team')")
    x(con, s, json.dumps(snap["dashboard_flow"]), json.dumps(snap["current_state"]), json.dumps(snap["cumulative"]), json.dumps(snap["engaged_deal_ids"]), json.dumps(snap))
    bad(con, s, json.dumps(snap["dashboard_flow"]), json.dumps(snap["current_state"]), json.dumps(snap["cumulative"]), json.dumps(snap["engaged_deal_ids"]), json.dumps(snap))   # one per (family, day)
    bad(con, s.replace("'2026-10-01','Rapid", "'2026-10-01','Rapid").replace("'2026-10-01',", "'2026-10-02',", 1), "{bad", "{}", "{}", "[]", "{}")
    assert val(con, "SELECT engaged_count FROM rpt_legacy_snapshot") == 3
    flat = {(r[0], r[1]): r[2] for r in rows(con, "SELECT metric, row_label, value FROM v_legacy_snapshot_rows")}
    assert flat[("dashboard_flow", "Leads Assigned")] == 59 and flat[("cumulative", "Leads Assigned")] == 1117 and flat[("current_state", "No Pickup")] == 10
    x(con, "INSERT INTO rpt_legacy_crosscheck (snapshot_id, metric, row_label, legacy_value, recomputed_value) VALUES (1,'dashboard_flow','Leads Assigned',59,61)")
    assert val(con, "SELECT delta FROM rpt_legacy_crosscheck") == 2


# ------------------------------------------------------------------------------------------------ ext + FTS
def test_gmail_fts_stays_in_sync_through_insert_update_delete(con):
    x(con, "INSERT INTO ext_gmail_account (email_address, token_ref) VALUES ('bhanu.enamala@lh2.ai', 'bhanu_gmail_readonly_token.json')")
    bad(con, "INSERT INTO ext_gmail_account (email_address) VALUES ('Bhanu.Enamala@lh2.ai')")                                 # normalised addresses only
    acc = val(con, "SELECT gmail_account_id FROM ext_gmail_account")
    x(con, "INSERT INTO ext_gmail_thread (gmail_account_id, thread_id, subject) VALUES (?, 't1', 'Intro to LH2')", acc)
    th = val(con, "SELECT thread_pk FROM ext_gmail_thread")
    ins = ("INSERT INTO ext_gmail_message (gmail_account_id, thread_pk, message_id, from_addr, to_addrs, subject, snippet, body_text, fetched_at) "
           "VALUES (?,?,?,?,?,?,?,?, '2026-10-04T00:00:00.000Z')")
    x(con, ins, acc, th, "m1", "kartik@lh2.ai", "bhanu.enamala@lh2.ai", "COBOL dataset proposal", "snippet one", "We can supply mainframe COBOL repositories.")
    x(con, ins, acc, th, "m2", "prospect@acme.com", "bhanu.enamala@lh2.ai", "Re: pricing", "snippet two", "Pricing for the ledger data looks fine.")
    bad(con, ins, acc, th, "m2", "x@y.com", "z@y.com", "dup message id", "", "")
    bad(con, ins, acc, th, "m3", "Mixed@Case.com", "z@y.com", "uppercase address", "", "")

    def hits(q):
        return [r[0] for r in rows(con, "SELECT m.message_id FROM ext_gmail_message_fts f JOIN ext_gmail_message m ON m.message_pk = f.rowid WHERE ext_gmail_message_fts MATCH ? ORDER BY m.message_id", q)]
    assert hits("cobol") == ["m1"] and hits("pricing") == ["m2"] and hits("subject:proposal") == ["m1"] and hits("from_addr:acme") == ["m2"]
    assert hits("ledger OR mainframe") == ["m1", "m2"]
    x(con, "UPDATE ext_gmail_message SET subject = 'Contract signed' WHERE message_id = 'm1'")
    assert hits("proposal") == [] and hits("subject:contract") == ["m1"] and hits("cobol") == ["m1"]
    x(con, "UPDATE ext_gmail_message SET fetched_at = '2026-10-05T00:00:00.000Z' WHERE message_id = 'm1'")                      # unindexed column: no index churn
    x(con, "DELETE FROM ext_gmail_message WHERE message_id = 'm2'")
    assert hits("pricing") == []
    x(con, "INSERT INTO ext_gmail_label (gmail_account_id, label_id, name, label_type) VALUES (?, 'INBOX', 'INBOX', 'system')", acc)
    x(con, "INSERT INTO ext_gmail_message_label (message_pk, label_pk) SELECT message_pk, label_pk FROM ext_gmail_message, ext_gmail_label")
    x(con, "INSERT INTO ext_gmail_attachment (message_pk, part_id, filename, mime_type, extracted_text) SELECT message_pk, '1', 'terms.pdf', 'application/pdf', 'indemnity clause' FROM ext_gmail_message")
    assert [r[0] for r in rows(con, "SELECT rowid FROM ext_gmail_attachment_fts WHERE ext_gmail_attachment_fts MATCH 'indemnity'")] == [1]
    x(con, "DELETE FROM ext_gmail_account")                                                                                   # cascades to labels, threads, messages, attachments
    assert val(con, "SELECT COUNT(*) FROM ext_gmail_message") == 0 and val(con, "SELECT COUNT(*) FROM ext_gmail_attachment") == 0
    assert hits("cobol") == []
    assert all(v == "ok" for v in db.fts_integrity(con).values())
    # rebuild is safe at any time
    x(con, "INSERT INTO ext_gmail_message_fts(ext_gmail_message_fts) VALUES('rebuild')")


def test_gsheet_tables_and_fts(con):
    ins = "INSERT INTO ext_gsheet_catalog (spreadsheet_id, title, owner_email, catalog_fetched_at, pii_class, pull_enabled) VALUES (?,?,?, '2026-10-04T00:00:00.000Z', ?, ?)"
    x(con, ins, "1abc", "Tracxn", "someone@gmail.com", "normal", 1)
    x(con, ins, "1xyz", "Founder's Office intern screen", "k@lh2holdings.com", "excluded", 0)
    bad(con, ins, "1bad", "x", "o", "excluded", 1)                                                                            # excluded sheets are never pulled
    bad(con, ins, "1bad", "x", "o", "secret", 1)
    x(con, "INSERT INTO ext_gsheet_tab (spreadsheet_id, sheet_id, title, grid_rows, grid_cols) VALUES ('1abc', 0, 'funded', 1000, 20)")
    bad(con, "INSERT INTO ext_gsheet_tab (spreadsheet_id, sheet_id, title) VALUES ('1abc', 0, 'dup sheet id')")
    bad(con, "INSERT INTO ext_gsheet_tab (spreadsheet_id, sheet_id, title, pull_status) VALUES ('1abc', 1, 'x', 'ok')")       # ok needs pulled_at
    bad(con, "INSERT INTO ext_gsheet_tab (spreadsheet_id, sheet_id, title) VALUES ('nope', 9, 'orphan')")
    tab = val(con, "SELECT tab_pk FROM ext_gsheet_tab")
    ins = "INSERT INTO ext_gsheet_row (tab_pk, row_number, cells_json, text_flat, fetched_at) VALUES (?,?,?,?, '2026-10-04T00:00:00.000Z')"
    x(con, ins, tab, 1, '["Company","Domain"]', "Company Domain")
    x(con, ins, tab, 2, '["Acme Fintech","acme.in"]', "Acme Fintech acme.in")
    bad(con, ins, tab, 2, '[]', "dup row")
    bad(con, ins, tab, 3, '{"not":"array"}', "x")
    assert [r[0] for r in rows(con, "SELECT rowid FROM ext_gsheet_row_fts WHERE ext_gsheet_row_fts MATCH 'fintech'")] == [2]
    x(con, "UPDATE ext_gsheet_row SET text_flat = 'Acme Insurtech acme.in' WHERE row_number = 2")
    assert rows(con, "SELECT rowid FROM ext_gsheet_row_fts WHERE ext_gsheet_row_fts MATCH 'fintech'") == []
    x(con, "DELETE FROM ext_gsheet_catalog WHERE spreadsheet_id = '1abc'")                                                    # cascade: tabs and rows
    assert val(con, "SELECT COUNT(*) FROM ext_gsheet_row") == 0 and rows(con, "SELECT rowid FROM ext_gsheet_row_fts WHERE ext_gsheet_row_fts MATCH 'insurtech'") == []
    assert db.doctor(con)["ok"]


def test_lead_source_and_vertical(con):
    x(con, "INSERT INTO lead_source (code, label, channel) VALUES ('scraping_algo_it_services', 'Scraping Algo ( IT services )', 'scrape')")
    bad(con, "INSERT INTO lead_source (code, label) VALUES ('scraping_algo_it_services', 'dup')")
    bad(con, "INSERT INTO lead_source (code, label) VALUES ('Has Spaces', 'x')")
    x(con, "INSERT INTO vertical (slug, name, country_code) VALUES ('india_cobol_ip', 'India COBOL IP', 'IN')")
    bad(con, "INSERT INTO vertical (slug, name, country_code) VALUES ('uk', 'UK', 'GBR')")
    bad(con, "INSERT INTO vertical (slug, name, parent_vertical_id) VALUES ('child', 'Child', 99)")   # parent must exist
    make_world(con)
    d = add_deal(con, "main", "70", "default", "c1")
    x(con, "UPDATE deal SET lead_source_id = 1, vertical_id = 1 WHERE deal_id = ?", d)
    assert val(con, "SELECT lead_source FROM v_deal_current WHERE deal_id = ?", d) == "Scraping Algo ( IT services )"


def test_archived_deals_keep_history_but_leave_occupancy(con):
    make_world(con)
    d = add_deal(con, "main", "80", "default", "c1", archived=1)
    x(con, "UPDATE deal SET archived_at = '2026-10-02T00:00:00.000Z' WHERE deal_id = ?", d)
    add_event(con, d, "main", "default", "c1", "2026-10-01T03:00:00.000Z", "INTEGRATION")
    assert val(con, "SELECT deals FROM v_funnel_stage_counts WHERE funnel_slug='coding' AND stage_id='c1'") == 0
    assert val(con, "SELECT COUNT(*) FROM deal_stage_event") == 1
    assert val(con, "SELECT is_archived FROM v_deal_current WHERE deal_id = ?", d) == 1


def test_date_columns_reject_non_dates_instead_of_passing_on_null(con):
    """date('04/10/2026') is NULL and a NULL CHECK result would pass: the CHECKs use IS, so garbage is rejected."""
    bad(con, "UPDATE pipeline SET cohort_start = '15/09/2026' WHERE funnel_slug = 'cluster2'")
    bad(con, "UPDATE pipeline SET cohort_start = '2026-13-45' WHERE funnel_slug = 'cluster2'")
    bad(con, "UPDATE pipeline SET cohort_start = 'abcdefghij' WHERE funnel_slug = 'cluster2'")
    bad(con, "INSERT INTO cost_rate (vendor, unit, usd_per_unit, effective_from) VALUES ('apollo','credit',0.1,'01-10-2026')")
    x(con, "INSERT INTO cost_rate (vendor, unit, usd_per_unit, effective_from) VALUES ('apollo','credit',0.1,'2026-10-01')")
    bad(con, "INSERT INTO cost_rate (vendor, unit, usd_per_unit, effective_from) VALUES ('apollo','credit',0.2,'2026-10-01')")


def test_cli_main_runs_status_and_doctor(path, capsys):
    assert db.main(["migrate", "--db", path]) == 0
    assert db.main(["status", "--db", path]) == 0
    assert db.main(["doctor", "--db", path]) == 0
    out = capsys.readouterr().out
    assert '"ok": true' in out


def test_migrations_have_no_if_not_exists_so_a_divergent_object_fails_loudly(path):
    for m in db.discover_migrations():
        assert not [s for s in db.split_sql_statements(m.sql) if "IF NOT EXISTS" in s.upper().split("(")[0]], m.filename
    pre = sqlite3.connect(path, isolation_level=None)
    pre.execute("CREATE TABLE account (hand_made INTEGER)")                  # a pre-existing, different 'account'
    pre.close()
    with pytest.raises(db.MigrationError) as ei:
        db.migrate(path)
    assert "already exists" in str(ei.value) and "rolled back" in str(ei.value)


def test_doctor_detects_schema_drift_that_the_checksum_ledger_cannot_see(con):
    assert db.doctor(con)["schema_drift"] == []
    x(con, "CREATE TABLE hand_made (a INTEGER)")
    x(con, "DROP VIEW v_note")
    x(con, "CREATE INDEX ix_extra ON company(first_seen_at)")
    rep = db.doctor(con)
    assert not rep["ok"]
    drift = " | ".join(rep["schema_drift"])
    assert "table hand_made: in the database, not created by any migration" in drift
    assert "view v_note: in the migrations, missing from the database" in drift
    assert "index ix_extra" in drift


def test_readonly_connection_and_transaction_helper(path):
    db.migrate(path)
    ro = db.connect(path, readonly=True)
    assert val(ro, "SELECT COUNT(*) FROM account") == 3
    with pytest.raises(sqlite3.OperationalError):
        ro.execute("INSERT INTO lead_source (code, label) VALUES ('x','x')")
    ro.close()
    with pytest.raises(FileNotFoundError):
        db.connect(path + ".missing", readonly=True)
    c = db.connect(path)
    with pytest.raises(RuntimeError):
        with db.transaction(c):
            c.execute("INSERT INTO lead_source (code, label) VALUES ('rolled_back','x')")
            raise RuntimeError("boom")
    assert val(c, "SELECT COUNT(*) FROM lead_source") == 0 and not c.in_transaction
    with db.transaction(c):
        c.execute("INSERT INTO lead_source (code, label) VALUES ('kept','x')")
    assert val(c, "SELECT COUNT(*) FROM lead_source") == 1
    c.close()


def test_doctor_on_empty_database_does_not_crash(path):
    open(path, "w").close()                                                  # an existing, empty database file
    rep = db.doctor(path)
    assert rep["migrations"]["applied"] == [] and rep["migrations"]["pending"] and rep["migrations"]["ok"] is False
    assert rep["integrity_check"]["ok"] and rep["row_counts"] == {} and rep["schema_drift"] == []


def test_doctor_seed_and_status_never_create_a_database_file(path, tmp_path):
    for call in (lambda: db.doctor(path), lambda: db.status(path)):
        call()
        assert not os.path.exists(path)
    rep = db.doctor(path)
    assert rep["exists"] is False and rep["ok"] is False and any("does not exist" in w for w in rep["warnings"])
    with pytest.raises(FileNotFoundError):
        db.seed_reference_data(path)
    with pytest.raises(FileNotFoundError):
        db.analyze(path)
    assert db.migrate(path, dry_run=True)[0] == "0001_foundation.sql" and not os.path.exists(path)      # a dry run does not create it either
    assert db.main(["doctor", "--db", path]) == 1 and not os.path.exists(path)
    assert db.main(["seed", "--db", path]) == 2 and not os.path.exists(path)
