#!/usr/bin/env python3
"""Move coding (IT-services) cold-call leads from Lamiya's Coding queue (MAIN) to Harsha (RAT) and Vaishnavi (COMPANYOPS) so each has
TARGET coding leads at the cold-call stage.  Cross-portal, so each lead is RE-CREATED in the target portal (deal + contact + association,
same tag logic as push_itsvc_leads.py) and only then archived in MAIN (deal, and its contact when no other live deal uses it).

Rules enforced: dedup against the TARGET portals (and any other MAIN deal) via leadgen.dedup - the source deal itself is ignored;
callable number + LinkedIn required; Lamiya's brand-new itsvc leads from today are not touched; deals at the entry stage only
(nothing with call history moves).  Dry run by default; --apply writes an audit file first, creates, verifies, then archives sources."""
import argparse, json, sqlite3, sys, time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
import push_itsvc_leads as P  # noqa: E402  (HubSpot helpers, portals, dropdown option logic)
from leadgen import dedup, norm  # noqa: E402

LAMIYA = "96574824"
TARGETS = {"rat": ("Harsha A", "98906502"), "companyops": ("Vaishnavi Kannan", "168341981")}
GOAL = 100


def search(p, obj, filters, props):
    out, after = [], None
    while True:
        body = {"filterGroups": [{"filters": filters}], "properties": props, "limit": 100}
        if after: body["after"] = after
        j = P.call(p, "POST", "/crm/v3/objects/%s/search" % obj, json=body)
        out += j["results"]; after = j.get("paging", {}).get("next", {}).get("after")
        if not after: return out


