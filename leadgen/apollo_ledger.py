"""The only way this repo calls Apollo.  Every call is written to ext_apollo_call (migration 0018) before the function returns.

    call(method, endpoint, body, *, purpose, caller, request_key=None) -> (status, json)
    estimate_cost(endpoint, body=None) -> (min_credits, max_credits, note)   # pure local lookup, no network call

What it does:
  * refuses to run while APOLLO_PAUSED=1 in .env, unless allow=True is passed by the caller (the user's own go-ahead, per job)
  * reads Apollo's free credit meter (POST /usage_stats/credit_usage_stats) before and after the call, so credits_charged is a
    measured delta when the meter moves, and falls back to the documented rule (charge_basis='documented_rule') otherwise —
    the meter itself has its own daily rate limit (2000 calls/day, separate from lead-credit spend) and goes silent (429) when
    it's hit, which is why the documented rule below has to be right on its own, not just a fallback for when the meter lags
  * CREDIT_RULES is sourced from Apollo's own published pricing docs (fetched 2026-10-08, see the comment on each entry) —
    not inferred from observed behaviour, so estimate_cost() can tell a caller what a call will cost BEFORE it's made, with
    no network round-trip and no dependence on the meter being up
  * records what came back (found, person id, org id, mobile returned yes/no, e-mail yes/no) but never the phone number or e-mail
  * one row per call, written even when the call fails (error = exception class name only)

It does not decide what to do with the result.  The caller records pushed_to / pushed_hs_id with mark_pushed() after a HubSpot write.

HARD RULE (2026-10-10, do not relax this on any clone of this repo, on any laptop):
  Every single Apollo API call goes through call() in this file.  Never requests.get/post directly against
  api.apollo.io from any script, anywhere in this repo.  Reason: Apollo does NOT remember or discount a repeat
  reveal — confirmed live this session by deliberately re-revealing the exact same person_id twice in a row:
  it charged the full ~9 credits again, both times, same phone number returned.  There is no server-side
  caching to rely on.  ext_apollo_call (this file's own table) is therefore the ONLY thing standing between
  this team and silently re-paying for a person/org already revealed.  Before any new reveal, check
  ext_apollo_call (or apollo_person_id in ext_apollo_candidate_pool / ext_sourcing_result) for a prior
  successful call on the same person_id first.
  This applies identically on a teammate's laptop with a separate local leadgen.sqlite — the table only exists
  after `python -m leadgen migrate` has been run there too.  A fresh clone with no migration run yet has no
  ledger at all, so the very first thing to do on any new machine this repo gets pushed to is run the
  migrations, then confirm ext_apollo_call exists, before making a single Apollo call from it.
"""
import datetime as dt
import json
import re
import sqlite3
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
BASE = "https://api.apollo.io/api/v1"

