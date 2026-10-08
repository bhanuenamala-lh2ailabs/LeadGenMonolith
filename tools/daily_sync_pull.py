#!/usr/bin/env python3
"""Daily Sync: pull HubSpot stage history for the five verticals, compute WTD/MTD actuals and targets, write the
'Daily Sync' tab of the Supply Team Mapping sheet (SUPPLY_TEAM_MAPPING_SHEET_ID in .env).

This is the REFERENCE implementation for the logic. It is being ported to a Google Apps Script time-driven trigger so
it runs natively inside the sheet, without a machine that has to stay on. Read this file top to bottom before porting;
the comments explain the decisions, not just the steps. See docs/DAILY_SYNC_APPS_SCRIPT_HANDOFF.md for the porting brief.

Usage: .venv/bin/python tools/daily_sync_pull.py [--write]   (without --write, prints the computed tables and stops)
"""
import argparse, datetime as dt, json, math, sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
env = {}
for line in open(ROOT / ".env", encoding="utf-8"):
    if "=" in line and not line.lstrip().startswith("#"):
        k, v = line.split("=", 1); env[k.strip()] = v.strip().strip('"').strip("'")

def hdr(k): return {"Authorization": "Bearer " + env[k], "Content-Type": "application/json"}
MAIN, CO, RAT = hdr("HUBSPOT_KEY_MAIN"), hdr("HUBSPOT_KEY_COMPANYOPS"), hdr("HUBSPOT_KEY_RAT")

# ---------------------------------------------------------------------------------------------------------------
# 1. Calendar: which days count as "working days" for the target math. Sat/Sun always; specific holidays listed here.
#    Only the TARGET denominator uses this. The actual HubSpot data pull always scans every day, holidays included —
#    if a rep genuinely worked on a holiday, that work still shows up in the actuals.
# ---------------------------------------------------------------------------------------------------------------
HOLIDAYS = {dt.date(2026, 10, 2)}  # add more as they come up

def is_working_day(d: dt.date) -> bool:
    return d.weekday() < 5 and d not in HOLIDAYS  # Monday=0 .. Sunday=6

def working_days_between(start: dt.date, end: dt.date) -> int:
    """Inclusive of both ends."""
    n, d = 0, start
    while d <= end:
        if is_working_day(d): n += 1
        d += dt.timedelta(days=1)
    return n

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
today = dt.datetime.now(IST).date()
month_start = today.replace(day=1)
week_start = today - dt.timedelta(days=today.weekday())  # Monday of this week
WTD_DAYS = working_days_between(week_start, today)
MTD_DAYS = working_days_between(month_start, today)
# The actual HubSpot window starts at local midnight of the 1st of the month / the Monday, converted to UTC.
MTD_START = dt.datetime.combine(month_start, dt.time(0, 0), IST).astimezone(dt.timezone.utc)
WTD_START = dt.datetime.combine(week_start, dt.time(0, 0), IST).astimezone(dt.timezone.utc)

# ---------------------------------------------------------------------------------------------------------------
# 2. Relabelling for the Coding deals that live, or used to live, in CompanyOps/RAT (reassigned/moved reps).
#    A deal's full stage history can span two pipelines (its old home, then the Coding pipeline it moved into).
#    Entries in the Coding pipeline use Coding's own labels already; entries still carrying the OLD pipeline's
#    labels are translated through this table.
# ---------------------------------------------------------------------------------------------------------------
REASSIGN = {
    "cold called assigned": "Cold Call", "cold call": "Cold Call", "no pickup": "No Pickup", "no pickup/callback": "No Pickup",
    "interested": "Interested", "closed/won": "Closed/Won", "replied": "Interested", "1st interest sent": "Interested",
    "1st interest follow up": "Interested", "discovery call": "GMeet Fixed", "gmeet1 completed": "GMeet Fixed",
    "call rescheduled": "No Pickup", "callback": "No Pickup", "retired: callback +1 day (use no pickup + task instead)": "No Pickup",
    "sample requested": "Script Shared", "sample follow up": "Script Shared", "sample received": "Script Results Received",
    "negotiation": "Commercial Negotiation", "contract signed": "Deal Contract Signed", "loi signed": "LOI",
    "dead: cold call / wrong number": "Dead/ColdCall/WrongNumber", "dead: cold call / not interested": "Dead/ColdCall/Not Interested",
    "dead: cold call / wrong fit": "Dead/ColdCall/WrongFit", "dead: cold call / no pickup": "Dead/ColdCall/NoPickup",
    "dead: replied / not interested": "Dead/ColdCall/Not Interested",
}

