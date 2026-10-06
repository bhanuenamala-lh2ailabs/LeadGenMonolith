"""leadgen.google: Sheets + Gmail pulls, the read-only guard, backoff and token handling.  NO network: every Google service is a fake.

Run:  mkdir -p db/_scratch && .venv/bin/python -m pytest -q tests/test_google.py --basetemp=db/_scratch/pytest
"""
import base64
import datetime
import gzip
import json
import os
import re
import sqlite3
import stat

import httplib2
import pytest
from googleapiclient.errors import HttpError

from leadgen import db as ldb
from leadgen.google import auth, gmail, sheets

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL_DB = os.path.join(ROOT, "db", "leadgen.sqlite")
GOOGLE_DIR = os.path.join(ROOT, "leadgen", "google")


# ------------------------------------------------------------------------------------------------ fixtures / helpers
@pytest.fixture
def con(tmp_path):
    p = os.path.abspath(str(tmp_path / "t.sqlite"))
    assert p != REAL_DB
    ldb.migrate(p)
    c = ldb.connect(p)
    yield c
    c.close()


def http_error(status, reason="", message="boom"):
    body = json.dumps({"error": {"code": status, "message": message, "errors": [{"reason": reason}]}}).encode()
    return HttpError(httplib2.Response({"status": str(status)}), body)


class Req(object):
    """A fake googleapiclient request: execute() runs the callable."""

    def __init__(self, fn):
        self.fn = fn

    def execute(self, *a, **k):
        return self.fn()


def b64(text):
    raw = text if isinstance(text, bytes) else text.encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


# ================================================================================================ guard / source scans
class Anything(object):
    """A service double on which EVERY attribute exists (so only the guard can stop a call)."""

    def __init__(self, path=""):
        self._p = path

    def __getattr__(self, name):
        return lambda *a, **k: Anything(self._p + "." + name)

    def execute(self):
        return {"called": self._p}


def test_guard_allows_reads_and_blocks_everything_else():
    svc = auth.guard(Anything())
    assert svc.spreadsheets().get(spreadsheetId="x").execute() == {"called": ".spreadsheets.get"}
    assert svc.spreadsheets().values().batchGet(spreadsheetId="x", ranges=["A"]).execute()["called"].endswith("values.batchGet")
    assert svc.files().list(q="x").execute()
    assert svc.users().messages().get(userId="me", id="1").execute()
    assert svc.users().messages().attachments().get(userId="me", messageId="1", id="a").execute()
    assert svc.users().history().list(userId="me", startHistoryId="1").execute()
    blocked = [
        lambda: svc.spreadsheets().batchUpdate(spreadsheetId="x", body={}),
        lambda: svc.spreadsheets().create(body={}),
        lambda: svc.spreadsheets().values().update(spreadsheetId="x", range="A1", body={}),
        lambda: svc.spreadsheets().values().append(spreadsheetId="x", range="A1", body={}),
        lambda: svc.spreadsheets().values().clear(spreadsheetId="x", range="A1"),
        lambda: svc.spreadsheets().values().batchUpdate(spreadsheetId="x", body={}),
        lambda: svc.spreadsheets().values().batchClear(spreadsheetId="x", body={}),
        lambda: svc.spreadsheets().sheets().copyTo(spreadsheetId="x", sheetId=0, body={}),
        lambda: svc.files().update(fileId="x", body={}),
        lambda: svc.files().delete(fileId="x"),
        lambda: svc.files().create(body={}),
        lambda: svc.files().copy(fileId="x"),
        lambda: svc.files().export(fileId="x", mimeType="text/csv"),
        lambda: svc.permissions().create(fileId="x", body={}),
        lambda: svc.users().messages().send(userId="me", body={}),
        lambda: svc.users().messages().modify(userId="me", id="1", body={}),
        lambda: svc.users().messages().trash(userId="me", id="1"),
        lambda: svc.users().messages().delete(userId="me", id="1"),
        lambda: svc.users().messages().batchModify(userId="me", body={}),
        lambda: svc.users().messages().insert(userId="me", body={}),
        lambda: svc.users().drafts().create(userId="me", body={}),
        lambda: svc.users().labels().create(userId="me", body={}),
        lambda: svc.users().labels().delete(userId="me", id="x"),
        lambda: svc.users().threads().modify(userId="me", id="x", body={}),
        lambda: svc.users().watch(userId="me", body={}),
        lambda: svc._http,
    ]
    for fn in blocked:
        with pytest.raises(auth.WriteBlocked):
            fn()
    with pytest.raises(auth.WriteBlocked):
        svc.anything_else()
    with pytest.raises(auth.WriteBlocked):
        svc.x = 1


def test_sheet_reader_and_gmail_client_wrap_services_in_the_guard():
    r = sheets.SheetReader(Anything())
    c = gmail.GmailClient(Anything())
    assert isinstance(r.svc, auth.ReadOnlyGuard) and isinstance(c.svc, auth.ReadOnlyGuard)
    with pytest.raises(auth.WriteBlocked):
        r.svc.spreadsheets().values().update()


DISTINCT = r"\.(send|modify|trash|untrash|batchModify|batchDelete|batchUpdate|batchClear|copyTo|emptyTrash|import_|drafts|watch|getBytes)\s*\("
ON_ACCESSOR = r"\(\)\s*\.(delete|insert|update|patch|create|append|clear|copy|export|stop)\s*\("      # users().messages().delete( ... but not list.append(x)
MUTATING = DISTINCT + "|" + ON_ACCESSOR


@pytest.mark.parametrize("fname", ["gmail.py", "sheets.py", "auth.py"])
def test_source_never_names_a_mutating_google_method(fname):
    src = open(os.path.join(GOOGLE_DIR, fname)).read()
    hits = re.findall(DISTINCT, src) + re.findall(ON_ACCESSOR, src)
    assert hits == [], "mutating-looking calls in %s: %s" % (fname, hits)
    for bad in ("gmail.send", "gmail.modify", "gmail.compose", "gmail.insert", "gmail.settings", "mail.google.com/", "/auth/drive\"", "/auth/spreadsheets\""):
        assert bad not in src, "%s mentions %s" % (fname, bad)


def test_gmail_module_requests_readonly_scope_only_and_no_impersonation():
    g = open(os.path.join(GOOGLE_DIR, "gmail.py")).read()
    a = open(os.path.join(GOOGLE_DIR, "auth.py")).read()
    assert auth.SCOPE_GMAIL_RO == "https://www.googleapis.com/auth/gmail.readonly"
    assert re.findall(r"googleapis\.com/auth/[a-z.]+", g + a) and all(
        s.endswith(".readonly") for s in set(re.findall(r"googleapis\.com/auth/[a-z.]+", g + a)))
    assert not re.search(r"\.with_(subject|scopes_if_required)\(|\bsubject\s*=\s*[A-Za-z_\"']+\s*[,)]", a + g.replace("subject=excluded.subject", "")), "impersonation code found"
    assert "domain-wide" not in (a + g).lower() or "no impersonation" in (a + g).lower()


# ================================================================================================ auth: retries, limiter, tokens
def test_execute_retries_429_with_exponential_backoff_then_succeeds():
    seq = [http_error(429, "RESOURCE_EXHAUSTED"), http_error(429), http_error(503), "ok"]
    sleeps = []

    def fn():
        v = seq.pop(0)
        if isinstance(v, Exception):
            raise v
        return v
    out = auth.execute(Req(fn), None, sleep=sleeps.append)
    assert out == "ok" and len(sleeps) == 3
    assert sleeps[0] < sleeps[1] < sleeps[2] or sleeps[1] < sleeps[2]       # exponential growth (jitter keeps the order for factor 2)
    assert all(s <= 90 * 1.25 for s in sleeps)


def test_execute_retries_rate_limit_403_but_not_permission_403_or_404():
    seq = [http_error(403, "userRateLimitExceeded"), "ok"]
    sleeps = []

    def fn():
        v = seq.pop(0)
        if isinstance(v, Exception):
            raise v
        return v
    assert auth.execute(Req(fn), None, sleep=sleeps.append) == "ok" and len(sleeps) == 1
    for err in (http_error(403, "forbidden"), http_error(404, "notFound"), http_error(400, "badRequest")):
        n = []

        def boom(err=err):
            n.append(1)
            raise err
        with pytest.raises(HttpError):
            auth.execute(Req(boom), None, sleep=lambda s: None)
        assert len(n) == 1