# Sourced directly from Apollo's own docs (fetched 2026-10-08):
#   https://docs.apollo.io/docs/api-pricing                                  -> the per-endpoint credit table
#   https://docs.apollo.io/docs/retrieve-mobile-phone-numbers-for-contacts   -> the async phone-reveal flow
# Apollo's own pricing page explicitly flags phone-reveal cost as "illustrative" and plan-dependent, so where a call's
# true cost can't be known until the response (whether a mobile is actually on file), the range below is a worst-case
# bound — the real, final number is still corrected to a measured meter_delta in call() whenever the meter is reachable.
# Each entry: (min_credits, max_credits, per_unit, note). per_unit is what the max scales with ("call", "page", "person").
CREDIT_RULES = {
    "/people/match": (1.0, 9.0, "call",
        "People Enrichment: 1 credit if a person is found (0 if not), +8 more only if reveal_phone_number=true AND a "
        "mobile is actually on file. Source table: 'People enrichment 1-9 credits: 1 credit for demographics/email; "
        "+8 credits if mobile phone is returned.'"),
    "/people/bulk_match": (1.0, 9.0, "person",
        "Bulk People Enrichment: same per-person rule as /people/match, up to 10 people per call — cost scales with "
        "how many of the batch are found/revealed, not a flat per-call fee."),
    "/organizations/enrich": (1.0, 1.0, "call", "Organization Enrichment: flat 1 credit per organization."),
    "/organizations/bulk_enrich": (1.0, 1.0, "org", "Bulk Organization Enrichment: flat 1 credit per organization."),
    "/mixed_companies/search": (1.0, 1.0, "page", "Organization Search: 1 credit per page requested (up to 100 results/page) — this is a SEARCH call (q_organization_name etc.), not an enrichment; cost is exactly the page count you ask for, known before calling."),
    "/mixed_people/api_search": (0.0, 0.0, "call", "People Search: free — returns a results LIST, not enriched contact data; no credits until a specific person is enriched via /people/match."),
    "/organizations/job_postings": (1.0, 1.0, "page", "Organization Job Postings: 1 credit per page (up to 10,000 results/page)."),
    "/news_articles/search": (1.0, 1.0, "page", "News Articles Search: 1 credit per page (up to 25 results/page)."),
    "/people": (1.0, 1.0, "call", "Get complete person info: flat 1 credit per person."),
    "/organizations": (1.0, 1.0, "call", "Get complete organization info: flat 1 credit per company."),
    # /webhook_result/{request_id} (the async phone-reveal poll) is OUR OWN discovered mechanism, not an officially
    # documented endpoint — Apollo's docs say phone reveal is webhook-push-only ("you cannot poll"), but polling this
    # path has worked reliably in practice all session. It costs nothing to call (you're reading a job you already
    # paid to start at /people/match) — the +8 mobile-reveal credit, if any, was already incurred by that kickoff
    # call, this just reveals whether it hit. Not in CREDIT_RULES (its name isn't a fixed string, see cost_of() below);
    # treated as (0.0, 0.0, "call", ...) directly in cost_of().
}


def cost_of(endpoint, body=None):
    """Pure local lookup — the (min, max, note) credit bound for a call that hasn't been made yet. No network call."""
    body = body or {}
    if endpoint.startswith("/webhook_result/"):
        return (0.0, 0.0, "Polling an async reveal job you already paid for at /people/match — the poll itself is free; "
                           "the +8 (if any) was already incurred at the kickoff call, not here.")
    rule = CREDIT_RULES.get(endpoint)
    if rule is None:
        return (None, None, "no documented rule for %r — treat as unknown, do NOT assume free" % endpoint)
    mn, mx, per_unit, note = rule
    if endpoint == "/people/match" and not body.get("reveal_phone_number"):
        return (0.0, 1.0, note + " (reveal_phone_number not set on this call, so the +8 branch cannot apply — ceiling is 1.)")
    if per_unit == "page":
        pages = int(body.get("page") or 1)
        return (mn * pages, mx * pages, note + (" (x%d page(s) requested)" % pages if pages != 1 else ""))
    return (mn, mx, note)


def estimate_cost(endpoint, body=None):
    """Public entry point: what will this call cost, BEFORE it's made? Returns (min_credits, max_credits, note).
    Use this to budget a batch ahead of time: sum max_credits across every planned call for a hard worst-case cap."""
    return cost_of(endpoint, body)


class ApolloPaused(RuntimeError):
    pass


def _env():
    return dict(l.split("=", 1) for l in (ROOT / ".env").read_text().splitlines() if "=" in l and not l.startswith("#"))


def _meter(key):
    r = requests.post(BASE + "/usage_stats/credit_usage_stats", headers={"X-Api-Key": key, "Content-Type": "application/json"}, timeout=30)
    if not r.ok:
        return None
    # Real shape is {"credit_usage_stats": {"lead_credit": {...}}} - the old code read .get("lead_credit") off the
    # TOP-LEVEL body (missing the "credit_usage_stats" wrapper), so it always silently returned None. That meant
    # every credit figure logged all session came from the documented-rule estimate table (call()'s elif chain),
    # never from an actual before/after balance delta - found 2026-10-10 while running a user-requested balance check.
    lc = ((r.json() or {}).get("credit_usage_stats") or {}).get("lead_credit") or {}
    v = lc.get("left_over")
    return float(v) if v is not None else None


