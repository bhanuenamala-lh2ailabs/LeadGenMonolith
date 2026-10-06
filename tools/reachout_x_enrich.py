#!/usr/bin/env python3
"""Find the X (Twitter) profile of each person in the 'Reachout list | LH2 AI' sheet that has a LinkedIn profile.
Apollo people/match by LinkedIn URL (one call per person, no phone reveal).  Approved by the user 2026-10-06 for this job only.
Writes out/reachout_x_links.csv (private).  Does not write to the shared sheet."""
import csv, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from leadgen.google import auth
from leadgen import apollo_ledger as L
from googleapiclient.discovery import build

SID = "1xuoBloYzQOg3j27UQC0t6l9AXjFw9IxAGknlbYNsgOo"
TABS = ["NVIDIA", "xAI", "OpenAI", "Amazon", "AlephA", "SurgeAI"]
OUT = ROOT / "out" / "reachout_x_links.csv"

def li_url(v):
    v = (v or "").strip()
    if not v:
        return ""
    if "linkedin.com" in v:
        return v if v.startswith("http") else "https://" + v
    return "https://www.linkedin.com/in/" + v.strip("/")

def main():
    creds = auth.user_credentials("companyops_sheets_token.json", ["https://www.googleapis.com/auth/spreadsheets"])
    svc = build("sheets", "v4", credentials=creds, cache_discovery=False)
    res = svc.spreadsheets().values().batchGet(spreadsheetId=SID, ranges=[f"'{t}'!A1:AA1000" for t in TABS]).execute()
    people = []
    for t, vr in zip(TABS, res["valueRanges"]):
        rows = vr.get("values", [])
        if not rows:
            continue
        hdr = [h.strip().lower() for h in rows[0]]
        ni = next((i for i, h in enumerate(hdr) if h == "name"), 0)
        li = next((i for i, h in enumerate(hdr) if "linkedin" in h), None)
        ci = next((i for i, h in enumerate(hdr) if h == "company"), None)
        di = next((i for i, h in enumerate(hdr) if h == "designation"), None)
        for r in rows[1:]:
            get = lambda i: (r[i] if i is not None and i < len(r) else "")
            url = li_url(get(li))
            if not url or not str(get(ni)).strip():
                continue
            people.append({"tab": t, "name": get(ni).strip(), "designation": get(di), "company": get(ci), "linkedin": url})
    print("people with LinkedIn:", len(people), flush=True)
    done = {}
    if OUT.exists():
        for r in csv.DictReader(open(OUT, encoding="utf-8")):
            done[r["linkedin"]] = r
    rows_out = list(done.values())
    for n, p in enumerate(people, 1):
        if p["linkedin"] in done:
            continue
        try:
            status, data, _ = L.call("POST", "/people/match", {"linkedin_url": p["linkedin"]},
                                     purpose="Reachout list X-link enrichment (user-approved 2026-10-06)",
                                     caller="tools/reachout_x_enrich.py", segment="Reachout list (AI labs)",
                                     request_key=p["linkedin"], allow=True)
            person = (data or {}).get("person") or {}
            x = person.get("twitter_url") or ""
            rec = dict(p, x_url=x, apollo_found=1 if person.get("id") else 0, status=status)
        except Exception as e:
            rec = dict(p, x_url="", apollo_found=0, status=type(e).__name__)
        rows_out.append(rec); done[p["linkedin"]] = rec
        if n % 25 == 0 or n == len(people):
            with open(OUT, "w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=["tab", "name", "designation", "company", "linkedin", "x_url", "apollo_found", "status"])
                w.writeheader(); w.writerows(rows_out)
            print(f"[{n}/{len(people)}] found X: {sum(1 for r in rows_out if r.get('x_url'))} | found person: {sum(1 for r in rows_out if str(r.get('apollo_found'))=='1')}", flush=True)
        time.sleep(0.2)
    print("done", flush=True)

if __name__ == "__main__":
    main()
