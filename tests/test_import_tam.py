"""Tests for leadgen.legacy_import.tam on a tiny fixture copy of the tam.sqlite3 schema (never the real database, never the real source file).

Run:  .venv/bin/python -m pytest tests/test_import_tam.py --basetemp=db/_scratch/pytest
"""
import hashlib
import json
import os
import shutil
import sqlite3

import pytest

from leadgen import db
from leadgen.legacy_import import tam

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL_DB = os.path.join(ROOT, "db", "leadgen.sqlite")

SOURCE_DDL = """
CREATE TABLE async_jobs (id INTEGER PRIMARY KEY, vendor TEXT, request_id TEXT, payload TEXT, status TEXT DEFAULT 'pending', attempts INT DEFAULT 0,
  created_at TEXT DEFAULT (datetime('now')), resolved_at TEXT);
CREATE TABLE categories (id INTEGER PRIMARY KEY, key TEXT UNIQUE NOT NULL, name TEXT NOT NULL, status TEXT DEFAULT 'active', hubspot_segment TEXT,
  config_path TEXT, created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE category_sources (category_id INT NOT NULL REFERENCES categories(id), source TEXT NOT NULL, tier TEXT, status TEXT, access_method TEXT, url TEXT,
  robots_status TEXT, blocked_reason TEXT, rows_total INT DEFAULT 0, rows_new_total INT DEFAULT 0, attempts INT DEFAULT 0, last_attempt_at TEXT,
  last_success_at TEXT, saturated INT DEFAULT 0, notes TEXT, updated_at TEXT DEFAULT (datetime('now')), PRIMARY KEY (category_id, source));
CREATE TABLE classifications (company_id INT NOT NULL REFERENCES companies(id), category_id INT NOT NULL REFERENCES categories(id), keyword_score REAL,
  keyword_hits TEXT, segments TEXT, primary_segment TEXT, icp_bucket TEXT CHECK (icp_bucket IN ('Fit','Maybe','Out')), confidence REAL,
  has_own_tech_product INT, india_hq TEXT, company_type TEXT, evidence_quote TEXT, evidence_url TEXT, pm_tooling_signals TEXT, notes TEXT, model TEXT,
  prompt_version TEXT, classified_at TEXT DEFAULT (datetime('now')), PRIMARY KEY (company_id, category_id));
CREATE TABLE companies (id INTEGER PRIMARY KEY, root_domain TEXT UNIQUE, linkedin_url TEXT, name TEXT NOT NULL, name_norm TEXT, alt_domains TEXT,
  hq_city TEXT, hq_state TEXT, hq_country TEXT, india_hq TEXT CHECK (india_hq IN ('yes','no','unknown')) DEFAULT 'unknown', employee_count INT,
  headcount_band TEXT, founded_year INT, funding_stage TEXT, total_funding_usd REAL, office_phone TEXT, google_place_id TEXT, apollo_org_id TEXT,
  site_status TEXT, final_url TEXT, first_seen_at TEXT DEFAULT (datetime('now')), last_seen_at TEXT
, legal_name TEXT, cin TEXT, domain_confidence REAL, merged_into INT REFERENCES companies(id));
CREATE TABLE company_categories (company_id INT NOT NULL REFERENCES companies(id), category_id INT NOT NULL REFERENCES categories(id), segment TEXT,
  icp_bucket TEXT CHECK (icp_bucket IN ('Fit','Maybe','Out')), confidence REAL, score REAL, priority_tier TEXT, first_seen_at TEXT DEFAULT (datetime('now')),
  updated_at TEXT, regulated_status TEXT, regulatory_sensitivity TEXT, parked_as TEXT, funnel_stage TEXT, funnel_bucket TEXT, funnel_reason TEXT,
  funnel_updated_at TEXT, PRIMARY KEY (company_id, category_id));
CREATE TABLE contacts (id INTEGER PRIMARY KEY, company_id INT REFERENCES companies(id), apollo_person_id TEXT UNIQUE, full_name TEXT, first_name TEXT,
  last_name TEXT, title TEXT, seniority TEXT, role_rank INT, linkedin_url TEXT, email TEXT, email_status TEXT, email_verified_at TEXT, phone_mobile TEXT,
  phone_other TEXT, phone_source TEXT, in_salesnav INT DEFAULT 0, enrich_state TEXT DEFAULT 'found', created_at TEXT DEFAULT (datetime('now')), updated_at TEXT
, category_id INT, funnel_stage TEXT, funnel_bucket TEXT, funnel_reason TEXT, funnel_updated_at TEXT, email_bucket TEXT, phone_bucket TEXT);
CREATE TABLE cost_ledger (id INTEGER PRIMARY KEY, run_id INT, category_id INT REFERENCES categories(id), vendor TEXT, unit TEXT, qty REAL, usd_est REAL,
  note TEXT, at TEXT DEFAULT (datetime('now')));
CREATE TABLE crm_pushes (id INTEGER PRIMARY KEY, company_id INT REFERENCES companies(id), category_id INT REFERENCES categories(id), crm TEXT DEFAULT 'hubspot',
  deal_id TEXT, contact_id TEXT, owner TEXT, pipeline TEXT, dealstage TEXT, segment TEXT, status TEXT DEFAULT 'pushed', note TEXT,
  pushed_at TEXT DEFAULT (datetime('now')));
CREATE TABLE funnel_events (id INTEGER PRIMARY KEY, category_id INT REFERENCES categories(id), entity_type TEXT NOT NULL CHECK (entity_type IN ('company','contact')),
  entity_id INT NOT NULL, stage TEXT NOT NULL, bucket TEXT NOT NULL, reason TEXT, source TEXT, run_id INT REFERENCES stage_runs(id), at TEXT DEFAULT (datetime('now')));
CREATE TABLE outreach_batches (id INTEGER PRIMARY KEY, batch_no INT, contact_id INT REFERENCES contacts(id), channel TEXT, created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE "pages" (id INTEGER PRIMARY KEY, company_id INT REFERENCES companies(id), url TEXT, page_type TEXT, title TEXT, text TEXT, markdown TEXT,
  http_status INT, text_hash TEXT, crawled_at TEXT DEFAULT (datetime('now')), UNIQUE(company_id, url));
CREATE TABLE raw_sources (id INTEGER PRIMARY KEY, category_id INT REFERENCES categories(id), source TEXT NOT NULL, source_ref TEXT, run_id INT REFERENCES stage_runs(id),
  company_name TEXT, website TEXT, root_domain TEXT, linkedin_url TEXT, city TEXT, state TEXT, country TEXT, phone TEXT, segment_hint TEXT, signal_text TEXT,
  payload_json TEXT, fetched_at TEXT DEFAULT (datetime('now')), company_id INT REFERENCES companies(id), UNIQUE(source, source_ref, company_name, website));
CREATE TABLE salesnav_import (id INTEGER PRIMARY KEY, file TEXT, company_name TEXT, company_linkedin_url TEXT, website TEXT, root_domain TEXT, person_name TEXT,
  person_title TEXT, person_linkedin_url TEXT, imported_at TEXT DEFAULT (datetime('now')));
CREATE TABLE signalhire_results (item TEXT PRIMARY KEY, status TEXT, mobile TEXT, email TEXT, raw TEXT, received_at TEXT DEFAULT (datetime('now')));
CREATE TABLE signals (company_id INT PRIMARY KEY REFERENCES companies(id), mx_provider TEXT, mx_records TEXT, techs TEXT, uses_jira INT, uses_slack INT,
  uses_asana INT, uses_clickup INT, uses_gworkspace INT, jira_scrum_mentions INT, open_jobs INT, updated_at TEXT DEFAULT (datetime('now')));
CREATE TABLE stage_runs (id INTEGER PRIMARY KEY, category_id INT REFERENCES categories(id), stage TEXT, source TEXT, args TEXT, started_at TEXT DEFAULT (datetime('now')),
  finished_at TEXT, status TEXT, rows_in INT, rows_new INT, rows_seen INT, saturated INT DEFAULT 0, error TEXT);
"""

