#!/usr/bin/env python3
"""Campaign lead files (LinkedIn lead-gen form / ads exports) -> SQLite (ext_campaign, ext_campaign_lead) -> 3-portal dedup -> HubSpot.

    load   <csv> --slug S --name N --channel C --vertical V --label LinkedinAdsLead    file into SQLite (idempotent on sha256)
    dedup  --slug S                                                                      gates + dedup against ALL THREE portals (live)
    push   --slug S [--apply]                                                            net-new to Yuktha / Lamiya (MAIN Coding, Cold Call), recorded in SQLite
    status --slug S                                                                      counts only

Gates (standing rules): +91 MOBILE number, LinkedIn URL, e-mail present, not a form test lead, not duplicated in the file, not on the suppression
list, no live deal for the person or their company in main / companyops / rat.  Rows failing a gate are KEPT with the reason (rule 8).
Deal = the person's name (precedent: the 16 LinkedinAdsLead deals already in MAIN), contact with phone + e-mail + LinkedIn, deal associated to it.
Writes to HubSpot only with --apply.  Prints counts, never contact details."""
import argparse, csv, hashlib, json, re, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
from leadgen import db as ldb, dedup, norm, suppression  # noqa: E402

MAIN_PIPELINE, MAIN_COLD = "default", "3992480462"
CALLERS = [("Yuktha Anand", "96573782"), ("Lamiya Saleem", "96574824")]
GENERIC = {"gmail.com", "yahoo.com", "yahoo.co.uk", "yahoo.co.in", "hotmail.com", "outlook.com", "icloud.com", "rediffmail.com", "live.com", "proton.me", "protonmail.com", "gmail.con"}
NOW = "strftime('%Y-%m-%dT%H:%M:%fZ','now')"


def callable_mobile(raw):
    """(E.164, region) for a callable mobile, else ("", None).  India: a valid 10-digit national number starting 6-9 whose type is
    MOBILE or FIXED_LINE_OR_MOBILE (libphonenumber cannot separate these for Indian numbers).  Elsewhere: type MOBILE only.
    Numbers with no country code are not guessed: they stay unverified."""
    import phonenumbers
    raw = (raw or "").strip()
    if not raw.startswith("+"): return "", None
    try: n = phonenumbers.parse(raw, None)
    except Exception: return "", None
    if not phonenumbers.is_valid_number(n): return "", None
    reg = phonenumbers.region_code_for_number(n); t = phonenumbers.number_type(n); P = phonenumbers.PhoneNumberType
    nat = str(n.national_number)
    ok = (reg == "IN" and len(nat) == 10 and nat[0] in "6789" and t in (P.MOBILE, P.FIXED_LINE_OR_MOBILE)) or (reg != "IN" and t == P.MOBILE)
    return (phonenumbers.format_number(n, phonenumbers.PhoneNumberFormat.E164), reg) if ok else ("", None)


def col(r, *names):
    low = {k.lstrip("﻿").strip().strip('"').lower(): (v or "").strip() for k, v in r.items()}
    for n in names:
        if low.get(n.lower()): return low[n.lower()]
    return ""


def full_name(row):
    return " ".join(x for x in ((row["first_name"] or "").strip(), (row["last_name"] or "").strip()) if x).strip()


def biz_domain(email):
    d = norm.norm_domain((email or "").split("@")[-1]) if email and "@" in email else None
    return d if d and d not in GENERIC else ""


def cmd_load(a, con):
    p = Path(a.csv); sha = hashlib.sha256(p.read_bytes()).hexdigest()
    got = con.execute("SELECT campaign_id FROM ext_campaign WHERE source_sha256=?", (sha,)).fetchone()
    if got: print("already loaded (same sha256): campaign_id", got[0]); return
    rows = list(csv.DictReader(open(p, encoding="utf-8-sig")))
    with con:
        cid = con.execute("INSERT INTO ext_campaign(slug,name,channel,vertical,hubspot_lead_source_label,source_file,source_sha256,notes) VALUES (?,?,?,?,?,?,?,?)",
                          (a.slug, a.name, a.channel, a.vertical, a.label, str(p.relative_to(ROOT)) if p.is_absolute() and ROOT in p.parents else str(p), sha, a.notes or None)).lastrowid
        seen = set()
        for i, r in enumerate(rows, 1):
            li = norm.norm_linkedin_person(col(r, "LinkedIn profile URL", "linkedin"))
            em = norm.norm_email(col(r, "Work email", "email")) if col(r, "Work email", "email") else None
            ph, reg = callable_mobile(col(r, "Phone number", "phone"))
            status, detail = "pending", None
            if col(r, "test_lead").lower() == "true": status, detail = "invalid", "form test lead"
            key = li or em or ph
            if status == "pending" and key in seen: status, detail = "duplicate_in_file", "repeats an earlier row"
            seen.add(key)
            con.execute("INSERT INTO ext_campaign_lead(campaign_id,row_no,first_name,last_name,company,company_domain_norm,person_linkedin_norm,email_norm,phone_e164,country,raw_json,dedup_status,dedup_detail) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (cid, i, col(r, "First name") or None, col(r, "Last name") or None, None, biz_domain(em) or None, li, em, ph,
                         reg, json.dumps({k.lstrip("﻿").strip('"'): v for k, v in r.items()}, ensure_ascii=False), status, detail))
    print("loaded campaign", a.slug, "rows", len(rows))


