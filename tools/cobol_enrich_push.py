#!/usr/bin/env python3
"""COBOL fresh-build candidates -> CEO-type POC with a +91 mobile -> Coding pipeline (Lamiya / Yuktha, equal split).

Source: legacy/hubspot/TAMBuildSpecs/India/COBOL_Fresh_Build/out/final_in_scope_candidates.json (150 job-posting-evidence companies,
145 without a domain, headcount unverified).  Authorised by the user on 2026-10-05: lift the Apollo pause FOR THIS JOB ONLY, 10-company
pilot first, then stop at 60 callable leads or 1,200 credits, whichever comes first.  Apollo goes back to paused afterwards (the pause
flag in .env is never edited; this script only runs with --allow-apollo).

Per company: org search by name (1 credit when it returns a result) -> headcount gate 11-499 (org enrich +1 only if the search omits it)
-> people api_search (free) -> CEO/Founder/MD > COO > CTO -> unlock via people/match (1, also returns the work email) -> phone reveal (+8)
-> accept only a valid Indian MOBILE number and a LinkedIn URL.  Every result (incl. misses) is saved so nothing is paid for twice, and the
cap is enforced on Apollo's own free credit_usage_stats meter, not on our own counters.
    --pilot 10                     first 10 companies, then stop and report
    --run                          continue (cumulative cap across pilot+run)
    --push [--apply]               dedup against all 3 portals and create deal+contact in Coding Cold Call (lead_source COBOL)"""
import argparse, difflib, json, re, sqlite3, sys, time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
from leadgen import dedup, norm  # noqa: E402

SRC = ROOT / "legacy/hubspot/TAMBuildSpecs/India/COBOL_Fresh_Build/out/final_in_scope_candidates.json"
AUDIT = ROOT / "data" / "audit"; STATE = AUDIT / "cobol_enrich_state.json"
env = dict(l.strip().split("=", 1) for l in (ROOT / ".env").read_text().splitlines() if "=" in l and not l.startswith("#"))
BASE = "https://api.apollo.io/api/v1"
H = {"x-api-key": env["APOLLO_API_KEY"], "Content-Type": "application/json", "accept": "application/json", "Cache-Control": "no-cache", "User-Agent": "curl/8.4.0"}
MIN_HC, MAX_HC = 11, 499
TITLE_RANK = [(1, r"\b(founder|co-?founder|ceo|chief executive|managing director|\bmd\b|proprietor|owner)\b"), (2, r"\b(coo|chief operating)\b"), (3, r"\b(cto|chief technology|chief technical)\b")]


def title_rank(t):
    t = (t or "").lower()
    for r, rx in TITLE_RANK:
        if re.search(rx, t): return r
    return 99


def india_mobile(raw):
    import phonenumbers
    try: n = phonenumbers.parse(raw, "IN")
    except Exception: return ""
    ok = phonenumbers.is_valid_number(n) and phonenumbers.region_code_for_number(n) == "IN" and phonenumbers.number_type(n) == phonenumbers.PhoneNumberType.MOBILE
    return phonenumbers.format_number(n, phonenumbers.PhoneNumberFormat.E164) if ok else ""


def api(method, path, body=None, tries=4):
    """Every call goes through leadgen/apollo_ledger.call (one ledger row each). The free meter read is not a spend, so it stays direct."""
    import sys as _s
    _s.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from leadgen import apollo_ledger as _L
    if path == "/usage_stats/credit_usage_stats":
        r = requests.request(method, BASE + path, headers=H, json=body, timeout=45)
        return r.status_code, (r.json() if r.text else {})
    for a in range(tries):
        try:
            st, data, _ = _L.call(method, path, body, purpose="COBOL/IT enrichment (cobol_enrich_push)", caller="tools/cobol_enrich_push.py",
                                  segment="COBOL India POC", request_key=(body or {}).get("linkedin_url") or (body or {}).get("domain"), allow=True)
        except Exception:
            time.sleep(2); continue
        if st in (429, 502, 503, 504): time.sleep(3 * (a + 1)); continue
        return st, data
    return 0, {}


def consumed():
    s, d = api("POST", "/usage_stats/credit_usage_stats", {})
    lc = d["credit_usage_stats"]["lead_credit"]
    return lc["consumed"], lc["left_over"]


def best_org(name, orgs):
    n = norm.norm_company_name(name) or name.lower(); best, br = None, 0
    for o in orgs:
        on = norm.norm_company_name(o.get("name") or "") or (o.get("name") or "").lower()
        r = difflib.SequenceMatcher(None, n, on).ratio()
        if on and (n in on or on in n) and min(len(n), len(on)) >= 5: r = max(r, 0.9)
        if r > br: best, br = o, r
    return (best, br) if br >= 0.84 else (None, br)