def stage_labels(h, pid):
    return {s["id"]: s["label"] for s in requests.get(f"https://api.hubapi.com/crm/v3/pipelines/deals/{pid}", headers=h, timeout=30).json()["stages"]}

def search_touched(h, pid, since):
    ids, after = [], 0
    while True:
        b = {"filterGroups": [{"filters": [{"propertyName": "pipeline", "operator": "EQ", "value": pid},
             {"propertyName": "hs_lastmodifieddate", "operator": "GTE", "value": since.strftime("%Y-%m-%dT%H:%M:%SZ")}]}],
             "properties": ["dealname"], "limit": 100, "after": after}
        r = requests.post("https://api.hubapi.com/crm/v3/objects/deals/search", headers=h, json=b, timeout=60).json()
        ids += [x["id"] for x in r.get("results", [])]
        if "paging" not in r: return ids
        after = r["paging"]["next"]["after"]

def counts_for_ids(h, ids, label_sources):
    """label_sources: [(labels_map, mode), ...], first match on a stage id wins. mode None = label used as-is;
    'reassign' = translated through REASSIGN. Returns (mtd_counts, wtd_counts), each {canonical_label: distinct_deal_count}."""
    import collections
    wtd, mtd = collections.defaultdict(set), collections.defaultdict(set)
    for did in ids:
        j = requests.get(f"https://api.hubapi.com/crm/v3/objects/deals/{did}?propertiesWithHistory=dealstage", headers=h, timeout=30).json()
        for hent in (j.get("propertiesWithHistory") or {}).get("dealstage", []):
            t = dt.datetime.fromisoformat(hent["timestamp"].replace("Z", "+00:00"))
            if t < MTD_START: continue
            canon = None
            for labels, mode in label_sources:
                raw = labels.get(hent["value"])
                if raw is None: continue
                canon = raw if mode is None else REASSIGN.get(raw.strip().lower())
                break
            if not canon: continue
            # Entry rungs (the funnel's first stage) count any source; every other stage counts human (CRM_UI) moves only.
            any_src = canon.lower() in ("cold call", "cold called assigned", "cold lead")
            if not any_src and hent.get("sourceType") != "CRM_UI": continue
            mtd[canon].add(did)
            if t >= WTD_START: wtd[canon].add(did)
    return {k: len(v) for k, v in mtd.items()}, {k: len(v) for k, v in wtd.items()}

def counts(h, pid, label_source=None):
    return counts_for_ids(h, search_touched(h, pid, MTD_START), [(stage_labels(h, pid), label_source)])

def counts_moved(h, new_pid, old_pid):
    new_labels, old_labels = stage_labels(h, new_pid), stage_labels(h, old_pid)
    ids = search_touched(h, new_pid, MTD_START)
    return counts_for_ids(h, ids, [(new_labels, None), (old_labels, "reassign")])

# ---------------------------------------------------------------------------------------------------------------
# 3. Pull every vertical.
# ---------------------------------------------------------------------------------------------------------------
def pull_all():
    result = {}
    mtd1, wtd1 = counts(MAIN, "default")
    mtd2, wtd2 = counts_moved(CO, "2608440044", "2464812771")
    mtd3, wtd3 = counts_moved(RAT, "2608440047", "2575252183")
    import collections
    result["coding"] = {"mtd": dict(collections.Counter(mtd1) + collections.Counter(mtd2) + collections.Counter(mtd3)),
                        "wtd": dict(collections.Counter(wtd1) + collections.Counter(wtd2) + collections.Counter(wtd3))}
    mtd, wtd = counts(MAIN, "2425754306"); result["coops_global"] = {"mtd": mtd, "wtd": wtd}
    mtd, wtd = counts(CO, "default"); result["cluster1"] = {"mtd": mtd, "wtd": wtd}
    mtd, wtd = counts(CO, "2464812771"); result["cluster2"] = {"mtd": mtd, "wtd": wtd}
    mtd, wtd = counts(RAT, "2575252183"); result["rat"] = {"mtd": mtd, "wtd": wtd}
    return result

# ---------------------------------------------------------------------------------------------------------------
# 4. Row formulas: each Targets-sheet metric is a sum of specific stage labels. A deal that moved past a stage is
#    treated as having passed every earlier one on the same line, so the sum is over "this stage or anything after it".
# ---------------------------------------------------------------------------------------------------------------
METRICS = ["Leads assigned", "Calls attempted", "Calls picked", "Interested", "VC setup", "VC completed",
           "Script/1-pager shared", "Script results/1-pager filled", "Contract/LOI signed"]

