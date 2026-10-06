"""leadgen.norm: the normalisers must produce exactly what the database CHECKs accept, on the quirks measured in the real data."""
import sqlite3

import pytest

from leadgen import db, norm


@pytest.fixture
def con(tmp_path):
    p = str(tmp_path / "n.sqlite")
    db.migrate(p)
    c = db.connect(p)
    yield c
    c.close()


def test_domains():
    assert norm.norm_domain('"google.com') == "google.com"
    assert norm.norm_domain("​​google.com") == "google.com"
    assert norm.norm_domain("https://WWW.Acme.COM:8443/path?q=1#x") == "acme.com"
    assert norm.norm_domain("user@mail.acme.io") == "mail.acme.io"
    assert norm.norm_domain("pegàsehealth.com").startswith("xn--") and norm.norm_domain("acme.com.") == "acme.com"
    for junk in ("", None, "acme", "a..com", "-x.com", "not a domain", "http://", "x.c"):
        assert norm.norm_domain(junk) is None, junk


def test_linkedin_urls():
    assert norm.norm_linkedin_company("https://in.linkedin.com/company/Big-Whale/?trk=x") == "company/big-whale"
    assert norm.norm_linkedin_company("linkedin.com/company/63-moons-%e2%84%a2") == "company/63-moons-™"
    assert norm.norm_linkedin_company("https://www.linkedin.com/company/'aastha-health-care'") == "company/aastha-health-care"
    assert norm.norm_linkedin_company("https://www.linkedin.com/school/MIT/") == "school/mit"
    assert norm.norm_linkedin_company("https://www.linkedin.com/in/jane") is None
    assert norm.norm_linkedin_person("https://www.linkedin.com/in/Jane-Doe/") == "in/jane-doe"
    assert norm.norm_linkedin_person("https://www.linkedin.com/company/acme") is None
    assert norm.norm_linkedin_person("https://www.linkedin.com/pub/jane/1/2/3") is None


def test_ids_names_emails_cin():
    assert norm.norm_email(" Jane@X.COM ") == "jane@x.com" and norm.norm_email("nope") is None and norm.norm_email("a b@x.com") is None
    assert norm.norm_company_name("  Big   WHALE Corp ") == "big whale corp" and norm.norm_company_name("  ") is None
    assert norm.norm_cin("u72900ka2019ptc128848") == "U72900KA2019PTC128848" and norm.norm_cin("") is None and norm.norm_cin("AAE-7433") is None
    assert norm.norm_llpin("aae-7433") == "AAE-7433" and norm.norm_llpin("U72900KA2019PTC128848") is None
    assert norm.norm_country("India") == "IN" and norm.norm_country(" in ") == "IN" and norm.norm_country("UK") == "GB" and norm.norm_country("Narnia") is None


def test_phones():
    for raw in ("+91 98765 43210", "+91-98765-43210", "0091 98765 43210", "09876543210", "9876543210", "91 98765 43210", "(+91) 98765 43210"):
        assert norm.norm_phone_e164(raw) == "+919876543210", raw
    assert norm.norm_phone_e164("+1 415 555 0100") == "+14155550100"
    for raw in ("", None, "12345", "abc", "+91 12", "98765"):
        assert norm.norm_phone_e164(raw) is None, raw


def test_timestamps_every_legacy_spelling():
    assert norm.to_utc_ms("2026-09-29 14:02:34") == "2026-09-29T14:02:34.000Z"                 # tam: naive SQLite datetime('now') = UTC
    assert norm.to_utc_ms("2026-09-29T14:02:34.123456Z") == "2026-09-29T14:02:34.123Z"          # itsvc microseconds
    assert norm.to_utc_ms("2026-09-29T14:02:34+00:00") == "2026-09-29T14:02:34.000Z"            # corpus
    assert norm.to_utc_ms("2026-09-29T14:02:34.123456+00:00") == "2026-09-29T14:02:34.123Z"     # pipeline
    assert norm.to_utc_ms("2026-09-29T14:02:34") == "2026-09-29T14:02:34.000Z"                  # radar naive
    assert norm.to_utc_ms("2026-09-29") == "2026-09-29T00:00:00.000Z"
    assert norm.to_utc_ms("2026-10-04T18:30:00+0530") == "2026-10-04T13:00:00.000Z"             # gsheets catalog 'generated'
    assert norm.to_utc_ms(1759586554.5) == "2025-10-04T14:02:34.500Z" and norm.to_utc_ms("1759586554.5") == "2025-10-04T14:02:34.500Z"
    assert norm.to_utc_ms("2026-07-15 20:33:27", assume="ist") == "2026-07-15T15:03:27.000Z"    # STAGE_TRANSITIONS.csv wall clock
    for junk in ("", None, "2026-07", "2026", "Tue, 07 Jul 2026 10:00:00 GMT", "2026-13-45", "2026-02-30 00:00:00"):
        assert norm.to_utc_ms(junk) is None, junk


def test_every_normaliser_output_is_accepted_by_the_database(con):
    ts = norm.to_utc_ms("2026-07-15 20:33:27", assume="ist")
    con.execute("INSERT INTO company (canonical_name, name_norm, first_seen_at, hq_country) VALUES ('A','a',?,?)", (ts, norm.norm_country("India")))
    ins = "INSERT INTO company_identifier (company_id, identifier_type, value_norm) VALUES (1,?,?)"
    con.execute(ins, ("root_domain", norm.norm_domain("https://WWW.Acme.COM/x")))
    con.execute(ins, ("linkedin_company", norm.norm_linkedin_company("https://in.linkedin.com/company/Acme-Corp/")))
    con.execute(ins, ("cin", norm.norm_cin("u72900ka2019ptc128848")))
    con.execute(ins, ("llpin", norm.norm_llpin("aae-7433")))
    con.execute("INSERT INTO contact (email_norm, linkedin_person_norm) VALUES (?,?)", (norm.norm_email("A@B.com"), norm.norm_linkedin_person("linkedin.com/in/Jane/")))
    con.execute("INSERT INTO contact_phone (contact_id, phone_raw, phone_e164, phone_type) VALUES (1,'09876543210',?, 'mobile')", (norm.norm_phone_e164("09876543210"),))
    assert con.execute("SELECT is_indian_mobile FROM contact_phone").fetchone()[0] == 1
    sp = "INSERT INTO suppression (kind, match_type, match_value_norm) VALUES ('never_push',?,?)"
    for t, v in (("domain", norm.norm_domain("https://WWW.Whale.COM/")), ("company_name", norm.norm_company_name("Big  Whale")),
                 ("linkedin_company", norm.norm_linkedin_company("https://www.linkedin.com/company/Big-Whale/")),
                 ("linkedin_person", norm.norm_linkedin_person("https://www.linkedin.com/in/CEO/")), ("email", norm.norm_email("CEO@whale.com")),
                 ("phone", norm.norm_phone_e164("98765 43211")), ("cin", norm.norm_cin("u72900ka2019ptc128849"))):
        con.execute(sp, (t, v))