T = "2026-09-28 11:14:26"

# company columns: id, root_domain, linkedin_url, name, name_norm, hq_country, india_hq, apollo_org_id, domain_confidence, merged_into
COMPANIES = [
    (1, "acme.com", "https://www.linkedin.com/company/'Acme-Inc'/", "Acme Inc", "acme inc", "India", "yes", None, 1.0, None),
    (2, '"acme.com', None, '"acme.com', '"acme.com', "IN", "yes", None, 0.8, None),                          # quoted spelling of company 1's domain
    (3, None, None, "Acme Old", "acme old", None, "unknown", None, None, 1),                                  # tombstone -> 1
    (4, "​beta.io", "https://www.linkedin.com/in/some-person", "Beta", "beta", "IN", "yes", None, 1.0, None),   # zero-width + person URL as company URL
    (5, None, None, "Name Only Ltd", "name only ltd", "Narnia", "unknown", None, None, None),
    (6, "gamma.in", None, "Gamma", "gamma", "India", "yes", "abc123", 1.0, None),
    (7, None, None, "Acme Older", "acme older", None, "unknown", None, None, 3),                              # tombstone chain 7 -> 3 -> 1
]


def build_source(path, orphan=False, extra_signal_rowids=True):
    con = sqlite3.connect(path)
    con.executescript(SOURCE_DDL)
    con.executemany("INSERT INTO categories (id, key, name, status, hubspot_segment, created_at) VALUES (?, ?, ?, 'active', ?, ?)",
                    [(1, "fintech", "Fintech", "Fintech", T), (2, "edtech", "Ed-Tech", "EdTech", T)])
    con.execute("INSERT INTO stage_runs (id, category_id, stage, source, status, started_at) VALUES (1, 1, 'discover', 'sebi', 'ok', ?)", (T,))
    con.executemany("INSERT INTO companies (id, root_domain, linkedin_url, name, name_norm, hq_country, india_hq, apollo_org_id, domain_confidence, merged_into, "
                    "first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [c + (T, "2026-09-29 12:00:00") for c in COMPANIES])
    con.executemany(
        "INSERT INTO company_categories (company_id, category_id, segment, icp_bucket, confidence, score, priority_tier, first_seen_at, updated_at, funnel_stage, "
        "funnel_bucket, funnel_reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(1, 1, "WEALTH", "Fit", 0.9, 75.0, "A", T, "2026-09-29 12:44:55", "crm_push", "pushed", None),
         (4, 1, "LEND", "Out", 0.7, 20.0, "C", T, "2026-09-29 12:44:55", "classify", "out_not_category", "not our category"),
         (5, 2, None, None, None, 12.0, "D", T, None, "score", "scored", '{"_unjudged": "scored", "india_hq": 10}'),
         (2, 1, "WEALTH", "Maybe", 0.5, 45.0, "C", T, None, "classify", "maybe", None),                       # second verdict for the same golden company + vertical
         (6, 2, "K12", "Maybe", 0.6, 40.0, "C", T, None, "classify", "maybe", None)])
    con.executemany("INSERT INTO classifications (company_id, category_id, keyword_score, primary_segment, icp_bucket, confidence, evidence_quote, evidence_url, notes, model, "
                    "prompt_version, classified_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [(1, 1, 3.0, "WEALTH", "Fit", 0.9, "we manage wealth", "https://acme.example/about", "wealth platform", "claude-subagent", "v1", "2026-09-29 10:00:00"),
                     (4, 1, 1.0, "LEND", "Out", 0.7, None, None, "not a fintech", None, None, "2026-09-29 11:00:00"),
                     (5, 2, 0.5, None, None, None, None, None, None, None, None, "2026-09-29 11:00:00")])
    sig = "INSERT INTO signals (%scompany_id, mx_provider, uses_jira, uses_slack, jira_scrum_mentions, open_jobs, updated_at) VALUES (%s?, ?, ?, ?, ?, ?, ?)"
    if extra_signal_rowids:
        con.execute(sig % ("rowid, ", "?, "), (10, 1, "google_workspace", 1, 0, 3, 5, "2026-09-28 12:52:09"))
        con.execute(sig % ("rowid, ", "?, "), (20, 6, None, None, None, 0, 0, "2026-09-28 12:53:09"))
    else:
        con.execute(sig % ("", ""), (1, "google_workspace", 1, 0, 3, 5, "2026-09-28 12:52:09"))
    con.executemany("INSERT INTO cost_ledger (id, run_id, category_id, vendor, unit, qty, usd_est, note, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [(1, 1, 1, "apollo", "org_search_page", 1.0, 0.0, "fintech|11,50|p1", "2026-09-28 12:13:42"),
                     (2, None, None, "apollo", "phone_reveal", 8.0, 0.0, None, "2026-09-28 12:14:42"),
                     (3, None, 2, "apollo", "org_enrich", 10.0, 0.05, "headcount x10", "2026-09-28 12:15:42")])
    con.executemany("INSERT INTO crm_pushes (id, company_id, category_id, crm, deal_id, contact_id, pipeline, status, note, pushed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [(1, 1, 1, "hubspot", "5001", None, "2464812771", "pushed", "Acme", T),
                     (2, 4, 1, "hubspot", "5002", None, "default", "removed_off_icp", "Beta", T),
                     (3, None, 1, "hubspot", "5003", None, None, "pushed", "Old Name Co", T),
                     (4, 6, 2, "hubspot", "5004", None, "default", "contact_corrected", "Gamma", T)]
                    + ([(5, 999, 1, "hubspot", "5005", None, "default", "pushed", "Orphan", T)] if orphan else []))
    con.executemany("INSERT INTO funnel_events (id, category_id, entity_type, entity_id, stage, bucket, reason, source, run_id, at) VALUES (?, ?, 'company', ?, ?, ?, ?, ?, ?, ?)",
                    [(1, 1, 1, "score", "scored", None, "score", 1, T), (2, 1, 4, "classify", "out_not_category", "x", "classify", 1, T)])
    con.execute("INSERT INTO pages (id, company_id, url, page_type, title, text, crawled_at) VALUES (1, 1, 'https://acme.example', 'home', 'Acme', 'hello é中', ?)", (T,))
    con.execute("INSERT INTO raw_sources (id, category_id, source, source_ref, run_id, company_name, root_domain, company_id, fetched_at) VALUES (1, 1, 'sebi', 'r1', 1, 'Acme', 'acme.com', 1, ?)", (T,))
    con.execute("INSERT INTO signalhire_results (item, status, raw) VALUES ('https://www.linkedin.com/in/x', 'success', '{}')")
    con.execute("INSERT INTO category_sources (category_id, source, tier, status) VALUES (1, 'sebi', 'free', 'ok')")
    con.commit()
    con.close()


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        h.update(fh.read())
    return h.hexdigest()


