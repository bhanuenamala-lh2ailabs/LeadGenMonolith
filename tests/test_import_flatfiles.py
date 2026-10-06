"""Tests for leadgen/legacy_import/flatfiles.py: snapshots, whale list, generic CSV/XLSX/JSON staging and the link pass.

Every test builds a throw-away ``legacy/`` tree and a throw-away migrated database; nothing reads or writes the real db/leadgen.sqlite
(the one test that looks at the real legacy/ folder is read-only and does a dry run without a database).
"""
import json
import os
import shutil
import sqlite3

import pytest

from leadgen import db, suppression
from leadgen.legacy_import import flatfiles as ff

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------------------------------- fixtures
@pytest.fixture(scope="module")
def template_db(tmp_path_factory):
    p = str(tmp_path_factory.mktemp("tpl") / "t.sqlite")
    db.migrate(p, backup=False)
    return p


@pytest.fixture()
def path(tmp_path, template_db):
    dst = str(tmp_path / "work.sqlite")
    src = sqlite3.connect(template_db)
    out = sqlite3.connect(dst)
    src.backup(out)
    out.close()
    src.close()
    return dst


@pytest.fixture()
def con(path):
    c = db.connect(path, must_exist=True)
    yield c
    c.close()


@pytest.fixture()
def legacy(tmp_path):
    root = tmp_path / "legacy"
    for r in ff.LEGACY_REPOS:
        (root / r).mkdir(parents=True)
    return root


