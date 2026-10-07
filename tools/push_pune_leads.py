#!/usr/bin/env python3
"""Push the Pune Enterprise SaaS IT leads (Pune_EnterpriseSaaS_IT_leads.csv) into Company Ops Cluster 2 at 'Cold called assigned'.

Owners (COMPANYOPS): Amisha Pujari 100 leads, Tanisha Sharma 28 leads, so each reaches 100 cold-called deals.
Tags: lead_source = 'IT Services ( Pune )', segment = 'Enterprise SaaS/IT'.
Each lead = one Deal (named after the COMPANY) + one Contact + the deal<->contact association.

STANDING RULES (user, 2026-10-05): dedup against ALL THREE portals (leadgen.dedup, local index + live); callable +91 number only;
LinkedIn URL always; no number or LinkedIn, no push. Dry run by default; --apply writes.
"""
import argparse, csv, json, re, sqlite3, sys, time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from leadgen import dedup, norm  # noqa: E402

CSV = ROOT / "Pune_EnterpriseSaaS_IT_leads.csv"
OUTDIR = ROOT / "data" / "audit"
BASE = "https://api.hubapi.com"
PIPELINE, STAGE = "2464812771", "4275264191"          # Company Ops Cluster 2 / Cold called assigned
TAG = "IT Services ( Pune )"
SEGMENT = "Enterprise SaaS/IT"
OWNERS = [("Amisha Pujari", "168015679", 41)]  # remaining 41 after the 28 Tanisha leads were pushed (7 Oct)

env = {}
for line in open(ROOT / ".env", encoding="utf-8"):
    if "=" in line and not line.lstrip().startswith("#"):
        k, v = line.split("=", 1); env[k.strip()] = v.strip().strip('"').strip("'")
_sess = requests.Session()
_sess.headers.update({"Authorization": "Bearer " + env["HUBSPOT_KEY_COMPANYOPS"], "Content-Type": "application/json"})


def call(method, path, ok=(200, 201, 204, 207), **kw):
    for attempt in range(7):
        r = _sess.request(method, BASE + path, timeout=60, **kw)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2 * (attempt + 1)); continue
        if r.status_code in ok:
            return r.json() if r.text else {}
        raise SystemExit("%s %s -> %s %s" % (method, path, r.status_code, r.text[:400]))
    raise SystemExit("%s %s kept failing" % (method, path))


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--apply", action="store_true"); a = ap.parse_args()
    rows = list(csv.DictReader(open(CSV, encoding="utf-8-sig")))
    held, ok = [], []
    for r in rows:
        ph = (r["phone"] or "").strip()
        e164 = ph if re.fullmatch(r"\+91\d{10}", ph) else None
        if not (e164 and r["listLinkedinUrl"].strip() and r["firstName"].strip() and r["company"].strip()):
            held.append((r["company"], "gate: needs +91 number, LinkedIn URL, name and company")); continue
        r["_e164"] = e164; ok.append(r)
    con = sqlite3.connect("file:%s?mode=ro" % (ROOT / "db/leadgen.sqlite"), uri=True); con.row_factory = sqlite3.Row
    leads = [dict(company=r["company"], domain="", email=(r["email"] or "").strip() or None, phone=r["_e164"], linkedin=r["listLinkedinUrl"].strip()) for r in ok]
    hits = dedup.check(leads, con=con, live=True)
    keep, seen = [], set()
    for r, lead in zip(ok, leads):
        h = hits.get(dedup.lead_key(dict(domain="", company=r["company"])))
        if h: held.append((r["company"], "already in HubSpot: " + dedup.explain(h))); continue
        key = r["company"].strip().lower()
        if key in seen: held.append((r["company"], "duplicate company inside the file")); continue
        seen.add(key); keep.append(r)
    need = sum(n for _, _, n in OWNERS)
    if len(keep) < need: raise SystemExit("only %d pushable leads, need %d" % (len(keep), need))
    plan, i = [], 0
    for name, oid, n in OWNERS:
        for r in keep[i:i + n]:
            plan.append(dict(row=r, owner=name, owner_id=oid))
        i += n
    print("input rows %d | held out %d | pushable after dedup %d | to push %d" % (len(rows), len(held), len(keep), len(plan)))
    for c, n in Counter(re.split(r":| \(", reason)[0] for _, reason in held).items(): print("   held out - %s: %d" % (c, n))
    for name, _, n in OWNERS: print("   %-16s %d" % (name, sum(1 for x in plan if x["owner"] == name)))
    if not a.apply:
        print("\nDRY RUN: nothing written. Re-run with --apply."); return

    OUTDIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    audit = OUTDIR / ("pune_push_%s.json" % stamp)
    audit.write_text(json.dumps(dict(at=datetime.now(timezone.utc).isoformat(), held_out=held,
                                     plan=[dict(company=x["row"]["company"], owner=x["owner"], phone=x["row"]["_e164"], linkedin=x["row"]["listLinkedinUrl"]) for x in plan]), indent=1))
    audit.chmod(0o600); print("audit:", audit)
    created = []
    for n, x in enumerate(plan, 1):
        r = x["row"]
        props = dict(firstname=r["firstName"].strip(), lastname=r["lastName"].strip(), jobtitle=r["title"].strip(), phone=r["_e164"],
                     mobilephone=r["_e164"], company=r["company"].strip(), linkedin_url=r["listLinkedinUrl"].strip(), hubspot_owner_id=x["owner_id"])
        if (r["email"] or "").strip(): props["email"] = r["email"].strip()
        res = call("POST", "/crm/v3/objects/contacts", json={"properties": props}, ok=(200, 201, 409))
        if "id" not in res and "email" in props:
            props.pop("email"); res = call("POST", "/crm/v3/objects/contacts", json={"properties": props})
        contact = res["id"]
        dprops = dict(dealname=r["company"].strip(), pipeline=PIPELINE, dealstage=STAGE, hubspot_owner_id=x["owner_id"],
                      lead_source=TAG, segment=SEGMENT, description="Pune Enterprise SaaS IT lead. Source file: Pune_EnterpriseSaaS_IT_leads.csv")
        deal = call("POST", "/crm/v3/objects/deals", json={"properties": dprops})["id"]
        call("PUT", "/crm/v4/objects/deal/%s/associations/default/contact/%s" % (deal, contact))
        created.append(dict(deal=deal, contact=contact, owner=x["owner"]))
        if n % 20 == 0 or n == len(plan): print("  %d/%d created" % (n, len(plan)), flush=True)
        time.sleep(0.15)
    (OUTDIR / ("pune_push_created_%s.json" % stamp)).write_text(json.dumps(created, indent=1))
    print("created:", len(created))


if __name__ == "__main__":
    main()
