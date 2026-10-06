"""Tests of the Supply Funnel Report by Vertical mail (leadgen/reports/vertical_mail.py, config/mail_rows.yaml).

Pure-logic tests build events in memory; no database is needed for them.  The `real_*` tests read db/leadgen.sqlite READ-ONLY and are
skipped when it is absent.  Run:  mkdir -p db/_scratch && .venv/bin/python -m pytest -q tests --basetemp=db/_scratch/pytest_v
"""
import os
import sqlite3

import pytest

from leadgen import config as lcfg
from leadgen.reports import vertical_mail as vm

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL_DB = os.path.join(ROOT, "db", "leadgen.sqlite")
real = pytest.mark.skipif(not os.path.exists(REAL_DB), reason="real database not present")

# The rows the approved mail shows, per vertical, in order (the layout contract).
PDF_ROWS = {
    "coding": ["Leads Assigned", "No Pickup", "Interested", "GMeet Fixed", "Script Shared", "Script Results Received",
               "Commercial Negotiation", "LOI", "Deal Contract Signed", "Closed/Won", "Dead"],
    "coops_global": ["Cold Lead", "Communicated", "No Response", "Interested", "VC Fixed", "1 Pager Shared",
                     "1 Pager Output Received", "Commercial Negotiation", "LOI Signed", "Contract Signed", "Closed/Won", "Dead"],
    "cluster1": ["Leads Assigned", "No Pickup/Callback", "Replied", "Discovery Call Scheduled", "Dead: Discovery Call No Show",
                 "Discovery Call Done", "One Pager Shared", "One Pager Received", "LOI Signed", "Ops Handover",
                 "Dead Cold Call: Wrong Number", "Dead Cold Call: Wrong Fit", "Dead Cold Call: Not Interested", "Dead Cold Call: No Pickup"],
    "cluster2": ["Leads Assigned", "LinkedIn Connected", "No Pickup / Callback", "Replied", "1st Interest", "Discovery Call Scheduled",
                 "One Pager Requested", "One Pager Follow Up", "One Pager Received", "LOI Signed", "Contract Signed",
                 "Ops Data Handover", "Payment Initiation", "Closed/Won", "Dead"],
    "rat": ["Cold Called Assigned", "No Pickup/Callback", "Replied", "1st Interest Sent", "Discovery Call Scheduled", "Call Rescheduled",
            "Sample Requested", "Sample Follow Up", "Sample Received", "Negotiation", "Contract Signed", "Ops Data Handover Done",
            "Payment Initiation"],
}
TITLES = ["Coding Funnel", "Company Ops Global", "Company Ops Cluster 1 India", "Company Ops Cluster 2 India", "Rapid Action Team"]


def vdef_of(key, rows, tile="wins"):
    d = {"key": key, "title": key, "tile": tile, "won_row": "Closed/Won", "samples_row": "Sample Received", "rows": rows}
    return d


def row(name, labels, entry=False, prefix=None, any_source=False):
    return {"name": name, "labels": labels, "entry": entry, "label_prefix": prefix, "any_source": any_source}


def live_rows():
    return vm.load_mail_rows()["verticals"]


# ------------------------------------------------------------------------------------------------ row mapping coverage
def test_config_lists_every_pdf_row_in_order():
    cfg = vm.load_mail_rows()
    by_key = {v["key"]: v for v in cfg["verticals"]}
    assert set(by_key) == set(PDF_ROWS)
    for key, expected in PDF_ROWS.items():
        assert [r["name"] for r in by_key[key]["rows"]] == expected, key
    assert [v["title"] for v in cfg["verticals"]] == TITLES


def test_every_mapped_label_maps_to_one_row_only():
    """A stage label belongs to one printed row, except in a derived row marked overlap (Cluster 1 'Discovery Call Done')."""
    for v in live_rows():
        owner = {}
        for r in v["rows"]:
            for lab in r.get("labels") or []:
                if lab in owner and not (r.get("overlap") or owner[lab][1]):
                    assert False, (v["key"], lab)
                owner.setdefault(lab, (r["name"], bool(r.get("overlap"))))