def reveal(pid):
    s, d = api("POST", "/people/match", {"id": pid, "reveal_phone_number": True, "webhook_url": "https://example.com/apollo-noop"})
    rid = d.get("request_id")
    if not rid: return None, None
    for _ in range(8):
        time.sleep(7)
        s2, r2 = api("GET", "/webhook_result/%s" % rid)
        if s2 == 200:
            w = r2.get("webhook_result") or {}
            if w.get("status") in ("success", "failed"):
                ppl = w.get("people") or []
                if not ppl: return None, None
                ph = ppl[0].get("phone_numbers") or []
                mob = [p for p in ph if p.get("type_cd") == "mobile"]
                return (mob[0] if mob else (ph[0] if ph else {})).get("sanitized_number"), ppl[0].get("email")
    return None, None


def process(c):
    out = {"company": c["name"], "evidence": c.get("evidence", ""), "status": "", "notes": []}
    if c.get("domain") and c["domain"] != "None": domain = c["domain"]; org = None
    else:
        s, d = api("POST", "/mixed_companies/search", {"q_organization_name": c["name"], "organization_locations": ["India"], "page": 1, "per_page": 5})
        orgs = d.get("organizations") or d.get("accounts") or []
        org, ratio = best_org(c["name"], orgs)
        words = re.sub(r"[^A-Za-z0-9 ]", " ", c["name"]).split()
        if not org and len(words) > 2:            # free when empty: retry on the first two words, same strict similarity gate
            s, d = api("POST", "/mixed_companies/search", {"q_organization_name": " ".join(words[:2]), "organization_locations": ["India"], "page": 1, "per_page": 8})
            orgs = d.get("organizations") or d.get("accounts") or []
            org, ratio = best_org(" ".join(words[:2]), orgs)
        if not org: out["status"] = "org_not_found"; out["notes"].append("search returned %d, best match %.2f" % (len(orgs), ratio)); return out
        domain = org.get("primary_domain") or (org.get("website_url") or "").replace("https://", "").replace("http://", "").strip("/").replace("www.", "")
        if not domain: out["status"] = "org_no_domain"; return out
    out["domain"] = domain
    hc = (org or {}).get("estimated_num_employees")
    if hc is None:
        s, d = api("GET", "/organizations/enrich?domain=%s" % domain)
        hc = (d.get("organization") or {}).get("estimated_num_employees")
    out["headcount"] = hc
    if hc is None or not (MIN_HC <= int(hc) <= MAX_HC): out["status"] = "headcount_out_of_range" if hc is not None else "headcount_unknown"; return out
    s, d = api("POST", "/mixed_people/api_search", {"q_organization_domains": domain, "page": 1, "per_page": 10})
    people = sorted([p for p in (d.get("people") or []) if title_rank(p.get("title")) <= 3], key=lambda p: title_rank(p.get("title")))
    if not people: out["status"] = "no_ceo_tier_poc"; return out
    for p in people[:2]:
        s, d = api("POST", "/people/match", {"id": p.get("id")}); person = d.get("person") or {}
        full = (person.get("name") or p.get("name") or "").strip(); li = person.get("linkedin_url") or ""
        if not full or not li: out["notes"].append("no linkedin"); continue
        mob, em = reveal(person.get("id") or p.get("id"))
        mob = india_mobile(mob or "")
        email = person.get("email") or ""
        email = email if ("@" in email and "not_unlocked" not in email and person.get("email_status") == "verified") else ""
        if mob:
            first, _, last = full.partition(" ")
            out.update(status="lead", first=first, last=last, title=person.get("title") or p.get("title") or "", linkedin=li, mobile=mob, email=email); return out
        out["notes"].append("no indian mobile for %s" % full[:20])
    out["status"] = "no_mobile"; return out


def load_state():
    return json.loads(STATE.read_text()) if STATE.exists() else {"start": None, "results": {}}


def save_state(st): AUDIT.mkdir(parents=True, exist_ok=True); STATE.write_text(json.dumps(st, indent=1)); STATE.chmod(0o600)


def candidates():
    rows = json.loads(SRC.read_text())
    con = sqlite3.connect("file:%s?mode=ro" % (ROOT / "db/leadgen.sqlite"), uri=True)
    leads = [dict(company=r["name"], domain=(r.get("domain") if r.get("domain") not in (None, "None") else ""), email="", phone="", linkedin="") for r in rows]
    hits = dedup.check(leads, con=con, live=True)
    return [r for r, l in zip(rows, leads) if not hits.get(dedup.lead_key(l))], sum(1 for l in leads if hits.get(dedup.lead_key(l)))