def test_execute_gives_up_after_retries():
    n = []

    def boom():
        n.append(1)
        raise http_error(429)
    with pytest.raises(HttpError):
        auth.execute(Req(boom), None, retries=3, sleep=lambda s: None)
    assert len(n) == 4


def test_rate_limiter_spaces_calls():
    t = [0.0]
    slept = []

    def sleep(s):
        slept.append(s)
        t[0] += s
    lim = auth.RateLimiter(60, clock=lambda: t[0], sleep=sleep)       # 1 call / second
    for _ in range(4):
        lim.wait()
    assert sum(slept) == pytest.approx(3.0)
    lim.penalty(30)
    lim.wait()
    assert sum(slept) >= 3.0 + 29


def test_token_status_is_offline_and_never_contains_secret_values(tmp_path):
    p = tmp_path / "tok.json"
    p.write_text(json.dumps({"token": "ya29.SECRETACCESS", "refresh_token": "1//SECRETREFRESH", "client_id": "cid.apps", "client_secret": "SECRETCLIENT",
                             "scopes": [auth.SCOPE_GMAIL_RO], "expiry": "2999-01-01T00:00:00.000000Z", "account": "me@x.ai"}))
    os.chmod(str(p), 0o600)
    st = auth.token_status(str(p), required_scope=auth.SCOPE_GMAIL_RO)
    blob = json.dumps(st)
    assert st["ok"] and st["has_refresh_token"] and st["has_required_scope"] and st["access_token_valid_now"]
    for secret in ("SECRETACCESS", "SECRETREFRESH", "SECRETCLIENT"):
        assert secret not in blob
    st2 = auth.token_status(str(p), required_scope="https://www.googleapis.com/auth/gmail.send")
    assert not st2["ok"] and "scope" in st2["problem"]
    os.chmod(str(p), 0o644)
    assert not auth.token_status(str(p))["ok"]
    assert auth.token_status(str(tmp_path / "nope.json"))["problem"] == "missing"


def test_refreshed_token_is_written_back_to_the_same_file_only_with_0600(tmp_path):
    p = tmp_path / "tok.json"
    orig = {"token": "old", "refresh_token": "R", "client_id": "C", "client_secret": "S", "scopes": ["a"], "account": "me@x.ai", "universe_domain": "googleapis.com",
            "expiry": "2020-01-01T00:00:00.000000Z", "token_uri": "https://oauth2.googleapis.com/token"}
    p.write_text(json.dumps(orig))
    os.chmod(str(p), 0o644)
    auth.persist_refreshed_token(str(p), "new-access", datetime.datetime(2030, 1, 1, 12, 0, 0))
    now = json.loads(p.read_text())
    assert now["token"] == "new-access" and now["expiry"] == "2030-01-01T12:00:00.000000Z"
    assert {k: v for k, v in now.items() if k not in ("token", "expiry")} == {k: v for k, v in orig.items() if k not in ("token", "expiry")}
    assert stat.S_IMODE(os.stat(str(p)).st_mode) == 0o600
    assert [f for f in os.listdir(str(tmp_path)) if f != "tok.json"] == []        # no temp leftovers, nothing written elsewhere


def test_user_credentials_refreshes_in_memory_and_persists(tmp_path, monkeypatch):
    p = tmp_path / "tok.json"
    p.write_text(json.dumps({"token": "old", "refresh_token": "R", "client_id": "C", "client_secret": "S", "scopes": [auth.SCOPE_GMAIL_RO],
                             "expiry": "2020-01-01T00:00:00.000000Z", "token_uri": "https://oauth2.googleapis.com/token"}))
    os.chmod(str(p), 0o600)
    from google.oauth2 import credentials as gc

    def fake_refresh(self, request):
        self.token = "fresh"
        self.expiry = datetime.datetime.utcnow() + datetime.timedelta(hours=1)
    monkeypatch.setattr(gc.Credentials, "refresh", fake_refresh)
    creds = auth.user_credentials(str(p), required_scopes=[auth.SCOPE_GMAIL_RO], request_factory=lambda: object())
    assert creds.token == "fresh"
    assert json.loads(p.read_text())["token"] == "fresh"
    with pytest.raises(auth.TokenError):
        auth.user_credentials(str(p), required_scopes=["https://www.googleapis.com/auth/gmail.send"])
    with pytest.raises(auth.TokenError):
        auth.user_credentials(str(tmp_path / "missing.json"))


def test_service_account_has_no_subject_parameter():
    import inspect
    assert "subject" not in inspect.signature(auth.service_account_credentials).parameters
    assert all(s.endswith(".readonly") for s in auth.SA_SCOPES)


# ================================================================================================ Sheets
INTERN = "1IPA45kJ6yTsBo8d33DM0Ay_CIfBa6jrj_wrh3A4_uiQ"


def mini_catalog(path, extra=()):
    sheets_ = [
        {"id": "S_HUMAN", "name": "Human leads", "owners": ["bhanu.enamala@lh2.ai"], "modifiedTime": "2026-10-03T17:38:36.848Z", "createdTime": "2026-10-01T00:00:00.000Z",
         "lastModifyingUser": "bhanu.enamala@lh2.ai", "canEdit": True, "sharedDriveId": None, "visible_to": ["hubspot_sheets_token", "companyops_sheets_token"],
         "url": "https://docs.google.com/spreadsheets/d/S_HUMAN", "timeZone": "Asia/Calcutta",
         "tabs": [{"title": "Leads", "sheetId": 0, "hidden": False, "rows": 1000, "cols": 26},
                  {"title": "It's big", "sheetId": 7, "hidden": True, "rows": 45000, "cols": 5}]},
        {"id": "S_BULK", "name": "ToolSmith-12", "owners": ["purunjay.choudhary@lh2.ai"], "modifiedTime": "2026-09-30T10:00:00.000Z", "createdTime": "2026-09-30T10:00:00.000Z",
         "lastModifyingUser": None, "canEdit": False, "sharedDriveId": None, "visible_to": ["hubspot_sheets_token"], "url": "u", "timeZone": "Asia/Calcutta",
         "tabs": [{"title": "Sheet1", "sheetId": 0, "hidden": False, "rows": 1000, "cols": 24}]},
        {"id": INTERN, "name": "Founder's Office(Strategy & Ops) - Automation Intern", "owners": ["kenisha.thacker@lh2holdings.com"],
         "modifiedTime": "2026-09-01T10:00:00.000Z", "createdTime": "2026-09-01T10:00:00.000Z", "visible_to": ["hubspot_sheets_token"], "url": "u", "timeZone": "UTC",
         "tabs": [{"title": "Sheet1", "sheetId": 0, "hidden": False, "rows": 1000, "cols": 26}]},
        {"id": "S_CREDS", "name": "Creds", "owners": ["shobit.gupta@lh2.ai"], "modifiedTime": "2026-09-01T10:00:00.000Z", "createdTime": "2026-09-01T10:00:00.000Z",
         "visible_to": ["hubspot_sheets_token"], "url": "u", "timeZone": "UTC", "tabs": [{"title": "Sheet1", "sheetId": 0, "hidden": False, "rows": 10, "cols": 2}]},
        {"id": "S_RESP", "name": "Applicants form (Responses)", "owners": ["hr@x.com"], "modifiedTime": "2026-09-01T10:00:00.000Z", "createdTime": "2026-09-01T10:00:00.000Z",
         "visible_to": ["hubspot_sheets_token"], "url": "u", "timeZone": "UTC", "tabs": [{"title": "Form Responses 1", "sheetId": 0, "hidden": False, "rows": 10, "cols": 2}]},
        {"id": "S_SA", "name": "Private Codebase Tracker", "owners": ["aman.taneja@lh2.ai"], "modifiedTime": "2026-10-01T10:00:00.000Z", "createdTime": "2026-09-01T10:00:00.000Z",
         "visible_to": ["sa_lh2bot"], "url": "u", "timeZone": "UTC", "tabs": [{"title": "Pipeline Tracker", "sheetId": 411280464, "hidden": False, "rows": 1000, "cols": 10}]},
    ] + list(extra)
    path.write_text(json.dumps({"generated": "2026-10-04T20:21:49+0530", "note": "t", "sheets": sheets_}))
    return str(path)


