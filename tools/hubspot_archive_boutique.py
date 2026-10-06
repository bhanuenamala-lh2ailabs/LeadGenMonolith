#!/usr/bin/env python3
"""Archive the Coding-pipeline deals at 'Cold Call' whose lead_source is 'Boutique Consulting ( Cat 1 )',
plus their contacts, but ONLY contacts whose every associated deal is in that archive set.

Dry run by default (reads only). `--apply` performs the archive (HubSpot 'archive' = soft delete, recoverable for
deals via POST /crm/v3/objects/deals/batch/restore) and writes an audit JSON to data/audit/ BEFORE the first write.
This lives in tools/ on purpose: the leadgen/ package is read-only against HubSpot by design (docs/ARCHITECTURE.md)."""
import argparse, json, sys, time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
BASE = "https://api.hubapi.com"
PIPELINE = "default"                       # Coding, portal 246754894
STAGE_LABEL = "Cold Call"
LEAD_SOURCE = "Boutique Consulting ( Cat 1 )"


def key():
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith("HUBSPOT_KEY_MAIN="):
            return line.split("=", 1)[1].strip()
    sys.exit("HUBSPOT_KEY_MAIN missing")


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


def search_deals(stage_id):
    out, after = [], None
    while True:
        body = {"filterGroups": [{"filters": [
            {"propertyName": "pipeline", "operator": "EQ", "value": PIPELINE},
            {"propertyName": "dealstage", "operator": "EQ", "value": stage_id},
            {"propertyName": "lead_source", "operator": "EQ", "value": LEAD_SOURCE}]}],
            "properties": ["dealname", "dealstage", "pipeline", "lead_source", "hubspot_owner_id", "createdate"], "limit": 100}
        if after: body["after"] = after
        j = call("POST", "/crm/v3/objects/deals/search", json=body)
        out += j["results"]; after = j.get("paging", {}).get("next", {}).get("after")
        if not after: return out


def assoc(frm, to, ids):
    """frm->to association ids for each id (v4 batch read), returns {id: [to_ids]}"""
    res = {}
    for i in range(0, len(ids), 100):
        j = call("POST", "/crm/v4/associations/%s/%s/batch/read" % (frm, to), json={"inputs": [{"id": x} for x in ids[i:i + 100]]})
        for row in j.get("results", []):
            res[row["from"]["id"]] = [t["toObjectId"] if "toObjectId" in t else t["id"] for t in row.get("to", [])]
    return res


def batch_archive(obj, ids):
    n = 0
    for i in range(0, len(ids), 100):
        call("POST", "/crm/v3/objects/%s/batch/archive" % obj, json={"inputs": [{"id": x} for x in ids[i:i + 100]]})
        n += len(ids[i:i + 100])
    return n


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--apply", action="store_true"); ap.add_argument("--expect-deals", type=int, default=42)
    a = ap.parse_args()
    stages = call("GET", "/crm/v3/pipelines/deals/%s" % PIPELINE)["stages"]
    sid = [s["id"] for s in stages if s["label"] == STAGE_LABEL][0]
    deals = search_deals(sid)
    deal_ids = [d["id"] for d in deals]
    print("deals matching (pipeline Coding, stage %s, lead_source %s): %d" % (STAGE_LABEL, LEAD_SOURCE, len(deal_ids)))

    d2c = assoc("deals", "contacts", deal_ids)
    contact_ids = sorted({c for v in d2c.values() for c in v})
    c2d = assoc("contacts", "deals", contact_ids) if contact_ids else {}
    deal_set = set(deal_ids)
    safe = [c for c in contact_ids if set(c2d.get(c, [])) <= deal_set]
    shared = [c for c in contact_ids if c not in safe]
    print("contacts on those deals: %d | archive-safe (all their deals are in the set): %d | shared with other deals (will be kept): %d"
          % (len(contact_ids), len(safe), len(shared)))

    audit = {"at": datetime.now(timezone.utc).isoformat(), "portal": "main/246754894", "pipeline": PIPELINE, "stage": STAGE_LABEL,
             "lead_source": LEAD_SOURCE, "deals": [{"id": d["id"], "name": d["properties"].get("dealname"), "owner": d["properties"].get("hubspot_owner_id"),
             "created": d["properties"].get("createdate")} for d in deals], "contacts_archived": safe, "contacts_kept_shared": shared,
             "restore_hint": "POST /crm/v3/objects/deals/batch/restore with the deal ids"}
    if len(deal_ids) != a.expect_deals:
        sys.exit("expected %d deals but found %d: refusing to continue" % (a.expect_deals, len(deal_ids)))
    if not a.apply:
        print("DRY RUN: nothing written. Re-run with --apply to archive."); return
    out = ROOT / "data" / "audit"; out.mkdir(parents=True, exist_ok=True)
    f = out / ("boutique_archive_%s.json" % datetime.now().strftime("%Y%m%dT%H%M%S"))
    f.write_text(json.dumps(audit, indent=1)); f.chmod(0o600)
    print("audit written:", f)
    print("archived deals:", batch_archive("deals", deal_ids))
    print("archived contacts:", batch_archive("contacts", safe))


if __name__ == "__main__":
    main()
