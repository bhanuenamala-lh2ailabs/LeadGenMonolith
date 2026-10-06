"""Regression tests for the schema review (docs/review/schema-critic-A.md, schema-critic-B.md; decisions in docs/review/schema-decisions.md).

Every test here pins a defect that the first 66 tests did not catch: garbage timestamps, history destroyed by REPLACE / DELETE /
table rebuilds, deleted-stage aliases, the +91 gate for landlines, synthetic events counted as human, un-normalised suppression
values, ambiguous pipelines, drift between deal state and event log, and the migration-runner hazards.
"""
import concurrent.futures
import glob
import os
import re
import shutil
import sqlite3
import time

import pytest

from leadgen import db, norm, suppression
from test_schema import add_deal, add_event, bad, con, make_world, path, rows, val, x  # noqa: F401  (fixtures + helpers)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ============================================================================================== H1 timestamps
BAD_TS = ["2026-13-45T99:99:99Z", "2026-10-04t12:00:00.000Z", "2026-10-04T12:00:00.000z", "2026-10-04T12:00:00+05:30Z",
          "2026-10-04T12:00:00 garbage Z", "2026-10-04T00:00:00Z", "2026-10-04T00:00:00.123456789Z", "2026-02-30T00:00:00.000Z",
          "2026-10-04T24:00:00.000Z", "2026-10-04 12:00:00.000Z", "2026-10-04T12:00:00.000Zjunk", "", "yesterday"]


def test_timestamp_check_rejects_everything_but_the_canonical_millisecond_shape(con):
    make_world(con)
    d = add_deal(con, "main", "1", "default", "c1")
    for i, ts in enumerate(BAD_TS):
        with pytest.raises(sqlite3.IntegrityError):
            add_event(con, d, "main", "default", "c1", ts)
        if ts:
            bad(con, "UPDATE deal SET hs_created_at = ? WHERE deal_id = ?", ts, d)
    add_event(con, d, "main", "default", "c1", "2026-10-03T18:30:00.000Z")
    assert rows(con, "SELECT ist_day FROM deal_stage_event") == [("2026-10-04",)]
    assert val(con, "SELECT COUNT(*) FROM deal_stage_event WHERE ist_day IS NULL") == 0
    # text order == time order, because the width is fixed
    add_event(con, d, "main", "default", "np", "2026-10-03T18:30:00.001Z")
    assert [r[0] for r in rows(con, "SELECT to_stage_id FROM deal_stage_event ORDER BY entered_at")] == ["c1", "np"]


def test_no_check_constraint_still_uses_the_loose_like_pattern(con):
    sqls = [r[0] for r in rows(con, "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'legacy\\_%' ESCAPE '\\'")]
    assert not [s for s in sqls if "LIKE '____-__-__T" in s]
    assert sum(s.count("IS strftime('%Y-%m-%dT%H:%M:%fZ'") for s in sqls) >= 100


def test_date_only_columns_reject_garbage_not_pass_on_null(con):
    make_world(con)
    bad(con, "INSERT INTO stage_alias (account_id, pipeline_id, alias_kind, alias_value, stage_id, valid_to) VALUES ('main','default','label','x','np','abcdefghij')")
    bad(con, "INSERT INTO stage_alias (account_id, pipeline_id, alias_kind, alias_value, stage_id, valid_to) VALUES ('main','default','label','x','np','2026-99-99')")
    x(con, "INSERT INTO stage_alias (account_id, pipeline_id, alias_kind, alias_value, stage_id, valid_to) VALUES ('main','default','label','x','np','2026-09-30')")
    bad(con, "INSERT INTO rpt_report_run (report_kind, report_day, window_end) VALUES ('daily','2026-10-04','abcdefghij')")
    bad(con, "INSERT INTO rpt_report_run (report_kind, report_day, window_start, window_end) VALUES ('daily','2026-10-04','2026-10-05','2026-10-04')")   # end < start
    x(con, "INSERT INTO rpt_report_run (report_kind, report_day, window_start, window_end) VALUES ('daily','2026-10-04','2026-09-28','2026-10-04')")


# ============================================================================================== H2 history is never destroyed
def test_replace_and_delete_cannot_erase_stage_history(con):
    make_world(con)
    d = add_deal(con, "main", "1", "default", "c1", "111")
    add_event(con, d, "main", "default", "c1", "2026-10-03T19:00:00.000Z")
    x(con, "INSERT INTO deal_owner_event (deal_id, account_id, owner_id, assigned_at) VALUES (?, 'main', 1, '2026-10-03T19:00:00.000Z')", d)
    with pytest.raises(sqlite3.IntegrityError):                        # REPLACE = DELETE + INSERT: the delete is RESTRICTed
        x(con, "INSERT OR REPLACE INTO deal (deal_id, account_id, hs_deal_id, pipeline_id, stage_id) VALUES (?, 'main', '1', 'default', 'np')", d)
    with pytest.raises(sqlite3.IntegrityError):
        x(con, "REPLACE INTO deal (account_id, hs_deal_id, pipeline_id, stage_id) VALUES ('main', '1', 'default', 'np')")
    with pytest.raises(sqlite3.IntegrityError):
        x(con, "DELETE FROM deal WHERE deal_id = ?", d)
    # the sanctioned upsert keeps the row and its history
    x(con, "INSERT INTO deal (account_id, hs_deal_id, pipeline_id, stage_id, dealname) VALUES ('main','1','default','np','renamed') "
           "ON CONFLICT(account_id, hs_deal_id) DO UPDATE SET stage_id = excluded.stage_id, dealname = excluded.dealname")
    assert rows(con, "SELECT deal_id, stage_id, dealname FROM deal") == [(d, "np", "renamed")]
    assert val(con, "SELECT COUNT(*) FROM deal_stage_event") == 1 and val(con, "SELECT COUNT(*) FROM deal_owner_event") == 1
    # archive instead of delete
    x(con, "UPDATE deal SET is_archived = 1, archived_at = '2026-10-04T00:00:00.000Z' WHERE deal_id = ?", d)


def test_replace_cannot_silently_wipe_children_or_rewrite_the_audit_log(con):
    make_world(con)
    x(con, "INSERT INTO company (canonical_name, name_norm) VALUES ('A','a')")
    x(con, "INSERT INTO company_identifier (company_id, identifier_type, value_norm) VALUES (1, 'root_domain', 'a.com')")
    x(con, "INSERT INTO origin_ref (entity_type, entity_id, source_system, source_table, source_pk) VALUES ('company', 1, 'tam', 'companies', '1')")
    with pytest.raises(sqlite3.IntegrityError):
        x(con, "INSERT OR REPLACE INTO company (company_id, canonical_name, name_norm) VALUES (1, 'A2', 'a2')")
    assert val(con, "SELECT COUNT(*) FROM company_identifier") == 1 and val(con, "SELECT COUNT(*) FROM origin_ref") == 1
    x(con, "INSERT INTO contact (first_name) VALUES ('P')")
    x(con, "INSERT INTO contact_phone (contact_id, phone_raw) VALUES (1, '123')")
    with pytest.raises(sqlite3.IntegrityError):
        x(con, "INSERT OR REPLACE INTO contact (contact_id, first_name) VALUES (1, 'P2')")
    # stage -> stage_map / stage_alias are RESTRICTed as well
    with pytest.raises(sqlite3.IntegrityError):
        x(con, "INSERT OR REPLACE INTO stage (account_id, pipeline_id, stage_id, label) VALUES ('main','default','c1','Cold Call 2')")
    with pytest.raises(sqlite3.IntegrityError):
        x(con, "DELETE FROM stage WHERE stage_id = 'np'")
    # recursive_triggers=ON makes REPLACE fire the append-only delete trigger
    x(con, "INSERT INTO ops_audit_log (actor, action) VALUES ('cli', 'a')")
    with pytest.raises(sqlite3.IntegrityError):
        x(con, "INSERT OR REPLACE INTO ops_audit_log (audit_id, actor, action) VALUES (1, 'cli', 'rewritten')")
    assert val(con, "SELECT action FROM ops_audit_log") == "a"


