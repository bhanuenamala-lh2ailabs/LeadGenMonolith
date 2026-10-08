#!/usr/bin/env python3
"""Cluster 2 rebalance to Market Research, 7 Oct task:

1. Remove Tanisha's and Amisha's current Cold-called-assigned deals to the local reserve (non-destructive: unassign
   owner, keep the full record in ext_coding_lead_reserve; nothing is deleted from HubSpot, per this session's
   established substitute for a literal delete, which the environment's own safety check refuses).
2. Push the Market Research sheet leads (gated: +91 mobile, LinkedIn, deduped against all 3 portals), split across
   the two reps, tagged segment='Market Research', lead_source='Market Research ( Company Ops )'.
3. Whatever's still short of 100 per rep is filled by recycling the OTHER rep's No pickup deals: pull them, reserve
   them (unassign, non-destructive), and re-create as a fresh Cold called assigned deal for the rep who needs it.
   A rep's own No pickup never becomes their own new Cold called assigned deal — always crossed.

Dry run by default; --apply writes.
"""
import argparse, datetime as dt, json, sqlite3, sys, time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "db" / "leadgen.sqlite"
TARGET = 100
PIPE = "2464812771"
TANISHA, AMISHA = "168015618", "168015679"
NAMES = {TANISHA: "Tanisha", AMISHA: "Amisha"}
MR_TAG, MR_SEGMENT = "Market Research ( Company Ops )", "Market Research"

env = {}
for line in open(ROOT / ".env", encoding="utf-8"):
    if "=" in line and not line.lstrip().startswith("#"):
        k, v = line.split("=", 1); env[k.strip()] = v.strip().strip('"').strip("'")
H = {"Authorization": "Bearer " + env["HUBSPOT_KEY_COMPANYOPS"], "Content-Type": "application/json"}