def pending_gates(r):
    """first failing gate for a row that is still 'pending', else None"""
    if not r["person_linkedin_norm"]: return "no LinkedIn URL"
    if not r["phone_e164"]: return "no callable mobile (+91 or another country's mobile; no country code = not checked)"
    if not r["email_norm"]: return "no e-mail"
    return None


def cmd_phones(a, con):
    """re-evaluate the phone of every row rejected by the phone gate, from its stored raw row (the source file is never re-read)"""
    cid = con.execute("SELECT campaign_id FROM ext_campaign WHERE slug=?", (a.slug,)).fetchone()["campaign_id"]
    rows = con.execute("SELECT lead_id, raw_json FROM ext_campaign_lead WHERE campaign_id=? AND dedup_status='invalid' AND dedup_detail LIKE 'no %' ", (cid,)).fetchall()
    with con:
        for r in rows:
            ph, reg = callable_mobile(json.loads(r["raw_json"]).get("Phone number", ""))
            if ph:
                con.execute("UPDATE ext_campaign_lead SET phone_e164=?, country=?, dedup_status='pending', dedup_detail=NULL WHERE lead_id=?", (ph, reg, r["lead_id"]))
    print("re-checked %d phone-rejected rows" % len(rows))


def cmd_dedup(a, con):
    cid = con.execute("SELECT campaign_id FROM ext_campaign WHERE slug=?", (a.slug,)).fetchone()["campaign_id"]
    rows = con.execute("SELECT * FROM ext_campaign_lead WHERE campaign_id=? AND dedup_status IN ('pending','new','in_hubspot') AND pushed_hs_deal_id IS NULL ORDER BY row_no", (cid,)).fetchall()
    cand, upd = [], []
    for r in rows:
        g = pending_gates(r)
        if g: upd.append(("invalid", g, r["lead_id"])); continue
        try: sup = suppression.check(con, domain=r["company_domain_norm"], email=r["email_norm"], phone=r["phone_e164"], linkedin_person=r["person_linkedin_norm"])
        except Exception as e: upd.append(("invalid", "suppression check failed closed: %s" % e, r["lead_id"])); continue
        if sup: upd.append(("suppressed", "suppression list (never push)", r["lead_id"])); continue
        cand.append(r)
    leads = [dict(company=full_name(r), domain=r["company_domain_norm"] or "", email=r["email_norm"], phone=r["phone_e164"], linkedin=r["person_linkedin_norm"]) for r in cand]
    hits = dedup.check(leads, con=con, live=True)
    for r, l in zip(cand, leads):
        h = hits.get(dedup.lead_key(l))
        upd.append(("in_hubspot", dedup.explain(h)[:300], r["lead_id"]) if h else ("new", None, r["lead_id"]))
    with con:
        for st, det, lid in upd:
            con.execute("UPDATE ext_campaign_lead SET dedup_status=?, dedup_detail=?, dedup_checked_at=%s WHERE lead_id=?" % NOW, (st, det, lid))
    cmd_status(a, con)


def cmd_status(a, con):
    cid = con.execute("SELECT campaign_id FROM ext_campaign WHERE slug=?", (a.slug,)).fetchone()["campaign_id"]
    for r in con.execute("SELECT dedup_status s, count(*) n, sum(pushed_hs_deal_id IS NOT NULL) p FROM ext_campaign_lead WHERE campaign_id=? GROUP BY 1 ORDER BY 2 DESC", (cid,)): print("  %-18s %4d  pushed %d" % (r["s"], r["n"], r["p"]))
    for r in con.execute("SELECT dedup_detail d, count(*) n FROM ext_campaign_lead WHERE campaign_id=? AND dedup_status='invalid' GROUP BY 1 ORDER BY 2 DESC", (cid,)): print("     invalid because: %-45s %d" % (r["d"], r["n"]))