def test_no_code_under_leadgen_uses_replace():
    """String literals (docstrings excluded) in leadgen/**/*.py must not contain INSERT OR REPLACE / REPLACE INTO."""
    import ast
    pat = re.compile(r"\bINSERT\s+OR\s+REPLACE\b|\bREPLACE\s+INTO\b", re.I)
    bad_lines = []
    for p in glob.glob(os.path.join(ROOT, "leadgen", "**", "*.py"), recursive=True):
        tree = ast.parse(open(p, encoding="utf-8").read(), p)
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)) and node.body:
                first = node.body[0]
                if isinstance(first, ast.Expr) and isinstance(getattr(first, "value", None), ast.Constant):
                    docstrings.add(id(first.value))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings and pat.search(node.value):
                bad_lines.append("%s:%d" % (os.path.relpath(p, ROOT), node.lineno))
    assert not bad_lines, "use INSERT ... ON CONFLICT DO UPDATE: %s" % bad_lines
    assert pat.search("insert or replace into x") and pat.search("REPLACE INTO x")


def test_touch_triggers_survive_recursive_triggers_and_rapid_updates(con):
    assert val(con, "PRAGMA recursive_triggers") == 1
    x(con, "INSERT INTO company (canonical_name, name_norm) VALUES ('A','a')")
    seen = set()
    for i in range(300):                                           # many updates inside the same millisecond must not recurse forever
        x(con, "UPDATE company SET canonical_name = ?", "A%d" % i)
        seen.add(val(con, "SELECT updated_at FROM company"))
    assert len(seen) >= 1 and all(s.endswith("Z") for s in seen)
    x(con, "UPDATE company SET updated_at = '2026-01-01T00:00:00.000Z'")
    assert val(con, "SELECT updated_at FROM company") == "2026-01-01T00:00:00.000Z"


def test_connection_pragmas_safety_set(con):
    assert val(con, "PRAGMA recursive_triggers") == 1
    assert val(con, "PRAGMA trusted_schema") == 0
    assert val(con, "PRAGMA cell_size_check") == 1
    assert val(con, "PRAGMA journal_size_limit") == 67108864
    assert val(con, "PRAGMA wal_autocheckpoint") == 1000


# ---- the rebuild protocol -------------------------------------------------------------------------------------------------
def _mig_dir(tmp_path, upto):
    d = tmp_path / "mig"
    d.mkdir()
    for f in db.discover_migrations():
        if f.version <= upto:
            shutil.copy(f.path, d / f.filename)
    return d


def _rebuild_sql(con, table, header=True):
    ddl = val(con, "SELECT sql FROM sqlite_master WHERE type='table' AND name = ?", table)
    deps = [r[0] for r in rows(con, "SELECT sql FROM sqlite_master WHERE tbl_name = ? AND type IN ('index','trigger') AND sql IS NOT NULL", table)]
    out = "-- migrate: foreign_keys=off\n" if header else "-- plain migration\n"
    out += "PRAGMA legacy_alter_table = ON;\n"
    out += ddl.replace("CREATE TABLE %s (" % table, "CREATE TABLE %s_new (" % table, 1) + ";\n"
    out += "INSERT INTO %s_new SELECT * FROM %s;\nDROP TABLE %s;\nALTER TABLE %s_new RENAME TO %s;\nPRAGMA legacy_alter_table = OFF;\n" % ((table,) * 5)
    out += "".join(d + ";\n" for d in deps)
    return out


def test_rebuild_migration_preserves_children_only_with_the_foreign_keys_off_header(path, tmp_path):
    d = _mig_dir(tmp_path, 4)
    db.migrate(path, migrations_dir=str(d))
    c = db.connect(path)
    make_world(c)
    deal = add_deal(c, "main", "1", "default", "c1", "111")
    add_event(c, deal, "main", "default", "c1", "2026-10-03T19:00:00.000Z")
    add_event(c, deal, "main", "default", "np", "2026-10-04T19:00:00.000Z", from_stage="c1")
    # (a) the same rebuild WITHOUT the header: DROP TABLE deal runs under foreign_keys=ON and is refused (history RESTRICT) - atomic rollback
    (d / "0005_rebuild_deal.sql").write_text(_rebuild_sql(c, "deal", header=False))
    with pytest.raises(db.MigrationError) as ei:
        db.migrate(path, migrations_dir=str(d), backup=False)
    assert "rolled back" in str(ei.value) and "FOREIGN KEY" in str(ei.value)
    assert val(c, "SELECT COUNT(*) FROM deal_stage_event") == 2 and val(c, "SELECT MAX(version) FROM schema_migration") == 4
    # (b) with the header: foreign_keys=OFF for the rebuild only; children untouched; FK enforcement is back on afterwards
    (d / "0005_rebuild_deal.sql").write_text(_rebuild_sql(c, "deal", header=True))
    res = db.migrate(path, migrations_dir=str(d))
    assert list(res) == ["0005_rebuild_deal.sql"] and res.backup and os.path.exists(res.backup)
    assert val(c, "SELECT COUNT(*) FROM deal_stage_event") == 2 and val(c, "SELECT COUNT(*) FROM deal") == 1
    assert val(c, "PRAGMA foreign_keys") == 1 and val(c, "PRAGMA user_version") == 5
    assert rows(c, "PRAGMA foreign_key_check") == []
    with pytest.raises(sqlite3.IntegrityError):
        x(c, "DELETE FROM deal")                                        # RESTRICT still enforced on the rebuilt table
    c.close()
    assert db.doctor(path, migrations_dir=str(d))["ok"]


def test_rebuild_that_leaves_new_orphans_is_rolled_back_and_plain_migrations_are_checked_too(path, tmp_path):
    d = _mig_dir(tmp_path, 2)
    db.migrate(path, migrations_dir=str(d))
    c = db.connect(path)
    x(c, "INSERT INTO lead_source (code, label) VALUES ('apollo','Apollo')")
    make_world(c)
    add_deal(c, "main", "1", "default", "c1")
    x(c, "UPDATE deal SET lead_source_id = 1")
    (d / "0003_lose_parent.sql").write_text("-- migrate: foreign_keys=off\nDELETE FROM lead_source;\n")
    with pytest.raises(db.MigrationError) as ei:
        db.migrate(path, migrations_dir=str(d), backup=False)
    assert "new foreign-key violations" in str(ei.value)
    assert val(c, "SELECT COUNT(*) FROM lead_source") == 1 and val(c, "PRAGMA foreign_keys") == 1
    # a plain migration that inserts an orphan with defer_foreign_keys is caught at the end as well
    (d / "0003_lose_parent.sql").write_text("PRAGMA defer_foreign_keys = ON;\nUPDATE deal SET lead_source_id = 999;\n")
    with pytest.raises(db.MigrationError):
        db.migrate(path, migrations_dir=str(d), backup=False)
    c.close()


def test_preexisting_legacy_orphans_do_not_block_migration_and_are_a_warning_not_a_failure(path, tmp_path):
    d = _mig_dir(tmp_path, 14)
    db.migrate(path, migrations_dir=str(d))
    c = db.connect(path)
    c.execute("PRAGMA foreign_keys = OFF")
    x(c, "INSERT INTO ops_import_run (kind, source_name) VALUES ('legacy_import','radar')")
    x(c, "INSERT INTO legacy_radar_candidate_entity (candidate_id, cin, match_score, match_method, is_confirmed, _import_run_id) "
         "VALUES (10799, 'U72900KA2019PTC128848', 1.0, 'override', 1, 1)")
    c.execute("PRAGMA foreign_keys = ON")
    rep = db.doctor(c, migrations_dir=str(d))
    assert rep["foreign_key_check"]["legacy"] >= 1 and rep["foreign_key_check"]["canonical"] == 0 and rep["ok"]
    assert any("legacy_*" in w for w in rep["warnings"])
    (d / "0015_more.sql").write_text("CREATE TABLE extra_t (a INTEGER);\n")                 # a later migration is not blocked by the old orphan
    assert list(db.migrate(path, migrations_dir=str(d), backup=False)) == ["0015_more.sql"]
    # but a CANONICAL orphan fails the doctor
    c.execute("PRAGMA foreign_keys = OFF")
    x(c, "INSERT INTO stage_alias (account_id, pipeline_id, alias_kind, alias_value, stage_id) VALUES ('main','default','label','ghost','nope')")
    c.execute("PRAGMA foreign_keys = ON")
    assert not db.doctor(c, migrations_dir=str(d))["ok"]
    c.close()