def enrich(args):
    if env.get("APOLLO_PAUSED") == "1" and not args.allow_apollo:
        sys.exit("Apollo is PAUSED (APOLLO_PAUSED=1). The user lifted it for this job only: run with --allow-apollo.")
    st = load_state(); cands, held = candidates(); todo = [c for c in cands if c["name"] not in st["results"]]
    now, left = consumed()
    if st["start"] is None: st["start"] = now; save_state(st)
    print("candidates %d (already in HubSpot, skipped: %d) | to do %d | credits used so far in this job: %d | left %d" % (len(cands), held, len(todo), now - st["start"], left))
    if args.pilot: todo = todo[:args.pilot]
    leads = sum(1 for r in st["results"].values() if r["status"] == "lead")
    for n, c in enumerate(todo, 1):
        spent = consumed()[0] - st["start"]
        if spent >= args.max_credits: print("CREDIT CAP reached (%d)" % spent); break
        if leads >= args.max_leads: print("LEAD TARGET reached (%d)" % leads); break
        r = process(c); st["results"][c["name"]] = r; save_state(st)
        leads += r["status"] == "lead"
        print("  %3d/%d %-34s %-24s %s" % (n, len(todo), c["name"][:34], r["status"], (r.get("mobile", "") + " " + r.get("title", ""))[:40]), flush=True)
    after, left = consumed(); res = st["results"]
    from collections import Counter
    print("\noutcomes:", dict(Counter(r["status"] for r in res.values())))
    print("job credits so far: %d | leads %d | credits per lead %.1f | left %d" % (after - st["start"], leads, (after - st["start"]) / max(leads, 1), left))


def push(args):
    import push_itsvc_leads as P
    st = load_state(); leads = [r for r in st["results"].values() if r["status"] == "lead"]
    con = sqlite3.connect("file:%s?mode=ro" % (ROOT / "db/leadgen.sqlite"), uri=True)
    ds = [dict(company=r["company"], domain=r["domain"], email=r["email"], phone=r["mobile"], linkedin=r["linkedin"]) for r in leads]
    hits = dedup.check(ds, con=con, live=True)
    blocked = [r["company"] for r, d in zip(leads, ds) if hits.get(dedup.lead_key(d))]
    HOLD = ("hr consultants", "golden opportunities", "experian")     # staffing firms / a global giant's arm: not own-codebase companies
    held = [r["company"] for r in leads if any(h in r["company"].lower() for h in HOLD)]
    print("blocked by dedup:", blocked, "| held out by judgement:", held)
    ok = [r for r, d in zip(leads, ds) if not hits.get(dedup.lead_key(d)) and r["company"] not in held]
    print("leads %d | blocked by dedup (any portal): %d | to push: %d" % (len(leads), len(leads) - len(ok), len(ok)))
    owners = [("Lamiya Saleem", "96574824"), ("Yuktha Anand", "96573782")]
    ok.sort(key=lambda r: r["company"].lower()); plan = [(r, owners[i % 2]) for i, r in enumerate(ok)]
    for name, _ in owners: print("   %-14s %d" % (name, sum(1 for _, o in plan if o[0] == name)))
    if not args.apply: print("DRY RUN"); return
    cfg = P.PORTALS["main"]; prog_f = AUDIT / "cobol_push_progress.json"; prog = json.loads(prog_f.read_text()) if prog_f.exists() else {}
    vals, _ = P.ensure_options("main", "lead_source", {"COBOL"}, False); tagv = vals["COBOL"]
    for r, (oname, oid) in plan:
        stt = prog.setdefault(r["domain"], {})
        if "contact" not in stt:
            props = dict(firstname=r["first"], lastname=r["last"], jobtitle=r["title"], phone=r["mobile"], mobilephone=r["mobile"], company=r["company"], website=r["domain"], hubspot_owner_id=oid, linkedin_url=r["linkedin"])
            if r["email"]: props["email"] = r["email"]
            res = P.call("main", "POST", "/crm/v3/objects/contacts", json={"properties": props}, _tolerate=(409,))
            if "id" not in res and props.get("email"):
                stt["email_conflict"] = props.pop("email"); res = P.call("main", "POST", "/crm/v3/objects/contacts", json={"properties": props})
            stt["contact"] = res["id"]
        if "deal" not in stt:
            desc = "COBOL fresh build (Naukri/LinkedIn Jobs/web evidence), headcount %s. Evidence: %s" % (r.get("headcount"), (r["evidence"] or "")[:300])
            stt["deal"] = P.call("main", "POST", "/crm/v3/objects/deals", json={"properties": dict(dealname=r["company"], pipeline=cfg["pipeline"], dealstage=cfg["stage"], hubspot_owner_id=oid, description=desc, lead_source=tagv)})["id"]
        if not stt.get("assoc"):
            P.call("main", "PUT", "/crm/v4/objects/deal/%s/associations/default/contact/%s" % (stt["deal"], stt["contact"])); stt["assoc"] = True
        prog_f.write_text(json.dumps(prog)); time.sleep(0.15)
    print("pushed %d deals (each with a contact)" % len(plan))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--pilot", type=int, default=0); ap.add_argument("--run", action="store_true"); ap.add_argument("--push", action="store_true")
    ap.add_argument("--apply", action="store_true"); ap.add_argument("--allow-apollo", action="store_true")
    ap.add_argument("--max-credits", type=int, default=1200); ap.add_argument("--max-leads", type=int, default=60)
    a = ap.parse_args()
    if a.push: push(a)
    elif a.pilot or a.run: enrich(a)
    else: ap.print_help()
