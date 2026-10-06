#!/usr/bin/env python3
"""Merge the per-repo Apollo audit ledgers (docs/audit/apollo/**) into one master list of credit events,
choosing exactly one source per (repo, period) so nothing is double counted, then test which day boundary
(timezone) best lines our traced credits up with Apollo's own daily usage chart.
Outputs: docs/audit/apollo/master_ledger.csv, master_summary.json (and prints a report)."""
import csv, json, re, collections
from datetime import datetime, timedelta
from pathlib import Path

A = Path(__file__).resolve().parent.parent / "docs" / "audit" / "apollo"
IST = timedelta(hours=5, minutes=30)

def fl(x):
    try: return float(str(x).replace(",", ""))
    except Exception: return 0.0

def pts(s):
    s = (s or "").strip().replace(" IST", "")
    if not s: return None
    s = s.replace("T", " ")
    s = re.sub(r"\+05:30$", "", s)
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try: return datetime.strptime(s, f)
        except ValueError: pass
    return None

events = []   # dict(repo, source, start, end, credits, est, purpose)

def add(repo, source, start, end, credits, est, purpose, extra=None):
    if not credits or start is None: return
    events.append(dict(repo=repo, source=source, start=start, end=end or start, credits=credits, est=est, purpose=purpose[:120], **(extra or {})))

def is_est(r):
    n = r.get("notes", "")
    if "[ESTIMATED]" in n: return True
    if "[MEASURED]" in n: return False
    return r.get("evidence_type") in ("code_estimate", "chat_claim")

# --- companyOps -------------------------------------------------------------
for r in csv.DictReader(open(A / "pre/companyops_ledger.csv")):
    if r["repo"] != "companyOps": continue
    d = r["date_ist"]
    if d >= "2026-09-29": continue
    add("companyOps", "pre/companyops_ledger.csv", pts(r["ts_ist"]), None, fl(r["credits"]), is_est(r), r["purpose"])
for r in csv.DictReader(open(A / "companyops_ledger.csv")):
    if r["repo"] != "companyOps" or r["date_ist"] < "2026-09-29": continue
    add("companyOps", "companyops_ledger.csv", pts(r["ts_ist"]), None, fl(r["credits"]), is_est(r), r["purpose"])

# --- hubspot repo -----------------------------------------------------------
for r in csv.DictReader(open(A / "pre/hubspot_ledger.csv")):
    if r["repo"] != "hubspot": continue
    add("hubspot", "pre/hubspot_ledger.csv", pts(r["ts_ist"]), None, fl(r["credits"]), is_est(r), r["purpose"])
for r in csv.DictReader(open(A / "agents/hubspot_0927_1004.csv")):
    s, e = pts(r["start_ts_ist"]), pts(r["end_ts_ist"])
    if s is None or s < datetime(2026, 9, 29): continue
    tr = fl(r["credits_tracked"]); un = fl(r["credits_untracked_central"])
    add("hubspot", "agents/hubspot_0927_1004.csv", s, e, tr + un, un > 0 and tr == 0, r["agent_task"])

# --- RapidActionTeam (the RAT repo's own spend only; sibling-repo RAT-purpose rows are already in hubspot) ---
for f in ("pre/rat_ledger.csv", "rat_ledger.csv"):
    for r in csv.DictReader(open(A / f)):
        if r["repo"] != "RapidActionTeam": continue
        if r["date_ist"] >= "2026-09-29" and f.startswith("pre"): continue
        if r["date_ist"] < "2026-09-29" and not f.startswith("pre"): continue
        add("RapidActionTeam", f, pts(r["ts_ist"]), None, fl(r["credits"]), is_est(r), r["purpose"])

# --- write master ledger ---------------------------------------------------------
with open(A / "master_ledger.csv", "w", newline="") as fh:
    w = csv.writer(fh); w.writerow(["repo", "source", "start_ist", "end_ist", "credits", "estimated", "purpose"])
    for e in sorted(events, key=lambda e: e["start"]):
        w.writerow([e["repo"], e["source"], e["start"].isoformat(sep=" "), e["end"].isoformat(sep=" "), e["credits"], int(e["est"]), e["purpose"]])

# --- chart (user's screenshot, +-0.3k, labelled days) -----------------------------
CHART = {"2026-09-21": 12500, "2026-09-22": 6800, "2026-09-23": 5600, "2026-09-24": 12400, "2026-09-25": 3000,
         "2026-09-26": 0, "2026-09-27": 4600, "2026-09-28": 9300, "2026-09-29": 13800, "2026-09-30": 14800,
         "2026-10-01": 7100, "2026-10-02": 0, "2026-10-03": 6200, "2026-10-04": 0}

def bucket(offset_h):
    """day buckets for a day boundary at local midnight of UTC+offset_h; spread interval events by minute"""
    shift = timedelta(hours=offset_h) - IST     # IST clock -> local clock
    out = collections.defaultdict(float)
    for e in events:
        s, t = e["start"] + shift, e["end"] + shift
        mins = max(int((t - s).total_seconds() // 60), 0)
        if mins == 0:
            out[s.strftime("%Y-%m-%d")] += e["credits"]
        else:
            share = e["credits"] / (mins + 1)
            for i in range(mins + 1):
                out[(s + timedelta(minutes=i)).strftime("%Y-%m-%d")] += share
    return out

if __name__ == "__main__":
    tot = collections.Counter()
    for e in events: tot[(e["repo"], e["est"])] += e["credits"]
    print("events:", len(events), " totals (repo, estimated?):", {f"{k[0]}|{'E' if k[1] else 'M'}": round(v) for k, v in tot.items()})
    print("grand total traced:", round(sum(e["credits"] for e in events)))
    results = []
    for h2 in range(-24, 29):      # offsets in half hours
        h = h2 / 2
        b = bucket(h)
        over = sum(max(0.0, b.get(d, 0) - c - 300) for d, c in CHART.items())
        viol = sum(1 for d, c in CHART.items() if b.get(d, 0) - c > 300)
        results.append((over, viol, h, b))
    results.sort(key=lambda x: (x[0], x[1]))
    print("\nbest day boundaries (UTC offset, overshoot credits beyond chart+0.3k, #days overshooting):")
    for over, viol, h, b in results[:8]:
        print(f"  UTC{h:+.1f}  overshoot={over:7.0f}  days_over={viol}")
    print("\nfixed reference offsets:")
    for name, h in (("UTC", 0), ("IST", 5.5), ("US Pacific (PDT)", -7), ("US Eastern (EDT)", -4)):
        b = bucket(h); over = sum(max(0.0, b.get(d, 0) - c - 300) for d, c in CHART.items())
        print(f"  {name:18s} overshoot={over:7.0f}")
    json.dump({"events": len(events), "traced_total": sum(e["credits"] for e in events),
               "best": [(h, over, viol) for over, viol, h, _ in results[:5]]}, open(A / "master_summary.json", "w"), indent=1)
