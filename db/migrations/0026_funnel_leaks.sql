-- 0026_funnel_leaks.sql
-- Every point where a sourced candidate drops out of the funnel before being pushed, with WHY, so these can
-- be revisited later rather than silently disappearing. Three leak types identified so far (2026-10-10),
-- more will be added as later steps (reveal, assignment, push, HubSpot funnel stages) turn up their own:
--   - 'duplicate_person_same_company' : a second (or third...) person matched at an org we already have a
--      higher-priority-title contact for (Step 5 of the sourcing playbook). No credit cost (search is free)
--      but it's still a result row consumed against the search endpoint's page/rate-limit budget for nothing.
--   - 'low_phone_confidence'          : has_direct_phone came back "Maybe" rather than "Yes" - we skip
--      revealing these (per the has_direct_phone pre-filter), so no credit spent, but the row still cost a
--      slot in that page's rate-limited search budget.
--   - 'hubspot_dedup_block'           : leadgen.dedup.py found a live hit against an existing HubSpot deal
--      in any of the 3 portals. `detail` must capture exactly what matched and where (field, portal, deal id)
--      so this can be audited later - "blocked" alone is not enough.
-- cost_impact classifies what the leak actually wastes, per the user's own framing: credit spend is the
-- expensive resource for reveal-stage leaks; for pre-reveal (search-stage) leaks there's no credit cost, but
-- Apollo's search endpoint still has its own hourly/daily/weekly rate limit, and every wasted row eats into
-- that budget on a day we're trying to use it fully.

CREATE TABLE ext_funnel_leak (
  leak_id            INTEGER PRIMARY KEY,
  leak_type           TEXT    NOT NULL CHECK (leak_type IN
                         ('duplicate_person_same_company', 'low_phone_confidence', 'hubspot_dedup_block')),
  cost_impact           TEXT    NOT NULL CHECK (cost_impact IN ('credit_waste', 'rate_limit_waste', 'none')),
  run_id                 INTEGER REFERENCES ext_sourcing_run(run_id),
  result_id                INTEGER REFERENCES ext_sourcing_result(result_id),
  apollo_person_id           TEXT,
  apollo_org_id                 TEXT,
  company                        TEXT,
  detail                           TEXT    NOT NULL CHECK (detail <> ''),  -- the matching entry and where, for dedup leaks; the competing title/person, for duplicate-person leaks
  detected_at                        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

CREATE INDEX idx_funnel_leak_type ON ext_funnel_leak(leak_type, detected_at);
CREATE INDEX idx_funnel_leak_run ON ext_funnel_leak(run_id);
CREATE INDEX idx_funnel_leak_org ON ext_funnel_leak(apollo_org_id);

-- Quick daily/cumulative rollup - how many leaked, of what kind, costing what, per run/sub-ICP.
CREATE VIEW v_funnel_leak_summary AS
SELECT r.cluster, r.vertical, r.sub_icp, r.headcount_band,
       l.leak_type, l.cost_impact, COUNT(*) AS leak_count
  FROM ext_funnel_leak l
  LEFT JOIN ext_sourcing_run r ON r.run_id = l.run_id
 GROUP BY r.cluster, r.vertical, r.sub_icp, r.headcount_band, l.leak_type, l.cost_impact;
