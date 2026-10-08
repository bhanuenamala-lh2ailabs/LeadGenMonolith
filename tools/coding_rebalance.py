#!/usr/bin/env python3
"""Daily Coding cold-call rebalance: every rep ends at exactly TARGET (100) Cold Call deals.

Reps over target: the excess is removed from HubSpot (archived there, recoverable 90 days) and kept in full in the
local reserve (db table ext_coding_lead_reserve, migration 0022), status='available'.
Reps under target: rows are pulled back out of the reserve (oldest first) and pushed into that rep's HubSpot Cold Call
stage, same portal or cross-portal, status->'reassigned'.

If the reserve doesn't have enough rows to fill everyone under target, those reps are filled as far as the reserve allows
and the shortfall is reported — nothing is invented.

Dry run by default; --apply writes. Meant to be the body of the 9:30 AM job once it's been watched run clean by hand.
"""
import argparse, datetime as dt, json, re, sqlite3, sys, time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "db" / "leadgen.sqlite"
TARGET = 100

env = {}
for line in open(ROOT / ".env", encoding="utf-8"):
    if "=" in line and not line.lstrip().startswith("#"):
        k, v = line.split("=", 1); env[k.strip()] = v.strip().strip('"').strip("'")

def hdr(k): return {"Authorization": "Bearer " + env[k], "Content-Type": "application/json"}

REPS = [
    dict(name="Lamiya",    account="main",       key="HUBSPOT_KEY_MAIN",       pipeline="default",     owner="96574824",  li="linkedin_url",    tag=True),
    dict(name="Yuktha",    account="main",       key="HUBSPOT_KEY_MAIN",       pipeline="default",     owner="96573782",  li="linkedin_url",    tag=True),
    dict(name="Shagufta",  account="main",       key="HUBSPOT_KEY_MAIN",       pipeline="default",     owner="168572330", li="linkedin_url",    tag=True),
    dict(name="Vaishnavi", account="companyops", key="HUBSPOT_KEY_COMPANYOPS", pipeline="2608440044",  owner="168341981", li="linkedin_url",    tag=True),
    dict(name="Harsha",    account="rat",        key="HUBSPOT_KEY_RAT",        pipeline="2608440047",  owner="98906502",  li="hs_linkedin_url", tag=False),
]

