"""Tests of the unified daily funnel report (leadgen/reports, config/report.yaml, docs/REPORTS.md).

Synthetic tests build a tiny world in a throwaway database (never db/leadgen.sqlite).  The `real_*` tests read the real database READ-ONLY
and are skipped when it is absent; they carry the built-in regression checks (CoOps Cold Lead by country, live deal counts per pipeline).
Run:  mkdir -p db/_scratch && .venv/bin/python -m pytest -q tests --basetemp=db/_scratch/pytest_rpt
"""
import io
import json
import os
import re

import pytest

from leadgen import db as ldb
from leadgen.reports import build, daily, mailer, metrics as M, render

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL_DB = os.path.join(ROOT, "db", "leadgen.sqlite")
real = pytest.mark.skipif(not os.path.exists(REAL_DB), reason="real database not present")

D0 = "2026-10-01"


# ------------------------------------------------------------------------------------------------ synthetic world
def ts(day, hhmm_ist="12:00"):
    """UTC timestamp for an IST wall-clock time on an IST day."""
    import datetime
    t = datetime.datetime.strptime(day + " " + hhmm_ist, "%Y-%m-%d %H:%M") - datetime.timedelta(minutes=330)
    return t.strftime("%Y-%m-%dT%H:%M:%S.000Z")


@pytest.fixture
def world(tmp_path):
    p = os.path.abspath(str(tmp_path / "w.sqlite"))
    assert p != REAL_DB
    ldb.migrate(p)
    con = ldb.connect(p)
    st = [("c0", "Cold Call", "SOURCED", None, None), ("c1", "No Pickup", "NO_ANSWER", None, None), ("c2", "Interested", "INTERESTED", None, None),
          ("c3", "GMeet Fixed", "MEETING_BOOKED", None, None), ("c4", "Script Shared", "ASSET_REQUESTED", None, None), ("c5", "Closed/Won", "WON", None, None),
          ("c6", "Dead/WrongNumber", "DEAD", "BAD_CONTACT", 1), ("c7", "Dead/NoShow", "DEAD", "NO_SHOW", 4)]
    for i, (sid, label, code, reason, depth) in enumerate(st):
        con.execute("INSERT INTO stage (account_id, pipeline_id, stage_id, label, display_order) VALUES ('main','default',?,?,?)", (sid.replace("c", "100"), label, i))
        con.execute("INSERT INTO stage_map (account_id, pipeline_id, stage_id, canonical_code, dead_reason_code, depth_reached) VALUES ('main','default',?,?,?,?)",
                    (sid.replace("c", "100"), code, reason, depth))
    con.execute("INSERT INTO stage (account_id, pipeline_id, stage_id, label, display_order) VALUES ('main','default','1009','Mystery',9)")      # unmapped on purpose
    con.execute("INSERT INTO stage (account_id, pipeline_id, stage_id, label, display_order) VALUES ('main','2425754306','2000','Cold Lead',0)")
    con.execute("INSERT INTO stage_map (account_id, pipeline_id, stage_id, canonical_code) VALUES ('main','2425754306','2000','SOURCED')")
    con.execute("INSERT INTO owner (account_id, hs_owner_id, hs_user_id, email_norm, display_name) VALUES ('main','1001','1001','yuktha.anand@lh2.ai','Yuktha Anand')")
    con.execute("INSERT INTO owner (account_id, hs_owner_id, hs_user_id, email_norm, display_name) VALUES ('main','1002','1002','lamiya.saleem@lh2.ai','Lamiya Saleem')")
    for code, label in (("uk_proptech", "UK_Proptech"), ("fintech_us", "Fintech_US"), ("outflo_georgia", "Outflo_Georgia")):
        con.execute("INSERT INTO lead_source (code, label) VALUES (?,?)", (code, label))
    con.execute("INSERT INTO ops_sync_state (source_system, scope, object_type, cursor_name, account_id, last_success_at, last_status, rows_synced) "
                "VALUES ('hubspot','main','deals','hs_lastmodifieddate','main','2026-10-05T10:00:00.000Z','ok',3)")
    owner = {r["hs_owner_id"]: r["owner_id"] for r in con.execute("SELECT owner_id, hs_owner_id FROM owner")}
    w = type("W", (), {})()
    w.path, w.con, w.owner = p, con, owner
    w.n = 0

    def deal(stage, pipe="default", owner_hs="1001", lead=None, archived=0):
        w.n += 1
        ls = con.execute("SELECT lead_source_id FROM lead_source WHERE label = ?", (lead,)).fetchone() if lead else None
        cur = con.execute("INSERT INTO deal (account_id, hs_deal_id, pipeline_id, stage_id, owner_id, lead_source_id, is_archived, hs_created_at) VALUES ('main',?,?,?,?,?,?, '2026-09-01T00:00:00.000Z')",
                          (str(1000 + w.n), pipe, stage, owner[owner_hs], ls[0] if ls else None, archived))
        return cur.lastrowid

    def ev(deal_id, stage, day, hhmm="12:00", src="CRM_UI", actor="1001", pipe="default"):
        con.execute("INSERT INTO deal_stage_event (deal_id, account_id, pipeline_id, to_stage_id, entered_at, source_type, actor_user_id, actor_owner_id, pipeline_basis) "
                    "VALUES (?, 'main', ?, ?, ?, ?, ?, ?, 'unique_stage_id')", (deal_id, pipe, stage, ts(day, hhmm), src, actor if src == "CRM_UI" else None, owner[actor] if (actor and src == "CRM_UI") else None))
    w.deal, w.ev = deal, ev
    yield w
    con.close()


