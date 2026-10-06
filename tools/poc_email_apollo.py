#!/usr/bin/env python3
"""Look up the POC's work email in Apollo for the leads in data/audit/itsvc_plan.json (written by push_itsvc_leads.py).

User authorisation (2026-10-05): try Apollo/SignalHire for the POC email on these leads only; fine if none found.  SignalHire is at 0 credits.
This SPENDS Apollo lead credits (people/match = 1 credit when data is found), so it is measured: the free credit_usage_stats meter is read
before and after, a hard credit cap stops the run, and every result (including 'none') is saved so nothing is paid for twice.

    python tools/poc_email_apollo.py --pilot 5              # measure cost and hit rate on 5 leads
    python tools/poc_email_apollo.py --run --max-credits 300 # the rest, stops at the cap
Only a VERIFIED work email on the company's own domain is accepted; placeholders ('email_not_unlocked') are discarded."""
import argparse, json, sys, time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
AUDIT = ROOT / "data" / "audit"
env = dict(l.strip().split("=", 1) for l in (ROOT / ".env").read_text().splitlines() if "=" in l and not l.startswith("#"))
if env.get("APOLLO_PAUSED") == "1":
    sys.exit("Apollo is PAUSED (APOLLO_PAUSED=1 in .env, set 2026-10-05 by the user). Not calling Apollo. Remove the flag only when the user says go.")
H = {"x-api-key": env["APOLLO_API_KEY"], "Content-Type": "application/json", "Cache-Control": "no-cache", "User-Agent": "curl/8.4.0"}
BASE = "https://api.apollo.io/api/v1"


def consumed():
    r = requests.post(BASE + "/usage_stats/credit_usage_stats", headers=H, json={}, timeout=30)
    r.raise_for_status()
    s = r.json()["credit_usage_stats"]
    return s["lead_credit"]["consumed"], s["lead_credit"]["left_over"]


def lookup(lead):
    body = {"linkedin_url": lead["linkedin"], "first_name": lead["first"], "last_name": lead["last"], "domain": lead["domain"],
            "reveal_personal_emails": False, "reveal_phone_number": False}
    for attempt in range(4):
        import sys as _s
        _s.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from leadgen import apollo_ledger as _L
        st, data, _ = _L.call("POST", "/people/match", body, purpose="POC work e-mail (poc_email_apollo, user-approved)",
                              caller="tools/poc_email_apollo.py", segment="ITs POC e-mail", request_key=str(body.get("linkedin_url") or body.get("domain") or ""), allow=True)
        class _R: pass
        r = _R(); r.status_code = st; r.json = (lambda d=data: d)
        if r.status_code == 429:
            time.sleep(5 * (attempt + 1)); continue
        if r.status_code == 422:
            raise SystemExit("Apollo 422 (insufficient credits?): " + r.text[:200])
        r.raise_for_status()
        p = (r.json() or {}).get("person") or {}
        email, status = (p.get("email") or ""), (p.get("email_status") or "")
        ok = ("@" in email and "not_unlocked" not in email and status == "verified" and email.split("@")[1].lower().endswith(lead["domain"].lower().split("//")[-1].replace("www.", "")))
        return {"poc_email": email if ok else None, "status": status or "none", "source": "apollo", "returned_email_present": bool(email)}
    raise SystemExit("Apollo kept rate-limiting")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--pilot", type=int, default=0); ap.add_argument("--run", action="store_true"); ap.add_argument("--max-credits", type=int, default=300)
    a = ap.parse_args()
    plan = json.loads((AUDIT / "itsvc_plan.json").read_text())
    cache_f = AUDIT / "itsvc_emails.json"
    cache = json.loads(cache_f.read_text()) if cache_f.exists() else {}
    todo = [p for p in plan if p["domain"] not in cache]
    if a.pilot: todo = todo[:a.pilot]
    elif not a.run: sys.exit("use --pilot N or --run")
    before, left = consumed()
    print("Apollo lead credits before: consumed=%d left=%d | leads to look up: %d | cap %s" % (before, left, len(todo), a.max_credits))
    n = 0
    for lead in todo:
        now, _ = consumed() if n and n % 10 == 0 else (None, None)
        if now is not None and now - before >= a.max_credits:
            print("credit cap reached (%d): stopping" % (now - before)); break
        cache[lead["domain"]] = lookup(lead); n += 1
        cache_f.write_text(json.dumps(cache, indent=1)); cache_f.chmod(0o600)
        time.sleep(0.4)
    after, left2 = consumed()
    found = sum(1 for p in todo[:n] if cache[p["domain"]]["poc_email"])
    placeholder = sum(1 for p in todo[:n] if cache[p["domain"]]["returned_email_present"] and not cache[p["domain"]]["poc_email"])
    print("looked up %d | verified work email found: %d | email present but not verified/usable: %d | none: %d" % (n, found, placeholder, n - found - placeholder))
    print("credits spent (meter): %d  =  %.2f per lead | left now: %d" % (after - before, (after - before) / max(n, 1), left2))


if __name__ == "__main__":
    main()
