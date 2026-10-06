"""Tests for leadgen.legacy_import.hubspot_repo_dbs (verbatim mirrors + canonical mapping of the eight hubspot-repo SQLite databases).

Everything runs on tiny fixture databases under tmp_path; the real db/leadgen.sqlite and legacy/ are never touched.  The fixture source
schemas are derived from the legacy_<db>_* mirrors of the migrated target (same columns / types / primary keys, no _import_run_id), so the
tests cover the same table set the migrations 0007-0014 define.
"""
import json
import logging
import os
import sqlite3

import pytest

from leadgen import db
from leadgen.legacy_import import hubspot_repo_dbs as H

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL_DB = os.path.join(ROOT, "db", "leadgen.sqlite")

PII = ["Priya Sharma", "priya.sharma@acme.com", "+919812345678", "9812345678", "Rahul Verma", "linkedin.com/in/priya-sharma", "Sridhar", "Acme Tech"]


# ------------------------------------------------------------------------------------------------ fixture builders
def _create_source(target: sqlite3.Connection, name: str, path: str) -> None:
    src = sqlite3.connect(path)
    prefix = "legacy_%s_" % name
    for (tname,) in target.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE ? ESCAPE '\\' ORDER BY name", (prefix.replace("_", "\\_") + "%",)):
        cols = [r for r in target.execute("PRAGMA table_info(%s)" % tname) if r[1] != "_import_run_id"]
        pks = [r[1] for r in sorted(cols, key=lambda r: r[5]) if r[5]]
        defs = []
        for r in cols:
            d = '"%s" %s' % (r[1], r[2] or "")
            if len(pks) == 1 and r[5] and (r[2] or "").upper() == "INTEGER":
                d += " PRIMARY KEY"
            elif r[3]:
                d += " NOT NULL"
            if r[4] is not None:
                d += " DEFAULT %s" % r[4]
            defs.append(d)
        if len(pks) > 1 or (len(pks) == 1 and not any((c[2] or "").upper() == "INTEGER" for c in cols if c[1] == pks[0])):
            defs.append("PRIMARY KEY (%s)" % ", ".join('"%s"' % p for p in pks))
        src.execute('CREATE TABLE "%s" (%s)' % (tname[len(prefix):], ", ".join(defs)))
    for vname, vsql in target.execute("SELECT name, sql FROM sqlite_master WHERE type = 'view' AND name LIKE ?", (prefix + "%",)):
        src.execute(vsql.replace(prefix, "").replace("CREATE VIEW " + vname[len(prefix):], "CREATE VIEW " + vname[len(prefix):]))
    src.commit()
    src.close()


def _ins(path: str, table: str, rows):
    c = sqlite3.connect(path)
    for r in rows:
        c.execute('INSERT INTO "%s" (%s) VALUES (%s)' % (table, ", ".join('"%s"' % k for k in r), ", ".join("?" * len(r))), list(r.values()))
    c.commit()
    c.close()