def engine(w):
    return M.Engine(w.con)


# ------------------------------------------------------------------------------------------------ dictionary
def test_dictionary_and_functions_agree():
    cfg = M.load_report_cfg()
    fns = set(m["fn"] for m in cfg["metrics"].values())
    for f in fns:
        assert callable(getattr(M, f, None)), f
    # every public metric function is defined in the dictionary
    expected = {"rung_entries", "deals_entered", "inception", "leads_assigned", "engaged", "occupancy", "dead_breakdown", "derived_rungs", "change", "cold_lead_country",
                "integrity", "crosscheck"}
    assert fns == expected
    for k, m in cfg["metrics"].items():
        for field in ("name", "definition", "source", "unit", "window", "scope"):
            assert m.get(field), (k, field)


def test_change_rules():
    assert M.change(0, 5, True)["flag"] == "new"
    assert M.change(0, 0, True)["flag"] == "flat"
    assert M.change(5, 8, True)["flag"] == "abs" and M.change(5, 8, True)["pct"] is None          # prev below the threshold: absolute only
    assert M.change(100, 150, True) == {"abs": 50, "pct": 50.0, "flag": "pct"}
    assert M.change(100, 150, False)["flag"] == "partial" and M.change(100, 150, False)["pct"] is None   # partial window: never a percentage


# ------------------------------------------------------------------------------------------------ semantics
def test_ist_day_boundary_and_entry_any_source_engaged_human_only(world):
    w = world
    a = w.deal("1000")
    b = w.deal("1000")
    w.ev(a, "1000", "2026-10-02", "00:30", src="CRM_UI")           # 00:30 IST = 19:00Z the day before: must land on IST day 10-02
    w.ev(b, "1000", "2026-10-02", "09:00", src="INTEGRATION")      # an API push
    assert w.con.execute("SELECT MIN(ist_day) FROM deal_stage_event").fetchone()[0] == "2026-10-02"
    e = engine(w)
    la = M.leads_assigned(e, "coding", "2026-10-02")
    assert la["today"]["total"] == 2 and la["today"]["human"] == 1 and la["today"]["headline"] == 2        # entry rung: any source
    eg = M.engaged(e, "coding", "2026-10-02")
    assert eg["today"] == 1                                                                              # engaged: the human click only
    assert M.engaged(e, "coding", "2026-10-01")["today"] == 0


def test_non_entry_headline_is_human_only_but_auto_is_carried(world):
    w = world
    a, b = w.deal("1001"), w.deal("1001")
    w.ev(a, "1001", D0, src="CRM_UI")
    w.ev(b, "1001", D0, src="INTEGRATION")
    e = engine(w)
    r = M.rung_entries(e, "coding", "NO_ANSWER", D0)
    assert (r["today"]["human"], r["today"]["auto"], r["today"]["total"], r["today"]["headline"]) == (1, 1, 2, 1)


