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

def search_all(h, pid):
    """Every deal ever in this pipeline, no date floor - needed for ITD (inception to date). Added
    2026-10-11 when the Performance Report sheet's ITD column (already live, already relied on) turned
    out to have no computation behind it in this script at all - it only ever did WTD/MTD. Bigger pull
    than search_touched (which only finds deals touched since MTD_START): for a pipeline like MAIN with
    4,765 deals this is most/all of them, not just this month's activity.
    Returns (ids, owner_map) - owner_map is {deal_id: hubspot_owner_id}, added 2026-10-12 for the
    per-rep breakdown tables."""
    ids, owner_map, after = [], {}, 0
    while True:
        b = {"filterGroups": [{"filters": [{"propertyName": "pipeline", "operator": "EQ", "value": pid}]}],
             "properties": ["dealname", "hubspot_owner_id"], "limit": 100, "after": after}
        r = requests.post("https://api.hubapi.com/crm/v3/objects/deals/search", headers=h, json=b, timeout=60).json()
        for x in r.get("results", []):
            ids.append(x["id"])
            owner_map[x["id"]] = (x.get("properties") or {}).get("hubspot_owner_id")
        if "paging" not in r: return ids, owner_map
        after = r["paging"]["next"]["after"]

def counts_for_ids(h, ids, label_sources, owner_map=None):
    """label_sources: [(labels_map, mode), ...], first match on a stage id wins. mode None = label used as-is;
    'reassign' = translated through REASSIGN. Returns (itd_counts, mtd_counts, wtd_counts), each
    {canonical_label: distinct_deal_count}. All three come from ONE pass over each deal's full stage
    history (no separate pull per window) - ITD has no date floor, MTD/WTD filter the same events down.
    If owner_map is given, ALSO returns a 4th dict {owner_id: (itd_counts, mtd_counts, wtd_counts)} -
    added 2026-10-12 for per-rep breakdown tables, same underlying pull, no extra API calls."""
    import collections
    itd, wtd, mtd = collections.defaultdict(set), collections.defaultdict(set), collections.defaultdict(set)
    by_owner = collections.defaultdict(lambda: (collections.defaultdict(set), collections.defaultdict(set), collections.defaultdict(set)))
    for did in ids:
        j = requests.get(f"https://api.hubapi.com/crm/v3/objects/deals/{did}?propertiesWithHistory=dealstage", headers=h, timeout=30).json()
        owner = owner_map.get(did) if owner_map else None
        for hent in (j.get("propertiesWithHistory") or {}).get("dealstage", []):
            t = dt.datetime.fromisoformat(hent["timestamp"].replace("Z", "+00:00"))
            canon = None
            for labels, mode in label_sources:
                raw = labels.get(hent["value"])
                if raw is None: continue
                canon = raw if mode is None else REASSIGN.get(raw.strip().lower())
                break
            if not canon: continue
            # Entry rungs (the funnel's first stage) count any source; every other stage counts human (CRM_UI) moves only.
            # FIXED 2026-10-12: this was an EXACT-match tuple check against "cold lead" - Cluster 1/2's
            # real entry stage after today's migration is "Cold lead assigned" (not an exact match), so
            # every automated bulk push (our own dailyPush batch creates, all INTEGRATION-sourced, not
            # CRM_UI) was being wrongly excluded from "Leads assigned" - undercounting the metric that
            # matters most. Found live via a sanity check: "Picked to assigned %" came out at 10300%.
            any_src = canon.lower() in ("cold call", "cold called assigned", "cold lead", "cold lead assigned")
            if not any_src and hent.get("sourceType") != "CRM_UI": continue
            itd[canon].add(did)
            if t >= MTD_START: mtd[canon].add(did)
            if t >= WTD_START: wtd[canon].add(did)
            if owner:
                oi, om, ow = by_owner[owner]
                oi[canon].add(did)
                if t >= MTD_START: om[canon].add(did)
                if t >= WTD_START: ow[canon].add(did)
    agg = ({k: len(v) for k, v in itd.items()}, {k: len(v) for k, v in mtd.items()}, {k: len(v) for k, v in wtd.items()})
    if owner_map is None:
        return agg
    owner_agg = {o: ({k: len(v) for k, v in oi.items()}, {k: len(v) for k, v in om.items()}, {k: len(v) for k, v in ow.items()})
                 for o, (oi, om, ow) in by_owner.items()}
    return agg + (owner_agg,)

