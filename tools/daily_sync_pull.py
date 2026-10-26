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

def _batch_history(h, ids, prop="dealstage"):
    """Yields (deal_id, history_list) for every id, via /batch/read with propertiesWithHistory - 50 deals
    per call (HubSpot's own cap for history batches, confirmed live 2026-10-12: "maximum number of inputs
    supported in a batch request for property histories is 50"), NOT 1 call per deal. This is the fix for
    the unbounded, deal-count-scaling runtime that made the Performance Report pull take 1h41m+ on
    Coding's MAIN portal (4,765+ deals, one GET each) - same 50-per-call cap cuts that to ~96 calls."""
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        r = requests.post("https://api.hubapi.com/crm/v3/objects/deals/batch/read", headers=h, json={
            "inputs": [{"id": did} for did in chunk], "propertiesWithHistory": [prop],
        }, timeout=60).json()
        for res in r.get("results", []):
            yield res["id"], (res.get("propertiesWithHistory") or {}).get(prop, [])

# ---------------------------------------------------------------------------------------------------------------
# 1b. Cascade-credit fix, Cluster-1 ONLY (2026-10-15, explicit user ask after finding Manit/Pratyush skip
#     "Replied" straight to "1st interest sent" ~95% of the time - "if jumps happen all middle stages must
#     be counted", chosen over strict enforcement because forcing every click costs real calling time for
#     something callers route around anyway). Implemented as SYNTHETIC HISTORY INJECTION, not a rewrite of
#     counts_for_ids/rep_day_counts/pull_rep_granular: for a deal that reached a later funnel stage without
#     ever touching an earlier metric's own stage, _cascade_augment_history() adds one synthetic CRM_UI
#     history entry for the skipped metric, timestamped at the deal's earliest real touch within that
#     metric's own-or-later label set (exactly the dry-run comparison written to the sheet on 2026-10-15,
#     Company Ops (Cluster-1) rows 43-62). Every existing counting function then sees this as ordinary
#     history and produces cascade-correct numbers with ZERO changes to its own logic - scoped to Cluster-1
#     only via each call site's own `cascade=` flag; Cluster-2/Coding/RAT/Global never see augmented data.
CASCADE_METRICS_ORDER = ["Leads assigned", "Calls attempted", "Calls picked", "Interested", "VC setup",
                         "VC completed", "Script/1-pager shared", "Script results/1-pager filled", "Contract/LOI signed"]
CASCADE_OWN_LABELS_CLUSTER1 = {
    "Leads assigned": {"cold lead assigned"},
    "Calls attempted": {"no pickup - attempt 1", "no pickup - attempt 2", "no pickup - attempt 3",
                         "dead: cold call / wrong number", "dead: cold call / not interested",
                         "dead: cold call / wrong fit", "replied"},
    "Calls picked": {"replied", "dead: cold call / not interested", "dead: cold call / wrong fit"},
    "Interested": {"1st interest sent"},
    "VC setup": {"discovery call"},
    "VC completed": {"dead: discovery call / rejected by lh2", "dead: discovery call / not interested", "one pager requested"},
    "Script/1-pager shared": {"one pager requested"},
    "Script results/1-pager filled": {"one pager received"},
    "Contract/LOI signed": {"loi signed"},
}
CASCADE_REPRESENTATIVE_CLUSTER1 = {
    "Calls attempted": "Replied", "Calls picked": "Replied", "Interested": "1st interest sent",
    "VC setup": "Discovery call", "VC completed": "One pager requested",
    "Script/1-pager shared": "One pager requested", "Script results/1-pager filled": "One pager received",
    "Contract/LOI signed": "LOI signed",
}
CASCADE_SETS_CLUSTER1 = {}
for _i, _m in enumerate(CASCADE_METRICS_ORDER):
    _s = set()
    for _m2 in CASCADE_METRICS_ORDER[_i:]:
        _s |= CASCADE_OWN_LABELS_CLUSTER1[_m2]
    CASCADE_SETS_CLUSTER1[_m] = _s
CASCADE_ENTRY_RUNG = {"cold call", "cold called assigned", "cold lead", "cold lead assigned"}

# Global's own stage taxonomy (from row_formulas' "coops_global" branch) is structurally the same
# 9-metric model as Cluster-1's, just with Global's own label names substituted in - so it gets its own
# parallel profile rather than reusing Cluster-1's label sets (which wouldn't match anything on Global's
# pipeline). Added 2026-10-15 (explicit user ask) as a SEPARATE summary-only comparison, not wired into
# the live coops_global pull_all()/write_performance_report() path - see cluster1_vs_global_cascade.py.
CASCADE_OWN_LABELS_GLOBAL = {
    "Leads assigned": {"cold lead"},
    "Calls attempted": {"communicated", "no response", "dead: communicated / invalid contact",
                         "dead: communicated / not interested", "dead: communicated / wrong fit",
                         "dead: communicated / no response"},
    "Calls picked": {"interested", "dead: communicated / not interested"},
    "Interested": {"interested"},
    "VC setup": {"vc fixed"},
    "VC completed": {"dead: vc / rejected by lh2", "dead: vc / not interested", "1 pager shared"},
    "Script/1-pager shared": {"1 pager shared"},
    "Script results/1-pager filled": {"1 pager output received"},
    "Contract/LOI signed": {"loi signed"},
}
CASCADE_REPRESENTATIVE_GLOBAL = {
    "Calls attempted": "Communicated", "Calls picked": "Interested", "Interested": "Interested",
    "VC setup": "VC Fixed", "VC completed": "1 Pager Shared",
    "Script/1-pager shared": "1 Pager Shared", "Script results/1-pager filled": "1 Pager Output Received",
    "Contract/LOI signed": "LOI Signed",
}
CASCADE_SETS_GLOBAL = {}
for _i, _m in enumerate(CASCADE_METRICS_ORDER):
    _s = set()
    for _m2 in CASCADE_METRICS_ORDER[_i:]:
        _s |= CASCADE_OWN_LABELS_GLOBAL[_m2]
    CASCADE_SETS_GLOBAL[_m] = _s

CASCADE_PROFILE_CLUSTER1 = {"own": CASCADE_OWN_LABELS_CLUSTER1, "sets": CASCADE_SETS_CLUSTER1,
                            "representative": CASCADE_REPRESENTATIVE_CLUSTER1}
CASCADE_PROFILE_GLOBAL = {"own": CASCADE_OWN_LABELS_GLOBAL, "sets": CASCADE_SETS_GLOBAL,
                          "representative": CASCADE_REPRESENTATIVE_GLOBAL}

# ADDED 2026-10-22 (explicit user ask: "cascading gotta be for all verticals") - EXTENDS the 2026-10-15
# Cluster-1-only cascade-credit fix to every vertical this file writes. Cluster-2 reuses
# CASCADE_PROFILE_CLUSTER1 verbatim (same CompanyOps account, same stage-label vocabulary, just a
# different pipeline id - "default" vs "2464812771" - so a separate profile would just be a byte-for-byte
# duplicate). Coding and Engineering (rat) get their own profiles below, built from each vertical's
# CURRENT row_formulas() branch (the verified source of truth for what counts toward each metric) -
# own_labels are that branch's g(c, ...) argument lists lowercased, and each representative is the
# "forward progress" / success-path label within that metric's own set (the one that also serves as the
# entry point into the NEXT metric down the funnel) - same choice rule CASCADE_REPRESENTATIVE_CLUSTER1
# already uses (e.g. Cluster-1's "Calls attempted"/"Calls picked" both represent as "Replied", the shared
# success label between the two).
CASCADE_OWN_LABELS_CODING = {
    "Leads assigned": {"cold call"},
    "Calls attempted": {"no pickup - attempt 1", "no pickup - attempt 2", "no pickup - attempt 3",
                         "dead/coldcall/nopickup", "dead/coldcall/wrongnumber", "dead/coldcall/not interested",
                         "dead/coldcall/wrongfit", "interested"},
    "Calls picked": {"interested", "dead/coldcall/not interested", "dead/coldcall/wrongfit"},
    "Interested": {"interested"},
    "VC setup": {"gmeet fixed"},
    "VC completed": {"script shared", "dead/gmeet/privacy concerns", "dead/gmeet/wrong fit"},
    "Script/1-pager shared": {"script shared"},
    "Script results/1-pager filled": {"script results received"},
    "Contract/LOI signed": {"loi", "deal contract signed", "closed/won"},
}
CASCADE_REPRESENTATIVE_CODING = {
    "Calls attempted": "Interested", "Calls picked": "Interested", "Interested": "Interested",
    "VC setup": "GMeet Fixed", "VC completed": "Script Shared",
    "Script/1-pager shared": "Script Shared", "Script results/1-pager filled": "Script Results Received",
    "Contract/LOI signed": "LOI",
}
CASCADE_SETS_CODING = {}
for _i, _m in enumerate(CASCADE_METRICS_ORDER):
    _s = set()
    for _m2 in CASCADE_METRICS_ORDER[_i:]:
        _s |= CASCADE_OWN_LABELS_CODING[_m2]
    CASCADE_SETS_CODING[_m] = _s

CASCADE_OWN_LABELS_RAT = {
    "Leads assigned": {"cold called assigned"},
    "Calls attempted": {"no pickup", "callback", "dead: coldcall/wrongnumber", "dead: coldcall/notinterested", "replied"},
    "Calls picked": {"replied", "dead: coldcall/notinterested"},
    "Interested": {"1st interest sent"},
    "VC setup": {"discovery call"},
    "VC completed": {"dead: discoverycall/rejectedbylh2", "dead: discoverycall/notinterested", "sample requested"},
    "Script/1-pager shared": {"sample requested"},
    "Script results/1-pager filled": {"sample received"},
    "Contract/LOI signed": {"contract signed"},
}
CASCADE_REPRESENTATIVE_RAT = {
    "Calls attempted": "Replied", "Calls picked": "Replied", "Interested": "1st Interest Sent",
    "VC setup": "Discovery Call", "VC completed": "Sample Requested",
    "Script/1-pager shared": "Sample Requested", "Script results/1-pager filled": "Sample Received",
    "Contract/LOI signed": "Contract Signed",
}
CASCADE_SETS_RAT = {}
for _i, _m in enumerate(CASCADE_METRICS_ORDER):
    _s = set()
    for _m2 in CASCADE_METRICS_ORDER[_i:]:
        _s |= CASCADE_OWN_LABELS_RAT[_m2]
    CASCADE_SETS_RAT[_m] = _s

CASCADE_PROFILE_CODING = {"own": CASCADE_OWN_LABELS_CODING, "sets": CASCADE_SETS_CODING,
                          "representative": CASCADE_REPRESENTATIVE_CODING}
CASCADE_PROFILE_RAT = {"own": CASCADE_OWN_LABELS_RAT, "sets": CASCADE_SETS_RAT,
                       "representative": CASCADE_REPRESENTATIVE_RAT}

def _cascade_augment_history(history, resolve_fn, rev_labels, profile=None):
    """history + one synthetic entry per metric this deal skipped on its way to a later one. profile
    (added 2026-10-15, defaults to Cluster-1's own - the only profile this function had before today)
    selects which vertical's own-label/cascade-set/representative maps to use; see CASCADE_PROFILE_GLOBAL
    for the second one. resolve_fn(raw_value) -> canonical label or None (same resolver the caller
    already uses). rev_labels: lowercase-label -> a stage id resolve_fn would map back to that label,
    used to synthesize a plausible 'value' so the augmented entry round-trips through the caller's own
    normal resolution unchanged."""
    if profile is None:
        profile = CASCADE_PROFILE_CLUSTER1
    own_labels, cascade_sets, representative = profile["own"], profile["sets"], profile["representative"]
    touches = []
    for hent in history:
        raw = resolve_fn(hent["value"])
        if not raw: continue
        lab = raw.strip().lower()
        any_src = lab in CASCADE_ENTRY_RUNG
        if not any_src and hent.get("sourceType") != "CRM_UI": continue
        try:
            ts_dt = dt.datetime.fromisoformat(hent["timestamp"].replace("Z", "+00:00"))
        except Exception:
            continue
        touches.append((lab, ts_dt, hent["timestamp"]))
    augmented = list(history)
    for m in CASCADE_METRICS_ORDER:
        own = own_labels[m]
        if any(lab in own for lab, _, _ in touches):
            continue  # real own-touch already exists - nothing to inject
        casc = cascade_sets[m]
        matches = sorted((ts_dt, ts_raw) for lab, ts_dt, ts_raw in touches if lab in casc)
        if not matches:
            continue
        rep_label = representative.get(m)
        rep_id = rev_labels.get(rep_label.lower()) if rep_label else None
        if not rep_id:
            continue
        augmented.append({"value": rep_id, "timestamp": matches[0][1], "sourceType": "CRM_UI",
                           "sourceId": "cascade-synthetic"})
    return augmented

