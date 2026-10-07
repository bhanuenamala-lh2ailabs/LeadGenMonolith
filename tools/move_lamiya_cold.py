#!/usr/bin/env python3
"""Copy Lamiya's Cold Call deals (MAIN Coding) into a target rep's Coding pipeline, then delete the originals from Lamiya.

    --target companyops  -> Vaishnavi Kannan, CompanyOps Coding pipeline 2608440044
    --target rat         -> Harsha A, RAT Coding pipeline 2608440047

Only deals whose contact has a +91 mobile and a LinkedIn URL are copied. A deal is skipped if the target portal already has
a live deal with the same name or a contact with the same phone (dedup). Originals are deleted only after the copy count matches.
Dry run by default; --apply writes.
"""
import argparse, json, re, sys, time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
env = {}
for line in open(ROOT / ".env", encoding="utf-8"):
    if "=" in line and not line.lstrip().startswith("#"):
        k, v = line.split("=", 1); env[k.strip()] = v.strip().strip('"').strip("'")

def hdr(k): return {"Authorization": "Bearer " + env[k], "Content-Type": "application/json"}
MAIN, CO, RAT = hdr("HUBSPOT_KEY_MAIN"), hdr("HUBSPOT_KEY_COMPANYOPS"), hdr("HUBSPOT_KEY_RAT")
LAMIYA = "96574824"
TARGETS = {
    "companyops": dict(h=CO, owner="168341981", pipeline="2608440044", li="linkedin_url", tag=True, name="Vaishnavi"),
    "rat": dict(h=RAT, owner="98906502", pipeline="2608440047", li="hs_linkedin_url", tag=False, name="Harsha"),
}

def search(h, obj, flt, props, limit=100):
    b = {"filterGroups": [{"filters": flt}], "properties": props, "limit": limit}
    return requests.post(f"https://api.hubapi.com/crm/v3/objects/{obj}/search", headers=h, json=b, timeout=60).json()