class FakeSheets(object):
    """Fake Sheets v4 service: data[spreadsheet_id][tab title] = list of rows (None-padded / ragged like the real API after trimming)."""

    def __init__(self, data):
        self.data = data
        self.calls = []
        self.fail = {}          # (spreadsheet_id) -> list of exceptions to raise on batchGet (consumed)

    def spreadsheets(self):
        return FakeSpreadsheets(self)


class FakeSpreadsheets(object):
    def __init__(self, svc):
        self.svc = svc

    def get(self, spreadsheetId, fields=None, includeGridData=False):
        def run():
            self.svc.calls.append(("get", spreadsheetId))
            tabs = self.svc.data[spreadsheetId]
            return {"spreadsheetId": spreadsheetId, "properties": {"title": "t", "timeZone": "UTC"},
                    "sheets": [{"properties": {"sheetId": i, "title": t, "index": i, "gridProperties": {"rowCount": 1000, "columnCount": 26}}} for i, t in enumerate(tabs)]}
        return Req(run)

    def values(self):
        return self

    def batchGet(self, spreadsheetId, ranges, valueRenderOption=None, dateTimeRenderOption=None, majorDimension=None):
        def run():
            self.svc.calls.append(("batchGet", spreadsheetId, tuple(ranges), valueRenderOption))
            errs = self.svc.fail.get(spreadsheetId)
            if errs:
                raise errs.pop(0)
            out = []
            for r in ranges:
                m = re.match(r"^'((?:[^']|'')*)'(?:!A(\d+):[A-Z]+(\d+))?$", r)
                title = m.group(1).replace("''", "'")
                rows = self.svc.data[spreadsheetId].get(title)
                if rows is None:
                    raise http_error(400, "badRequest", "Unable to parse range: %s" % r)
                lo, hi = (int(m.group(2)), int(m.group(3))) if m.group(2) else (1, len(rows))
                chunk = [list(x) for x in rows[lo - 1:hi]]
                while chunk and not any(c not in ("", None) for row in chunk[-1:] for c in row):
                    chunk.pop()                                    # the real API trims trailing empty rows
                out.append({"range": r, "majorDimension": "ROWS", "values": chunk})
            return {"spreadsheetId": spreadsheetId, "valueRanges": out}
        return Req(run)


def make_pool(fake):
    return sheets.ReaderPool(per_minute=0, sleep=lambda s: None, factory=lambda name: fake)


@pytest.fixture
def cat(con, tmp_path):
    p = mini_catalog(tmp_path / "cat.json")
    out = sheets.load_catalog(con, p)
    return out


def test_load_catalog_is_idempotent_and_enforces_exclusions(con, tmp_path):
    p = mini_catalog(tmp_path / "cat.json")
    r1 = sheets.load_catalog(con, p)
    r2 = sheets.load_catalog(con, p)
    assert r1["spreadsheets"] == 6 and r1["new"] == 6 and r2["new"] == 0 and r2["updated"] == 6
    assert con.execute("SELECT COUNT(*) FROM ext_gsheet_catalog").fetchone()[0] == 6
    assert con.execute("SELECT COUNT(*) FROM ext_gsheet_tab").fetchone()[0] == 7
    row = con.execute("SELECT * FROM ext_gsheet_catalog WHERE spreadsheet_id='S_HUMAN'").fetchone()
    assert row["owner_email"] == "bhanu.enamala@lh2.ai" and row["modified_time"] == "2026-10-03T17:38:36.848Z" and row["pull_enabled"] == 0
    assert row["catalog_fetched_at"] == "2026-10-04T14:51:49.000Z"                    # +05:30 converted to UTC
    for sid in (INTERN, "S_CREDS"):
        r = con.execute("SELECT pii_class, pull_enabled FROM ext_gsheet_catalog WHERE spreadsheet_id=?", (sid,)).fetchone()
        assert (r["pii_class"], r["pull_enabled"]) == ("excluded", 0)
    assert con.execute("SELECT pii_class FROM ext_gsheet_catalog WHERE spreadsheet_id='S_RESP'").fetchone()[0] == "personal"
    # flipping pull_enabled by hand on an excluded sheet is undone by the database
    con.execute("UPDATE ext_gsheet_catalog SET pull_enabled=1, pii_class='normal' WHERE spreadsheet_id=?", (INTERN,))
    assert con.execute("SELECT pii_class, pull_enabled FROM ext_gsheet_catalog WHERE spreadsheet_id=?", (INTERN,)).fetchone()[:] == ("excluded", 0)


def test_plan_groups_and_bulk_switch(con, cat):
    plan = {p["spreadsheet_id"]: p for p in sheets.build_plan(con)}
    assert plan["S_HUMAN"]["group"] == "human" and plan["S_HUMAN"]["action"] == "pull"
    assert plan["S_BULK"]["group"] == "bulk" and plan["S_BULK"]["action"] == "skip"
    assert plan["S_CREDS"]["action"] == "skip" and plan["S_CREDS"]["group"] == "excluded"
    assert plan["S_RESP"]["group"] == "flagged" and plan["S_RESP"]["action"] == "skip"
    assert plan[INTERN]["action"] == "skip" and "HARD-EXCLUDED" in plan[INTERN]["reason"]
    assert plan["1aj-d_IlHFyHOUdnrgHYc5mb6gpAnIijORZt8Vyna5j4"]["action"] == "skip"           # the Supply Funnel DOC is not a spreadsheet
    assert plan["S_SA"]["group"] == "human" and plan["S_SA"]["action"] == "pull"
    bulk_on = {p["spreadsheet_id"]: p for p in sheets.build_plan(con, include_bulk=True)}
    assert bulk_on["S_BULK"]["action"] == "pull" and bulk_on[INTERN]["action"] == "skip" and bulk_on["S_CREDS"]["action"] == "skip"
    assert len(sheets.HARD_CODED_SHEETS) == 11


BIG = [["h1", "h2", "h3", "h4", "h5"]] + [["r%d" % i, i, i * 1.5, "", True] for i in range(2, 45001)]


def human_data():
    return {
        "S_HUMAN": {
            "Leads": [["Company", "Phone", "Note"], ["Acme", 919876543210, "x"], [], ["", "", ""], ["Beta, Inc", "+91 98765", "has \"quote\"\nnewline"], ["", "", "only note"], [], [], []],
            "It's big": BIG,
        },
        "S_SA": {"Pipeline Tracker": [["a", "b"], [1, 2]]},
    }