def counts(h, pid, label_source=None, by_owner=False):
    ids, owner_map = search_all(h, pid)
    return counts_for_ids(h, ids, [(stage_labels(h, pid), label_source)], owner_map=owner_map if by_owner else None)

def counts_moved(h, new_pid, old_pid, by_owner=False):
    new_labels, old_labels = stage_labels(h, new_pid), stage_labels(h, old_pid)
    ids, owner_map = search_all(h, new_pid)
    return counts_for_ids(h, ids, [(new_labels, None), (old_labels, "reassign")], owner_map=owner_map if by_owner else None)

# ---------------------------------------------------------------------------------------------------------------
# 2b. Rep maps - owner_id -> display name, only for the 3 verticals the per-rep breakdown was asked for
#     (2026-10-12). Coding merges 3 portals so its 5 reps' owner ids come from 3 different HubSpot accounts.
# ---------------------------------------------------------------------------------------------------------------
REP_MAPS = {
    "cluster1": {"168609107": "Manit", "169098409": "Pratyush"},
    "cluster2": {"168015618": "Tanisha", "168015679": "Amisha"},
    "coding": {"96574824": "Lamiya", "96573782": "Yuktha", "168572330": "Shagufta",
               "168341981": "Vaishnavi", "98906502": "Harsha"},
}

# ---------------------------------------------------------------------------------------------------------------
# 3. Pull every vertical. BY_OWNER verticals also get a per-rep breakdown (same pull, no extra API calls).
# ---------------------------------------------------------------------------------------------------------------
BY_OWNER_VERTICALS = {"cluster1", "cluster2", "coding"}

def _checkpoint(result):
    """Saves whatever has been computed SO FAR, after every vertical - not just at the very end. Added
    2026-10-12: a ~75+ minute run with zero output until the full thing returns means a crash (or just
    long enough that the person watching can't tell if it's stuck) loses everything already done, same
    shape as the earlier Apollo poll_only incident. This makes every already-finished vertical durable
    the moment it finishes, independent of whether the rest of the run ever completes."""
    PULL_CACHE.parent.mkdir(parents=True, exist_ok=True)
    PULL_CACHE.write_text(json.dumps(result))