def stage_id(h, pid, label):
    st = requests.get(f"https://api.hubapi.com/crm/v3/pipelines/deals/{pid}", headers=h, timeout=30).json()["stages"]
    return next(s["id"] for s in st if s["label"].lower() == label.lower())

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--target", choices=TARGETS, required=True)
    ap.add_argument("--need", type=int, required=True); ap.add_argument("--apply", action="store_true"); a = ap.parse_args()
    T = TARGETS[a.target]
    cold_main = stage_id(MAIN, "default", "Cold Call")
    cold_tgt = stage_id(T["h"], T["pipeline"], "Cold Call")
    # 1 Lamiya's cold-call deals
    src, after = [], 0
    while True:
        r = search(MAIN, "deals", [{"propertyName": "pipeline", "operator": "EQ", "value": "default"},
                   {"propertyName": "dealstage", "operator": "EQ", "value": cold_main}, {"propertyName": "hubspot_owner_id", "operator": "EQ", "value": LAMIYA}],
                   ["dealname", "lead_source"], limit=100) if False else None
        b = {"filterGroups": [{"filters": [{"propertyName": "pipeline", "operator": "EQ", "value": "default"},
             {"propertyName": "dealstage", "operator": "EQ", "value": cold_main}, {"propertyName": "hubspot_owner_id", "operator": "EQ", "value": LAMIYA}]}],
             "properties": ["dealname", "lead_source"], "limit": 100, "after": after}
        r = requests.post("https://api.hubapi.com/crm/v3/objects/deals/search", headers=MAIN, json=b, timeout=60).json()
        src += r.get("results", [])
        if "paging" not in r: break
        after = r["paging"]["next"]["after"]
    print("Lamiya cold-call deals in MAIN:", len(src), flush=True)
    picks = []
    for d in src:
        a_ = requests.get(f"https://api.hubapi.com/crm/v4/objects/deals/{d['id']}/associations/contacts", headers=MAIN, timeout=30).json().get("results", [])
        if not a_: continue
        cid = a_[0]["toObjectId"]
        c = requests.get(f"https://api.hubapi.com/crm/v3/objects/contacts/{cid}?properties=firstname,lastname,jobtitle,phone,mobilephone,email,linkedin_url,company",
                         headers=MAIN, timeout=30).json()
        p = c.get("properties", {})
        ph = (p.get("mobilephone") or p.get("phone") or "").strip()
        e164 = ph if re.fullmatch(r"\+91\d{10}", ph) else None
        li = (p.get("linkedin_url") or "").strip()
        if not e164 or not li: continue
        name = d["properties"]["dealname"]
        if search(T["h"], "deals", [{"propertyName": "dealname", "operator": "EQ", "value": name}], ["dealname"], 1).get("total"): continue
        if search(T["h"], "contacts", [{"propertyName": "phone", "operator": "EQ", "value": e164}], ["phone"], 1).get("total"): continue
        picks.append(dict(source=d["id"], name=name, lead_source=d["properties"].get("lead_source"), contact=p, phone=e164, linkedin=li))
        if len(picks) == a.need: break
    print(f"copyable for {T['name']} (+91 mobile, LinkedIn, not already in target): {len(picks)} of {a.need} needed", flush=True)
    if not a.apply:
        print("DRY RUN: nothing written. Re-run with --apply."); return
    if len(picks) < a.need: raise SystemExit("not enough copyable deals; nothing written")
    created = []
    for x in picks:
        p = x["contact"]
        cprops = dict(firstname=p.get("firstname") or "", lastname=p.get("lastname") or "", jobtitle=p.get("jobtitle") or "", phone=x["phone"],
                      mobilephone=x["phone"], company=p.get("company") or x["name"], hubspot_owner_id=T["owner"])
        cprops[T["li"]] = x["linkedin"]
        if p.get("email"): cprops["email"] = p["email"]
        c = requests.post("https://api.hubapi.com/crm/v3/objects/contacts", headers=T["h"], json={"properties": cprops}, timeout=60)
        if c.status_code == 409 and "email" in cprops:
            cprops.pop("email"); c = requests.post("https://api.hubapi.com/crm/v3/objects/contacts", headers=T["h"], json={"properties": cprops}, timeout=60)
        if not c.ok: print("contact fail", x["name"], c.text[:120]); continue
        cid = c.json()["id"]
        dprops = dict(dealname=x["name"], pipeline=T["pipeline"], dealstage=cold_tgt, hubspot_owner_id=T["owner"],
                      description="Copied from Lamiya (MAIN Coding, Cold Call) to %s on 7 Oct 2026 (cold-call top-up)." % T["name"])
        if T["tag"] and x["lead_source"]: dprops["lead_source"] = x["lead_source"]
        d = requests.post("https://api.hubapi.com/crm/v3/objects/deals", headers=T["h"], json={"properties": dprops}, timeout=60)
        if not d.ok: print("deal fail", x["name"], d.text[:120]); continue
        did = d.json()["id"]
        requests.put(f"https://api.hubapi.com/crm/v4/objects/deal/{did}/associations/default/contact/{cid}", headers=T["h"], timeout=60)
        created.append(dict(source_deal=x["source"], new_deal=did, name=x["name"])); time.sleep(0.15)
    (ROOT / "data/audit" / f"lamiya_to_{a.target}_2026_10_07.json").write_text(json.dumps(created, indent=1))
    print("copied:", len(created), flush=True)
    if len(created) != a.need: raise SystemExit("copy count mismatch: Lamiya's originals NOT deleted")
    for c in created:
        requests.delete(f"https://api.hubapi.com/crm/v3/objects/deals/{c['source_deal']}", headers=MAIN, timeout=60)
    print("deleted from Lamiya in MAIN:", len(created))
    v = search(T["h"], "deals", [{"propertyName": "pipeline", "operator": "EQ", "value": T["pipeline"]},
              {"propertyName": "hubspot_owner_id", "operator": "EQ", "value": T["owner"]}, {"propertyName": "dealstage", "operator": "EQ", "value": cold_tgt}],
              ["dealname"], 1)
    print(f"{T['name']} cold call now:", v.get("total"))

if __name__ == "__main__":
    main()
