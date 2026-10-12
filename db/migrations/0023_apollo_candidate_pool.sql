-- 0023_apollo_candidate_pool.sql
-- Fresh Apollo-revealed candidates that have NOT been pushed to any HubSpot portal yet - a different lifecycle
-- from ext_coding_lead_reserve (0022), which only holds leads that already existed in HubSpot and were pulled
-- back out. This table is the landing spot for "we already spent the match+reveal credit and have a real,
-- callable contact, but no rep has been named yet." Apollo itself does NOT remember a reveal (confirmed live,
-- 2026-10-10: re-revealing the same person_id charged the full 9 credits again) - apollo_person_id here is our
-- own cache so a future sourcing run can skip re-paying for a person we've already revealed once.
-- Lifecycle: 'available' (sitting here, nobody asked for it yet) -> 'assigned' (user named a rep, we pushed it
-- to their HubSpot portal) - every row is accounted for in exactly one of those two states, never deleted.

CREATE TABLE ext_apollo_candidate_pool (
  candidate_id         INTEGER PRIMARY KEY,
  status                TEXT    NOT NULL CHECK (status IN ('available', 'assigned')) DEFAULT 'available',
  apollo_person_id      TEXT    NOT NULL,
  apollo_org_id          TEXT,
  company                 TEXT    NOT NULL,
  first_name               TEXT,
  last_name                 TEXT,
  title                      TEXT,
  phone_e164                  TEXT    NOT NULL,  -- mobile-mandatory rule: this table only ever holds a real, revealed +91 mobile
  email                         TEXT,
  linkedin_url                   TEXT,
  icp_segment                     TEXT    NOT NULL,  -- the keyword/sector this was sourced under, e.g. 'telemedicine'
  source_note                       TEXT,  -- free text, e.g. 'Apollo ICP Sourcing (telemedicine, 100-500, India)'
  discovered_at                       TEXT    NOT NULL CHECK (discovered_at IS strftime('%Y-%m-%dT%H:%M:%fZ', discovered_at)),
  assigned_account                      TEXT    CHECK (assigned_account IN ('main', 'companyops', 'rat')),
  assigned_pipeline                       TEXT,
  assigned_owner_name                       TEXT,
  assigned_hs_deal_id                         TEXT,
  assigned_hs_contact_id                        TEXT,
  assigned_at                                     TEXT    CHECK (assigned_at IS NULL OR assigned_at IS strftime('%Y-%m-%dT%H:%M:%fZ', assigned_at)),
  created_at                                        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  updated_at                                          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

CREATE INDEX idx_apollo_candidate_pool_status ON ext_apollo_candidate_pool(status);
CREATE INDEX idx_apollo_candidate_pool_person ON ext_apollo_candidate_pool(apollo_person_id);

CREATE VIEW v_apollo_candidate_pool_available AS
SELECT candidate_id, company, first_name, last_name, title, phone_e164, email, linkedin_url, icp_segment,
       source_note, discovered_at
  FROM ext_apollo_candidate_pool
 WHERE status = 'available'
 ORDER BY discovered_at;