@pytest.fixture(scope="module")
def template_db(tmp_path_factory):
    p = str(tmp_path_factory.mktemp("tpl") / "template.sqlite")
    db.migrate(p)
    return p


@pytest.fixture
def paths(tmp_path, template_db):
    target = str(tmp_path / "t.sqlite")
    assert os.path.abspath(target) != REAL_DB
    shutil.copy(template_db, target)
    src = str(tmp_path / "tam.sqlite3")
    build_source(src)
    return target, src


def counts(target, tables):
    c = sqlite3.connect(target)
    try:
        return {t: c.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0] for t in tables}
    finally:
        c.close()


def q(target, sql, *args):
    c = sqlite3.connect(target)
    try:
        return c.execute(sql, args).fetchall()
    finally:
        c.close()


# ------------------------------------------------------------------------------------------------ verbatim
def test_verbatim_loads_every_table_exactly_and_is_idempotent(paths):
    target, src = paths
    before = sha(src)
    rep = tam.run(target, src, only="verbatim")["stages"]["verbatim"]
    assert set(rep["tables"]) == set(tam.LOAD_ORDER) and len(rep["tables"]) == 17
    sc = sqlite3.connect(src)
    for t, e in rep["tables"].items():
        n = sc.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
        assert e["source_rows"] == e["loaded_rows"] == n, t
        assert q(target, "SELECT COUNT(*) FROM legacy_tam_%s" % t)[0][0] == n
        assert q(target, "SELECT COUNT(*) FROM legacy_tam_%s WHERE _import_run_id IS NULL" % t)[0][0] == 0
    assert rep["loaded"] == sum(e["source_rows"] for e in rep["tables"].values())
    # values are identical, including rowid of tables whose key is not a rowid alias (signals: INT PRIMARY KEY)
    assert q(target, "SELECT rowid, company_id FROM legacy_tam_signals ORDER BY rowid") == [(10, 1), (20, 6)]
    assert q(target, "SELECT text FROM legacy_tam_pages")[0][0] == "hello é中"
    assert q(target, "SELECT root_domain FROM legacy_tam_companies WHERE id = 4")[0][0] == "​beta.io"      # verbatim: nothing normalised
    # second run: nothing duplicated, every table skipped
    rep2 = tam.run(target, src, only="verbatim")["stages"]["verbatim"]
    assert rep2["loaded"] == 0 and rep2["skipped_tables"] == 17
    assert q(target, "SELECT COUNT(*) FROM legacy_tam_companies")[0][0] == len(COMPANIES)
    runs = q(target, "SELECT status, source_name, rows_written, rows_skipped FROM ops_import_run WHERE source_name = 'tam' ORDER BY import_run_id")
    assert [r[0] for r in runs] == ["succeeded", "succeeded"] and runs[0][2] == rep["loaded"] and runs[1][3] == rep["loaded"]
    assert sha(src) == before, "the legacy source must never be modified"