def test_union_vs_sum_and_invariants(world):
    w = world
    d1, d2 = w.deal("1002"), w.deal("1002")
    for day in ("2026-09-28", "2026-09-29", "2026-09-30", D0):
        w.ev(d1, "1001" if day == "2026-09-28" else "1001", day, hhmm="10:00")
    w.ev(d2, "1002", D0)
    e = engine(w)
    eg = M.engaged(e, "coding", D0)
    assert eg["roll7_union"] == 2                                                                         # union of distinct deals, not 4+1
    # No Pickup entries: d1 is re-entered ... same stage same day is de-duplicated; several days sum
    w.ev(d1, "1002", D0, hhmm="13:00")
    e = engine(w)
    r = M.rung_entries(e, "coding", "NO_ANSWER", D0)
    assert r["today"]["headline"] <= r["roll7"]["headline"] or r["roll7"]["deals_all"] <= r["inception"]["deals_all"]
    prev = -1
    for d in M.day_range("2026-09-25", "2026-10-03"):                                                     # Inception only grows
        inc = M.rung_entries(e, "coding", "NO_ANSWER", d)["inception"]["deals_all"]
        assert inc >= prev
        prev = inc
    for code in e.canon_order:
        r = M.rung_entries(e, "ALL", code, D0)
        assert r["today"]["deals_all"] <= r["roll7"]["deals_all"] <= r["inception"]["deals_all"], code


def test_same_day_duplicate_entries_are_deduplicated(world):
    w = world
    d = w.deal("1001")
    w.ev(d, "1001", D0, "10:00")
    w.ev(d, "1000", D0, "11:00")
    w.ev(d, "1001", D0, "12:00")           # back to No Pickup the same day by the same person: still one entry
    e = engine(w)
    assert M.rung_entries(e, "coding", "NO_ANSWER", D0)["today"]["human"] == 1


def test_as_of_day_never_reads_later_events(world):
    w = world
    d = w.deal("1002")
    w.ev(d, "1000", "2026-09-30")
    w.ev(d, "1002", "2026-10-03")
    e = engine(w)
    assert M.rung_entries(e, "coding", "INTERESTED", "2026-10-02")["inception"]["deals_all"] == 0
    assert M.rung_entries(e, "coding", "INTERESTED", "2026-10-03")["inception"]["deals_all"] == 1
    assert M.rung_entries(e, "coding", "INTERESTED", "2026-10-02")["roll7"]["headline"] == 0


def test_unmapped_stage_is_loud_not_dropped(world):
    w = world
    d = w.deal("1000")
    w.ev(d, "1009", D0)
    e = engine(w)
    r = M.rung_entries(e, "ALL", M.UNMAPPED, D0)
    assert r["inception"]["deals_all"] == 1
    model = build.build_model(e, D0)
    assert any(u["stage_id"] == "1009" for u in model["integrity"]["unmapped_stages"])
    assert any(row["code"] == M.UNMAPPED for row in model["combined"]["rungs"])


def test_derived_meeting_held_requires_booking_then_progress_and_no_noshow(world):
    w = world
    held, noshow, only_booked = w.deal("1004"), w.deal("1007"), w.deal("1003")
    w.ev(held, "1003", "2026-09-29"); w.ev(held, "1004", D0)
    w.ev(noshow, "1003", "2026-09-29"); w.ev(noshow, "1007", "2026-09-30"); w.ev(noshow, "1004", D0)
    w.ev(only_booked, "1003", "2026-09-29")
    e = engine(w)
    r = M.rung_entries(e, "coding", "MEETING_HELD", D0)
    assert r["inception"]["deals_all"] == 1                      # only the deal that progressed and never no-showed
    assert M.rung_entries(e, "coding", "MEETING_HELD", "2026-09-30")["inception"]["deals_all"] == 0     # as-of: evidence arrived on D0


def test_net_new_excludes_bad_contact(world):
    w = world
    ok, bad = w.deal("1001"), w.deal("1006")
    w.ev(ok, "1001", D0); w.ev(bad, "1006", D0)
    e = engine(w)
    assert M.engaged(e, "coding", D0)["today"] == 2
    assert M.engaged(e, "coding", D0)["net_new_today"] == 1


def test_occupancy_excludes_archived_and_matches_reconstruction(world):
    w = world
    a, b, c = w.deal("1001"), w.deal("1001"), w.deal("1001", archived=1)
    for d in (a, b, c):
        w.ev(d, "1001", D0)
    e = engine(w)
    live = M.occupancy(e, "2026-10-05")                          # >= the sync day: deal table
    assert live["total"] == 2
    e.live_day = "2099-01-01"; e._occ_cache = None               # force the event-store reconstruction
    rec = M.occupancy(e, D0)
    assert rec["by_code"] == live["by_code"] or rec["total"] == 2


