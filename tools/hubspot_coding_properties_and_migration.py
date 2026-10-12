#!/usr/bin/env python3
"""Coding funnel equivalent of hubspot_cluster2_properties_and_migration.py - same playbook, now across
THREE separate HubSpot portals (MAIN, RAT, CompanyOps) instead of one, since Coding's 5 reps
(Lamiya/Yuktha/Shagufta in MAIN, Vaishnavi in CompanyOps, Harsha in RAT) live in three different
HubSpot accounts with three different private-app tokens.

Per-portal work:
  1. Create segment/sub_icp/headcount_band/apollo_filter_signature/call_attempts_count (skip any that
     already exist - CompanyOps already has all but the new option values, added separately).
  2. Create the "No Pickup - Attempt 1/2/3" stage chain (same granular-reporting pattern as Cluster 2),
     right after the existing flat "No Pickup" stage.
  3. Migrate every deal currently sitting in the old flat "No Pickup" stage into "No Pickup - Attempt 1",
     seeding call_attempts_count=1 (matches Cluster 2's migration - a deal that's been in No Pickup at
     least once genuinely represents 1 real attempt).
  4. HARD DELETE the old "No Pickup" stage once it's empty - per the explicit standing rule from the
     Cluster 2 build ("retired in place is confusing just delete them"), not retire-in-place.

A full pre-migration backup of every deal's current properties, across all 3 portals, is written to
data/audit/coding_pre_migration_full_backup_<timestamp>.json BEFORE any write, every run (dry-run or
apply) - so --apply is never the first time this data has been captured.

Dry run by default. `--apply` performs the writes."""
import argparse, json, sys, time
from datetime import datetime, timezone
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parent.parent
BASE = "https://api.hubapi.com"

SEGMENT_OPTIONS = ["IT Services"]
SUBICP_OPTIONS = ["IT services, consulting & managed services providers (India)"]
BAND_OPTIONS = ["51-200", "201-350", "351-500"]  # Coding-specific - NOT Cluster 1/2's bands

PORTALS = [
    {"label": "MAIN", "env_key": "HUBSPOT_KEY_MAIN", "pipeline": "default", "old_nopickup_id": "4104051404"},
    {"label": "RAT", "env_key": "HUBSPOT_KEY_RAT", "pipeline": "2608440047", "old_nopickup_id": "4405141229"},
    {"label": "COMPANYOPS", "env_key": "HUBSPOT_KEY_COMPANYOPS", "pipeline": "2608440044", "old_nopickup_id": "4405141187"},
]


def env():
    d = {}
    for line in (ROOT / ".env").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            d[k.strip()] = v.strip().strip('"').strip("'")
    return d