def test_verbatim_resumes_an_unfinished_table_without_duplicates(paths):
    target, src = paths
    tam.run(target, src, only="verbatim")
    c = sqlite3.connect(target)
    c.execute("DELETE FROM legacy_tam_companies WHERE id > 4")                                           # simulate a crash half way through one table
    c.execute("UPDATE ops_sync_state SET last_status = 'running', cursor_value = NULL WHERE object_type = 'companies' AND scope = 'tam'")
    c.commit()
    c.close()
    rep = tam.run(target, src, only="verbatim")["stages"]["verbatim"]
    assert rep["tables"]["companies"]["action"] == "loaded" and rep["loaded"] == len(COMPANIES)
    assert rep["skipped_tables"] == 16
    assert q(target, "SELECT COUNT(*), COUNT(DISTINCT id) FROM legacy_tam_companies") == [(len(COMPANIES), len(COMPANIES))]
    # --reload-verbatim forces a delete-and-reload even when complete
    rep = tam.run(target, src, only="verbatim", reload=True)["stages"]["verbatim"]
    assert rep["skipped_tables"] == 0 and q(target, "SELECT COUNT(*) FROM legacy_tam_categories")[0][0] == 2


def test_verbatim_keeps_foreign_key_orphans_and_reports_them(tmp_path, template_db):
    target = str(tmp_path / "t.sqlite")
    shutil.copy(template_db, target)
    src = str(tmp_path / "tam.sqlite3")
    build_source(src, orphan=True)
    rep = tam.run(target, src, only="verbatim")["stages"]["verbatim"]
    assert rep["tables"]["crm_pushes"]["loaded_rows"] == 5 and rep["fk_orphans"] == 1
    assert q(target, "SELECT COUNT(*) FROM ops_dq_issue WHERE rule_code = 'legacy_fk_orphan'")[0][0] == 1
    c = db.connect(target)
    try:
        assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        c.close()


