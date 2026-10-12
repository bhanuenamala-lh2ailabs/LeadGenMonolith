#!/usr/bin/env python3
"""Archive (HubSpot 'delete' = soft delete, recoverable via POST /crm/v3/objects/deals/batch/restore) the
2,014 deals sitting at the Cluster 2 'LinkedIn sent' stage, id 4307081975, pipeline 2464812771, companyops.

Context (2026-10-11): the LinkedIn-outreach flow is moving to OutFlo-webhook-driven (Connection Accepted ->
LinkedIn connected, First Reply -> Replied), making a separate 'LinkedIn sent' HubSpot stage redundant -
OutFlo is now the system of record for 'sent'. These 2,014 deals all carry the single generic tag
lead_source='Outflo Outreach ( Startups )', which does not map to any current OutFlo campaign by name
(the one similarly-named campaign, Codebase_Distressed_Active_Startups_India, launched 30 Aug - AFTER these
deals' 13 Aug createdate, so it cannot be their real source). Explicit user instruction, 2026-10-11:
"its okay absolutely delete them" - given directly after being told this overrides the project's standing
non-destructive-retirement rule (used everywhere else this session: Coding's old Call Attempted, Cluster 2's
old Retired: Callback stage were both relabelled+closed-lost in place, never deleted).

Dry run by default (reads only). `--apply` performs the archive and writes a full audit JSON to data/audit/
BEFORE the first write. This lives in tools/ on purpose: leadgen/ is read-only against HubSpot by design -
same convention as hubspot_archive_boutique.py."""
import argparse, json, sys, time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
BASE = "https://api.hubapi.com"
PIPELINE = "2464812771"   # Company Ops Cluster 2 India, companyops portal
STAGE_ID = "4307081975"   # LinkedIn sent


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
            raise SystemExit("HubSpot %s %s -> %s %s" % (method, path, r.status_code, r.text[:300]))
        return r.json() if r.text else {}
    raise SystemExit("HubSpot %s %s kept failing" % (method, path))


def search_deals():
    out, after = [], None
    while True:
        body = {"filterGroups": [{"filters": [
            {"propertyName": "pipeline", "operator": "EQ", "value": PIPELINE},
            {"propertyName": "dealstage", "operator": "EQ", "value": STAGE_ID}]}],
            "properties": ["dealname", "dealstage", "pipeline", "lead_source", "hubspot_owner_id", "createdate"], "limit": 100}
        if after:
            body["after"] = after
        r = call("POST", "/crm/v3/objects/deals/search", json=body)
        out += r.get("results", [])
        after = r.get("paging", {}).get("next", {}).get("after")
        if not after:
            break
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    deals = search_deals()
    print(f"{len(deals)} deals found at LinkedIn sent (stage {STAGE_ID})")
    if not a.apply:
        print("DRY RUN - rerun with --apply to actually archive these. No writes made.")
        return

    audit_dir = ROOT / "data" / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    audit_path = audit_dir / f"linkedin_sent_delete_{ts}.json"
    audit_path.write_text(json.dumps({
        "action": "archive (soft delete)", "reason": "LinkedIn-sent stage removed, OutFlo-webhook-driven flow replaces it",
        "instructed_by_user": "2026-10-11, explicit: 'its okay absolutely delete them'",
        "count": len(deals), "deals": deals,
    }, indent=2))
    print("audit written ->", audit_path)

    ids = [d["id"] for d in deals]
    archived = 0
    for i in range(0, len(ids), 100):
        chunk = ids[i:i+100]
        call("POST", "/crm/v3/objects/deals/batch/archive", json={"inputs": [{"id": x} for x in chunk]})
        archived += len(chunk)
        print(f"archived {archived}/{len(ids)}")
    print("DONE.")


if __name__ == "__main__":
    main()