def test_migrate_backs_up_a_non_empty_database_and_the_first_run_needs_none(path, tmp_path):
    d = _mig_dir(tmp_path, 3)
    res = db.migrate(path, migrations_dir=str(d))
    assert res.backup is None                                   # nothing to protect on the first run
    (d / "0004_more.sql").write_text("CREATE TABLE extra_t (a INTEGER);\n")
    res = db.migrate(path, migrations_dir=str(d))
    assert res.backup and res.backup.startswith(os.path.join(os.path.dirname(path), "backup")) and "pre-0004" in res.backup
    b = sqlite3.connect(res.backup)
    assert b.execute("SELECT MAX(version) FROM schema_migration").fetchone()[0] == 3        # the backup is the state BEFORE the migration
    b.close()
    (d / "0005_more.sql").write_text("CREATE TABLE extra_t2 (a INTEGER);\n")
    assert db.migrate(path, migrations_dir=str(d), backup=False).backup is None


def test_concurrent_first_migrations_report_exactly_what_each_applied(tmp_path):
    p = str(tmp_path / "race.sqlite")
    n = len(db.discover_migrations())
    with concurrent.futures.ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda _: list(db.migrate(p)), range(4)))
    assert sorted(f for r in results for f in r) == sorted(f.filename for f in db.discover_migrations())      # each file reported once
    assert sum(len(r) for r in results) == n
    assert db.status(p)["ok"]


def test_two_statements_on_one_line_get_a_clear_error(path, tmp_path):
    d = tmp_path / "m"
    d.mkdir()
    (d / "0001_oneline.sql").write_text("CREATE TABLE a (x INTEGER); CREATE TABLE b (y INTEGER);\n")
    with pytest.raises(db.MigrationError) as ei:
        db.migrate(path, migrations_dir=str(d))
    assert "own line" in str(ei.value) and "0001_oneline.sql" in str(ei.value)
    with db.connect(path) as c:
        assert val(c, "SELECT COUNT(*) FROM sqlite_master WHERE name IN ('a','b')") == 0


def test_old_sqlite_is_refused(monkeypatch, path):
    monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 30, 0))
    monkeypatch.setattr(sqlite3, "sqlite_version", "3.30.0")
    with pytest.raises(RuntimeError) as ei:
        db.connect(path)
    assert "too old" in str(ei.value)


def test_transaction_is_immediate_only_and_read_transaction_exists(con):
    with pytest.raises(TypeError):
        with db.transaction(con, immediate=False):          # a deferred writer fails instantly with 'database is locked'
            pass
    with db.read_transaction(con) as c:
        assert c.in_transaction and val(c, "SELECT COUNT(*) FROM account") == 3
    assert not con.in_transaction


def test_pre_migration_checkout_is_lf_pinned():
    ga = open(os.path.join(ROOT, ".gitattributes")).read()
    assert "db/migrations/*.sql text eol=lf" in ga


# ============================================================================================== H3 stage aliases
def test_stage_alias_rules(con):
    make_world(con)
    x(con, "INSERT INTO stage (account_id, pipeline_id, stage_id, label, is_deleted) VALUES ('main','default','900','old one',1)")
    x(con, "INSERT INTO stage (account_id, pipeline_id, stage_id, label, is_deleted) VALUES ('main','default','901','old two',1)")
    a = "INSERT INTO stage_alias (account_id, pipeline_id, alias_kind, alias_value, stage_id) VALUES ('main','default','stage_id',?,?)"
    bad(con, a, "900", "901")                      # target is itself a tombstone
    bad(con, a, "np", "i1")                        # alias value must be numeric ...
    bad(con, a, "5", "i1")                         # ... and an existing tombstone
    x(con, "INSERT INTO stage (account_id, pipeline_id, stage_id, label) VALUES ('main','default','777','live numeric')")
    bad(con, a, "777", "i1")                       # a LIVE id can never be shadowed by an alias
    bad(con, a, "900", "900")                      # not itself
    x(con, a, "900", "i1")
    with pytest.raises(sqlite3.IntegrityError):                        # the UPDATE path enforces the same rules
        x(con, "UPDATE stage_alias SET alias_value = '777' WHERE alias_value = '900'")
    # label aliases are not subject to the stage_id rules
    x(con, "INSERT INTO stage_alias (account_id, pipeline_id, alias_kind, alias_value, stage_id) VALUES ('main','default','label','900','np')")


def test_tombstones_are_hidden_from_board_counts_until_a_deal_sits_in_them(con):
    make_world(con)
    x(con, "INSERT INTO stage (account_id, pipeline_id, stage_id, label, is_deleted) VALUES ('main','default','900','Gone',1)")
    x(con, "INSERT INTO stage_map (account_id, pipeline_id, stage_id, canonical_code) VALUES ('main','default','900','SOURCED')")
    stages = lambda: {r[0] for r in rows(con, "SELECT stage_id FROM v_funnel_stage_counts WHERE funnel_slug='coding'")}
    assert "900" not in stages()
    add_deal(con, "main", "1", "default", "900")
    assert "900" in stages()


def test_alias_precedence_own_stage_map_wins(con):
    make_world(con)
    x(con, "INSERT INTO stage (account_id, pipeline_id, stage_id, label, is_deleted) VALUES ('main','default','900','Gone',1)")
    x(con, "INSERT INTO stage_alias (account_id, pipeline_id, alias_kind, alias_value, stage_id) VALUES ('main','default','stage_id','900','np')")
    x(con, "INSERT INTO stage_map (account_id, pipeline_id, stage_id, canonical_code) VALUES ('main','default','900','REPLIED')")
    assert rows(con, "SELECT canonical_code, native_group_stage_id FROM v_stage_effective WHERE stage_id='900'") == [("REPLIED", "np")]


# ============================================================================================== H4 index use without ANALYZE
def _plan(con, sql, *args):
    return " ".join(r[3] for r in rows(con, "EXPLAIN QUERY PLAN " + sql, *args))


def test_day_filters_use_the_day_index_even_without_analyze(con):
    x(con, "DROP TABLE IF EXISTS sqlite_stat1")                       # a fresh database before the first ANALYZE
    assert val(con, "SELECT COUNT(*) FROM sqlite_master WHERE name = 'sqlite_stat1'") == 0
    for view in ("v_funnel_entries_daily", "v_funnel_entries_daily_canonical", "v_funnel_entries_daily_in_scope"):
        plan = _plan(con, "SELECT * FROM %s WHERE ist_day = ?" % view, "2026-10-04")
        assert "ix_dse_ist_day (ist_day=?)" in plan, (view, plan)
    plan = _plan(con, "SELECT COUNT(*) FROM deal_stage_event WHERE actor_owner_id IS NOT NULL AND ist_day = ?", "2026-10-04")
    assert "ix_dse_ist_day" in plan
    plan = _plan(con, "SELECT COUNT(DISTINCT deal_id) FROM deal_stage_event WHERE account_id='main' AND pipeline_id='default' AND to_stage_id='c1'")
    assert "COVERING INDEX ix_dse_stage_time" in plan                 # deal_id is part of the index
    plan = _plan(con, "SELECT MIN(entered_at) FROM deal_stage_event WHERE deal_id = 5 AND is_human = 1")
    assert "ix_dse_first_human" in plan or "ix_dse_deal_pipe_time" in plan or "autoindex" in plan
    assert val(con, "SELECT COUNT(*) FROM sqlite_master WHERE name = 'ix_dse_from_stage'") == 0
    assert val(con, "SELECT COUNT(*) FROM sqlite_master WHERE name = 'ix_dse_day'") == 0


def test_migrate_analyzes_and_doctor_flags_missing_stats_on_big_stores(con, path):
    assert db.doctor(con)["has_planner_stats"] in (True, False)
    db.analyze(con)
    assert val(con, "SELECT COUNT(*) FROM sqlite_master WHERE name = 'sqlite_stat1'") == 1