def _db():
    con = sqlite3.connect(str(ROOT / "db/leadgen.sqlite"))
    con.execute("PRAGMA foreign_keys=ON")
    return con


def _ist(now_utc):
    return (now_utc + dt.timedelta(hours=5, minutes=30)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]


def call(method, endpoint, body=None, *, purpose, caller, segment, request_key=None, allow=False):
    if not re.match(r"^/[a-z0-9_/-]+$", endpoint):  # digits and '-' allowed: /webhook_result/{request_id} ids can be large signed integers
        raise ValueError("bad endpoint %r" % endpoint)
    if not purpose.strip() or not caller.strip() or not segment.strip():
        raise ValueError("purpose, caller and segment are required: every Apollo call must say why and for which segment")
    env = _env()
    if env.get("APOLLO_PAUSED") == "1" and not allow:
        raise ApolloPaused("Apollo is paused (APOLLO_PAUSED=1). Call with allow=True only for a job the user has approved.")
    key = env["APOLLO_API_KEY"]
    headers = {"X-Api-Key": key, "Content-Type": "application/json", "accept": "application/json"}
    est_min, est_max, est_note = cost_of(endpoint, body)  # known BEFORE the request fires — pure local lookup, no network call
    print("[apollo_ledger] about to call %s — documented cost %s-%s credits (%s)" % (endpoint, est_min, est_max, est_note))
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
        # /webhook_result/{request_id} (the async phone-reveal poll) wraps its person in
        # {"webhook_result": {"people": [...]}} , not the {"person": {...}} shape every other endpoint uses.
        # Without this fallback, mobile_returned/email_returned/result_found stay blank for every async reveal,
        # even a successful one — the credits still get logged right (meter_delta doesn't depend on this), but
        # the ledger becomes unable to answer "which calls actually found a mobile".
        webhook_people = ((data or {}).get("webhook_result") or {}).get("people") or []
        person = (data or {}).get("person") or (webhook_people[0] if webhook_people else {}) or {}
        org = (data or {}).get("organization") or {}
        found = 1 if (person.get("id") or org.get("id") or (data or {}).get("organizations") or (data or {}).get("people") or webhook_people) else 0
        mobile = 1 if any(p.get("type_cd") == "mobile" for p in (person.get("phone_numbers") or [])) or person.get("mobile_phone") else (0 if person else None)
        email = 1 if person.get("email") else (0 if person else None)
        # A negative delta (after > before) is a measurement artifact, not a real refund - Apollo's own
        # /usage_stats/credit_usage_stats read has occasional read-lag under rapid sequential calls, and can
        # return a stale "after" that's higher than a fresher "before". Found live 2026-10-11 running 174
        # sequential reveals: it crashed the CHECK (credits_charged >= 0) constraint outright. Don't trust a
        # negative delta - fall back to documented_rule, same as when the meter is unavailable entirely.
        if before is not None and after is not None and before != after and before > after:
            credits, basis = round(before - after, 2), "meter_delta"
        elif endpoint == "/people/match" and status == 200:
            credits, basis = (1.0 if found else 0.0), "documented_rule"  # Apollo: 1 if data found, 0 if nothing found
        elif endpoint.startswith("/webhook_result/") and status == 200:
            # The async phone-reveal poll itself is free to call (you're just reading a job you already paid to
            # start) — but this is the ONLY place the ledger learns whether that earlier /people/match reveal
            # kickoff actually surfaced a mobile. Apollo's documented rule charges +8 on top of the base 1-credit
            # match for a confirmed mobile; attribute it here, at the poll that confirms it, rather than trying to
            # predict it at kickoff time. 0 if the job resolved with no mobile on file (person still found).
            credits, basis = (8.0 if mobile else 0.0), "documented_rule"
        elif endpoint in CREDIT_RULES and status == 200:
            # Same sourced table estimate_cost() uses pre-call — for every endpoint here min==max except /people/match
            # (handled above), so this is a single deterministic number, not a range: org enrich, org search (xN pages),
            # people search. Keeps the post-call "actual" and the pre-call "estimate" permanently in sync — one table.
            _, mx, _note = cost_of(endpoint, body)
            credits, basis = mx, "documented_rule"
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