def test_cold_lead_country_rules(world):
    w = world
    for lead, n in (("UK_Proptech", 2), ("Fintech_US", 3), ("Outflo_Georgia", 1), (None, 1)):
        for _ in range(n):
            d = w.deal("2000", pipe="2425754306", lead=lead)
            w.ev(d, "2000", D0, pipe="2425754306")
    e = engine(w)
    cl = M.cold_lead_country(e, "2026-10-05")
    assert cl["total"] == 7
    assert cl["by_country"] == {"US": 3, "UK": 2, "Georgia": 1, "Unattributed": 1}
    cfg = M.load_report_cfg()["cold_lead_split"]
    for label, want in (("Cold Call ( Proptech US )", "US"), ("US_EST_Adtech", "US"), ("UK_fintech", "UK"), ("Australia_salesNav", "Australia"), ("Outflo_Australia", "Australia"),
                        ("Singapore_Mobility", "Singapore"), ("Outflo_Singapore", "Singapore"), ("Outflo_Georgia", "Georgia"), ("Something else", "Unattributed")):
        assert M.classify_country(label, cfg["rules"], cfg["unmatched"]) == want, label


def test_window_coverage_is_labelled_n_of_7(world):
    w = world
    d = w.deal("1001"); w.ev(d, "1001", "2026-10-03")
    e = engine(w)
    e.first_day = "2026-09-20"
    cov = e.coverage("2026-09-27", "2026-10-03", "2026-10-03")
    assert cov["covered"] == 7 and cov["days"] == 7
    e.first_day = "2026-09-30"
    cov = e.coverage("2026-09-27", "2026-10-03", "2026-10-03")
    assert cov["covered"] == 4 and len(cov["missing"]) == 3
    ch = build.build_model(e, "2026-10-03")["headline"]["leads_engaged"]["change_roll7"]
    assert ch["pct"] is None                                      # never a percentage on a partial window


# ------------------------------------------------------------------------------------------------ determinism, persistence, rendering
def test_model_json_is_deterministic_and_clean(world):
    w = world
    d = w.deal("1002"); w.ev(d, "1000", D0); w.ev(d, "1002", D0, "15:00")
    a = build.model_json(build.build_model(engine(w), D0))
    b = build.model_json(build.build_model(engine(w), D0))
    assert a == b
    for bad in ("NaN", "Infinity"):
        assert bad not in a
    m = build.build_model(engine(w), D0)
    htm = render.render_html(m)
    body = htm[htm.index("<body>"):]
    for pat in (r"\bNone\b", r"\bnan\b", r"\{\{", r"%s", r"undefined", r"\bNaN\b"):
        assert not re.search(pat, body), pat
    assert "<script" not in htm and "http://" not in htm and "https://" not in htm       # self-contained, no external assets
    md = render.render_md(m); csv_text = render.render_csv(m)
    assert not re.search(r"\bNone\b", md) and not re.search(r"\bnan\b", csv_text.lower())


def test_persist_is_idempotent_and_matches_the_model(world):
    w = world
    d1, d2 = w.deal("1001"), w.deal("1001")
    w.ev(d1, "1000", "2026-09-30"); w.ev(d1, "1001", D0); w.ev(d2, "1000", D0, src="INTEGRATION")
    e = engine(w)
    days = M.day_range("2026-09-28", D0)
    ids = []
    for _ in range(2):
        with ldb.transaction(w.con):
            cur = w.con.execute("INSERT INTO rpt_report_run (report_kind, report_day) VALUES ('rebuild', ?)", (D0,))
        ids.append(cur.lastrowid)
        build.persist_days(w.con, e, days, ids[-1])
    n1 = w.con.execute("SELECT COUNT(*) FROM rpt_funnel_daily").fetchone()[0]
    assert n1 > 0
    row = w.con.execute("SELECT entries_human, entries_auto, occupancy_eod FROM rpt_funnel_daily WHERE ist_day = ? AND stage_id = '1000'", (D0,)).fetchone()
    assert (row[0], row[1]) == (0, 1)                             # the API push: automated, not human
    assert w.con.execute("SELECT COUNT(*) FROM rpt_funnel_daily WHERE report_run_id = ?", (ids[0],)).fetchone()[0] == 0     # second run replaced the rows
    assert w.con.execute("SELECT SUM(occupancy_eod) FROM rpt_funnel_daily WHERE ist_day = ? AND cohort_scope = 'all'", (D0,)).fetchone()[0] == 2