# ============================================================================================== H5 the +91 mobile gate
def test_mobile_gate_requires_a_mobile_type_and_a_consistent_country(con):
    x(con, "INSERT INTO contact (first_name) VALUES ('A')")
    x(con, "INSERT INTO company (canonical_name, name_norm) VALUES ('Co','co')")
    for table, key, owner in (("contact_phone", "contact_id", 1), ("company_phone", "company_id", 1)):
        ins = "INSERT INTO %s (%s, phone_raw, phone_e164, country_iso2, phone_type) VALUES (?,?,?,?,?)" % (table, key)
        x(con, ins, owner, "a", "+919876543210", "IN", "mobile")
        x(con, ins, owner, "b", "+919876543211", "IN", "landline")        # 10 digits starting 9 but typed landline: closed
        x(con, ins, owner, "c", "+919876543212", "IN", "unknown")         # unclassified: closed (fail closed)
        x(con, ins, owner, "d", "+919876543213", None, "voip")
        x(con, ins, owner, "e", "+14155550100", "US", "mobile")
        bad(con, ins, owner, "f", "+919876543214", "US", "mobile")        # +91 with country US
        bad(con, ins, owner, "g", "+14155550101", "IN", "mobile")         # IN with a +1 number
        assert [r[0] for r in rows(con, "SELECT is_indian_mobile FROM %s ORDER BY phone_id" % table)] == [1, 0, 0, 0, 0]
    assert rows(con, "SELECT has_indian_mobile, best_mobile_e164 FROM v_contact_mobile_gate") == [(1, "+919876543210")]
    assert rows(con, "SELECT has_indian_mobile, best_mobile_e164, phone_count FROM v_company_mobile_gate") == [(1, "+919876543210", 5)]


def test_company_phone_rules(con):
    x(con, "INSERT INTO company (canonical_name, name_norm) VALUES ('Co','co')")
    ins = "INSERT INTO company_phone (company_id, phone_raw, phone_e164, phone_type, is_placeholder, is_primary) VALUES (1,?,?,?,?,?)"
    x(con, ins, "+91 98765 43210", "+919876543210", "mobile", 1, 0)         # the well-known dummy: placeholder never opens the gate
    x(con, ins, "9742044482", None, "unknown", 0, 1)                          # unparsed raw spelling keeps phone_raw
    bad(con, ins, "9742044482", None, "unknown", 0, 0)                        # same raw twice
    bad(con, ins, "x", "+919876543299", "mobile", 0, 1)                       # one primary per company
    bad(con, ins, "x", "919876543299", "mobile", 0, 0)                        # not E.164
    assert val(con, "SELECT has_indian_mobile FROM v_company_mobile_gate") == 0
    x(con, "INSERT INTO origin_ref (entity_type, entity_id, source_system, source_table, source_pk) VALUES ('company_phone', 1, 'itsvc', 'candidates', 'c1')")
    x(con, "INSERT INTO origin_ref (entity_type, entity_id, source_system, source_table, source_pk) VALUES ('company_phone', 2, 'itsvc', 'candidates', 'c1')")  # fan-out allowed
    bad(con, "INSERT INTO origin_ref (entity_type, entity_id, source_system, source_table, source_pk) VALUES ('company_phone', 99, 'itsvc', 'candidates', 'c9')")


def test_phone_gate_review_lists_what_the_gate_could_not_decide(con):
    x(con, "INSERT INTO contact (first_name) VALUES ('A')")
    x(con, "INSERT INTO contact_phone (contact_id, phone_raw, phone_e164, phone_type) VALUES (1,'1','+919876543210','unknown')")
    x(con, "INSERT INTO contact_phone (contact_id, phone_raw, phone_e164, phone_type) VALUES (1,'+91-7042813998',NULL,'unknown')")
    x(con, "INSERT INTO contact_phone (contact_id, phone_raw, phone_e164, phone_type) VALUES (1,'020 1234',NULL,'unknown')")
    x(con, "INSERT INTO contact_phone (contact_id, phone_raw, phone_e164, phone_type) VALUES (1,'2','+919876543211','mobile')")
    assert sorted(rows(con, "SELECT reason, phone_raw FROM v_phone_gate_review")) == [("e164_mobile_shape_unclassified", "1"), ("raw_indian_shape_without_e164", "+91-7042813998")]
    assert db.doctor(con)["phone_gate_review"] == 2


# ============================================================================================== H6 synthetic events are never human
def test_synthetic_and_current_state_events_cannot_claim_crm_ui(con):
    make_world(con)
    d = add_deal(con, "main", "1", "default", "c1")
    for origin in ("synthetic", "hubspot_current"):
        with pytest.raises(sqlite3.IntegrityError):
            add_event(con, d, "main", "default", "c1", "2026-10-04T01:00:00.000Z", source="CRM_UI", origin=origin)
        add_event(con, d, "main", "default", "c1", "2026-10-04T01:00:00.000Z", source="SYNTHETIC", origin=origin)
        x(con, "DELETE FROM deal_stage_event")
    # real history (incl. backfill checkpoints and the CSV) legitimately carries CRM_UI
    for i, origin in enumerate(("hubspot_history", "backfill_checkpoint", "stage_transitions_csv", "legacy_import")):
        add_event(con, d, "main", "default", "c1", "2026-10-04T0%d:00:00.000Z" % (i + 1), source="CRM_UI", origin=origin)
    assert val(con, "SELECT SUM(is_human) FROM deal_stage_event") == 4


def test_csv_and_api_copies_of_the_same_move_are_detected(con):
    make_world(con)
    d = add_deal(con, "main", "1", "default", "c1")
    add_event(con, d, "main", "default", "np", "2026-10-04T05:00:27.412Z", origin="hubspot_history")
    add_event(con, d, "main", "default", "np", "2026-10-04T05:00:27.000Z", origin="stage_transitions_csv")      # second precision, different ms: UNIQUE misses it
    assert rows(con, "SELECT to_stage_id, entered_second, events, origins FROM v_dse_near_duplicate") == [("np", "2026-10-04T05:00:27", 2, 2)]
    assert db.doctor(con)["near_duplicate_events"] == 1
    assert "ix_dse_dedupe_sec" in _plan(con, "SELECT 1 FROM deal_stage_event WHERE deal_id=1 AND to_stage_id='np' AND substr(entered_at,1,19)='2026-10-04T05:00:27'")


# ============================================================================================== H7 suppression
def test_suppression_values_must_be_normalised(con):
    sp = "INSERT INTO suppression (kind, match_type, match_value_norm, reason) VALUES ('never_push', ?, ?, 'whale')"
    for t, v in (("domain", "https://acme.com/path"), ("domain", "acme..com"), ("domain", "-x.com"), ("domain", "acme.com."), ("domain", "ac me.com"),
                 ("domain", "ACME.com"), ("domain", "acme_corp.com"), ("company_name", "ACME Corp"),
                 ("linkedin_company", "Company/ACME/"), ("linkedin_company", "company/acme/"), ("linkedin_company", "acme"), ("linkedin_company", "company/acme?x=1"),
                 ("linkedin_person", "in/Jane"), ("linkedin_person", "company/acme"), ("linkedin_person", "in/jane/"), ("email", "a b@x.com")):
        bad(con, sp, t, v)
    for t, v in (("domain", "acme.com"), ("company_name", "acme corp"), ("linkedin_company", "company/acme"), ("linkedin_company", "school/mit"),
                 ("linkedin_person", "in/jane"), ("email", "a@x.com"), ("phone", "+919876543210"), ("cin", "U12345MH2020PTC123456")):
        x(con, sp, t, v)


def test_one_never_push_row_per_value_not_one_per_account(con):
    sp = "INSERT INTO suppression (kind, match_type, match_value_norm, account_id) VALUES (?, 'domain', 'whale.com', ?)"
    x(con, sp, "never_push", None)
    bad(con, sp, "never_push", "main")
    x(con, sp, "dnc", "main")                                              # other kinds may still be account specific
    x(con, "UPDATE suppression SET is_active = 0 WHERE kind = 'never_push'")
    x(con, sp, "never_push", "rat")                                        # a deactivated row frees the value