def test_verbatim_refuses_a_target_without_migration_0006(tmp_path):
    src = str(tmp_path / "tam.sqlite3")
    build_source(src)
    empty = str(tmp_path / "empty.sqlite")
    sqlite3.connect(empty).close()
    with pytest.raises(tam.ImportErrorTam):
        tam.run(empty, src, only="verbatim")


# ------------------------------------------------------------------------------------------------ canonical
def test_canonical_mapping(paths):
    target, src = paths
    tam.run(target, src, only="verbatim")
    rep = tam.run(target, src, only="canonical")["stages"]["canonical"]
    # companies: 7 tam rows, 6 golden (company 2 is the quoted spelling of company 1's domain -> folded, never a second golden)
    assert rep["companies_created"] == 6 and rep["companies_folded_into_earlier_tam_row"] == 1
    assert q(target, "SELECT COUNT(*) FROM company")[0][0] == 6
    g = {r[0]: r[1] for r in q(target, "SELECT source_pk, entity_id FROM origin_ref WHERE entity_type = 'company' AND source_table = 'companies'")}
    assert len(g) == 7 and g["1"] == g["2"] and len({g[k] for k in g}) == 6
    # identifiers are normalised; unnormalisable ones are not loaded un-normalised
    idents = q(target, "SELECT identifier_type, value_norm, is_strong, is_primary FROM company_identifier ORDER BY identifier_type, value_norm")
    assert ("root_domain", "acme.com", 1, 1) in idents and ("root_domain", "beta.io", 1, 1) in idents and ("root_domain", "gamma.in", 1, 1) in idents
    assert ("linkedin_company", "company/acme-inc", 1, 1) in idents and ("apollo_org", "abc123", 1, 1) in idents
    assert not any(i[0] == "linkedin_company" and "in/" in i[1] for i in idents)
    assert q(target, "SELECT hq_country, india_hq FROM company WHERE company_id = ?", g["1"]) == [("IN", "yes")]
    assert q(target, "SELECT hq_country FROM company WHERE company_id = ?", g["5"]) == [(None,)]
    # tombstones kept and pointed at their parent (chain 7 -> 3 -> 1)
    assert q(target, "SELECT merged_into_company_id FROM company WHERE company_id = ?", g["3"]) == [(g["1"],)]
    assert q(target, "SELECT merged_into_company_id FROM company WHERE company_id = ?", g["7"]) == [(g["3"],)]
    assert q(target, "SELECT survivor_company_id FROM v_company_survivor WHERE company_id = ?", g["7"]) == [(g["1"],)]
    # verticals
    assert q(target, "SELECT slug, hubspot_segment, country_code FROM vertical ORDER BY slug") == [("edtech", "EdTech", "IN"), ("fintech", "Fintech", "IN")]
    # verdicts: every row kept, rejects included, unscored when the source had no bucket, duplicate key not current
    v = q(target, "SELECT icp_bucket, native_bucket, is_current, verdict_method, reason_code FROM tam_company_verdict ORDER BY verdict_id")
    assert len(v) == 5
    assert sorted(x[0] for x in v) == ["fit", "maybe", "maybe", "out", "unscored"]
    assert ("out", "Out", 1, "llm", "out_not_category") in v and ("unscored", None, 1, "rules", "scored") in v
    assert [x[2] for x in v].count(0) == 1
    out = q(target, "SELECT reason, evidence_quote, score, priority_tier FROM tam_company_verdict WHERE icp_bucket = 'out'")
    assert out == [("not our category", None, 20.0, "C")]
    d = json.loads(q(target, "SELECT details_json FROM tam_company_verdict WHERE icp_bucket = 'fit'")[0][0])
    assert d["funnel_stage"] == "crm_push" and d["classification"]["notes"] == "wealth platform" and d["tam"] == {"category_id": 1, "company_id": 1, "first_seen_at": T}
    d = json.loads(q(target, "SELECT details_json FROM tam_company_verdict WHERE icp_bucket = 'unscored'")[0][0])
    assert d["score_components"] == {"_unjudged": "scored", "india_hq": 10}
    # signals (EAV; NULL values skipped)
    assert q(target, "SELECT signal_code, value_bool, value_num, value_text FROM company_signal WHERE company_id = ? ORDER BY signal_code", g["1"]) == [
        ("jira_scrum_mentions", None, 3.0, None), ("mx_provider", None, None, "google_workspace"), ("open_jobs", None, 5.0, None),
        ("uses_jira", 1, None, None), ("uses_slack", 0, None, None)]
    # costs: usd_est 0 -> unknown NULL (never 0); phone_reveal qty = calls, credits = 8 per call
    cl = q(target, "SELECT unit, qty, credits, usd, usd_basis, vertical_id IS NOT NULL, run_ref FROM cost_ledger ORDER BY cost_id")
    assert cl[0] == ("org_search_page", 1.0, 1.0, None, "unknown", 1, "stage_run:1")
    assert cl[1] == ("phone_reveal", 1.0, 8.0, None, "unknown", 0, None)
    assert cl[2] == ("org_enrich", 10.0, 10.0, 0.05, "estimated", 1, None)
    # suppression from push statuses (pinned to companyops); contact_corrected has none
    s = q(target, "SELECT kind, match_type, match_value_norm, account_id FROM suppression ORDER BY kind, match_value_norm")
    assert ("delivered", "domain", "acme.com", "companyops") in s and ("off_icp", "domain", "beta.io", "companyops") in s
    assert ("delivered", "company_name", "old name co", "companyops") in s and len(s) == 3
    # provenance: every canonical row has an origin, nothing is orphaned
    cov = {r[0]: r[2] for r in q(target, "SELECT * FROM v_origin_coverage")}
    assert cov["company"] == 0 and cov["tam_company_verdict"] == 0 and cov["cost_ledger"] == 0 and cov["suppression"] == 0
    assert q(target, "SELECT COUNT(*) FROM origin_ref WHERE entity_type = 'vertical'")[0][0] == 2
    # data-quality findings are aggregated, ids only
    rules = {r[0]: json.loads(r[1]) for r in q(target, "SELECT rule_code, details_json FROM ops_dq_issue WHERE fingerprint LIKE 'tam_import|%'")}
    assert rules["tam_linkedin_unnormalisable"]["count"] == 1 and rules["tam_country_unmapped"]["count"] == 1
    assert rules["tam_rows_share_strong_identifier"]["sample_source_ids"] == ["2"]
    assert rules["tam_company_without_strong_identifier"]["count"] == 1
    assert q(target, "SELECT COUNT(*) FROM tam_company_verdict v JOIN company c USING (company_id)")[0][0] == 5      # FKs resolve