def coding_count(p):
    cfg = P.PORTALS[p]; oid = TARGETS[p][1]
    f = [{"propertyName": "pipeline", "operator": "EQ", "value": cfg["pipeline"]}, {"propertyName": "dealstage", "operator": "EQ", "value": cfg["stage"]},
         {"propertyName": "hubspot_owner_id", "operator": "EQ", "value": oid}]
    ds = search(p, "deals", f, [cfg["tag_prop"], "segment"])
    return sum(1 for d in ds if (d["properties"].get(cfg["tag_prop"]) or "").startswith("IT Services") or d["properties"].get("segment") == "IT Services")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--apply", action="store_true"); a = ap.parse_args()
    prog_new = json.loads((ROOT / "data/audit/itsvc_push_progress.json").read_text())
    new_ids = {v["deal"] for k, v in prog_new.items() if k.startswith("main|")}
    need = {p: max(GOAL - coding_count(p), 0) for p in TARGETS}
    print("coding leads at cold call now -> need to reach %d: Harsha +%d, Vaishnavi +%d" % (GOAL, need["rat"], need["companyops"]))
    src = search("main", "deals", [{"propertyName": "pipeline", "operator": "EQ", "value": "default"}, {"propertyName": "dealstage", "operator": "EQ", "value": "3992480462"},
                                   {"propertyName": "hubspot_owner_id", "operator": "EQ", "value": LAMIYA}], ["dealname", "lead_source", "description", "createdate"])
    src = [d for d in src if d["id"] not in new_ids and "it services" in (d["properties"].get("lead_source") or "").lower()]
    print("Lamiya's existing IT-services deals at Cold Call: %d" % len(src))
    ids = [d["id"] for d in src]; assoc = {}
    for i in range(0, len(ids), 100):
        j = P.call("main", "POST", "/crm/v4/associations/deals/contacts/batch/read", json={"inputs": [{"id": x} for x in ids[i:i + 100]]})
        for r in j.get("results", []): assoc[r["from"]["id"]] = [t["toObjectId"] for t in r.get("to", [])]
    cids = sorted({c for v in assoc.values() for c in v}); contacts = {}
    for i in range(0, len(cids), 100):
        j = P.call("main", "POST", "/crm/v3/objects/contacts/batch/read", json={"properties": ["firstname", "lastname", "jobtitle", "email", "phone", "mobilephone", "linkedin_url", "hs_linkedin_url", "company", "website"], "inputs": [{"id": str(x)} for x in cids[i:i + 100]]})
        for c in j["results"]: contacts[c["id"]] = c["properties"]
    cands = []
    for d in src:
        cs = assoc.get(d["id"], [])
        if not cs: continue
        c = contacts.get(str(cs[0])) or {}
        li = c.get("linkedin_url") or c.get("hs_linkedin_url") or ""
        ph = norm.norm_phone_e164(c.get("mobilephone") or c.get("phone") or "") or ""
        if not (li and ph.startswith(P.CALLABLE) and c.get("firstname")): continue
        em = (c.get("email") or "").strip()
        dom = norm.norm_domain(c.get("website") or "") or ""
        cands.append(dict(deal=d, contact_id=str(cs[0]), c=c, li=li, ph=ph, email=em, domain=dom, tag=d["properties"]["lead_source"], name=d["properties"]["dealname"],
                          other_contacts=[str(x) for x in cs[1:]]))
    print("with a contact that has LinkedIn + callable number: %d" % len(cands))
    # dedup against the TARGET portals (ignore the source deal itself)
    con = sqlite3.connect("file:%s?mode=ro" % (ROOT / "db/leadgen.sqlite"), uri=True)
    leads = [dict(company=x["name"], domain=x["domain"] or ("lamiya-%s" % x["deal"]["id"]), email=x["email"], phone=x["ph"], linkedin=x["li"]) for x in cands]
    t0 = time.time(); hits = dedup.check(leads, con=con, live=True); print("dedup took %.0fs" % (time.time() - t0))
    clear = []
    for x, l in zip(cands, leads):
        h = [h for h in hits.get(dedup.lead_key(l), []) if not (h["portal"] == "main" and str(h.get("deal_id")) == x["deal"]["id"])]
        if h: continue
        clear.append(x)
    print("clear of duplicates elsewhere: %d" % len(clear))
    clear.sort(key=lambda x: (0 if x["email"] else 1, x["tag"], x["name"].lower()))
    plan = []; i = 0
    for x in clear:
        for p in ("rat", "companyops"):
            if need[p] > 0 and len([q for q in plan if q["portal"] == p]) < need[p] and not any(q is x for q in plan):
                plan.append(dict(x=x, portal=p, owner_id=TARGETS[p][1])); break
    got = Counter(q["portal"] for q in plan)
    print("planned moves: Harsha %d, Vaishnavi %d | with email: %d" % (got["rat"], got["companyops"], sum(1 for q in plan if q["x"]["email"])))
    print("tags used:", Counter(q["x"]["tag"] for q in plan).most_common(6))
    short = {p: need[p] - got[p] for p in TARGETS if need[p] - got[p] > 0}
    if short: print("SHORT of goal:", short)
    if not a.apply: print("DRY RUN: nothing written."); return

    out = ROOT / "data/audit"; stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    audit = out / ("lamiya_move_%s.json" % stamp)
    audit.write_text(json.dumps(dict(at=datetime.now(timezone.utc).isoformat(), moves=[dict(src_deal=q["x"]["deal"]["id"], src_contact=q["x"]["contact_id"], company=q["x"]["name"], to=q["portal"], tag=q["x"]["tag"]) for q in plan]), indent=1)); audit.chmod(0o600)
    print("audit:", audit)
    opts = {}
    for p in TARGETS:
        labels = {q["x"]["tag"] for q in plan if q["portal"] == p}
        if labels: opts[p] = P.ensure_options(p, P.PORTALS[p]["tag_prop"], labels, True); print("[%s] options present (added %d)" % (p, len(opts[p][1])))
    prog_f = out / "lamiya_move_progress.json"; prog = json.loads(prog_f.read_text()) if prog_f.exists() else {}
    for n, q in enumerate(plan, 1):
        x, p = q["x"], q["portal"]; cfg = P.PORTALS[p]; k = "%s|%s" % (p, x["deal"]["id"]); st = prog.setdefault(k, {})
        if "contact" not in st:
            props = dict(firstname=x["c"].get("firstname"), lastname=x["c"].get("lastname") or "", jobtitle=x["c"].get("jobtitle") or "", phone=x["ph"], mobilephone=x["ph"],
                         company=x["c"].get("company") or x["name"], hubspot_owner_id=q["owner_id"])
            if x["c"].get("website"): props["website"] = x["c"]["website"]
            props[cfg["li_prop"]] = x["li"]
            if x["email"]: props["email"] = x["email"]
            res = P.call(p, "POST", "/crm/v3/objects/contacts", json={"properties": props}, _tolerate=(409,))
            if "id" not in res and props.get("email"):
                st["email_conflict"] = props.pop("email"); res = P.call(p, "POST", "/crm/v3/objects/contacts", json={"properties": props})
            st["contact"] = res["id"]
        if "deal" not in st:
            desc = (x["deal"]["properties"].get("description") or "").strip()
            desc = (desc + " | " if desc else "") + "Moved from Coding pipeline (Lamiya) on 2026-10-05; original deal id %s." % x["deal"]["id"]
            props = dict(dealname=x["name"], pipeline=cfg["pipeline"], dealstage=cfg["stage"], hubspot_owner_id=q["owner_id"], description=desc)
            props[cfg["tag_prop"]] = opts[p][0][x["tag"]]; props.update(cfg.get("extra_deal_props", {}))
            st["deal"] = P.call(p, "POST", "/crm/v3/objects/deals", json={"properties": props})["id"]
        if not st.get("assoc"):
            P.call(p, "PUT", "/crm/v4/objects/deal/%s/associations/default/contact/%s" % (st["deal"], st["contact"])); st["assoc"] = True
        if n % 10 == 0 or n == len(plan): prog_f.write_text(json.dumps(prog)); prog_f.chmod(0o600); print("  %d/%d created in target" % (n, len(plan)))
        time.sleep(0.15)
    # verify targets (deal exists, owner, stage, has contact) BEFORE touching the sources
    ok_moves = []
    for p in TARGETS:
        mine = [q for q in plan if q["portal"] == p]; dids = [prog["%s|%s" % (p, q["x"]["deal"]["id"])]["deal"] for q in mine]
        good = set()
        for i in range(0, len(dids), 100):
            chunk = dids[i:i + 100]
            j = P.call(p, "POST", "/crm/v3/objects/deals/batch/read", json={"properties": ["hubspot_owner_id", "dealstage"], "inputs": [{"id": d} for d in chunk]})
            right = {d["id"] for d in j["results"] if d["properties"]["hubspot_owner_id"] == TARGETS[p][1] and d["properties"]["dealstage"] == P.PORTALS[p]["stage"]}
            j = P.call(p, "POST", "/crm/v4/associations/deals/contacts/batch/read", json={"inputs": [{"id": d} for d in chunk]})
            has_contact = {r["from"]["id"] for r in j.get("results", [])}
            good |= (right & has_contact)
        for q, d in zip(mine, dids):
            if d in good: ok_moves.append(q)
        print("[%s] verified in target: %d of %d" % (p, len([1 for d in dids if d in good]), len(dids)))
    # archive sources
    sd = [q["x"]["deal"]["id"] for q in ok_moves]
    for i in range(0, len(sd), 100): P.call("main", "POST", "/crm/v3/objects/deals/batch/archive", json={"inputs": [{"id": x} for x in sd[i:i + 100]]})
    cc = sorted({q["x"]["contact_id"] for q in ok_moves}); c2d = {}
    for i in range(0, len(cc), 100):
        j = P.call("main", "POST", "/crm/v4/associations/contacts/deals/batch/read", json={"inputs": [{"id": c} for c in cc[i:i + 100]]})
        for r in j.get("results", []): c2d[r["from"]["id"]] = {t["toObjectId"] for t in r.get("to", [])}
    moved = set(sd); safe = [c for c in cc if c2d.get(c, set()) <= moved | set()]
    for i in range(0, len(safe), 100): P.call("main", "POST", "/crm/v3/objects/contacts/batch/archive", json={"inputs": [{"id": x} for x in safe[i:i + 100]]})
    print("archived in MAIN: %d deals, %d contacts (%d contacts kept: still on other live deals)" % (len(sd), len(safe), len(cc) - len(safe)))
    for p in TARGETS: print("[%s] %s now has %d coding leads at cold call" % (p, TARGETS[p][0], coding_count(p)))


if __name__ == "__main__":
    main()