def test_pull_stores_trimmed_rows_chunks_big_tabs_and_writes_csv(con, cat, tmp_path):
    fake = FakeSheets(human_data())
    plan = [p for p in sheets.build_plan(con) if p["spreadsheet_id"] == "S_HUMAN"]
    out = sheets.run_pull(con, make_pool(fake), plan, data_dir=str(tmp_path / "csv"))
    assert out["pulled"] == 1 and out["failed"] == 0
    leads = con.execute("SELECT * FROM ext_gsheet_tab WHERE spreadsheet_id='S_HUMAN' AND title='Leads'").fetchone()
    assert leads["pull_status"] == "ok" and leads["pulled_rows"] == 4 and json.loads(leads["header_json"]) == ["Company", "Phone", "Note"]
    rows = con.execute("SELECT row_number, cells_json FROM ext_gsheet_row WHERE tab_pk=? ORDER BY row_number", (leads["tab_pk"],)).fetchall()
    assert [r[0] for r in rows] == [1, 2, 5, 6]                                          # empty rows dropped, row numbers kept
    assert json.loads(rows[1][1]) == ["Acme", 919876543210, "x"]
    assert json.loads(rows[3][1]) == ["", "", "only note"]                               # interior blanks kept so columns line up
    big = con.execute("SELECT * FROM ext_gsheet_tab WHERE spreadsheet_id='S_HUMAN' AND title=\"It's big\"").fetchone()
    assert big["pulled_rows"] == 45000
    ranges = [c[2] for c in fake.calls if c[0] == "batchGet" for c in [c]]
    flat = [r for rs in ranges for r in rs]
    assert "'Leads'" in flat and any(r.startswith("'It''s big'!A1:E20000") for r in flat) and any(r.startswith("'It''s big'!A40001:E45000") for r in flat)
    assert all(c[3] == "UNFORMATTED_VALUE" for c in fake.calls if c[0] == "batchGet")
    maxrow = con.execute("SELECT MAX(row_number), COUNT(*) FROM ext_gsheet_row WHERE tab_pk=?", (big["tab_pk"],)).fetchone()
    assert tuple(maxrow) == (45000, 45000)
    # FTS works
    assert con.execute("SELECT COUNT(*) FROM ext_gsheet_row_fts WHERE ext_gsheet_row_fts MATCH 'acme'").fetchone()[0] == 1
    # CSV copy with preserved row numbers
    csvs = sorted(os.listdir(str(tmp_path / "csv")))
    assert csvs == ["S_HUMAN__It_s_big.csv", "S_HUMAN__Leads.csv"]
    import csv as _csv
    got = list(_csv.reader(open(str(tmp_path / "csv" / "S_HUMAN__Leads.csv"), newline="", encoding="utf-8")))
    assert got[0] == ["Company", "Phone", "Note"] and got[1] == ["Acme", "919876543210", "x"] and got[2] == [] and got[4] == ["Beta, Inc", "+91 98765", "has \"quote\"\nnewline"]
    # bookkeeping
    assert con.execute("SELECT pull_enabled, data_pulled_at IS NOT NULL FROM ext_gsheet_catalog WHERE spreadsheet_id='S_HUMAN'").fetchone()[:] == (1, 1)
    st = con.execute("SELECT last_status, cursor_value FROM ops_sync_state WHERE source_system='gsheets' AND object_type='values' AND cursor_name='S_HUMAN'").fetchone()
    assert tuple(st) == ("ok", "2026-10-03T17:38:36.848Z")
    assert con.execute("SELECT status FROM ops_import_run WHERE kind='gsheets_pull'").fetchone()[0] == "succeeded"


def test_rerun_is_incremental_and_idempotent(con, cat, tmp_path):
    data = human_data()
    fake = FakeSheets(data)
    plan = [p for p in sheets.build_plan(con) if p["spreadsheet_id"] == "S_HUMAN"]
    sheets.run_pull(con, make_pool(fake), plan, data_dir=None)
    n_calls = len(fake.calls)
    before = con.execute("SELECT COUNT(*), SUM(length(cells_json)) FROM ext_gsheet_row").fetchone()[:]
    out = sheets.run_pull(con, make_pool(fake), plan, data_dir=None)                    # modifiedTime unchanged -> no API call at all
    assert out["unchanged"] == 1 and out["pulled"] == 0 and len(fake.calls) == n_calls
    # --force re-reads but rewrites nothing (row hashes are equal)
    leads_pk = con.execute("SELECT tab_pk FROM ext_gsheet_tab WHERE spreadsheet_id='S_HUMAN' AND title='Leads'").fetchone()[0]
    t0 = con.execute("SELECT fetched_at FROM ext_gsheet_row WHERE tab_pk=? AND row_number=2", (leads_pk,)).fetchone()[0]
    sheets.run_pull(con, make_pool(fake), plan, force=True, data_dir=None)
    assert con.execute("SELECT COUNT(*), SUM(length(cells_json)) FROM ext_gsheet_row").fetchone()[:] == before
    assert con.execute("SELECT fetched_at FROM ext_gsheet_row WHERE tab_pk=? AND row_number=2", (leads_pk,)).fetchone()[0] == t0
    # the sheet changes upstream: one row edited, one deleted, one added; Drive says it was modified later
    data["S_HUMAN"]["Leads"][1] = ["Acme Corp", 919876543210, "x"]
    data["S_HUMAN"]["Leads"][4] = []
    data["S_HUMAN"]["Leads"].append(["Gamma", 1, 2])
    con.execute("UPDATE ext_gsheet_catalog SET modified_time='2026-10-04T01:00:00.000Z' WHERE spreadsheet_id='S_HUMAN'")
    out = sheets.run_pull(con, make_pool(fake), plan, data_dir=None)
    assert out["pulled"] == 1
    rows = {r[0]: json.loads(r[1]) for r in con.execute("SELECT row_number, cells_json FROM ext_gsheet_row WHERE tab_pk=?", (leads_pk,))}
    assert sorted(rows) == [1, 2, 6, 10] and rows[2][0] == "Acme Corp" and rows[10][0] == "Gamma"
    assert con.execute("SELECT COUNT(*) FROM ext_gsheet_row_fts WHERE ext_gsheet_row_fts MATCH 'corp'").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM ext_gsheet_row_fts WHERE ext_gsheet_row_fts MATCH 'beta'").fetchone()[0] == 0
    con.execute("INSERT INTO ext_gsheet_row_fts(ext_gsheet_row_fts) VALUES('integrity-check')")


def test_excluded_and_unplanned_sheets_are_never_read(con, cat):
    fake = FakeSheets({INTERN: {"Sheet1": [["name", "phone"]]}, "S_CREDS": {"Sheet1": [["pw"]]}, "S_BULK": {"Sheet1": [["x"]]}, "S_RESP": {"Form Responses 1": [["x"]]}})
    reader = sheets.SheetReader(fake)
    for sid in (INTERN, "S_CREDS", "S_RESP", "S_BULK"):                                  # pull_enabled = 0 / excluded
        with pytest.raises(sheets.WriteDenied):
            sheets.pull_spreadsheet(con, reader, sid)
    # even if somebody forces the plan: enable_pulls refuses excluded ids and the DB trigger blocks row inserts
    forced = [{"spreadsheet_id": s, "action": "pull", "title": "", "group": "x", "reason": "", "owner": None} for s in (INTERN, "S_CREDS")]
    assert sheets.enable_pulls(con, forced) == 0
    out = sheets.run_pull(con, make_pool(fake), forced, force=True, data_dir=None)
    assert out["failed"] == 2 and fake.calls == []
    tab_pk = con.execute("SELECT tab_pk FROM ext_gsheet_tab WHERE spreadsheet_id=?", (INTERN,)).fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO ext_gsheet_row (tab_pk, row_number, cells_json, fetched_at) VALUES (?,1,'[]',?)", (tab_pk, ldb.utc_now_iso()))
    assert con.execute("SELECT COUNT(*) FROM ext_gsheet_row").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM v_gsheet_excluded_rows").fetchone()[0] == 0
    # the default plan never contains them as pull targets
    assert not [p for p in sheets.build_plan(con, include_bulk=True) if p["action"] == "pull" and p["spreadsheet_id"] in (INTERN, "S_CREDS", "S_RESP")]


def test_access_error_marks_the_tab_and_keeps_the_sheet_pending(con, cat):
    fake = FakeSheets(human_data())
    fake.fail["S_HUMAN"] = [http_error(403, "forbidden", "The caller does not have permission")] * 3
    plan = [p for p in sheets.build_plan(con) if p["spreadsheet_id"] == "S_HUMAN"]
    out = sheets.run_pull(con, make_pool(fake), plan, data_dir=None)
    assert out["failed"] == 1 and out["pulled"] == 0 and "permission" in json.dumps(out["failures"])
    assert con.execute("SELECT COUNT(*) FROM ext_gsheet_tab WHERE spreadsheet_id='S_HUMAN' AND pull_status='error'").fetchone()[0] >= 1
    assert con.execute("SELECT last_status FROM ops_sync_state WHERE object_type='values' AND cursor_name='S_HUMAN'").fetchone()[0] == "error"
    assert con.execute("SELECT status FROM ops_import_run WHERE kind='gsheets_pull'").fetchone()[0] == "partial"
    fake.fail.clear()                                                                       # access fixed: the next run resumes this sheet
    out = sheets.run_pull(con, make_pool(fake), plan, data_dir=None)
    assert out["pulled"] == 1 and out["failed"] == 0


def test_429_inside_pull_is_backed_off_not_failed(con, cat):
    fake = FakeSheets(human_data())
    fake.fail["S_HUMAN"] = [http_error(429, "RESOURCE_EXHAUSTED"), http_error(429, "RESOURCE_EXHAUSTED")]
    sleeps = []
    pool = sheets.ReaderPool(per_minute=0, sleep=sleeps.append, factory=lambda n: fake)
    plan = [p for p in sheets.build_plan(con) if p["spreadsheet_id"] == "S_HUMAN"]
    out = sheets.run_pull(con, pool, plan, data_dir=None)
    real = [x for x in sleeps if x > 1]
    assert out["pulled"] == 1 and len(real) >= 2 and max(real) > min(real)