def test_canonical_is_idempotent(paths):
    target, src = paths
    tables = ["company", "company_identifier", "vertical", "tam_company_verdict", "company_signal", "cost_ledger", "suppression", "origin_ref", "merge_candidate"]
    tam.run(target, src, only="canonical")
    first = counts(target, tables)
    rep = tam.run(target, src, only="canonical")["stages"]["canonical"]
    assert counts(target, tables) == first
    assert rep["companies_already_imported"] == 7 and rep["verdicts_already_imported"] == 5 and rep["costs_already_imported"] == 3
    assert not rep.get("companies_created")


def test_canonical_links_to_a_company_that_already_exists_by_strong_key(paths):
    target, src = paths
    c = db.connect(target)
    with db.transaction(c):
        cid = c.execute("INSERT INTO company (canonical_name, name_norm) VALUES ('Gamma Technologies', 'gamma technologies')").lastrowid
        c.execute("INSERT INTO company_identifier (company_id, identifier_type, value_norm, source_system) VALUES (?, 'root_domain', 'gamma.in', 'hubspot')", (cid,))
        # a second company holds beta's LinkedIn slug: strong-key conflict inside one tam row must not overwrite, but queue a human merge
        other = c.execute("INSERT INTO company (canonical_name, name_norm) VALUES ('Beta Other', 'beta other')").lastrowid
        c.execute("INSERT INTO company_identifier (company_id, identifier_type, value_norm, source_system) VALUES (?, 'apollo_org', 'abc123', 'hubspot')", (other,))
    c.close()
    rep = tam.run(target, src, only="canonical")["stages"]["canonical"]
    assert rep["companies_linked_to_existing"] == 1
    g = dict(q(target, "SELECT source_pk, entity_id FROM origin_ref WHERE entity_type = 'company' AND source_table = 'companies'"))
    assert g["6"] == cid                                                                                # gamma.in matched the existing golden company
    assert q(target, "SELECT is_strong FROM company_identifier WHERE company_id = ? AND identifier_type = 'apollo_org'", cid) == [(0,)]   # conflicting key kept as weak evidence
    mc = q(target, "SELECT left_id, right_id, match_kind FROM merge_candidate")
    assert mc == [(min(cid, other), max(cid, other), "strong_identifier_conflict")]
    assert q(target, "SELECT COUNT(*) FROM company")[0][0] == 2 + 5                                       # 7 tam rows - 1 folded - 1 matched = 5 new