def row_formulas(vertical):
    g = lambda c, *keys: sum(c.get(k, 0) for k in keys)
    if vertical == "coding":
        return lambda c: {
            "Leads assigned": g(c, "Cold Call"),
            "Calls attempted": g(c, "No Pickup", "Dead/ColdCall/NoPickup", "Dead/ColdCall/WrongNumber", "Dead/ColdCall/Not Interested", "Interested"),
            "Calls picked": g(c, "Interested", "Dead/ColdCall/Not Interested"),
            "Interested": g(c, "Interested"), "VC setup": g(c, "GMeet Fixed"),
            "VC completed": g(c, "Script Shared", "Dead/GMeet/Privacy Concerns", "Dead/GMeet/wrong fit"),
            "Script/1-pager shared": g(c, "Script Shared"), "Script results/1-pager filled": g(c, "Script Results Received"),
            "Contract/LOI signed": g(c, "Deal Contract Signed"),
        }
    if vertical == "coops_global":
        return lambda c: {
            "Leads assigned": g(c, "Cold Lead"),
            "Calls attempted": g(c, "Communicated", "No Response", "Dead: Communicated / Invalid Contact", "Dead: Communicated / Not Interested", "Dead: Communicated / Wrong Fit", "Dead: Communicated / No Response"),
            "Calls picked": g(c, "Interested", "Dead: Communicated / Not Interested"),
            "Interested": g(c, "Interested"), "VC setup": g(c, "VC Fixed"),
            "VC completed": g(c, "1 Pager Shared", "Dead: VC / Rejected by LH2", "Dead: VC / Not Interested"),
            "Script/1-pager shared": g(c, "1 Pager Shared"), "Script results/1-pager filled": g(c, "1 Pager Output Received"),
            "Contract/LOI signed": g(c, "LOI Signed"),
        }
    if vertical in ("cluster1", "cluster2"):
        return lambda c: {
            "Leads assigned": g(c, "Cold called assigned"),
            "Calls attempted": g(c, "No pickup", "Dead: Cold Call / Wrong Number", "Dead: Cold Call / Not Interested", "Replied"),
            "Calls picked": g(c, "Replied", "Dead: Cold Call / Not Interested"),
            "Interested": g(c, "1st interest sent"), "VC setup": g(c, "Discovery call"),
            "VC completed": g(c, "Dead: Discovery Call / Rejected by LH2", "Dead: Discovery Call / Not Interested", "One pager requested"),
            "Script/1-pager shared": g(c, "One pager requested"), "Script results/1-pager filled": g(c, "One pager received"),
            "Contract/LOI signed": g(c, "LOI signed"),
        }
    if vertical == "rat":
        return lambda c: {
            "Leads assigned": g(c, "Cold Called Assigned"),
            "Calls attempted": g(c, "No Pickup", "Callback", "Dead: ColdCall/WrongNumber", "Dead: ColdCall/NotInterested", "Replied"),
            "Calls picked": g(c, "Replied", "Dead: ColdCall/NotInterested"),
            "Interested": g(c, "1st Interest Sent"), "VC setup": g(c, "Discovery Call"),
            "VC completed": g(c, "Dead: DiscoveryCall/RejectedByLH2", "Dead: DiscoveryCall/NotInterested", "Sample Requested"),
            "Script/1-pager shared": g(c, "Sample Requested"), "Script results/1-pager filled": g(c, "Sample Received"),
            "Contract/LOI signed": g(c, "Contract Signed"),
        }
    raise ValueError(vertical)

