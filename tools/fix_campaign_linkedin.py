#!/usr/bin/env python3
"""Correct the LinkedIn URL on contacts already pushed from a campaign file.

Bug (fixed in load_campaign.py on 2026-10-05): the push wrote "https://www.linkedin.com/in/" + "in/<slug>", i.e. .../in/in/<slug>, which 404s.
For each pushed lead this reads the deal's MAIN contact, checks its first + last name against the stored lead, and sets linkedin_url to
"https://www.linkedin.com/" + person_linkedin_norm.  Dry run by default; --apply writes (contact PATCH only)."""
import argparse, sqlite3, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
import push_itsvc_leads as P  # noqa: E402

ap = argparse.ArgumentParser(); ap.add_argument("--slug", required=True); ap.add_argument("--apply", action="store_true"); a = ap.parse_args()
con = sqlite3.connect("file:%s?mode=ro" % (ROOT / "db/leadgen.sqlite"), uri=True); con.row_factory = sqlite3.Row
leads = con.execute("""SELECT l.lead_id, l.first_name, l.last_name, l.person_linkedin_norm, l.pushed_hs_deal_id FROM ext_campaign_lead l
                       JOIN ext_campaign c USING (campaign_id) WHERE c.slug=? AND l.pushed_hs_deal_id IS NOT NULL""", (a.slug,)).fetchall()
ok = bad = skipped = 0
for L in leads:
    want = "https://www.linkedin.com/" + L["person_linkedin_norm"]
    assoc = P.call("main", "POST", "/crm/v4/associations/deals/contacts/batch/read", json={"inputs": [{"id": L["pushed_hs_deal_id"]}]})
    cids = [t["toObjectId"] for r in assoc.get("results", []) for t in r.get("to", [])]
    if len(cids) != 1: skipped += 1; print("  lead %s: %d contacts on deal, skipped" % (L["lead_id"], len(cids))); continue
    p = P.call("main", "GET", "/crm/v3/objects/contacts/%s?properties=firstname,lastname,linkedin_url" % cids[0]).get("properties", {})
    name_ok = (p.get("firstname") or "").strip().lower() == (L["first_name"] or "").strip().lower() and (p.get("lastname") or "").strip().lower() == (L["last_name"] or "").strip().lower()
    if not name_ok: skipped += 1; print("  lead %s: contact name differs from the lead, skipped" % L["lead_id"]); continue
    if p.get("linkedin_url") == want: ok += 1; continue
    if a.apply: P.call("main", "PATCH", "/crm/v3/objects/contacts/%s" % cids[0], json={"properties": {"linkedin_url": want}})
    bad += 1
print("leads %d | already correct %d | to fix %d (%s) | skipped %d" % (len(leads), ok, bad, "fixed" if a.apply else "dry run", skipped))