def pull_all():
    result = {}
    import collections
    r1 = counts(MAIN, "default", by_owner=True)
    r2 = counts_moved(CO, "2608440044", "2464812771", by_owner=True)
    r3 = counts_moved(RAT, "2608440047", "2575252183", by_owner=True)
    itd1, mtd1, wtd1, owners1 = r1
    itd2, mtd2, wtd2, owners2 = r2
    itd3, mtd3, wtd3, owners3 = r3
    merged_owners = {}
    for owners in (owners1, owners2, owners3):
        for oid, (oi, om, ow) in owners.items():
            if oid not in merged_owners:
                merged_owners[oid] = ({}, {}, {})
            poi, pom, pow_ = merged_owners[oid]
            merged_owners[oid] = (dict(collections.Counter(poi) + collections.Counter(oi)),
                                   dict(collections.Counter(pom) + collections.Counter(om)),
                                   dict(collections.Counter(pow_) + collections.Counter(ow)))
    result["coding"] = {"itd": dict(collections.Counter(itd1) + collections.Counter(itd2) + collections.Counter(itd3)),
                        "mtd": dict(collections.Counter(mtd1) + collections.Counter(mtd2) + collections.Counter(mtd3)),
                        "wtd": dict(collections.Counter(wtd1) + collections.Counter(wtd2) + collections.Counter(wtd3)),
                        "by_owner": merged_owners}
    _checkpoint(result)
    itd, mtd, wtd = counts(MAIN, "2425754306"); result["coops_global"] = {"itd": itd, "mtd": mtd, "wtd": wtd}
    _checkpoint(result)
    itd, mtd, wtd, owners = counts(CO, "default", by_owner=True); result["cluster1"] = {"itd": itd, "mtd": mtd, "wtd": wtd, "by_owner": owners}
    _checkpoint(result)
    itd, mtd, wtd, owners = counts(CO, "2464812771", by_owner=True); result["cluster2"] = {"itd": itd, "mtd": mtd, "wtd": wtd, "by_owner": owners}
    _checkpoint(result)
    itd, mtd, wtd = counts(RAT, "2575252183"); result["rat"] = {"itd": itd, "mtd": mtd, "wtd": wtd}
    _checkpoint(result)
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
            # FIXED 2026-10-12: the flat "No Pickup" stage was retired today (migrated to a 3-attempt
            # chain, matching Cluster 1/2) - 1,621 in-flight deals were moved to "No Pickup - Attempt 1"
            # as part of that migration, and 2 new Interested-stage dead reasons were added
            # (No Response / Not Interested), mirroring Cluster 1/2's own reason set exactly.
            "Calls attempted": g(c, "No Pickup - Attempt 1", "No Pickup - Attempt 2", "No Pickup - Attempt 3",
                                  "Dead/ColdCall/NoPickup", "Dead/ColdCall/WrongNumber", "Dead/ColdCall/Not Interested", "Interested"),
            "Calls picked": g(c, "Interested", "Dead/ColdCall/Not Interested"),
            "Interested": g(c, "Interested"), "VC setup": g(c, "GMeet Fixed"),
            "VC completed": g(c, "Script Shared", "Dead/GMeet/Privacy Concerns", "Dead/GMeet/wrong fit"),
            "Script/1-pager shared": g(c, "Script Shared"), "Script results/1-pager filled": g(c, "Script Results Received"),
            # Coding-only, user asked 2026-10-09: count LOI-reached deals alongside Deal Contract Signed, not
            # contract-signed alone. NOTE this is a stage-visit sum, same convention as every other row on this
            # line (e.g. Calls attempted above) — it is not deduped across the two labels, so a deal whose history
            # shows both LOI and Deal Contract Signed (the normal case: LOI happens before the contract is signed)
            # contributes to both terms, same as how "Calls attempted" already sums No Pickup + Interested even
            # though one deal can visit both. Not changed to a dedup'd union: that would be a different aggregation
            # model than every other row in this file uses, not a fix to this one row.
            # FIXED 2026-10-12: user also asked to add Closed/Won deals to this row, not added until now.
            # "Closed/Won" is REASSIGN's existing canonical label for deals that moved in from another
            # pipeline already closed-won - same stage-visit-sum convention, not deduped against the other two.
            "Contract/LOI signed": g(c, "LOI", "Deal Contract Signed", "Closed/Won"),
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
            # FIXED 2026-10-12: these used to say "Cold called assigned" / "No pickup" (the stage names
            # from before today's Cluster 1/2 migration). The rename to "Cold lead assigned" and the
            # split of "No pickup" into the "No Pickup - Attempt 1/2/3" chain left this file silently
            # undercounting - Leads assigned read a flat 0 for both verticals, found live doing the ITD
            # work below. Calls attempted now sums all 3 attempt stages, since reaching ANY of them means
            # at least one real dial happened - that's the same thing "No pickup" used to mean alone.
            "Leads assigned": g(c, "Cold lead assigned"),
            "Calls attempted": g(c, "No Pickup - Attempt 1", "No Pickup - Attempt 2", "No Pickup - Attempt 3",
                                  "Dead: Cold Call / Wrong Number", "Dead: Cold Call / Not Interested", "Replied"),
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
DAILY_LEADS = {"coding": 500, "coops_global": 100, "cluster1": 200, "cluster2": 200, "rat": 200}
# FIXED 2026-10-12: cluster1 was 100 here, silently halving EVERY target for Cluster-1 (Leads
# assigned, Calls attempted, Calls picked, VC setup, Script shared, Script results, Contract/LOI) —
# caught live when the user asked to check VC setup's WTD target against the Targets tab (showed 8,
# should be 15). Cluster 1 has 2 reps (Amisha + Manit) each needing 100 leads/day = 200/day team-wide,
# same as Cluster 2 (Vaishnavi + Tanisha) — the two clusters' daily rate was never actually different,
# this was just a typo from when the clusters were first split out.
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