def counts_for_ids(h, ids, label_sources, owner_map=None, loi_dedup_labels=None, cascade=False, cascade_profile=None):
    """label_sources: [(labels_map, mode), ...], first match on a stage id wins. mode None = label used as-is;
    'reassign' = translated through REASSIGN. Returns (itd_counts, mtd_counts, wtd_counts), each
    {canonical_label: distinct_deal_count}. All three come from ONE pass over each deal's full stage
    history (no separate pull per window) - ITD has no date floor, MTD/WTD filter the same events down.
    If owner_map is given, ALSO returns a 4th dict {owner_id: (itd_counts, mtd_counts, wtd_counts)} -
    added 2026-10-12 for per-rep breakdown tables, same underlying pull, no extra API calls.
    loi_dedup_labels (added 2026-10-13, see feedback_loi_closedwon_dedup memory): a set of canonical
    labels that should ALSO be unioned into a synthetic "__loi_dedup__" bucket - a deal that visits ANY
    of these labels counts once toward it, no matter how many of them it visits, unlike every other
    label here (each gets its own independent per-label distinct-deal set, so a deal touching 2 summed
    labels on one row legitimately counts twice - the normal, documented convention). "__loi_dedup__"
    rides the exact same set.add(did) dedup machinery as every other label, just fed from multiple
    source labels instead of one, so it costs nothing extra and can't drift from the real per-deal walk."""
    import collections
    itd, wtd, mtd = collections.defaultdict(set), collections.defaultdict(set), collections.defaultdict(set)
    by_owner = collections.defaultdict(lambda: (collections.defaultdict(set), collections.defaultdict(set), collections.defaultdict(set)))
    if cascade:
        _labels0, _mode0 = label_sources[0]
        def _resolve0(raw_value):
            raw = _labels0.get(raw_value)
            return raw if _mode0 is None else (REASSIGN.get(raw.strip().lower()) if raw else None)
        _rev0 = {lab.strip().lower(): sid for sid, lab in _labels0.items()}
    for did, history in _batch_history(h, ids):
        if cascade:
            history = _cascade_augment_history(history, _resolve0, _rev0, profile=cascade_profile)
        owner = owner_map.get(did) if owner_map else None
        for hent in history:
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
            if loi_dedup_labels and canon in loi_dedup_labels:
                itd["__loi_dedup__"].add(did)
                if t >= MTD_START: mtd["__loi_dedup__"].add(did)
                if t >= WTD_START: wtd["__loi_dedup__"].add(did)
            if owner:
                oi, om, ow = by_owner[owner]
                oi[canon].add(did)
                if t >= MTD_START: om[canon].add(did)
                if t >= WTD_START: ow[canon].add(did)
                if loi_dedup_labels and canon in loi_dedup_labels:
                    oi["__loi_dedup__"].add(did)
                    if t >= MTD_START: om["__loi_dedup__"].add(did)
                    if t >= WTD_START: ow["__loi_dedup__"].add(did)
    agg = ({k: len(v) for k, v in itd.items()}, {k: len(v) for k, v in mtd.items()}, {k: len(v) for k, v in wtd.items()})
    if owner_map is None:
        return agg
    owner_agg = {o: ({k: len(v) for k, v in oi.items()}, {k: len(v) for k, v in om.items()}, {k: len(v) for k, v in ow.items()})
                 for o, (oi, om, ow) in by_owner.items()}
    return agg + (owner_agg,)

def counts(h, pid, label_source=None, by_owner=False, loi_dedup_labels=None, cascade=False, cascade_profile=None):
    ids, owner_map = search_all(h, pid)
    return counts_for_ids(h, ids, [(stage_labels(h, pid), label_source)], owner_map=owner_map if by_owner else None, loi_dedup_labels=loi_dedup_labels, cascade=cascade, cascade_profile=cascade_profile)

def counts_moved(h, new_pid, old_pid, by_owner=False, loi_dedup_labels=None, cascade=False, cascade_profile=None):
    new_labels, old_labels = stage_labels(h, new_pid), stage_labels(h, old_pid)
    ids, owner_map = search_all(h, new_pid)
    return counts_for_ids(h, ids, [(new_labels, None), (old_labels, "reassign")], owner_map=owner_map if by_owner else None,
                          loi_dedup_labels=loi_dedup_labels, cascade=cascade, cascade_profile=cascade_profile)

# ---------------------------------------------------------------------------------------------------------------
# 2b. Rep maps - owner_id -> display name, only for the 3 verticals the per-rep breakdown was asked for
#     (2026-10-12). Coding merges 3 portals so its 5 reps' owner ids come from 3 different HubSpot accounts.
# ---------------------------------------------------------------------------------------------------------------
REP_MAPS = {
    "cluster1": {"168609107": "Manit", "169098409": "Pratyush"},
    "cluster2": {"168015618": "Tanisha", "168015679": "Amisha"},
    # UPDATED 2026-10-14 (explicit user ask): Coding's POC-level reps are now Akarsh/Shagufta/Harsha/
    # Vaishnavi - Lamiya and Yuktha dropped from here (their Cold Call pools were redistributed to these
    # 4 today; they're no longer the active rep set this report tracks per-rep).
    # UPDATED 2026-10-21 (explicit user ask): Akarsh ("168572679") dropped from here too - he's no longer
    # on Coding. His block in the already-written week-of-2026-10-19 sheet was cleared by hand the same
    # day (content + formatting, that week's rows only - earlier weeks' Akarsh blocks are untouched, they
    # reflect when he really was on this vertical). NOT re-added anywhere yet - which vertical/map he
    # belongs under next is still pending the user's own say-so, don't guess at it here.
    "coding": {"168572330": "Shagufta", "98906502": "Harsha", "168341981": "Vaishnavi"},
    # ADDED 2026-10-14 (explicit user ask): Engineering Datasets ("rat" vertical)'s own POCs, in the RAT
    # account's own "Rapid Action Team" pipeline (2575252183) - NOT the "Coding" pipeline (2608440047) in
    # that same RAT account, which is Harsha's Coding-overflow queue, a different vertical entirely.
    # Non-granular by explicit user ask - see build_rep_block_rows_rat below.
    # Hemanth (169456665) ADDED 2026-10-19 - was live in this pipeline but missing from this map, so his
    # activity was silently excluded from the Performance Report rep blocks until caught and fixed here.
    "rat": {"168541136": "Prerna", "168829625": "Rohan", "169456665": "Hemanth"},
}

# ---------------------------------------------------------------------------------------------------------------
# 2c'. Rep-level day-breakdown (Phase 1, 2026-10-13: "rep level dashboard... rolls up to daily table that
#      rolls to wtd that rolls to mtd" with "daily comes as collapsible column before wtd"). Cluster-1/2
#      only for now (coding is Phase 2, coops_global/rat have no SOP/rep model for this yet).
#
#      Deliberately a SEPARATE pass (its own _batch_history walk), not threaded through counts_for_ids -
#      two reasons: (1) this needs EARLIEST-in-week-per-(deal,label) timestamps, a different aggregation
#      than counts_for_ids' "any entry since WTD_START" membership test, so it isn't a free byproduct of
#      that walk; (2) keeping it structurally separate means this new, not-yet-proven feature can't ever
#      regress the itd/mtd/wtd numbers that were already independently verified exact (36/36 cross-checks)
#      earlier this week - a bug here is contained to the new day columns, not the whole report.
#
#      Dedup rule (ported from the Apps Script build's own hard-won fix, 2026-10-12): a deal's contribution
#      to one canonical label is attributed to the EARLIEST in-week qualifying timestamp for that
#      (deal, label) pair, not to every timestamp it has - otherwise a deal that re-enters the same label
#      more than once in the week (e.g. bounced back to "No Pickup - Attempt 1" after a callback reschedule)
#      would be counted on more than one day, and day-sums would exceed the correctly-deduped WTD total.
#      Summing a label's counts across all 7 day-slots therefore always exactly equals that label's own
#      WTD distinct-deal count - same guarantee the Apps Script build verified exact, 2026-10-12.
# ---------------------------------------------------------------------------------------------------------------
def rep_day_counts(h, ids, label_sources, owner_map, week_start_date, today_date, cascade=False, cascade_profile=None):
    """Returns {owner_id: {day_iso: {canonical_label: distinct_deal_count}}} for every calendar day (IST)
    from week_start_date to today_date inclusive. Resolution + entry-rung/CRM_UI-source filter is
    intentionally duplicated from counts_for_ids (not shared) - see the block comment above.
    cascade=True (EXTENDED 2026-10-22 to every vertical, was Cluster-1 only - see _cascade_augment_history):
    each deal's history is augmented with synthetic skipped-stage entries before this function's own
    earliest-in-week dedup ever sees it. cascade_profile selects which vertical's own-label/representative
    maps to use (defaults to Cluster-1's inside _cascade_augment_history if not given)."""
    import collections
    if cascade:
        _labels0, _mode0 = label_sources[0]
        def _resolve0(raw_value):
            raw = _labels0.get(raw_value)
            return raw if _mode0 is None else (REASSIGN.get(raw.strip().lower()) if raw else None)
        _rev0 = {lab.strip().lower(): sid for sid, lab in _labels0.items()}
    earliest = collections.defaultdict(dict)  # (owner, canon) -> {deal_id: earliest_ts_in_week}
    for did, history in _batch_history(h, ids):
        if cascade:
            history = _cascade_augment_history(history, _resolve0, _rev0, profile=cascade_profile)
        owner = owner_map.get(did)
        if not owner:
            continue
        for hent in history:
            canon = None
            for labels, mode in label_sources:
                raw = labels.get(hent["value"])
                if raw is None: continue
                canon = raw if mode is None else REASSIGN.get(raw.strip().lower())
                break
            if not canon: continue
            any_src = canon.lower() in ("cold call", "cold called assigned", "cold lead", "cold lead assigned")
            if not any_src and hent.get("sourceType") != "CRM_UI": continue
            t = dt.datetime.fromisoformat(hent["timestamp"].replace("Z", "+00:00"))
            if t < WTD_START: continue
            key = (owner, canon)
            prev = earliest[key].get(did)
            if prev is None or t < prev:
                earliest[key][did] = t
    by_day = collections.defaultdict(lambda: collections.defaultdict(lambda: collections.defaultdict(set)))
    for (owner, canon), dmap in earliest.items():
        for did, t in dmap.items():
            day = t.astimezone(IST).date()
            if day < week_start_date or day > today_date:
                continue  # guards clock-skew/future timestamps; WTD_START already bounds the lower end
            by_day[owner][day.isoformat()][canon].add(did)
    return {owner: {day: {canon: len(idset) for canon, idset in canons.items()} for day, canons in days.items()}
            for owner, days in by_day.items()}

def team_day_counts(rep_day_data):
    """Team-level {day_iso: {canon: count}}, summed across every mapped rep's own day buckets
    (rep_day_counts' output) - added 2026-10-14 so the main table can show the same collapsible
    day-columns-before-WTD the rep blocks already have. Deliberately reuses the already-pulled rep_day
    data (no extra HubSpot calls, no new aggregation pass) rather than a 4th independent walk. Safe to
    sum across owners at the (day, canon) level: a deal belongs to exactly one owner, so this can't
    double-count - it inherits the SAME documented gap as 'sum-of-mapped-reps != team total' elsewhere
    (a deal under an unmapped/former owner is invisible here too, so day-sums can undercount the main
    WTD actual by that same small amount) - not a new gap, the existing one surfacing in a new place."""
    import collections
    out = collections.defaultdict(lambda: collections.defaultdict(int))
    for days in rep_day_data.values():
        for day, canons in days.items():
            for canon, cnt in canons.items():
                out[day][canon] += cnt
    return {day: dict(canons) for day, canons in out.items()}

CASCADE_PROFILE_BY_VERTICAL = {"cluster1": CASCADE_PROFILE_CLUSTER1, "cluster2": CASCADE_PROFILE_CLUSTER1,
                               "coding": CASCADE_PROFILE_CODING, "rat": CASCADE_PROFILE_RAT,
                               "coops_global": CASCADE_PROFILE_GLOBAL}

def pull_rep_day(h, pid, vertical):
    ids, owner_map = search_all(h, pid)
    labels = stage_labels(h, pid)
    # EXTENDED 2026-10-22 (was cluster1 only) - every vertical now gets cascade-credit on its day columns
    # too, using that vertical's own profile (cluster2 reuses cluster1's - same stage-label vocabulary).
    return rep_day_counts(h, ids, [(labels, None)], owner_map, week_start, today, cascade=True,
                          cascade_profile=CASCADE_PROFILE_BY_VERTICAL[vertical])

def pull_rep_day_coding():
    """Coding's rep-day pull spans 3 portals (MAIN + 2 old-pipeline-overflow portals that use REASSIGN
    translation, same shape as counts_moved() above) and must be MERGED by owner+day+label across all
    three, not just concatenated - a rep's deals can live in any of the 3 portals under portal-local
    owner ids that never collide across portals, so summing per (owner, day, label) key is safe."""
    import collections
    portals = [(MAIN, "default", None), (CO, "2608440044", "2464812771"), (RAT, "2608440047", "2575252183")]
    merged = collections.defaultdict(lambda: collections.defaultdict(lambda: collections.defaultdict(int)))
    for h, pid, old_pid in portals:
        ids, owner_map = search_all(h, pid)
        labels = stage_labels(h, pid)
        label_sources = [(labels, None)] if not old_pid else [(labels, None), (stage_labels(h, old_pid), "reassign")]
        # EXTENDED 2026-10-22 (was no cascade at all for coding) - cascade_profile applies against this
        # portal's OWN native labels (label_sources[0], always mode=None) since _cascade_augment_history
        # only ever looks at the first label_source; the REASSIGN-translated old-pipeline entries (the
        # second source, old portals only) are not cascade-augmented - same pre-existing simplification
        # cascade already accepted everywhere else (it only ever resolves off label_sources[0]).
        portal_result = rep_day_counts(h, ids, label_sources, owner_map, week_start, today, cascade=True,
                                       cascade_profile=CASCADE_PROFILE_CODING)
        for owner, days in portal_result.items():
            for day, canons in days.items():
                for canon, cnt in canons.items():
                    merged[owner][day][canon] += cnt
    return {owner: {day: dict(canons) for day, canons in days.items()} for owner, days in merged.items()}

