-- 0027_candidate_pool_reveal_detail.sql
-- The candidate pool (0023) only ever captured what's needed to push a contact — it has no room for the
-- things needed to explain WHY the lead was sourced, which is what the caller actually needs to trust it.
-- Adds the fields a push-time "why this deal" note is built from: Apollo's own match_confidence (how sure
-- Apollo itself is this is the right person), the org's ACTUAL confirmed tech stack (from the /people/match
-- reveal response's organization.current_technologies[] - already paid for at reveal time, zero extra
-- credit cost to also store it), and the raw reveal JSON so nothing is lost if a field wasn't thought of yet.

ALTER TABLE ext_apollo_candidate_pool ADD COLUMN match_confidence TEXT
  CHECK (match_confidence IS NULL OR match_confidence IN ('high', 'medium', 'low', 'none'));
ALTER TABLE ext_apollo_candidate_pool ADD COLUMN confirmed_technologies_json TEXT
  CHECK (confirmed_technologies_json IS NULL OR json_valid(confirmed_technologies_json));
ALTER TABLE ext_apollo_candidate_pool ADD COLUMN raw_reveal_json TEXT
  CHECK (raw_reveal_json IS NULL OR json_valid(raw_reveal_json));
ALTER TABLE ext_apollo_candidate_pool ADD COLUMN source_run_id INTEGER REFERENCES ext_sourcing_run(run_id);
