#!/usr/bin/env python3
"""EdTech ICP passers -> POC phone (+91) + verified work email via Apollo -> Company Ops Cluster 1, owner Manit, cold-call stage.

User authorisation (2026-10-05): "go ahead with the ICP check first and the passing ones ... +91 found push to Manit".  Apollo stays PAUSED in
.env; this tool only spends with --allow-apollo, in a measured pilot, under a hard credit cap read from Apollo's free meter.
Selection (free): Fit + Maybe-that-are-own-product companies from out/edtech_untouched_scored.csv, India HQ not 'no', known headcount in 20-1000 or unknown;
dedup against ALL THREE portals (company name / domain / POC LinkedIn) before any credit is spent.
Per company: org enrich by domain (1) -> headcount gate 20-1000, India -> person match by the export's LinkedIn URL, or free people search if the export has none (1)
-> phone reveal (+8) -> keep only a valid Indian MOBILE and a LinkedIn URL.  Push: deal named after the company + contact, Cluster 1 (pipeline default),
stage Cold called assigned, owner Manit; 20-50 headcount gets a caller note (rule 4).
    --select                       free: show who qualifies and who is already in HubSpot
    --enrich --pilot 4 --allow-apollo / --enrich --allow-apollo --max-credits 300
    --push [--apply]"""
import argparse, csv, glob, hashlib, json, sqlite3, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
import cobol_enrich_push as C  # noqa: E402  (Apollo helpers: api, consumed, reveal, india_mobile, title_rank)
import push_itsvc_leads as P  # noqa: E402  (HubSpot helpers)
from leadgen import dedup, norm  # noqa: E402

D = ROOT / "data/inbox/edtech"; AUDIT = ROOT / "data/audit"; RES = AUDIT / "edtech_apollo_results.json"
MANIT = "168609107"; C1_PIPELINE = "default"; C1_COLD = "4327110346"
NON_EDU = {"FluxGen", "PathiQ", "MintCarb", "ScaNxt", "Kapable", "Avidia Labs", "Spoodle", "Shastra Lab"}       # not education products: segment 'Enterprise SaaS/IT'
SKIP = {"Shastra Lab"}                                                       # verdict: likely a 2-person company, below the floor


def verdicts():
    v = {}
    for f in glob.glob(str(D / "verdicts/batch_*.json")):
        for x in json.load(open(f)): v[x["company"]] = x
    return v


def select():
    rows = {r["company"]: r for r in csv.DictReader(open(ROOT / "out/edtech_untouched_scored.csv"))}; ver = verdicts(); sel = []
    for c, r in rows.items():
        v = ver.get(c) or {}
        if r["icp_bucket"] == "Fit" or (r["icp_bucket"] == "Maybe" and v.get("company_type") == "product" and v.get("has_own_tech_product")):
            if v.get("india_hq") == "no" or c in SKIP: continue
            sel.append(dict(company=c, domain=r["resolved_domain"], poc_name=r["poc_name"], poc_title=r["poc_title"], poc_linkedin=r["poc_linkedin"], bucket=r["icp_bucket"], score=int(r["score"]),
                            evidence=v.get("evidence_quote", ""), ops=r["ops_signals"], india=v.get("india_hq", "")))
    con = sqlite3.connect("file:%s?mode=ro" % (ROOT / "db/leadgen.sqlite"), uri=True)
    leads = [dict(company=s["company"], domain=s["domain"], linkedin=s["poc_linkedin"], email="", phone="") for s in sel]
    hits = dedup.check(leads, con=con, live=True)
    clear, blocked = [], []
    for s, l in zip(sel, leads):
        h = hits.get(dedup.lead_key(l))
        (blocked if h else clear).append((s, dedup.explain(h) if h else ""))
    return [s for s, _ in clear], blocked


def enrich(a):
    if C.env.get("APOLLO_PAUSED") == "1" and not a.allow_apollo: sys.exit("Apollo is PAUSED. Run with --allow-apollo (user authorised this job).")
    clear, blocked = select(); res = json.loads(RES.read_text()) if RES.exists() else {"start": None, "results": {}}
    now, left = C.consumed()
    if res["start"] is None: res["start"] = now
    todo = [s for s in clear if s["company"] not in res["results"]]
    if a.pilot: todo = todo[:a.pilot]
    print("qualifying %d | blocked by dedup %d | to do now %d | credits used so far %d | left %d" % (len(clear), len(blocked), len(todo), now - res["start"], left), flush=True)
    for s in todo:
        if C.consumed()[0] - res["start"] >= a.max_credits: print("CREDIT CAP reached"); break
        r = {"company": s["company"], "domain": s["domain"], "status": "", "notes": []}
        sc, d = C.api("GET", "/organizations/enrich?domain=%s" % s["domain"]); org = d.get("organization") or {}
        hc = org.get("estimated_num_employees"); r["headcount"] = hc; r["org_country"] = org.get("country")
        if hc is not None and not (20 <= int(hc) <= 1000): r["status"] = "headcount_out_of_range"
        elif org.get("country") and "india" not in org["country"].lower(): r["status"] = "not_india"
        else:
            pid, li = None, s["poc_linkedin"]
            if li:
                sc, d = C.api("POST", "/people/match", {"linkedin_url": li, "domain": s["domain"]}); person = d.get("person") or {}
            else:
                sc, d = C.api("POST", "/mixed_people/api_search", {"q_organization_domains": s["domain"], "page": 1, "per_page": 10})
                ppl = sorted([p for p in (d.get("people") or []) if C.title_rank(p.get("title")) <= 3], key=lambda p: C.title_rank(p.get("title")))
                if not ppl: r["status"] = "no_ceo_tier_poc"; res["results"][s["company"]] = r; RES.write_text(json.dumps(res, indent=1)); print("  %-22s %s" % (s["company"][:22], r["status"])); continue
                sc, d = C.api("POST", "/people/match", {"id": ppl[0]["id"]}); person = d.get("person") or {}
            pid = person.get("id"); li = person.get("linkedin_url") or li
            if not pid or not li: r["status"] = "no_person_or_linkedin"
            else:
                mob, em = C.reveal(pid); mob = C.india_mobile(mob or "")
                email = person.get("email") or ""
                email = email if ("@" in email and "not_unlocked" not in email and person.get("email_status") == "verified") else ""
                if mob:
                    full = (person.get("name") or s["poc_name"] or "").strip(); first, _, last = full.partition(" ")
                    r.update(status="lead", first=first, last=last, title=person.get("title") or s["poc_title"], linkedin=li, mobile=mob, email=email, bucket=s["bucket"], evidence=s["evidence"], ops=s["ops"])
                else: r["status"] = "no_indian_mobile"
        res["results"][s["company"]] = r; RES.write_text(json.dumps(res, indent=1)); RES.chmod(0o600)
        print("  %-22s %-24s hc=%s %s" % (s["company"][:22], r["status"], r.get("headcount"), r.get("mobile", "")), flush=True)
    after, left = C.consumed(); leads = sum(1 for x in res["results"].values() if x["status"] == "lead")
    print("job credits so far %d | leads %d | left %d" % (after - res["start"], leads, left))