# Raw stage-label groups per METRICS row, cluster1/cluster2 ONLY - MUST mirror row_formulas()'s
# cluster1/cluster2 g(c, ...) argument lists exactly (they're identical for both clusters there). Kept as
# a deliberate, explicitly-flagged duplication rather than refactoring the already-verified row_formulas()
# mid-build (lower risk right now); if those g(...) lists ever change, update this dict too.
REP_METRIC_LABELS_C1C2 = {
    "Leads assigned": ["Cold lead assigned"],
    # FIXED 2026-10-22: "Dead: Cold Call / Wrong Fit" was missing from both rows below despite this
    # dict's own comment claiming it mirrors row_formulas()'s cluster1/cluster2 branch exactly - that
    # branch added Wrong Fit to both Calls attempted and Calls picked on 2026-10-14 (see its own fix
    # comment), but this day-column-only dict was never updated to match. Found live after Tanisha
    # reported 80 leads worked today but the sheet's Thu day-column showing only 61 for Calls attempted -
    # all 22 of that day's real "Dead: Cold Call / Wrong Fit" outcomes were silently excluded from every
    # day column (not just today's) while still correctly counted in the WTD/MTD Actual totals, which use
    # row_formulas() directly, not this dict.
    "Calls attempted": ["No Pickup - Attempt 1", "No Pickup - Attempt 2", "No Pickup - Attempt 3",
                         "Dead: Cold Call / Wrong Number", "Dead: Cold Call / Not Interested",
                         "Dead: Cold Call / Wrong Fit", "Replied"],
    "Calls picked": ["Replied", "Dead: Cold Call / Not Interested", "Dead: Cold Call / Wrong Fit"],
    "Interested": ["1st interest sent"],
    "VC setup": ["Discovery call"],
    "VC completed": ["Dead: Discovery Call / Rejected by LH2", "Dead: Discovery Call / Not Interested", "One pager requested"],
    "Script/1-pager shared": ["One pager requested"],
    "Script results/1-pager filled": ["One pager received"],
    "Contract/LOI signed": ["LOI signed"],
}
REP_METRIC_LABELS_CODING = {
    "Leads assigned": ["Cold Call"],
    # FIXED 2026-10-14: Dead/ColdCall/WrongFit added, mirroring row_formulas()'s coding block exactly (see
    # that fix's comment - must stay in sync with it per this dict's own documented convention).
    "Calls attempted": ["No Pickup - Attempt 1", "No Pickup - Attempt 2", "No Pickup - Attempt 3",
                         "Dead/ColdCall/NoPickup", "Dead/ColdCall/WrongNumber", "Dead/ColdCall/Not Interested",
                         "Dead/ColdCall/WrongFit", "Interested"],
    "Calls picked": ["Interested", "Dead/ColdCall/Not Interested", "Dead/ColdCall/WrongFit"],
    "Interested": ["Interested"],
    "VC setup": ["GMeet Fixed"],
    "VC completed": ["Script Shared", "Dead/GMeet/Privacy Concerns", "Dead/GMeet/wrong fit"],
    "Script/1-pager shared": ["Script Shared"],
    "Script results/1-pager filled": ["Script Results Received"],
    # Day-column caveat (documented, not silently approximated): this is the plain stage-visit sum, same
    # as every other row here - NOT the union-dedup "__loi_dedup__" the WTD/MTD/ITD actual columns use
    # (see feedback_loi_closedwon_dedup). A deal that passed through both LOI and Deal Contract Signed in
    # the same week would double-count across its two days here, so day-sums for THIS ONE row won't
    # always foot exactly to the (correctly deduped) WTD actual shown beside it - every other row's day-sum
    # does foot exactly, verified. Flagged rather than building a day-level dedup pass this round.
    "Contract/LOI signed": ["LOI", "Deal Contract Signed", "Closed/Won"],
}
REP_METRIC_LABELS_RAT = {
    "Leads assigned": ["Cold Called Assigned"],
    "Calls attempted": ["No Pickup", "Callback", "Dead: ColdCall/WrongNumber", "Dead: ColdCall/NotInterested", "Replied"],
    "Calls picked": ["Replied", "Dead: ColdCall/NotInterested"],
    "Interested": ["1st Interest Sent"],
    "VC setup": ["Discovery Call"],
    "VC completed": ["Dead: DiscoveryCall/RejectedByLH2", "Dead: DiscoveryCall/NotInterested", "Sample Requested"],
    "Script/1-pager shared": ["Sample Requested"],
    "Script results/1-pager filled": ["Sample Received"],
    "Contract/LOI signed": ["Contract Signed"],
}
REP_METRIC_LABELS = {"cluster1": REP_METRIC_LABELS_C1C2, "cluster2": REP_METRIC_LABELS_C1C2, "coding": REP_METRIC_LABELS_CODING,
                     "rat": REP_METRIC_LABELS_RAT}

def col_letter(idx0):
    """0-based column index -> A1 column letter(s): 0->'A', 25->'Z', 26->'AA', ..."""
    idx0 += 1
    s = ""
    while idx0 > 0:
        idx0, r = divmod(idx0 - 1, 26)
        s = chr(65 + r) + s
    return s

# ---------------------------------------------------------------------------------------------------------------
# 2c. "Contract/LOI signed" dedup source labels (feedback_loi_closedwon_dedup, stated 2026-10-13): a deal
#     that visits ANY of these counts ONCE toward "__loi_dedup__", not once per label visited. Cluster-1/2/
#     coops_global/rat each only have ONE real label feeding this row today, so for them "__loi_dedup__"
#     is numerically identical to the old sum (dedup of 1 thing is a no-op) - verified, not just assumed,
#     the first time this ran. Coding is the one vertical where this actually changes the number (LOI +
#     Deal Contract Signed + Closed/Won are 3 distinct labels a deal can pass through on its way to close).
# ---------------------------------------------------------------------------------------------------------------
LOI_DEDUP_LABELS = {
    "coding": {"LOI", "Deal Contract Signed", "Closed/Won"},
    "coops_global": {"LOI Signed"},
    "cluster1": {"LOI signed"},
    "cluster2": {"LOI signed"},
    "rat": {"Contract Signed"},
}