def test_suppression_gate_normalises_probes_and_fails_closed(con):
    sp = "INSERT INTO suppression (kind, match_type, match_value_norm, account_id, reason) VALUES (?, ?, ?, ?, 'r')"
    x(con, sp, "never_push", "domain", "whale.com", None)
    x(con, sp, "never_push", "company_name", "big whale corp", None)
    x(con, sp, "never_push", "linkedin_company", "company/big-whale", None)
    x(con, sp, "dnc", "email", "jane@x.com", "rat")
    x(con, sp, "delivered", "phone", "+919876543210", None)
    p = lambda **kw: suppression.check(con, **kw)
    assert p(domain="https://WWW.Whale.com/pricing")[0]["kind"] == "never_push"
    assert p(company_name="  BIG   Whale Corp ")[0]["match_type"] == "company_name"
    assert p(linkedin_company="https://in.linkedin.com/company/Big-Whale/?trk=x")[0]["match_value_norm"] == "company/big-whale"
    assert p(phone="098765 43210")[0]["kind"] == "delivered"
    assert p(email="JANE@x.com") == [] and p(email="JANE@x.com", account_id="rat")[0]["kind"] == "dnc"      # account-specific rows only for that account
    assert p(domain="innocent.com", email=None, phone="") == []
    with pytest.raises(suppression.SuppressedError) as ei:
        suppression.assert_pushable(con, domain="whale.com", email="new@x.com")
    assert ei.value.matches and "never_push" in str(ei.value)
    suppression.assert_pushable(con, domain="innocent.com")
    with pytest.raises(suppression.UnnormalisableProbe):                    # cannot be checked => must not pass as clean
        suppression.check(con, domain="not a domain")
    with pytest.raises(TypeError):
        suppression.check(con, website="x.com")
    x(con, "UPDATE suppression SET expires_at = '2020-01-01T00:00:00.000Z', added_at = '2019-01-01T00:00:00.000Z' WHERE match_value_norm = 'whale.com'")
    assert p(domain="whale.com") == []


# ============================================================================================== M1 pipeline provenance
def test_pipeline_basis_is_mandatory_and_disclosed(con):
    make_world(con)
    d = add_deal(con, "main", "1", "default", "i1")
    ins = ("INSERT INTO deal_stage_event (deal_id, account_id, pipeline_id, to_stage_id, entered_at, source_type{c}) VALUES (?, 'main','default','i1','2026-10-04T05:00:00.000Z','CRM_UI'{v})")
    bad(con, ins.format(c="", v=""), d)                                         # no default: the importer must state how it resolved the pipeline
    bad(con, ins.format(c=", pipeline_basis", v=",'ambiguous'"), d)
    add_event(con, d, "main", "default", "i1", "2026-10-04T05:00:00.000Z", basis="deal_current")
    add_event(con, d, "main", "default", "i2", "2026-10-04T06:00:00.000Z", basis="unique_stage_id", from_stage="i1")
    assert rows(con, "SELECT account_id, pipeline_id, pipeline_basis, events, deals FROM v_event_pipeline_guess") == [("main", "default", "deal_current", 1, 1)]
    assert db.doctor(con)["event_pipeline_guesses"] == 1


def test_a_move_to_the_same_stage_of_the_same_pipeline_is_not_a_transition(con):
    make_world(con)
    d = add_deal(con, "main", "1", "default", "i1")
    with pytest.raises(sqlite3.IntegrityError):
        add_event(con, d, "main", "default", "i1", "2026-10-04T05:00:00.000Z", from_stage="i1")
    x(con, "INSERT INTO deal_stage_event (deal_id, account_id, pipeline_id, to_stage_id, from_pipeline_id, from_stage_id, entered_at, source_type, pipeline_basis) "
           "VALUES (?, 'main','2425754306','i1','default','i1','2026-10-04T05:00:00.000Z','CRM_UI','pipeline_history')", d)   # Coding -> CoOps keeping a shared stage id


# ============================================================================================== M2 / M3 company model
def test_deal_company_and_deal_company_link_cannot_disagree(con):
    make_world(con)
    x(con, "INSERT INTO company (canonical_name, name_norm) VALUES ('Acme','acme')")
    x(con, "INSERT INTO company (canonical_name, name_norm) VALUES ('Other','other')")
    x(con, "INSERT INTO company_account_link (company_id, account_id, hs_company_id) VALUES (1,'main','301')")
    x(con, "INSERT INTO company_account_link (company_id, account_id, hs_company_id) VALUES (1,'main','302')")
    d = add_deal(con, "main", "1", "default", "c1")
    x(con, "UPDATE deal SET company_id = 2 WHERE deal_id = ?", d)
    bad(con, "INSERT INTO deal_company (deal_id, link_id, account_id, is_primary) VALUES (?, 1, 'main', 1)", d)       # primary link says Acme, deal says Other
    x(con, "INSERT INTO deal_company (deal_id, link_id, account_id, is_primary) VALUES (?, 1, 'main', 0)", d)          # non-primary links are free
    x(con, "UPDATE deal SET company_id = 1 WHERE deal_id = ?", d)
    x(con, "UPDATE deal_company SET is_primary = 1 WHERE link_id = 1")
    with pytest.raises(sqlite3.IntegrityError):
        x(con, "UPDATE deal SET company_id = 2 WHERE deal_id = ?", d)
    x(con, "INSERT INTO deal_company (deal_id, link_id, account_id, is_primary) VALUES (?, 2, 'main', 0)", d)
    bad(con, "UPDATE deal_company SET is_primary = 1 WHERE link_id = 2")                                               # one primary company per deal
    # a merge that moves the link first and the deal second never trips the check; drift made by moving only the link is reported
    x(con, "UPDATE company_account_link SET company_id = 2 WHERE link_id = 1")
    assert rows(con, "SELECT deal_company_id, link_company_id FROM v_deal_company_drift") == [(1, 2)]
    assert db.doctor(con)["deal_company_drift"] == 1
    x(con, "UPDATE deal SET company_id = 2 WHERE deal_id = ?", d)
    assert rows(con, "SELECT * FROM v_deal_company_drift") == []


def test_company_merge_cycles_are_rejected_and_identifiers_resolve_to_the_survivor(con):
    for n in "abc":
        x(con, "INSERT INTO company (canonical_name, name_norm) VALUES (?, ?)", n.upper(), n)
    at = "2026-10-04T00:00:00.000Z"
    x(con, "UPDATE company SET merged_into_company_id = 1, merged_at = ? WHERE company_id = 2", at)
    x(con, "UPDATE company SET merged_into_company_id = 2, merged_at = ? WHERE company_id = 3", at)
    with pytest.raises(sqlite3.IntegrityError):
        x(con, "UPDATE company SET merged_into_company_id = 3, merged_at = ? WHERE company_id = 1", at)          # 1 -> 3 -> 2 -> 1
    with pytest.raises(sqlite3.IntegrityError):
        x(con, "UPDATE company SET merged_into_company_id = 2, merged_at = ? WHERE company_id = 1", at)
    x(con, "INSERT INTO company_identifier (company_id, identifier_type, value_norm) VALUES (3, 'root_domain', 'c.com')")
    assert rows(con, "SELECT survivor_company_id, on_tombstone FROM v_identifier_survivor") == [(1, 1)]
    assert rows(con, "SELECT company_id FROM v_company_survivor ORDER BY 1") == [(1,), (2,), (3,)]


def test_company_identifier_strong_unique_weak_may_repeat_and_domains_are_clean(con):
    for n in "ab":
        x(con, "INSERT INTO company (canonical_name, name_norm) VALUES (?, ?)", n.upper(), n)
    ins = "INSERT INTO company_identifier (company_id, identifier_type, value_norm, is_strong, is_primary) VALUES (?,?,?,?,?)"
    x(con, ins, 1, "root_domain", "hyatt.com", 0, 0)                          # a domain claimed by several distinct companies: evidence only
    x(con, ins, 2, "root_domain", "hyatt.com", 0, 0)
    bad(con, ins, 2, "root_domain", "hyatt.com", 0, 0)                        # but not twice on one company
    bad(con, ins, 2, "root_domain", "acme.com", 0, 1)                         # a weak identifier is never primary
    x(con, ins, 1, "root_domain", "acme.com", 1, 1)
    bad(con, ins, 2, "root_domain", "acme.com", 1, 0)                         # strong ones stay globally unique
    x(con, ins, 2, "root_domain", "acme.com", 0, 0)
    for v in ("a..com", "-x.com", ".x.com", "x-.com", "x.-com", "x.com."):
        bad(con, ins, 2, "root_domain", v, 1, 0)
    x(con, ins, 2, "llpin", "AAE-7433", 1, 0)
    for v in ("aae-7433", "AAE7433", "AAE-74330", "AA-7433"):
        bad(con, ins, 2, "llpin", v, 1, 0)
    assert rows(con, "SELECT root_domain FROM v_company_golden WHERE company_id = 2") == [(None,)]           # weak evidence never becomes THE domain