# ---------------------------------------------------------------------------------------------------------------
# 7. Performance Report sheet - one TAB per vertical. Redesigned 2026-10-12 per explicit feedback on the
#    first version:
#    (a) each WEEK gets its own frozen block, appended under the previous week's - not overwritten in
#        place every refresh. A block for the current week IS still updated in place on every daily
#        refresh within that week (so "today's numbers" stay current); only a genuine week change
#        appends a new block below, leaving every prior week untouched.
#    (b) cluster1/cluster2/coding additionally get a per-rep breakdown table under the team block,
#        and the granular/derived rows are grouped into one collapsible row-group (matching how Cluster
#        2's original hand-built block worked) instead of interleaved with the core 9.
# ---------------------------------------------------------------------------------------------------------------
PERF_REPORT_TABS = {"coding": "Coding", "coops_global": "Company Ops (Global)", "cluster1": "Company Ops (Cluster-1)",
                     "cluster2": "Company Ops (Cluster-2)", "rat": "Engineering Datasets"}

import re as _re
WEEK_BANNER_RE = _re.compile(r"week starting Mon (\d{4}-\d{2}-\d{2})")

def pctstr(a, b):
    if not b: return "—"
    return f"{round(100 * a / b, 1)}%"

def build_core_rows(v, pulled, prefix_label=None):
    """The 9 core metric rows, WTD/WTD-target/%/MTD/MTD-target/%//ITD. prefix_label overrides the
    banner's vertical name (used for per-rep sub-blocks where the 'vertical' is really one rep)."""
    actual = row_formulas(v)
    ia_all, wa_all, ma_all = actual(pulled["itd"]), actual(pulled["wtd"]), actual(pulled["mtd"])
    rows = []
    for m in METRICS:
        wt, mt = target(v, m)
        wa, ma, ia = wa_all[m], ma_all[m], ia_all[m]
        rows.append([m, wa, wt if wt is not None else "—", pct(wa, wt) if wt else "—", "",
                     ma, mt if mt is not None else "—", pct(ma, mt) if mt else "—", "", ia])
    return rows, wa_all, ma_all, ia_all

def build_granular_rows(v, core_wa, core_ma, core_ia, granular_stats):
    """The derived/granular rows, as one contiguous block (so they can be grouped/collapsed together) -
    real numbers where feasible from this run's own data, honest 'n/a' + reason where not."""
    g = granular_stats
    la_w, la_m, la_i = core_wa["Leads assigned"], core_ma["Leads assigned"], core_ia["Leads assigned"]
    cp_w, cp_m, cp_i = core_wa["Calls picked"], core_ma["Calls picked"], core_ia["Calls picked"]
    intr_w, intr_m, intr_i = core_wa["Interested"], core_ma["Interested"], core_ia["Interested"]
    vcc_w, vcc_m, vcc_i = core_wa["VC completed"], core_ma["VC completed"], core_ia["VC completed"]
    return [
        ["Same-day lead coverage %", "n/a", "—", "—", "", "n/a", "—", "—", "", "n/a"],
        ["Email/WhatsApp coverage % - non-picked leads", "n/a", "—", "—", "", "n/a", "—", "—", "", "n/a"],
        ["Email/WhatsApp coverage % - all leads", f"{g['pct_touch']}%", "—", "—", "", f"{g['pct_touch']}%", "—", "—", "", f"{g['pct_touch']}%"],
        ["Picked to assigned %", pctstr(cp_w, la_w), "—", "—", "", pctstr(cp_m, la_m), "—", "—", "", pctstr(cp_i, la_i)],
        ["Follow-up coverage - call-picked leads", "n/a", "—", "—", "", "n/a", "—", "—", "", "n/a"],
        ["Follow-up coverage - interested leads", "n/a", "—", "—", "", "n/a", "—", "—", "", "n/a"],
        ["Follow-up coverage - VC no-shows", "n/a", "—", "—", "", "n/a", "—", "—", "", "n/a"],
        ["VC completed to interested %", pctstr(vcc_w, intr_w), "—", "—", "", pctstr(vcc_m, intr_m), "—", "—", "", pctstr(vcc_i, intr_i)],
        ["Same-day script-shared coverage %", "n/a", "—", "—", "", "n/a", "—", "—", "", "n/a"],
    ]

