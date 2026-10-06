-- 0021_apollo_grants.sql
-- Credit top-ups and the meter readings used to reconcile the local Apollo ledger (ext_apollo_call) against Apollo's own meter.
-- Every credit that enters or leaves the account is one row here or in ext_apollo_call; nothing is estimated silently.

CREATE TABLE ext_apollo_grant (
  grant_id     INTEGER PRIMARY KEY,
  granted_at   TEXT    NOT NULL CHECK (granted_at IS strftime('%Y-%m-%dT%H:%M:%fZ', granted_at)),
  credits      REAL    NOT NULL CHECK (credits > 0),
  note         TEXT    NOT NULL CHECK (note <> '')  -- e.g. 'top-up 150k, approved by user 2026-10-06'
) STRICT;

CREATE TABLE ext_apollo_meter_read (
  meter_read_id INTEGER PRIMARY KEY,
  read_at       TEXT    NOT NULL CHECK (read_at IS strftime('%Y-%m-%dT%H:%M:%fZ', read_at)),
  lead_left     REAL    NOT NULL,   -- lead_credit left_over reported by Apollo's usage endpoint
  lead_consumed REAL    NOT NULL,   -- lead_credit consumed reported by Apollo
  note          TEXT
) STRICT;

-- v_apollo_dot_to_dot: every logged call, one row each, with the account it was about and what it returned.
CREATE VIEW v_apollo_dot_to_dot AS
SELECT apollo_call_id, called_at_ist, caller, segment, purpose, endpoint, request_key, http_status,
       person_id, org_id, mobile_returned, email_returned, credits_charged, charge_basis, outcome, outcome_reason, pushed_to, pushed_hs_id
  FROM ext_apollo_call;