# ------------------------------------------------------------------------------------------------ link pass
def _seed_deals(target, spec):
    c = db.connect(target)
    with db.transaction(c):
        c.execute("INSERT INTO stage (account_id, pipeline_id, stage_id, label) VALUES ('companyops', 'default', 's1', 'S1')")
        for hs, company in spec:
            c.execute("INSERT INTO deal (account_id, hs_deal_id, pipeline_id, stage_id, company_id) VALUES ('companyops', ?, 'default', 's1', ?)", (hs, company))
    c.close()


def test_link_pass_is_separable_logs_unmatched_and_resolves_on_rerun(paths):
    target, src = paths
    tam.run(target, src, only="verbatim")
    tam.run(target, src, only="canonical")
    g = dict(q(target, "SELECT source_pk, entity_id FROM origin_ref WHERE entity_type = 'company' AND source_table = 'companies'"))
    # 1st pass: HubSpot sync has not produced the deals yet -> every push id is logged, nothing dropped, no deal is written
    rep = tam.run(target, src, only="link")["stages"]["link"]
    assert rep["pushes"] == 4 and rep["unmatched_deal_ids"] == 4 and rep["matched_deals"] == 0
    assert q(target, "SELECT COUNT(*) FROM ops_dq_issue WHERE rule_code = 'tam_push_unmatched_deal' AND status = 'open'")[0][0] == 4
    assert q(target, "SELECT COUNT(*) FROM origin_ref WHERE source_table = 'crm_pushes' AND entity_type IN ('deal', 'company')")[0][0] == 0
    # deals appear later: 5001 agrees with the tam company, 5002 belongs to a different golden company, 5003 has no company
    _seed_deals(target, [("5001", g["1"]), ("5002", g["6"]), ("5003", None)])
    rep = tam.run(target, src, only="link")["stages"]["link"]
    assert rep["matched_deals"] == 3 and rep["unmatched_deal_ids"] == 1
    assert rep["company_agrees"] == 1 and rep["company_conflicts_merge_candidates"] == 1 and rep.get("deal_company_unknown", 0) == 0 and rep["push_company_unknown"] == 1 and rep["deal_without_any_company"] == 1
    assert q(target, "SELECT status FROM ops_dq_issue WHERE fingerprint = 'tam_push_unmatched_deal|1'") == [("resolved",)]
    assert q(target, "SELECT status FROM ops_dq_issue WHERE fingerprint = 'tam_push_unmatched_deal|4'") == [("open",)]
    assert q(target, "SELECT COUNT(*) FROM origin_ref WHERE source_table = 'crm_pushes' AND entity_type = 'deal'")[0][0] == 3
    assert q(target, "SELECT match_kind, left_id, right_id FROM merge_candidate") == [("tam_crm_push_deal", min(g["4"], g["6"]), max(g["4"], g["6"]))]
    # re-running changes nothing
    before = counts(target, ["origin_ref", "merge_candidate", "deal"])
    tam.run(target, src, only="link")
    assert counts(target, ["origin_ref", "merge_candidate", "deal"]) == before
    # the deal table itself is never written by the link pass
    assert q(target, "SELECT company_id FROM deal WHERE hs_deal_id = '5003'") == [(None,)]


