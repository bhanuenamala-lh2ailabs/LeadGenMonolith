-- 0019_apollo_outcome.sql
-- Adds the segment a call belongs to and what became of its result (pushed to HubSpot, discarded with a reason, or held locally).
-- Every row written from now on carries these; rows written before this migration keep outcome 'unrecorded'.

ALTER TABLE ext_apollo_call ADD COLUMN segment TEXT NOT NULL DEFAULT 'unassigned'
  CHECK (segment <> '');
ALTER TABLE ext_apollo_call ADD COLUMN outcome TEXT NOT NULL DEFAULT 'unrecorded'
  CHECK (outcome IN ('unrecorded', 'pushed_hubspot', 'discarded', 'held_local', 'no_result'));
ALTER TABLE ext_apollo_call ADD COLUMN outcome_reason TEXT
  CHECK (outcome_reason IS NULL OR outcome_reason <> '');

CREATE INDEX ix_ext_apollo_call_segment ON ext_apollo_call(segment, called_at);
CREATE INDEX ix_ext_apollo_call_outcome ON ext_apollo_call(outcome, called_at);

-- v_apollo_audit: the audit view. Filter by ist_day range, segment, caller or outcome.
CREATE VIEW v_apollo_audit AS
SELECT substr(called_at_ist, 1, 10) AS ist_day, segment, caller, purpose, endpoint,
       count(*) AS calls, sum(credits_charged) AS credits,
       sum(mobile_returned) AS mobiles, sum(email_returned) AS emails,
       sum(outcome = 'pushed_hubspot') AS pushed, sum(outcome = 'discarded') AS discarded,
       sum(outcome = 'held_local') AS held_local, sum(outcome = 'no_result') AS no_result,
       sum(outcome = 'unrecorded') AS unrecorded
  FROM ext_apollo_call GROUP BY 1, 2, 3, 4, 5;
