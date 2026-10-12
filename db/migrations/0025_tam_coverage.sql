-- 0025_tam_coverage.sql
-- TAM-exhaustion tracking per exact filter combination. Sourcing for a given sub-ICP/headcount-band happens
-- in small diversified bites across many days (not all 193 matches pulled in one sitting), so we need a
-- running answer to "how much of Apollo's reported TAM for this exact filter have we actually pulled so far,
-- cumulatively across every run that used it" - not just what one run's own page/per_page says.
--
-- filter_signature = sha256 of the request body with 'page' removed and keys sorted - the same filter
-- combination (same cluster/vertical/sub_icp/headcount_band/tech-uids/titles/etc, any page) always hashes the
-- same, so runs made today, next week, and next month against the identical slice all roll up together.

ALTER TABLE ext_sourcing_run ADD COLUMN filter_signature TEXT NOT NULL DEFAULT '';

CREATE INDEX idx_sourcing_run_signature ON ext_sourcing_run(filter_signature);

-- One row per exact filter combination ever run: Apollo's latest reported TAM size, how many pages we've
-- fetched against it, the highest page reached, how many distinct people we've actually pulled (deduped
-- across all runs/pages sharing this signature - a page can't repeat a person, but two separate runs could
-- overlap if Apollo's own ordering drifted), and the resulting % of TAM covered.
CREATE VIEW v_tam_coverage AS
SELECT run_stats.filter_signature,
       run_stats.cluster, run_stats.vertical, run_stats.sub_icp, run_stats.headcount_band,
       run_stats.total_entries_reported,
       run_stats.pages_fetched,
       run_stats.max_page_reached,
       run_stats.first_run_at,
       run_stats.last_run_at,
       COALESCE(pc.distinct_people, 0)                                             AS people_fetched_distinct,
       CASE WHEN run_stats.total_entries_reported > 0
            THEN ROUND(100.0 * COALESCE(pc.distinct_people, 0) / run_stats.total_entries_reported, 2)
            ELSE NULL END                                                           AS pct_covered,
       CASE WHEN run_stats.total_entries_reported > 0
            THEN run_stats.total_entries_reported - COALESCE(pc.distinct_people, 0)
            ELSE NULL END                                                           AS people_remaining_est
  FROM (
    SELECT filter_signature, cluster, vertical, sub_icp, headcount_band,
           MAX(total_entries_reported) AS total_entries_reported,  -- Apollo's TAM count can drift run to run; take the latest-known
           COUNT(*)                    AS pages_fetched,
           MAX(page)                   AS max_page_reached,
           MIN(created_at)             AS first_run_at,
           MAX(created_at)             AS last_run_at
      FROM ext_sourcing_run
     GROUP BY filter_signature
  ) run_stats
  LEFT JOIN (
    SELECT r.filter_signature, COUNT(DISTINCT res.apollo_person_id) AS distinct_people
      FROM ext_sourcing_result res
      JOIN ext_sourcing_run r ON r.run_id = res.run_id
     GROUP BY r.filter_signature
  ) pc ON pc.filter_signature = run_stats.filter_signature;
