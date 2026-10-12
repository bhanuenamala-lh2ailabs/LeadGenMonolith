-- 0024_icp_sourcing_runs.sql
-- Repeatable-process tracking for the cluster-by-cluster, vertical-by-vertical, sub-ICP-by-sub-ICP Apollo
-- people-search sourcing we're starting with Cluster 2 / Manufacturing / Discrete manufacturing and intend to
-- run the same way for every remaining sub-ICP, vertical and cluster after it.
--
-- ext_apollo_call (0018) already tracks every raw Apollo HTTP call + credits + timestamp at the call level, but
-- it has no structured cluster/vertical/sub_icp/headcount_band columns and doesn't store the actual request
-- body or the people returned - it's a ledger, not a sourcing-run record. This migration adds that layer on top,
-- linked by apollo_call_id so credits/timestamp are never duplicated, just referenced.
--
-- ext_sourcing_run = one row per API call made for ICP sourcing (the "input": exact body, page, headcount band,
--   which cluster/vertical/sub_icp it belongs to).
-- ext_sourcing_result = one row per person Apollo returned in that run (the "output": raw record, title-priority
--   rank, and whether our dedup-by-org logic kept it as the one contact per company).
-- Nothing here is a revealed contact yet (no phone/email) - that only happens later, at the /people/match step,
-- which lands in ext_apollo_candidate_pool (0023) once revealed, or directly in HubSpot if pushed immediately.

CREATE TABLE ext_sourcing_run (
  run_id                   INTEGER PRIMARY KEY,
  apollo_call_id           INTEGER NOT NULL REFERENCES ext_apollo_call(apollo_call_id),
  cluster                  TEXT    NOT NULL CHECK (cluster <> ''),           -- e.g. 'cluster2'
  vertical                 TEXT    NOT NULL CHECK (vertical <> ''),         -- e.g. 'Manufacturing'
  sub_icp                  TEXT    NOT NULL CHECK (sub_icp <> ''),          -- e.g. 'Discrete manufacturing with MES/ERP-integrated shop floors'
  headcount_band           TEXT    NOT NULL CHECK (headcount_band <> ''),   -- Apollo syntax, e.g. '201,500'
  endpoint                 TEXT    NOT NULL CHECK (endpoint LIKE '/%'),
  request_body_json        TEXT    NOT NULL CHECK (json_valid(request_body_json)),  -- the exact body sent - full input, replayable
  page                     INTEGER NOT NULL CHECK (page >= 1),
  per_page                 INTEGER NOT NULL CHECK (per_page >= 1),
  total_entries_reported   INTEGER,   -- Apollo's server-side total match count for this filter (not just this page)
  results_returned         INTEGER NOT NULL CHECK (results_returned >= 0),  -- actual people count in this page's response
  created_at                TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

CREATE INDEX idx_sourcing_run_tags ON ext_sourcing_run(cluster, vertical, sub_icp, headcount_band);
CREATE INDEX idx_sourcing_run_call ON ext_sourcing_run(apollo_call_id);

CREATE TABLE ext_sourcing_result (
  result_id              INTEGER PRIMARY KEY,
  run_id                 INTEGER NOT NULL REFERENCES ext_sourcing_run(run_id),
  apollo_person_id       TEXT    NOT NULL,
  apollo_org_id          TEXT,
  company                TEXT,
  title                   TEXT,
  last_name_obfuscated    TEXT,     -- Apollo's own redacted field - this endpoint never returns a real last name
  has_direct_phone         TEXT    CHECK (has_direct_phone IS NULL OR has_direct_phone IN ('Yes', 'Maybe: please request direct dial via people/bulk_match')),
  title_priority_rank       INTEGER,  -- index into our Founder>Co-Founder>CEO>CTO>COO order (0 = highest); NULL if title matched none of them
  is_priority_pick           INTEGER NOT NULL DEFAULT 0 CHECK (is_priority_pick IN (0, 1)),  -- 1 = the single contact we keep per org after dedup
  raw_json                     TEXT    NOT NULL CHECK (json_valid(raw_json)),  -- the full person object Apollo returned, for later backfill
  created_at                     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

CREATE INDEX idx_sourcing_result_run ON ext_sourcing_result(run_id);
CREATE INDEX idx_sourcing_result_org ON ext_sourcing_result(apollo_org_id);
CREATE INDEX idx_sourcing_result_priority ON ext_sourcing_result(is_priority_pick);

-- One row per company per run, after dedup - the actual "who do we contact at this company" shortlist.
CREATE VIEW v_sourcing_priority_picks AS
SELECT r.run_id, r.cluster, r.vertical, r.sub_icp, r.headcount_band,
       res.result_id, res.apollo_person_id, res.apollo_org_id, res.company, res.title, res.has_direct_phone
  FROM ext_sourcing_result res
  JOIN ext_sourcing_run r ON r.run_id = res.run_id
 WHERE res.is_priority_pick = 1;