# ---------------------------------------------------------------------------------------------------------------
# 3. Pull every vertical. BY_OWNER verticals also get a per-rep breakdown (same pull, no extra API calls).
# ---------------------------------------------------------------------------------------------------------------
BY_OWNER_VERTICALS = {"cluster1", "cluster2", "coding"}
# DAY_COLUMN_VERTICALS (added 2026-10-16, explicit user ask: "engineering data sets can we also get the
# daily level columns") - which verticals get the 7 fixed day-columns in the TEAM table and rep blocks.
# Deliberately a SEPARATE set from BY_OWNER_VERTICALS: rat has a rep+day-history model (pull_rep_day
# works fine against its pipeline, same as cluster1/2) so it belongs here, but it still has no granular
# SOP/Task-coverage model (see the "coops_global/rat have no granular pull" comments throughout this
# file) so it must stay OUT of BY_OWNER_VERTICALS, which also gates the rep_granular pull loop below -
# adding rat there would wrongly attempt a granular pull this vertical has no schema for.
DAY_COLUMN_VERTICALS = BY_OWNER_VERTICALS | {"rat"}

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
    # EXTENDED 2026-10-22 (explicit user ask: "cascading gotta be for all verticals") - cascade=True on
    # all 3 of Coding's portals, using CASCADE_PROFILE_CODING (built from this vertical's own
    # row_formulas() branch - see that profile's definition above for the own-label/representative
    # choices). Was previously Cluster-1 only.
    r1 = counts(MAIN, "default", by_owner=True, loi_dedup_labels=LOI_DEDUP_LABELS["coding"], cascade=True, cascade_profile=CASCADE_PROFILE_CODING)
    r2 = counts_moved(CO, "2608440044", "2464812771", by_owner=True, loi_dedup_labels=LOI_DEDUP_LABELS["coding"], cascade=True, cascade_profile=CASCADE_PROFILE_CODING)
    r3 = counts_moved(RAT, "2608440047", "2575252183", by_owner=True, loi_dedup_labels=LOI_DEDUP_LABELS["coding"], cascade=True, cascade_profile=CASCADE_PROFILE_CODING)
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
    # EXTENDED 2026-10-22 (explicit user ask: "cascading gotta be for all verticals") - cascade=True with
    # CASCADE_PROFILE_GLOBAL (built 2026-10-15, previously only used by the offline cluster1_vs_global_cascade.py
    # comparison tool, never wired into the live path until now).
    itd, mtd, wtd = counts(MAIN, "2425754306", loi_dedup_labels=LOI_DEDUP_LABELS["coops_global"], cascade=True, cascade_profile=CASCADE_PROFILE_GLOBAL); result["coops_global"] = {"itd": itd, "mtd": mtd, "wtd": wtd}
    _checkpoint(result)
    # cascade=True (2026-10-15, Cluster-1 first; EXTENDED to every vertical 2026-10-22, explicit user ask
    # "cascading gotta be for all verticals") - see _cascade_augment_history's block comment above
    # counts_for_ids(). Cluster-2 reuses Cluster-1's own profile (same stage-label vocabulary, same
    # CompanyOps account, just a different pipeline id).
    itd, mtd, wtd, owners = counts(CO, "default", by_owner=True, loi_dedup_labels=LOI_DEDUP_LABELS["cluster1"], cascade=True); result["cluster1"] = {"itd": itd, "mtd": mtd, "wtd": wtd, "by_owner": owners}
    _checkpoint(result)
    itd, mtd, wtd, owners = counts(CO, "2464812771", by_owner=True, loi_dedup_labels=LOI_DEDUP_LABELS["cluster2"], cascade=True); result["cluster2"] = {"itd": itd, "mtd": mtd, "wtd": wtd, "by_owner": owners}
    _checkpoint(result)
    # CHANGED 2026-10-14 (explicit user ask): by_owner=True added so Prerna/Rohan get a per-rep block
    # - team itd/mtd/wtd numbers themselves are unaffected, same counts() call. cascade=True + its own
    # CASCADE_PROFILE_RAT added 2026-10-22, same "all verticals" ask as above.
    itd, mtd, wtd, owners = counts(RAT, "2575252183", by_owner=True, loi_dedup_labels=LOI_DEDUP_LABELS["rat"], cascade=True, cascade_profile=CASCADE_PROFILE_RAT); result["rat"] = {"itd": itd, "mtd": mtd, "wtd": wtd, "by_owner": owners}
    _checkpoint(result)
    # Rep-level day-breakdown pull (cluster1/cluster2: Phase 1, 2026-10-13; coding: Phase 2, same day;
    # rat: added 2026-10-16, explicit user ask "engineering data sets can we also get the daily level
    # columns" - rat's pipeline supports the same pull_rep_day() walk as cluster1/2, it just never had
    # this pass wired in before). Separate pass (its own search_all + stage_labels + batch_history) rather
    # than threaded through counts() above - deliberately NOT sharing state with the already-verified
    # itd/mtd/wtd pipeline, so this feature can't regress numbers that were already checked correct. See
    # rep_day_counts() docstring.
    result["cluster1"]["rep_day"] = pull_rep_day(CO, "default", "cluster1")
    _checkpoint(result)
    result["cluster2"]["rep_day"] = pull_rep_day(CO, "2464812771", "cluster2")
    _checkpoint(result)
    result["coding"]["rep_day"] = pull_rep_day_coding()
    _checkpoint(result)
    result["rat"]["rep_day"] = pull_rep_day(RAT, "2575252183", "rat")
    _checkpoint(result)
    # Rep + day-level GRANULAR breakdown (2026-10-14, explicit user ask) - same separate-pass posture as
    # rep_day above, for the same reason (can't regress the already-verified core numbers).
    for v in BY_OWNER_VERTICALS:
        rep_g, team_g = pull_rep_granular(v)
        result[v]["rep_granular"] = rep_g
        result[v]["team_granular"] = team_g
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
            # FIXED 2026-10-14 (same policy as cluster1/2, explicit user ask: "every single lead gets
            # dialed" - Dead/ColdCall/WrongFit added to both rows; it's always reached post-call now,
            # same reasoning as the cluster1/2 fix above.
            "Calls attempted": g(c, "No Pickup - Attempt 1", "No Pickup - Attempt 2", "No Pickup - Attempt 3",
                                  "Dead/ColdCall/NoPickup", "Dead/ColdCall/WrongNumber", "Dead/ColdCall/Not Interested",
                                  "Dead/ColdCall/WrongFit", "Interested"),
            "Calls picked": g(c, "Interested", "Dead/ColdCall/Not Interested", "Dead/ColdCall/WrongFit"),
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
            # FIXED 2026-10-13 (feedback_loi_closedwon_dedup): this WAS a plain stage-visit sum, so a deal
            # that passed through LOI then Deal Contract Signed (the normal path) counted TWICE, inflating
            # the metric. Now reads the union-dedup'd "__loi_dedup__" bucket counts_for_ids() builds
            # (counted once per deal regardless of how many of the 3 labels it visited); falls back to the
            # old sum only if "__loi_dedup__" is somehow missing (shouldn't happen - pull_all() always
            # passes loi_dedup_labels now).
            "Contract/LOI signed": c.get("__loi_dedup__", g(c, "LOI", "Deal Contract Signed", "Closed/Won")),
        }
    if vertical == "coops_global":
        return lambda c: {
            "Leads assigned": g(c, "Cold Lead"),
            "Calls attempted": g(c, "Communicated", "No Response", "Dead: Communicated / Invalid Contact", "Dead: Communicated / Not Interested", "Dead: Communicated / Wrong Fit", "Dead: Communicated / No Response"),
            "Calls picked": g(c, "Interested", "Dead: Communicated / Not Interested"),
            "Interested": g(c, "Interested"), "VC setup": g(c, "VC Fixed"),
            "VC completed": g(c, "1 Pager Shared", "Dead: VC / Rejected by LH2", "Dead: VC / Not Interested"),
            "Script/1-pager shared": g(c, "1 Pager Shared"), "Script results/1-pager filled": g(c, "1 Pager Output Received"),
            "Contract/LOI signed": c.get("__loi_dedup__", g(c, "LOI Signed")),
        }
    if vertical in ("cluster1", "cluster2"):
        return lambda c: {
            # FIXED 2026-10-12: these used to say "Cold called assigned" / "No pickup" (the stage names
            # from before today's Cluster 1/2 migration). The rename to "Cold lead assigned" and the
            # split of "No pickup" into the "No Pickup - Attempt 1/2/3" chain left this file silently
            # undercounting - Leads assigned read a flat 0 for both verticals, found live doing the ITD
            # work below. Calls attempted now sums all 3 attempt stages, since reaching ANY of them means
            # at least one real dial happened - that's the same thing "No pickup" used to mean alone.
            # FIXED 2026-10-14 (explicit user policy change, Cluster-2 SOP): "Dead: Cold Call / Wrong Fit"
            # used to be excluded from both Calls attempted and Calls picked - it used to cover TWO SOP
            # paths (screened out before ever dialing, OR picked up and found wrong-fit while talking),
            # and since a stage alone can't tell those apart, it was left out of both rather than guess.
            # The user has now retired the pre-dial screening path entirely: every Wrong Fit outcome is
            # SOP-mandated to require an actual call first (with a note on what was learned) - so the
            # ambiguity is gone and it now belongs in both the dial-happened and pickup-happened buckets,
            # same as "Not Interested" already was.
            "Leads assigned": g(c, "Cold lead assigned"),
            "Calls attempted": g(c, "No Pickup - Attempt 1", "No Pickup - Attempt 2", "No Pickup - Attempt 3",
                                  "Dead: Cold Call / Wrong Number", "Dead: Cold Call / Not Interested",
                                  "Dead: Cold Call / Wrong Fit", "Replied"),
            "Calls picked": g(c, "Replied", "Dead: Cold Call / Not Interested", "Dead: Cold Call / Wrong Fit"),
            "Interested": g(c, "1st interest sent"), "VC setup": g(c, "Discovery call"),
            "VC completed": g(c, "Dead: Discovery Call / Rejected by LH2", "Dead: Discovery Call / Not Interested", "One pager requested"),
            "Script/1-pager shared": g(c, "One pager requested"), "Script results/1-pager filled": g(c, "One pager received"),
            "Contract/LOI signed": c.get("__loi_dedup__", g(c, "LOI signed")),
        }
    if vertical == "rat":
        return lambda c: {
            "Leads assigned": g(c, "Cold Called Assigned"),
            "Calls attempted": g(c, "No Pickup", "Callback", "Dead: ColdCall/WrongNumber", "Dead: ColdCall/NotInterested", "Replied"),
            "Calls picked": g(c, "Replied", "Dead: ColdCall/NotInterested"),
            "Interested": g(c, "1st Interest Sent"), "VC setup": g(c, "Discovery Call"),
            "VC completed": g(c, "Dead: DiscoveryCall/RejectedByLH2", "Dead: DiscoveryCall/NotInterested", "Sample Requested"),
            "Script/1-pager shared": g(c, "Sample Requested"), "Script results/1-pager filled": g(c, "Sample Received"),
            "Contract/LOI signed": c.get("__loi_dedup__", g(c, "Contract Signed")),
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

def build_full_rows(v, pulled, granular_stats):
    """All 18 rows for one vertical, in the EXACT order of the master 'Weekly Targets' tab (Subvertical
    Lead sheet) - matched 2026-10-12 per explicit user ask ('use same order of metrics', reference
    screenshot of that tab). Previously this was the 9 core rows then a separate contiguous granular
    block (grouped/collapsible); that grouping is dropped here since the two sets are now interleaved to
    match the reference order and can no longer be one contiguous range. Two derived rows that existed
    before ('Picked to assigned %', 'VC completed to interested %') are dropped - they aren't part of the
    Weekly Targets tab's 18 rows, so keeping them would break the 1:1 match the user asked for.
    ADDED 2026-10-14: cluster1/cluster2/coding (the 3 verticals with a rep model, BY_OWNER_VERTICALS) get
    7 fixed day-columns inserted before WTD here too, matching the rep blocks' own layout - summed across
    reps via team_day_counts(). coops_global/rat have no rep model to derive a day breakdown from, so they
    keep the original day-less 10-column shape unchanged."""
    actual = row_formulas(v)
    ia_all, wa_all, ma_all = actual(pulled["itd"]), actual(pulled["wtd"]), actual(pulled["mtd"])
    g = granular_stats.get(v)

    has_days = v in DAY_COLUMN_VERTICALS and bool(pulled.get("rep_day"))
    team_day = team_day_counts(pulled["rep_day"]) if has_days else {}
    day_dates = [week_start + dt.timedelta(days=i) for i in range(7)]

    def day_vals(labels):
        if not has_days:
            return []
        vals = []
        for d in day_dates:
            if d > today:
                vals.append("")
            else:
                dm = team_day.get(d.isoformat(), {})
                vals.append(sum(dm.get(l, 0) for l in labels))
        return vals

    team_granular = pulled.get("team_granular") or {}

    def fmt_granular(v2):
        if v2 == "no_noshow_stage": return "n/a (no no-show stage defined)"
        if v2 is None: return "0 in bucket"
        return f"{v2}%"

    def granular_day_vals(metric_key):
        """Real per-day team values for the 5 metrics that are genuinely day-windowable (see
        pull_rep_granular's docstring) - '—' for days without enough data, '' for days not yet elapsed.
        None if this vertical has no granular pull at all (coops_global/rat, or has_days False)."""
        if not has_days or not team_granular:
            return None
        vals = []
        for d in day_dates:
            if d > today:
                vals.append("")
            else:
                w = team_granular.get(d.isoformat())
                vals.append(fmt_granular(w[metric_key]) if w else "0 in bucket")
        return vals

    def flat_row(label, value, granular_key=None):
        # FIXED 2026-10-14: WTD Actual/MTD Actual used to repeat the same all-time 'value' as ITD for
        # every flat row - harmless placeholder before, but actively WRONG once real day-windowed team
        # data exists (team_granular), since the all-time rate and the WTD/MTD rate for the same metric
        # can differ a lot (caught live: Same day Lead coverage showed WTD Actual 14.3% when the real WTD
        # figure was 49.2% - the all-time/ITD number mislabeled as WTD). For the 5 windowed metrics, WTD
        # Actual/MTD Actual now come from team_granular's own 'wtd'/'mtd' entries; ITD keeps the original
        # all-time 'value' (that one genuinely IS all-time, unaffected by this bug).
        if granular_key is not None:
            dv = granular_day_vals(granular_key)
            if dv is None:
                dv = ["—"] * 7 if has_days else []
            wtd_w = team_granular.get("wtd")
            mtd_w = team_granular.get("mtd")
            wtd_val = fmt_granular(wtd_w[granular_key]) if wtd_w else value
            mtd_val = fmt_granular(mtd_w[granular_key]) if mtd_w else value
        else:
            dv = ["—"] * 7 if has_days else []
            wtd_val = mtd_val = value
        return [label] + dv + [wtd_val, "—", "—", "", mtd_val, "—", "—", "", value]

    def core_row(label, m):
        """label is the display text (matched to the Weekly Targets tab's own wording); m is the
        row_formulas()/METRICS key the actual numbers are computed under - they differ for 3 rows
        whose wording the Weekly Targets tab phrases differently than this file's internal metric name."""
        wt, mt = target(v, m)
        wa, ma, ia = wa_all[m], ma_all[m], ia_all[m]
        labels = REP_METRIC_LABELS[v][m] if has_days else None  # only index REP_METRIC_LABELS when it
        # actually has an entry for v - coops_global/rat aren't in it (KeyError otherwise, has_days is
        # already False for them so day_vals(None) returns [] unused anyway)
        return ([label] + day_vals(labels) +
                [wa, wt if wt is not None else "—", pct(wa, wt) if wt else "—", "",
                 ma, mt if mt is not None else "—", pct(ma, mt) if mt else "—", "", ia])

    # CHANGED 2026-10-12 per explicit user ask: coops_global/rat never got the granular HubSpot schema
    # changes (no Task-coverage wiring, no stage-bucket definitions) that coding/cluster1/cluster2 have -
    # those two verticals now render truly BLANK ("") for every granular cell instead of a literal "n/a"
    # string, since there's nothing partially-computed to report, just an intentionally unsupported
    # vertical for this round of work.
    n_callers = len(REP_MAPS[v]) if v in REP_MAPS else ""
    avg_attempts = g["avg_attempts"] if g else ""
    pct_touch = f"{g['pct_touch']}%" if g else ""
    pct_nonpicked_touch = f"{g['pct_nonpicked_touch']}%" if g else ""

    def fu_val(key):
        # None = bucket had 0 deals this pull (real "no data yet", not a build gap); "no_noshow_stage" =
        # vertical has no distinct no-show dead-reason stage wired up (real data-model gap, see
        # BUCKET_STAGES comment) - both get their own honest label instead of a bare "n/a".
        if not g: return ""
        v2 = g[key]
        if v2 == "no_noshow_stage": return "n/a (no no-show stage defined)"
        if v2 is None: return "0 in bucket"
        return f"{v2}%"

    same_day_lead = (f"{g['pct_same_day_lead']}%" if g["pct_same_day_lead"] is not None else "0 in bucket") if g else ""
    # FIXED 2026-10-12: ported from the Apps Script build - VC-setup-day vs script-shared-day, the real
    # (non-degenerate) version of this metric. See BUCKET_STAGES's vc_setup/script_shared comment.
    same_day_script = (f"{g['pct_same_day_script']}%" if g.get("pct_same_day_script") is not None else "0 in bucket") if g else ""

    return [
        flat_row("Callers", n_callers),
        core_row("Leads assigned", "Leads assigned"),
        # ADDED 2026-10-14: real day-wise team values for the 5 genuinely day-windowable granular rows,
        # via granular_key -> pull_rep_granular()'s team_granular (see that function's docstring for which
        # metrics are windowable and why avg_attempts/touch coverage deliberately aren't).
        flat_row("Same day Lead coverage (% of same day leads where call is attempted)", same_day_lead, "pct_same_day_lead"),
        flat_row("Call attempts per lead", avg_attempts),
        core_row("Calls attempted", "Calls attempted"),
        flat_row("Email/ Whatsapp coverage% on non-picked call leads", pct_nonpicked_touch, "pct_nonpicked_touch"),
        flat_row("Email/ whatsapp coverage% on all leads", pct_touch, "pct_touch"),
        core_row("Calls picked", "Calls picked"),
        flat_row("Follow-up coverage on follow-up marked call-picked leads", fu_val("pct_followup_picked"), "pct_followup_picked"),
        core_row("Interested", "Interested"),
        flat_row("Follow-up coverage on follow-up marked interested leads", fu_val("pct_followup_interested"), "pct_followup_interested"),
        core_row("VC setup", "VC setup"),
        flat_row("Follow-up coverage for no-shows on VC", fu_val("pct_followup_noshow"), "pct_followup_noshow"),
        core_row("VC completed", "VC completed"),
        flat_row("Same day script shared coverage on VC completed", same_day_script, "pct_same_day_script"),
        core_row("Script shared / 1 pager shared / Requirement doc shared", "Script/1-pager shared"),
        core_row("Script results/ filled one-pager/ volume & sample data", "Script results/1-pager filled"),
        core_row("Contract / LOI signed", "Contract/LOI signed"),
    ]

# The old single-row-per-rep MTD-only summary table (REP_TABLE_COL="L", WRITE_REP_ROWS flag) that lived
# here was retired 2026-10-13 (Phase 1) - replaced by the full per-rep day->WTD->MTD->ITD blocks below
# (build_rep_block_rows), not kept alongside as dead/disabled code.

GRANULAR_ROW_SPECS = [
    # (label, granular_key) in build_full_rows' exact interleaved order - the 7 rows that are day/WTD/MTD
    # -windowable per-rep via pull_rep_granular(). ADDED 2026-10-14: the 2 Email/WhatsApp coverage rows
    # joined this list (previously all-time-only, see pull_rep_granular's touch_h/n comment for why
    # windowing them by assignment-date is correct here, unlike avg_attempts). Only "Callers" and "Call
    # attempts per lead" remain without a per-rep windowed source and stay "—" throughout in rep blocks.
    ("Same day Lead coverage (% of same day leads where call is attempted)", "pct_same_day_lead"),
    ("Email/ Whatsapp coverage% on non-picked call leads", "pct_nonpicked_touch"),
    ("Email/ whatsapp coverage% on all leads", "pct_touch"),
    ("Follow-up coverage on follow-up marked call-picked leads", "pct_followup_picked"),
    ("Follow-up coverage on follow-up marked interested leads", "pct_followup_interested"),
    ("Follow-up coverage for no-shows on VC", "pct_followup_noshow"),
    ("Same day script shared coverage on VC completed", "pct_same_day_script"),
]

def build_rep_block_rows(v, rep_oid, rep_name, pulled, rep_day_data, rep_granular_data, n_reps):
    """One rep's full block: banner + header + all 18 rows in the SAME interleaved core+granular order as
    build_full_rows (Weekly-Targets order), with 7 FIXED day-columns (Mon..Sun, blank for days not yet
    elapsed) before WTD. Phase 1 (2026-10-13) built only the 9 core METRICS rows, since granular stats
    weren't owner-attributed yet. ADDED 2026-10-14 (explicit user ask, 'granular... each rep wise too'):
    the 5 genuinely day/WTD/MTD-windowable granular rows (see GRANULAR_ROW_SPECS/pull_rep_granular) now
    get real per-rep values here too, keeping row order identical to build_full_rows so a rep block's row
    N is always the same metric as the main table's row N - this is also what keeps the row-alignment with
    main_rows' own 18-row count correct (n_rows, used for row-grouping, is still len(full_rows)).
    The other 4 flat rows (Callers, Call attempts per lead, 2x Email/WhatsApp coverage) have no per-rep
    data source and stay a plain '—' flat_row, same honesty policy as everywhere else in this file.
    Target columns: the team's own WTD/MTD target (target()) split evenly across n_reps, rounded up -
    the two clusters currently have exactly 2 reps each sharing one team target (matches the known
    '100 leads/day/rep = 200/day team' convention), not a per-rep target HubSpot tracks separately."""
    day_dates = [week_start + dt.timedelta(days=i) for i in range(7)]
    day_headers = [f"{d.strftime('%a')} {d.month}/{d.day}" for d in day_dates]
    banner = f"{rep_name} — week starting Mon {week_start.isoformat()}, refreshed {today.isoformat()}."
    header = (["Metric"] + day_headers +
              ["WTD Actual", "WTD Target", "% Target Achievement", "",
               "MTD Actual", "MTD Target", "% Target Achievement", "", "ITD Actual"])
    rows = [[banner], header]

    actual = row_formulas(v)
    owner_counts = pulled.get("by_owner", {}).get(rep_oid)
    o_itd, o_mtd, o_wtd = owner_counts if owner_counts else ({}, {}, {})
    ia_all, ma_all, wa_all = actual(o_itd), actual(o_mtd), actual(o_wtd)
    rep_day = rep_day_data.get(rep_oid, {})
    rep_g = rep_granular_data.get(rep_oid, {})

    def core_day_vals(labels):
        vals = []
        for d in day_dates:
            if d > today:
                vals.append("")  # day hasn't happened yet this week - honest blank, not a 0
            else:
                day_map = rep_day.get(d.isoformat(), {})
                vals.append(sum(day_map.get(l, 0) for l in labels))
        return vals

    def core_row(m):
        labels = REP_METRIC_LABELS[v][m]
        team_wt, team_mt = target(v, m)
        rep_wt = math.ceil(team_wt / n_reps) if team_wt is not None else None
        rep_mt = math.ceil(team_mt / n_reps) if team_mt is not None else None
        wa, ma, ia = wa_all[m], ma_all[m], ia_all[m]
        return ([m] + core_day_vals(labels) +
                [wa, rep_wt if rep_wt is not None else "—", pct(wa, rep_wt) if rep_wt else "—", "",
                 ma, rep_mt if rep_mt is not None else "—", pct(ma, rep_mt) if rep_mt else "—", "", ia])

    def fmt_g(v2):
        if v2 == "no_noshow_stage": return "n/a (no no-show stage defined)"
        if v2 is None: return "0 in bucket"
        return f"{v2}%"

    def granular_row(label, key):
        vals = []
        for d in day_dates:
            if d > today:
                vals.append("")
            else:
                w = rep_g.get(d.isoformat())
                vals.append(fmt_g(w[key]) if w else "0 in bucket")
        wtd_v = fmt_g(rep_g["wtd"][key]) if "wtd" in rep_g else "—"
        mtd_v = fmt_g(rep_g["mtd"][key]) if "mtd" in rep_g else "—"
        return [label] + vals + [wtd_v, "—", "—", "", mtd_v, "—", "—", "", "—"]

    def blank_flat_row(label):
        return [label] + ["—"] * 7 + ["—", "—", "—", "", "—", "—", "—", "", "—"]

    granular_by_key = {key: (label, key) for label, key in GRANULAR_ROW_SPECS}
    rows += [
        blank_flat_row("Callers"),
        core_row("Leads assigned"),
        granular_row(*granular_by_key["pct_same_day_lead"]),
        blank_flat_row("Call attempts per lead"),
        core_row("Calls attempted"),
        granular_row(*granular_by_key["pct_nonpicked_touch"]),
        granular_row(*granular_by_key["pct_touch"]),
        core_row("Calls picked"),
        granular_row(*granular_by_key["pct_followup_picked"]),
        core_row("Interested"),
        granular_row(*granular_by_key["pct_followup_interested"]),
        core_row("VC setup"),
        granular_row(*granular_by_key["pct_followup_noshow"]),
        core_row("VC completed"),
        granular_row(*granular_by_key["pct_same_day_script"]),
        core_row("Script/1-pager shared"),
        core_row("Script results/1-pager filled"),
        core_row("Contract/LOI signed"),
    ]
    return rows

def _retired_build_rep_block_rows_rat(v, rep_oid, rep_name, pulled, n_reps):
    """RETIRED 2026-10-16: superseded by build_rep_block_rows() once rat got REP_METRIC_LABELS_RAT +
    rep_day data (see the "engineering data sets... daily level columns" change). Left unreferenced
    rather than deleted in case the day-column rep block ever needs to be compared back against this
    original non-granular/day-less shape from 2026-10-14."""
    banner = f"{rep_name} — refreshed {today.isoformat()}."
    header = ["Metric", "WTD Actual", "WTD Target", "% Target Achievement", "",
              "MTD Actual", "MTD Target", "% Target Achievement", "", "ITD Actual"]
    rows = [[banner], header]

    actual = row_formulas(v)
    owner_counts = pulled.get("by_owner", {}).get(rep_oid)
    o_itd, o_mtd, o_wtd = owner_counts if owner_counts else ({}, {}, {})
    ia_all, ma_all, wa_all = actual(o_itd), actual(o_mtd), actual(o_wtd)

    def core_row(label, m):
        team_wt, team_mt = target(v, m)
        rep_wt = math.ceil(team_wt / n_reps) if team_wt is not None else None
        rep_mt = math.ceil(team_mt / n_reps) if team_mt is not None else None
        wa, ma, ia = wa_all[m], ma_all[m], ia_all[m]
        return [label, wa, rep_wt if rep_wt is not None else "—", pct(wa, rep_wt) if rep_wt else "—", "",
                ma, rep_mt if rep_mt is not None else "—", pct(ma, rep_mt) if rep_mt else "—", "", ia]

    def blank_row(label):
        return [label] + ["—"] * 9

    rows += [
        blank_row("Callers"),
        core_row("Leads assigned", "Leads assigned"),
        blank_row("Same day Lead coverage (% of same day leads where call is attempted)"),
        blank_row("Call attempts per lead"),
        core_row("Calls attempted", "Calls attempted"),
        blank_row("Email/ Whatsapp coverage% on non-picked call leads"),
        blank_row("Email/ whatsapp coverage% on all leads"),
        core_row("Calls picked", "Calls picked"),
        blank_row("Follow-up coverage on follow-up marked call-picked leads"),
        core_row("Interested", "Interested"),
        blank_row("Follow-up coverage on follow-up marked interested leads"),
        core_row("VC setup", "VC setup"),
        blank_row("Follow-up coverage for no-shows on VC"),
        core_row("VC completed", "VC completed"),
        blank_row("Same day script shared coverage on VC completed"),
        core_row("Script shared / 1 pager shared / Requirement doc shared", "Script/1-pager shared"),
        core_row("Script results/ filled one-pager/ volume & sample data", "Script results/1-pager filled"),
        core_row("Contract / LOI signed", "Contract/LOI signed"),
    ]
    return rows

def build_week_block(v, pulled, granular_stats_by_vertical, rep_names):
    """Full block for one vertical, one week. Returns (main_rows, rep_blocks, n_rows, full_rows):
    main_rows go in columns A-J (banner, header, the 18 rows in Weekly-Targets-tab order - see
    build_full_rows). rep_blocks is a list of (rep_name, rows) for verticals in REP_METRIC_LABELS
    (cluster1/cluster2: Phase 1; coding: Phase 2, same day) - each rep's own full block
    (build_rep_block_rows), placed beside the main table at its own column offset (computed in
    write_performance_report), row-aligned to start at the SAME row as main_rows. Scales to any number of
    reps (2 for cluster1/2, 5 for coding) - the column-offset loop in write_performance_report is generic.
    CHANGED 2026-10-12: core (9) and granular (9) used to be two separate contiguous chunks, the
    granular one row-grouped/collapsible. Reordered to interleave and match the master 'Weekly Targets'
    tab's own row order exactly (explicit user ask, reference screenshot of that tab). The collapsible
    groups now follow THAT tab's own grouping too (confirmed from the same screenshot) - one small group
    per core row that has derived row(s) right after it (Leads assigned, Calls attempted, Calls picked,
    Interested, VC setup, VC completed), built in write_performance_report() from GROUP_SPECS below, not
    one big block.
    CHANGED 2026-10-13 (Phase 1): the old single thin per-rep summary table (one row per rep, MTD-only,
    REP_TABLE_COL) is replaced by full per-rep blocks with day->WTD->MTD->ITD rollups - see
    build_rep_block_rows. The old WRITE_REP_ROWS/REP_TABLE_COL feature is retired, not kept alongside."""
    # TRIMMED 2026-10-12 per user: banner keeps only the week/refresh stamp, no WTD/MTD/ITD glossary text.
    banner = f"{PERF_REPORT_TABS[v]} — week starting Mon {week_start.isoformat()}, refreshed {today.isoformat()}."
    # ADDED 2026-10-14: team-level day columns before WTD, same shape as the rep blocks' own day headers,
    # for the 3 verticals that have a rep model to sum a day breakdown from (BY_OWNER_VERTICALS).
    if v in DAY_COLUMN_VERTICALS:
        day_dates = [week_start + dt.timedelta(days=i) for i in range(7)]
        day_headers = [f"{d.strftime('%a')} {d.month}/{d.day}" for d in day_dates]
    else:
        day_headers = []
    header = (["Metric"] + day_headers +
              ["WTD Actual", "WTD Target", "% Target Achievement", "", "MTD Actual", "MTD Target", "% Target Achievement", "", "ITD Actual (new tracking)"])
    main_rows = [[banner], header]
    full_rows = build_full_rows(v, pulled, granular_stats_by_vertical)
    main_rows += full_rows
    n_rows = len(full_rows)
    main_rows.append([])  # spacer before the next week's block

    rep_blocks = []
    # CHANGED 2026-10-16 (explicit user ask: "engineering data sets can we also get the daily level
    # columns"): "rat" used to need its own branch here (build_rep_block_rows_rat, a day-less/granular-less
    # simplified block) since it wasn't in REP_METRIC_LABELS/BY_OWNER_VERTICALS. Now that REP_METRIC_LABELS
    # has a "rat" entry (REP_METRIC_LABELS_RAT) and rep_day is pulled for it too, it goes through the same
    # build_rep_block_rows() as cluster1/cluster2/coding - rep_granular_data is just an empty dict for rat
    # (no granular SOP model for it, unchanged), which build_rep_block_rows already renders as honest
    # "0 in bucket"/"—" cells, same as it always has for any rep with no granular data yet.
    if v in REP_METRIC_LABELS and v in rep_names and pulled.get("by_owner"):
        rep_day_data = pulled.get("rep_day", {})
        rep_granular_data = pulled.get("rep_granular", {})
        n_reps = len(rep_names[v])
        for oid, name in rep_names[v].items():
            rep_blocks.append((name, build_rep_block_rows(v, oid, name, pulled, rep_day_data, rep_granular_data, n_reps)))
    return main_rows, rep_blocks, n_rows, full_rows

# Collapsible-group spec, copied from the master "Weekly Targets" tab's own grouping (confirmed via
# screenshot 2026-10-12): each core row that has derived row(s) directly under it gets ONE group covering
# just those derived rows (the core row itself stays outside the group, so it's always visible even
# collapsed). Script shared / Script results / Contract-LOI have no derived rows after them, so no group.
GROUP_SPECS = [
    ("Leads assigned", 2),   # Same day Lead coverage, Call attempts per lead
    ("Calls attempted", 2),  # Email/WhatsApp non-picked, Email/WhatsApp all leads
    ("Calls picked", 1),     # Follow-up coverage - call-picked
    ("Interested", 1),       # Follow-up coverage - interested
    ("VC setup", 1),         # Follow-up coverage - VC no-shows
    ("VC completed", 1),     # Same day script shared coverage
]

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

NAVY = {"red": 0.1176, "green": 0.2, "blue": 0.3569}
TEAL = {"red": 0.098, "green": 0.549, "blue": 0.498}
MINT = {"red": 0.929, "green": 0.969, "blue": 0.957}
WHITE = {"red": 1, "green": 1, "blue": 1}
# Same scheme as the Daily Sync tab and the (now-paused) Apps Script build - column widths sized for
# Python's 10-column layout (no day-breakdown here, unlike the paused JS version).
COL_WIDTHS = [420, 95, 85, 80, 20, 95, 85, 80, 20, 135]
# ADDED 2026-10-14 (rat added 2026-10-16): label + 7 day-slot columns (55px, same width as the rep
# blocks' own day columns) + the same WTD/MTD/ITD widths as COL_WIDTHS - for the DAY_COLUMN_VERTICALS
# tabs whose main table now carries a team-level day breakdown too (see build_full_rows/build_week_block).
COL_WIDTHS_WITH_DAYS = [420, 55, 55, 55, 55, 55, 55, 55, 95, 85, 80, 20, 95, 85, 80, 20, 135]

def main_col_widths(v):
    return COL_WIDTHS_WITH_DAYS if v in DAY_COLUMN_VERTICALS else COL_WIDTHS

def _region_format_requests(sheet_numeric_id, start_row, n_rows, start_col0, col_widths, label_wrap=True):
    """Column widths + navy banner / teal header / mint-banded body rows for ONE rectangular region
    (banner row, header row, n_rows body rows) starting at start_col0. Factored out 2026-10-13 so the
    main A-J block and the new per-rep blocks (placed at other column offsets) share one formatting
    implementation instead of two copies that could drift - the main block's call below is byte-for-byte
    the same requests it always sent, just produced by this shared function now."""
    banner_row0 = start_row - 1
    header_row0 = banner_row0 + 1
    body_start0 = header_row0 + 1
    n_cols = len(col_widths)
    end_col0 = start_col0 + n_cols

    requests = []
    for i, w in enumerate(col_widths):
        requests.append({"updateDimensionProperties": {"range": {"sheetId": sheet_numeric_id, "dimension": "COLUMNS",
            "startIndex": start_col0 + i, "endIndex": start_col0 + i + 1}, "properties": {"pixelSize": w}, "fields": "pixelSize"}})
    if label_wrap:
        requests.append({"repeatCell": {"range": {"sheetId": sheet_numeric_id, "startColumnIndex": start_col0, "endColumnIndex": start_col0 + 1},
            "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP", "verticalAlignment": "MIDDLE"}},
            "fields": "userEnteredFormat.wrapStrategy,userEnteredFormat.verticalAlignment"}})

    # FIXED 2026-10-14: the label_wrap request above applies WRAP to the whole of column A (needed for
    # long metric labels like "Email/ Whatsapp coverage% on non-picked call leads") - but column A is
    # also where the banner text lives, and WRAP there broke the banner's natural single-line overflow
    # into the blank navy cells beside it, wrapping it to 2 lines inside the fixed 32px-tall banner row
    # and visually overlapping the header row underneath. OVERFLOW_CELL here restores the single-line
    # look (the banner's own backgroundColor/textFormat fields don't touch wrapStrategy, so without this
    # explicit override the column-wide WRAP silently wins).
    requests.append({"repeatCell": {"range": {"sheetId": sheet_numeric_id, "startRowIndex": banner_row0, "endRowIndex": banner_row0 + 1,
        "startColumnIndex": start_col0, "endColumnIndex": end_col0},
        "cell": {"userEnteredFormat": {"backgroundColor": NAVY, "textFormat": {"foregroundColor": WHITE, "bold": True, "fontSize": 11},
                                        "wrapStrategy": "OVERFLOW_CELL"}},
        "fields": "userEnteredFormat.backgroundColor,userEnteredFormat.textFormat,userEnteredFormat.wrapStrategy"}})
    requests.append({"updateDimensionProperties": {"range": {"sheetId": sheet_numeric_id, "dimension": "ROWS",
        "startIndex": banner_row0, "endIndex": banner_row0 + 1}, "properties": {"pixelSize": 32}, "fields": "pixelSize"}})

    requests.append({"repeatCell": {"range": {"sheetId": sheet_numeric_id, "startRowIndex": header_row0, "endRowIndex": header_row0 + 1,
        "startColumnIndex": start_col0, "endColumnIndex": end_col0},
        "cell": {"userEnteredFormat": {"backgroundColor": TEAL, "textFormat": {"foregroundColor": WHITE, "bold": True}, "horizontalAlignment": "CENTER"}},
        "fields": "userEnteredFormat.backgroundColor,userEnteredFormat.textFormat,userEnteredFormat.horizontalAlignment"}})
    requests.append({"repeatCell": {"range": {"sheetId": sheet_numeric_id, "startRowIndex": header_row0, "endRowIndex": header_row0 + 1,
        "startColumnIndex": start_col0, "endColumnIndex": start_col0 + 1},
        "cell": {"userEnteredFormat": {"horizontalAlignment": "LEFT"}}, "fields": "userEnteredFormat.horizontalAlignment"}})

    for j in range(n_rows):
        body_row0 = body_start0 + j
        bg = MINT if j % 2 == 0 else WHITE
        requests.append({"repeatCell": {"range": {"sheetId": sheet_numeric_id, "startRowIndex": body_row0, "endRowIndex": body_row0 + 1,
            "startColumnIndex": start_col0, "endColumnIndex": end_col0},
            "cell": {"userEnteredFormat": {"backgroundColor": bg}}, "fields": "userEnteredFormat.backgroundColor"}})
    return requests