def write(p, text, encoding="utf-8"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(text.encode(encoding))
    return p


def val(c, sql, *a):
    return c.execute(sql, a).fetchone()[0]


# ---------------------------------------------------------------------------------------------------- schema
def test_migration_0015_tables_and_checks(con):
    assert val(con, "SELECT MAX(version) FROM schema_migration") >= 15
    con.execute("INSERT INTO ext_file_catalog (path, source_repo, sha256, size_bytes, kind, role) VALUES ('legacy/hubspot/a.csv','hubspot',?,1,'csv','pool')", ("a" * 64,))
    fid = val(con, "SELECT file_id FROM ext_file_catalog")

    def bad(sql, *a):
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(sql, a)

    ins = "INSERT INTO ext_file_row (file_id, sheet_name, row_no, row_json) VALUES (?,?,?,?)"
    bad(ins, fid, "csv", 1, "not json")
    bad(ins, fid, "csv", 1, "[1,2]")                       # must be an object
    bad(ins, fid, "", 1, "{}")
    bad(ins, fid, "csv", 0, "{}")
    con.execute(ins, (fid, "csv", 1, "{}"))
    bad(ins, fid, "csv", 1, "{}")                          # (file, sheet, row_no) is unique
    bad("UPDATE ext_file_row SET company_domain_norm = 'WWW.Acme.com'")
    bad("UPDATE ext_file_row SET linkedin_norm = 'in/someone'")
    bad("UPDATE ext_file_row SET phone_e164 = '98765'")
    bad("UPDATE ext_file_row SET company_id = 999")        # FK (foreign_keys=ON)
    bad("INSERT INTO ext_file_catalog (path, source_repo, sha256, size_bytes, kind, role) VALUES ('legacy/hubspot/b.csv','hubspot',?,1,'csv','skipped')", "b" * 64)   # skipped needs a reason
    bad("INSERT INTO ext_file_catalog (path, source_repo, sha256, size_bytes, kind, role, skip_reason) VALUES ('legacy/hubspot/c.csv','hubspot',?,1,'csv','pool','why')", "c" * 64)
    bad("INSERT INTO ext_file_catalog (path, source_repo, sha256, size_bytes, kind, role) VALUES ('/abs/d.csv','hubspot',?,1,'csv','pool')", "d" * 64)
    bad("INSERT INTO ext_file_catalog (path, source_repo, sha256, size_bytes, kind, role) VALUES ('legacy/other/e.csv','elsewhere',?,1,'csv','pool')", "e" * 64)
    con.execute("DELETE FROM ext_file_catalog")            # cascades to the rows
    assert val(con, "SELECT COUNT(*) FROM ext_file_row") == 0


# ---------------------------------------------------------------------------------------------------- snapshots
def _snap(day, **extra):
    d = {"date": day, "flow": {"A": 1, "B": 2}, "current_state": {"A": 5}, "dashboard_flow": {"Leads": 3}, "cumulative": {"Leads": 30}, "engaged_deal_ids": ["1", "2", "3"]}
    d.update(extra)
    return json.dumps(d)


def _snapshot_tree(legacy):
    write(legacy / "RapidActionTeam/snapshots/rat_2026-09-25.json", _snap("2026-09-25"))
    write(legacy / "RapidActionTeam/snapshots/rat_2026-09-28.json", _snap("2026-09-28", seeded=True))
    h = legacy / "hubspot/crm_mirror/data/snapshots"
    write(h / "full_funnel_2026-09-03.json", _snap("2026-09-03"))
    write(h / "coding_funnel_2026-09-03.json", json.dumps({"date": "2026-09-03", "flow": {"A": 1}, "current_state": {"A": 4}}))
    write(h / "full_funnel_coopsglobal_2026-09-23.json", json.dumps({"date": "2026-09-23", "flow": {}, "current_state": {}, "cumulative": {"X": 1}, "dashboard_flow": {"X": 1}, "engaged_deal_ids": []}))
    write(h / "full_funnel_owner_96573782_2026-09-03.json", json.dumps({"date": "2026-09-03", "owner_id": "96573782", "owner_name": "A", "flow": {"A": 1}, "current_state": {"A": 2}}))
    write(h / "full_funnel_owner_unassigned_2026-09-03.json", json.dumps({"date": "2026-09-03", "owner_id": None, "owner_name": "Unassigned", "flow": {}, "current_state": {"A": 2}}))
    write(h / "hubspot_2026-07-29.json", json.dumps({"generated": "2026-07-29", "deals": 1197, "contacts": 1358, "companies": 871}))
    write(h / "notes.json", "{}")                                            # not a snapshot family
    c = legacy / "companyOps/crm_mirror/data/snapshots"
    write(c / "full_funnel_cluster1_2026-09-26.json", json.dumps({"date": "2026-09-26", "dashboard_flow": {"A": 1}, "current_state": {"A": 2}, "cumulative": {"A": 3}, "engaged_deal_ids": [], "bootstrap": True}))
    write(c / "full_funnel_cluster2_2026-09-26.json", json.dumps({"date": "2026-09-26", "dashboard_flow": {"A": 9}, "current_state": {"A": 8}, "cumulative": {"A": 7}, "engaged_deal_ids": [], "bootstrap": False}))


def test_snapshots_import_maps_families_and_is_idempotent(path, con, legacy):
    _snapshot_tree(legacy)
    s = ff.import_snapshots(path, legacy_root=str(legacy))
    assert s["files"] == 10 and s["inserted"] == 10 and s["failed"] == 0 and s["unchanged"] == 0
    assert s["unrecognised_json"] == ["legacy/hubspot/crm_mirror/data/snapshots/notes.json"]
    assert s["by_family"] == {"rat": 2, "main_full": 1, "main_coding": 1, "main_coopsglobal": 1, "main_owner": 2, "main_hubspot_mirror": 1,
                              "companyops_cluster/default": 1, "companyops_cluster/2464812771": 1}
    rows = {(r["source_family"], r["account_id"], r["pipeline_id"], r["owner_hs_id"], r["snapshot_day"]): r
            for r in con.execute("SELECT * FROM rpt_legacy_snapshot")}
    r = rows[("rat", "rat", "2575252183", None, "2026-09-28")]
    assert r["is_seeded"] == 1 and r["engaged_count"] == 3 and json.loads(r["flow_json"]) == {"A": 1, "B": 2}
    assert json.loads(r["payload_json"])["date"] == "2026-09-28" and len(r["source_sha256"]) == 64
    assert rows[("main_coding", "main", "default", None, "2026-09-03")]["cumulative_json"] is None            # absent block stays NULL
    assert rows[("main_coopsglobal", "main", "2425754306", None, "2026-09-23")]["engaged_count"] == 0
    assert ("main_owner", "main", None, "unassigned", "2026-09-03") in rows and ("main_owner", "main", None, "96573782", "2026-09-03") in rows
    m = rows[("main_hubspot_mirror", "main", None, None, "2026-07-29")]
    assert m["flow_json"] is None and json.loads(m["payload_json"])["deals"] == 1197
    c1 = rows[("companyops_cluster", "companyops", "default", None, "2026-09-26")]
    c2 = rows[("companyops_cluster", "companyops", "2464812771", None, "2026-09-26")]
    assert c1["is_bootstrap"] == 1 and c2["is_bootstrap"] == 0 and json.loads(c2["dashboard_flow_json"]) == {"A": 9}
    # the flattened view the cross-check job reads
    assert val(con, "SELECT value FROM v_legacy_snapshot_rows WHERE source_family='rat' AND snapshot_day='2026-09-28' AND metric='dashboard_flow' AND row_label='Leads'") == 3
    assert val(con, "SELECT status FROM ops_import_run WHERE kind='snapshot_import'") == "succeeded"
    # second run: nothing changes
    s2 = ff.import_snapshots(path, legacy_root=str(legacy))
    assert s2["inserted"] == 0 and s2["updated"] == 0 and s2["unchanged"] == 10
    assert val(con, "SELECT COUNT(*) FROM rpt_legacy_snapshot") == 10
    # a changed file updates in place (same unique key), a new day inserts
    write(legacy / "RapidActionTeam/snapshots/rat_2026-09-25.json", _snap("2026-09-25", flow={"A": 99}))
    write(legacy / "RapidActionTeam/snapshots/rat_2026-09-29.json", _snap("2026-09-29"))
    s3 = ff.import_snapshots(path, legacy_root=str(legacy))
    assert s3["updated"] == 1 and s3["inserted"] == 1 and s3["unchanged"] == 9
    assert json.loads(val(con, "SELECT flow_json FROM rpt_legacy_snapshot WHERE source_family='rat' AND snapshot_day='2026-09-25'")) == {"A": 99}
    assert val(con, "SELECT COUNT(*) FROM rpt_legacy_snapshot") == 11


def test_snapshots_dq_for_mismatches_and_broken_files(path, con, legacy):
    write(legacy / "RapidActionTeam/snapshots/rat_2026-09-25.json", _snap("2026-09-26"))                         # date field disagrees with the file name
    write(legacy / "RapidActionTeam/snapshots/rat_2026-09-28.json", _snap("2026-09-28", flow={"A": "oops"}))     # non-numeric metric
    write(legacy / "RapidActionTeam/snapshots/rat_2026-09-29.json", "{not json")
    s = ff.import_snapshots(path, legacy_root=str(legacy))
    assert s["failed"] == 1 and s["dq"] == {"date_mismatch": 1, "non_numeric_metric": 1, "unreadable": 1}
    rules = {r[0] for r in con.execute("SELECT rule_code FROM ops_dq_issue")}
    assert {"legacy_snapshot_date_mismatch", "legacy_snapshot_non_numeric_metric", "legacy_snapshot_unreadable"} <= rules
    assert val(con, "SELECT status FROM ops_import_run WHERE kind='snapshot_import'") == "partial"
    assert val(con, "SELECT COUNT(*) FROM rpt_legacy_snapshot WHERE snapshot_day = '2026-09-26'") == 1         # the payload date wins


def test_snapshots_dry_run_writes_nothing(path, con, legacy):
    _snapshot_tree(legacy)
    s = ff.import_snapshots(path, dry_run=True, legacy_root=str(legacy))
    assert s["inserted"] == 10 and s["dry_run"] is True
    assert val(con, "SELECT COUNT(*) FROM rpt_legacy_snapshot") == 0 and val(con, "SELECT COUNT(*) FROM ops_import_run WHERE kind='snapshot_import'") == 0


# ---------------------------------------------------------------------------------------------------- whale list
WHALE_HEADER = "rank,score,name,domain,city,contact_name,contact_linkedin,contact_email,contact_phone\n"


def _whales(legacy):
    write(legacy / "hubspot/reserve/whale_list_27_NEVER_PUSH_TO_HUBSPOT.csv",
          WHALE_HEADER + '1,90,"Acme  Software, Inc",https://WWW.Acme-Soft.com/about,Pune,A B,https://www.linkedin.com/in/Some-One/?x=1,Boss@Acme-Soft.com,+91 98765 43210\n'
          "2,80,Globex Corp,globex.io,Delhi,,,,\n"
          "3,70,No Domain Ltd,,Mumbai,,,,\n"
          "4,60,,not a domain at all,Mumbai,,,,\n")
    write(legacy / "hubspot/temporary/Whale_List_30.csv", "﻿" + WHALE_HEADER + "1,90,Acme Software Inc,acme-soft.com,Pune,,,,\n5,50,Initech,initech.com,Goa,,,,\n")


def test_whale_import_is_never_push_and_normalised(path, con, legacy):
    _whales(legacy)
    s = ff.import_whales(path, legacy_root=str(legacy))
    assert s["rows_read"] == 6 and s["rejected"] == 0 and s["rows_without_any_value"] == 1 and s["reactivated"] == 0
    got = {(r["match_type"], r["match_value_norm"]) for r in con.execute("SELECT * FROM suppression WHERE kind = 'never_push' AND is_active = 1 AND account_id IS NULL")}
    assert {("domain", "acme-soft.com"), ("domain", "globex.io"), ("domain", "initech.com"), ("company_name", "acme software, inc"),
            ("company_name", "acme software inc"), ("company_name", "globex corp"), ("company_name", "no domain ltd"), ("company_name", "initech"),
            ("linkedin_person", "in/some-one"), ("email", "boss@acme-soft.com"), ("phone", "+919876543210")} <= got
    assert ("domain", "not a domain at all") not in got                       # an unnormalisable value is never stored raw
    assert s["distinct_values"] == len(got)
    # every outbound check, however the probe is spelled, is blocked
    for probe in ({"domain": "http://www.ACME-SOFT.com/x"}, {"email": " BOSS@acme-soft.com "}, {"linkedin_company": None, "linkedin_person": "linkedin.com/in/some-one"},
                  {"company_name": "GLOBEX   corp"}, {"phone": "098765 43210"}):
        with pytest.raises(suppression.SuppressedError):
            suppression.assert_pushable(con, account_id="main", **probe)
    suppression.assert_pushable(con, account_id="main", domain="clean-company.com")
    # provenance + audit + file-level flag
    assert val(con, "SELECT COUNT(*) FROM origin_ref WHERE entity_type='suppression' AND source_system LIKE 'csv:%'") >= len(got)
    assert val(con, "SELECT COUNT(*) FROM ops_audit_log WHERE action='suppression.add'") == 1
    list_name = val(con, "SELECT list_name FROM suppression WHERE match_type='domain' AND match_value_norm='acme-soft.com'")
    assert "Whale_List_30.csv" in list_name and "whale_list_27_NEVER_PUSH_TO_HUBSPOT.csv" in list_name
    # idempotent
    s2 = ff.import_whales(path, legacy_root=str(legacy))
    assert s2["inserted"] == 0 and s2["already_present"] == s["distinct_values"]
    assert val(con, "SELECT COUNT(*) FROM ops_audit_log WHERE action='suppression.add'") == 1                  # nothing changed -> no new audit row


def test_whale_reactivates_a_deactivated_row(path, con, legacy):
    _whales(legacy)
    ff.import_whales(path, legacy_root=str(legacy))
    con.execute("UPDATE suppression SET is_active = 0 WHERE match_type = 'domain' AND match_value_norm = 'globex.io'")
    suppression.assert_pushable(con, domain="globex.io")                      # (a human switched it off)
    s = ff.import_whales(path, legacy_root=str(legacy))
    assert s["reactivated"] == 1
    with pytest.raises(suppression.SuppressedError):
        suppression.assert_pushable(con, domain="globex.io")
    assert val(con, "SELECT COUNT(*) FROM ops_dq_issue WHERE rule_code = 'whale_reactivated'") == 1


def test_whale_dry_run_reports_without_writing(path, con, legacy):
    _whales(legacy)
    s = ff.import_whales(path, dry_run=True, legacy_root=str(legacy))
    assert s["inserted"] == s["distinct_values"] > 0
    assert val(con, "SELECT COUNT(*) FROM suppression") == 0 and val(con, "SELECT COUNT(*) FROM ops_import_run WHERE source_name = 'whale_lists'") == 0


# ---------------------------------------------------------------------------------------------------- files
def _tree(legacy):
    write(legacy / "hubspot/reserve/pool.csv",
          "Company Name,Website,LinkedIn URL,Contact LinkedIn,Email,Mobile,Notes\n"
          "Acme Ltd,https://www.acme.com/en,https://in.linkedin.com/company/acme-ltd/,https://www.linkedin.com/in/jane-doe,Jane@Acme.com,98765 43210,\"multi\nline note\"\n"
          "Beta Inc,https://www.linkedin.com/company/beta,,,,,\n"
          "Gamma,N/A,,,bad-email,12,x\n"
          ",,,,,,\n")
    write(legacy / "companyOps/data/exports/semi.csv", "id;name;domain\n1;Delta GmbH;delta.de\n2;Epsilon;epsilon.fr\n", "cp1252")
    write(legacy / "companyOps/data/exports/accents.csv", "﻿name,domain\nCafé Zoe,zoe.in\n", "utf-8")
    write(legacy / "companyOps/data/exports/latin.csv", "name,domain\nZoé SA,zoe.es\nMüller,mueller.de\n", "cp1252")
    write(legacy / "hubspot/exports/tabbed.tsv", "name\tdomain\nTab Co\ttab.co\n")
    write(legacy / "hubspot/exports/copy_a.csv", "name,domain\nSame Co,same.co\n")
    write(legacy / "hubspot/reserve/copy_b.csv", "name,domain\nSame Co,same.co\n")                      # byte-identical, better role (pool)
    write(legacy / "hubspot/exports/empty.csv", "")
    write(legacy / "hubspot/exports/client_secret_123.csv", "a,b\n1,2\n")
    write(legacy / "hubspot/exports/~$lock.xlsx", "x")
    write(legacy / "hubspot/node_modules/pkg/junk.csv", "a,b\n1,2\n")
    write(legacy / "hubspot/godown/shutdown-radar-us/pipeline/processed/cands.csv", "company_name,domain\nThe French Gourmet,the.com\n")
    write(legacy / "hubspot/crm_mirror/data/deals.json", json.dumps([{"id": "1", "dealname": "Acme", "domain": "acme.com", "linkedin_url": "https://linkedin.com/company/acme-ltd"}, {"id": "2", "dealname": "Beta"}]))
    write(legacy / "hubspot/crm_mirror/data/index/by_domain.json", json.dumps({"acme.com": "11", "beta.io": "12"}))
    write(legacy / "hubspot/crm_mirror/data/index/deal_names.json", json.dumps({"acme": [{"id": "1"}], "beta": [{"id": "2"}]}))
    write(legacy / "hubspot/crm_mirror/data/index/sheet_worked_exclude.json", json.dumps({"generated": "2026-08-03", "names": ["x"], "detail": [{"company": "Velotio", "norm": "velotio"}]}))
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "First"
    ws.append(["Roster title only"])
    ws.append(["Company", "Domain", "Phone", "Date"])
    ws.append(["Xi Corp", "xi.com", 9876543210, __import__("datetime").datetime(2026, 9, 1)])
    ws.append([None, None, None, None])
    ws.append(["Omicron", "omicron.com", "+44 20 7946 0958", None])
    ws2 = wb.create_sheet("Second")
    ws2.append(["Email", "Name"])
    ws2.append(["a@b.co", "Pi"])
    (legacy / "companyOps/data/imports").mkdir(parents=True)
    wb.save(str(legacy / "companyOps/data/imports/book.xlsx"))


def _rows(con, where, *a):
    return [dict(r) for r in con.execute("SELECT r.* FROM ext_file_row r JOIN ext_file_catalog f USING(file_id) WHERE " + where, a)]


def test_files_import_catalog_rows_roles_and_skips(path, con, legacy):
    _tree(legacy)
    s = ff.import_files(path, legacy_root=str(legacy))
    assert s["failed"] == 0 and s["files_seen"] == 16
    cat = {r["path"]: dict(r) for r in con.execute("SELECT * FROM ext_file_catalog")}
    skipped = {p: r["skip_reason"] for p, r in cat.items() if r["role"] == "skipped"}
    assert set(skipped) == {"legacy/hubspot/exports/empty.csv", "legacy/hubspot/exports/client_secret_123.csv", "legacy/hubspot/exports/~$lock.xlsx"}
    assert all(skipped.values()) and not any("node_modules" in p for p in cat)
    assert cat["legacy/hubspot/reserve/pool.csv"]["role"] == "pool" and cat["legacy/companyOps/data/imports/book.xlsx"]["role"] == "source_of_truth"
    assert cat["legacy/companyOps/data/exports/semi.csv"]["role"] == "export" and cat["legacy/hubspot/exports/tabbed.tsv"]["kind"] == "tsv"
    # encodings and delimiters
    assert cat["legacy/companyOps/data/exports/semi.csv"]["delimiter"] == ";" and cat["legacy/hubspot/exports/tabbed.tsv"]["delimiter"] == "\t"
    assert cat["legacy/companyOps/data/exports/accents.csv"]["encoding"] == "utf-8-sig" and cat["legacy/companyOps/data/exports/latin.csv"]["encoding"] == "cp1252"
    assert json.loads(_rows(con, "f.path LIKE '%accents.csv'")[0]["row_json"]) == {"name": "Café Zoe", "domain": "zoe.in"}
    assert json.loads(_rows(con, "f.path LIKE '%latin.csv'")[1]["row_json"])["name"] == "Müller"
    # csv with a multi-line cell, a blank row and a trailing comma
    p = cat["legacy/hubspot/reserve/pool.csv"]
    assert p["rows"] == 3 and p["rows_loaded"] == 3 and json.loads(p["columns_json"])["csv"][0] == "Company Name"
    r = _rows(con, "f.path LIKE '%pool.csv'")
    assert [x["row_no"] for x in r] == [1, 2, 3] and json.loads(r[0]["row_json"])["Notes"] == "multi\nline note"
    assert (r[0]["company_domain_norm"], r[0]["linkedin_norm"], r[0]["person_linkedin_norm"], r[0]["email_norm"], r[0]["phone_e164"], r[0]["company_name_norm"]) == \
        ("acme.com", "company/acme-ltd", "in/jane-doe", "jane@acme.com", "+919876543210", "acme ltd")
    assert r[1]["company_domain_norm"] is None and r[1]["linkedin_norm"] == "company/beta"                      # linkedin.com is not a company domain
    assert r[2]["company_domain_norm"] is None and r[2]["email_norm"] is None and r[2]["phone_e164"] is None   # junk values are not extracted
    # xlsx: all sheets, title row skipped, empty row skipped, typed cells, ISO dates
    b = cat["legacy/companyOps/data/imports/book.xlsx"]
    assert json.loads(b["columns_json"]) == {"First": ["Company", "Domain", "Phone", "Date"], "Second": ["Email", "Name"]} and b["rows"] == 3
    xr = _rows(con, "f.path LIKE '%book.xlsx' AND r.sheet_name = 'First'")
    assert json.loads(xr[0]["row_json"]) == {"Company": "Xi Corp", "Domain": "xi.com", "Phone": 9876543210, "Date": "2026-09-01T00:00:00"}
    assert xr[0]["phone_e164"] == "+919876543210" and xr[1]["phone_e164"] == "+442079460958" and xr[1]["row_no"] > xr[0]["row_no"]
    assert _rows(con, "f.path LIKE '%book.xlsx' AND r.sheet_name = 'Second'")[0]["email_norm"] == "a@b.co"
    # byte-identical copies: rows stored once (on the better-role copy), the other is catalogued with dup_of_file_id
    a, bb = cat["legacy/hubspot/exports/copy_a.csv"], cat["legacy/hubspot/reserve/copy_b.csv"]
    assert bb["dup_of_file_id"] is None and bb["rows_loaded"] == 1 and a["dup_of_file_id"] == bb["file_id"] and a["rows"] == 1 and a["rows_loaded"] == 0
    assert s["duplicates"] == 1 and a["sha256"] == bb["sha256"]
    # guessed-domain files keep the raw value but never expose it as a lookup key
    g = _rows(con, "f.path LIKE '%cands.csv'")[0]
    assert json.loads(g["row_json"])["domain"] == "the.com" and g["company_domain_norm"] is None and g["company_name_norm"] == "the french gourmet"
    # semicolon file: header detection + plain `name` column counts as the company because the table has a domain column
    sr = _rows(con, "f.path LIKE '%semi.csv'")
    assert sr[0]["company_domain_norm"] == "delta.de" and sr[0]["company_name_norm"] == "delta gmbh"
    assert val(con, "SELECT status FROM ops_import_run WHERE source_name = 'flatfiles'") == "succeeded"
    assert val(con, "SELECT SUM(rows_loaded) FROM ext_file_catalog") == val(con, "SELECT COUNT(*) FROM ext_file_row")


def test_json_mirrors_are_staged_flagged_stale_and_touch_nothing_canonical(path, con, legacy):
    _tree(legacy)
    before = {t: val(con, "SELECT COUNT(*) FROM " + t) for t in ("company", "contact", "deal", "company_identifier")}
    ff.import_files(path, legacy_root=str(legacy), include="crm_mirror")
    cat = {r["path"].rsplit("/", 1)[1]: dict(r) for r in con.execute("SELECT * FROM ext_file_catalog")}
    assert cat["deals.json"]["is_stale_mirror"] == 1 and cat["deals.json"]["role"] == "mirror" and cat["deals.json"]["kind"] == "json" and "stale mirror" in cat["deals.json"]["notes"]
    assert cat["sheet_worked_exclude.json"]["is_stale_mirror"] == 0 and cat["sheet_worked_exclude.json"]["role"] == "source_of_truth" and cat["sheet_worked_exclude.json"]["rows"] == 1
    assert cat["by_domain.json"]["rows"] == 2 and cat["deal_names.json"]["rows"] == 2                         # mappings become one row per key
    d = _rows(con, "f.path LIKE '%deals.json'")
    assert d[0]["company_domain_norm"] == "acme.com" and d[0]["linkedin_norm"] == "company/acme-ltd" and d[0]["company_name_norm"] == "acme"
    assert [r["company_domain_norm"] for r in _rows(con, "f.path LIKE '%by_domain.json'")] == ["acme.com", "beta.io"]
    assert _rows(con, "f.path LIKE '%sheet_worked_exclude.json'")[0]["company_name_norm"] == "velotio"
    assert before == {t: val(con, "SELECT COUNT(*) FROM " + t) for t in before}


def test_files_pass_is_idempotent_and_replaces_changed_files(path, con, legacy):
    _tree(legacy)
    s1 = ff.import_files(path, legacy_root=str(legacy))
    n = val(con, "SELECT COUNT(*) FROM ext_file_row")
    ids = [tuple(r) for r in con.execute("SELECT file_id, path, sha256 FROM ext_file_catalog ORDER BY file_id")]
    s2 = ff.import_files(path, legacy_root=str(legacy))
    assert s2["loaded"] == 0 and s2["rows_loaded"] == 0 and s2["unchanged"] == s1["files_seen"] - s1["skipped"] and s2["failed"] == 0
    assert val(con, "SELECT COUNT(*) FROM ext_file_row") == n and [tuple(r) for r in con.execute("SELECT file_id, path, sha256 FROM ext_file_catalog ORDER BY file_id")] == ids
    # change one file: its rows are replaced (same file_id), nothing else is touched
    write(legacy / "companyOps/data/exports/semi.csv", "id;name;domain\n1;Delta GmbH;delta.de\n", "cp1252")
    s3 = ff.import_files(path, legacy_root=str(legacy))
    assert s3["loaded"] == 1 and s3["rows_loaded"] == 1 and s3["unchanged"] == s2["unchanged"] - 1
    assert len(_rows(con, "f.path LIKE '%semi.csv'")) == 1 and val(con, "SELECT COUNT(*) FROM ext_file_row") == n - 1
    assert [r[0] for r in con.execute("SELECT file_id FROM ext_file_catalog ORDER BY file_id")] == [i[0] for i in ids]
    # --force reloads everything
    assert ff.import_files(path, legacy_root=str(legacy), force=True)["loaded"] == s1["loaded"] and val(con, "SELECT COUNT(*) FROM ext_file_row") == n - 1


def test_keep_duplicates_loads_every_copy(path, con, legacy):
    _tree(legacy)
    s = ff.import_files(path, legacy_root=str(legacy), keep_duplicates=True)
    assert s["duplicates"] == 0 and val(con, "SELECT COUNT(*) FROM ext_file_catalog WHERE dup_of_file_id IS NOT NULL") == 0
    assert val(con, "SELECT rows_loaded FROM ext_file_catalog WHERE path LIKE '%copy_a.csv'") == 1


def test_big_files_must_be_company_lists(path, con, legacy, monkeypatch):
    write(legacy / "hubspot/exports/big_list.csv", "company,website\n" + "".join("Co%d,co%d.com\n" % (i, i) for i in range(50)))
    write(legacy / "hubspot/exports/big_dump.csv", "ts,payload,status\n" + "".join("%d,abcdef,ok\n" % i for i in range(50)))
    monkeypatch.setattr(ff, "BIG_FILE_BYTES", 100)
    s = ff.import_files(path, legacy_root=str(legacy))
    assert s["skipped"] == 1
    r = con.execute("SELECT role, skip_reason, rows, rows_loaded FROM ext_file_catalog WHERE path LIKE '%big_dump.csv'").fetchone()
    assert r["role"] == "skipped" and "50 MB" in r["skip_reason"] and r["rows"] is None and r["rows_loaded"] == 0
    assert val(con, "SELECT rows_loaded FROM ext_file_catalog WHERE path LIKE '%big_list.csv'") == 50


def test_unreadable_xlsx_is_catalogued_not_fatal(path, con, legacy):
    write(legacy / "hubspot/exports/broken.xlsx", "this is not a zip")
    write(legacy / "hubspot/exports/ok.csv", "name,domain\nA,a.com\n")
    s = ff.import_files(path, legacy_root=str(legacy))
    assert s["failed"] == 1 and s["failed_files"][0]["path"].endswith("broken.xlsx") and s["loaded"] == 1
    r = con.execute("SELECT role, skip_reason FROM ext_file_catalog WHERE path LIKE '%broken.xlsx'").fetchone()
    assert r["role"] == "skipped" and r["skip_reason"].startswith("unreadable")
    assert val(con, "SELECT status FROM ops_import_run WHERE source_name = 'flatfiles'") == "partial"


def test_files_dry_run_writes_nothing(path, con, legacy):
    _tree(legacy)
    s = ff.import_files(path, dry_run=True, legacy_root=str(legacy))
    assert s["loaded"] > 0 and s["rows_parsed"] > 0 and s["dry_run"] is True
    assert val(con, "SELECT COUNT(*) FROM ext_file_catalog") == 0 and val(con, "SELECT COUNT(*) FROM ext_file_row") == 0
    assert val(con, "SELECT COUNT(*) FROM ops_import_run WHERE kind = 'legacy_import'") == 0


def test_whale_files_are_flagged_never_push_in_the_catalog(path, con, legacy):
    _whales(legacy)
    ff.import_files(path, legacy_root=str(legacy))
    assert val(con, "SELECT COUNT(*) FROM ext_file_catalog WHERE is_never_push = 1") == 2
    assert val(con, "SELECT COUNT(*) FROM ext_file_catalog WHERE is_never_push = 1 AND role = 'pool'") == 2


def test_write_passes_need_migration_0015(tmp_path, legacy):
    p = str(tmp_path / "old.sqlite")
    db.migrate(p, backup=False, migrations_dir=_migrations_below_15(tmp_path))
    write(legacy / "hubspot/exports/ok.csv", "name,domain\nA,a.com\n")
    with pytest.raises(RuntimeError, match="0015"):
        ff.import_files(p, legacy_root=str(legacy))
    assert ff.import_files(p, dry_run=True, legacy_root=str(legacy))["loaded"] == 1                 # a dry run works on an un-migrated database


def _migrations_below_15(tmp_path):
    d = tmp_path / "mig14"
    d.mkdir()
    for f in db.discover_migrations():
        if f.version < 15:
            shutil.copy(f.path, str(d / f.filename))
    return str(d)


# ---------------------------------------------------------------------------------------------------- link pass
def _company(con, name, domain=None, linkedin=None, strong=1):
    cid = con.execute("INSERT INTO company (canonical_name, name_norm) VALUES (?,?)", (name, name.lower())).lastrowid
    if domain:
        con.execute("INSERT INTO company_identifier (company_id, identifier_type, value_norm, is_strong) VALUES (?, 'root_domain', ?, ?)", (cid, domain, strong))
    if linkedin:
        con.execute("INSERT INTO company_identifier (company_id, identifier_type, value_norm, is_strong) VALUES (?, 'linkedin_company', ?, ?)", (cid, linkedin, strong))
    return cid


def test_link_files_uses_strong_keys_only_and_can_rerun(path, con, legacy):
    write(legacy / "hubspot/reserve/leads.csv",
          "Company,Website,LinkedIn,Contact LinkedIn,Email\n"
          "Acme,acme.com,,,x@nowhere.com\n"                                           # domain only
          "Beta,,https://linkedin.com/company/beta-co,,\n"                           # company linkedin only
          "Gamma,gamma.com,https://linkedin.com/company/gamma-co,,\n"                 # both agree
          "Delta,acme.com,https://linkedin.com/company/beta-co,,\n"                   # keys disagree -> ambiguous
          "Weak,weak.com,,,\n"                                                        # only a weak identifier exists
          "Nobody,nobody.com,,,\n"
          "Jane,,,https://www.linkedin.com/in/jane-doe,\n"
          "Mail,,,,shared@corp.com\n")
    ff.import_files(path, legacy_root=str(legacy))
    a = _company(con, "Acme", "acme.com")
    b = _company(con, "Beta", None, "company/beta-co")
    g = _company(con, "Gamma", "gamma.com", "company/gamma-co")
    _company(con, "Weak", "weak.com", strong=0)
    jane = con.execute("INSERT INTO contact (full_name, linkedin_person_norm) VALUES ('Jane', 'in/jane-doe')").lastrowid
    con.execute("INSERT INTO contact (full_name, email_norm) VALUES ('M1', 'shared@corp.com')")
    con.execute("INSERT INTO contact (full_name, email_norm) VALUES ('M2', 'shared@corp.com')")                     # ambiguous e-mail
    res = ff.link_files(path)
    rows = {json.loads(r["row_json"])["Company"]: dict(r) for r in _rows(con, "f.path LIKE '%leads.csv'")}
    assert (rows["Acme"]["company_id"], rows["Acme"]["company_link_method"]) == (a, "root_domain")
    assert (rows["Beta"]["company_id"], rows["Beta"]["company_link_method"]) == (b, "linkedin_company")
    assert (rows["Gamma"]["company_id"], rows["Gamma"]["company_link_method"]) == (g, "root_domain+linkedin_company")
    assert rows["Delta"]["company_id"] is None and rows["Weak"]["company_id"] is None and rows["Nobody"]["company_id"] is None
    assert (rows["Jane"]["contact_id"], rows["Jane"]["contact_link_method"]) == (jane, "linkedin_person") and rows["Mail"]["contact_id"] is None
    assert res["company_linked"] == 3 and res["company_ambiguous"] == 1 and res["contact_linked"] == 1 and res["contact_ambiguous"] == 0
    assert all(r["linked_at"] for r in (rows["Acme"], rows["Beta"], rows["Gamma"], rows["Jane"]))
    # re-run changes nothing
    again = ff.link_files(path)
    assert again["changed"] == 0 and again["company_linked"] == 3
    # a merge: Acme is merged into Gamma -> rows follow the survivor on the next pass
    con.execute("UPDATE company SET merged_into_company_id = ?, merged_at = '2026-10-04T00:00:00.000Z' WHERE company_id = ?", (g, a))
    ff.link_files(path)
    assert val(con, "SELECT company_id FROM ext_file_row WHERE json_extract(row_json, '$.Company') = 'Acme'") == g
    # a key that stops resolving clears the link
    con.execute("DELETE FROM company_identifier WHERE value_norm = 'company/beta-co'")
    ff.link_files(path)
    assert val(con, "SELECT company_id FROM ext_file_row WHERE json_extract(row_json, '$.Company') = 'Beta'") is None
    assert val(con, "SELECT company_link_method FROM ext_file_row WHERE json_extract(row_json, '$.Company') = 'Beta'") is None


def test_link_dry_run_and_whale_company_fill(path, con, legacy):
    _whales(legacy)
    ff.import_whales(path, legacy_root=str(legacy))
    ff.import_files(path, legacy_root=str(legacy))
    acme = _company(con, "Acme Software", "acme-soft.com")
    before = con.total_changes
    d = ff.link_files(path, dry_run=True)
    assert d["company_linked"] >= 1 and val(con, "SELECT COUNT(*) FROM ext_file_row WHERE company_id IS NOT NULL") == 0
    assert val(con, "SELECT COUNT(*) FROM suppression WHERE company_id IS NOT NULL") == 0
    del before
    r = ff.link_files(path)
    assert r["suppression_company_filled"] == 1 and val(con, "SELECT company_id FROM suppression WHERE kind='never_push' AND match_value_norm='acme-soft.com'") == acme
    assert val(con, "SELECT COUNT(*) FROM ext_file_row WHERE company_id = ?", acme) >= 1
    # linking never weakens the block
    with pytest.raises(suppression.SuppressedError):
        suppression.assert_pushable(con, domain="acme-soft.com")


# ---------------------------------------------------------------------------------------------------- CLI / PII
def test_cli_prints_counts_only_never_cell_values(path, legacy, capsys):
    write(legacy / "hubspot/reserve/pii.csv", "Company,Email,Mobile,Contact LinkedIn\nSecretCo Ltd,private.person@secretco-example.com,98765 43210,https://linkedin.com/in/private-person\n")
    write(legacy / "hubspot/reserve/whale_list_27_NEVER_PUSH_TO_HUBSPOT.csv", WHALE_HEADER + "1,9,Whalecorp Pvt,whalecorp-example.com,Pune,,,whale.boss@whalecorp-example.com,\n")
    rc = ff.main(["--db", path, "--only", "all", "--full", "--legacy-root", str(legacy)])
    out = capsys.readouterr()
    assert rc == 0
    blob = out.out + out.err
    for secret in ("secretco", "private.person", "98765", "private-person", "whale.boss", "whalecorp"):
        assert secret not in blob.lower()
    assert json.loads(out.out)["files"]["loaded"] >= 1
    rc = ff.main(["--db", path, "--only", "files", "--dry-run", "--legacy-root", str(legacy)])
    assert rc == 0 and "dry_run" in capsys.readouterr().out


def test_cli_refuses_a_missing_database(tmp_path, legacy, capsys):
    rc = ff.main(["--db", str(tmp_path / "nope.sqlite"), "--only", "files", "--legacy-root", str(legacy)])
    assert rc == 2 and not (tmp_path / "nope.sqlite").exists()


# ---------------------------------------------------------------------------------------------------- unit helpers
def test_helpers_encoding_delimiter_domain_and_keys(tmp_path):
    assert ff.detect_delimiter('a;b;"c;d"\n1;2;3') == ";" and ff.detect_delimiter("a\tb\tc") == "\t" and ff.detect_delimiter("only") == ","
    f = tmp_path / "x.bin"
    f.write_bytes("café".encode("cp1252"))
    assert ff.sha256_and_encoding(str(f), True)[1] == "cp1252"
    f.write_bytes(b"ab\x81cd")
    assert ff.sha256_and_encoding(str(f), True)[1] == "latin-1"                      # 0x81 is undefined in cp1252
    f.write_bytes("café".encode("utf-8"))
    assert ff.sha256_and_encoding(str(f), True)[1] == "utf-8"
    assert ff.domain_of("https://WWW.Foo.com/x?y") == "foo.com" and ff.domain_of("https://in.linkedin.com/company/x") is None
    assert ff.domain_of("a@gmail.com") is None and ff.domain_of("gmail.com") is None and ff.domain_of("N/A") is None and ff.domain_of("foo.com bar.com") == "foo.com"
    cols = ff.Columns(["Company Name", "Company Website", "linkedin_url", "Email Status", "Work Email", "phone_is_indian_mobile", "Phone Number", "Name"])
    assert cols.domain == ["Company Website"] and cols.email == ["Work Email"] and cols.phone == ["Phone Number"] and cols.name == ["Company Name"]
    recs, meta = ff.json_records({"a": 1, "rows": [{"x": 1}, {"x": 2}]})
    assert recs == [{"x": 1}, {"x": 2}] and meta == {"a": 1}
    assert ff.json_records(["a", {"k": 1}])[0] == [{"_value": "a"}, {"k": 1}]


# ---------------------------------------------------------------------------------------------------- the real legacy tree (read only)
@pytest.mark.skipif(not os.path.isdir(os.path.join(ROOT, "legacy", "RapidActionTeam", "snapshots")), reason="legacy/ not present")
def test_real_legacy_snapshots_and_whale_files_are_found():
    descs, other = ff.discover_snapshots(os.path.join(ROOT, "legacy"))
    fam = {}
    for d in descs:
        fam[d["family"]] = fam.get(d["family"], 0) + 1
    assert fam["rat"] == 5 and fam["main_full"] == 27 and fam["main_coding"] == 16 and fam["main_coopsglobal"] == 7 and fam["main_owner"] == 135
    assert fam["main_hubspot_mirror"] == 1 and fam["companyops_cluster"] == 50 and other == []
    parsed = [ff.parse_snapshot(d) for d in descs]
    assert all(p["day"] and p["payload"] for p in parsed) and not any(p["issues"] for p in parsed)
    names = {os.path.basename(f) for f in ff.find_whale_files(os.path.join(ROOT, "legacy"))}
    assert {"whales.csv", "Whale_List_30.csv", "Whale_List_Untouched_FINAL.csv", "whale_list_27_NEVER_PUSH_TO_HUBSPOT.csv"} <= names