REP_TABLE_COL = "L"  # per-rep block sits here, to the right of the main A-J table - a blank column K
                      # separates them, matching the "horizontally next to" layout asked for 2026-10-12.

def build_week_block(v, pulled, granular_stats_by_vertical, rep_names):
    """Full block for one vertical, one week. Returns (main_rows, rep_rows, n_core_rows, n_granular_rows):
    main_rows go in columns A-J (banner, header, 9 core (contiguous), granular (contiguous, row-grouped)).
    rep_rows go in columns L+ (its own small title/header/rep-rows block), row-aligned to start at the
    SAME row as main_rows - placed beside the main table, not stacked under it, and column-grouped
    (not row-grouped) since it occupies different columns on the SAME rows as part of the main block."""
    # TRIMMED 2026-10-12 per user: banner keeps only the week/refresh stamp, no WTD/MTD/ITD glossary text.
    banner = f"{PERF_REPORT_TABS[v]} — week starting Mon {week_start.isoformat()}, refreshed {today.isoformat()}."
    header = ["Metric", "WTD Actual", "WTD Target", "% Target Achievement", "", "MTD Actual", "MTD Target", "% Target Achievement", "", "ITD Actual (new tracking)"]
    main_rows = [[banner], header]
    core_rows, wa_all, ma_all, ia_all = build_core_rows(v, pulled)
    main_rows += core_rows
    n_core = len(core_rows)
    n_granular = 0
    if v in granular_stats_by_vertical:
        granular_rows = build_granular_rows(v, wa_all, ma_all, ia_all, granular_stats_by_vertical[v])
        main_rows += granular_rows
        n_granular = len(granular_rows)
    main_rows.append([])  # spacer before the next week's block

    rep_rows = []
    if v in rep_names and pulled.get("by_owner"):
        rep_rows.append([f"{PERF_REPORT_TABS[v]} — per rep (MTD actuals)"])
        rep_rows.append(["Rep", "Leads assigned", "Calls attempted", "Calls picked", "Interested", "VC setup",
                          "VC completed", "Script/1-pager shared", "Script results/1-pager filled", "Contract/LOI signed"])
        actual = row_formulas(v)
        for oid, name in rep_names[v].items():
            owner_counts = pulled["by_owner"].get(oid)
            if not owner_counts:
                rep_rows.append([name] + [0] * 9)
                continue
            o_itd, o_mtd, o_wtd = owner_counts
            ma_rep = actual(o_mtd)
            rep_rows.append([name] + [ma_rep[m] for m in METRICS])
    return main_rows, rep_rows, n_core, n_granular