def test_service_account_only_sheet_uses_the_sa_reader(con, cat):
    user, sa = FakeSheets(human_data()), FakeSheets(human_data())
    pool = sheets.ReaderPool(per_minute=0, sleep=lambda s: None, factory=lambda n: sa if n == "sa_lh2bot" else user)
    plan = [p for p in sheets.build_plan(con) if p["spreadsheet_id"] == "S_SA"]
    out = sheets.run_pull(con, pool, plan, data_dir=None)
    assert out["pulled"] == 1 and user.calls == [] and len(sa.calls) == 1


def test_renamed_tab_triggers_a_tab_list_refresh(con, cat):
    data = human_data()
    data["S_HUMAN"]["Leads2"] = data["S_HUMAN"].pop("Leads")                             # catalog still says "Leads"
    fake = FakeSheets(data)
    plan = [p for p in sheets.build_plan(con) if p["spreadsheet_id"] == "S_HUMAN"]
    out = sheets.run_pull(con, make_pool(fake), plan, data_dir=None)
    assert out["pulled"] == 1, out
    titles = sorted(r[0] for r in con.execute("SELECT title FROM ext_gsheet_tab WHERE spreadsheet_id='S_HUMAN'"))
    assert titles == ["It's big", "Leads2"]
    assert any(c[0] == "get" for c in fake.calls)


def test_refresh_catalog_from_drive_updates_modified_time_and_adds_new_files(con, cat):
    class Drive(object):
        def files(self):
            return self

        def list(self, **kw):
            assert kw["q"].startswith("mimeType='application/vnd.google-apps.spreadsheet'")
            return Req(lambda: {"files": [
                {"id": "S_HUMAN", "name": "Human leads v2", "owners": [{"emailAddress": "bhanu.enamala@lh2.ai"}], "modifiedTime": "2026-10-05T00:00:00.123Z",
                 "lastModifyingUser": {"emailAddress": "x@lh2.ai"}},
                {"id": "S_NEW", "name": "Brand new", "owners": [{"emailAddress": "Z@lh2.ai"}], "modifiedTime": "2026-10-05T01:00:00.000Z", "createdTime": "2026-10-05T01:00:00.000Z",
                 "capabilities": {"canEdit": True}, "webViewLink": "http://x"}]})
    r = sheets.refresh_catalog_from_drive(con, auth.guard(Drive()), "hubspot_sheets_token")
    assert r == {"seen": 2, "modified_changed": 1, "new": 1}
    assert con.execute("SELECT title, modified_time FROM ext_gsheet_catalog WHERE spreadsheet_id='S_HUMAN'").fetchone()[:] == ("Human leads v2", "2026-10-05T00:00:00.123Z")
    new = con.execute("SELECT owner_email, pull_enabled, visible_to_json FROM ext_gsheet_catalog WHERE spreadsheet_id='S_NEW'").fetchone()
    assert tuple(new) == ("z@lh2.ai", 0, '["hubspot_sheets_token"]')
    # after the refresh the previously pulled sheet needs a re-pull
    con.execute("UPDATE ops_sync_state SET cursor_value='x'")
    assert sheets.needs_pull(con, "S_HUMAN")


def test_helpers():
    assert sheets.col_letters(1) == "A" and sheets.col_letters(26) == "Z" and sheets.col_letters(27) == "AA" and sheets.col_letters(702) == "ZZ"
    assert sheets.quote_tab("It's") == "'It''s'"
    assert sheets.iso_ms("2026-10-03T17:38:36Z") == "2026-10-03T17:38:36.000Z"
    assert sheets.iso_ms("2026-10-04T20:21:49+0530") == "2026-10-04T14:51:49.000Z"
    assert sheets.iso_ms("garbage") is None
    assert sheets.clean_row(["a", "", None, "b", "", None]) == ["a", "", None, "b"]
    assert sheets.cell_text(3.0) == "3" and sheets.cell_text(True) == "TRUE" and sheets.cell_text(None) == ""
    groups = sheets.plan_requests([{"title": "a", "grid_rows": 1000, "grid_cols": 26}] * 25)
    assert len(groups) == 2 and len(groups[0]) == 20


# ================================================================================================ Gmail
def part(mime, text=None, data=None, filename="", att_id=None, headers=(), part_id="0", size=None, parts=None):
    body = {}
    if text is not None:
        body = {"size": len(text.encode()), "data": b64(text)}
    if data is not None:
        body = {"size": len(data), "data": b64(data)}
    if att_id:
        body = {"size": size if size is not None else 10, "attachmentId": att_id}
    p = {"partId": part_id, "mimeType": mime, "filename": filename, "headers": [{"name": k, "value": v} for k, v in headers], "body": body}
    if parts is not None:
        p["parts"] = parts
        p["body"] = {"size": 0}
    return p


def mk_msg(mid, thread, subject="Hello", frm="Alice <Alice@Example.com>", to="bob@x.com, \"Carol, C\" <carol@y.org>", cc=None, bcc=None, labels=("INBOX",),
           text="plain body", html_body=None, attachments=(), internal=1700000000000, history="100", date="Tue, 14 Nov 2023 22:13:20 +0000", extra_headers=()):
    alt = []
    if text is not None:
        alt.append(part("text/plain", text=text, headers=[("Content-Type", 'text/plain; charset="UTF-8"')], part_id="0.0"))
    if html_body is not None:
        alt.append(part("text/html", text=html_body, headers=[("Content-Type", "text/html; charset=utf-8")], part_id="0.1"))
    body = part("multipart/alternative", parts=alt, part_id="0.A") if len(alt) != 1 else alt[0]
    children = [body] + list(attachments)
    payload = part("multipart/mixed", parts=children, part_id="") if attachments else body
    hdrs = [("From", frm), ("To", to), ("Subject", subject), ("Date", date), ("Message-ID", "<%s@mail.example>" % mid)] + list(extra_headers)
    if cc:
        hdrs.append(("Cc", cc))
    if bcc:
        hdrs.append(("Bcc", bcc))
    payload["headers"] = [{"name": k, "value": v} for k, v in hdrs] + payload.get("headers", [])
    return {"id": mid, "threadId": thread, "labelIds": list(labels), "snippet": "snip &#39;%s&#39;" % mid, "historyId": history, "internalDate": str(internal),
            "sizeEstimate": 1234, "payload": payload}


class FakeGmail(object):
    """Fake Gmail v1: mailbox of messages, paginated list, batch get, history, attachments.  Counts every call."""

    def __init__(self, messages, page=2, history_id="500", attachments=None):
        self.messages = {m["id"]: m for m in messages}
        self.order = [m["id"] for m in messages]
        self.page = page
        self.history_id = history_id
        self.attachments = attachments or {}
        self.counts = {}
        self.history_records = []
        self.history_error = None
        self.get_fail = {}                   # message id -> list of exceptions (consumed per batch attempt)
        self.ghosts = {}                     # id -> threadId of messages that are listed but already gone (404 on get)
        self.getlog = []

    def _n(self, k):
        self.counts[k] = self.counts.get(k, 0) + 1

    # transport ---------------------------------------------------------------------------------
    def new_batch_http_request(self, callback=None):
        return FakeBatch(callback)

    def users(self):
        return FakeUsers(self)


class FakeBatch(object):
    def __init__(self, callback):
        self.cb = callback
        self.reqs = []

    def add(self, request, request_id=None):
        self.reqs.append((request_id, request))

    def execute(self):
        for rid, r in self.reqs:
            try:
                self.cb(rid, r.execute(), None)
            except HttpError as exc:
                self.cb(rid, None, exc)


class FakeUsers(object):
    def __init__(self, g):
        self.g = g

    def getProfile(self, userId):
        def run():
            self.g._n("profile")
            return {"emailAddress": "Bhanu.Enamala@lh2.ai", "messagesTotal": len(self.g.messages), "threadsTotal": 2, "historyId": self.g.history_id}
        return Req(run)

    def labels(self):
        return FakeLabels(self.g)

    def messages(self):
        return FakeMessages(self.g)

    def history(self):
        return FakeHistory(self.g)