def apply_block_formatting(s, sheet_id, sheet_numeric_id, start_row, n_rows, col_widths=COL_WIDTHS):
    """ADDED 2026-10-12 per explicit user ask ('absolute uniformity across 5 tabs') - column widths,
    frozen label column, navy banner / teal header / mint-banded body rows, applied identically to every
    vertical's block, every tab, every run. Python had NO formatting at all before this (lost somewhere
    along the way) - this is the same visual language as the Apps Script build, just ported back here
    since that automation is paused and this is the tool actually writing the sheet now.
    col_widths added 2026-10-14: defaults to the day-less 10-col shape, pass COL_WIDTHS_WITH_DAYS (via
    main_col_widths(v)) for the 3 tabs whose main table now carries a day breakdown too."""
    requests = []
    # FIXED 2026-10-12: the freeze-column request below failed with a real 400 on Coding/Company Ops
    # (Global)/Engineering Datasets - "can't freeze columns which contain only part of a merged cell" -
    # from a leftover merged cell in column A on those 3 tabs (pre-existing, from before today's work;
    # Cluster-1/2 didn't have this since their formatting's been reset repeatedly today already).
    # unmergeCells on a range with no merges is a safe no-op, so apply it defensively every time rather
    # than only on tabs known to have the problem.
    requests.append({"unmergeCells": {"range": {"sheetId": sheet_numeric_id, "startRowIndex": 0, "endRowIndex": 2000,
        "startColumnIndex": 0, "endColumnIndex": len(col_widths)}}})
    requests.append({"updateSheetProperties": {"properties": {"sheetId": sheet_numeric_id,
        "gridProperties": {"frozenColumnCount": 1}}, "fields": "gridProperties.frozenColumnCount"}})
    requests += _region_format_requests(sheet_numeric_id, start_row, n_rows, 0, col_widths)
    s.spreadsheets().batchUpdate(spreadsheetId=sheet_id, body={"requests": requests}).execute()