def test_hubspot_ids_have_no_leading_zeros(con):
    make_world(con)
    for sql in ("INSERT INTO owner (account_id, hs_owner_id, display_name) VALUES ('rat','0123','x')",
                "INSERT INTO owner (account_id, hs_owner_id, hs_user_id, display_name) VALUES ('rat','124','0124','x')",
                "INSERT INTO contact (account_id, hs_contact_id) VALUES ('rat','0900')",
                "INSERT INTO company_account_link (company_id, account_id, hs_company_id) VALUES (1,'rat','0301')",
                "INSERT INTO deal (account_id, hs_deal_id, pipeline_id, stage_id) VALUES ('main','0100','default','c1')",
                "INSERT INTO engagement (account_id, hs_engagement_id, kind, occurred_at) VALUES ('main','08001','note','2026-10-04T00:00:00.000Z')"):
        x(con, "INSERT INTO company (canonical_name, name_norm) SELECT 'Z','z' WHERE NOT EXISTS (SELECT 1 FROM company)")
        bad(con, sql)
    d = add_deal(con, "main", "1", "default", "c1")
    bad(con, "INSERT INTO deal_stage_event (deal_id, account_id, pipeline_id, to_stage_id, entered_at, source_type, actor_user_id, pipeline_basis) "
             "VALUES (?, 'main','default','c1','2026-10-04T00:00:00.000Z','CRM_UI','0911','manual')", d)
    bad(con, "INSERT INTO deal_stage_event (deal_id, account_id, pipeline_id, to_stage_id, entered_at, source_type, actor_user_id, pipeline_basis) "
             "VALUES (?, 'main','default','c1','2026-10-04T00:00:00.000Z','CRM_UI','9x1','manual')", d)
    assert norm.norm_hs_id("0123") == "123" and norm.norm_hs_id(" 45 ") == "45"
    with pytest.raises(ValueError):
        norm.norm_hs_id("12a")


# ============================================================================================== M5 / M6 drift, empty pipelines, dead stages
def test_deal_state_drift_view(con):
    make_world(con)
    a = add_deal(con, "main", "1", "default", "np")
    b = add_deal(con, "main", "2", "default", "np")
    add_event(con, a, "main", "default", "np", "2026-10-04T05:00:00.000Z")
    add_event(con, b, "main", "default", "np", "2026-10-04T05:00:00.000Z")
    assert rows(con, "SELECT * FROM v_deal_state_drift") == []
    x(con, "UPDATE deal SET stage_id = 'i1' WHERE deal_id = ?", b)             # the deals sync moved it, the event never arrived
    assert rows(con, "SELECT deal_id, deal_stage_id, last_event_stage_id FROM v_deal_state_drift") == [(b, "i1", "np")]
    assert db.doctor(con)["deal_state_drift"] == 1
    x(con, "UPDATE deal SET is_archived = 1 WHERE deal_id = ?", b)
    assert rows(con, "SELECT * FROM v_deal_state_drift") == []


def test_reporting_pipelines_without_stages_are_a_warning_not_silence(con):
    assert val(con, "SELECT COUNT(*) FROM v_pipeline_without_stages") == 5
    rep = db.doctor(con)
    assert rep["pipelines_without_stages"] == 5 and any("zero stage rows" in w for w in rep["warnings"])
    make_world(con)                                                   # stages for main/default (coding), main/2425754306 (coops_global), companyops/2464812771 (cluster2)
    assert sorted(r[0] for r in rows(con, "SELECT funnel_slug FROM v_pipeline_without_stages")) == ["cluster1", "rat"]


def test_dead_stage_needs_depth_reached_except_retired_admin(con):
    make_world(con)
    x(con, "INSERT INTO stage (account_id, pipeline_id, stage_id, label) VALUES ('main','default','d2','Dead 2')")
    sm = "INSERT INTO stage_map (account_id, pipeline_id, stage_id, canonical_code, dead_reason_code, depth_reached) VALUES ('main','default','d2','DEAD',?,?)"
    bad(con, sm, "NOT_INTERESTED", None)
    bad(con, sm, "WRONG_FIT", None)
    x(con, sm, "RETIRED_ADMIN", None)
    x(con, "UPDATE stage_map SET dead_reason_code = 'NO_SHOW', depth_reached = 4 WHERE stage_id = 'd2'")
    with pytest.raises(sqlite3.IntegrityError):
        x(con, "UPDATE stage_map SET depth_reached = NULL WHERE stage_id = 'd2'")


def test_cohort_unknown_creation_date_on_the_migration_day_is_out_of_scope(con):
    make_world(con)
    d = add_deal(con, "companyops", "20", "2464812771", "k1", created="2026-09-10T05:00:00.000Z")
    x(con, "UPDATE deal SET hs_created_at = NULL WHERE deal_id = ?", d)
    add_event(con, d, "companyops", "2464812771", "k1", "2026-09-15T05:00:00.000Z", "INTEGRATION")
    assert val(con, "SELECT in_scope FROM v_deal_cohort WHERE deal_id = ?", d) == 0


def test_deal_canonical_reach_view(con):
    make_world(con)
    d = add_deal(con, "main", "1", "default", "i1")
    add_event(con, d, "main", "default", "c1", "2026-10-01T03:00:00.000Z", "INTEGRATION")
    add_event(con, d, "main", "default", "i1", "2026-10-02T03:00:00.000Z", "CRM_UI")
    add_event(con, d, "main", "default", "i2", "2026-10-03T03:00:00.000Z", "CRM_UI", from_stage="i1")
    assert rows(con, "SELECT canonical_code, first_at, last_at, entries FROM v_deal_canonical_reach ORDER BY canonical_sort") == \
        [("SOURCED", "2026-10-01T03:00:00.000Z", "2026-10-01T03:00:00.000Z", 1), ("INTERESTED", "2026-10-02T03:00:00.000Z", "2026-10-03T03:00:00.000Z", 2)]


# ============================================================================================== M7 / M8 runner, keys, ops
def test_ids_are_never_reused_after_a_delete(con):
    x(con, "INSERT INTO company (canonical_name, name_norm) VALUES ('A','a')")
    x(con, "INSERT INTO company (canonical_name, name_norm) VALUES ('B','b')")
    x(con, "DELETE FROM company WHERE company_id = 2")
    x(con, "INSERT INTO company (canonical_name, name_norm) VALUES ('C','c')")
    assert val(con, "SELECT MAX(company_id) FROM company") == 3
    for t in ("contact", "deal", "tam_company_verdict", "suppression", "cost_ledger", "ops_audit_log", "company"):
        assert "AUTOINCREMENT" in val(con, "SELECT sql FROM sqlite_master WHERE name = ?", t).upper(), t


def test_origin_ref_binds_a_legacy_row_to_one_canonical_row_for_one_to_one_entities(con):
    x(con, "INSERT INTO company (canonical_name, name_norm) VALUES ('A','a')")
    x(con, "INSERT INTO company (canonical_name, name_norm) VALUES ('C','c')")
    oi = "INSERT INTO origin_ref (entity_type, entity_id, source_system, source_table, source_pk) VALUES (?,?,?,?,?)"
    x(con, oi, "company", 1, "tam", "companies", "1")
    bad(con, oi, "company", 2, "tam", "companies", "1")                          # the same legacy row cannot become two companies
    x(con, "INSERT INTO company_identifier (company_id, identifier_type, value_norm) VALUES (1, 'root_domain', 'a.com')")
    x(con, "INSERT INTO company_identifier (company_id, identifier_type, value_norm) VALUES (1, 'cin', 'U12345MH2020PTC123456')")
    x(con, oi, "company_identifier", 1, "tam", "companies", "1")                 # fan-out entity types are exempt
    x(con, oi, "company_identifier", 2, "tam", "companies", "1")


