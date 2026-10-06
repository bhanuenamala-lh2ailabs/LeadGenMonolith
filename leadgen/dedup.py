"""Three-portal dedup gate.  STANDING RULE (user, 2026-10-05): every lead is deduplicated against ALL THREE HubSpot portals
(main, companyops, rat) before anything is pushed.  Dedup unit = a live (non-archived) DEAL: a company or contact record with no deal
does not block, because it is only a record.  Archived deals do not block either.

Two layers:
  * local_check  - instant, from db/leadgen.sqlite (last sync): domain, email, normalised LinkedIn, phone, company name.
  * live_check   - confirms against HubSpot itself right now (read-only searches + association reads through the guarded
                   HubSpotClient), the three portals in parallel: company domain, contact email, phone variants, exact deal name.
check() runs both and merges.  A lead is *blocked* when any hit exists in any portal.

Lead dict keys: company, domain, email, phone, linkedin  (any may be blank).  Python 3.9.
"""
import sqlite3
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Iterable, List, Optional

from leadgen import norm

ACCOUNTS = ("main", "companyops", "rat")


def lead_key(lead: Dict[str, Any]) -> str:
    return (norm.norm_domain(lead.get("domain") or "") or (lead.get("domain") or "").lower() or (lead.get("company") or "").lower())


def phone_variants(raw: str) -> List[str]:
    e164 = norm.norm_phone_e164(raw or "") or ""
    if not e164:
        return []
    digits = e164.lstrip("+")
    out = {e164, digits}
    if e164.startswith("+91") and len(digits) == 12:
        out |= {digits[2:], "0" + digits[2:], "+91 " + digits[2:7] + " " + digits[7:], "+91-" + digits[2:]}
    return sorted(out)