def test_rows_resolve_against_a_synthetic_stage_table():
    labels = ["Cold Call", "Closed/Won", "Dead/X", "Dead - Y"]
    rows = vm.resolve_rows(vdef_of("t", [row("Leads Assigned", ["Cold Call"], entry=True), row("Closed/Won", ["Closed/Won"]),
                                         row("Dead", [], prefix="Dead")]), labels)
    assert [r.name for r in rows] == ["Leads Assigned", "Closed/Won", "Dead"]
    assert rows[2].matches("Dead - Y") and not rows[2].matches("Cold Call")


def test_missing_label_is_an_error_not_a_silent_zero():
    with pytest.raises(vm.MappingError):
        vm.resolve_rows(vdef_of("t", [row("Leads Assigned", ["Cold Call"], entry=True), row("Ghost", ["No Such Stage"])]), ["Cold Call"])


@real
def test_real_db_every_row_label_exists_in_its_pipeline():
    con = sqlite3.connect("file:%s?mode=ro" % REAL_DB, uri=True)
    try:
        for v in live_rows():
            labels = vm.stage_labels(con, v["account"], v["pipeline_id"])
            vm.resolve_rows(v, labels)
    finally:
        con.close()


# --------------------------------------------------------------------------------------------------------- chips
def test_chip_zero_to_positive_is_up_100():
    c = vm.chip(5, 0)
    assert c["kind"] == "up" and c["text"] == "↑ 100%"


def test_chip_equal_is_flat_including_zero_zero():
    assert vm.chip(7, 7)["text"] == "flat"
    assert vm.chip(0, 0)["text"] == "flat"


def test_chip_up_and_down_percentages():
    assert vm.chip(15, 10) == {"kind": "up", "text": "↑ 50%", "per_day": False}
    assert vm.chip(0, 4) == {"kind": "down", "text": "↓ 100%", "per_day": False}


def test_chip_is_na_when_a_side_has_no_data():
    assert vm.chip(None, 4)["kind"] == "na"
    assert vm.chip(4, None)["kind"] == "na"


def test_chip_on_partial_window_compares_per_covered_day():
    # 6 covered days (120 -> 20/day) against 7 covered days (140 -> 20/day): flat per day, flagged as per-day basis
    c = vm.chip(120, 140, cov_cur=6, n_cur=7, cov_prev=7, n_prev=7)
    assert c["text"] == "flat" and c["per_day"] is True


def test_daily_chip_withheld_for_partial_today_and_for_weekend_yesterday():
    assert vm.daily_chip(3, 9, "partial", "ok")["text"] == "—"  # dash, not a change, for a partial day
    assert vm.daily_chip(3, 0, "ok", "weekend")["text"] == "vs weekend"


def test_wins_tile_text():
    assert vm.week_sub(0) == "flat this week"
    assert vm.week_sub(2) == "+2 this week"


# ------------------------------------------------------------------------------------------------ weekend and gaps
def test_weekend_zero_is_weekend_status_and_a_real_covered_zero():
    days = ["2026-10-03", "2026-10-04", "2026-10-05"]           # Sat, Sun, Mon
    totals = {"2026-09-28": 1500, "2026-09-29": 1600, "2026-09-30": 1400, "2026-10-01": 1300}
    st = vm.day_status_map(days, totals, "2026-10-05", report_partial=True)
    assert st == {"2026-10-03": "weekend", "2026-10-04": "weekend", "2026-10-05": "partial"}
    value, cov, n = vm.wsum(lambda d: 0, days[:2], st)
    assert (value, cov, n) == (0, 2, 2)                          # weekend zeros are real and count as covered


def test_missing_weekday_is_gap_not_zero():
    days = ["2026-10-01", "2026-10-02", "2026-10-03"]
    totals = {"2026-09-28": 1500, "2026-09-29": 1600, "2026-09-30": 1400, "2026-10-01": 1300, "2026-10-02": 1}
    st = vm.day_status_map(days, totals, "2026-10-05", report_partial=False)
    assert st["2026-10-02"] == "gap"                              # 1 event on a Friday, far below 5 % of the weekday median
    value, cov, n = vm.wsum(lambda d: {"2026-10-01": 4, "2026-10-02": 0, "2026-10-03": 0}[d], days, st)
    assert (value, cov, n) == (4, 2, 3)                          # the gap day contributes nothing and is not counted as covered