def push(a):
    res = json.loads(RES.read_text())["results"]; leads = [r for r in res.values() if r["status"] == "lead"]
    bad = [r["company"] + " (" + (r.get("title") or "") + ")" for r in leads if C.title_rank(r.get("title")) > 3]
    if bad: print("held out - POC title is not CEO/Founder/MD/COO/CTO tier:", bad)
    leads = [r for r in leads if C.title_rank(r.get("title")) <= 3]
    con = sqlite3.connect("file:%s?mode=ro" % (ROOT / "db/leadgen.sqlite"), uri=True)
    ds = [dict(company=r["company"], domain=r["domain"], email=r["email"], phone=r["mobile"], linkedin=r["linkedin"]) for r in leads]
    hits = dedup.check(ds, con=con, live=True)
    ok = [r for r, d in zip(leads, ds) if not hits.get(dedup.lead_key(d))]
    print("leads with a +91 mobile: %d | blocked by dedup: %d | to push to Manit (Cluster 1, cold-called-assigned): %d" % (len(leads), len(leads) - len(ok), len(ok)))
    for r in ok: print("   %-22s %-28s hc=%s email=%s" % (r["company"][:22], (r["title"] or "")[:28], r.get("headcount"), "yes" if r["email"] else "no"))
    if not a.apply: print("DRY RUN"); return
    prog_f = AUDIT / "edtech_push_progress.json"; prog = json.loads(prog_f.read_text()) if prog_f.exists() else {}
    for r in ok:
        st = prog.setdefault(r["domain"], {})
        if "contact" not in st:
            props = dict(firstname=r["first"], lastname=r["last"], jobtitle=r["title"], phone=r["mobile"], mobilephone=r["mobile"], company=r["company"], website=r["domain"], hubspot_owner_id=MANIT, linkedin_url=r["linkedin"])
            if r["email"]: props["email"] = r["email"]
            x = P.call("companyops", "POST", "/crm/v3/objects/contacts", json={"properties": props}, _tolerate=(409,))
            if "id" not in x and props.get("email"): st["email_conflict"] = props.pop("email"); x = P.call("companyops", "POST", "/crm/v3/objects/contacts", json={"properties": props})
            st["contact"] = x["id"]
        if "deal" not in st:
            hc = r.get("headcount"); note = " Headcount ~%s (20-50 band): caller to judge fit on the call." % hc if hc and int(hc) < 50 else ""
            desc = "EdTech ICP check 2026-10-05 (%s). Own-product evidence: %s Ops signals: %s.%s" % (r["bucket"], (r["evidence"] or "")[:200], r.get("ops") or "none", note)
            seg = "Enterprise SaaS/IT" if r["company"] in NON_EDU else "EdTech"
            st["deal"] = P.call("companyops", "POST", "/crm/v3/objects/deals", json={"properties": dict(dealname=r["company"], pipeline=C1_PIPELINE, dealstage=C1_COLD, hubspot_owner_id=MANIT,
                                                                                                         lead_source="Cold Call ( Company Ops )", segment=seg, description=desc)})["id"]
        if not st.get("assoc"): P.call("companyops", "PUT", "/crm/v4/objects/deal/%s/associations/default/contact/%s" % (st["deal"], st["contact"])); st["assoc"] = True
        prog_f.write_text(json.dumps(prog)); time.sleep(0.15)
    print("pushed %d deals to Manit, each with a contact" % len(ok))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--select", action="store_true"); ap.add_argument("--enrich", action="store_true"); ap.add_argument("--push", action="store_true")
    ap.add_argument("--apply", action="store_true"); ap.add_argument("--allow-apollo", action="store_true"); ap.add_argument("--pilot", type=int, default=0); ap.add_argument("--max-credits", type=int, default=300)
    a = ap.parse_args()
    if a.select:
        clear, blocked = select(); print("qualifying and clear of HubSpot: %d" % len(clear))
        for s in clear: print("   %-5s %3d  %-26s %-22s POC: %s | %s" % (s["bucket"], s["score"], s["company"][:26], s["domain"], s["poc_name"] or "(none in export)", (s["poc_title"] or "")[:24]))
        print("blocked (already in HubSpot): %d" % len(blocked))
        for s, why in blocked: print("   %-26s %s" % (s["company"][:26], why[:90]))
    elif a.enrich: enrich(a)
    elif a.push: push(a)
    else: ap.print_help()