# ---------------------------------------------------------------------------------------------------------------
# 5. Targets. The Targets tab only has WEEKLY numbers (no monthly row), one column per vertical, and RAT has none at
#    all — it borrows the '3.4 Specialty Datasets' column (owner: Kartik), which is a clean 1:1 match once the daily
#    Leads-Assigned rate is converted: 200/day x 5 = 1000/week = Specialty Datasets' existing weekly Leads target.
#    A separate daily Leads-Assigned rate per vertical sets a scale FACTOR (new weekly leads target / old weekly
#    leads target); every other metric's old weekly target is multiplied by that same factor before being converted
#    to a working-day rate. This keeps every row's target proportional to the Leads-Assigned change, as asked.
# ---------------------------------------------------------------------------------------------------------------
DAILY_LEADS = {"coding": 500, "coops_global": 100, "cluster1": 100, "cluster2": 200, "rat": 200}
OLD_WEEKLY = {
 "coding":       {"Leads assigned": 1250, "Calls attempted": 1250, "Calls picked": 250, "Interested": None, "VC setup": 50, "VC completed": None, "Script/1-pager shared": 40, "Script results/1-pager filled": 24, "Contract/LOI signed": 5},
 "coops_global": {"Leads assigned": 1000, "Calls attempted": 1000, "Calls picked": 80,  "Interested": None, "VC setup": 8,  "VC completed": None, "Script/1-pager shared": 6,  "Script results/1-pager filled": 3,  "Contract/LOI signed": 1},
 "cluster1":     {"Leads assigned": 1000, "Calls attempted": 1000, "Calls picked": 150, "Interested": None, "VC setup": 15, "VC completed": None, "Script/1-pager shared": 12, "Script results/1-pager filled": 6,  "Contract/LOI signed": 3},
 "cluster2":     {"Leads assigned": 1000, "Calls attempted": 1000, "Calls picked": 150, "Interested": None, "VC setup": 15, "VC completed": None, "Script/1-pager shared": 12, "Script results/1-pager filled": 6,  "Contract/LOI signed": 3},
 "rat":          {"Leads assigned": 1000, "Calls attempted": 1000, "Calls picked": 200, "Interested": None, "VC setup": 40, "VC completed": None, "Script/1-pager shared": 30, "Script results/1-pager filled": 21, "Contract/LOI signed": 4},  # = Specialty Datasets column
}
FACTOR = {v: (DAILY_LEADS[v] * 5) / OLD_WEEKLY[v]["Leads assigned"] for v in OLD_WEEKLY}

def target(vertical, metric):
    """Returns (wtd_target, mtd_target), rounded UP (a target rounds up, never down — rounding down would quietly
    make the bar easier to hit), or (None, None) if the Targets tab has no number for this row."""
    old = OLD_WEEKLY[vertical][metric]
    if old is None: return None, None
    daily_equiv = (old * FACTOR[vertical]) / 5
    return math.ceil(daily_equiv * WTD_DAYS), math.ceil(daily_equiv * MTD_DAYS)

# ---------------------------------------------------------------------------------------------------------------
# 6. Build the table rows and (optionally) write them to the sheet.
# ---------------------------------------------------------------------------------------------------------------
TITLES = {"coding": "Coding", "coops_global": "Company Ops (Global)", "cluster1": "Company Ops (Cluster-1)",
          "cluster2": "Company Ops (Cluster-2)", "rat": "Engineering Datasets (formerly RAT; target column = Targets tab's '3.4 Specialty Datasets')"}

def pct(a, t):
    return "N/A" if not t else round(100 * a / t, 1)

def build_rows(pulled):
    banner = (f"Daily Sync — Supply Funnel, refreshed {today.isoformat()}. WTD = week starting Mon {week_start.isoformat()} "
              f"({WTD_DAYS} working days so far). MTD = month to date ({MTD_DAYS} working days so far; weekends and listed "
              f"holidays excluded from the day count, never from the data pull). Leads Assigned target = daily rate x "
              f"working days elapsed; every other metric's old weekly target is scaled by the same factor, then "
              f"target = (scaled weekly target / 5) x working days elapsed, rounded up.")
    rows = [[banner]]
    for v in ["coding", "coops_global", "cluster1", "cluster2", "rat"]:
        actual = row_formulas(v)
        wa_all, ma_all = actual(pulled[v]["wtd"]), actual(pulled[v]["mtd"])
        rows.append([TITLES[v]])
        rows.append(["Metric", "WTD Actual", "WTD Target", "% Target Achievement", "", "MTD Actual", "MTD Target", "% Target Achievement"])
        for m in METRICS:
            wt, mt = target(v, m)
            wa, ma = wa_all[m], ma_all[m]
            rows.append([m, wa, wt if wt is not None else "—", pct(wa, wt) if wt else "—", "",
                         ma, mt if mt is not None else "—", pct(ma, mt) if mt else "—"])
        rows.append([])
    return rows

def write_sheet(rows):
    sys.path.insert(0, str(ROOT.parent / "hubspot" / "crm_mirror" / "enrich"))
    import gsheets
    s = gsheets.svc("sheets")
    sheet_id = env["SUPPLY_TEAM_MAPPING_SHEET_ID"]
    s.spreadsheets().values().update(spreadsheetId=sheet_id, range="Daily Sync!A1", valueInputOption="USER_ENTERED", body={"values": rows}).execute()

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--write", action="store_true"); a = ap.parse_args()
    print(f"today={today} week_start={week_start} month_start={month_start} WTD_DAYS={WTD_DAYS} MTD_DAYS={MTD_DAYS}")
    pulled = pull_all()
    rows = build_rows(pulled)
    for r in rows:
        if r: print(r)
    if a.write:
        write_sheet(rows)
        print("written to the Daily Sync tab")
    else:
        print("(dry run: pass --write to update the sheet)")

if __name__ == "__main__":
    main()