# ------------------------------------------------------------------------------------------------ CLI / dry run
def test_dry_run_writes_nothing(paths, capsys):
    target, src = paths
    tam.run(target, src, only="verbatim")                              # have some state so the dry run has something to look at
    tables = [t for t, in q(target, "SELECT name FROM sqlite_master WHERE type = 'table'")]
    before = counts(target, tables)
    sha_src = sha(src)
    rep = tam.run(target, src, dry_run=True)
    assert counts(target, tables) == before
    assert sha(src) == sha_src
    assert rep["stages"]["verbatim"]["total_source_rows"] == sum(before["legacy_tam_" + t] for t in tam.LOAD_ORDER)
    assert rep["stages"]["verbatim"]["tables"]["companies"]["target"].startswith("already complete")
    assert rep["stages"]["canonical"]["companies_created"] == 6 and rep["stages"]["canonical"]["verdicts_created"] == 5
    assert rep["stages"]["link"]["pushes"] == 4
    rc = tam.main(["--db", target, "--source", src, "--dry-run", "-q"])
    out = capsys.readouterr().out
    assert rc == 0 and "DRY RUN" in out and "acme" not in out.lower()           # counts only: no company values in the output
    assert counts(target, tables) == before


def test_cli_only_flag_and_report_json(paths, tmp_path, capsys):
    target, src = paths
    rj = str(tmp_path / "r.json")
    assert tam.main(["--db", target, "--source", src, "--only", "verbatim", "-q", "--report-json", rj]) == 0
    assert "== verbatim" in capsys.readouterr().out
    assert json.load(open(rj))["stages"]["verbatim"]["loaded"] > 0
    assert q(target, "SELECT COUNT(*) FROM company")[0][0] == 0                  # --only verbatim does not touch the canonical layer
    assert tam.main(["--db", target, "--source", str(tmp_path / "missing.sqlite3"), "--only", "verbatim", "-q"]) == 2


def test_funnel_events_are_not_deal_stage_events(paths):
    target, src = paths
    tam.run(target, src, only="verbatim")
    tam.run(target, src, only="canonical")
    assert q(target, "SELECT COUNT(*) FROM deal_stage_event")[0][0] == 0
    assert q(target, "SELECT COUNT(*) FROM legacy_tam_funnel_events")[0][0] == 2
