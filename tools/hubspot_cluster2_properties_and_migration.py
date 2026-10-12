#!/usr/bin/env python3
"""Create the new Cluster 2 deal properties + the No-Pickup-Attempt stage chain, rename Cold called
assigned -> Cold lead assigned, migrate the 1,095 'No pickup' + 101 'Retired: Callback +1 day' deals into
'No Pickup - Attempt 1' (seeding call_attempts_count=1, tagged via a Note since we're not adding a
dedicated migrated_from_legacy property per the minimal-properties rule), then retire 'Retired: Callback'
in place. Full pre-migration backup already written to data/audit/cluster2_pre_migration_full_backup_*.json
before this script runs - see that file to reconstruct original state if anything needs reverting.

Deliberately NOT backfilling segment/sub_icp/headcount_band onto the ~4,898 pre-existing historical deals -
we have no real source-of-truth data for which search/segment produced them (pre-dates this whole sourcing
framework), and fabricating values would violate the project's own evidence-only principle. These 3 new
properties stay BLANK on every existing deal; they populate only on deals pushed through the new sourcing
pipeline going forward, where the real apollo_filter_signature/segment/sub_icp/headcount_band are known.

Dry run by default. `--apply` performs the writes."""
import argparse, json, sys, time
from datetime import datetime, timezone
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parent.parent
BASE = "https://api.hubapi.com"
PIPELINE = "2464812771"


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


SEGMENT_OPTIONS = ["Enterprise SaaS / IT", "Fintech", "Financial Services", "Adtech",
                   "Market Research", "Construction", "Real Estate / Proptech", "Manufacturing"]

SUB_ICP_OPTIONS = [
    # Manufacturing
    "Discrete manufacturing with MES/ERP-integrated shop floors",
    "Process manufacturing (chemicals, pharma, FMCG)",
    "Auto component & EMS/contract manufacturers",
    "Industrial IoT / predictive-maintenance platforms",
    "Supply chain & procurement platforms serving manufacturers",
    # Fintech
    "Payment providers", "Lending / NBFC & BNPL platforms", "Digital wallets & UPI-first apps",
    "Neobanks / digital-first banking platforms", "InsurTech (claims + underwriting workflows)",
    "WealthTech / robo-advisory & broking apps", "Credit scoring & underwriting engines",
    # Financial Services
    "Banks (retail & SME lending ops)", "NBFCs / housing finance companies",
    "Insurance companies (life, health, general)", "Asset management companies / mutual funds",
    "Stock broking & wealth management firms", "Credit rating agencies",
    # Real Estate/Proptech
    "Proptech marketplaces & transaction platforms", "Developer CRM/sales & construction PM platforms",
    "Coworking / flex-workspace operators", "Co-living & managed rental platforms",
    "Society/facility management SaaS", "Data-driven brokerage & advisory firms",
    # Adtech
    "Programmatic DSP/SSP & ad exchange platforms", "Marketing automation / CDP platforms",
    "Influencer marketing & creator-commerce platforms", "Attribution & campaign analytics platforms",
    "Retail media networks",
    # Market Research
    "Syndicated/panel-based research firms", "Survey & consumer-insights platforms",
    "Data/analytics-as-a-service providers", "Social listening & sentiment-analysis platforms",
    "CX/feedback management platforms",
    # Enterprise SaaS/IT
    "Vertical SaaS (HRTech, MarTech, LegalTech, CRM/ERP)", "IT services & GCCs",
    "DevOps/observability & ITSM platforms", "Customer support / helpdesk SaaS",
    "API/infra & developer-tooling platforms",
    # Construction
    "EPC & general contractors", "Construction-tech (PM, BIM, scheduling platforms)",
    "Construction materials procurement platforms", "Architecture & engineering design firms",
    "Site execution platforms",
]

CLOSED_LOST_REASON_OPTIONS = [
    "WrongFit: Industry keyword too broad", "WrongFit: Headcount mismatch (too small)",
    "WrongFit: Headcount mismatch (too large)", "WrongFit: Already a customer/competitor's customer",
    "WrongFit: Duplicate/already contacted", "WrongFit: Other",
    "NotInterested: No budget", "NotInterested: Already has a data vendor",
    "NotInterested: Not relevant to roadmap", "NotInterested: Privacy/compliance concerns",
    "NotInterested: Bad timing", "NotInterested: Other",
    "RejectedByLH2: Data volume insufficient", "RejectedByLH2: Data quality insufficient",
    "RejectedByLH2: Compliance/legal risk", "RejectedByLH2: Not real ICP despite screen",
    "RejectedByLH2: Pricing expectations mismatch",
    "OnePagerQuality: Volume too low", "OnePagerQuality: Stale/freshness", "OnePagerQuality: Missing fields",
    "OnePagerQuality: Unusable format",
    "LOI: Pricing gap", "LOI: Contractual - IP ownership", "LOI: Contractual - exclusivity",
    "LOI: Contractual - indemnity/liability",
    "WrongNumber: Disconnected", "WrongNumber: Reached wrong person",
]