def find_or_append_week_row(s, sheet_id, tab):
    """Reads the tab's full existing content, finds every week block's banner + start row. If the
    CURRENT week (week_start) already has a block, returns (its_start_row, its_old_length) to update in
    place. Otherwise returns (next_free_row, None) to append a brand-new block below everything existing
    - this is the actual fix for the 'overwrote last week's table' feedback: a week change now appends,
    it never overwrites a prior week's frozen numbers."""
    sys.path.insert(0, str(ROOT.parent / "hubspot" / "crm_mirror" / "enrich"))
    import gsheets
    try:
        existing = gsheets.read(sheet_id, f"{tab}!A1:A500")
    except Exception:
        existing = []
    week_starts = []  # (row_index_1based, date_str)
    for i, r in enumerate(existing, start=1):
        if not r: continue
        m = WEEK_BANNER_RE.search(r[0])
        if m: week_starts.append((i, m.group(1)))
    if not week_starts:
        return 1, None
    last_row, last_date = week_starts[-1]
    if last_date == week_start.isoformat():
        return last_row, len(existing) - last_row + 1
    return len(existing) + 1, None

def write_performance_report(pulled_all):
    sys.path.insert(0, str(ROOT.parent / "hubspot" / "crm_mirror" / "enrich"))
    import gsheets
    s = gsheets.svc("sheets")
    sheet_id = env["PERFORMANCE_REPORT_SHEET_ID"]
    meta = s.spreadsheets().get(spreadsheetId=sheet_id, fields="sheets.properties").execute()
    sheet_ids = {sh["properties"]["title"]: sh["properties"]["sheetId"] for sh in meta["sheets"]}

    granular_stats_by_vertical = _load_granular_stats()

    for v, tab in PERF_REPORT_TABS.items():
        pulled = pulled_all[v]
        start_row, old_len = find_or_append_week_row(s, sheet_id, tab)
        main_rows, rep_rows, n_core, n_granular = build_week_block(v, pulled, granular_stats_by_vertical, REP_MAPS)

        if old_len and old_len > len(main_rows):
            # this week's block shrank (shouldn't normally happen, but don't leave stale tail rows)
            s.spreadsheets().values().clear(spreadsheetId=sheet_id, range=f"{tab}!A{start_row}:U{start_row + old_len}").execute()
        s.spreadsheets().values().update(spreadsheetId=sheet_id, range=f"{tab}!A{start_row}",
            valueInputOption="USER_ENTERED", body={"values": main_rows}).execute()
        if rep_rows:
            s.spreadsheets().values().update(spreadsheetId=sheet_id, range=f"{tab}!{REP_TABLE_COL}{start_row}",
                valueInputOption="USER_ENTERED", body={"values": rep_rows}).execute()

        if tab in sheet_ids:
            group_requests = []
            if n_granular:
                # Sheets API row indices are 0-based; start_row here is 1-based (sheet row 1 = index 0).
                # 0-based granular start = (start_row-1) [banner] + 1 [header] + n_core [core rows]
                #                        = start_row + 1 + n_core.
                # Verified against a dry-run row dump (fake data, no HubSpot calls) before trusting this
                # on the real expensive pull - two earlier versions of this exact formula were off by one.
                g_start = start_row + 1 + n_core
                g_end = g_start + n_granular
                group_requests.append({"addDimensionGroup": {"range": {"sheetId": sheet_ids[tab], "dimension": "ROWS",
                                                                          "startIndex": g_start, "endIndex": g_end}}})
            if rep_rows:
                # Per-rep sits beside the main table (same rows, different columns) - collapsing it must
                # be a COLUMN group, not a row group, or it would also hide the main table's rows that
                # happen to share the same row range. 2026-10-12: fixed before this ever shipped wrong -
                # a row-group here would have collapsed the core/granular metrics too.
                col_start = ord(REP_TABLE_COL) - ord("A")  # 0-based column index, "L" -> 11
                col_end = col_start + 10  # rep name + 9 metric columns
                group_requests.append({"addDimensionGroup": {"range": {"sheetId": sheet_ids[tab], "dimension": "COLUMNS",
                                                                          "startIndex": col_start, "endIndex": col_end}}})
            if group_requests:
                try:
                    s.spreadsheets().batchUpdate(spreadsheetId=sheet_id, body={"requests": group_requests}).execute()
                except Exception as e:
                    print(f"    (grouping skipped for {tab}: {e})")
        print(f"  wrote {tab} (row {start_row}{', updated in place' if old_len else ', new week block appended'})")

