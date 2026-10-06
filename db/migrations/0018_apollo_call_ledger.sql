-- 0018_apollo_call_ledger.sql
-- Local record of every Apollo API call made from this repo (leadgen/apollo_ledger.py is the only writer).
-- Purpose: credits per call, what each call returned (mobile / email / person / org), and where the result went (HubSpot deal or
-- contact, or held here).  Nothing about the individual is kept beyond the Apollo person id, the LinkedIn slug, and the company domain
-- needed to match a later HubSpot push.  No phone number or e-mail is stored in this table: the phone and e-mail live in
-- contact_phone / contact as before.  Company-level facts only, per CONTEXT.md rule 9.
--
-- Rules:
--   * credits_charged is the value Apollo's credit meter reports for the call when it can be read, else the documented billing rule,
--     and charge_basis says which one.  Nothing is recorded as measured when it was only estimated.
--   * An Apollo call that is not logged here must not be made.

CREATE TABLE ext_apollo_call (
  apollo_call_id   INTEGER PRIMARY KEY,
  called_at        TEXT    NOT NULL CHECK (called_at IS strftime('%Y-%m-%dT%H:%M:%fZ', called_at)),  -- UTC time of the request
  called_at_ist    TEXT    NOT NULL,  -- same moment in IST, for the daily reports
  endpoint         TEXT    NOT NULL CHECK (endpoint LIKE '/%'),  -- e.g. '/people/match', '/organizations/enrich', '/mixed_companies/search'
  http_method      TEXT    NOT NULL CHECK (http_method IN ('GET', 'POST', 'PUT', 'DELETE')),
  purpose          TEXT    NOT NULL CHECK (purpose <> ''),  -- one line: what the call was for
  caller           TEXT    NOT NULL CHECK (caller <> ''),  -- script or tool name, e.g. 'tools/cobol_enrich_push.py'
  repo             TEXT    NOT NULL DEFAULT 'LeadGenMonolith',
  request_key      TEXT,  -- domain, LinkedIn slug or Apollo id the call was about (no e-mail, no phone)
  http_status      INTEGER CHECK (http_status IS NULL OR http_status BETWEEN 100 AND 599),
  result_found     INTEGER CHECK (result_found IS NULL OR result_found IN (0, 1)),  -- 1 when the call returned a record
  person_id        TEXT,  -- Apollo person id returned, if any
  org_id           TEXT,  -- Apollo organisation id returned, if any
  mobile_returned  INTEGER CHECK (mobile_returned IS NULL OR mobile_returned IN (0, 1)),  -- 1 when a mobile number was returned (never the number itself)
  email_returned   INTEGER CHECK (email_returned IS NULL OR email_returned IN (0, 1)),
  credits_charged  REAL    CHECK (credits_charged IS NULL OR credits_charged >= 0),
  charge_basis     TEXT    NOT NULL DEFAULT 'unknown' CHECK (charge_basis IN ('meter_delta', 'meter_reported', 'documented_rule', 'estimated', 'unknown')),
  meter_before     REAL,  -- lead_credit left_over before the call, when read
  meter_after      REAL,  -- lead_credit left_over after the call, when read
  pushed_to        TEXT CHECK (pushed_to IS NULL OR pushed_to IN ('main', 'companyops', 'rat', 'held_here')),  -- where the result went
  pushed_hs_id     TEXT CHECK (pushed_hs_id IS NULL OR (pushed_hs_id <> '' AND pushed_hs_id NOT GLOB '*[^0-9]*')),  -- HubSpot deal or contact id created from it
  error            TEXT  -- short error class on failure, never a response body
) STRICT;
CREATE INDEX ix_ext_apollo_call_time ON ext_apollo_call(called_at);
CREATE INDEX ix_ext_apollo_call_endpoint ON ext_apollo_call(endpoint, called_at);
CREATE INDEX ix_ext_apollo_call_purpose ON ext_apollo_call(caller, purpose);

-- v_apollo_daily: credits per IST day, per endpoint and per caller (counts and sums only).
CREATE VIEW v_apollo_daily AS
SELECT substr(called_at_ist, 1, 10) AS ist_day, endpoint, caller,
       count(*) AS calls, sum(credits_charged) AS credits, sum(mobile_returned) AS mobiles, sum(email_returned) AS emails,
       sum(pushed_hs_id IS NOT NULL) AS pushed
  FROM ext_apollo_call GROUP BY 1, 2, 3;