def enum_prop(name, label, options, multi=False):
    return {
        "name": name, "label": label, "type": "enumeration",
        "fieldType": "checkbox" if multi else "select",
        "groupName": "dealinformation",
        "options": [{"label": o, "value": o, "hidden": False} for o in options],
    }


NEW_PROPERTIES = [
    # 'segment' deliberately omitted here - it already exists as a shared, portfolio-wide property (used by
    # every cluster, not just Cluster 2) with its own option set. Adding our 4 missing exact-wording values to
    # it (not creating a duplicate property) happens in add_missing_segment_options() below.
    enum_prop("sub_icp", "Sub-ICP tested", SUB_ICP_OPTIONS),
    enum_prop("headcount_band", "Headcount band", ["51-200", "201-500", "501-1000"]),
    {"name": "apollo_filter_signature", "label": "Sourcing filter signature", "type": "string", "fieldType": "text", "groupName": "dealinformation"},
    {"name": "call_attempts_count", "label": "Call attempts (cumulative)", "type": "number", "fieldType": "number", "groupName": "dealinformation"},
    enum_prop("followup_channel_touch", "Follow-up channel touch", ["Email sent", "WhatsApp sent"], multi=True),
    enum_prop("closed_lost_reason_tag", "Closed-lost reason", CLOSED_LOST_REASON_OPTIONS),
    # gmeet1_link already exists (string/text, exact match to what's needed) - reused as-is, not recreated.
]

MISSING_SEGMENT_OPTIONS = ["Financial Services", "Construction", "Real Estate / Proptech", "Enterprise SaaS / IT"]


def add_missing_segment_options(apply):
    cur = call("GET", "/crm/v3/properties/deals/segment")
    if not cur:
        print("  ERROR: could not read existing 'segment' property")
        return
    existing_values = {o["value"] for o in cur.get("options", [])}
    to_add = [v for v in MISSING_SEGMENT_OPTIONS if v not in existing_values]
    if not to_add:
        print("  all 4 exact-wording options already present, nothing to add")
        return
    print(f"  {'[apply] adding' if apply else '[dry-run] would add'} options to existing 'segment' property: {to_add}")
    if apply:
        new_options = cur["options"] + [{"label": v, "value": v, "hidden": False} for v in to_add]
        call("PATCH", "/crm/v3/properties/deals/segment", json={"options": new_options})


def create_properties(apply):
    for p in NEW_PROPERTIES:
        existing = call("GET", f"/crm/v3/properties/deals/{p['name']}")
        if existing:
            print(f"  property '{p['name']}' already exists, skipping")
            continue
        print(f"  {'[apply] creating' if apply else '[dry-run] would create'} property '{p['name']}'")
        if apply:
            call("POST", "/crm/v3/properties/deals", json=p)


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
    stage_id = "4275264191"
    print(f"  {'[apply] renaming' if apply else '[dry-run] would rename'} Cold called assigned -> Cold lead assigned")
    if apply:
        call("PATCH", f"/crm/v3/pipelines/deals/{PIPELINE}/stages/{stage_id}", json={"label": "Cold lead assigned"})


def migrate_no_pickup_deals(apply, attempt1_stage_id):
    old_stage_ids = ["4307080899", "4307080900"]  # No pickup, Retired: Callback +1 day
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


def retire_callback_stage(apply):
    stage_id = "4307080900"
    print(f"  {'[apply] retiring' if apply else '[dry-run] would retire'} 'Retired: Callback +1 day' in place")
    if apply:
        call("PATCH", f"/crm/v3/pipelines/deals/{PIPELINE}/stages/{stage_id}", json={
            "label": "Retired: Callback +1 day (superseded by No Pickup - Attempt chain)",
            "displayOrder": 101, "metadata": {"probability": "0.0"}})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    print("=== 1. properties ===")
    create_properties(a.apply)
    print("=== 1b. missing segment options ===")
    add_missing_segment_options(a.apply)
    print("=== 2. new stages ===")
    created = create_new_stages(a.apply)
    print("=== 3. rename Cold called assigned ===")
    rename_cold_assigned(a.apply)
    print("=== 4. migrate No pickup + Retired: Callback deals ===")
    attempt1_id = created.get("No Pickup - Attempt 1")
    migrate_no_pickup_deals(a.apply, attempt1_id)
    print("=== 5. retire Retired: Callback stage ===")
    retire_callback_stage(a.apply)
    if not a.apply:
        print("\nDRY RUN complete - rerun with --apply to execute for real.")


if __name__ == "__main__":
    main()