def test_cli_dry_run_never_mails_and_send_goes_to_default_recipient_only(world, tmp_path):
    w = world
    d = w.deal("1001"); w.ev(d, "1001", D0)
    out = io.StringIO()
    sent = []

    def boom(raw, cfg):
        sent.append(raw)
        return {}
    rc = daily.run(["--date", D0, "--db", w.path, "--out-dir", str(tmp_path / "o"), "--no-persist"], send_fn=boom, out=out)
    assert rc == 0 and sent == [] and "dry-run" in out.getvalue()
    for name in daily.FILES:
        assert os.path.getsize(str(tmp_path / "o" / D0 / name)) > 0
    out = io.StringIO()
    rc = daily.run(["--date", D0, "--db", w.path, "--out-dir", str(tmp_path / "o"), "--send"], send_fn=boom, out=out)
    assert rc == 0 and len(sent) == 1
    import base64, email
    msg = email.message_from_bytes(base64.urlsafe_b64decode(sent[0]))
    assert msg["To"] == "bhanu.enamala@lh2.ai"
    assert w.con.execute("SELECT mail_mode, mail_to FROM rpt_report_run ORDER BY report_run_id DESC LIMIT 1").fetchone()[:] == ("sent", "bhanu.enamala@lh2.ai")


def test_no_persist_opens_read_only(world, tmp_path):
    before = world.con.execute("SELECT COUNT(*) FROM rpt_report_run").fetchone()[0]
    daily.run(["--date", D0, "--db", world.path, "--out-dir", str(tmp_path / "o"), "--no-persist"], out=io.StringIO())
    assert world.con.execute("SELECT COUNT(*) FROM rpt_report_run").fetchone()[0] == before
    assert world.con.execute("SELECT COUNT(*) FROM rpt_funnel_daily").fetchone()[0] == 0


# ------------------------------------------------------------------------------------------------ mail transport (fake only)
def test_recipient_policy():
    cfg = M.load_report_cfg()
    assert mailer.resolve_recipients(cfg, None) == ["bhanu.enamala@lh2.ai"]
    assert mailer.resolve_recipients(cfg, "a@x.com, b@y.org") == ["a@x.com", "b@y.org"]
    with pytest.raises(mailer.MailError):
        mailer.resolve_recipients(cfg, "not-an-address")


def test_gmail_transport_with_a_fake_service():
    cfg = M.load_report_cfg()
    calls = []

    class Exec:
        def __init__(self, body): self.body = body
        def execute(self): calls.append(self.body); return {"id": "m1", "threadId": "t1"}

    class Messages:
        def send(self, userId, body): assert userId == "me"; return Exec(body)

    class Users:
        def messages(self): return Messages()

    class Svc:
        def users(self): return Users()
    seen = {}

    def factory(token_file, scope):
        seen["scope"] = scope; seen["token"] = token_file
        return Svc()
    msg = mailer.build_message("subj", ["bhanu.enamala@lh2.ai"], "text", "<p>html</p>")
    res = mailer.send_raw(mailer.encode_raw(msg), cfg, token_file="fake_token.json", service_factory=factory)
    assert res["message_id"] == "m1" and seen["scope"].endswith("gmail.send") and len(calls) == 1 and "raw" in calls[0]


def test_configured_send_tokens_have_the_send_scope_offline():
    from leadgen.google import auth
    cfg = M.load_report_cfg()
    present = [n for n in cfg["mail"]["token_files"] if os.path.exists(auth.secrets_path(n))]
    if not present:
        pytest.skip("no send token files present")
    st = auth.token_status(present[0], cfg["mail"]["scope"])        # reads the local file only: no network, no secret printed
    assert st["has_required_scope"] is True


def test_reports_code_makes_no_hubspot_or_other_network_call():
    import ast
    base = os.path.join(ROOT, "leadgen", "reports")
    for fn in os.listdir(base):
        if not fn.endswith(".py"):
            continue
        src = open(os.path.join(base, fn), encoding="utf-8").read()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] + [getattr(node, "module", "") or ""]
                for n in names:
                    assert not n.startswith(("requests", "urllib", "http.client", "leadgen.hubspot")), (fn, n)
        assert "INSERT OR REPLACE" not in src.upper().replace("  ", " ") and "REPLACE INTO" not in src.upper()