def session(token):
    s = requests.Session()
    s.headers.update({"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    return s


def call(s, method, path, **kw):
    for attempt in range(6):
        r = s.request(method, BASE + path, timeout=60, **kw)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2 * (attempt + 1)); continue
        if r.status_code >= 400:
            print(f"  WARN {method} {path} -> {r.status_code} {r.text[:300]}")
            return None
        return r.json() if r.text else {}
    print(f"  FAILED (retries exhausted) {method} {path}")
    return None


def backup_portal(s, pipeline, label):
    deals = []
    body = {"filterGroups": [{"filters": [{"propertyName": "pipeline", "operator": "EQ", "value": pipeline}]}],
            "properties": ["dealname", "dealstage", "hubspot_owner_id", "segment", "sub_icp",
                            "headcount_band", "call_attempts_count", "createdate"],
            "limit": 100}
    after = None
    while True:
        b = dict(body)
        if after: b["after"] = after
        r = call(s, "POST", "/crm/v3/objects/deals/search", json=b)
        if not r: break
        deals += r.get("results", [])
        after = r.get("paging", {}).get("next", {}).get("after")
        if not after: break
    print(f"  {label}: backed up {len(deals)} deals")
    return deals


def create_properties(s, apply, label):
    props = [
        {"name": "segment", "label": "Segment", "type": "enumeration", "fieldType": "select",
         "groupName": "dealinformation", "options": [{"label": v, "value": v, "displayOrder": i, "hidden": False} for i, v in enumerate(SEGMENT_OPTIONS)]},
        {"name": "sub_icp", "label": "Sub ICP", "type": "enumeration", "fieldType": "select",
         "groupName": "dealinformation", "options": [{"label": v, "value": v, "displayOrder": i, "hidden": False} for i, v in enumerate(SUBICP_OPTIONS)]},
        {"name": "headcount_band", "label": "Headcount Band", "type": "enumeration", "fieldType": "select",
         "groupName": "dealinformation", "options": [{"label": v, "value": v, "displayOrder": i, "hidden": False} for i, v in enumerate(BAND_OPTIONS)]},
        {"name": "apollo_filter_signature", "label": "Apollo Filter Signature", "type": "string", "fieldType": "text", "groupName": "dealinformation"},
        {"name": "call_attempts_count", "label": "Call attempts (cumulative)", "type": "number", "fieldType": "number", "groupName": "dealinformation"},
    ]
    for p in props:
        existing = call(s, "GET", f"/crm/v3/properties/deals/{p['name']}")
        if existing:
            # property already exists (e.g. CompanyOps) - just make sure Coding's option values are present
            if p.get("options"):
                opts = existing["options"]
                have = {o["value"] for o in opts}
                max_order = max((o.get("displayOrder", 0) for o in opts), default=0)
                missing = [v for v in [o["value"] for o in p["options"]] if v not in have]
                if missing:
                    print(f"  {label}: {'[apply] adding' if apply else '[dry-run] would add'} {len(missing)} new option(s) to existing '{p['name']}': {missing}")
                    if apply:
                        for i, v in enumerate(missing):
                            opts.append({"label": v, "value": v, "displayOrder": max_order + i + 1, "hidden": False})
                        call(s, "PATCH", f"/crm/v3/properties/deals/{p['name']}", json={"options": opts})
                else:
                    print(f"  {label}: '{p['name']}' already has all needed options")
            else:
                print(f"  {label}: '{p['name']}' already exists, skipping")
            continue
        print(f"  {label}: {'[apply] creating' if apply else '[dry-run] would create'} property '{p['name']}'")
        if apply:
            call(s, "POST", "/crm/v3/properties/deals", json=p)


def create_new_stages(s, apply, pipeline, label):
    new_stages = [
        {"label": "No Pickup - Attempt 1", "displayOrder": 1.1},
        {"label": "No Pickup - Attempt 2", "displayOrder": 1.2},
        {"label": "No Pickup - Attempt 3", "displayOrder": 1.3},
    ]
    created_ids = {}
    for st in new_stages:
        print(f"  {label}: {'[apply] creating' if apply else '[dry-run] would create'} stage '{st['label']}'")
        if apply:
            r = call(s, "POST", f"/crm/v3/pipelines/deals/{pipeline}/stages", json={
                "label": st["label"], "displayOrder": st["displayOrder"], "metadata": {"probability": "0.1"}})
            if r:
                created_ids[st["label"]] = r["id"]
                print(f"    -> id {r['id']}")
    return created_ids


def migrate_no_pickup_deals(s, apply, pipeline, old_stage_id, attempt1_stage_id, label):
    body = {"filterGroups": [{"filters": [
        {"propertyName": "pipeline", "operator": "EQ", "value": pipeline},
        {"propertyName": "dealstage", "operator": "EQ", "value": old_stage_id}]}],
        "properties": ["dealname"], "limit": 100}
    ids = []
    after = None
    while True:
        b = dict(body)
        if after: b["after"] = after
        r = call(s, "POST", "/crm/v3/objects/deals/search", json=b)
        if not r: break
        ids += [d["id"] for d in r.get("results", [])]
        after = r.get("paging", {}).get("next", {}).get("after")
        if not after: break
    print(f"  {label}: {len(ids)} deals found in old No Pickup stage")
    if not apply:
        print(f"  {label}: [dry-run] would migrate these to No Pickup - Attempt 1, call_attempts_count=1")
        return len(ids)
    if not attempt1_stage_id:
        print(f"  {label}: ERROR no Attempt-1 stage id available, cannot migrate")
        return 0
    moved = 0
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        b = {"inputs": [{"id": x, "properties": {"dealstage": attempt1_stage_id, "call_attempts_count": "1"}} for x in chunk]}
        call(s, "POST", "/crm/v3/objects/deals/batch/update", json=b)
        moved += len(chunk)
        print(f"    {label}: migrated {moved}/{len(ids)}")
    return moved


def delete_old_stage(s, apply, pipeline, old_stage_id, label):
    print(f"  {label}: {'[apply] hard-deleting' if apply else '[dry-run] would hard-delete'} old No Pickup stage {old_stage_id}")
    if apply:
        r = call(s, "DELETE", f"/crm/v3/pipelines/deals/{pipeline}/stages/{old_stage_id}")
        print(f"    -> {'ok' if r is not None else 'FAILED (see WARN above)'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    e = env()

    print("=== 0. pre-migration backup (every run, dry-run or apply) ===")
    backup = {}
    for p in PORTALS:
        s = session(e[p["env_key"]])
        backup[p["label"]] = backup_portal(s, p["pipeline"], p["label"])
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = ROOT / "data" / "audit" / f"coding_pre_migration_full_backup_{ts}.json"
    out.write_text(json.dumps(backup, indent=1))
    print(f"  backup written -> {out}")

    for p in PORTALS:
        print(f"\n{'='*20} {p['label']} {'='*20}")
        s = session(e[p["env_key"]])
        print("--- 1. properties ---")
        create_properties(s, a.apply, p["label"])
        print("--- 2. new stages ---")
        created = create_new_stages(s, a.apply, p["pipeline"], p["label"])
        print("--- 3. migrate No Pickup deals ---")
        attempt1_id = created.get("No Pickup - Attempt 1")
        migrate_no_pickup_deals(s, a.apply, p["pipeline"], p["old_nopickup_id"], attempt1_id, p["label"])
        print("--- 4. delete old No Pickup stage ---")
        delete_old_stage(s, a.apply, p["pipeline"], p["old_nopickup_id"], p["label"])

    if not a.apply:
        print("\nDRY RUN complete across all 3 portals - rerun with --apply to execute for real.")


if __name__ == "__main__":
    main()
