"""HubSpot sync tests: read-only guard, retry / pagination / rate limit (fake session, no network), sync into a throwaway database,
canonicalize idempotency, stage-pipeline resolution, strong-key company linking, phone gate.

Run:  mkdir -p db/_scratch && .venv/bin/python -m pytest -q tests --basetemp=db/_scratch/pytest
Nothing here touches the real database or the network.
"""
import json
import os

import pytest

from leadgen import db
from leadgen.hubspot import canonicalize as cz
from leadgen.hubspot import sync as hs
from leadgen.hubspot.client import (HubSpotClient, HubSpotError, MissingScope, RateLimiter, ReadOnlyViolation, assert_read_only)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL_DB = os.path.join(ROOT, "db", "leadgen.sqlite")
SECRET = "pat-na2-FAKE-TEST-TOKEN-not-real"


# ------------------------------------------------------------------------------------------------ fakes
class Resp:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.headers = headers or {}
        self.content = json.dumps(self._body).encode()

    def json(self):
        return self._body


class FakeSession:
    """Plays back a list of responses (or callables) and records every request."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        r = self.responses.pop(0)
        return r(method, url, kw) if callable(r) else r


def mk_client(responses, **kw):
    sleeps = []
    kw.setdefault("sleep", sleeps.append)
    c = HubSpotClient("rat", token=SECRET, session=FakeSession(responses), **kw)
    c.sleeps = sleeps
    return c


# ------------------------------------------------------------------------------------------------ the read-only guard
@pytest.mark.parametrize("method,path", [
    ("GET", "/crm/v3/objects/deals"), ("GET", "/crm/v3/pipelines/deals"), ("get", "/account-info/v3/details"),
    ("POST", "/crm/v3/objects/deals/search"), ("POST", "/crm/v3/objects/contacts/search"),
    ("POST", "/crm/v3/objects/deals/batch/read"), ("POST", "/crm/v4/associations/deals/contacts/batch/read"),
])
def test_guard_allows_reads(method, path):
    assert_read_only(method, path)


@pytest.mark.parametrize("method,path", [
    ("POST", "/crm/v3/objects/deals"), ("POST", "/crm/v3/objects/deals/batch/create"), ("POST", "/crm/v3/objects/deals/batch/update"),
    ("POST", "/crm/v3/objects/deals/batch/archive"), ("POST", "/crm/v3/objects/deals/merge"), ("POST", "/crm/v4/associations/deals/contacts/batch/create"),
    ("POST", "/crm/v4/associations/deals/contacts/batch/archive"), ("POST", "/crm/v3/pipelines/deals"),
    ("PATCH", "/crm/v3/objects/deals/1"), ("PUT", "/crm/v4/objects/deals/1/associations/contacts/2"), ("DELETE", "/crm/v3/objects/deals/1"),
    ("DELETE", "/crm/v3/objects/deals/search"), ("PATCH", "/crm/v3/objects/deals/search"), ("PUT", "/crm/v3/objects/deals/batch/read"),
    ("POST", "/crm/v3/objects/deals/search/../1"), ("POST", "https://api.hubapi.com/crm/v3/objects/deals/search"), ("GET", "https://evil.example/x"),
    ("POST", "crm/v3/objects/deals/search"), ("HEAD", "/crm/v3/objects/deals"), ("POST", "/crm/v3/objects/deals/search/extra"),
    ("POST", "/crm/v3/objects/deals/1/search"),
])
def test_guard_blocks_writes(method, path):
    with pytest.raises(ReadOnlyViolation):
        assert_read_only(method, path)


def test_guard_raises_before_anything_is_sent():
    c = mk_client([])
    for m, p in (("PATCH", "/crm/v3/objects/deals/1"), ("DELETE", "/crm/v3/objects/deals/1"), ("POST", "/crm/v3/objects/deals")):
        with pytest.raises(ReadOnlyViolation):
            c.request(m, p, json_body={"properties": {"dealname": "x"}})
    with pytest.raises(ReadOnlyViolation):
        c.post_read("/crm/v3/objects/deals/batch/create", {})
    assert c.session_calls == [] if hasattr(c, "session_calls") else c._session.calls == []
    assert c.calls == 0


def test_no_write_verb_anywhere_in_the_package():
    """Belt and braces: the package source never mentions a write verb call."""
    import re
    pkg = os.path.join(ROOT, "leadgen", "hubspot")
    for fn in ("client.py", "sync.py", "canonicalize.py"):
        src = open(os.path.join(pkg, fn), encoding="utf-8").read()
        assert not re.search(r"\.(patch|put|delete)\(", src), fn
        assert not re.search(r"""request\(\s*["'](PATCH|PUT|DELETE)""", src), fn


# ------------------------------------------------------------------------------------------------ client behaviour
def test_bearer_header_and_no_secret_in_errors():
    c = mk_client([Resp(200, {"ok": 1})])
    assert c.get("/account-info/v3/details") == {"ok": 1}
    method, url, kw = c._session.calls[0]
    assert method == "GET" and url == "https://api.hubapi.com/account-info/v3/details"
    assert kw["headers"]["Authorization"] == "Bearer " + SECRET
    c2 = mk_client([Resp(400, {"category": "VALIDATION_ERROR", "message": "bad " + SECRET})])
    with pytest.raises(HubSpotError) as ei:
        c2.get("/crm/v3/objects/deals")
    assert SECRET not in str(ei.value)


def test_retry_429_honours_retry_after_and_5xx_backs_off():
    c = mk_client([Resp(429, {}, {"Retry-After": "7"}), Resp(502), Resp(503), Resp(200, {"x": 1})], backoff_base=1.0)
    assert c.get("/crm/v3/objects/deals") == {"x": 1}
    assert c.retries == 3 and c.calls == 4
    assert c.sleeps[0] >= 7                                  # Retry-After honoured
    assert c.sleeps[1] == 2.0 and c.sleeps[2] == 4.0         # exponential backoff 1, 2, 4 (attempt 1 uses max(1*2^1, none))


def test_retry_gives_up():
    c = mk_client([Resp(500)] * 3, max_retries=2)
    with pytest.raises(HubSpotError) as ei:
        c.get("/crm/v3/objects/deals")
    assert ei.value.status == 500 and c.calls == 3


def test_missing_scope_is_typed():
    c = mk_client([Resp(403, {"category": "MISSING_SCOPES", "message": "This app hasn't been granted all required scopes"})])
    with pytest.raises(MissingScope):
        c.get("/crm/v3/objects/emails")


def test_pagination_follows_after():
    pages = [Resp(200, {"results": [{"id": "1"}, {"id": "2"}], "paging": {"next": {"after": "2"}}}),
             Resp(200, {"results": [{"id": "3"}]})]
    c = mk_client(pages)
    got = [[r["id"] for r in p] for p in c.paginate_get("/crm/v3/objects/deals", {"archived": "false"}, limit=2)]
    assert got == [["1", "2"], ["3"]]
    assert c._session.calls[1][2]["params"]["after"] == "2" and c._session.calls[0][2]["params"]["limit"] == 2


def test_rate_limiter_100_per_10s():
    t = [0.0]
    sl = []

    def sleep(d):
        sl.append(d)
        t[0] += d
    rl = RateLimiter(100, 10.0, clock=lambda: t[0], sleep=sleep)
    for _ in range(100):
        assert rl.acquire() == 0
    assert sl == []
    assert rl.acquire() > 9.9                                # the 101st call waits for the window to roll
    assert len(sl) >= 1


def test_search_windowed_splits_above_cap():
    totals = {}

    def search(method, url, kw):
        body = kw["json"]
        flt = body["filterGroups"][0]["filters"]
        lo, hi = int(flt[0]["value"]), int(flt[1]["value"])
        if body["limit"] == 1:
            return Resp(200, {"total": 20000 if hi - lo > 25 else 3, "results": []})
        totals[(lo, hi)] = totals.get((lo, hi), 0) + 1
        return Resp(200, {"results": [{"id": "%d" % lo}], "paging": {}})
    c = mk_client([search] * 200)
    ids = [r["id"] for p in c.search_windowed("deals", "hs_lastmodifieddate", 0, 100, ["x"]) for r in p]
    assert ids == ["0", "25", "50", "75"] and all(hi - lo <= 25 for lo, hi in totals)


def test_batch_read_associations_shape():
    c = mk_client([Resp(200, {"results": [{"from": {"id": "1"}, "to": [{"toObjectId": 9, "associationTypes": [{"category": "HUBSPOT_DEFINED", "typeId": 5, "label": "Primary"}]}]}]})])
    out = c.batch_read_associations("deals", "companies", ["1", "2"])
    assert out == {"1": [{"id": "9", "types": [{"category": "HUBSPOT_DEFINED", "typeId": 5, "label": "Primary"}]}]}
    assert c._session.calls[0][0] == "POST" and c._session.calls[0][1].endswith("/crm/v4/associations/deals/companies/batch/read")


# ------------------------------------------------------------------------------------------------ database fixtures
@pytest.fixture
def con(tmp_path):
    p = os.path.abspath(str(tmp_path / "t.sqlite"))
    assert p != REAL_DB
    db.migrate(p)
    c = db.connect(p)
    yield c
    c.close()


def pipeline_json(pid, label, stages):
    return {"id": pid, "label": label, "displayOrder": 0, "archived": False, "createdAt": "2026-01-01T00:00:00.000Z",
            "stages": [{"id": sid, "label": sl, "displayOrder": i, "archived": False, "metadata": {"isClosed": "true" if closed else "false", "probability": "0.5"}}
                       for i, (sid, sl, closed) in enumerate(stages)]}


class RunStub:
    run_id = None


def raw(con, account, object_type, hs_id, props, assoc=None, history=None, archived=False, created="2026-09-01T00:00:00.000Z", updated="2026-09-02T00:00:00.000Z"):
    run = hs.Run(con, account, {})
    with db.transaction(con):
        r = hs.upsert_raw(con, run, account, object_type, hs_id, props, associations=assoc, history=history, archived=archived, created=created, updated=updated)
    run.finish("succeeded")
    return r


def H(value, t, src="CRM_UI", uid=None):
    e = {"value": value, "timestamp": t, "sourceType": src, "sourceId": "x"}
    if uid:
        e["updatedByUserId"] = uid
    return e


STAGE_CFG = {"version": 1, "pipelines": [
    {"account": "main", "pipeline_id": "default", "stages": [
        {"id": "10", "label": "Cold Call", "canonical": "SOURCED", "kind": "live"},
        {"id": "20", "label": "Interested", "canonical": "INTERESTED", "kind": "live"},
        {"id": "30", "label": "Dead", "canonical": "DEAD", "kind": "dead", "dead_reason": "NOT_INTERESTED", "depth_reached": 2}]},
    {"account": "main", "pipeline_id": "2425754306", "stages": [
        {"id": "20", "label": "Interested", "canonical": "INTERESTED", "kind": "live"},
        {"id": "40", "label": "Cold Lead", "canonical": "SOURCED", "kind": "live"}]}],
    "deleted_stages": [], "label_aliases": []}


def seed_main(con):
    raw(con, "main", "pipelines", "default", pipeline_json("default", "Coding", [("10", "Cold Call", False), ("20", "Interested", False), ("30", "Dead", True)]))
    raw(con, "main", "pipelines", "2425754306", pipeline_json("2425754306", "CoOps ( Global )", [("40", "Cold Lead", False), ("20", "Interested", False)]))
    raw(con, "main", "owners", "111", {"id": "111", "email": "A@lh2.ai", "firstName": "Ann", "lastName": "Lee", "userId": 111, "archived": False})
    raw(con, "main", "owners", "222", {"id": "222", "email": "b@lh2.ai", "firstName": "Bo", "lastName": None, "userId": 222, "archived": True})


def canon(con, account="main", cfg=STAGE_CFG, all_rows=False):
    c = cz.Canon(account, con, all_rows=all_rows, log=lambda m: None)
    c.stage_map_cfg = cfg
    return c.run()


def changed(res):
    return {k: {a: n for a, n in v.items() if a in ("inserted", "updated", "deleted") and n} for k, v in res["stats"].items()
            if any(v.get(a) for a in ("inserted", "updated", "deleted"))}


def total_changes(res):
    return sum(sum(v.values()) for v in changed(res).values())


# ------------------------------------------------------------------------------------------------ hsraw
def test_upsert_raw_unchanged_changed_and_revision(con):
    assert raw(con, "rat", "companies", "5", {"name": "A"}) == "inserted"
    assert raw(con, "rat", "companies", "5", {"name": "A"}) == "unchanged"
    assert raw(con, "rat", "companies", "5", {"name": "B"}) == "updated"
    assert con.execute("SELECT COUNT(*) FROM hsraw_object_revision").fetchone()[0] == 1
    # KEEP preserves associations / history written by another pass
    run = hs.Run(con, "rat", {})
    with db.transaction(con):
        hs.upsert_raw(con, run, "rat", "deals", "9", {"dealname": "d"}, associations=None, history={"dealstage": []})
        hs.set_associations(con, run, "rat", "deals", "9", {"contacts": [{"id": "1", "types": []}], "companies": []})
        assert hs.upsert_raw(con, run, "rat", "deals", "9", {"dealname": "d"}, associations=hs.KEEP, history={"dealstage": []}) == "unchanged"
    run.finish("succeeded")


def test_run_counts_and_sync_state(con):
    run = hs.Run(con, "rat", {"full": True})
    run.bump("deals", "read", 3)
    run.bump("deals", "inserted", 3)
    hs.set_cursor(con, "rat", "deals", "2026-10-04T00:00:00.000Z", 3, run.run_id)
    run.finish("succeeded")
    r = con.execute("SELECT status, rows_read, rows_written, kind FROM ops_import_run WHERE import_run_id=?", (run.run_id,)).fetchone()
    assert tuple(r) == ("succeeded", 3, 3, "hubspot_sync")
    assert hs.get_cursor(con, "rat", "deals") == "2026-10-04T00:00:00.000Z"


class FakeSyncClient:
    """Stands in for HubSpotClient inside AccountSync: serves canned pages; refuses emails with a missing scope."""

    def __init__(self, portal):
        self.portal = portal
        self.calls = 0
        self.retries = 0
        self.log = None

    def get(self, path, params=None):
        if path == "/account-info/v3/details":
            return {"portalId": self.portal, "timeZone": "US/Eastern"}
        if path == "/crm/v3/pipelines/deals":
            return {"results": [pipeline_json("2575252183", "Rapid Action Team", [("1", "Cold Called Assigned", False), ("2", "Closed / Won", True)])]}
        if path.startswith("/crm/v3/properties/"):
            return {"results": [{"name": "dealname", "hubspotDefined": True}, {"name": "lead_category", "hubspotDefined": False}]}
        raise AssertionError(path)

    def paginate_get(self, path, params=None, limit=100):
        if path == "/crm/v3/owners":
            if params.get("archived") == "false":
                yield [{"id": "9", "email": "x@lh2.ai", "firstName": "X", "lastName": "Y", "userId": 9, "archived": False}]
            return
        if path.endswith("/emails"):
            from leadgen.hubspot.client import MissingScope as MS
            raise MS("HTTP 403 [MISSING_SCOPES]", 403, path, "MISSING_SCOPES")
        if path.endswith("/deals") and params.get("archived") == "false":
            yield [{"id": "100", "properties": {"dealname": "Acme", "dealstage": "1", "pipeline": "2575252183", "lead_category": "CAD", "hubspot_owner_id": "9"},
                    "propertiesWithHistory": {"dealstage": [H("1", "2026-09-23T10:00:00.000Z", "INTEGRATION")], "pipeline": [H("2575252183", "2026-09-23T10:00:00.000Z", "INTEGRATION")]},
                    "createdAt": "2026-09-23T10:00:00.000Z", "updatedAt": "2026-09-23T10:00:01.000Z", "archived": False}]
        return
        yield

    def search_total(self, ot, filters=None):
        return {"deals": 1}.get(ot, 0)

    def batch_read_associations(self, f, t, ids, per_call=100):
        return {"100": [{"id": "5", "types": [{"category": "HUBSPOT_DEFINED", "typeId": 3, "label": None}]}]} if (f, t) == ("deals", "contacts") else {}


def test_account_sync_end_to_end_with_fake_client(con):
    s = hs.AccountSync("rat", con, client=FakeSyncClient(247485022), full=True, objects=["account", "owners", "pipelines", "properties", "deals", "notes", "emails", "assoc"],
                       log=lambda m: None)
    res = s.run_all()
    assert res["status"] == "succeeded" and res["scope_missing"] == ["emails"]
    assert con.execute("SELECT COUNT(*) FROM hsraw_object WHERE account_id='rat' AND object_type='deals'").fetchone()[0] == 1
    row = con.execute("SELECT associations_json, history_json, properties_json FROM hsraw_object WHERE object_type='deals'").fetchone()
    assert json.loads(row["associations_json"])["contacts"][0]["id"] == "5"
    assert "dealstage" in json.loads(row["history_json"])
    assert con.execute("SELECT status, rows_read FROM ops_import_run WHERE kind='hubspot_sync' ORDER BY import_run_id DESC").fetchone()[0] == "succeeded"
    assert con.execute("SELECT last_status FROM ops_sync_state WHERE object_type='deals'").fetchone()[0] == "ok"
    dqr = con.execute("SELECT rule_code, severity, entity_ref FROM ops_dq_issue WHERE fingerprint='hubspot_scope_missing|rat|emails'").fetchone()
    assert tuple(dqr) == ("hubspot_scope_missing", "warn", "emails")
    # a second full run changes no payload
    res2 = hs.AccountSync("rat", con, client=FakeSyncClient(247485022), full=True, objects=["deals"], log=lambda m: None).run_all()
    assert res2["counts"]["deals"]["unchanged"] == 1 and res2["counts"]["deals"].get("updated", 0) == 0


def test_portal_mismatch_aborts(con):
    with pytest.raises(RuntimeError):
        hs.AccountSync("rat", con, client=FakeSyncClient(999), full=True, objects=["account"], log=lambda m: None).run_all()
    assert con.execute("SELECT status FROM ops_import_run ORDER BY import_run_id DESC").fetchone()[0] == "failed"


# ------------------------------------------------------------------------------------------------ canonicalize
def test_canonicalize_world_and_idempotency(con):
    seed_main(con)
    raw(con, "main", "properties", "deals", {"results": [{"name": "dealname", "hubspotDefined": True}, {"name": "lead_source", "hubspotDefined": False}]})
    raw(con, "main", "companies", "900", {"name": "Acme Ltd", "domain": "acme.com", "country": "India", "phone": "+91 98123 45678"})
    raw(con, "main", "companies", "901", {"name": "Gmail Co", "domain": "gmail.com"})
    raw(con, "main", "companies", "902", {"name": "Gmail Co", "domain": "gmail.com"})
    raw(con, "main", "contacts", "700", {"firstname": "Ann", "lastname": "Roe", "email": "ANN@Acme.com", "phone": "+91 98123 45678", "mobilephone": "08042453955",
                                         "associatedcompanyid": "900", "hs_linkedin_url": "https://www.linkedin.com/in/Ann-Roe/"})
    hist = {"dealstage": [H("20", "2026-09-10T10:00:00.000Z", "CRM_UI", 111), H("40", "2026-09-05T10:00:00.000Z", "INTEGRATION"), H("20", "2026-09-04T10:00:00.000Z", "CRM_UI", 111),
                          H("10", "2026-09-01T10:00:00.000Z", "INTEGRATION")],
            "pipeline": [H("2425754306", "2026-09-05T09:59:59.000Z", "API"), H("default", "2026-09-01T10:00:00.000Z", "INTEGRATION")],
            "hubspot_owner_id": [H("111", "2026-09-02T00:00:00.000Z", "CRM_UI", 111)]}
    raw(con, "main", "deals", "5000", {"dealname": "Acme", "dealstage": "20", "pipeline": "2425754306", "hubspot_owner_id": "111", "lead_source": "Scraping Algo ( IT services )", "cost": "12.5"},
        assoc={"contacts": [{"id": "700", "types": [{"typeId": 3, "label": None}]}], "companies": [{"id": "900", "types": [{"typeId": 5, "label": "Primary"}, {"typeId": 341, "label": None}]}]},
        history=hist)
    res = canon(con)
    assert res["unmapped"] == []
    # owner / stage / mapping
    assert con.execute("SELECT display_name, email_norm, hs_user_id FROM owner WHERE hs_owner_id='111'").fetchone()[:] == ("Ann Lee", "a@lh2.ai", "111")
    assert con.execute("SELECT COUNT(*) FROM stage_map WHERE account_id='main'").fetchone()[0] == 5
    # events: 10 (default) -> 20 resolved by pipeline history to default, 40 unique -> CoOps, 20 again -> CoOps (history says so)
    ev = [tuple(r) for r in con.execute("SELECT entered_at, pipeline_id, to_stage_id, from_pipeline_id, from_stage_id, pipeline_basis, source_type, is_human, ist_day FROM deal_stage_event ORDER BY entered_at")]
    assert [(e[1], e[2], e[5]) for e in ev] == [("default", "10", "unique_stage_id"), ("default", "20", "pipeline_history"),
                                                ("2425754306", "40", "unique_stage_id"), ("2425754306", "20", "pipeline_history")]
    assert ev[1][3:5] == ("default", "10") and ev[0][3:5] == (None, None) and ev[3][3:5] == ("2425754306", "40")
    assert [e[7] for e in ev] == [0, 1, 0, 1] and ev[0][8] == "2026-09-01"
    assert con.execute("SELECT actor_owner_id IS NOT NULL FROM deal_stage_event WHERE is_human=1 LIMIT 1").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM deal_owner_event").fetchone()[0] == 1
    # deal
    d = con.execute("SELECT pipeline_id, stage_id, cost_usd, company_id IS NOT NULL AS has_co, segment FROM deal").fetchone()
    assert tuple(d) == ("2425754306", "20", 12.5, 1, None)
    assert con.execute("SELECT COUNT(*) FROM deal_contact").fetchone()[0] == 1
    assert con.execute("SELECT is_primary FROM deal_company").fetchone()[0] == 1
    assert con.execute("SELECT label FROM lead_source").fetchone()[0] == "Scraping Algo ( IT services )"
    # contact + phones + gate
    c = con.execute("SELECT email_norm, linkedin_person_norm, company_id IS NOT NULL FROM contact").fetchone()
    assert tuple(c) == ("ann@acme.com", "in/ann-roe", 1)
    ph = {r["phone_e164"]: (r["phone_type"], r["is_indian_mobile"], r["country_iso2"]) for r in con.execute("SELECT * FROM contact_phone")}
    assert ph["+919812345678"] == ("mobile", 1, "IN") and ph["+918042453955"] == ("landline", 0, "IN")     # 80 = Bangalore landline (phonenumbers classifies it exactly): gate stays closed
    assert con.execute("SELECT COUNT(*) FROM v_contact_mobile_gate WHERE has_indian_mobile=1").fetchone()[0] == 1
    # companies: free-mail domains never link; name-only twin -> merge_candidate, not a merge
    assert con.execute("SELECT COUNT(*) FROM company WHERE merged_into_company_id IS NULL").fetchone()[0] == 3
    assert con.execute("SELECT COUNT(*) FROM merge_candidate WHERE match_kind='name_norm'").fetchone()[0] == 1
    assert con.execute("SELECT is_strong FROM company_identifier WHERE value_norm='gmail.com' LIMIT 1").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM company_identifier WHERE identifier_type='hs_company'").fetchone()[0] == 3
    assert con.execute("SELECT COUNT(*) FROM v_origin_coverage WHERE rows_without_origin > 0").fetchone()[0] == 0
    # idempotent: nothing changes the second time (neither dirty-only nor --all)
    assert total_changes(canon(con)) == 0
    r2 = canon(con, all_rows=True)
    assert total_changes(r2) == 0, changed(r2)
    assert con.execute("SELECT COUNT(*) FROM hsraw_object WHERE canonical_payload_sha256 IS NOT payload_sha256").fetchone()[0] == 0


def test_deleted_stage_becomes_tombstone_with_alias_and_mapping(con):
    seed_main(con)
    cfg = {"version": 1, "pipelines": STAGE_CFG["pipelines"], "label_aliases": [],
           "deleted_stages": [{"account": "main", "pipeline_id": "default", "id": "99", "label": "Old cold call (deleted)", "alias_of": "10", "deleted_on": "2026-09-15"}]}
    hist = {"dealstage": [H("10", "2026-09-02T10:00:00.000Z"), H("99", "2026-09-01T10:00:00.000Z", "INTEGRATION"), H("77", "2026-08-31T10:00:00.000Z", "INTEGRATION")],
            "pipeline": [H("default", "2026-08-31T10:00:00.000Z", "INTEGRATION")]}
    raw(con, "main", "deals", "5001", {"dealname": "Old", "dealstage": "10", "pipeline": "default"}, assoc={"contacts": [], "companies": []}, history=hist)
    res = canon(con, cfg=cfg)
    t = {r["stage_id"]: (r["is_deleted"], r["label_source"], r["label"]) for r in con.execute("SELECT * FROM stage WHERE pipeline_id='default'")}
    assert t["99"] == (1, "alias_seed", "Old cold call (deleted)") and t["77"][:2] == (1, "history")
    assert con.execute("SELECT stage_id FROM stage_alias WHERE alias_value='99'").fetchone()[0] == "10"
    # 99 inherits the mapping through the alias; 77 is unknown history-only => reported as unmapped, loudly
    assert [u["stage_id"] for u in res["unmapped"]] == ["77"]
    assert con.execute("SELECT severity FROM ops_dq_issue WHERE fingerprint='unmapped_stage|main|default|77'").fetchone()[0] == "error"
    assert con.execute("SELECT COUNT(*) FROM deal_stage_event WHERE to_stage_id IN ('99','77')").fetchone()[0] == 2
    assert con.execute("SELECT canonical_code FROM v_stage_effective WHERE stage_id='99'").fetchone()[0] == "SOURCED"
    assert total_changes(canon(con, cfg=cfg)) == 0


def test_event_without_resolvable_pipeline_is_skipped_with_dq(con):
    seed_main(con)
    # stage 20 exists in BOTH main pipelines; the deal has no pipeline history and its current pipeline is neither -> cannot resolve
    raw(con, "main", "pipelines", "555", pipeline_json("555", "Other", [("5", "x", False)]))
    hist = {"dealstage": [H("20", "2026-09-02T10:00:00.000Z")], "pipeline": []}
    raw(con, "main", "deals", "5002", {"dealname": "Odd", "dealstage": "5", "pipeline": "555"}, assoc={"contacts": [], "companies": []}, history=hist)
    canon(con)
    assert con.execute("SELECT COUNT(*) FROM deal_stage_event").fetchone()[0] == 0
    assert con.execute("SELECT rule_code FROM ops_dq_issue WHERE rule_code='ambiguous_stage_pipeline'").fetchone() is not None


def test_cross_portal_company_links_on_strong_key_only(con):
    raw(con, "main", "companies", "1", {"name": "Zeta Corp", "domain": "https://www.Zeta.io/about"})
    raw(con, "rat", "companies", "2", {"name": "Zeta Corporation Pvt", "domain": "zeta.io"})
    raw(con, "rat", "companies", "3", {"name": "Zeta Corp", "domain": "other-zeta.com"})          # name-only twin: NOT linked
    for a in ("main", "rat"):
        c = cz.Canon(a, con, log=lambda m: None)
        c.stage_map_cfg = {"pipelines": []}
        c.run()
    links = {(r["account_id"], r["hs_company_id"]): (r["company_id"], r["link_method"]) for r in con.execute("SELECT * FROM company_account_link")}
    assert links[("main", "1")][0] == links[("rat", "2")][0] and links[("rat", "2")][1] == "root_domain"
    assert links[("rat", "3")][0] != links[("main", "1")][0] and links[("rat", "3")][1] == "new_golden"
    assert con.execute("SELECT COUNT(*) FROM company").fetchone()[0] == 2
    assert con.execute("SELECT COUNT(*) FROM merge_candidate WHERE match_kind='name_norm'").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM company WHERE merged_into_company_id IS NOT NULL").fetchone()[0] == 0


def test_engagement_and_html_notes(con):
    seed_main(con)
    raw(con, "main", "deals", "5003", {"dealname": "N", "dealstage": "10", "pipeline": "default"}, assoc={"contacts": [], "companies": []}, history={"dealstage": [H("10", "2026-09-01T00:00:00.000Z")], "pipeline": []})
    raw(con, "main", "notes", "8001", {"hs_note_body": '<div dir="auto"><p style="margin:0;">11-50 employees,&nbsp;not interesting </p><p>second</p></div>', "hs_timestamp": "2026-09-03T05:00:00.378Z",
                                       "hubspot_owner_id": "111"}, assoc={"deals": [{"id": "5003", "types": []}], "contacts": [], "companies": []})
    canon(con)
    e = con.execute("SELECT kind, body_text, owner_id IS NOT NULL, ist_day FROM engagement").fetchone()
    assert e["kind"] == "note" and e["body_text"] == "11-50 employees, not interesting\nsecond" and e[2] == 1 and e["ist_day"] == "2026-09-03"
    assert con.execute("SELECT COUNT(*) FROM engagement_assoc").fetchone()[0] == 1


def test_phone_classification_policy():
    f = cz.classify_phone
    assert f("+91 98123 45678")["type"] == "mobile" and f("9812345678")["e164"] == "+919812345678"
    assert f("+91 80 4245 3955")["type"] == "landline"                     # Bangalore landline: exact with phonenumbers installed (fails closed to unknown without it)
    assert f("12345")["e164"] is None                                      # unparseable: keeps phone_raw only
    assert f("+91 98765 43210")["placeholder"] == 1                        # famous dummy number: never opens the gate
    assert f("+91 12345 67890")["placeholder"] == 1 and f("+919999999999")["placeholder"] == 1
    assert f("+34608351111") == {"e164": "+34608351111", "country": "ES", "type": "mobile", "placeholder": 0}   # non-India numbers are typed exactly now; the gate only ever opens for +91 mobile


def test_stage_map_yaml_is_consistent_with_the_ladder():
    """config/stage_map.yaml: every entry names a real canonical code, dead reasons only on DEAD, depth_reached on every non-retired dead stage."""
    from leadgen import config
    cfg = config.load_yaml("stage_map")
    codes = {s["code"]: s for s in config.load_canonical_stages()["stages"]}
    reasons = {r["code"] for r in config.load_canonical_stages()["dead_reasons"]}
    seen = set()
    for pl in cfg["pipelines"]:
        for s in pl["stages"]:
            key = (pl["account"], str(pl["pipeline_id"]), str(s["id"]))
            assert key not in seen
            seen.add(key)
            c = codes[s["canonical"]]
            kind = "won" if c["is_won"] else ("dead" if c["is_dead"] else "live")
            assert s["kind"] == kind, key
            if kind == "dead":
                assert s["dead_reason"] in reasons
                assert s["dead_reason"] == "RETIRED_ADMIN" or s.get("depth_reached") is not None
            else:
                assert "dead_reason" not in s and "depth_reached" not in s
            assert not c["is_derived"], key
    assert len({k[0] for k in seen}) == 3 and len({(k[0], k[1]) for k in seen}) == 5            # all five reporting pipelines


def test_deactivated_user_actor_resolves_through_equal_ids_and_is_disclosed(con):
    seed_main(con)
    raw(con, "main", "owners", "333", {"id": "333", "email": "gone@lh2.ai", "firstName": "Gone", "lastName": "User", "archived": True})     # no userId on record
    hist = {"dealstage": [H("20", "2026-09-04T10:00:00.000Z", "CRM_UI", 333), H("10", "2026-09-01T10:00:00.000Z", "INTEGRATION")],
            "pipeline": [H("default", "2026-09-01T10:00:00.000Z", "INTEGRATION")]}
    raw(con, "main", "deals", "5010", {"dealname": "G", "dealstage": "20", "pipeline": "default"}, assoc={"contacts": [], "companies": []}, history=hist)
    canon(con)
    row = con.execute("SELECT e.actor_user_id, o.hs_owner_id FROM deal_stage_event e JOIN owner o ON o.owner_id = e.actor_owner_id WHERE e.is_human = 1").fetchone()
    assert tuple(row) == ("333", "333")
    assert con.execute("SELECT severity FROM ops_dq_issue WHERE rule_code='actor_owner_inferred'").fetchone()[0] == "info"


def test_first_association_read_does_not_litter_revisions(con):
    run = hs.Run(con, "main", {})
    with db.transaction(con):
        hs.upsert_raw(con, run, "main", "deals", "77", {"dealname": "d"}, associations=None, history={"dealstage": []})
        hs.set_associations(con, run, "main", "deals", "77", {"contacts": [], "companies": []})
    assert con.execute("SELECT COUNT(*) FROM hsraw_object_revision").fetchone()[0] == 0
    with db.transaction(con):
        hs.set_associations(con, run, "main", "deals", "77", {"contacts": [{"id": "1", "types": []}], "companies": []})
    assert con.execute("SELECT COUNT(*) FROM hsraw_object_revision").fetchone()[0] == 1          # a real change keeps the previous payload
    run.finish("succeeded")


def test_stage_map_yaml_pipelines_match_accounts_yaml():
    from leadgen import config
    cfg = config.load_yaml("stage_map")
    reporting = {(p.account_slug, p.pipeline_id) for p in config.reporting_pipelines()}
    assert {(pl["account"], str(pl["pipeline_id"])) for pl in cfg["pipelines"]} == reporting
    ign = {(i["account"], str(i["pipeline_id"])) for i in cfg.get("ignored_pipelines") or []}
    assert ign == {("rat", "default")} and not (ign & reporting)
    for d in cfg["deleted_stages"]:                                      # tombstones: own mapping or alias, never both missing
        assert d.get("alias_of") or d.get("canonical"), d["id"]
        assert (d["account"], str(d["pipeline_id"])) in reporting


def test_association_order_from_hubspot_is_normalised():
    """HubSpot returns targets and association types in varying order: the stored payload must not change because of it."""
    def resp(order):
        types = [{"category": "HUBSPOT_DEFINED", "typeId": 341, "label": None}, {"category": "HUBSPOT_DEFINED", "typeId": 5, "label": "Primary"}]
        to = [{"toObjectId": 20, "associationTypes": types}, {"toObjectId": 9, "associationTypes": types}]
        if order:
            types, to = types[::-1], to[::-1]
        return Resp(200, {"results": [{"from": {"id": "1"}, "to": [dict(t, associationTypes=types) for t in to]}]})
    a = mk_client([resp(False)]).batch_read_associations("deals", "companies", ["1"])
    b = mk_client([resp(True)]).batch_read_associations("deals", "companies", ["1"])
    assert a == b and [x["id"] for x in a["1"]] == ["9", "20"] and [t["typeId"] for t in a["1"][0]["types"]] == [5, 341]