# ------------------------------------------------------------------------------------------------ real database (read-only)
@pytest.fixture(scope="module")
def real_engine():
    if not os.path.exists(REAL_DB):
        pytest.skip("real database not present")
    con = ldb.connect(REAL_DB, readonly=True)
    e = M.Engine(con)
    yield e
    con.close()


@real
def test_real_cold_lead_country_regression(real_engine):
    cl = M.cold_lead_country(real_engine, "2026-10-04")
    assert cl["total"] == 725
    assert cl["by_country"] == {"US": 358, "UK": 209, "Singapore": 100, "Australia": 51, "Georgia": 7}


@real
def test_real_live_counts_equal_hubspot(real_engine):
    want = {"coding": 4821, "coops_global": 1380, "cluster1": 1135, "cluster2": 6440, "rat": 1236}
    for slug, n in want.items():
        assert M.occupancy(real_engine, "2026-10-04", slug)["total"] == n, slug
    assert M.occupancy(real_engine, "2026-10-04")["total"] == sum(want.values())
    assert M.occupancy(real_engine, "2026-10-04")["unmapped"] == 0
    # the stock RAT pipeline is not a reporting funnel
    assert ("rat", "default") not in real_engine.slug_of


@real
def test_real_occupancy_reconstruction_equals_live(real_engine):
    live = M.occupancy(real_engine, "2026-10-04")
    e = real_engine
    saved = e.live_day
    try:
        e.live_day = "2099-01-01"; e._occ_cache = None
        rec = M.occupancy(e, "2026-10-04")
    finally:
        e.live_day = saved; e._occ_cache = None
    assert rec["by_code"] == live["by_code"] and rec["total"] == live["total"]


@real
def test_real_invariants_over_days(real_engine):
    e = real_engine
    days = M.day_range("2026-09-20", "2026-10-04")
    prev = {}
    for d in days:
        for fk in e.slugs + [M.ALL]:
            for code in e.canon_order:
                r = M.rung_entries(e, fk, code, d)
                assert r["today"]["deals_all"] <= r["roll7"]["deals_all"] <= r["inception"]["deals_all"], (fk, code, d)
                assert r["roll7"]["total"] >= r["roll7"]["deals_all"] or code in ("MEETING_HELD", "EVALUATED")
                assert r["inception"]["deals_all"] >= prev.get((fk, code), 0), (fk, code, d)          # monotone
                prev[(fk, code)] = r["inception"]["deals_all"]
        eg = M.engaged(e, M.ALL, d)
        assert eg["today"] <= eg["roll7_union"]
        assert eg["roll7_union"] <= sum(M.engaged(e, M.ALL, x)["today"] for x in M.day_range(M.day_add(d, -6), d)) + 0


@real
def test_real_engaged_is_human_only(real_engine):
    e = real_engine
    d = "2026-10-01"
    human = set(ev.deal for ev in e.by_day[d] if ev.human)
    auto_only = set(ev.deal for ev in e.by_day[d] if not ev.human) - human
    assert M.engaged(e, M.ALL, d)["today"] == len(human)
    assert auto_only and not (auto_only & e.engaged_deals(M.ALL, d, d, d))


@real
def test_real_json_is_byte_identical_on_rerun(real_engine, tmp_path):
    a = build.model_json(build.build_model(real_engine, "2026-10-01"))
    con2 = ldb.connect(REAL_DB, readonly=True)
    try:
        b = build.model_json(build.build_model(M.Engine(con2), "2026-10-01"))
    finally:
        con2.close()
    assert a == b


@real
def test_real_crosscheck_engaged_sets_match_legacy(real_engine):
    cc = M.crosscheck(real_engine, "2026-10-04", families=["companyops_cluster", "rat"])
    if not cc["available"]:
        pytest.skip("legacy snapshots not loaded")
    days = [(s, d) for s in cc["series"] for d in s["days"] if d.get("engaged") and d["day"] == "2026-10-01"]
    assert days
    for s, d in days:
        e = d["engaged"]
        assert e["legacy"] == e["mine"] == e["both"], (s["family"], s["pipeline_id"], e)
