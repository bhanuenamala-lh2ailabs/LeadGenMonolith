"""The only way this repo calls Apollo.  Every call is written to ext_apollo_call (migration 0018) before the function returns.

    call(method, endpoint, body, *, purpose, caller, request_key=None) -> (status, json)

What it does:
  * refuses to run while APOLLO_PAUSED=1 in .env, unless allow=True is passed by the caller (the user's own go-ahead, per job)
  * reads Apollo's free credit meter (POST /usage_stats/credit_usage_stats) before and after the call, so credits_charged is a
    measured delta when the meter moves, and falls back to the documented rule (charge_basis='documented_rule') otherwise
  * records what came back (found, person id, org id, mobile returned yes/no, e-mail yes/no) but never the phone number or e-mail
  * one row per call, written even when the call fails (error = exception class name only)

It does not decide what to do with the result.  The caller records pushed_to / pushed_hs_id with mark_pushed() after a HubSpot write.
"""
import datetime as dt
import json
import re
import sqlite3
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
BASE = "https://api.apollo.io/api/v1"
DOCUMENTED_RULE = {  # Apollo's documented billing (docs/audit/apollo/MASTER_RECONCILIATION.md section 3)
    "/people/match": None,  # 1 if data found, +8 if a mobile is returned, 0 if nothing found: resolved after the response
    "/organizations/enrich": 1.0,
    "/mixed_companies/search": 1.0,  # per page
    "/mixed_people/api_search": 0.0,
}


class ApolloPaused(RuntimeError):
    pass


def _env():
    return dict(l.split("=", 1) for l in (ROOT / ".env").read_text().splitlines() if "=" in l and not l.startswith("#"))


def _meter(key):
    r = requests.post(BASE + "/usage_stats/credit_usage_stats", headers={"X-Api-Key": key, "Content-Type": "application/json"}, timeout=30)
    if not r.ok:
        return None
    lc = (r.json() or {}).get("lead_credit") or {}
    v = lc.get("left_over")
    return float(v) if v is not None else None


def _db():
    con = sqlite3.connect(str(ROOT / "db/leadgen.sqlite"))
    con.execute("PRAGMA foreign_keys=ON")
    return con


def _ist(now_utc):
    return (now_utc + dt.timedelta(hours=5, minutes=30)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]


def call(method, endpoint, body=None, *, purpose, caller, segment, request_key=None, allow=False):
    if not re.match(r"^/[a-z_/]+$", endpoint):
        raise ValueError("bad endpoint %r" % endpoint)
    if not purpose.strip() or not caller.strip() or not segment.strip():
        raise ValueError("purpose, caller and segment are required: every Apollo call must say why and for which segment")
    env = _env()
    if env.get("APOLLO_PAUSED") == "1" and not allow:
        raise ApolloPaused("Apollo is paused (APOLLO_PAUSED=1). Call with allow=True only for a job the user has approved.")
    key = env["APOLLO_API_KEY"]
    headers = {"X-Api-Key": key, "Content-Type": "application/json", "accept": "application/json"}
    now = dt.datetime.now(dt.timezone.utc)
    before = _meter(key)
    row = dict(called_at=now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z", called_at_ist=_ist(now), endpoint=endpoint, http_method=method,
               purpose=purpose, caller=caller, segment=segment, request_key=request_key, meter_before=before, error=None)
    status, data, err = None, {}, None
    try:
        r = requests.request(method, BASE + endpoint, headers=headers, json=body, timeout=60)
        status = r.status_code
        data = r.json() if r.content else {}
    except Exception as e:  # recorded below; the caller still gets the exception
        err = type(e).__name__
        raise
    finally:
        after = _meter(key)
        person = (data or {}).get("person") or {}
        org = (data or {}).get("organization") or {}
        found = 1 if (person.get("id") or org.get("id") or (data or {}).get("organizations") or (data or {}).get("people")) else 0
        mobile = 1 if any(p.get("type_cd") == "mobile" for p in (person.get("phone_numbers") or [])) or person.get("mobile_phone") else (0 if person else None)
        email = 1 if person.get("email") else (0 if person else None)
        if before is not None and after is not None and before != after:
            credits, basis = round(before - after, 2), "meter_delta"
        elif endpoint == "/people/match" and status == 200:
            credits, basis = (1.0 if found else 0.0), "documented_rule"  # Apollo: 1 if data found, 0 if nothing found
        elif endpoint in DOCUMENTED_RULE and DOCUMENTED_RULE[endpoint] is not None:
            credits, basis = DOCUMENTED_RULE[endpoint], "documented_rule"
        else:
            credits, basis = None, "unknown"
        row.update(http_status=status, result_found=found if status else None, person_id=person.get("id"), org_id=org.get("id"),
                   mobile_returned=mobile, email_returned=email, credits_charged=credits, charge_basis=basis, meter_after=after, error=err)
        con = _db()
        with con:
            con.execute("""INSERT INTO ext_apollo_call(called_at,called_at_ist,endpoint,http_method,purpose,caller,segment,request_key,http_status,result_found,
                           person_id,org_id,mobile_returned,email_returned,credits_charged,charge_basis,meter_before,meter_after,error)
                           VALUES (:called_at,:called_at_ist,:endpoint,:http_method,:purpose,:caller,:segment,:request_key,:http_status,:result_found,
                           :person_id,:org_id,:mobile_returned,:email_returned,:credits_charged,:charge_basis,:meter_before,:meter_after,:error)""", row)
            call_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        con.close()
    return status, data, call_id


def mark_outcome(apollo_call_id, outcome, reason=None, pushed_hs_id=None):
    """outcome: pushed_hubspot (pushed_hs_id = the HubSpot id created), discarded (reason required), held_local, no_result."""
    if outcome not in ("pushed_hubspot", "discarded", "held_local", "no_result"):
        raise ValueError("bad outcome %r" % outcome)
    if outcome == "discarded" and not reason:
        raise ValueError("a discarded result needs a reason")
    con = _db()
    with con:
        con.execute("UPDATE ext_apollo_call SET outcome=?, outcome_reason=?, pushed_hs_id=? WHERE apollo_call_id=?",
                    (outcome, reason, pushed_hs_id, apollo_call_id))
    con.close()