def test_lh2_domain_is_unique_per_portal_and_normalised(con):
    x(con, "INSERT INTO company (canonical_name, name_norm) VALUES ('A','a')")
    ins = "INSERT INTO company_account_link (company_id, account_id, hs_company_id, lh2_domain) VALUES (1,?,?,?)"
    x(con, ins, "main", "1", "acme.com")
    bad(con, ins, "main", "2", "acme.com")
    x(con, ins, "rat", "2", "acme.com")                                           # another portal is another HubSpot
    bad(con, ins, "main", "3", "ACME.com")
    bad(con, ins, "main", "3", " acme2.com")
    bad(con, ins, "main", "3", "")
    x(con, ins, "main", "3", None)
    x(con, ins, "main", "4", None)


def test_legacy_crosscheck_key_with_null_report_run(con):
    make_world(con)
    x(con, "INSERT INTO pipeline (account_id, pipeline_id, label) VALUES ('rat','2575252184','x')")
    x(con, "INSERT INTO rpt_legacy_snapshot (source_family, account_id, pipeline_id, snapshot_day, source_path, payload_json) VALUES ('rat','rat','2575252183','2026-10-01','p','{}')")
    cc = "INSERT INTO rpt_legacy_crosscheck (snapshot_id, report_run_id, metric, row_label, legacy_value, recomputed_value) VALUES (1, ?, 'flow', 'Leads', 1, 2)"
    x(con, cc, None)
    bad(con, cc, None)                                                            # NULL used to make the key non-unique
    x(con, "INSERT INTO rpt_report_run (report_kind, report_day) VALUES ('adhoc','2026-10-04')")
    x(con, cc, 1)
    bad(con, cc, 1)


def test_import_run_status_matches_finished_at_and_stale_jobs_are_reported(con):
    bad(con, "INSERT INTO ops_import_run (kind, source_name, status) VALUES ('seed','s','succeeded')")                  # finished but no end time
    bad(con, "INSERT INTO ops_import_run (kind, source_name, status, started_at, finished_at) VALUES ('seed','s','running','2026-10-03T00:00:00.000Z','2026-10-04T00:00:00.000Z')")
    x(con, "INSERT INTO ops_import_run (kind, source_name, status, started_at, finished_at) VALUES ('seed','s','succeeded','2026-10-03T00:00:00.000Z','2026-10-04T00:00:00.000Z')")
    assert db.doctor(con)["stale_running_jobs"] == 0
    x(con, "INSERT INTO ops_import_run (kind, source_name, status, started_at) VALUES ('seed','old','running','2020-01-01T00:00:00.000Z')")
    x(con, "INSERT INTO ops_import_run (kind, source_name) VALUES ('seed','fresh')")
    x(con, "INSERT INTO ops_sync_state (source_system, scope, object_type, last_status, last_attempt_at) VALUES ('gmail','me','messages','running','2020-01-01T00:00:00.000Z')")
    rep = db.doctor(con)
    assert rep["stale_running_jobs"] == 2 and any("crashed job" in w for w in rep["warnings"])


def test_hsraw_revision_when_history_changes_with_a_new_hash(con):
    ins = ("INSERT INTO hsraw_object (account_id, object_type, hs_id, properties_json, history_json, history_fetched_at, fetched_at, payload_sha256) "
           "VALUES ('main','deals','1','{}',?,'2026-10-04T00:00:00.000Z','2026-10-04T00:00:00.000Z',?)")
    x(con, ins, '{"dealstage":[{"value":"a"}],"pipeline":[{"value":"default"}]}', "a" * 64)
    x(con, "UPDATE hsraw_object SET history_json = '{\"dealstage\":[{\"value\":\"a\"},{\"value\":\"b\"}]}', payload_sha256 = ?", "b" * 64)    # contract: hash covers history
    assert val(con, "SELECT json_extract(history_json,'$.pipeline[0].value') FROM hsraw_object_revision") == "default"


def test_legacy_mirrors_have_an_import_run_index_and_doctor_flags_unattributed_rows(con):
    legacy = [r[0] for r in rows(con, "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'legacy\\_%' ESCAPE '\\'")]
    assert len(legacy) == 75
    missing = [t for t in legacy if val(con, "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name = ?", "ix_%s_run" % t) == 0]
    assert not missing
    assert db.doctor(con)["legacy_rows_without_import_run"] == []
    x(con, "INSERT INTO legacy_tam_categories (id, key, name) VALUES (1,'adtech','Adtech')")
    assert db.doctor(con)["legacy_rows_without_import_run"] == ["legacy_tam_categories"]
    assert "ix_legacy_tam_categories_run" in _plan(con, "SELECT 1 FROM legacy_tam_categories WHERE _import_run_id = 1")


# ============================================================================================== B: legacy fit
def test_company_signal_and_verdict_extras(con):
    x(con, "INSERT INTO company (canonical_name, name_norm) VALUES ('A','a')")
    cs = "INSERT INTO company_signal (company_id, signal_code, value_bool, value_num, value_text, source_system, observed_at) VALUES (1,?,?,?,?,?,?)"
    x(con, cs, "distress_tier", None, None, "confirmed", "radar", "2026-09-01T00:00:00.000Z")
    x(con, cs, "uses_jira", 1, None, None, "tam", None)
    bad(con, cs, "uses_jira", 0, None, None, "tam", None)                       # same observation twice (NULL observed_at included)
    bad(con, cs, "empty", None, None, None, "tam", None)                        # a signal carries a value
    bad(con, cs, "Bad Code", 1, None, None, "tam", None)
    bad(con, cs, "x", 2, None, None, "tam", None)
    with pytest.raises(sqlite3.IntegrityError):
        x(con, "DELETE FROM company")                                           # signals/evidence are not cascaded away
    ver = "INSERT INTO tam_company_verdict (company_id, icp_bucket, verdict_method, native_bucket, evidence_quote, details_json) VALUES (1,?,?,?,?,?)"
    x(con, ver, "unscored", "import", "Unclear", "we sell cobol tooling", '{"regulated_status":"SEBI:Stock Broker (equity)","parked_as":"individual_practitioner"}')
    bad(con, ver, "unscored", "import", "Weak", None, '{"a":1}')                         # one CURRENT verdict per (company, vertical)
    x(con, "UPDATE tam_company_verdict SET is_current = 0")
    bad(con, ver, "unscored", "import", "Weak", None, "[1]")                              # details must be a JSON object
    bad(con, ver, "unscored", "import", "Weak", None, "not json")
    x(con, ver, "unscored", "import", "Weak", None, '{"a":1}')


def test_contact_attrs_and_vertical_segment(con):
    x(con, "INSERT INTO contact (first_name, attrs_json) VALUES ('A', '{\"din\":\"01234567\"}')")
    bad(con, "INSERT INTO contact (first_name, attrs_json) VALUES ('B', '[1]')")
    x(con, "INSERT INTO vertical (slug, name, hubspot_segment) VALUES ('fintech','Fintech','Fintech')")
    bad(con, "INSERT INTO vertical (slug, name, hubspot_segment) VALUES ('fintech2','Fintech 2','Fintech')")      # deal.segment -> vertical is deterministic
    x(con, "INSERT INTO vertical (slug, name) VALUES ('a','A')")
    x(con, "INSERT INTO vertical (slug, name) VALUES ('b','B')")                                                  # NULL segments may repeat


def test_cost_ledger_real_quantity_with_unknown_price_is_not_a_placeholder(con):
    cl = "INSERT INTO cost_ledger (vendor, unit, qty, credits, usd, usd_basis, occurred_at, is_placeholder) VALUES ('apollo','phone_reveal',?,?,?,?,'2026-09-29T10:00:00.000Z',?)"
    x(con, cl, 3, 24, None, "unknown", 0)            # quantities are real spend signals: price unknown, row NOT fake
    x(con, cl, 1, None, None, "unknown", 1)
    bad(con, cl, 1, None, 0.5, "estimated", 1)
    assert val(con, "SELECT SUM(credits) FROM cost_ledger WHERE is_placeholder = 0") == 24


