#!/usr/bin/env python3
"""Move N cold-called-assigned deals from one Cluster 2 owner to another (HubSpot owner property only).
Dry run by default; --apply writes. Usage: shift_cold_owner.py --from 168015618 --to 168015679 --n 20 [--apply]"""
import argparse, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
import push_itsvc_leads as P  # noqa: E402
PIPE, COLD = "2464812771", "4275264191"
ap = argparse.ArgumentParser(); ap.add_argument("--from", dest="src", required=True); ap.add_argument("--to", dest="dst", required=True)
ap.add_argument("--n", type=int, required=True); ap.add_argument("--apply", action="store_true"); a = ap.parse_args()
ids = []; after = None
body = {"filterGroups": [{"filters": [{"propertyName": "hubspot_owner_id", "operator": "EQ", "value": a.src},
        {"propertyName": "pipeline", "operator": "EQ", "value": PIPE}, {"propertyName": "dealstage", "operator": "EQ", "value": COLD}]}],
        "properties": ["dealname"], "limit": 100}
while True:
    b = dict(body)
    if after: b["after"] = after
    j = P.call("companyops", "POST", "/crm/v3/objects/deals/search", json=b)
    ids += [r["id"] for r in j.get("results", [])]
    after = (j.get("paging") or {}).get("next", {}).get("after")
    if not after: break
pick = sorted(ids, key=int)[: a.n]
print("source cold-call deals:", len(ids), "| will move:", len(pick), "| to owner", a.dst, "| apply" if a.apply else "| DRY RUN")
if a.apply:
    for i in pick:
        P.call("companyops", "PATCH", f"/crm/v3/objects/deals/{i}", json={"properties": {"hubspot_owner_id": a.dst}})
    print("moved", len(pick))
