#!/usr/bin/env python3
"""Cluster 1 equivalent of hubspot_cluster2_properties_and_migration.py - same playbook, different pipeline.
Pipeline 'default' (Company Ops Cluster 1 India), companyops portal. Full pre-migration backup already
written to data/audit/cluster1_pre_migration_full_backup_*.json before this runs.

sub_icp/headcount_band/apollo_filter_signature/call_attempts_count/followup_channel_touch/closed_lost_reason_tag/
gmeet1_link already exist portal-wide from the Cluster 2 work - NOT recreated here, just reused. Only the
'segment' property needs its 5 missing exact-wording Cluster-1 options added.

Dry run by default. `--apply` performs the writes."""
import argparse, sys, time
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parent.parent
BASE = "https://api.hubapi.com"
PIPELINE = "default"


def key():
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith("HUBSPOT_KEY_COMPANYOPS="):
            return line.split("=", 1)[1].strip()
    sys.exit("HUBSPOT_KEY_COMPANYOPS missing")


S = requests.Session()
S.headers.update({"Authorization": "Bearer " + key(), "Content-Type": "application/json"})


def call(method, path, **kw):
    for attempt in range(6):
        r = S.request(method, BASE + path, timeout=60, **kw)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2 * (attempt + 1)); continue
        if r.status_code >= 400:
            print(f"  WARN {method} {path} -> {r.status_code} {r.text[:300]}")
            return None
        return r.json() if r.text else {}
    print(f"  FAILED (retries exhausted) {method} {path}")
    return None


MISSING_SEGMENT_OPTIONS = ["Food Tech", "Ed-Tech", "Mobility", "Ecommerce/Marketplace", "Healthcare/MedTech"]


def add_missing_segment_options(apply):
    cur = call("GET", "/crm/v3/properties/deals/segment")
    if not cur:
        print("  ERROR: could not read existing 'segment' property")
        return
    existing_values = {o["value"] for o in cur.get("options", [])}
    to_add = [v for v in MISSING_SEGMENT_OPTIONS if v not in existing_values]
    if not to_add:
        print("  all 5 exact-wording options already present, nothing to add")
        return
    print(f"  {'[apply] adding' if apply else '[dry-run] would add'} options to existing 'segment' property: {to_add}")
    if apply:
        new_options = cur["options"] + [{"label": v, "value": v, "hidden": False} for v in to_add]
        call("PATCH", "/crm/v3/properties/deals/segment", json={"options": new_options})


def create_new_stages(apply):
    new_stages = [
        {"label": "No Pickup - Attempt 1", "displayOrder": 3},
        {"label": "No Pickup - Attempt 2", "displayOrder": 4},
        {"label": "No Pickup - Attempt 3", "displayOrder": 5},
    ]
    created_ids = {}
    for s in new_stages:
        print(f"  {'[apply] creating' if apply else '[dry-run] would create'} stage '{s['label']}'")
        if apply:
            r = call("POST", f"/crm/v3/pipelines/deals/{PIPELINE}/stages", json={
                "label": s["label"], "displayOrder": s["displayOrder"], "metadata": {"probability": "0.1"}})
            if r:
                created_ids[s["label"]] = r["id"]
                print(f"    -> id {r['id']}")
    return created_ids


def rename_cold_assigned(apply):
    stage_id = "4327110346"
    print(f"  {'[apply] renaming' if apply else '[dry-run] would rename'} Cold called assigned -> Cold lead assigned")
    if apply:
        call("PATCH", f"/crm/v3/pipelines/deals/{PIPELINE}/stages/{stage_id}", json={"label": "Cold lead assigned"})


def migrate_no_pickup_deals(apply, attempt1_stage_id):
    old_stage_ids = ["4326787819", "4326787820"]  # No pickup, Retired: Callback +1 day
    ids = []
    for sid in old_stage_ids:
        body = {"filterGroups": [{"filters": [
            {"propertyName": "pipeline", "operator": "EQ", "value": PIPELINE},
            {"propertyName": "dealstage", "operator": "EQ", "value": sid}]}],
            "properties": ["dealname"], "limit": 100}
        after = None
        while True:
            if after:
                body["after"] = after
            r = call("POST", "/crm/v3/objects/deals/search", json=body)
            if not r:
                break
            ids += [d["id"] for d in r.get("results", [])]
            after = r.get("paging", {}).get("next", {}).get("after")
            if not after:
                break
    print(f"  {len(ids)} deals found across the two legacy stages")
    if not apply:
        print("  [dry-run] would migrate these to No Pickup - Attempt 1, call_attempts_count=1")
        return len(ids)
    if not attempt1_stage_id:
        print("  ERROR: no Attempt-1 stage id available, cannot migrate")
        return 0
    moved = 0
    for i in range(0, len(ids), 100):
        chunk = ids[i:i+100]
        body = {"inputs": [{"id": x, "properties": {"dealstage": attempt1_stage_id, "call_attempts_count": "1"}} for x in chunk]}
        call("POST", "/crm/v3/objects/deals/batch/update", json=body)
        moved += len(chunk)
        print(f"    migrated {moved}/{len(ids)}")
    return moved


def delete_empty_stages(apply):
    stages = {
        "No pickup (old, now empty)": "4326787819",
        "Retired: Callback +1 day (old, now empty)": "4326787820",
        "LinkedIn sent (already empty)": "4327110345",
    }
    for label, sid in stages.items():
        print(f"  {'[apply] deleting' if apply else '[dry-run] would delete'} stage '{label}'")
        if apply:
            r = S.delete(f"{BASE}/crm/v3/pipelines/deals/{PIPELINE}/stages/{sid}")
            print(f"    -> {r.status_code} {r.text[:200]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    print("=== 1. missing segment options ===")
    add_missing_segment_options(a.apply)
    print("=== 2. new stages ===")
    created = create_new_stages(a.apply)
    print("=== 3. rename Cold called assigned ===")
    rename_cold_assigned(a.apply)
    print("=== 4. migrate No pickup + Retired: Callback deals ===")
    attempt1_id = created.get("No Pickup - Attempt 1")
    migrate_no_pickup_deals(a.apply, attempt1_id)
    print("=== 5. delete now-empty legacy stages ===")
    delete_empty_stages(a.apply)
    if not a.apply:
        print("\nDRY RUN complete - rerun with --apply to execute for real.")


if __name__ == "__main__":
    main()
