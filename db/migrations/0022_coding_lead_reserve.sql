-- 0022_coding_lead_reserve.sql
-- The Coding cold-call rebalance pool. When a rep's Cold Call count goes above the daily target (100), the excess is
-- removed from HubSpot (archived there, recoverable 90 days) and kept here in full, so it is never lost, only parked.
-- When a rep is below target, a row from here is pushed back into that rep's HubSpot (same portal or cross-portal)
-- and marked reassigned. Every lead is accounted for in exactly one of: a rep's HubSpot cold-call queue, or this table
-- with status='available'.

CREATE TABLE ext_coding_lead_reserve (
  reserve_id          INTEGER PRIMARY KEY,
  status               TEXT    NOT NULL CHECK (status IN ('available', 'reassigned')) DEFAULT 'available',
  source_account       TEXT    NOT NULL CHECK (source_account IN ('main', 'companyops', 'rat')),
  source_pipeline      TEXT    NOT NULL,
  source_owner_name    TEXT    NOT NULL,
  source_hs_deal_id    TEXT    NOT NULL,
  source_hs_contact_id TEXT,
  dealname             TEXT    NOT NULL,
  first_name           TEXT,
  last_name            TEXT,
  title                TEXT,
  company               TEXT,
  phone_e164            TEXT,
  email                  TEXT,
  linkedin_url           TEXT,
  lead_source_label      TEXT,
  dealstage_label         TEXT    NOT NULL,  -- the stage it was removed from, e.g. 'Cold Call'
  removed_at               TEXT    NOT NULL CHECK (removed_at IS strftime('%Y-%m-%dT%H:%M:%fZ', removed_at)),
  reassigned_account       TEXT    CHECK (reassigned_account IN ('main', 'companyops', 'rat')),
  reassigned_owner_name    TEXT,
  reassigned_hs_deal_id    TEXT,
  reassigned_hs_contact_id TEXT,
  reassigned_at             TEXT    CHECK (reassigned_at IS NULL OR reassigned_at IS strftime('%Y-%m-%dT%H:%M:%fZ', reassigned_at)),
  created_at                 TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  updated_at                  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

CREATE INDEX idx_coding_lead_reserve_status ON ext_coding_lead_reserve(status);

CREATE VIEW v_coding_lead_reserve_available AS
SELECT reserve_id, source_account, source_owner_name, dealname, first_name, last_name, phone_e164, linkedin_url,
       lead_source_label, dealstage_label, removed_at
  FROM ext_coding_lead_reserve
 WHERE status = 'available'
 ORDER BY removed_at;
