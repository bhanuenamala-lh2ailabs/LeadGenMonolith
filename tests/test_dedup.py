"""Dedup gate: pure-function and local-index tests (no network)."""
import sqlite3

from leadgen import dedup


def _db():
    con = sqlite3.connect(":memory:")
    con.executescript("""
    CREATE TABLE deal (deal_id INTEGER PRIMARY KEY, account_id TEXT, hs_deal_id TEXT, dealname TEXT, lh2_domain TEXT, is_archived INT DEFAULT 0);
    CREATE TABLE company_account_link (link_id INTEGER PRIMARY KEY, hs_domain TEXT, lh2_domain TEXT);
    CREATE TABLE deal_company (deal_id INT, link_id INT);
    CREATE TABLE contact (contact_id INTEGER PRIMARY KEY, email_norm TEXT, linkedin_person_norm TEXT, is_archived INT DEFAULT 0);
    CREATE TABLE deal_contact (deal_id INT, contact_id INT);
    CREATE TABLE contact_phone (contact_id INT, phone_e164 TEXT);
    INSERT INTO deal VALUES (1,'companyops','900','AiSensy',NULL,0),(2,'main','901','Archived Co',NULL,1),(3,'rat','902','Zeta Labs',NULL,0);
    INSERT INTO company_account_link VALUES (1,'aisensy.com',NULL),(2,'archivedco.com',NULL);
    INSERT INTO deal_company VALUES (1,1),(2,2);
    INSERT INTO contact VALUES (1,'a@zeta.io','zeta-person',0);
    INSERT INTO deal_contact VALUES (3,1);
    INSERT INTO contact_phone VALUES (1,'+919876500001');
    """)
    return con


def test_phone_variants_cover_stored_formats():
    v = dedup.phone_variants("+91 99301 32026")
    assert "+919930132026" in v and "9930132026" in v and "09930132026" in v and "919930132026" in v
    assert dedup.phone_variants("") == []


def test_lead_key_prefers_domain():
    assert dedup.lead_key({"domain": "https://www.AiSensy.com/", "company": "x"}) == "aisensy.com"
    assert dedup.lead_key({"company": "Zeta Labs"}) == "zeta labs"


def test_local_check_blocks_on_domain_name_phone_and_email():
    con = _db()
    leads = [dict(company="AiSensy", domain="aisensy.com", email="", phone="", linkedin=""),
             dict(company="Other Name", domain="zeta.io", email="A@zeta.io", phone="+91 98765 00001", linkedin=""),
             dict(company="Fresh Co", domain="fresh.co", email="", phone="+919000000000", linkedin="")]
    hits = dedup.check(leads, con=con, live=False)
    assert {h["portal"] for h in hits["aisensy.com"]} == {"companyops"}
    assert {h["kind"] for h in hits["zeta.io"]} >= {"email", "phone"} and all(h["portal"] == "rat" for h in hits["zeta.io"])
    assert "fresh.co" not in hits


def test_archived_deals_do_not_block():
    hits = dedup.check([dict(company="Archived Co", domain="archivedco.com", email="", phone="", linkedin="")], con=_db(), live=False)
    assert not hits
