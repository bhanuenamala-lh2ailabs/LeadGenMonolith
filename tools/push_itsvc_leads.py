#!/usr/bin/env python3
"""Push the itsvc-tam POC list (legacy/hubspot/itsvc-tam/out/poc_callable_250.csv) into HubSpot at the entry/cold-call stage, split
equally between five callers across three portals:

    MAIN       Coding pipeline          stage Cold Call            Yuktha Anand, Lamiya Saleem, Shagufta Khan
    RAT        Rapid Action Team        stage Cold Called Assigned Harsha A
    COMPANYOPS Company Ops Cluster 2    stage Cold called assigned Vaishnavi Kannan

Each lead = one Deal (named after the COMPANY) + one Contact (title, LinkedIn, mobile, email if found) + the deal<->contact association.
Tag = 'IT Services ( <City> )' in the portal's lead dropdown (MAIN lead_source, RAT lead_category, COMPANYOPS lead_source + segment).

STANDING RULES enforced here (user, 2026-10-05):
  1. every lead is deduplicated against ALL THREE portals (leadgen.dedup: local index + live confirmation); any hit blocks the lead;
  2. only callable numbers (+91 / +44 / +61 / +65) and a LinkedIn URL are pushed; whale/suppression list is never pushed;
  3. the POC's email is pushed when one exists; for leads without one we try to find the POC's official email from the company's own site
     (leadgen.emailfind, free, robots-aware, ONLY the POC's own address, never a generic info@); otherwise the lead goes without an email.
Dry run by default.  --apply: adds missing dropdown options, writes an audit JSON, creates records idempotently (progress file), verifies.
Lives in tools/ on purpose: leadgen/ stays read-only against HubSpot."""
import argparse, csv, json, re, sqlite3, sys, time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from leadgen import dedup, emailfind, norm, suppression  # noqa: E402

CSV = ROOT / "legacy/hubspot/itsvc-tam/out/poc_callable_250.csv"
OUTDIR = ROOT / "data" / "audit"
BASE = "https://api.hubapi.com"
CALLABLE = ("+91", "+44", "+61", "+65")
# reviewer-flagged (training institutes, agencies, not software, possible BPO); matched case-insensitively as substrings
EXCLUDE_SUBSTR = ["adcc academy", "technokraft training", "fireblaze", "pesto tech", "analytixlabs", "weboin", "serp wizard",
                  "petroleum engineers associat", "nsut incubation", "namo padmavati"]

PORTALS = {
    "main": dict(key="HUBSPOT_KEY_MAIN", pipeline="default", stage="3992480462", tag_prop="lead_source", li_prop="linkedin_url"),
    "rat": dict(key="HUBSPOT_KEY_RAT", pipeline="2575252183", stage="4332503773", tag_prop="lead_category", li_prop="hs_linkedin_url"),
    "companyops": dict(key="HUBSPOT_KEY_COMPANYOPS", pipeline="2464812771", stage="4275264191", tag_prop="lead_source", li_prop="linkedin_url",
                       extra_deal_props={"segment": "IT Services"}),
}
OWNERS = [("Yuktha Anand", "main", "96573782"), ("Lamiya Saleem", "main", "96574824"), ("Shagufta Khan", "main", "168572330"),
          ("Harsha A", "rat", "98906502"), ("Vaishnavi Kannan", "companyops", "168341981")]
CITY_MAP = {"Bengaluru": "Bangalore"}

env = dict(l.strip().split("=", 1) for l in (ROOT / ".env").read_text().splitlines() if "=" in l and not l.startswith("#"))
_sessions = {}


def sess(p):
    if p not in _sessions:
        s = requests.Session(); s.headers.update({"Authorization": "Bearer " + env[PORTALS[p]["key"]], "Content-Type": "application/json"})
        _sessions[p] = s
    return _sessions[p]