def test_missing_day_renders_as_none_not_zero_in_a_section():
    rows_cfg = [row("Cold Lead", ["Cold Lead"], entry=True), row("Closed/Won", ["Closed/Won"], any_source=True)]
    v = vm.Vertical(vdef_of("t", rows_cfg), vm.resolve_rows(vdef_of("t", rows_cfg), ["Cold Lead", "Closed/Won"]), [])
    days7 = vm.window("2026-10-05", 7)
    prev7 = vm.window("2026-09-28", 7)
    st = {d: "ok" for d in days7 + prev7}
    st["2026-10-04"] = "gap"
    s = vm.build_section(v, "2026-10-05", st, days7, prev7)
    assert s["rows"][0]["yest"] is None
    assert s["rows"][0]["chip_day"]["kind"] == "na"
    assert s["rows"][0]["roll7_cov"] == 6


def test_window_helpers():
    assert vm.window("2026-10-05", 3) == ["2026-10-03", "2026-10-04", "2026-10-05"]
    assert vm.window(vm.add_days("2026-10-05", -7), 7)[0] == "2026-09-22"


# ---------------------------------------------------------------------------------------------- counting rules
def test_entry_row_any_source_other_rows_human_only_and_inception_is_all_time():
    rows = vm.resolve_rows(vdef_of("t", [row("Leads Assigned", ["Cold Call"], entry=True), row("Interested", ["Interested"])]),
                           ["Cold Call", "Interested"])
    events = [
        (1, "Cold Call", "2026-10-01", False),       # API entry: counts for the entry row
        (1, "Interested", "2026-10-02", False),      # API move: does NOT count for a human row
        (2, "Interested", "2026-10-03", True),       # human move: counts
        (2, "Interested", "2026-10-05", True),       # same deal again on another day: distinct per day, once in inception
    ]
    v = vm.Vertical(vdef_of("t", []), rows, events)
    assert v.day_count(0, "2026-10-01") == 1
    assert v.day_count(1, "2026-10-02") == 0
    assert v.inception(1, "2026-10-04") == 1
    assert v.inception(1, "2026-10-05") == 1
    assert v.day_count(1, "2026-10-05") == 1


def test_dead_row_is_the_union_of_dead_labels_and_engaged_counts_distinct_deals():
    rows = vm.resolve_rows(vdef_of("t", [row("Dead", [], prefix="Dead")]), ["Dead/A", "Dead - B", "Interested"])
    events = [(1, "Dead/A", "2026-10-01", True), (1, "Dead - B", "2026-10-01", True), (2, "Dead - B", "2026-10-01", False),
              (3, "Interested", "2026-10-01", True)]
    v = vm.Vertical(vdef_of("t", []), rows, events)
    assert v.day_count(0, "2026-10-01") == 1                    # deal 1 once despite two dead stages; deal 2 is API so excluded
    assert v.engaged_day("2026-10-01") == 2                      # deals 1 and 3 by human move; deal 2 is not human
    assert v.engaged_union(["2026-10-01", "2026-10-02"]) == {1, 3}


# -------------------------------------------------------------------------------------------- real database smoke
@real
def test_real_report_has_five_sections_and_no_zero_for_the_partial_or_gap_days():
    con = sqlite3.connect("file:%s?mode=ro" % REAL_DB, uri=True)
    con.row_factory = sqlite3.Row
    try:
        rep = vm.build_report(con, "2026-10-05")
    finally:
        con.close()
    assert [s["title"] for s in rep["sections"]] == TITLES
    assert rep["status"]["2026-10-05"] == "partial"
    assert rep["status"]["2026-10-03"] == "weekend" and rep["status"]["2026-10-04"] == "weekend"
    assert rep["status"]["2026-10-02"] == "gap"
    html = vm.render_html(rep)
    assert "%s" not in html and "%(" not in html
    assert "SAMPLES RECEIVED TO DATE" in html and "WINS TO DATE" in html


def test_send_path_refuses_a_recipient_other_than_the_default_without_to():
    from leadgen.reports import mailer
    cfg = lcfg.load_yaml("report")
    assert mailer.resolve_recipients(cfg, None) == [cfg["mail"]["default_to"]]
    assert cfg["mail"]["default_to"] == "bhanu.enamala@lh2.ai"