def _fixture_rows(paths):
    p = paths
    _ins(p["itsvc"], "entities", [
        dict(entity_id="e_1", canonical_name="Acme Tech Pvt Ltd", legal_name=None, cin=None, llpin=None, domain="www.Acme.com",
             linkedin_url="https://www.linkedin.com/company/acme-tech/", headcount_band="51-200", first_seen="2026-10-03T13:29:28.902134Z"),
        dict(entity_id="e_2", canonical_name="Beta Soft", domain="beta.io", linkedin_url="https://www.linkedin.com/company/beta-soft", headcount_band="11-50",
             first_seen="2026-10-03T13:29:28.907030Z"),
        dict(entity_id="e_3", canonical_name="Chain One", domain="chain.com", linkedin_url="https://www.linkedin.com/company/chain-one"),
        dict(entity_id="e_4", canonical_name="Chain Two", domain="chain.com", linkedin_url="https://www.linkedin.com/company/chain-two"),
        dict(entity_id="e_5", canonical_name="Chain Three", domain="chain.com", linkedin_url="https://www.linkedin.com/company/chain-three"),
        dict(entity_id="e_6", canonical_name="Nodomain Labs", domain="", linkedin_url="https://www.linkedin.com/company/nodomain-labs"),
    ])
    cand = []
    for i, (eid, name, dom, li, ph, ap) in enumerate([
            ("e_1", "Acme Tech Pvt Ltd", "www.Acme.com", "https://www.linkedin.com/company/acme-tech/", "+91 98123 45678", "ap1"),
            ("e_2", "Beta Soft", "beta.io", "https://www.linkedin.com/company/beta-soft", "+91 80 4123 4567", "ap2"),
            ("e_3", "Chain One", "chain.com", "https://www.linkedin.com/company/chain-one", "+91 12345 67890", "ap3"),
            ("e_4", "Chain Two", "chain.com", "https://www.linkedin.com/company/chain-two", "", "ap4"),
            ("e_5", "Chain Three", "chain.com", "https://www.linkedin.com/company/chain-three", "", "ap5"),
            ("e_6", "Nodomain Labs", "", "https://www.linkedin.com/company/nodomain-labs", "+1 415-555-0123", "ap6")], 1):
        cand.append(dict(id=i, raw_id=None, source="apollo_org", name=name, domain=dom, website="https://" + (dom or "x.test"), linkedin_url=li, phone_e164=ph,
                         extra_json=json.dumps({"apollo_id": ap, "founded_year": 2015})))
    _ins(p["itsvc"], "candidates", cand)
    _ins(p["itsvc"], "classifications", [
        dict(entity_id="e_1", model="pre-classification-prior", prompt_version="v0", builds_software=1, created_at="2026-10-03T13:29:28.907654Z",
             rules_applied_json=json.dumps({"score": 85, "band": "A"})),
        dict(entity_id="e_2", model="rules", prompt_version="v1", builds_software=1, dev_intensity=2, subsegments_json='["web_mobile_agency", "qa_testing"]',
             tags_json="[]", confidence=0.9, cost_usd=0.0, created_at="2026-10-03T13:48:23.373435Z",
             rules_applied_json=json.dumps({"score": 92, "band": "A", "reasons": ["headcount[51-200]=100"], "rules_applied": []}))])
    _ins(p["itsvc"], "site_text", [dict(entity_id="e_1", domain="acme.com", status="ok", http_code=200, text="hello", fetched_at="2026-10-03T14:00:00.000000Z")])
    _ins(p["itsvc"], "suppression", [dict(id=1, entity_id="e_2", domain="Bad.COM", email=None, phone_e164=None, reason="DNC list", ts="2026-10-03T14:00:00.000000Z"),
                                     dict(id=2, entity_id=None, domain="not a domain", email=None, phone_e164=None, reason="x", ts=None)])
    # ---- pipeline
    _ins(p["pipeline"], "companies", [
        dict(domain="acme.com", company_name="Acme Technologies", website="https://acme.com", city="Pune", state="MH", hq_country="India", founded_year=2012,
             size_band="100-500", size_bucket="mid", status="", sources_json='[{"source": "goodfirms", "url": "u"}]', gate_pass=1, gate_reason=None,
             created_at="2026-07-22T07:31:56.152398+00:00", updated_at="2026-07-30T06:26:31.729635+00:00"),
        dict(domain="gamma.in", company_name="Gamma Works", hq_country="India", gate_pass=0, gate_reason="size ~5 below floor 50",
             created_at="2026-07-22T07:31:56.152398+00:00", updated_at="2026-07-30T06:26:31.729635+00:00")])
    _ins(p["pipeline"], "people", [
        dict(id=1, domain="acme.com", name="Priya Sharma", role="CTO", linkedin_url="https://www.linkedin.com/in/priya-sharma", phone="+919812345678",
             phone_source="signalhire", email="Priya.Sharma@acme.com", is_primary=1, confidence="amber"),
        dict(id=2, domain="acme.com", name="Rahul Verma", role="Founder", phone="+14155550123", phone_source="signalhire", is_primary=0, confidence="amber"),
        dict(id=3, domain="orphan.test", name="No Company", role="x", is_primary=0, confidence="red")])
    _ins(p["pipeline"], "quota", [dict(provider="signalhire", metric="search", window_key="2026-07-22", used=178, limit_value=4000, updated_at="2026-07-22T07:40:15.293440+00:00"),
                                  dict(provider="signalhire", metric="credits", window_key="2026-07-22", used=171, limit_value=None, updated_at="2026-07-22T07:40:15.293440+00:00")])
    # ---- resolver
    _ins(p["resolver"], "companies", [
        dict(domain="gamma.in", status="full", cin="U72200AP2001PTC036337", last_stage="C_constructed",
             data=json.dumps({"company": "Gamma Works Private Limited", "domain": "gamma.in", "cin": "U72200AP2001PTC036337", "founder_name": "Sridhar Panuganti",
                              "din": "00557699", "title": "Managing Director", "linkedin_url": "https://www.linkedin.com/in/sridhar-panuganti-9375706",
                              "identity_status": "full", "confidence": "medium"})),
        dict(domain="radarco.com", status="name_only", cin="U74999DL2016PTC298050", last_stage="F", data=json.dumps({"company": "Radar Co Private Limited"})),
        dict(domain="pending.test", status="pending", cin="", last_stage="B_wall", data="")])
    _ins(p["resolver"], "counters", [dict(source="ddg", day="2026-08-16", n=103)])
    # ---- radar (one candidate_entity row points at a CIN with no entity row = the real-data orphan)
    _ins(p["radar"], "candidate", [dict(id=1, brand_name="Radar Co", brand_name_norm="radar co", first_seen_at="2026-08-04T17:26:06", discovery_source="stk7"),
                                   dict(id=2, brand_name="Orphan Co", brand_name_norm="orphan co", first_seen_at="2026-08-04T17:26:06", discovery_source="ibbi")])
    _ins(p["radar"], "entity", [dict(cin="U74999DL2016PTC298050", legal_name="RADAR CO PRIVATE LIMITED", legal_name_norm="radar co", company_status="Strike Off",
                                     date_of_registration="2016-05-01", registered_state="delhi", source="stk7", fetched_at="2026-08-04T18:54:06")])
    _ins(p["radar"], "candidate_entity", [dict(candidate_id=1, cin="U74999DL2016PTC298050", match_score=100.0, match_method="exact", is_confirmed=1),
                                          dict(candidate_id=2, cin="U12345KA2019PTC000001", match_score=100.0, match_method="override", is_confirmed=1)])
    _ins(p["radar"], "scored", [dict(candidate_id=1, confidence=0.9, tier="confirmed", shutdown_date="2024-03-13", shutdown_year=2024, reason="strike-off",
                                     rationale="STK-7 notice", scored_at="2026-08-04T21:27:29")])
    _ins(p["radar"], "liveness", [dict(candidate_id=1, domain="radarco.com", dns_resolves=0, liveness_score=0.9, checked_at="2026-08-04T18:31:24")])
    _ins(p["radar"], "evidence", [dict(id=1, candidate_id=1, kind="stk7", source_url="https://x.test/a.pdf", as_of_date="2024-03-13", snippet="notice", fetched_at="2026-08-04T17:26:06",
                                       dedupe_key="k1"),
                                  dict(id=2, candidate_id=1, kind="stk7", source_url="https://x.test/b.pdf", as_of_date="2024-03-14", snippet="notice2", fetched_at="2026-08-04T17:26:07",
                                       dedupe_key="k2")])
    _ins(p["radar"], "run_stat", [dict(k="resolve", v="{}")])
    # ---- corpus
    _ins(p["corpus"], "vertical", [dict(id=1, slug="uk_proptech", display_name="UK PropTech", region="UK", country_code="GB", created_at="2026-09-28T16:12:38+00:00")])
    _ins(p["corpus"], "company", [
        dict(id=1, vertical_id=1, display_name="Beta Soft Limited", name_norm="beta soft limited", country_code="GB", status="active", headcount_est=None,
             first_seen_at="2026-09-28T16:13:43+00:00", last_updated_at="2026-09-28T16:13:43+00:00", founded_year=2010, status_as_of="2026-09-01"),
        dict(id=2, vertical_id=1, display_name="Prop Two", name_norm="prop two", root_domain="beta.io", country_code="GB", status="liquidation",
             first_seen_at="2026-09-28T16:13:43+00:00", last_updated_at="2026-09-28T16:13:43+00:00")])
    _ins(p["corpus"], "company_identifier", [dict(id=1, company_id=1, id_type="companies_house", id_value="01234567", is_primary=1, confidence=1.0),
                                             dict(id=2, company_id=2, id_type="companies_house", id_value="07654321", is_primary=1, confidence=1.0)])
    _ins(p["corpus"], "icp_score", [dict(id=1, company_id=1, vertical_id=1, score=61.5, score_raw=70.0, band="P1", match_label="Fit", confidence=0.8,
                                         segments='["dev_crm_construction_pm"]', scored_by="rules_v1", rationale="coverage 16%", scored_at="2026-09-28T16:57:37+00:00"),
                                    dict(id=2, company_id=2, vertical_id=1, score=1.0, band="P4", match_label="Unclear", scored_by="rules_v1", scored_at="2026-09-28T16:57:37+00:00")])
    _ins(p["corpus"], "icp_score_component", [dict(id=1, icp_score_id=1, rule_slug="sic_priority", points=3.0, detail="x"), dict(id=2, icp_score_id=1, rule_slug="credibility", points=0.0)])
    _ins(p["corpus"], "indicator_def", [dict(id=1, slug="has_crm_pipeline", display_name="CRM", category="ops_exhaust", value_type="bool", created_at="2026-09-28T16:00:00+00:00")])
    _ins(p["corpus"], "indicator", [dict(id=1, company_id=1, indicator_id=1, value_bool=1, confidence=0.7, evidence_snippet="crm", observed_at="2026-09-28T16:13:43+00:00")])
    _ins(p["corpus"], "suppression", [dict(id=1, kind="domain", value="blocked.example", reason="competitor list", added_at="2026-09-28T16:13:43+00:00")])
    # ---- gmaps / searchledger / itdirs
    places = [{"id": "ChIJ1", "displayName": {"text": "Gamma Works Pvt Ltd"}, "websiteUri": "https://www.gamma.in/", "nationalPhoneNumber": "040 2345 6789",
               "formattedAddress": "Plot 1, Hyderabad, Telangana 500081, India", "rating": 4.5, "userRatingCount": 10},
              {"id": "ChIJ2", "displayName": {"text": "Delta Labs"}, "formattedAddress": "Somewhere, Pune, Maharashtra 411001, India"}]
    _ins(p["gmaps"], "tiles", [dict(k="t1", query="q", bbox="{}", places=json.dumps(places), calls=1, at="2026-08-04T12:31:50"),
                               dict(k="t2", query="q2", bbox="{}", places=json.dumps(places[:1]), calls=1, at="2026-08-04T12:32:50")])
    _ins(p["gmaps"], "usage", [dict(month="2026-08", calls=304)])
    _ins(p["searchledger"], "calls", [dict(id=1, ts_utc=1787044869.77, company="X", http_status=200, profiles_returned=2, size_used=5, total_reported=2, run_id="r1"),
                                      dict(id=2, ts_utc=1787044899.77, company="Y", http_status=402, profiles_returned=0, size_used=5, total_reported=0, run_id="r1")])
    _ins(p["searchledger"], "events", [dict(ts_utc=1787044869.0, kind="run_start", note="n")])
    _ins(p["itdirs"], "pages", [dict(url="https://infopark.in/companies", ts=1787048054)])