class FakeLabels(object):
    def __init__(self, g):
        self.g = g

    def list(self, userId):
        return Req(lambda: {"labels": [{"id": "INBOX", "name": "INBOX", "type": "system"}, {"id": "SENT", "name": "SENT", "type": "system"},
                                       {"id": "Label_1", "name": "Leads/2026", "type": "user"}]})

    def get(self, userId, id):
        return Req(lambda: {"id": id, "name": id if id != "Label_1" else "Leads/2026", "type": "user" if id == "Label_1" else "system", "messagesTotal": 3, "threadsTotal": 2})


class FakeMessages(object):
    def __init__(self, g):
        self.g = g

    def list(self, userId, maxResults=None, pageToken=None, includeSpamTrash=None):
        def run():
            self.g._n("list")
            assert includeSpamTrash is True
            start = int(pageToken[1:]) if pageToken else 0
            ids = self.g.order[start:start + self.g.page]
            out = {"messages": [{"id": i, "threadId": (self.g.messages.get(i) or {}).get("threadId") or self.g.ghosts.get(i)} for i in ids]}
            if start + self.g.page < len(self.g.order):
                out["nextPageToken"] = "p%d" % (start + self.g.page)
            return out
        return Req(run)

    def get(self, userId, id, format="full"):
        def run():
            self.g._n("get_" + format)
            self.g.getlog.append((id, format))
            errs = self.g.get_fail.get(id)
            if errs:
                raise errs.pop(0)
            if id not in self.g.messages:
                raise http_error(404, "notFound")
            m = self.g.messages[id]
            if format == "minimal":
                return {"id": id, "threadId": m["threadId"], "labelIds": m["labelIds"], "historyId": m["historyId"]}
            return m
        return Req(run)

    def attachments(self):
        return FakeAttachments(self.g)


class FakeAttachments(object):
    def __init__(self, g):
        self.g = g

    def get(self, userId, messageId, id):
        def run():
            self.g._n("attachment")
            return {"data": b64(self.g.attachments[id]), "size": len(self.g.attachments[id])}
        return Req(run)


class FakeHistory(object):
    def __init__(self, g):
        self.g = g

    def list(self, userId, startHistoryId, pageToken=None, maxResults=None, historyTypes=None):
        def run():
            self.g._n("history")
            assert set(historyTypes) == {"messageAdded", "messageDeleted", "labelAdded", "labelRemoved"}
            if self.g.history_error:
                raise self.g.history_error
            return {"history": self.g.history_records, "historyId": self.g.history_id}
        return Req(run)


def client_for(fake, **kw):
    return gmail.GmailClient(fake, gmail.UnitThrottle(1e9), sleep=lambda s: None, **kw)


def sample_mailbox():
    pdf = part("application/pdf", filename="Quote 2026.pdf", att_id="ATT1", size=2000, part_id="1", headers=[("Content-Disposition", 'attachment; filename="Quote 2026.pdf"')])
    img = part("image/png", att_id="ATT2", size=300, part_id="2", headers=[("Content-ID", "<logo@x>"), ("Content-Disposition", "inline")])
    big = part("application/zip", filename="huge.zip", att_id="ATT3", size=gmail.ATTACHMENT_CAP + 1, part_id="3")
    small_inline = part("text/csv", filename="tiny.csv", data=b"a,b\n1,2\n", part_id="4")
    return [
        mk_msg("m1", "t1", subject="=?UTF-8?B?w4RwZmVs?= offer", cc="dave@z.com", bcc="Erin <ERIN@z.com>", html_body="<p>html <b>rich</b></p>",
               attachments=[pdf, img, big, small_inline], internal=1700000001000),
        mk_msg("m2", "t1", subject="Re: =?UTF-8?B?w4RwZmVs?= offer", labels=("SENT",), text="reply text", internal=1700000002000, history="101"),
        mk_msg("m3", "t2", subject="Html only", text=None, html_body="<html><head><style>x{}</style></head><body><p>Hello&nbsp;<b>HTML</b> world</p><script>bad()</script></body></html>",
               labels=("INBOX", "Label_1"), internal=1700000003000, history="102"),
        mk_msg("m4", "t2", subject="Spam one", labels=("SPAM",), text="buy now", internal=1700000004000, history="103"),
        mk_msg("m5", "t3", subject="Draft", labels=("DRAFT",), text="draft text", internal=1700000005000, history="104"),
    ]


@pytest.fixture
def gdir(tmp_path):
    return str(tmp_path / "gmail")


def test_parse_message_multipart_headers_bodies_attachments():
    m = sample_mailbox()[0]
    p = gmail.parse_message(m)
    assert p["from_addr"] == "alice@example.com" and p["to_addrs"] == "bob@x.com,carol@y.org" and p["cc_addrs"] == "dave@z.com" and p["bcc_addrs"] == "erin@z.com"
    assert p["subject"] == "Äpfel offer" and p["rfc822_message_id"] == "<m1@mail.example>"
    assert p["body_text"] == "plain body" and p["body_html"] is None                      # text/plain present: html not stored
    assert p["internal_at"] == "2023-11-14T22:13:21.000Z" and p["date_header_at"] == "2023-11-14T22:13:20.000Z"
    assert p["snippet"] == "snip 'm1'" and p["has_attachments"] == 1
    atts = {a["part_id"]: a for a in p["attachments"]}
    assert set(atts) == {"1", "2", "3", "4"}
    assert atts["1"]["filename"] == "Quote 2026.pdf" and atts["1"]["disposition"] == "attachment" and atts["1"]["attachment_id"] == "ATT1"
    assert atts["2"]["disposition"] == "inline" and atts["2"]["content_id"] == "logo@x"
    assert atts["4"]["inline_data"] == b"a,b\n1,2\n" and atts["4"]["attachment_id"] is None
    assert json.loads(p["headers_json"])[0] == ["From", "Alice <Alice@Example.com>"]


def test_parse_message_html_only_and_odd_inputs():
    p = gmail.parse_message(sample_mailbox()[2])
    assert "Hello" in p["body_text"] and "HTML world" in p["body_text"] and "bad()" not in p["body_text"] and "x{}" not in p["body_text"]
    assert p["body_html"].startswith("<html>") and p["attachments"] == []
    # nested multipart/related inside alternative inside mixed, latin-1 body, missing headers, garbage Date
    latin = part("text/plain", data="café".encode("latin-1"), headers=[("Content-Type", "text/plain; charset=ISO-8859-1")], part_id="0.0.0")
    inner = part("multipart/related", parts=[latin], part_id="0.0")
    payload = part("multipart/mixed", parts=[part("multipart/alternative", parts=[inner], part_id="0")], part_id="")
    payload["headers"] = [{"name": "Date", "value": "not a date"}, {"name": "From", "value": "weird"}]
    msg = {"id": "z", "payload": payload}
    q = gmail.parse_message(msg)
    assert q["body_text"] == "café" and q["date_header_at"] is None and q["from_addr"] is None and q["thread_id"] == "z" and q["internal_at"] is None
    assert gmail.addresses('"A, B" <a@x.com>, a@x.com, c@y.org;') == ["a@x.com", "c@y.org"]
    assert gmail.iso_from_date_header("Tue, 14 Nov 2023 22:13:20 +0530") == "2023-11-14T16:43:20.000Z"