def cmd_push(a, con):
    import push_itsvc_leads as P
    camp = con.execute("SELECT * FROM ext_campaign WHERE slug=?", (a.slug,)).fetchone()
    rows = con.execute("SELECT * FROM ext_campaign_lead WHERE campaign_id=? AND dedup_status='new' AND pushed_hs_deal_id IS NULL ORDER BY row_no", (camp["campaign_id"],)).fetchall()
    plan = [(r, CALLERS[i % len(CALLERS)]) for i, r in enumerate(rows)]
    print("net-new to push: %d -> %s" % (len(plan), ", ".join("%s %d" % (n, sum(1 for _, c in plan if c[0] == n)) for n, _ in CALLERS)))
    if not a.apply: print("DRY RUN (add --apply)"); return
    label = camp["hubspot_lead_source_label"]
    values, _ = P.ensure_options("main", "lead_source", [label], apply=False)
    if label not in values: sys.exit("lead_source option %r does not exist in MAIN; refusing to invent it" % label)
    lsv = values[label]
    for r, (owner, oid) in plan:
        # last-second live recheck of this one lead (a deal could have been created since the batch dedup)
        l = dict(company=full_name(r), domain=r["company_domain_norm"] or "", email=r["email_norm"], phone=r["phone_e164"], linkedin=r["person_linkedin_norm"])
        if dedup.check([l], con=con, live=True).get(dedup.lead_key(l)):
            with con: con.execute("UPDATE ext_campaign_lead SET dedup_status='in_hubspot', dedup_detail='appeared in HubSpot before push', dedup_checked_at=%s WHERE lead_id=?" % NOW, (r["lead_id"],))
            continue
        raw = json.loads(r["raw_json"]); name = full_name(r)
        props = dict(firstname=(r["first_name"] or "").strip(), lastname=(r["last_name"] or "").strip(), email=r["email_norm"], phone=r["phone_e164"], mobilephone=r["phone_e164"], hubspot_owner_id=oid,
                     linkedin_url="https://www.linkedin.com/" + r["person_linkedin_norm"])  # norm gives "in/<slug>"
        if r["company_domain_norm"]: props["website"] = r["company_domain_norm"]
        x = P.call("main", "POST", "/crm/v3/objects/contacts", json={"properties": props}, _tolerate=(409, 400))
        if "id" not in x:   # e-mail conflict (409) or invalid address such as a typo'd TLD (400): keep the lead, drop the e-mail from the contact
            props.pop("email"); x = P.call("main", "POST", "/crm/v3/objects/contacts", json={"properties": props})
        deal = P.call("main", "POST", "/crm/v3/objects/deals", json={"properties": dict(
            dealname=name, pipeline=MAIN_PIPELINE, dealstage=MAIN_COLD, hubspot_owner_id=oid, lead_source=lsv,
            description="LinkedIn lead-gen form (%s), submitted %s. Inbound: the person filled the form themselves." % (raw.get("form_name", ""), raw.get("created_time", "")))})
        P.call("main", "PUT", "/crm/v4/objects/deal/%s/associations/default/contact/%s" % (deal["id"], x["id"]))
        with con:
            con.execute("UPDATE ext_campaign_lead SET pushed_account='main', pushed_hs_deal_id=?, pushed_owner=?, pushed_lead_source=?, pushed_at=%s WHERE lead_id=?" % NOW, (deal["id"], owner, label, r["lead_id"]))
        time.sleep(0.15)
    cmd_status(a, con)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="cmd", required=True)
    l = sp.add_parser("load"); l.add_argument("csv"); l.add_argument("--slug", required=True); l.add_argument("--name", required=True); l.add_argument("--channel", required=True)
    l.add_argument("--vertical"); l.add_argument("--label"); l.add_argument("--notes")
    for n in ("phones", "dedup", "status", "push"):
        s = sp.add_parser(n); s.add_argument("--slug", required=True)
        if n == "push": s.add_argument("--apply", action="store_true")
    a = ap.parse_args(); con = ldb.connect(); con.row_factory = __import__("sqlite3").Row
    {"load": cmd_load, "phones": cmd_phones, "dedup": cmd_dedup, "status": cmd_status, "push": cmd_push}[a.cmd](a, con)