def call(p, method, path, ok=(200, 201, 204, 207), **kw):
    tolerate = kw.pop("_tolerate", ())
    for attempt in range(7):
        r = sess(p).request(method, BASE + path, timeout=60, **kw)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2 * (attempt + 1)); continue
        if r.status_code in ok or r.status_code in tolerate:
            return r.json() if r.text else {}
        raise SystemExit("[%s] %s %s -> %s %s" % (p, method, path, r.status_code, r.text[:400]))
    raise SystemExit("[%s] %s %s kept failing" % (p, method, path))


def tag(city):
    return "IT Services ( %s )" % CITY_MAP.get(city.strip(), city.strip())


def load_rows():
    rows = []
    for r in csv.DictReader(open(CSV)):
        r = {k: (v or "").strip() for k, v in r.items()}
        r["_domain"] = norm.norm_domain(r["Domain"]) or r["Domain"].lower()
        rows.append(r)
    return rows


def ensure_options(p, prop, labels, apply):
    cur = call(p, "GET", "/crm/v3/properties/deals/%s" % prop)
    have = {o["label"]: o for o in cur["options"]}
    missing = sorted(l for l in set(labels) if l not in have)
    if apply and missing:
        opts = [dict(label=o["label"], value=o["value"], displayOrder=o.get("displayOrder", i), hidden=bool(o.get("hidden"))) for i, o in enumerate(cur["options"])]
        n = len(opts)
        for k, l in enumerate(missing):
            opts.append(dict(label=l, value=l, displayOrder=n + k, hidden=False))
        call(p, "PATCH", "/crm/v3/properties/deals/%s" % prop, json={"options": opts})
        new = call(p, "GET", "/crm/v3/properties/deals/%s" % prop)["options"]
        before = [(o["label"], o["value"]) for o in cur["options"]]
        assert set(before) <= {(o["label"], o["value"]) for o in new}, "existing options changed!"
        assert all(l in {o["label"] for o in new} for l in missing), "new options not visible"
    return {l: (have[l]["value"] if l in have else l) for l in set(labels)}, missing


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--apply", action="store_true"); ap.add_argument("--website-emails", action="store_true"); a = ap.parse_args()
    rows = load_rows(); total_in = len(rows); held = []
    # 1 hard gates: callable number, LinkedIn URL, names, domain
    ok = []
    for r in rows:
        e164 = norm.norm_phone_e164(r["Mobile"]) or ""
        if not (r["_domain"] and r["LinkedIn URL"] and r["First Name"] and r["Company"] and e164.startswith(CALLABLE)):
            held.append((r["Company"], "gate: needs callable number (%s), LinkedIn URL, name, domain" % "/".join(CALLABLE))); continue
        r["_phone"] = e164; ok.append(r)
    rows = ok
    # 2 reviewer-flagged
    keep = []
    for r in rows:
        if any(s in r["Company"].lower() for s in EXCLUDE_SUBSTR): held.append((r["Company"], "reviewer-flagged (training institute / agency / not software / possible BPO)"))
        else: keep.append(r)
    rows = keep
    # 3 suppression list (whale etc.)
    con = sqlite3.connect("file:%s?mode=ro" % (ROOT / "db/leadgen.sqlite"), uri=True); con.row_factory = sqlite3.Row
    keep = []
    for r in rows:
        try:
            hit = suppression.check(con, domain=r["_domain"], company_name=r["Company"], email=r["Email"] or None, phone=r["_phone"], linkedin_person=r["LinkedIn URL"])
        except Exception as e:
            held.append((r["Company"], "suppression check failed closed: %s" % e)); continue
        if hit: held.append((r["Company"], "suppression list (never push)")); continue
        keep.append(r)
    rows = keep
    # 4 dedup against ALL THREE portals (local index + live)
    t0 = time.time()
    leads = [dict(company=r["Company"], domain=r["_domain"], email=r["Email"], phone=r["_phone"], linkedin=r["LinkedIn URL"]) for r in rows]
    hits = dedup.check(leads, con=con, live=True)
    print("dedup (3 portals, local+live) took %.0fs" % (time.time() - t0))
    keep, seen = [], set()
    for r in rows:
        h = hits.get(dedup.lead_key(dict(domain=r["_domain"], company=r["Company"])))
        if h: held.append((r["Company"], "already in HubSpot: " + dedup.explain(h))); continue
        if r["_domain"] in seen: held.append((r["Company"], "duplicate domain inside the file")); continue
        seen.add(r["_domain"]); keep.append(r)
    rows = sorted(keep, key=lambda r: (r["City"], r["Company"].lower()))
    # 5 POC work email: from data/audit/itsvc_emails.json (filled by tools/poc_email_apollo.py); website crawl only with --website-emails
    emails = {}
    cache = OUTDIR / "itsvc_emails.json"
    if cache.exists(): emails = json.loads(cache.read_text())
    if a.website_emails:
        need = [r for r in rows if not r["Email"] and r["_domain"] not in emails]
        if need:
            res = emailfind.find_many([dict(domain=r["_domain"], first_name=r["First Name"], last_name=r["Last Name"]) for r in need])
            for d, v in res.items():
                if v.get("poc_email"): emails[d] = {"poc_email": v["poc_email"], "source": "website"}
    for r in rows:
        if not r["Email"] and emails.get(r["_domain"], {}).get("poc_email"): r["Email"] = emails[r["_domain"]]["poc_email"]
    # 6 assignment (round robin over city/company order -> similar city mix per caller)
    plan = []
    for i, r in enumerate(rows):
        name, portal, oid = OWNERS[i % len(OWNERS)]
        plan.append(dict(row=r, owner=name, portal=portal, owner_id=oid, tag=tag(r["City"])))
    OUTDIR.mkdir(parents=True, exist_ok=True)
    (OUTDIR / "itsvc_plan.json").write_text(json.dumps([dict(company=x["row"]["Company"], domain=x["row"]["_domain"], first=x["row"]["First Name"], last=x["row"]["Last Name"],
                                                         linkedin=x["row"]["LinkedIn URL"], city=x["row"]["City"], owner=x["owner"], portal=x["portal"]) for x in plan], indent=1))
    print("\ninput rows: %d | held out: %d | to push: %d | with an email: %d" % (total_in, len(held), len(plan), sum(1 for x in plan if x["row"]["Email"])))
    for c, n in Counter(re.split(r":| \(", reason)[0] for _, reason in held).items(): print("   held out - %s: %d" % (c, n))
    for company, reason in held: print("     -", company, "|", reason[:170])
    print("\nper caller:")
    for name, portal, _ in OWNERS: print("   %-18s %-10s %d" % (name, portal, sum(1 for x in plan if x["owner"] == name)))
    need_opts = {}
    for p in PORTALS:
        labels = {x["tag"] for x in plan if x["portal"] == p}
        if labels:
            vals, missing = ensure_options(p, PORTALS[p]["tag_prop"], labels, False); need_opts[p] = (vals, missing)
            print("[%s] %s: %d tag values, %d dropdown options to add" % (p, PORTALS[p]["tag_prop"], len(labels), len(missing)))
    if not a.apply:
        print("\nDRY RUN: nothing written. Re-run with --apply."); return

    # ---- APPLY ----
    OUTDIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    audit = OUTDIR / ("itsvc_push_%s.json" % stamp)
    audit.write_text(json.dumps(dict(at=datetime.now(timezone.utc).isoformat(), held_out=held,
                                     plan=[dict(company=x["row"]["Company"], domain=x["row"]["_domain"], city=x["row"]["City"], owner=x["owner"], portal=x["portal"],
                                                tag=x["tag"], email=x["row"]["Email"] or None) for x in plan]), indent=1))
    audit.chmod(0o600); print("audit:", audit)
    for p in PORTALS:
        labels = {x["tag"] for x in plan if x["portal"] == p}
        if labels: need_opts[p] = ensure_options(p, PORTALS[p]["tag_prop"], labels, True); print("[%s] dropdown options now present (added %d)" % (p, len(need_opts[p][1])))
    seg = call("companyops", "GET", "/crm/v3/properties/deals/segment")
    if not any(o["label"] == "IT Services" for o in seg["options"]):
        opts = [dict(label=o["label"], value=o["value"], displayOrder=o.get("displayOrder", i), hidden=bool(o.get("hidden"))) for i, o in enumerate(seg["options"])]
        opts.append(dict(label="IT Services", value="IT Services", displayOrder=len(opts), hidden=False))
        call("companyops", "PATCH", "/crm/v3/properties/deals/segment", json={"options": opts}); print("[companyops] segment option 'IT Services' added")
    prog_file = OUTDIR / "itsvc_push_progress.json"
    prog = json.loads(prog_file.read_text()) if prog_file.exists() else {}
    for n, x in enumerate(plan, 1):
        r, p = x["row"], x["portal"]; cfg = PORTALS[p]; k = "%s|%s" % (p, r["_domain"]); st = prog.setdefault(k, {})
        if "contact" not in st:
            props = dict(firstname=r["First Name"], lastname=r["Last Name"], jobtitle=r["Title"], phone=r["_phone"], mobilephone=r["_phone"],
                         company=r["Company"], website=r["_domain"], hubspot_owner_id=x["owner_id"])
            props[cfg["li_prop"]] = r["LinkedIn URL"]
            if r["Email"]: props["email"] = r["Email"]
            res = call(p, "POST", "/crm/v3/objects/contacts", json={"properties": props}, _tolerate=(409,))
            if "id" not in res and props.get("email"):          # email already on another (deal-less) contact: push the lead without it
                st["email_conflict"] = props.pop("email")
                res = call(p, "POST", "/crm/v3/objects/contacts", json={"properties": props})
            st["contact"] = res["id"]
        if "deal" not in st:
            desc = "IT services company (itsvc-tam, Apollo org search + POC reveal). City: %s. Headcount band: %s. Founded: %s. Domain: %s. Tier: %s. Evidence: %s." % (
                r["City"], r["Headcount Band"], r["Founded"], r["_domain"], r["Codebase Tier"], r["Evidence"])
            props = dict(dealname=r["Company"], pipeline=cfg["pipeline"], dealstage=cfg["stage"], hubspot_owner_id=x["owner_id"], description=desc)
            props[cfg["tag_prop"]] = need_opts[p][0][x["tag"]]
            props.update(cfg.get("extra_deal_props", {}))
            st["deal"] = call(p, "POST", "/crm/v3/objects/deals", json={"properties": props})["id"]
        if not st.get("assoc"):
            call(p, "PUT", "/crm/v4/objects/deal/%s/associations/default/contact/%s" % (st["deal"], st["contact"])); st["assoc"] = True
        if n % 10 == 0 or n == len(plan):
            prog_file.write_text(json.dumps(prog)); prog_file.chmod(0o600); print("  %d/%d created" % (n, len(plan)))
        time.sleep(0.15)
    prog_file.write_text(json.dumps(prog))
    print("\nVERIFY (live):")
    for name, portal, oid in OWNERS:
        ids = [prog["%s|%s" % (portal, x["row"]["_domain"])]["deal"] for x in plan if x["owner"] == name]
        bare = live = 0
        for i in range(0, len(ids), 100):
            j = call(portal, "POST", "/crm/v4/associations/deals/contacts/batch/read", json={"inputs": [{"id": d} for d in ids[i:i + 100]]})
            bare += len(ids[i:i + 100]) - len(j.get("results", []))
            j = call(portal, "POST", "/crm/v3/objects/deals/batch/read", json={"properties": ["hubspot_owner_id", "dealstage"], "inputs": [{"id": d} for d in ids[i:i + 100]]})
            live += sum(1 for d in j.get("results", []) if d["properties"]["hubspot_owner_id"] == oid and d["properties"]["dealstage"] == PORTALS[portal]["stage"])
        print("   %-18s deals=%d | at entry stage, right owner=%d | deals with no contact=%d" % (name, len(ids), live, bare))


if __name__ == "__main__":
    main()