def _load_granular_stats():
    """Fast pass (no per-deal history) - avg call_attempts_count + followup_channel_touch coverage %,
    per vertical. See tools/logs or scratchpad granular_rows_fix.py for the standalone version this was
    lifted from; folded in here so write_performance_report is self-contained."""
    PORTALS = {
        "cluster1": [(env["HUBSPOT_KEY_COMPANYOPS"], "default")],
        "cluster2": [(env["HUBSPOT_KEY_COMPANYOPS"], "2464812771")],
        "coding": [(env["HUBSPOT_KEY_MAIN"], "default"), (env["HUBSPOT_KEY_COMPANYOPS"], "2608440044"), (env["HUBSPOT_KEY_RAT"], "2608440047")],
    }
    out = {}
    for v, portals in PORTALS.items():
        total_n, weighted_att, weighted_touch = 0, 0.0, 0.0
        for key, pipeline in portals:
            h = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
            body = {"filterGroups": [{"filters": [{"propertyName": "pipeline", "operator": "EQ", "value": pipeline}]}],
                    "properties": ["call_attempts_count", "followup_channel_touch"], "limit": 100}
            deals, after = [], None
            while True:
                b = dict(body)
                if after: b["after"] = after
                r = requests.post("https://api.hubapi.com/crm/v3/objects/deals/search", headers=h, json=b, timeout=60).json()
                deals += r.get("results", [])
                after = r.get("paging", {}).get("next", {}).get("after")
                if not after: break
            n = len(deals)
            total_n += n
            weighted_att += sum(float(d["properties"].get("call_attempts_count") or 0) for d in deals)
            weighted_touch += sum(1 for d in deals if d["properties"].get("followup_channel_touch"))
        out[v] = {"avg_attempts": round(weighted_att / total_n, 2) if total_n else 0,
                   "pct_touch": round(100 * weighted_touch / total_n, 1) if total_n else 0}
    return out

PULL_CACHE = ROOT / "tools" / "logs" / "last_pull_cache.json"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="write the Daily Sync tab (Supply Team Mapping sheet)")
    ap.add_argument("--write-performance-report", action="store_true", help="write the 5 per-team tabs (Supply Team Performance Report sheet)")
    ap.add_argument("--use-cache", action="store_true", help="skip the slow HubSpot pull, reuse the last run's cached result "
                     "(tools/logs/last_pull_cache.json) - for iterating on WRITE/formatting logic only, never for a real "
                     "scheduled refresh (the data goes stale the moment the cache was written).")
    a = ap.parse_args()
    print(f"today={today} week_start={week_start} month_start={month_start} WTD_DAYS={WTD_DAYS} MTD_DAYS={MTD_DAYS}")
    if a.use_cache and PULL_CACHE.exists():
        pulled = json.loads(PULL_CACHE.read_text())
        print(f"(using cached pull from {PULL_CACHE} - NOT fresh HubSpot data)")
    else:
        pulled = pull_all()
        PULL_CACHE.parent.mkdir(parents=True, exist_ok=True)
        PULL_CACHE.write_text(json.dumps(pulled))
        print(f"cached this pull -> {PULL_CACHE} (use --use-cache to reuse it for a formatting-only re-run)")
    rows = build_rows(pulled)
    for r in rows:
        if r: print(r)
    if a.write:
        write_sheet(rows)
        print("written to the Daily Sync tab")
    if a.write_performance_report:
        write_performance_report(pulled)
        print("written to the Performance Report tabs")
    if not a.write and not a.write_performance_report:
        print("(dry run: pass --write and/or --write-performance-report to update the sheets)")

if __name__ == "__main__":
    main()