@pytest.fixture
def world(tmp_path):
    target = os.path.abspath(str(tmp_path / "t.sqlite"))
    assert target != REAL_DB
    db.migrate(target, backup=False)
    root = tmp_path / "hubspot"
    paths = {}
    con = db.connect(target)
    for s in H.SOURCES:
        p = str(root / s.rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        _create_source(con, s.name, p)
        paths[s.name] = p
    con.close()
    _fixture_rows(paths)
    return {"db": target, "root": str(root), "paths": paths}


def run(world, **kw):
    kw.setdefault("legacy_root", world["root"])
    return H.run(world["db"], **kw)


def q(world, sql, *args):
    c = sqlite3.connect(world["db"])
    try:
        return c.execute(sql, args).fetchall()
    finally:
        c.close()


def counts(world):
    c = sqlite3.connect(world["db"])
    try:
        names = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'ops_audit%'")]
        return {n: c.execute('SELECT COUNT(*) FROM "%s"' % n).fetchone()[0] for n in names}
    finally:
        c.close()


def src_count(world, name, table):
    c = sqlite3.connect(world["paths"][name])
    try:
        return c.execute('SELECT COUNT(*) FROM "%s"' % table).fetchone()[0]
    finally:
        c.close()


# ================================================================================================ verbatim
def test_fixture_covers_every_mirrored_table(world):
    """The fixture sources have exactly the tables of migrations 0007-0014 (so a new table breaks this test, not a real run)."""
    for s in H.SOURCES:
        c = sqlite3.connect(world["paths"][s.name])
        tables, views, skipped = H.source_tables(c)
        c.close()
        mirrored = [r[0] for r in db.connect(world["db"]).execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE ? ESCAPE '\\'", (s.prefix.replace("_", "\\_") + "%",))]
        assert sorted(s.prefix + t for t, _ in tables) == sorted(mirrored) and not skipped


def test_verbatim_exact_row_parity_for_every_table(world):
    rep = run(world, do_canonical=False)
    total = 0
    for name, r in rep["verbatim"].items():
        assert r["status"] == "succeeded" and not r["already_imported"]
        for t, c in r["tables"].items():
            assert c["source"] == src_count(world, name, t) == c["loaded"] and c["content_ok"], (name, t, c)
            assert q(world, 'SELECT COUNT(*) FROM "legacy_%s_%s" WHERE _import_run_id = ?' % (name, t), r["run_id"])[0][0] == c["source"]
            total += c["source"]
    assert total > 30
    # corpus views: mirrored over the loaded tables and equal to the source views
    assert all(v["ok"] for v in rep["verbatim"]["corpus"]["views"].values()) and len(rep["verbatim"]["corpus"]["views"]) == 3
    # run bookkeeping
    runs = q(world, "SELECT source_name, status, rows_read, rows_written, source_fingerprint FROM ops_import_run WHERE source_name LIKE 'hubspot_repo:%:verbatim'")
    assert len(runs) == 8 and all(r[1] == "succeeded" and len(r[4]) == 64 and r[2] == r[3] for r in runs)
    assert q(world, "SELECT COUNT(*) FROM legacy_itsvc_candidates WHERE _import_run_id IS NULL")[0][0] == 0


def test_verbatim_values_and_rowids_are_identical(world):
    run(world, do_canonical=False)
    s = sqlite3.connect(world["paths"]["pipeline"])
    t = sqlite3.connect(world["db"])
    for table, key in (("people", "id"), ("companies", "domain"), ("quota", "provider, metric, window_key")):
        cols = [r[1] for r in s.execute("PRAGMA table_info(%s)" % table)]
        a = s.execute("SELECT rowid, %s FROM %s ORDER BY rowid" % (", ".join(cols), table)).fetchall()
        b = t.execute("SELECT rowid, %s FROM legacy_pipeline_%s ORDER BY rowid" % (", ".join(cols), table)).fetchall()
        assert a == b
    # PK-less table keeps the source rowid
    sl = sqlite3.connect(world["paths"]["resolver"])
    assert sl.execute("SELECT rowid, source, ts, code FROM walls").fetchall() == t.execute("SELECT rowid, source, ts, code FROM legacy_resolver_walls").fetchall()


def test_radar_orphan_is_loaded_and_recorded(world):
    rep = run(world, only=["radar"], do_canonical=False)
    assert rep["verbatim"]["radar"]["fk_orphans"] == 1
    assert src_count(world, "radar", "candidate_entity") == q(world, "SELECT COUNT(*) FROM legacy_radar_candidate_entity")[0][0] == 2
    iss = q(world, "SELECT rule_code, severity, status FROM ops_dq_issue WHERE rule_code = 'legacy_fk_orphan'")
    assert iss == [("legacy_fk_orphan", "warn", "open")]
    assert db.connect(world["db"]).execute("PRAGMA foreign_keys").fetchone()[0] == 1
    # doctor treats legacy orphans as warnings, not failures
    assert db.doctor(world["db"], quick=True)["ok"]


def test_verbatim_rerun_is_a_noop(world):
    run(world, do_canonical=False)
    before = counts(world)
    rep = run(world, do_canonical=False)
    assert all(r["already_imported"] for r in rep["verbatim"].values())
    assert counts(world) == before


def test_verbatim_resume_after_crash_does_not_duplicate(world):
    with pytest.raises(KeyboardInterrupt):
        run(world, do_canonical=False, only=["pipeline"], batch_rows=1, fail_after_rows=3)
    st = q(world, "SELECT import_run_id, status FROM ops_import_run WHERE source_name = 'hubspot_repo:pipeline:verbatim'")
    assert len(st) == 1 and st[0][1] == "aborted"
    partial = sum(q(world, "SELECT COUNT(*) FROM legacy_pipeline_%s" % t)[0][0] for t in ("companies", "people", "quota", "cache", "crm_feedback", "no_domain", "raw_listings"))
    assert 0 < partial <= 3
    rep = run(world, do_canonical=False, only=["pipeline"], batch_rows=1)
    r = rep["verbatim"]["pipeline"]
    assert r["resumed"] and r["run_id"] == st[0][0] and r["status"] == "succeeded"
    for t, c in r["tables"].items():
        assert c["source"] == c["loaded"] == src_count(world, "pipeline", t)
    assert q(world, "SELECT COUNT(*) FROM ops_import_run WHERE source_name = 'hubspot_repo:pipeline:verbatim'")[0][0] == 1


def test_changed_source_is_refused_not_stacked(world):
    run(world, do_canonical=False, only=["gmaps"])
    _ins(world["paths"]["gmaps"], "usage", [dict(month="2026-09", calls=1)])
    with pytest.raises(H.ImportFailure):
        run(world, do_canonical=False, only=["gmaps"])
    assert q(world, "SELECT COUNT(*) FROM legacy_gmaps_usage")[0][0] == 1


def test_verbatim_dry_run_writes_nothing(world):
    before = counts(world)
    rep = run(world, do_canonical=False, dry_run=True)
    assert counts(world) == before
    assert rep["verbatim"]["itsvc"]["tables"]["entities"]["would_insert"] == 6


def test_virtual_tables_are_skipped(tmp_path):
    p = str(tmp_path / "v.sqlite")
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE plain (a)")
    c.execute("CREATE VIRTUAL TABLE docs USING fts5(body)")
    tables, views, skipped = H.source_tables(c)
    assert [t for t, _ in tables] == ["plain"] and "docs" in skipped and any(s.startswith("docs_") for s in skipped)


def test_missing_source_and_unknown_name_fail_before_writing(world):
    before = counts(world)
    with pytest.raises(H.ImportFailure):
        run(world, only=["nope"])
    os.remove(world["paths"]["itdirs"])
    with pytest.raises(H.ImportFailure):
        run(world)
    assert counts(world) == before


# ================================================================================================ canonical
def canon(world, **kw):
    return run(world, do_verbatim=False, **kw)


def test_canonical_links_only_through_strong_keys(world):
    canon(world)
    # itsvc Acme (domain www.Acme.com) and pipeline acme.com are ONE golden company
    acme = q(world, "SELECT company_id FROM company_identifier WHERE identifier_type = 'root_domain' AND value_norm = 'acme.com'")
    assert len(acme) == 1
    srcs = q(world, "SELECT DISTINCT source_system FROM origin_ref WHERE entity_type = 'company' AND entity_id = ? ORDER BY 1", acme[0][0])
    assert srcs == [("itsvc",), ("pipeline",)]
    # resolver gamma.in + pipeline gamma.in + gmaps (website domain) -> one company; radar co links resolver via CIN (+ liveness domain)
    gamma = q(world, "SELECT DISTINCT company_id FROM company_identifier WHERE value_norm IN ('gamma.in', 'U72200AP2001PTC036337')")
    assert len(gamma) == 1
    assert {r[0] for r in q(world, "SELECT source_system FROM origin_ref WHERE entity_type = 'company' AND entity_id = ?", gamma[0][0])} == {"pipeline", "resolver", "gmaps"}
    radar = q(world, "SELECT DISTINCT company_id FROM company_identifier WHERE value_norm IN ('radarco.com', 'U74999DL2016PTC298050')")
    assert len(radar) == 1
    assert {r[0] for r in q(world, "SELECT source_system FROM origin_ref WHERE entity_type = 'company' AND entity_id = ?", radar[0][0])} == {"resolver", "radar"}
    # the orphan CIN of radar still produced a company (never dropped)
    assert q(world, "SELECT COUNT(*) FROM company_identifier WHERE value_norm = 'U12345KA2019PTC000001' AND is_strong = 1")[0][0] == 1


def test_shared_domain_is_weak_evidence_and_never_links(world):
    canon(world)
    rows = q(world, "SELECT is_strong, COUNT(DISTINCT company_id) FROM company_identifier WHERE value_norm = 'chain.com' GROUP BY is_strong")
    assert rows == [(0, 3)]       # three distinct companies, all weak
    assert q(world, "SELECT COUNT(*) FROM company WHERE canonical_name LIKE 'Chain %'")[0][0] == 3
    assert q(world, "SELECT COUNT(*) FROM company_identifier WHERE is_primary = 1 AND is_strong = 0")[0][0] == 0


def test_name_only_matches_are_queued_not_merged(world):
    canon(world)
    mc = q(world, "SELECT entity_type, match_kind, score, status, evidence_json FROM merge_candidate WHERE match_kind = 'name_norm'")
    assert len(mc) == 1 and mc[0][0] == "company" and mc[0][3] == "open" and 0.6 <= mc[0][2] <= 0.95
    ev = json.loads(mc[0][4])
    assert ev["sources"] == ["corpus", "itsvc"] and ev["name_key"] == "beta soft"
    # beta.io is claimed by itsvc Beta Soft AND corpus 'Prop Two' (shared strong key): linked, so no 2nd company for it
    assert q(world, "SELECT COUNT(DISTINCT company_id) FROM company_identifier WHERE value_norm = 'beta.io'")[0][0] == 1
    assert q(world, "SELECT COUNT(*) FROM company WHERE merged_into_company_id IS NOT NULL")[0][0] == 0


def test_phones_and_the_plus91_gate(world):
    canon(world)
    cp = {r[0]: r[1:] for r in q(world, "SELECT phone_raw, phone_e164, phone_type, is_indian_mobile, is_placeholder FROM contact_phone")}
    assert cp["+919812345678"] == ("+919812345678", "mobile", 1, 0)
    assert cp["+14155550123"][1:] == ("unknown", 0, 0)
    comp = {r[0]: r[1:] for r in q(world, "SELECT phone_raw, phone_e164, phone_type, is_indian_mobile, is_placeholder FROM company_phone")}
    assert comp["+91 98123 45678"][2] == 1                         # mobile -> gate open
    assert comp["+91 80 4123 4567"][1:3] == ("landline", 0)        # 80 = Bangalore fixed line: classified exactly, gate closed
    assert comp["+91 12345 67890"][3] == 1 and comp["+91 12345 67890"][2] == 0   # dummy -> placeholder, never opens the gate
    assert comp["040 2345 6789"][1] == "landline"                  # Google Places number
    # placeholder / unknown never pass the generated gate
    assert q(world, "SELECT COUNT(*) FROM company_phone WHERE is_indian_mobile = 1 AND (is_placeholder = 1 OR phone_type <> 'mobile')")[0][0] == 0


def test_people_become_contacts_attached_to_the_golden_company(world):
    canon(world)
    row = q(world, "SELECT c.full_name, c.job_title, c.email_norm, c.linkedin_person_norm, c.company_id, i.company_id FROM contact c "
                   "JOIN company_identifier i ON i.identifier_type = 'root_domain' AND i.value_norm = 'acme.com' WHERE c.full_name = 'Priya Sharma'")
    assert row and row[0][2] == "priya.sharma@acme.com" and row[0][3] == "in/priya-sharma" and row[0][4] == row[0][5]
    attrs = json.loads(q(world, "SELECT attrs_json FROM contact WHERE full_name = 'Priya Sharma'")[0][0])
    assert attrs["name_source"] is None and attrs["confidence"] == "amber"
    # founder from the resolver: DIN in attrs_json, company = the gamma.in golden company
    f = q(world, "SELECT attrs_json, company_id FROM contact WHERE full_name = 'Sridhar Panuganti'")
    assert json.loads(f[0][0])["din"] == "00557699"
    # a person whose domain has no company is kept, company NULL, and reported
    assert q(world, "SELECT company_id FROM contact WHERE full_name = 'No Company'") == [(None,)]
    assert q(world, "SELECT occurrences FROM ops_dq_issue WHERE rule_code = 'contact_without_company'") == [(1,)]


def test_verdicts_placeholders_and_buckets(world):
    canon(world)
    assert q(world, "SELECT COUNT(*) FROM tam_company_verdict WHERE is_placeholder = 1")[0][0] == 0
    # itsvc: only the 'rules' row became a verdict, unscored (itsvc has no ICP bucket), the placeholder prior did not
    v = q(world, "SELECT icp_bucket, verdict_method, score, priority_tier, segment, model FROM tam_company_verdict v JOIN origin_ref o ON o.entity_type = 'tam_company_verdict' "
                 "AND o.entity_id = v.verdict_id AND o.source_system = 'itsvc'")
    assert v == [("unscored", "rules", 92.0, "A", "web_mobile_agency", "rules")]
    assert q(world, "SELECT occurrences FROM ops_dq_issue WHERE rule_code = 'placeholder_not_imported'") == [(1,)]
    # corpus: Fit/P1 -> fit; Unclear -> unscored with the label kept verbatim; components kept in details_json
    fit = q(world, "SELECT icp_bucket, icp_band, native_bucket, details_json, vertical_id FROM tam_company_verdict WHERE native_bucket = 'Fit'")
    assert fit[0][:3] == ("fit", "P1", "Fit") and [c["rule"] for c in json.loads(fit[0][3])["components"]] == ["sic_priority", "credibility"]
    assert q(world, "SELECT icp_bucket FROM tam_company_verdict WHERE native_bucket = 'Unclear'") == [("unscored",)]
    # pipeline gate: fail -> out with the reason, pass -> unscored (never fit)
    assert q(world, "SELECT icp_bucket, reason_code, reason FROM tam_company_verdict WHERE native_bucket = 'gate_fail'") == [("out", "gate_fail", "size ~5 below floor 50")]
    assert q(world, "SELECT COUNT(*) FROM tam_company_verdict WHERE icp_bucket = 'fit'")[0][0] == 1
    # one current verdict per (company, vertical)
    assert q(world, "SELECT COUNT(*) FROM (SELECT 1 FROM tam_company_verdict WHERE is_current = 1 GROUP BY company_id, COALESCE(vertical_id, 0) HAVING COUNT(*) > 1)")[0][0] == 0


def test_signals_from_radar_corpus_gmaps(world):
    canon(world)
    sig = {r[0]: r[1:] for r in q(world, "SELECT signal_code, value_text, value_num, value_bool, confidence FROM company_signal WHERE source_system = 'radar'")}
    assert sig["distress_tier"][0] == "confirmed" and sig["distress_tier"][3] == 0.9
    assert sig["evidence_stk7"][1] == 2.0                    # evidence aggregated per kind
    assert sig["dns_resolves"][2] == 0
    assert q(world, "SELECT value_bool FROM company_signal WHERE signal_code = 'has_crm_pipeline'") == [(1,)]
    assert q(world, "SELECT value_num FROM company_signal WHERE signal_code = 'gmaps_rating'") == [(4.5,)]


def test_costs_unknown_is_null_never_zero(world):
    canon(world)
    rows = q(world, "SELECT vendor, unit, qty, credits, usd, usd_basis, is_placeholder FROM cost_ledger ORDER BY vendor, unit, qty")
    assert len(rows) == 5          # 2 quota + 1 maps usage + 2 search calls
    assert all(r[4] is None and r[5] == "unknown" and r[6] == 0 for r in rows)
    assert ("google_maps", "places_call", 304.0, None, None, "unknown", 0) in rows
    assert ("signalhire", "credit", 171.0, 171.0, None, "unknown", 0) in rows
    assert q(world, "SELECT COUNT(*) FROM cost_ledger WHERE usd = 0")[0][0] == 0


def test_suppression_tables_map_and_unnormalisable_values_are_reported(world):
    canon(world)
    s = q(world, "SELECT kind, match_type, match_value_norm, list_name FROM suppression ORDER BY list_name")
    assert ("competitor", "domain", "blocked.example", "corpus.suppression") in s
    assert ("dnc", "domain", "bad.com", "itsvc.suppression") in s
    assert len(s) == 2
    d = q(world, "SELECT severity, occurrences FROM ops_dq_issue WHERE rule_code = 'suppression_unnormalisable'")
    assert d == [("error", 1)]       # the junk value was NOT imported, and is loudly reported


def test_every_canonical_row_has_an_origin_ref(world):
    canon(world)
    for table, pk, etype in (("company", "company_id", "company"), ("company_identifier", "identifier_id", "company_identifier"), ("contact", "contact_id", "contact"),
                             ("contact_phone", "phone_id", "contact_phone"), ("company_phone", "phone_id", "company_phone"),
                             ("tam_company_verdict", "verdict_id", "tam_company_verdict"), ("cost_ledger", "cost_id", "cost_ledger"),
                             ("suppression", "suppression_id", "suppression"), ("vertical", "vertical_id", "vertical")):
        n = q(world, "SELECT COUNT(*) FROM %s t WHERE NOT EXISTS (SELECT 1 FROM origin_ref o WHERE o.entity_type = ? AND o.entity_id = t.%s)" % (table, pk), etype)[0][0]
        total = q(world, "SELECT COUNT(*) FROM %s" % table)[0][0]
        if table == "vertical":
            assert n == 1 and total == 2       # india_it_services is ours (no source row); corpus 'uk_proptech' has an origin
        else:
            assert n == 0 and total > 0, table
    assert q(world, "SELECT COUNT(*) FROM company_signal WHERE import_run_id IS NULL OR source_system = ''")[0][0] == 0


def test_canonical_rerun_is_idempotent_and_dry_run_writes_nothing(world):
    canon(world, dry_run=True)
    assert q(world, "SELECT COUNT(*) FROM company")[0][0] == 0 and q(world, "SELECT COUNT(*) FROM ops_import_run")[0][0] == 0
    rep = canon(world)
    first = counts(world)
    assert first["company"] > 10 and rep["overlap"]["source_pair_overlap"]
    canon(world)
    again = counts(world)
    diff = {k: (first[k], again[k]) for k in first if first[k] != again[k]}
    assert set(diff) <= {"ops_import_run", "ops_dq_issue"}, diff     # only the run ledger grows
    # nothing but bookkeeping changed in canonical tables
    assert again["company"] == first["company"] and again["origin_ref"] == first["origin_ref"] and again["company_signal"] == first["company_signal"]


def test_dry_run_matches_the_real_run_counts(world):
    d = canon(world, dry_run=True)
    r = canon(world)
    assert d["counts"] == r["counts"]
    assert d["overlap"]["source_pair_overlap"] == r["overlap"]["source_pair_overlap"]


def test_only_filter_and_partial_stages(world):
    rep = run(world, only=["pipeline"], do_verbatim=True, do_canonical=True)
    assert set(rep["verbatim"]) == {"pipeline"} and "pipeline" in rep["canonical"] and "itsvc" not in rep["canonical"]
    assert q(world, "SELECT COUNT(*) FROM legacy_itsvc_entities")[0][0] == 0
    assert q(world, "SELECT COUNT(DISTINCT source_system) FROM origin_ref")[0][0] == 1


def test_overlap_report_counts_cross_store_companies(world):
    rep = canon(world)
    o = rep["overlap"]
    assert o["source_pair_overlap"]["itsvc x pipeline"] == 1 and o["source_pair_overlap"]["pipeline x resolver"] >= 1
    assert o["identifiers_confirmed_by_2plus_sources"]["root_domain"] >= 3
    assert o["links_by_key"]["resolver <- root_domain"] >= 1 and o["links_by_key"]["radar <- cin"] >= 1


def test_database_stays_healthy_after_both_stages(world):
    run(world)
    rep = db.doctor(world["db"], quick=True)
    assert rep["ok"], rep
    assert q(world, "PRAGMA foreign_key_check") and all(r[0].startswith("legacy_") for r in q(world, "PRAGMA foreign_key_check"))


def test_logs_and_report_never_contain_personal_data(world, caplog, capsys):
    caplog.set_level(logging.DEBUG, logger="leadgen")
    rc = H.main(["--db", world["db"], "--legacy-root", world["root"]])
    out = capsys.readouterr()
    assert rc == 0
    blob = caplog.text + out.out + out.err
    for needle in PII:
        assert needle not in blob, needle
    dq = " ".join(r[0] + r[1] for r in q(world, "SELECT message, details_json FROM ops_dq_issue"))
    for needle in PII:
        assert needle not in dq, needle


def test_cli_dry_run_and_error_exit_code(world, capsys):
    assert H.main(["--db", world["db"], "--legacy-root", world["root"], "--dry-run", "--only", "itsvc,pipeline", "--only-verbatim"]) == 0
    assert "DRY RUN" in capsys.readouterr().out
    assert q(world, "SELECT COUNT(*) FROM legacy_itsvc_entities")[0][0] == 0
    assert H.main(["--db", world["db"], "--only", "bogus"]) == 2


# ================================================================================================ unit tests
def test_classify_phone_is_conservative():
    assert H.classify_phone("+919812345678") == ("mobile", "IN")
    assert H.classify_phone("+917042813998") == ("mobile", "IN")
    assert H.classify_phone("+918041234567")[0] == "landline"       # Bangalore 80 (exact via phonenumbers)
    assert H.classify_phone("+917912345678")[0] == "landline"       # Ahmedabad 79 (exact via phonenumbers)
    assert H.classify_phone("+917712345678")[0] == "unknown"        # Raipur 771
    assert H.classify_phone("+911123456789")[0] == "landline"
    assert H.classify_phone("+14155550123") == ("unknown", None)
    assert H.classify_phone(None) == ("unknown", None)


def test_dummy_phones():
    for raw in ("+91 12345 67890", "0123456789", "9999999999", "+91 98765 43210", "123"):
        assert H.is_dummy_phone(raw), raw
    assert not H.is_dummy_phone("+91 98123 45678")
    pf = H.phone_fields("0123456789")
    assert pf["placeholder"] == 1 and pf["type"] == "unknown"


def test_name_key_status_and_helpers():
    assert H.name_key("beta soft limited") == H.name_key("beta soft pvt. ltd.") == "beta soft"
    assert H.name_key("ab") == "ab"
    assert H.map_status("active - proposal to strike off") == "active"
    assert H.map_status("in administration/receiver manager") == "administration"
    assert H.map_status("Struck Off (STK-7)") == "struck_off"
    assert H.map_status("Under Liquidation (IBBI)") == "liquidation"
    assert H.map_status(None) == "unknown" and H.map_status("weird") == "unknown"
    assert H.suppression_kind("Whale list") == "never_push" and H.suppression_kind("x", "dnc") == "dnc" and H.suppression_kind("?") == "other"
    assert H.infer_match_type("a@b.co") == "email" and H.infer_match_type("acme.com") == "domain" and H.infer_match_type("U72200AP2001PTC036337") == "cin"
    assert H.infer_match_type("anything", "domain") == "domain" and H.infer_match_type("???") is None
    assert H.scan_shared(["a.com", "a.com", "b.com", "b.com", "b.com", None]) == {"b.com"}


def test_no_replace_idiom_in_module():
    import re
    src = open(os.path.join(ROOT, "leadgen", "legacy_import", "hubspot_repo_dbs.py"), encoding="utf-8").read()
    assert not re.search(r"INSERT\s+OR\s+REPLACE|REPLACE\s+INTO", src, re.I)