# ------------------------------------------------------------------ local (instant)
def local_check(con: sqlite3.Connection, leads: Iterable[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """matches against live deals in the synced DB.  Returns {lead_key: [hit,...]}"""
    con.row_factory = sqlite3.Row
    idx = {"domain": defaultdict(set), "email": defaultdict(set), "linkedin": defaultdict(set), "phone": defaultdict(set), "name": defaultdict(set)}
    for r in con.execute("""SELECT d.account_id a, d.hs_deal_id h, d.dealname n, d.lh2_domain ld, cal.hs_domain cd, cal.lh2_domain cld
                            FROM deal d LEFT JOIN deal_company dc ON dc.deal_id = d.deal_id
                            LEFT JOIN company_account_link cal ON cal.link_id = dc.link_id WHERE d.is_archived = 0"""):
        for dom in (r["ld"], r["cd"], r["cld"]):
            nd = norm.norm_domain(dom or "")
            if nd: idx["domain"][nd].add((r["a"], r["h"]))
        nn = norm.norm_company_name(r["n"] or "")
        if nn: idx["name"][nn].add((r["a"], r["h"]))
    for r in con.execute("""SELECT d.account_id a, d.hs_deal_id h, c.email_norm e, c.linkedin_person_norm l, cp.phone_e164 p
                            FROM deal d JOIN deal_contact dk ON dk.deal_id = d.deal_id JOIN contact c ON c.contact_id = dk.contact_id
                            LEFT JOIN contact_phone cp ON cp.contact_id = c.contact_id WHERE d.is_archived = 0 AND c.is_archived = 0"""):
        if r["e"]: idx["email"][r["e"]].add((r["a"], r["h"]))
        if r["l"]: idx["linkedin"][r["l"]].add((r["a"], r["h"]))
        if r["p"]: idx["phone"][r["p"]].add((r["a"], r["h"]))
    hits = defaultdict(list)
    for lead in leads:
        k = lead_key(lead)
        probes = {"domain": norm.norm_domain(lead.get("domain") or ""), "email": norm.norm_email(lead.get("email") or "") if lead.get("email") else None,
                  "linkedin": norm.norm_linkedin_person(lead.get("linkedin") or "") if lead.get("linkedin") else None,
                  "phone": norm.norm_phone_e164(lead.get("phone") or "") if lead.get("phone") else None,
                  "name": norm.norm_company_name(lead.get("company") or "")}
        for kind, val in probes.items():
            for acct, deal in sorted(idx[kind].get(val, ())) if val else ():
                hits[k].append({"layer": "local", "portal": acct, "kind": kind, "deal_id": deal})
    return hits


# ------------------------------------------------------------------ live (authoritative)
def _search_ids(client, obj: str, prop: str, values: List[str]) -> Dict[str, str]:
    found = {}
    vals = sorted({v for v in values if v})
    for i in range(0, len(vals), 90):
        body = {"filterGroups": [{"filters": [{"propertyName": prop, "operator": "IN", "values": vals[i:i + 90]}]}], "properties": [prop], "limit": 100}
        for x in client.post_read("/crm/v3/objects/%s/search" % obj, body).get("results", []):
            found[x["id"]] = (x["properties"].get(prop) or "").lower()
    return found


def _live_deal_ids(client, from_obj: str, ids: List[str]) -> Dict[str, List[str]]:
    """for each company/contact id: ids of its LIVE (non-archived) deals"""
    if not ids:
        return {}
    assoc = client.batch_read_associations(from_obj, "deals", list(ids))
    all_deals = sorted({a["id"] if "id" in a else a.get("toObjectId") for v in assoc.values() for a in v if (a.get("id") or a.get("toObjectId"))})
    live = set()
    for i in range(0, len(all_deals), 100):
        for d in client.batch_read("deals", all_deals[i:i + 100], ["dealname"]):
            live.add(str(d["id"]))
    return {cid: [str(a.get("id") or a.get("toObjectId")) for a in v if str(a.get("id") or a.get("toObjectId")) in live] for cid, v in assoc.items()}


def _live_one_portal(account: str, leads: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    from leadgen.hubspot.client import HubSpotClient
    cl = HubSpotClient(account)
    hits = []
    by = {"domain": {}, "email": {}, "phone": {}, "name": {}}
    for l in leads:
        k = lead_key(l)
        d = norm.norm_domain(l.get("domain") or "")
        if d: by["domain"].setdefault(d, k)
        if l.get("email"): by["email"].setdefault(l["email"].strip().lower(), k)
        for v in phone_variants(l.get("phone") or ""): by["phone"].setdefault(v.lower(), k)
        if l.get("company"): by["name"].setdefault(l["company"].strip().lower(), k)
    plans = [("companies", "domain", "domain", "companies"), ("contacts", "email", "email", "contacts"),
             ("contacts", "mobilephone", "phone", "contacts"), ("contacts", "phone", "phone", "contacts")]
    for obj, prop, kind, assoc_from in plans:
        found = _search_ids(cl, obj, prop, list(by[kind].keys()))
        live = _live_deal_ids(cl, assoc_from, list(found.keys()))
        for rid, val in found.items():
            if live.get(rid):
                hits.append({"layer": "live", "portal": account, "kind": kind, "record": "%s:%s" % (obj, rid), "deal_id": live[rid][0], "lead_key": by[kind].get(val)})
    for did, nm in _search_ids(cl, "deals", "dealname", list(by["name"].keys())).items():
        hits.append({"layer": "live", "portal": account, "kind": "name", "record": "deals:%s" % did, "deal_id": did, "lead_key": by["name"].get(nm)})
    return hits


def live_check(leads: List[Dict[str, Any]], accounts: Iterable[str] = ACCOUNTS) -> Dict[str, List[Dict[str, Any]]]:
    out = defaultdict(list)
    with ThreadPoolExecutor(max_workers=len(tuple(accounts))) as ex:
        for res in ex.map(lambda a: _live_one_portal(a, leads), accounts):
            for h in res:
                k = h.pop("lead_key", None)
                if k: out[k].append(h)
    return out


def check(leads: List[Dict[str, Any]], con: Optional[sqlite3.Connection] = None, live: bool = True) -> Dict[str, List[Dict[str, Any]]]:
    """Merged hits per lead key.  A lead with any hit must not be pushed."""
    merged = defaultdict(list)
    if con is not None:
        for k, v in local_check(con, leads).items(): merged[k] += v
    if live:
        for k, v in live_check(leads).items(): merged[k] += v
    return merged


def explain(hits: List[Dict[str, Any]]) -> str:
    seen, parts = set(), []
    for h in hits:
        t = (h["portal"], h["kind"], h.get("deal_id"))
        if t in seen: continue
        seen.add(t); parts.append("%s %s match (deal %s%s)" % (h["portal"], h["kind"], h.get("deal_id"), ", live-confirmed" if h["layer"] == "live" else ", local index"))
    return "; ".join(parts[:3])


# ------------------------------------------------------------------ CLI:  python -m leadgen dedup <file.csv> [--no-live]
def _pick(row: Dict[str, str], *names: str) -> str:
    low = {k.lower().strip(): (v or "").strip() for k, v in row.items()}
    for n in names:
        if low.get(n):
            return low[n]
    return ""


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    import csv
    from pathlib import Path
    ap = argparse.ArgumentParser(prog="python -m leadgen dedup", description="Check a lead file against ALL THREE HubSpot portals (read-only).")
    ap.add_argument("csv"); ap.add_argument("--no-live", action="store_true", help="local index only (instant, last sync)")
    a = ap.parse_args(argv)
    rows = list(csv.DictReader(open(a.csv)))
    leads = [dict(company=_pick(r, "company", "name", "company name", "organization"), domain=_pick(r, "domain", "website", "company domain"),
                  email=_pick(r, "email", "work email"), phone=_pick(r, "mobile", "phone", "mobile phone"), linkedin=_pick(r, "linkedin url", "linkedin", "linkedin_url"))
             for r in rows]
    from leadgen import db as _db
    con = _db.connect()
    hits = check(leads, con=con, live=not a.no_live)
    blocked = 0
    for l in leads:
        h = hits.get(lead_key(l))
        if h:
            blocked += 1
            print("BLOCKED  %-40s %s" % ((l["company"] or l["domain"])[:40], explain(h)))
    print("\n%d leads | %d already have a deal in HubSpot (%s) | %d clear" % (len(leads), blocked, "local+live" if not a.no_live else "local only", len(leads) - blocked))
    return 0