# Rep-block column widths (Phase 1, 2026-10-13): label + 7 fixed day-slots (Mon..Sun, always present so
# the block's width never shifts mid-week - see rep_day_counts' block comment for why a growing/shrinking
# day-column count was the exact class of bug that plagued the paused Apps Script build) + WTD
# act/target/%/blank + MTD act/target/%/blank + ITD actual. 17 columns total.
REP_COL_WIDTHS = [230, 55, 55, 55, 55, 55, 55, 55, 70, 65, 60, 15, 70, 65, 60, 15, 85]
REP_BLOCK_GAP = 1  # blank columns between the main table / rep blocks / rep blocks

def apply_rep_block_formatting(s, sheet_id, sheet_numeric_id, start_row, n_rows, start_col0):
    """Same visual language as apply_block_formatting, offset to start_col0 - no unmerge/freeze here,
    those are sheet-wide and the main block's call already handles them for this tab."""
    requests = _region_format_requests(sheet_numeric_id, start_row, n_rows, start_col0, REP_COL_WIDTHS)
    s.spreadsheets().batchUpdate(spreadsheetId=sheet_id, body={"requests": requests}).execute()

def write_performance_report(pulled_all):
    sys.path.insert(0, str(ROOT.parent / "hubspot" / "crm_mirror" / "enrich"))
    import gsheets
    s = gsheets.svc("sheets")
    sheet_id = env["PERFORMANCE_REPORT_SHEET_ID"]
    meta = s.spreadsheets().get(spreadsheetId=sheet_id, fields="sheets.properties").execute()
    sheet_ids = {sh["properties"]["title"]: sh["properties"]["sheetId"] for sh in meta["sheets"]}
    sheet_col_counts = {sh["properties"]["title"]: sh["properties"]["gridProperties"].get("columnCount", 26)
                         for sh in meta["sheets"]}

    # FIXED 2026-10-13 (Phase 2): a tab's grid only has as many columns as it was ever given (Coding's
    # was still at the default 28) - 5 rep blocks need up to ~101. Expand any tab under 160 cols before
    # writing, so this doesn't silently 400 the moment a vertical gets more rep blocks than the sheet
    # happened to already have room for.
    grow_requests = [{"updateSheetProperties": {"properties": {"sheetId": sheet_ids[tab],
        "gridProperties": {"columnCount": 160}}, "fields": "gridProperties.columnCount"}}
        for tab in PERF_REPORT_TABS.values() if tab in sheet_ids and sheet_col_counts.get(tab, 0) < 160]
    if grow_requests:
        s.spreadsheets().batchUpdate(spreadsheetId=sheet_id, body={"requests": grow_requests}).execute()

    granular_stats_by_vertical = _load_granular_stats()

    for v, tab in PERF_REPORT_TABS.items():
        pulled = pulled_all[v]
        start_row, old_len = find_or_append_week_row(s, sheet_id, tab)
        main_rows, rep_blocks, n_rows, full_rows = build_week_block(v, pulled, granular_stats_by_vertical, REP_MAPS)

        # CHANGED 2026-10-12: always clear a WIDE range unconditionally now, not just "if block shrank,
        # clear up to column U". The parallel Apps Script build (now paused) wrote its own wider per-rep
        # side-tables out to column AU for cluster1/cluster2's "this week" block - a conditional, narrow
        # clear left those columns stale and visible next to Python's own content. Clear generously
        # (A:BZ) every time before writing, same lesson learned fixing this exact class of bug in the
        # Apps Script version earlier today.
        clear_rows = max(old_len or 0, len(main_rows)) + 2
        # FIXED 2026-10-12: values().clear() only clears CONTENT, never FORMATTING - stale number formats
        # (percent/date) left over from an earlier write at this exact cell position were bleeding
        # through and displaying plain integers as garbled percentages (e.g. "VC setup" showing
        # "800.00%" instead of 8) - same root cause as the exact bug fixed in the Apps Script build
        # earlier today, just never ported back to this clear() call. A batchUpdate updateCells clear
        # (fields: userEnteredValue + userEnteredFormat) actually wipes both.
        # WIDENED 2026-10-13 (Phase 2): 78 columns was enough for cluster1/2's 2 rep blocks (up to ~col
        # 45) but coding has 5 rep blocks - 10 (main) + 1 (gap) + 5*(17+1) = 101 columns needed. 160 is a
        # generous margin for any vertical this pattern might extend to next (Engineering/RAT, Phase 4).
        if tab in sheet_ids:
            s.spreadsheets().batchUpdate(spreadsheetId=sheet_id, body={"requests": [{"updateCells": {
                "range": {"sheetId": sheet_ids[tab], "startRowIndex": start_row - 1, "endRowIndex": start_row - 1 + clear_rows,
                          "startColumnIndex": 0, "endColumnIndex": 160},
                "fields": "userEnteredValue,userEnteredFormat"}}]}).execute()
        else:
            s.spreadsheets().values().clear(spreadsheetId=sheet_id, range=f"{tab}!A{start_row}:BZ{start_row + clear_rows}").execute()
        s.spreadsheets().values().update(spreadsheetId=sheet_id, range=f"{tab}!A{start_row}",
            valueInputOption="USER_ENTERED", body={"values": main_rows}).execute()
        # Rep blocks sit beside the main table, each at its own fixed column offset: main table (10 or,
        # for the 3 BY_OWNER_VERTICALS tabs since 2026-10-14, 17 cols) + 1-col gap, then each rep's 17-col
        # block + 1-col gap - computed the same way every write so a rep's columns never shift
        # (REP_BLOCK_GAP/REP_COL_WIDTHS, see their definitions above).
        rep_block_starts = []  # [(rep_name, rows, start_col0), ...] - reused below for grouping/formatting
        col0 = len(main_col_widths(v)) + REP_BLOCK_GAP
        for rep_name, rows in rep_blocks:
            rep_block_starts.append((rep_name, rows, col0))
            s.spreadsheets().values().update(spreadsheetId=sheet_id, range=f"{tab}!{col_letter(col0)}{start_row}",
                valueInputOption="USER_ENTERED", body={"values": rows}).execute()
            col0 += len(REP_COL_WIDTHS) + REP_BLOCK_GAP

        if tab in sheet_ids:
            group_requests = []
            # Drop any row-group left over from the old core/granular-block layout (2026-10-12 reorder) -
            # rows are interleaved now and can't be one contiguous collapsible range, and a stale group
            # from the previous layout would cover the wrong rows once this write lands.
            try:
                sheet_detail = s.spreadsheets().get(spreadsheetId=sheet_id, ranges=[tab],
                    fields="sheets.rowGroups,sheets.columnGroups").execute()
                sd = (sheet_detail.get("sheets") or [{}])[0]
                for old_group in sd.get("rowGroups", []):
                    group_requests.append({"deleteDimensionGroup": {"range": {
                        "sheetId": sheet_ids[tab], "dimension": "ROWS",
                        "startIndex": old_group["range"]["startIndex"], "endIndex": old_group["range"]["endIndex"]}}})
                # Also drop any COLUMN groups (day-column collapse, rep-table groups) left over from the
                # paused Apps Script build - Python's own layout never used column groups, so any that
                # exist now are stale and would show as orphaned collapse arrows with nothing to collapse.
                for old_cgroup in sd.get("columnGroups", []):
                    group_requests.append({"deleteDimensionGroup": {"range": {
                        "sheetId": sheet_ids[tab], "dimension": "COLUMNS",
                        "startIndex": old_cgroup["range"]["startIndex"], "endIndex": old_cgroup["range"]["endIndex"]}}})
            except Exception as e:
                print(f"    (old row/column-group lookup skipped for {tab}: {e})")
            # New per-core-row groups, matching the master "Weekly Targets" tab's own grouping (GROUP_SPECS
            # above). full_rows[i] 0-based sheet row = start_row + 1 + i (start_row is 1-based banner row;
            # +1 for the header row lands on full_rows[0]).
            labels = [r[0] for r in full_rows]
            for core_label, n_children in GROUP_SPECS:
                try:
                    idx = labels.index(core_label)
                except ValueError:
                    continue  # shouldn't happen - build_full_rows always emits these labels
                g_start = start_row + 1 + idx + 1  # first child row, right after the core row
                g_end = g_start + n_children
                group_requests.append({"addDimensionGroup": {"range": {"sheetId": sheet_ids[tab], "dimension": "ROWS",
                                                                          "startIndex": g_start, "endIndex": g_end}}})
            # Each rep block's 7 day-columns collapse as their own COLUMN group (not a row group - these
            # sit beside the main table on the SAME rows, so a row group would wrongly hide the main
            # table's rows too; same reasoning the old single-table rep feature already established).
            # Column index within a rep block: 0=label, 1..7=the 7 day slots, 8+=WTD/MTD/ITD.
            for rep_name, rows, rep_col0 in rep_block_starts:
                day_start = rep_col0 + 1
                day_end = day_start + 7
                group_requests.append({"addDimensionGroup": {"range": {"sheetId": sheet_ids[tab], "dimension": "COLUMNS",
                                                                          "startIndex": day_start, "endIndex": day_end}}})
            # ADDED 2026-10-14: the main table's own 7 day-columns (BY_OWNER_VERTICALS only) collapse the
            # same way, at column index 1..7 (0=label, same layout as a rep block's own day columns).
            if v in DAY_COLUMN_VERTICALS:
                group_requests.append({"addDimensionGroup": {"range": {"sheetId": sheet_ids[tab], "dimension": "COLUMNS",
                                                                          "startIndex": 1, "endIndex": 8}}})
            if group_requests:
                try:
                    s.spreadsheets().batchUpdate(spreadsheetId=sheet_id, body={"requests": group_requests}).execute()
                except Exception as e:
                    print(f"    (grouping skipped for {tab}: {e})")
            try:
                apply_block_formatting(s, sheet_id, sheet_ids[tab], start_row, n_rows, main_col_widths(v))
                for rep_name, rows, rep_col0 in rep_block_starts:
                    apply_rep_block_formatting(s, sheet_id, sheet_ids[tab], start_row, n_rows, rep_col0)
            except Exception as e:
                print(f"    (formatting skipped for {tab}: {e})")
        print(f"  wrote {tab} (row {start_row}{', updated in place' if old_len else ', new week block appended'}"
              f"{f', {len(rep_blocks)} rep block(s)' if rep_blocks else ''})")