def test_full_pull_paginates_stores_everything_and_is_idempotent(con, gdir):
    fake = FakeGmail(sample_mailbox(), page=2, attachments={"ATT1": b"%PDF-fake", "ATT2": b"\x89PNG"})
    s = gmail.sync(con, client_for(fake), data_dir=gdir)
    assert s["complete"] and s["mode"] == "full" and s["messages"] == 5 and s["mailbox"] == "bhanu.enamala@lh2.ai"
    assert fake.counts["list"] == 3 and fake.counts["get_full"] == 5
    assert con.execute("SELECT COUNT(*) FROM ext_gmail_message").fetchone()[0] == 5
    acc = con.execute("SELECT * FROM ext_gmail_account").fetchone()
    assert acc["email_address"] == "bhanu.enamala@lh2.ai" and acc["history_id"] == "500" and acc["messages_total"] == 5 and acc["token_ref"] == "gmail_readonly_token.json"
    assert json.loads(acc["scopes_json"]) == [auth.SCOPE_GMAIL_RO]
    # threads aggregate
    t1 = con.execute("SELECT * FROM ext_gmail_thread WHERE thread_id='t1'").fetchone()
    assert t1["message_count"] == 2 and t1["subject"].endswith("offer") and t1["first_message_at"] < t1["last_message_at"] and t1["snippet"] == "snip 'm2'"
    # labels + message_label
    assert {r[0] for r in con.execute("SELECT label_id FROM ext_gmail_label")} == {"INBOX", "SENT", "Label_1", "SPAM", "DRAFT"}     # SPAM/DRAFT created as placeholders
    assert con.execute("SELECT COUNT(*) FROM ext_gmail_message_label ml JOIN ext_gmail_label l USING(label_pk) WHERE l.label_id='Label_1'").fetchone()[0] == 1
    flags = {r[0]: tuple(r)[1:] for r in con.execute("SELECT message_id, is_sent, is_draft, is_spam_trash, has_attachments FROM ext_gmail_message")}
    assert flags["m2"] == (1, 0, 0, 0) and flags["m5"] == (0, 1, 0, 0) and flags["m4"] == (0, 0, 1, 0) and flags["m1"] == (0, 0, 0, 1)
    # FTS (body text, html-only rendering, addresses) + integrity
    q = lambda term: con.execute("SELECT COUNT(*) FROM ext_gmail_message_fts WHERE ext_gmail_message_fts MATCH ?", (term,)).fetchone()[0]
    assert q("reply") == 1 and q("world") == 1 and q("\"alice example com\"") >= 1 and q("apfel") == 2
    con.execute("INSERT INTO ext_gmail_message_fts(ext_gmail_message_fts) VALUES('integrity-check')")
    # raw JSON on disk, attachments on disk
    raw = con.execute("SELECT raw_path, raw_sha256 FROM ext_gmail_message WHERE message_id='m1'").fetchone()
    blob = open(os.path.join(ROOT, raw["raw_path"]), "rb").read()
    assert json.loads(gzip.decompress(blob))["id"] == "m1"
    import hashlib
    assert hashlib.sha256(blob).hexdigest() == raw["raw_sha256"]
    att = {r["part_id"]: r for r in con.execute("SELECT a.* FROM ext_gmail_attachment a JOIN ext_gmail_message m USING(message_pk) WHERE m.message_id='m1'")}
    assert open(os.path.join(ROOT, att["1"]["stored_path"]), "rb").read() == b"%PDF-fake" and att["1"]["content_sha256"] == hashlib.sha256(b"%PDF-fake").hexdigest()
    assert "/m1/" in att["1"]["stored_path"] and att["1"]["stored_path"].endswith("Quote 2026.pdf")
    assert att["2"]["disposition"] == "inline" and att["2"]["content_id"] == "logo@x" and att["2"]["stored_path"]
    assert att["3"]["stored_path"] is None and att["3"]["size_bytes"] == gmail.ATTACHMENT_CAP + 1                       # over the 25 MB cap: skipped
    assert open(os.path.join(ROOT, att["4"]["stored_path"]), "rb").read() == b"a,b\n1,2\n"
    dq = con.execute("SELECT rule_code, message FROM ops_dq_issue").fetchall()
    assert len(dq) == 1 and dq[0][0] == "gmail_attachment_too_large" and "huge.zip" in dq[0][1]
    assert fake.counts["attachment"] == 2                                                                                  # big one never downloaded
    # sync state + run
    assert con.execute("SELECT cursor_value FROM ops_sync_state WHERE source_system='gmail' AND cursor_name='historyId'").fetchone()[0] == "500"
    assert con.execute("SELECT cursor_value FROM ops_sync_state WHERE cursor_name='full_page_token'").fetchone()[0] is None
    assert con.execute("SELECT status FROM ops_import_run WHERE kind='gmail_pull'").fetchone()[0] == "succeeded"
    # second run: incremental, nothing new, nothing duplicated, no message fetched again
    fake.counts.clear()
    s2 = gmail.sync(con, client_for(fake), data_dir=gdir)
    assert s2["mode"] == "incremental" and s2["messages"] == 0 and "get_full" not in fake.counts
    assert con.execute("SELECT COUNT(*) FROM ext_gmail_message").fetchone()[0] == 5
    # forced full listing: all ids are known, nothing is fetched or rewritten
    fake.counts.clear()
    s3 = gmail.sync(con, client_for(fake), full=True, data_dir=gdir)
    assert s3["mode"] == "full" and s3["messages"] == 0 and "get_full" not in fake.counts and fake.counts["list"] == 3
    assert con.execute("SELECT COUNT(*) FROM ext_gmail_message").fetchone()[0] == 5


def test_pull_resumes_from_the_checkpoint_after_an_interruption(con, gdir):
    fake = FakeGmail(sample_mailbox(), page=2)
    s1 = gmail.sync(con, client_for(fake), max_messages=3, data_dir=gdir)
    assert not s1["complete"] and s1["messages"] == 3
    assert con.execute("SELECT COUNT(*) FROM ext_gmail_message").fetchone()[0] == 3
    assert con.execute("SELECT cursor_value FROM ops_sync_state WHERE cursor_name='historyId'").fetchone() is None            # not complete -> no incremental cursor yet
    assert con.execute("SELECT cursor_value FROM ops_sync_state WHERE cursor_name='full_start_history_id'").fetchone()[0] == "500"
    assert con.execute("SELECT status FROM ops_import_run WHERE kind='gmail_pull'").fetchone()[0] == "partial"
    fake.history_id = "999"                                                                                                  # mailbox moved on meanwhile
    fake.getlog.clear()
    s2 = gmail.sync(con, client_for(fake), data_dir=gdir)
    assert s2["complete"] and s2["resumed_from_page_token"] and s2["messages"] == 2
    fetched = [i for i, f in fake.getlog if f == "full"]
    assert sorted(fetched) == ["m4", "m5"]                                                                                   # m1-m3 were not fetched again
    assert con.execute("SELECT COUNT(*) FROM ext_gmail_message").fetchone()[0] == 5
    # the incremental cursor is the history id seen when the FULL pull started (500), not the later 999
    assert con.execute("SELECT cursor_value FROM ops_sync_state WHERE cursor_name='historyId'").fetchone()[0] == "500"


def test_unreadable_message_keeps_the_full_pull_open_and_is_retried(con, gdir):
    fake = FakeGmail(sample_mailbox(), page=2)
    fake.get_fail["m4"] = [http_error(400, "badRequest")]                                                                    # hard failure (not 404, not retryable)
    s = gmail.sync(con, client_for(fake), data_dir=gdir)
    assert s["failed"] == 1 and not s["complete"] and s["messages"] == 4
    assert con.execute("SELECT COUNT(*) FROM ext_gmail_message WHERE message_id='m4'").fetchone()[0] == 0
    assert con.execute("SELECT cursor_value FROM ops_sync_state WHERE cursor_name='historyId'").fetchone() is None            # not advanced: m4 would be lost for good
    s2 = gmail.sync(con, client_for(fake), data_dir=gdir)
    assert s2["complete"] and s2["messages"] == 1 and s2["mode"] == "full"
    assert con.execute("SELECT COUNT(*) FROM ext_gmail_message").fetchone()[0] == 5
    assert con.execute("SELECT COUNT(*) FROM (SELECT message_id FROM ext_gmail_message GROUP BY message_id HAVING COUNT(*) > 1)").fetchone()[0] == 0


def test_rate_limited_requests_inside_a_batch_are_retried_and_missing_ones_skipped(con, gdir):
    fake = FakeGmail(sample_mailbox(), page=10)
    fake.order.append("gone")                                                                                                # listed but deleted before it is fetched -> 404
    fake.ghosts["gone"] = "t9"
    fake.get_fail["m2"] = [http_error(429, "rateLimitExceeded"), http_error(403, "userRateLimitExceeded")]
    sleeps = []
    cl = gmail.GmailClient(fake, gmail.UnitThrottle(1e9), sleep=sleeps.append)
    s = gmail.sync(con, cl, data_dir=gdir)
    assert s["complete"] and s["messages"] == 5 and s["missing"] == 1 and s["failed"] == 0
    assert len(sleeps) == 2 and sleeps[1] > sleeps[0] * 0.9                                                                  # exponential backoff
    assert con.execute("SELECT COUNT(*) FROM ext_gmail_message WHERE message_id='m2'").fetchone()[0] == 1