def now_iso():
    n = dt.datetime.utcnow()
    return n.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (n.microsecond // 1000)

def stage_id(label):
    st = requests.get(f"https://api.hubapi.com/crm/v3/pipelines/deals/{PIPE}", headers=H, timeout=30).json()["stages"]
    return next(s["id"] for s in st if s["label"] == label)

def owned_deals(owner, stage, limit=None):
    ids, after = [], 0
    while True:
        b = {"filterGroups": [{"filters": [{"propertyName": "pipeline", "operator": "EQ", "value": PIPE},
             {"propertyName": "dealstage", "operator": "EQ", "value": stage}, {"propertyName": "hubspot_owner_id", "operator": "EQ", "value": owner}]}],
             "properties": ["dealname", "lead_source", "segment", "hubspot_owner_id"], "limit": 100, "after": after}
        r = requests.post("https://api.hubapi.com/crm/v3/objects/deals/search", headers=H, json=b, timeout=60).json()
        ids += r.get("results", [])
        if "paging" not in r or (limit and len(ids) >= limit): break
        after = r["paging"]["next"]["after"]
    return ids[:limit] if limit else ids

def ensure_option(prop, label):
    cur = requests.get(f"https://api.hubapi.com/crm/v3/properties/deals/{prop}", headers=H, timeout=30).json()
    if any(o["label"] == label for o in cur["options"]): return
    opts = [dict(l) for l in [dict(label=o["label"], value=o["value"], displayOrder=o.get("displayOrder", i), hidden=bool(o.get("hidden"))) for i, o in enumerate(cur["options"])]]
    opts.append(dict(label=label, value=label, displayOrder=len(opts), hidden=False))
    requests.patch(f"https://api.hubapi.com/crm/v3/properties/deals/{prop}", headers=H, json={"options": opts}, timeout=30)

def reserve_and_unassign(con, deal, stage_label, apply_):
    assoc = requests.get(f"https://api.hubapi.com/crm/v4/objects/deals/{deal['id']}/associations/contacts", headers=H, timeout=30).json().get("results", [])
    cid, cprops = None, {}
    if assoc:
        cid = assoc[0]["toObjectId"]
        cprops = requests.get(f"https://api.hubapi.com/crm/v3/objects/contacts/{cid}?properties=firstname,lastname,jobtitle,company,phone,mobilephone,email,linkedin_url",
                              headers=H, timeout=30).json().get("properties", {})
    owner_name = NAMES.get(deal["properties"].get("hubspot_owner_id"), deal["properties"].get("hubspot_owner_id"))
    row = dict(status="available", source_account="companyops", source_pipeline=PIPE, source_owner_name=owner_name,
               source_hs_deal_id=deal["id"], source_hs_contact_id=cid, dealname=deal["properties"]["dealname"],
               first_name=cprops.get("firstname"), last_name=cprops.get("lastname"), title=cprops.get("jobtitle"),
               company=cprops.get("company"), phone_e164=cprops.get("mobilephone") or cprops.get("phone"),
               email=cprops.get("email"), linkedin_url=cprops.get("linkedin_url"), lead_source_label=deal["properties"].get("lead_source"),
               dealstage_label=stage_label, removed_at=now_iso())
    if apply_:
        con.execute("""INSERT INTO ext_coding_lead_reserve(status,source_account,source_pipeline,source_owner_name,source_hs_deal_id,
            source_hs_contact_id,dealname,first_name,last_name,title,company,phone_e164,email,linkedin_url,lead_source_label,
            dealstage_label,removed_at) VALUES (:status,:source_account,:source_pipeline,:source_owner_name,:source_hs_deal_id,
            :source_hs_contact_id,:dealname,:first_name,:last_name,:title,:company,:phone_e164,:email,:linkedin_url,:lead_source_label,
            :dealstage_label,:removed_at)""", row)
        con.commit()
        requests.patch(f"https://api.hubapi.com/crm/v3/objects/deals/{deal['id']}", headers=H, json={"properties": {"hubspot_owner_id": ""}}, timeout=60)
        time.sleep(0.1)
    return row

def push_new(owner, cold, name, phone, linkedin, email, lead_source, segment, note):
    cprops = dict(firstname=name.get("first") or "", lastname=name.get("last") or "", hubspot_owner_id=owner)
    if phone: cprops["phone"] = cprops["mobilephone"] = phone
    if linkedin: cprops["linkedin_url"] = linkedin
    if email: cprops["email"] = email
    c = requests.post("https://api.hubapi.com/crm/v3/objects/contacts", headers=H, json={"properties": cprops}, timeout=60)
    if c.status_code == 409 and "email" in cprops:
        cprops.pop("email"); c = requests.post("https://api.hubapi.com/crm/v3/objects/contacts", headers=H, json={"properties": cprops}, timeout=60)
    if not c.ok: return None, c.text[:150]
    cid = c.json()["id"]
    dprops = dict(dealname=name.get("company") or (name.get("first", "") + " " + name.get("last", "")).strip(), pipeline=PIPE, dealstage=cold,
                  hubspot_owner_id=owner, description=note)
    if lead_source: dprops["lead_source"] = lead_source
    if segment: dprops["segment"] = segment
    d = requests.post("https://api.hubapi.com/crm/v3/objects/deals", headers=H, json={"properties": dprops}, timeout=60)
    if not d.ok: return None, d.text[:150]
    did = d.json()["id"]
    requests.put(f"https://api.hubapi.com/crm/v4/objects/deal/{did}/associations/default/contact/{cid}", headers=H, timeout=60)
    time.sleep(0.1)
    return did, None

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--apply", action="store_true"); a = ap.parse_args()
    con = sqlite3.connect(DB)
    cold = stage_id("Cold called assigned")
    nopickup = stage_id("No pickup")

    cur = {o: len(owned_deals(o, cold)) for o in (TANISHA, AMISHA)}
    print("current Cold called assigned:", {NAMES[o]: n for o, n in cur.items()})

    # 1: remove existing cold-called-assigned deals to the reserve
    for owner in (TANISHA, AMISHA):
        deals = owned_deals(owner, cold)
        print(f"{NAMES[owner]}: removing {len(deals)} current Cold called assigned to reserve")
        for d in deals:
            reserve_and_unassign(con, d, "Cold called assigned", a.apply)

    # 2: push Market Research leads, split across the two reps
    mr = json.load(open("/private/tmp/claude-501/-Users-bhanu-Desktop-LeadGenMonolith/651175ba-81a3-46b1-9ea9-d17bb79d12ea/scratchpad/market_research_pushable.json"))
    print("Market Research pushable:", len(mr))
    if a.apply:
        ensure_option("lead_source", MR_TAG)
    split = {TANISHA: mr[0::2], AMISHA: mr[1::2]}
    pushed = {TANISHA: 0, AMISHA: 0}
    for owner, rows in split.items():
        for r in rows:
            raw = r["raw"]
            name = dict(first=raw.get("POC name", "").split()[0] if raw.get("POC name") else "", last=" ".join(raw.get("POC name", "").split()[1:]), company=raw["Company"])
            if a.apply:
                did, err = push_new(owner, cold, name, r["phone"], r["linkedin"], r.get("email"), MR_TAG, MR_SEGMENT,
                                    "Market Research sheet (MarketResearch_C2), pushed 8 Oct 2026.")
                if err: print("  push fail", raw["Company"], err); continue
            pushed[owner] += 1
    print("pushed Market Research:", {NAMES[o]: n for o, n in pushed.items()})

    # 3: fill the remaining gap by recycling the OTHER rep's No pickup deals, crossed
    gap = {o: TARGET - pushed[o] for o in (TANISHA, AMISHA)}
    print("remaining gap after Market Research:", {NAMES[o]: n for o, n in gap.items()})
    other = {TANISHA: AMISHA, AMISHA: TANISHA}
    for needer, n in gap.items():
        if n <= 0: continue
        donor = other[needer]
        pool = owned_deals(donor, nopickup, limit=n)
        print(f"{NAMES[needer]}: filling {len(pool)} of {n} from {NAMES[donor]}'s No pickup pool (crossed, never same rep)")
        for d in pool:
            row = reserve_and_unassign(con, d, "No pickup", a.apply)
            if not a.apply: continue
            name = dict(first=row["first_name"] or "", last=row["last_name"] or "", company=row["company"] or row["dealname"])
            did, err = push_new(needer, cold, name, row["phone_e164"], row["linkedin_url"], row["email"], row["lead_source_label"], None,
                                f"Recycled from {NAMES[donor]}'s No pickup pool (8 Oct 2026 Cluster 2 rebalance); never returned to the same rep.")
            if err: print("  recycle push fail", row["dealname"], err); continue
            con.execute("""UPDATE ext_coding_lead_reserve SET status='reassigned', reassigned_account='companyops', reassigned_owner_name=?,
                reassigned_hs_deal_id=?, reassigned_at=?, updated_at=? WHERE source_hs_deal_id=?""",
                (NAMES[needer], did, now_iso(), now_iso(), d["id"]))
            con.commit()
        if len(pool) < n:
            print(f"  shortfall: {NAMES[needer]} still {n - len(pool)} short, {NAMES[donor]}'s No pickup pool didn't have enough")

    if a.apply:
        final = {o: len(owned_deals(o, cold)) for o in (TANISHA, AMISHA)}
        print("FINAL Cold called assigned:", {NAMES[o]: n for o, n in final.items()})
    else:
        print("DRY RUN: nothing written. Re-run with --apply.")

if __name__ == "__main__":
    main()