#   - "picked"/"interested"/"vc_noshow" are RAW dealstage LABELS (not canonical row_formulas() names) on
#     that vertical's PRIMARY portal/pipeline only - copied straight from the same g(c, ...) argument
#     lists in row_formulas() for that vertical, so they stay in sync with what "Calls picked"/
#     "Interested"/VC-completed already count. vc_noshow is None where no distinct no-show dead-reason
#     stage exists for that vertical yet (coding/coops_global/rat currently have none wired into
#     row_formulas() either) - those rows report an honest "n/a (no no-show stage defined)" rather than
#     a silent "n/a", since that's a real data-model gap, not a missing-access one.
#   - Coding's 2 secondary portals (old CompanyOps/RAT overflow pipelines) are EXCLUDED from this specific
#     per-deal bucket matching - their raw stage labels need REASSIGN translation first (same as the main
#     pull), which this fast single-pass search doesn't do. avg_attempts/pct_touch (below) still sum
#     across all 3 portals as before; only the NEW picked/interested/no-show coverage % rows are MAIN-only
#     for coding, noted in the printed output.
# "leads_assigned"/"calls_attempted" added 2026-10-12 for the same-day-lead-coverage build - same raw
# label sets as the matching g(c, ...) calls in row_formulas() for "Leads assigned"/"Calls attempted".
BUCKET_STAGES = {
    # vc_setup/script_shared ADDED 2026-10-12 (ported from the Apps Script build where this was already
    # fixed and verified live - real numbers like 1.40%/26.70%) - the real "same day script-shared"
    # metric compares VC-SETUP day (Discovery call entry) against SCRIPT-SHARED day (One pager requested
    # entry), two genuinely different stages. NOT VC-completed vs script-shared, which on the success
    # path IS the same stage transition as script-shared itself (trivially ~100%, degenerate).
    "cluster1":     {"picked": {"Replied", "Dead: Cold Call / Not Interested"}, "interested": {"1st interest sent"}, "vc_noshow": {"Dead: Discovery Call / No Show"},
                      "leads_assigned": {"Cold lead assigned"}, "calls_attempted": {"No Pickup - Attempt 1", "No Pickup - Attempt 2", "No Pickup - Attempt 3", "Dead: Cold Call / Wrong Number", "Dead: Cold Call / Not Interested", "Replied"},
                      "vc_setup": {"Discovery call"}, "script_shared": {"One pager requested"}},
    "cluster2":     {"picked": {"Replied", "Dead: Cold Call / Not Interested"}, "interested": {"1st interest sent"}, "vc_noshow": {"Dead: Discovery Call / No Show"},
                      "leads_assigned": {"Cold lead assigned"}, "calls_attempted": {"No Pickup - Attempt 1", "No Pickup - Attempt 2", "No Pickup - Attempt 3", "Dead: Cold Call / Wrong Number", "Dead: Cold Call / Not Interested", "Replied"},
                      "vc_setup": {"Discovery call"}, "script_shared": {"One pager requested"}},
    # FIXED 2026-10-12: vc_noshow was wrongly set to None for coding, inferred from row_formulas()'s VC
    # completed g(...) call not listing a no-show reason - that was a bad inference, never checked live.
    # Coding's real pipeline (confirmed live, all 3 accounts) DOES have a distinct no-show stage,
    # `Dead/GMeet/NoShow` - same stage the August funnel-flow-revised SOP added for exactly this. (Coding
    # also has `Dead/Interested/NoShow`, a no-show at the earlier 1st-interest stage - NOT used here,
    # since this row is specifically about VC/GMeet no-shows.)
    # FIXED 2026-10-14: Dead/ColdCall/WrongFit added to picked/calls_attempted, mirroring row_formulas().
    "coding":       {"picked": {"Interested", "Dead/ColdCall/Not Interested", "Dead/ColdCall/WrongFit"}, "interested": {"Interested"}, "vc_noshow": {"Dead/GMeet/NoShow"},
                      "leads_assigned": {"Cold Call"}, "calls_attempted": {"No Pickup - Attempt 1", "No Pickup - Attempt 2", "No Pickup - Attempt 3", "Dead/ColdCall/NoPickup", "Dead/ColdCall/WrongNumber", "Dead/ColdCall/Not Interested", "Dead/ColdCall/WrongFit", "Interested"},
                      "vc_setup": {"GMeet Fixed"}, "script_shared": {"Script Shared"}},
    "coops_global": {"picked": {"Interested", "Dead: Communicated / Not Interested"}, "interested": {"Interested"}, "vc_noshow": None,
                      "leads_assigned": {"Cold Lead"}, "calls_attempted": {"Communicated", "No Response", "Dead: Communicated / Invalid Contact", "Dead: Communicated / Not Interested", "Dead: Communicated / Wrong Fit", "Dead: Communicated / No Response"}},
    "rat":          {"picked": {"Replied", "Dead: ColdCall/NotInterested"}, "interested": {"1st Interest Sent"}, "vc_noshow": None,
                      "leads_assigned": {"Cold Called Assigned"}, "calls_attempted": {"No Pickup", "Callback", "Dead: ColdCall/WrongNumber", "Dead: ColdCall/NotInterested", "Replied"}},
}

def _load_granular_stats():
    """Real numbers for every granular row, including 'Same day script-shared coverage on VC completed'
    (FIXED 2026-10-12, ported from the Apps Script build where this was already solved: the OLD
    definition compared 'VC completed' against 'script shared', which on the success path for every
    vertical here IS the same stage transition (entering One-Pager-Requested/Script-Shared is what makes
    VC completed true on that path) - trivially ~100%, degenerate, not a real signal. The real metric
    compares VC-SETUP day (Discovery call / GMeet Fixed entry) against SCRIPT-SHARED day, two genuinely
    separate stages - see BUCKET_STAGES's vc_setup/script_shared keys.
    FIXED 2026-10-12: 'Email/WhatsApp - non-picked leads' and the 3 'Follow-up
    coverage' rows used to be hardcoded 'n/a' placeholders left over from when this block was first built.
    REBUILT same day, 2nd pass: the first real version used the `followup_count` deal property as a
    proxy for 'follow-up happened' - wrong source. The actual SOP (Company Ops SOP sheet, 'Hubspot-Flow'
    tab, 'No pickup' row) says a follow-up is a real HubSpot **Task** ("Create a callback task due +1 day,
    Task type: Call"), and this file's own pending-backlog note from 11 Oct already flagged this exact
    gap ("blocked on whether reps log real HubSpot Tasks - unverified") - should have been checked before
    the first rebuild, wasn't. Verified live 2026-10-12: all 3 portals DO have real Task data (MAIN 453,
    RAT 86, CompanyOps 274), associated to deals via the standard deals<->tasks association. 'Follow-up
    coverage' is now: % of deals in the bucket (picked / interested / VC-no-show) that have >=1 associated
    Task, via a reverse deals->tasks association batch-read on just that bucket's deal ids."""
    # SCOPED 2026-10-12 per explicit user ask: only cluster1/cluster2/coding - these are the only
    # verticals with the Task-logging SOP and stage model this function's numbers actually depend on.
    # coops_global/rat were only ever getting the 9 core rows anyway (no granular block), unchanged.
    PORTALS = {
        "cluster1": [(env["HUBSPOT_KEY_COMPANYOPS"], "default", None)],
        "cluster2": [(env["HUBSPOT_KEY_COMPANYOPS"], "2464812771", None)],
        # Coding spans 3 real HubSpot accounts - ALL THREE now computed (not just MAIN), per explicit
        # user ask ("coding on three accounts"). The 3rd element is the OLD pipeline id a deal's history
        # may carry raw labels from before it moved into Coding (same pattern as counts_moved() above) -
        # needed only for the same-day-lead-coverage history walk; current-state bucket matching doesn't
        # need it since `dealstage` already reflects the NEW Coding pipeline either way.
        "coding": [(env["HUBSPOT_KEY_MAIN"], "default", None),
                   (env["HUBSPOT_KEY_COMPANYOPS"], "2608440044", "2464812771"),
                   (env["HUBSPOT_KEY_RAT"], "2608440047", "2575252183")],
    }
    out = {}
    for v, portals in PORTALS.items():
        buckets = BUCKET_STAGES[v]
        total_n, weighted_att = 0, 0.0
        # Summed as (hits, total) pairs ACROSS all of this vertical's portals - not merged id lists, since
        # deal/task ids are only unique WITHIN one HubSpot account and each portal needs its own auth
        # header + its own Task pool for the association/email-type lookups below.
        picked_h = picked_n = interested_h = interested_n = noshow_h = noshow_n = 0
        nonpicked_h = nonpicked_n = all_h = all_n = 0
        same_day_hit = same_day_total = 0
        same_day_script_hit = same_day_script_total = 0
        any_noshow_stage = False

        for key, pipeline, old_pipeline in portals:
            h = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
            id_to_label = stage_labels(h, pipeline)
            body = {"filterGroups": [{"filters": [{"propertyName": "pipeline", "operator": "EQ", "value": pipeline}]}],
                    "properties": ["call_attempts_count", "dealstage", "followup_channel_touch"], "limit": 100}
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

            portal_ids = [d["id"] for d in deals]
            touch_by_deal = {d["id"]: (d["properties"] or {}).get("followup_channel_touch") for d in deals}
            portal_picked, portal_interested, portal_noshow, portal_nonpicked = [], [], [], []
            for d in deals:
                label = id_to_label.get(d["properties"].get("dealstage"))
                if label in buckets["picked"]:
                    portal_picked.append(d["id"])
                else:
                    portal_nonpicked.append(d["id"])
                if label in buckets["interested"]:
                    portal_interested.append(d["id"])
                if buckets["vc_noshow"] and label in buckets["vc_noshow"]:
                    any_noshow_stage = True
                    portal_noshow.append(d["id"])

            # Real Task coverage for THIS portal: reverse deals->tasks association batch-read over every
            # deal in it, storing the associated task-id SET per deal (answers both "has any follow-up
            # task" and "has an EMAIL-type task specifically" from one pull).
            task_ids_by_deal = {}
            for i in range(0, len(portal_ids), 100):
                chunk = portal_ids[i:i + 100]
                r = requests.post("https://api.hubapi.com/crm/v4/associations/deals/tasks/batch/read",
                    headers=h, json={"inputs": [{"id": did} for did in chunk]}, timeout=30).json()
                for res in r.get("results", []):
                    task_ids_by_deal[res["from"]["id"]] = {t["toObjectId"] for t in res.get("to", [])}
            def hits(ids): return sum(1 for i in ids if task_ids_by_deal.get(i))
            picked_h += hits(portal_picked); picked_n += len(portal_picked)
            interested_h += hits(portal_interested); interested_n += len(portal_interested)
            noshow_h += hits(portal_noshow); noshow_n += len(portal_noshow)

            # Email/WhatsApp coverage for THIS portal: the deal's own followup_channel_touch checkbox
            # ('Email sent'/'WhatsApp sent') IS the metric, per explicit user instruction 2026-10-14 -
            # no fallback to an EMAIL-type Task as a proxy, even though that property currently sits at
            # literal 0 adoption on cluster1/cluster2 (26 deals on coding MAIN) - that zero IS the real,
            # intended signal (reps not ticking the SOP-mandated box), not something to paper over with a
            # looser proxy. (The earlier Task-based fallback this replaces was itself a deliberate,
            # documented choice from earlier in the same session, made for the same reason - this is a
            # policy reversal on which signal counts as authoritative, not a bug fix.)
            def touch_hits(ids): return sum(1 for i in ids if (touch_by_deal.get(i) or "").strip())
            all_h += touch_hits(portal_ids); all_n += len(portal_ids)
            nonpicked_h += touch_hits(portal_nonpicked); nonpicked_n += len(portal_nonpicked)

            # Same-day lead coverage for THIS portal: per-deal dealstage history (same _batch_history()
            # infra the main WTD/MTD/ITD pull uses), comparing the EARLIEST entry into a leads_assigned
            # stage against the EARLIEST entry into any calls_attempted stage, same calendar day in IST.
            # For Coding's 2 non-MAIN portals, a deal's history may carry raw labels from its OLD pipeline
            # (before it moved into Coding) - same REASSIGN translation counts_moved() already uses for
            # the main WTD/MTD/ITD pull, applied here too so those deals' pre-move history isn't silently
            # unmatched.
            if old_pipeline:
                old_labels = stage_labels(h, old_pipeline)
                def resolve(raw_value):
                    if raw_value in id_to_label: return id_to_label[raw_value]
                    raw_label = old_labels.get(raw_value)
                    return REASSIGN.get(raw_label.strip().lower()) if raw_label else None
            else:
                def resolve(raw_value): return id_to_label.get(raw_value)
            vc_setup_labels = buckets.get("vc_setup")
            script_shared_labels = buckets.get("script_shared")
            for did, history in _batch_history(h, portal_ids):
                if v == "cluster1":
                    history = _cascade_augment_history(history, resolve, {lab.strip().lower(): sid for sid, lab in id_to_label.items()})
                assigned_ts = attempt_ts = vc_setup_ts = script_shared_ts = None
                for hent in history:
                    label = resolve(hent["value"])
                    if not label: continue
                    ts = dt.datetime.fromisoformat(hent["timestamp"].replace("Z", "+00:00"))
                    if label in buckets["leads_assigned"] and (assigned_ts is None or ts < assigned_ts):
                        assigned_ts = ts
                    if label in buckets["calls_attempted"] and (attempt_ts is None or ts < attempt_ts):
                        attempt_ts = ts
                    if vc_setup_labels and label in vc_setup_labels and (vc_setup_ts is None or ts < vc_setup_ts):
                        vc_setup_ts = ts
                    if script_shared_labels and label in script_shared_labels and (script_shared_ts is None or ts < script_shared_ts):
                        script_shared_ts = ts
                if assigned_ts is not None:
                    same_day_total += 1
                    if attempt_ts is not None and attempt_ts.astimezone(IST).date() == assigned_ts.astimezone(IST).date():
                        same_day_hit += 1
                if vc_setup_ts is not None:
                    same_day_script_total += 1
                    if script_shared_ts is not None and script_shared_ts.astimezone(IST).date() == vc_setup_ts.astimezone(IST).date():
                        same_day_script_hit += 1

        def pct_or_none(h_, n_): return round(100 * h_ / n_, 1) if n_ else None
        out[v] = {
            "avg_attempts": round(weighted_att / total_n, 2) if total_n else 0,
            "pct_touch": round(100 * all_h / all_n, 1) if all_n else 0,
            "pct_nonpicked_touch": round(100 * nonpicked_h / nonpicked_n, 1) if nonpicked_n else 0,
            "pct_followup_picked": pct_or_none(picked_h, picked_n),
            "pct_followup_interested": pct_or_none(interested_h, interested_n),
            "pct_followup_noshow": pct_or_none(noshow_h, noshow_n) if (buckets["vc_noshow"] or any_noshow_stage) else "no_noshow_stage",
            "pct_same_day_lead": round(100 * same_day_hit / same_day_total, 1) if same_day_total else None,
            "pct_same_day_script": round(100 * same_day_script_hit / same_day_script_total, 1) if same_day_script_total else None,
        }
    return out