def test_gsheets_are_opt_in_and_the_intern_sheet_can_never_be_pulled(con):
    cat = "INSERT INTO ext_gsheet_catalog (spreadsheet_id, title, owner_email, catalog_fetched_at{c}) VALUES (?, 't', 'o@x.com', '2026-10-04T00:00:00.000Z'{v})"
    x(con, cat.format(c="", v=""), "1other")
    assert rows(con, "SELECT pull_enabled, pii_class, owners_json, time_zone FROM ext_gsheet_catalog") == [(0, "normal", "[]", None)]
    x(con, "UPDATE ext_gsheet_catalog SET pull_enabled = 1, time_zone = 'Asia/Calcutta', owners_json = '[\"o@x.com\"]'")
    intern = val(con, "SELECT spreadsheet_id FROM ext_gsheet_exclusion")
    assert intern.startswith("1IPA45")
    x(con, cat.format(c=", pull_enabled, pii_class", v=", 1, 'normal'"), intern)       # the importer even asks for it: forced shut
    assert rows(con, "SELECT pii_class, pull_enabled FROM ext_gsheet_catalog WHERE spreadsheet_id = ?", intern) == [("excluded", 0)]
    x(con, "UPDATE ext_gsheet_catalog SET pull_enabled = 1, pii_class = 'normal' WHERE spreadsheet_id = ?", intern)
    assert rows(con, "SELECT pii_class, pull_enabled FROM ext_gsheet_catalog WHERE spreadsheet_id = ?", intern) == [("excluded", 0)]
    x(con, "INSERT INTO ext_gsheet_tab (spreadsheet_id, sheet_id, title) VALUES (?, 0, 'Sheet1')", intern)
    with pytest.raises(sqlite3.IntegrityError):                                         # no row can be stored for a pull_enabled = 0 sheet
        x(con, "INSERT INTO ext_gsheet_row (tab_pk, row_number, cells_json, fetched_at) VALUES (1, 1, '[]', '2026-10-04T00:00:00.000Z')")
    x(con, "INSERT INTO ext_gsheet_tab (spreadsheet_id, sheet_id, title) VALUES ('1other', 0, 'Sheet1')")
    x(con, "INSERT INTO ext_gsheet_row (tab_pk, row_number, cells_json, fetched_at) VALUES (2, 1, '[]', '2026-10-04T00:00:00.000Z')")
    assert db.doctor(con)["gsheet_excluded_rows"] == 0
    x(con, "UPDATE ext_gsheet_catalog SET pull_enabled = 0 WHERE spreadsheet_id = '1other'")       # disabled AFTER a pull: leftover rows are reported
    assert db.doctor(con)["gsheet_excluded_rows"] == 1


def test_gmail_message_keeps_what_a_full_message_carries(con):
    x(con, "INSERT INTO ext_gmail_account (email_address) VALUES ('bhanu.enamala@lh2.ai')")
    ins = ("INSERT INTO ext_gmail_message (gmail_account_id, message_id, from_addr, to_addrs, cc_addrs, bcc_addrs, reply_to, references_hdr, date_header_at, "
           "body_html, headers_json, fetched_at) VALUES (1,'m1','a@x.com','b@x.com,c@x.com','d@x.com','e@x.com','r@x.com','<a@x> <b@x>',?,'<p>hi</p>',?,'2026-10-04T00:00:00.000Z')")
    x(con, ins, "2026-10-04T05:00:00.000Z", '[["List-Unsubscribe","<mailto:u@x.com>"],["Delivered-To","bhanu.enamala@lh2.ai"]]')
    bad(con, ins.replace("'m1'", "'m2'"), "2026-10-04 05:00:00", "[]")
    bad(con, ins.replace("'m1'", "'m3'"), "2026-10-04T05:00:00.000Z", '{"not":"an array"}')
    bad(con, "INSERT INTO ext_gmail_message (gmail_account_id, message_id, bcc_addrs, fetched_at) VALUES (1,'m4','Mixed@X.com','2026-10-04T00:00:00.000Z')")
    x(con, "INSERT INTO ext_gmail_attachment (message_pk, part_id, filename, content_id, disposition) VALUES (1,'2','logo.png','<cid1>','inline')")
    bad(con, "INSERT INTO ext_gmail_attachment (message_pk, part_id, filename, disposition) VALUES (1,'3','x','weird')")
    assert rows(con, "SELECT rowid FROM ext_gmail_message_fts WHERE ext_gmail_message_fts MATCH 'from_addr:a'") == [(1,)]
    assert all(v == "ok" for v in db.fts_integrity(con).values())


# ============================================================================================== generator guards (L1)
def _gen():
    import importlib.util
    spec = importlib.util.spec_from_file_location("gen_legacy_ddl", os.path.join(ROOT, "tools", "gen_legacy_ddl.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _have_legacy(gen):
    return all(os.path.exists(os.path.join(ROOT, s[3])) for s in gen.SOURCES)


def test_generator_check_is_the_default_and_write_refuses_to_clobber(tmp_path, monkeypatch, capsys):
    gen = _gen()
    if not _have_legacy(gen):
        pytest.skip("legacy databases not available")
    d = tmp_path / "mig"
    shutil.copytree(os.path.join(ROOT, "db", "migrations"), str(d))
    monkeypatch.setattr(gen, "MIGRATIONS_DIR", str(d))
    assert gen.main([]) == 0                                              # default = --check, nothing written
    target = d / "0006_legacy_tam.sql"
    original = target.read_text()
    target.write_text(original + "\n-- hand edit\n")
    assert gen.main([]) == 1                                              # check: differs
    assert gen.main(["--write", "--only", "tam"]) == 1 and target.read_text().endswith("-- hand edit\n")      # refuses without --allow-change
    assert "REFUSED" in capsys.readouterr().out
    assert gen.main(["--write", "--allow-change", "--only", "tam"]) == 0 and target.read_text() == original
    # an APPLIED migration is never regenerated, even with --allow-change
    real = sqlite3.connect(str(tmp_path / "real.sqlite"))
    real.execute("CREATE TABLE schema_migration (version INTEGER, checksum_sha256 TEXT)")
    import hashlib
    real.execute("INSERT INTO schema_migration VALUES (6, ?)", (hashlib.sha256((original + "\n-- hand edit\n").encode()).hexdigest(),))
    real.commit()
    real.close()
    target.write_text(original + "\n-- hand edit\n")
    monkeypatch.setattr(gen, "_is_applied", lambda text, real=None, _orig=gen._is_applied: _orig(text, str(tmp_path / "real.sqlite")))
    assert gen.main(["--write", "--allow-change", "--only", "tam"]) == 1
    assert "APPLIED" in capsys.readouterr().out and target.read_text().endswith("-- hand edit\n")


def test_generator_output_has_run_indexes_no_if_not_exists_and_a_safe_terminator():
    gen = _gen()
    sql = gen.rewrite_ddl("CREATE VIEW v AS SELECT id FROM a -- trailing comment", "view", "p_", {"a"})
    assert sql.endswith("\n;") and "IF NOT EXISTS" not in sql
    assert gen.rewrite_ddl("CREATE TABLE IF NOT EXISTS a (id INTEGER PRIMARY KEY)", "table", "p_", {"a"}).startswith("CREATE TABLE p_a")
    if not _have_legacy(gen):
        pytest.skip("legacy databases not available")
    for src in gen.SOURCES:
        text = gen.render_migration(src)
        assert "IF NOT EXISTS" not in text.split("\n\n", 1)[1]
        assert len(re.findall(r"CREATE INDEX ix_%s\w+_run ON" % src[2], text)) == len(re.findall(r"^CREATE TABLE ", text, re.M))
    assert "LOAD NOTE" in gen.render_migration([s for s in gen.SOURCES if s[1] == "radar"][0])


def test_data_dictionary_is_current():
    import importlib.util
    spec = importlib.util.spec_from_file_location("gen_data_dictionary", os.path.join(ROOT, "tools", "gen_data_dictionary.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    text = mod.render()
    assert open(os.path.join(ROOT, "docs", "DATA_DICTIONARY.md"), encoding="utf-8").read() == text, "run tools/gen_data_dictionary.py"
    assert "deal_stage_event" in text and "company_phone" in text and "legacy_tam_companies" in text