def test_incremental_sync_uses_history(con, gdir):
    fake = FakeGmail(sample_mailbox(), page=10)
    gmail.sync(con, client_for(fake), data_dir=gdir)
    fake.messages["m6"] = mk_msg("m6", "t1", subject="Follow up", text="new mail body", internal=1700000009000, history="600")
    fake.messages["m2"]["labelIds"] = ["SENT", "Label_1"]
    fake.messages["m2"]["historyId"] = "601"
    fake.history_id = "610"
    fake.history_records = [
        {"id": "600", "messagesAdded": [{"message": {"id": "m6", "threadId": "t1"}}]},
        {"id": "601", "labelsAdded": [{"message": {"id": "m2", "threadId": "t1"}, "labelIds": ["Label_1"]}]},
        {"id": "602", "messagesDeleted": [{"message": {"id": "m4", "threadId": "t2"}}]},
        {"id": "603", "messagesAdded": [{"message": {"id": "m4", "threadId": "t2"}}, {"message": {"id": "nogo", "threadId": "t9"}}], "messagesDeleted": [{"message": {"id": "nogo"}}]},
    ]
    fake.counts.clear()
    fake.getlog.clear()
    s = gmail.sync(con, client_for(fake), data_dir=gdir)
    assert s["mode"] == "incremental" and s["complete"] and s["messages"] == 1 and s["label_updates"] == 1 and s["deleted_upstream"] == 2
    assert fake.counts.get("list") is None and fake.counts["history"] == 1
    assert ("m6", "full") in fake.getlog and ("m2", "minimal") in fake.getlog and not [g for g in fake.getlog if g[0] in ("m4", "nogo")]
    assert con.execute("SELECT cursor_value FROM ops_sync_state WHERE cursor_name='historyId'").fetchone()[0] == "610"
    assert con.execute("SELECT history_id FROM ext_gmail_account").fetchone()[0] == "610"
    labs = {r[0] for r in con.execute("SELECT l.label_id FROM ext_gmail_message_label ml JOIN ext_gmail_label l USING(label_pk) JOIN ext_gmail_message m USING(message_pk) WHERE m.message_id='m2'")}
    assert labs == {"SENT", "Label_1"}
    assert con.execute("SELECT COUNT(*) FROM ext_gmail_message WHERE message_id='m4'").fetchone()[0] == 1                       # deleted upstream: archive keeps it
    assert con.execute("SELECT message_count FROM ext_gmail_thread WHERE thread_id='t1'").fetchone()[0] == 3


def test_expired_history_falls_back_to_a_full_listing(con, gdir):
    fake = FakeGmail(sample_mailbox(), page=10)
    gmail.sync(con, client_for(fake), data_dir=gdir)
    fake.messages["m7"] = mk_msg("m7", "t7", subject="new", text="after expiry", internal=1700000010000, history="700")
    fake.order.insert(0, "m7")
    fake.history_error = http_error(404, "notFound", "Requested entity was not found.")
    fake.history_id = "800"
    s = gmail.sync(con, client_for(fake), data_dir=gdir)
    assert s["complete"] and s["mode"] == "full(fallback)" and s["messages"] == 1
    assert con.execute("SELECT COUNT(*) FROM ext_gmail_message").fetchone()[0] == 6
    assert con.execute("SELECT cursor_value FROM ops_sync_state WHERE cursor_name='historyId'").fetchone()[0] == "800"


def test_no_attachment_mode_stores_metadata_only(con, gdir):
    fake = FakeGmail(sample_mailbox()[:1], page=10, attachments={"ATT1": b"x", "ATT2": b"y"})
    gmail.sync(con, client_for(fake), attachments=False, data_dir=gdir)
    assert fake.counts.get("attachment") is None
    assert con.execute("SELECT COUNT(*) FROM ext_gmail_attachment").fetchone()[0] == 4
    assert con.execute("SELECT COUNT(*) FROM ext_gmail_attachment WHERE stored_path IS NOT NULL").fetchone()[0] == 1           # only the tiny inline-data part


def test_refresh_labels_updates_stored_messages(con, gdir):
    fake = FakeGmail(sample_mailbox(), page=10)
    gmail.sync(con, client_for(fake), data_dir=gdir)
    fake.messages["m1"]["labelIds"] = ["INBOX", "Label_1"]
    assert gmail.refresh_labels(con, client_for(fake), "Bhanu.Enamala@lh2.ai") == 5
    assert con.execute("SELECT COUNT(*) FROM ext_gmail_message_label ml JOIN ext_gmail_label l USING(label_pk) WHERE l.label_id='Label_1'").fetchone()[0] == 2


def test_throttle_limits_unit_rate():
    t = [0.0]
    slept = []

    def sleep(s):
        slept.append(s)
        t[0] += s
    th = gmail.UnitThrottle(100, clock=lambda: t[0], sleep=sleep)
    for _ in range(5):
        th.wait(50)                                                                                                          # 5 x 50 units at 100 units/s = 2 s for the last four
    assert sum(slept) == pytest.approx(2.0)


# ---- --check -------------------------------------------------------------------------------------------------------------
def test_check_reports_missing_token_without_network(tmp_path, monkeypatch, capsys):
    import socket

    def no_network(*a, **k):
        raise AssertionError("network used by --check")
    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(auth, "SECRETS_DIR", str(tmp_path))
    rep = gmail.check("gmail_readonly_token.json")
    assert rep["ready"] is False and rep["token"]["exists"] is False and "tools/gmail_auth.py" in rep["next_step"]
    assert gmail.main(["--check", "--token", "gmail_readonly_token.json"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ready"] is False and out["online_checked"] is False


def test_check_accepts_a_good_token_and_rejects_wrong_scope(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "SECRETS_DIR", str(tmp_path))
    tok = {"token": "T", "refresh_token": "R", "client_id": "C", "client_secret": "S", "scopes": [auth.SCOPE_GMAIL_RO], "expiry": "2020-01-01T00:00:00.000000Z", "account": "bhanu.enamala@lh2.ai"}
    p = tmp_path / "gmail_readonly_token.json"
    p.write_text(json.dumps(tok))
    os.chmod(str(p), 0o600)
    rep = gmail.check("gmail_readonly_token.json")
    assert rep["ready"] is True and rep["token"]["account"] == "bhanu.enamala@lh2.ai" and rep["token"]["access_token_valid_now"] is False
    tok["scopes"] = ["https://www.googleapis.com/auth/gmail.send"]
    p.write_text(json.dumps(tok))
    rep = gmail.check("gmail_readonly_token.json")
    assert rep["ready"] is False and "scope" in rep["token"]["problem"]
    assert '"R"' not in json.dumps(rep) and '"S"' not in json.dumps(rep)


def test_the_tests_never_touch_the_real_database_or_secrets():
    assert os.path.abspath(auth.SECRETS_DIR) == os.path.join(ROOT, "secrets")


def test_credentials_are_built_lazily_and_fall_back_in_order(con, cat):
    built = []

    def factory(name):
        built.append(name)
        if name == "hubspot_sheets_token":
            raise auth.TokenError("revoked")
        return FakeSheets(human_data())
    pool = sheets.ReaderPool(per_minute=0, sleep=lambda s: None, factory=factory)
    plan = [p for p in sheets.build_plan(con) if p["spreadsheet_id"] == "S_HUMAN"]
    out = sheets.run_pull(con, pool, plan, data_dir=None)
    assert out["pulled"] == 1 and built == ["hubspot_sheets_token", "companyops_sheets_token"]          # the SA was never loaded
    con.execute("UPDATE ops_sync_state SET cursor_value='x'")
    built.clear()
    pool2 = sheets.ReaderPool(per_minute=0, sleep=lambda s: None, factory=lambda n: (_ for _ in ()).throw(auth.TokenError("all dead")))
    out = sheets.run_pull(con, pool2, plan, data_dir=None)
    assert out["failed"] == 1 and "no usable credential" in json.dumps(out["failures"])