# ---------------------------------------------------------------------------------------------------------------
# 7a. Rep + day-level granular stats (2026-10-14, explicit user ask: "granular level reporting must come
#     for everyday too and also each rep wise too... rep wise rolls up to week and then month"). Covers the
#     5 metrics that are genuinely day/WTD/MTD-windowable by a stage-ENTRY timestamp (same-day lead
#     coverage, same-day script coverage, and the 3 follow-up-coverage rows) - windowed by the EARLIEST
#     entry into the relevant bucket stage, same dedup rule as rep_day_counts/team_day_counts. avg_attempts/
#     pct_touch/pct_nonpicked_touch are deliberately NOT windowed here (see the 2026-10-14 fix in the
#     now-retired standalone tools/granular_rep_day.py prototype this was ported from: windowing a "how
#     much effort has accumulated" metric by "leads assigned in this exact window" biases every rep toward
#     0, since a freshly-assigned lead hasn't had time to accumulate attempts yet) - those 3 stay whatever
#     _load_granular_stats() already computes (team-wide, all-time), unchanged.
#     Returns (rep_granular, team_granular): rep_granular is {owner: {window: {metric: pct_or_None}}},
#     team_granular is the same shape one level up (no owner key) - summed across this vertical's mapped
#     reps (REP_MAPS[v]), same "sum-of-mapped-reps" convention and gap as team_day_counts.
# ---------------------------------------------------------------------------------------------------------------
GRANULAR_WINDOWED_PORTALS = {
    "cluster1": [(lambda: env["HUBSPOT_KEY_COMPANYOPS"], "default", None)],
    "cluster2": [(lambda: env["HUBSPOT_KEY_COMPANYOPS"], "2464812771", None)],
    "coding": [(lambda: env["HUBSPOT_KEY_MAIN"], "default", None),
               (lambda: env["HUBSPOT_KEY_COMPANYOPS"], "2608440044", "2464812771"),
               (lambda: env["HUBSPOT_KEY_RAT"], "2608440047", "2575252183")],
}

def pull_rep_granular(v):
    buckets = BUCKET_STAGES[v]
    acc = {}  # owner -> window -> {picked_h/n, interested_h/n, noshow_h/n, sameday_lead_h/n, sameday_script_h/n,
              #                     touch_h/n, nonpicked_touch_h/n, any_noshow_stage}
    import collections
    def new_acc(): return {"picked_h": 0, "picked_n": 0, "interested_h": 0, "interested_n": 0,
        "noshow_h": 0, "noshow_n": 0, "sameday_lead_h": 0, "sameday_lead_n": 0,
        "sameday_script_h": 0, "sameday_script_n": 0,
        "touch_h": 0, "touch_n": 0, "nonpicked_touch_h": 0, "nonpicked_touch_n": 0, "any_noshow_stage": False}
    acc = collections.defaultdict(lambda: collections.defaultdict(new_acc))

    def window_keys(ts):
        keys = []
        d = ts.astimezone(IST).date()
        if week_start <= d <= today: keys.append(d.isoformat())
        if ts >= WTD_START: keys.append("wtd")
        if ts >= MTD_START: keys.append("mtd")
        return keys

    for key_fn, pipeline, old_pipeline in GRANULAR_WINDOWED_PORTALS[v]:
        key = key_fn()
        h = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
        id_to_label = stage_labels(h, pipeline)
        body = {"filterGroups": [{"filters": [{"propertyName": "pipeline", "operator": "EQ", "value": pipeline}]}],
                "properties": ["dealstage", "hubspot_owner_id", "followup_channel_touch"], "limit": 100}
        deals, after = [], None
        while True:
            b = dict(body)
            if after: b["after"] = after
            r = requests.post("https://api.hubapi.com/crm/v3/objects/deals/search", headers=h, json=b, timeout=60).json()
            deals += r.get("results", [])
            after = r.get("paging", {}).get("next", {}).get("after")
            if not after: break
        owner_of = {d["id"]: (d["properties"] or {}).get("hubspot_owner_id") for d in deals}
        current_label = {d["id"]: id_to_label.get((d["properties"] or {}).get("dealstage")) for d in deals}
        touched_of = {d["id"]: bool((d["properties"] or {}).get("followup_channel_touch")) for d in deals}
        portal_ids = [d["id"] for d in deals]

        # FIXED 2026-10-14 (explicit user ask): "follow-up coverage" means a follow-up task was both
        # CREATED and RESOLVED, not just created-and-ignored - checks for at least one associated task
        # with hs_task_status=COMPLETED, not just any association at all.
        task_ids_by_deal = {}
        for i in range(0, len(portal_ids), 100):
            chunk = portal_ids[i:i + 100]
            r = requests.post("https://api.hubapi.com/crm/v4/associations/deals/tasks/batch/read",
                headers=h, json={"inputs": [{"id": did} for did in chunk]}, timeout=30).json()
            for res in r.get("results", []):
                task_ids_by_deal[res["from"]["id"]] = {t.get("toObjectId") or t.get("id") for t in res.get("to", [])}
        all_task_ids = sorted({tid for ids in task_ids_by_deal.values() for tid in ids})
        completed_task_ids = set()
        for i in range(0, len(all_task_ids), 100):
            chunk = all_task_ids[i:i + 100]
            r = requests.post("https://api.hubapi.com/crm/v3/objects/tasks/batch/read", headers=h,
                json={"inputs": [{"id": tid} for tid in chunk], "properties": ["hs_task_status"]}, timeout=30).json()
            for t in r.get("results", []):
                if (t.get("properties") or {}).get("hs_task_status") == "COMPLETED":
                    completed_task_ids.add(t["id"])
        has_task = lambda did: bool(task_ids_by_deal.get(did, set()) & completed_task_ids)

        if old_pipeline:
            old_labels = stage_labels(h, old_pipeline)
            def resolve(raw_value, _id_to_label=id_to_label, _old_labels=old_labels):
                if raw_value in _id_to_label: return _id_to_label[raw_value]
                raw_label = _old_labels.get(raw_value)
                return REASSIGN.get(raw_label.strip().lower()) if raw_label else None
        else:
            def resolve(raw_value, _id_to_label=id_to_label): return _id_to_label.get(raw_value)

        vc_setup_labels, script_shared_labels = buckets.get("vc_setup"), buckets.get("script_shared")
        _rev_labels = {lab.strip().lower(): sid for sid, lab in id_to_label.items()} if v == "cluster1" else None
        for did, history in _batch_history(h, portal_ids):
            if v == "cluster1":
                history = _cascade_augment_history(history, resolve, _rev_labels)
            owner = owner_of.get(did)
            if not owner: continue
            assigned_ts = attempt_ts = vc_setup_ts = script_shared_ts = None
            picked_ts = interested_ts = noshow_ts = None
            for hent in history:
                label = resolve(hent["value"])
                if not label: continue
                # FIXED 2026-10-14 (caught live by user re: Pratyush's numbers): "Replied" can be reached
                # two ways - a real cold-call pickup (CRM_UI, a human moved it), or OutFlo's LinkedIn-reply
                # automation moving a deal straight to Replied with NO call ever made (SOP section 1b -
                # "skip the cold-call steps ... as if they just replied on a call"). Without this guard,
                # every LinkedIn-auto Replied was wrongly counted as a call attempt/pickup here. Same guard
                # counts_for_ids()/rep_day_counts() already apply for the core Calls-attempted/-picked rows
                # (added 2026-10-12) - this function never had it, so it alone was inflating same-day-lead
                # coverage and follow-up coverage's "picked" bucket with non-call LinkedIn replies.
                any_src = label.strip().lower() in ("cold call", "cold called assigned", "cold lead", "cold lead assigned")
                if not any_src and hent.get("sourceType") != "CRM_UI": continue
                ts = dt.datetime.fromisoformat(hent["timestamp"].replace("Z", "+00:00"))
                if label in buckets["leads_assigned"] and (assigned_ts is None or ts < assigned_ts): assigned_ts = ts
                if label in buckets["calls_attempted"] and (attempt_ts is None or ts < attempt_ts): attempt_ts = ts
                if vc_setup_labels and label in vc_setup_labels and (vc_setup_ts is None or ts < vc_setup_ts): vc_setup_ts = ts
                if script_shared_labels and label in script_shared_labels and (script_shared_ts is None or ts < script_shared_ts): script_shared_ts = ts
                if label in buckets["picked"] and (picked_ts is None or ts < picked_ts): picked_ts = ts
                if label in buckets["interested"] and (interested_ts is None or ts < interested_ts): interested_ts = ts
                if buckets["vc_noshow"] and label in buckets["vc_noshow"] and (noshow_ts is None or ts < noshow_ts): noshow_ts = ts

            same_day_lead_ok = (attempt_ts is not None and assigned_ts is not None and
                                 attempt_ts.astimezone(IST).date() == assigned_ts.astimezone(IST).date())
            same_day_script_ok = (script_shared_ts is not None and vc_setup_ts is not None and
                                   script_shared_ts.astimezone(IST).date() == vc_setup_ts.astimezone(IST).date())
            touched = touched_of.get(did, False)
            is_picked_now = current_label.get(did) in buckets["picked"]

            for ts_field, ts_val in (("assigned", assigned_ts), ("picked", picked_ts),
                                      ("interested", interested_ts), ("noshow", noshow_ts), ("vc_setup", vc_setup_ts)):
                if ts_val is None: continue
                for w in window_keys(ts_val):
                    a = acc[owner][w]
                    if ts_field == "assigned":
                        a["sameday_lead_n"] += 1; a["sameday_lead_h"] += 1 if same_day_lead_ok else 0
                        # Email/WhatsApp touch coverage (2026-10-14, explicit user ask): windowed as a
                        # SNAPSHOT of "deals added to the funnel in this window" - not an all-time rate,
                        # matching same_day_lead's own denominator exactly, since the SOP ties the touch
                        # to the same day a lead is worked (Attempt 1), not to its cumulative lifetime.
                        a["touch_n"] += 1; a["touch_h"] += 1 if touched else 0
                        if not is_picked_now:
                            a["nonpicked_touch_n"] += 1; a["nonpicked_touch_h"] += 1 if touched else 0
                    elif ts_field == "picked":
                        a["picked_n"] += 1; a["picked_h"] += 1 if has_task(did) else 0
                    elif ts_field == "interested":
                        a["interested_n"] += 1; a["interested_h"] += 1 if has_task(did) else 0
                    elif ts_field == "noshow":
                        a["any_noshow_stage"] = True
                        a["noshow_n"] += 1; a["noshow_h"] += 1 if has_task(did) else 0
                    elif ts_field == "vc_setup":
                        a["sameday_script_n"] += 1; a["sameday_script_h"] += 1 if same_day_script_ok else 0

    def pct(h_, n_): return round(100 * h_ / n_, 1) if n_ else None
    def finalize(m, any_noshow_stage):
        return {
            "pct_followup_picked": pct(m["picked_h"], m["picked_n"]),
            "pct_followup_interested": pct(m["interested_h"], m["interested_n"]),
            "pct_followup_noshow": pct(m["noshow_h"], m["noshow_n"]) if (buckets["vc_noshow"] or any_noshow_stage) else "no_noshow_stage",
            "pct_same_day_lead": pct(m["sameday_lead_h"], m["sameday_lead_n"]),
            "pct_same_day_script": pct(m["sameday_script_h"], m["sameday_script_n"]),
            "pct_touch": pct(m["touch_h"], m["touch_n"]),
            "pct_nonpicked_touch": pct(m["nonpicked_touch_h"], m["nonpicked_touch_n"]),
        }

    rep_granular = {owner: {w: finalize(m, m["any_noshow_stage"]) for w, m in windows.items()}
                     for owner, windows in acc.items()}

    # Team-level: sum raw hit/n across this vertical's MAPPED reps only (REP_MAPS[v]) before computing the
    # percentage - same "sum-of-mapped-reps" convention/gap as team_day_counts (an unmapped-owner deal is
    # invisible here too).
    team_acc = collections.defaultdict(new_acc)
    for owner in REP_MAPS.get(v, {}):
        for w, m in acc.get(owner, {}).items():
            t = team_acc[w]
            for k in ("picked_h", "picked_n", "interested_h", "interested_n", "noshow_h", "noshow_n",
                      "sameday_lead_h", "sameday_lead_n", "sameday_script_h", "sameday_script_n",
                      "touch_h", "touch_n", "nonpicked_touch_h", "nonpicked_touch_n"):
                t[k] += m[k]
            t["any_noshow_stage"] = t["any_noshow_stage"] or m["any_noshow_stage"]
    team_granular = {w: finalize(m, m["any_noshow_stage"]) for w, m in team_acc.items()}

    return rep_granular, team_granular

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