def now_iso():
    n = dt.datetime.utcnow()
    return n.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (n.microsecond // 1000)

def cold_stage_id(rep):
    h = hdr(rep["key"])
    st = requests.get(f"https://api.hubapi.com/crm/v3/pipelines/deals/{rep['pipeline']}", headers=h, timeout=30).json()["stages"]
    return next(s["id"] for s in st if s["label"] == "Cold Call")

def cold_deals(rep, stage, limit=None):
    h = hdr(rep["key"])
    ids, after = [], 0
    while True:
        b = {"filterGroups": [{"filters": [{"propertyName": "pipeline", "operator": "EQ", "value": rep["pipeline"]},
             {"propertyName": "dealstage", "operator": "EQ", "value": stage}, {"propertyName": "hubspot_owner_id", "operator": "EQ", "value": rep["owner"]}]}],
             "properties": ["dealname", "lead_source"], "limit": 100, "after": after}
        r = requests.post("https://api.hubapi.com/crm/v3/objects/deals/search", headers=h, json=b, timeout=60).json()
        ids += r.get("results", [])
        if "paging" not in r or (limit and len(ids) >= limit): break
        after = r["paging"]["next"]["after"]
    return ids[:limit] if limit else ids

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--apply", action="store_true"); a = ap.parse_args()
    con = sqlite3.connect(DB)
    counts, stage_ids = {}, {}
    for rep in REPS:
        stage_ids[rep["name"]] = cold_stage_id(rep)
        n = len(cold_deals(rep, stage_ids[rep["name"]]))
        counts[rep["name"]] = n
    print("live Cold Call counts:", counts)

    over = {n: c - TARGET for n, c in counts.items() if c > TARGET}
    under = {n: TARGET - c for n, c in counts.items() if c < TARGET}
    print("over target:", over, "| under target:", under)
    if not a.apply:
        print("DRY RUN: nothing written. Re-run with --apply.")

    # ---- remove the excess into the reserve ----
    removed_total = 0
    for rep in REPS:
        excess = over.get(rep["name"], 0)
        if not excess: continue
        h = hdr(rep["key"])
        deals = cold_deals(rep, stage_ids[rep["name"]], limit=excess)
        print(f"{rep['name']}: removing {len(deals)} excess (target removal {excess})")
        for d in deals:
            assoc = requests.get(f"https://api.hubapi.com/crm/v4/objects/deals/{d['id']}/associations/contacts", headers=h, timeout=30).json().get("results", [])
            cprops = {}
            cid = None
            if assoc:
                cid = assoc[0]["toObjectId"]
                cprops = requests.get(f"https://api.hubapi.com/crm/v3/objects/contacts/{cid}?properties=firstname,lastname,jobtitle,company,phone,mobilephone,email,{rep['li']}",
                                      headers=h, timeout=30).json().get("properties", {})
            row = dict(status="available", source_account=rep["account"], source_pipeline=rep["pipeline"], source_owner_name=rep["name"],
                       source_hs_deal_id=d["id"], source_hs_contact_id=cid, dealname=d["properties"]["dealname"],
                       first_name=cprops.get("firstname"), last_name=cprops.get("lastname"), title=cprops.get("jobtitle"),
                       company=cprops.get("company"), phone_e164=cprops.get("mobilephone") or cprops.get("phone"),
                       email=cprops.get("email"), linkedin_url=cprops.get(rep["li"]), lead_source_label=d["properties"].get("lead_source"),
                       dealstage_label="Cold Call", removed_at=now_iso())
            if a.apply:
                con.execute("""INSERT INTO ext_coding_lead_reserve(status,source_account,source_pipeline,source_owner_name,source_hs_deal_id,
                    source_hs_contact_id,dealname,first_name,last_name,title,company,phone_e164,email,linkedin_url,lead_source_label,
                    dealstage_label,removed_at) VALUES (:status,:source_account,:source_pipeline,:source_owner_name,:source_hs_deal_id,
                    :source_hs_contact_id,:dealname,:first_name,:last_name,:title,:company,:phone_e164,:email,:linkedin_url,:lead_source_label,
                    :dealstage_label,:removed_at)""", row)
                con.commit()
                # Non-destructive: unassign the owner instead of deleting. The deal stays in HubSpot, intact and
                # recoverable at any time; it just stops counting toward anyone's cold-call tally while it's "in reserve".
                requests.patch(f"https://api.hubapi.com/crm/v3/objects/deals/{d['id']}", headers=h,
                               json={"properties": {"hubspot_owner_id": ""}}, timeout=60)
                time.sleep(0.1)
            removed_total += 1
    print("removed into reserve:", removed_total)

    # ---- fill reps under target from the reserve ----
    pushed_total = 0
    for rep in REPS:
        need = under.get(rep["name"], 0)
        if not need: continue
        pool = con.execute("SELECT * FROM v_coding_lead_reserve_available LIMIT ?", (need,)).fetchall()
        cols = [c[0] for c in con.execute("SELECT * FROM v_coding_lead_reserve_available LIMIT 0").description]
        pool = [dict(zip(cols, r)) for r in pool]
        if len(pool) < need:
            print(f"{rep['name']}: needs {need}, reserve only has {len(pool)} available — filling what's there, shortfall {need - len(pool)}")
        else:
            print(f"{rep['name']}: filling {len(pool)} from reserve")
        if not a.apply: continue
        h = hdr(rep["key"])
        for row in pool:
            cprops = dict(firstname=row["first_name"] or "", lastname=row["last_name"] or "", jobtitle=row["title"] or "",
                          company=row["company"] or row["dealname"], hubspot_owner_id=rep["owner"])
            if row["phone_e164"]: cprops["phone"] = cprops["mobilephone"] = row["phone_e164"]
            if row["linkedin_url"]: cprops[rep["li"]] = row["linkedin_url"]
            if row["email"]: cprops["email"] = row["email"]
            c = requests.post("https://api.hubapi.com/crm/v3/objects/contacts", headers=h, json={"properties": cprops}, timeout=60)
            if c.status_code == 409 and "email" in cprops:
                cprops.pop("email"); c = requests.post("https://api.hubapi.com/crm/v3/objects/contacts", headers=h, json={"properties": cprops}, timeout=60)
            if not c.ok: print("  contact fail", row["dealname"], c.text[:150]); continue
            new_cid = c.json()["id"]
            dprops = dict(dealname=row["dealname"], pipeline=rep["pipeline"], dealstage=stage_ids[rep["name"]], hubspot_owner_id=rep["owner"],
                          description=f"From the Coding lead reserve (originally {row['source_owner_name']}, removed {row['removed_at']}).")
            if rep["tag"] and row["lead_source_label"]: dprops["lead_source"] = row["lead_source_label"]
            d = requests.post("https://api.hubapi.com/crm/v3/objects/deals", headers=h, json={"properties": dprops}, timeout=60)
            if not d.ok: print("  deal fail", row["dealname"], d.text[:150]); continue
            new_did = d.json()["id"]
            requests.put(f"https://api.hubapi.com/crm/v4/objects/deal/{new_did}/associations/default/contact/{new_cid}", headers=h, timeout=60)
            con.execute("""UPDATE ext_coding_lead_reserve SET status='reassigned', reassigned_account=?, reassigned_owner_name=?,
                reassigned_hs_deal_id=?, reassigned_hs_contact_id=?, reassigned_at=?, updated_at=? WHERE reserve_id=?""",
                (rep["account"], rep["name"], new_did, new_cid, now_iso(), now_iso(), row["reserve_id"]))
            con.commit()
            pushed_total += 1
            time.sleep(0.1)
    print("pushed from reserve:", pushed_total)
    if a.apply:
        left = con.execute("SELECT count(*) FROM ext_coding_lead_reserve WHERE status='available'").fetchone()[0]
        print("reserve now holds (available):", left)

if __name__ == "__main__":
    main()
