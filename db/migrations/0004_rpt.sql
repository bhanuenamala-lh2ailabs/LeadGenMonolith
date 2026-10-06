-- 0004_rpt.sql
-- Report layer: persisted daily facts, report-run ledger, the import of the legacy snapshot JSON history,
-- and the VIEWS the unified daily report queries.
--
-- Principle (docs/discovery/funnel-reports.md 4.1): one event store (deal_stage_event), many views.  The
-- rpt_* FACT tables are a materialisation that can be dropped and rebuilt for any day from the event store;
-- they exist so trend / Roll7 / Prev7 read from the database.  The legacy snapshot JSONs are imported into
-- rpt_legacy_snapshot and used ONLY to cross-check the recomputed numbers (rpt_legacy_crosscheck).
-- Three distinct numbers per stage, always labelled:  Entries (events), Deals entered (distinct), Occupancy.

-- ---------------------------------------------------------------------------------------------
-- rpt_report_run: one row per report execution (daily mail, rebuild, ad-hoc).  Mail is DRY-RUN by default:
-- mail_mode only becomes 'sent' with a recipient and a timestamp (CHECK).  sync_freshness_json and
-- unmapped_stages_json feed the report footer.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE rpt_report_run (
  report_run_id        INTEGER PRIMARY KEY,  -- surrogate key
  report_kind          TEXT    NOT NULL CHECK (report_kind IN ('daily', 'weekly', 'rebuild', 'adhoc')),  -- daily / weekly / rebuild / adhoc
  report_day           TEXT    NOT NULL CHECK (length(report_day) = 10 AND date(report_day) IS report_day),   -- IST calendar day reported
  window_start         TEXT    CHECK (window_start IS NULL OR (length(window_start) = 10 AND date(window_start) IS window_start)),  -- first IST day of the window
  window_end           TEXT    CHECK (window_end   IS NULL OR (length(window_end)   = 10 AND date(window_end)   IS window_end)),  -- last IST day of the window
  status               TEXT    NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'succeeded', 'partial', 'failed')),
  started_at           TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (started_at IS strftime('%Y-%m-%dT%H:%M:%fZ', started_at) AND started_at NOT GLOB '*T24:*'),
  finished_at          TEXT    CHECK (finished_at IS NULL OR (finished_at IS strftime('%Y-%m-%dT%H:%M:%fZ', finished_at) AND finished_at NOT GLOB '*T24:*')),
  data_asof            TEXT    CHECK (data_asof IS NULL OR (data_asof IS strftime('%Y-%m-%dT%H:%M:%fZ', data_asof) AND data_asof NOT GLOB '*T24:*')),            -- newest event / sync used
  sync_freshness_json  TEXT    NOT NULL DEFAULT '{}' CHECK (json_valid(sync_freshness_json)),
  unmapped_stages_json TEXT    NOT NULL DEFAULT '[]' CHECK (json_valid(unmapped_stages_json) AND json_type(unmapped_stages_json) = 'array'),
  history_failures     INTEGER NOT NULL DEFAULT 0 CHECK (history_failures >= 0),  -- deals whose history could not be read (shown in the footer)
  output_dir           TEXT,  -- directory the report files were written to
  outputs_json         TEXT    NOT NULL DEFAULT '{}' CHECK (json_valid(outputs_json) AND json_type(outputs_json) = 'object'),
  mail_mode            TEXT    NOT NULL DEFAULT 'none' CHECK (mail_mode IN ('none', 'dry_run', 'sent', 'failed')),  -- none / dry_run / sent / failed (dry-run by default)
  mail_to              TEXT,  -- recipient of the mail
  mail_subject         TEXT,  -- subject of the mail
  mail_sent_at         TEXT    CHECK (mail_sent_at IS NULL OR (mail_sent_at IS strftime('%Y-%m-%dT%H:%M:%fZ', mail_sent_at) AND mail_sent_at NOT GLOB '*T24:*')),
  metrics_version      TEXT,  -- version of the metric dictionary
  stage_map_sha256     TEXT    CHECK (stage_map_sha256 IS NULL OR (length(stage_map_sha256) = 64 AND stage_map_sha256 NOT GLOB '*[^0-9a-f]*')),   -- hash of stage_map + stage_alias used; the report refuses trend lines that span a mapping change (rpt_funnel_daily.canonical_code is denormalised)
  params_json          TEXT    NOT NULL DEFAULT '{}' CHECK (json_valid(params_json)),
  tool_version         TEXT,  -- version of the tool
  error                TEXT,  -- error text of a failed run
  import_run_id        INTEGER REFERENCES ops_import_run(import_run_id) ON DELETE SET NULL,
  CHECK (mail_mode <> 'sent' OR (mail_to IS NOT NULL AND mail_sent_at IS NOT NULL)),
  CHECK (finished_at IS NULL OR finished_at >= started_at),
  CHECK (window_start IS NULL OR window_end IS NULL OR window_start <= window_end)
) STRICT;
CREATE INDEX ix_report_run_day ON rpt_report_run(report_day, report_kind, started_at);

-- ---------------------------------------------------------------------------------------------
-- rpt_funnel_daily: daily funnel facts at NATIVE stage grain (canonical code denormalised), one row per
-- (IST day, account, pipeline, stage, cohort scope).  Entries are de-duplicated on (deal, stage, actor, IST day)
-- exactly as the dashboards did; Human = CRM_UI, Automated = everything else; Total = both.
--   cohort_scope 'all'      : all history
--   cohort_scope 'in_scope' : the pipeline's report cohort (pipeline.cohort_start / cohort_exclude_migration_on)
-- occupancy_eod = deals sitting in the stage at the end of that IST day (NULL if not computed).
-- ---------------------------------------------------------------------------------------------
CREATE TABLE rpt_funnel_daily (
  ist_day              TEXT    NOT NULL CHECK (length(ist_day) = 10 AND date(ist_day) IS ist_day),
  account_id           TEXT    NOT NULL,
  pipeline_id          TEXT    NOT NULL,  -- pipeline of the stage
  stage_id             TEXT    NOT NULL,  -- NATIVE stage (grouped under the live successor when a tombstone has a stage_id alias)
  cohort_scope         TEXT    NOT NULL DEFAULT 'all' CHECK (cohort_scope IN ('all', 'in_scope')),  -- all history or the pipeline's in_scope cohort
  canonical_code       TEXT    REFERENCES canonical_stage(canonical_code),  -- canonical rung (denormalised: rebuild after a mapping change)
  dead_reason_code     TEXT    REFERENCES canonical_dead_reason(dead_reason_code),  -- dead reason for DEAD rows
  entries_human        INTEGER NOT NULL DEFAULT 0 CHECK (entries_human >= 0),  -- de-duplicated (deal, stage, actor, IST day) entries by humans (CRM_UI)
  entries_auto         INTEGER NOT NULL DEFAULT 0 CHECK (entries_auto  >= 0),  -- the same for automation / integrations
  entries_total        INTEGER GENERATED ALWAYS AS (entries_human + entries_auto) STORED,  -- STORED generated: human + auto
  deals_entered_human  INTEGER NOT NULL DEFAULT 0 CHECK (deals_entered_human >= 0),  -- distinct deals entered by humans
  deals_entered_all    INTEGER NOT NULL DEFAULT 0 CHECK (deals_entered_all   >= 0),  -- distinct deals entered by anyone
  occupancy_eod        INTEGER CHECK (occupancy_eod IS NULL OR occupancy_eod >= 0),  -- deals sitting in the stage at the end of the IST day (NULL = not computed)
  report_run_id        INTEGER REFERENCES rpt_report_run(report_run_id) ON DELETE SET NULL,  -- run that wrote the row
  computed_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (computed_at IS strftime('%Y-%m-%dT%H:%M:%fZ', computed_at) AND computed_at NOT GLOB '*T24:*'),
  PRIMARY KEY (ist_day, account_id, pipeline_id, stage_id, cohort_scope),
  FOREIGN KEY (account_id, pipeline_id, stage_id) REFERENCES stage(account_id, pipeline_id, stage_id),
  CHECK (deals_entered_human <= entries_human),
  CHECK (deals_entered_all <= entries_human + entries_auto),
  CHECK (deals_entered_human <= deals_entered_all)
) STRICT, WITHOUT ROWID;
CREATE INDEX ix_rpt_funnel_daily_canon ON rpt_funnel_daily(account_id, pipeline_id, canonical_code, ist_day);
CREATE INDEX ix_rpt_funnel_daily_run   ON rpt_funnel_daily(report_run_id);
CREATE INDEX ix_rpt_funnel_daily_dead  ON rpt_funnel_daily(dead_reason_code) WHERE dead_reason_code IS NOT NULL;

-- ---------------------------------------------------------------------------------------------
-- rpt_owner_daily: owner-level (per person) facts.  Activity is credited to the ACTOR (owner resolved from
-- updatedByUserId); metric_code is a key of the single metric dictionary (config/report.yaml), e.g.
-- 'stage_entry' (with canonical_code), 'engaged', 'net_new', 'note_only', 'assigned'.  owner_id NULL = unassigned.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE rpt_owner_daily (
  owner_daily_id   INTEGER PRIMARY KEY,  -- surrogate key
  ist_day          TEXT    NOT NULL CHECK (length(ist_day) = 10 AND date(ist_day) IS ist_day),
  account_id       TEXT    NOT NULL,
  pipeline_id      TEXT    NOT NULL,  -- pipeline
  owner_id         INTEGER,  -- owner (the ACTOR for activity); NULL = unassigned
  metric_code      TEXT    NOT NULL CHECK (metric_code <> '' AND metric_code NOT GLOB '*[^a-z0-9_]*'),  -- key of the metric dictionary (stage_entry, engaged, net_new, note_only, assigned)
  canonical_code   TEXT    REFERENCES canonical_stage(canonical_code),  -- canonical rung for stage_entry metrics
  events_human     INTEGER NOT NULL DEFAULT 0 CHECK (events_human >= 0),  -- human events
  events_auto      INTEGER NOT NULL DEFAULT 0 CHECK (events_auto  >= 0),  -- automated events
  deals_distinct   INTEGER NOT NULL DEFAULT 0 CHECK (deals_distinct   >= 0),  -- distinct deals
  deals_via_stage  INTEGER NOT NULL DEFAULT 0 CHECK (deals_via_stage  >= 0),  -- distinct deals touched by a stage move
  deals_note_only  INTEGER NOT NULL DEFAULT 0 CHECK (deals_note_only  >= 0),  -- distinct deals touched only by a classified note
  report_run_id    INTEGER REFERENCES rpt_report_run(report_run_id) ON DELETE SET NULL,  -- run that wrote the row
  computed_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (computed_at IS strftime('%Y-%m-%dT%H:%M:%fZ', computed_at) AND computed_at NOT GLOB '*T24:*'),
  FOREIGN KEY (account_id, pipeline_id) REFERENCES pipeline(account_id, pipeline_id),
  FOREIGN KEY (owner_id, account_id)    REFERENCES owner(owner_id, account_id)
) STRICT;
CREATE UNIQUE INDEX ux_rpt_owner_daily ON rpt_owner_daily(ist_day, account_id, pipeline_id, COALESCE(owner_id, 0), metric_code, COALESCE(canonical_code, ''));
CREATE INDEX ix_rpt_owner_daily_owner ON rpt_owner_daily(owner_id, ist_day) WHERE owner_id IS NOT NULL;
CREATE INDEX ix_rpt_owner_daily_canon ON rpt_owner_daily(canonical_code) WHERE canonical_code IS NOT NULL;

-- ---------------------------------------------------------------------------------------------
-- rpt_engaged_daily: who engaged which deal on which IST day (M16/M17).  Needed for the 7-day "leads engaged" SET
-- UNION (a count of daily counts would double-count a deal touched on two days).  via: 'stage' = human stage move,
-- 'note' = classified human note only, 'both'.  is_net_new = earliest-ever human event of the deal falls on this
-- day and the deal is not in the reject set.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE rpt_engaged_daily (
  ist_day        TEXT    NOT NULL CHECK (length(ist_day) = 10 AND date(ist_day) IS ist_day),
  owner_id       INTEGER NOT NULL,  -- owner credited with the engagement
  deal_id        INTEGER NOT NULL,  -- deal engaged
  account_id     TEXT    NOT NULL,
  pipeline_id    TEXT    NOT NULL,  -- pipeline of the deal that day
  via            TEXT    NOT NULL CHECK (via IN ('stage', 'note', 'both')),  -- stage (human stage move) / note (classified human note only) / both
  is_net_new     INTEGER NOT NULL DEFAULT 0 CHECK (is_net_new IN (0, 1)),
  report_run_id  INTEGER REFERENCES rpt_report_run(report_run_id) ON DELETE SET NULL,  -- run that wrote the row
  PRIMARY KEY (ist_day, owner_id, deal_id),
  FOREIGN KEY (owner_id, account_id) REFERENCES owner(owner_id, account_id) ON DELETE RESTRICT,
  FOREIGN KEY (deal_id, account_id)  REFERENCES deal(deal_id, account_id)   ON DELETE RESTRICT,
  FOREIGN KEY (account_id, pipeline_id) REFERENCES pipeline(account_id, pipeline_id)
) STRICT, WITHOUT ROWID;
CREATE INDEX ix_rpt_engaged_deal ON rpt_engaged_daily(deal_id, ist_day);

-- ---------------------------------------------------------------------------------------------
-- rpt_legacy_snapshot: the legacy daily snapshot JSON files, verbatim (payload_json) plus their parsed top-level
-- keys.  6 families (docs/discovery/data-inventory.md 3.5).  pipeline_id is NULL for the whole-portal MAIN series
-- ('main_full') and per-owner series use owner_hs_id ('unassigned' allowed).  is_bootstrap / is_seeded record the
-- legacy approximation flags - the legacy `cumulative` chain is NOT trusted and is never used for headline numbers.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE rpt_legacy_snapshot (
  snapshot_id              INTEGER PRIMARY KEY,  -- surrogate key
  source_family            TEXT    NOT NULL  -- which of the legacy snapshot series (companyops_cluster, rat, main_full, ...)
                                   CHECK (source_family IN ('companyops_cluster','rat','main_full','main_coding','main_coopsglobal','main_owner','main_hubspot_mirror')),
  account_id               TEXT    NOT NULL REFERENCES account(account_id),
  pipeline_id              TEXT,  -- pipeline of the series (NULL for the whole-portal MAIN series)
  owner_hs_id              TEXT,  -- HubSpot owner id of a per-owner series ('unassigned' allowed)
  snapshot_day             TEXT    NOT NULL CHECK (length(snapshot_day) = 10 AND date(snapshot_day) IS snapshot_day),  -- day of the snapshot
  source_path              TEXT    NOT NULL CHECK (source_path <> ''),  -- file the snapshot was read from
  source_sha256            TEXT    CHECK (source_sha256 IS NULL OR length(source_sha256) = 64),
  is_bootstrap             INTEGER NOT NULL DEFAULT 0 CHECK (is_bootstrap IN (0, 1)),
  is_seeded                INTEGER NOT NULL DEFAULT 0 CHECK (is_seeded IN (0, 1)),
  flow_json                TEXT    CHECK (flow_json                IS NULL OR (json_valid(flow_json)                AND json_type(flow_json) = 'object')),
  current_state_json       TEXT    CHECK (current_state_json       IS NULL OR (json_valid(current_state_json)       AND json_type(current_state_json) = 'object')),
  dashboard_flow_json      TEXT    CHECK (dashboard_flow_json      IS NULL OR (json_valid(dashboard_flow_json)      AND json_type(dashboard_flow_json) = 'object')),
  cumulative_json          TEXT    CHECK (cumulative_json          IS NULL OR (json_valid(cumulative_json)          AND json_type(cumulative_json) = 'object')),
  engaged_deal_ids_json    TEXT    CHECK (engaged_deal_ids_json    IS NULL OR (json_valid(engaged_deal_ids_json)    AND json_type(engaged_deal_ids_json) = 'array')),
  engaged_count            INTEGER GENERATED ALWAYS AS (json_array_length(engaged_deal_ids_json)) STORED,  -- STORED generated: length of engaged_deal_ids_json
  payload_json             TEXT    NOT NULL CHECK (json_valid(payload_json) AND json_type(payload_json) = 'object'),
  import_run_id            INTEGER REFERENCES ops_import_run(import_run_id) ON DELETE SET NULL,
  created_at               TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at) AND created_at NOT GLOB '*T24:*'),
  FOREIGN KEY (account_id, pipeline_id) REFERENCES pipeline(account_id, pipeline_id),
  CHECK (source_family <> 'main_owner' OR owner_hs_id IS NOT NULL)
) STRICT;
CREATE UNIQUE INDEX ux_rpt_legacy_snapshot ON rpt_legacy_snapshot(source_family, account_id, COALESCE(pipeline_id, ''), COALESCE(owner_hs_id, ''), snapshot_day);
CREATE INDEX ix_rpt_legacy_snapshot_day ON rpt_legacy_snapshot(account_id, pipeline_id, snapshot_day);

-- ---------------------------------------------------------------------------------------------
-- rpt_legacy_crosscheck: legacy-vs-recomputed comparison rows written by the cross-check job.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE rpt_legacy_crosscheck (
  crosscheck_id     INTEGER PRIMARY KEY,  -- surrogate key
  snapshot_id       INTEGER NOT NULL REFERENCES rpt_legacy_snapshot(snapshot_id) ON DELETE CASCADE,  -- legacy snapshot compared
  report_run_id     INTEGER REFERENCES rpt_report_run(report_run_id) ON DELETE SET NULL,  -- run that recomputed the number
  metric            TEXT    NOT NULL CHECK (metric IN ('dashboard_flow', 'flow', 'current_state', 'cumulative', 'engaged')),  -- dashboard_flow / flow / current_state / cumulative / engaged
  row_label         TEXT    NOT NULL,  -- row label inside the metric
  legacy_value      REAL,  -- number in the legacy snapshot
  recomputed_value  REAL,  -- number recomputed from deal_stage_event
  delta             REAL    GENERATED ALWAYS AS (recomputed_value - legacy_value) STORED,  -- STORED generated: recomputed - legacy
  explanation       TEXT,  -- why they differ
  checked_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (checked_at IS strftime('%Y-%m-%dT%H:%M:%fZ', checked_at) AND checked_at NOT GLOB '*T24:*')
) STRICT;
-- COALESCE: a NULL report_run_id (also produced by ON DELETE SET NULL) must not make the key non-unique
CREATE UNIQUE INDEX ux_rpt_legacy_crosscheck ON rpt_legacy_crosscheck(snapshot_id, COALESCE(report_run_id, 0), metric, row_label);

-- =============================================================================================
-- VIEWS the report queries.  All are read-only conveniences over the canonical layer.
-- =============================================================================================

-- v_stage_effective: every stage with its canonical mapping and EFFECTIVE flags (per-stage override, else the
-- dead-reason flags, else the canonical rung's).  is_mapped = 0 marks an unmapped stage.
-- ALIAS RESOLUTION: a tombstoned stage (is_deleted = 1) that has a stage_alias(kind 'stage_id') and NO stage_map row of its own
-- inherits the stage_map row of the alias target (its live successor), so deleted ids still in history (the old LinkedIn
-- sent / connected ids) land on the right rung instead of UNMAPPED.  A stage with its own stage_map row keeps it.
-- resolved_via_alias_stage_id = the successor when an alias exists; native_group_stage_id = the stage id native reports should group
-- the stage's events under (the successor for an alias, else the stage itself).
CREATE VIEW v_stage_effective AS
SELECT s.account_id, s.pipeline_id, s.stage_id,
       s.label                AS stage_label,
       s.display_order        AS stage_order,
       s.is_closed, s.is_deleted,
       sm.canonical_code,
       cs.label               AS canonical_label,
       cs.depth               AS canonical_depth,
       cs.sort_order          AS canonical_sort,
       cs.is_live, cs.is_dead, cs.is_won, cs.is_entry,
       sm.dead_reason_code,
       dr.label               AS dead_reason_label,
       sm.depth_reached,
       sm.sub_row,
       COALESCE(sm.flag_attempt,   dr.flag_attempt,   cs.flag_attempt)   AS flag_attempt,
       COALESCE(sm.flag_connected, dr.flag_connected, cs.flag_connected) AS flag_connected,
       COALESCE(sm.flag_interested, cs.flag_interested)                  AS flag_interested,
       COALESCE(dr.counts_in_effort_kpis, 1)                             AS counts_in_effort_kpis,
       CASE WHEN sm.stage_id IS NULL THEN 0 ELSE 1 END                   AS is_mapped,
       sa.stage_id                                                       AS resolved_via_alias_stage_id,
       COALESCE(sa.stage_id, s.stage_id)                                 AS native_group_stage_id
FROM stage s
LEFT JOIN stage_alias sa ON sa.alias_kind = 'stage_id' AND sa.account_id = s.account_id AND sa.pipeline_id = s.pipeline_id AND sa.alias_value = s.stage_id
LEFT JOIN stage_map sm ON sm.account_id = s.account_id AND sm.pipeline_id = s.pipeline_id
      AND sm.stage_id = CASE WHEN EXISTS (SELECT 1 FROM stage_map o WHERE o.account_id = s.account_id AND o.pipeline_id = s.pipeline_id AND o.stage_id = s.stage_id)
                             THEN s.stage_id ELSE sa.stage_id END
LEFT JOIN canonical_stage cs ON cs.canonical_code = sm.canonical_code
LEFT JOIN canonical_dead_reason dr ON dr.dead_reason_code = sm.dead_reason_code;

-- v_deal_current: one row per deal with pipeline / stage / canonical / owner / company / lead-source resolved.
-- Archived deals are INCLUDED (is_archived); occupancy views filter them out.
CREATE VIEW v_deal_current AS
SELECT d.deal_id, d.account_id, d.hs_deal_id, d.pipeline_id,
       p.funnel_slug, p.label AS pipeline_label, p.is_reporting_funnel,
       d.stage_id, e.stage_label, e.canonical_code, e.canonical_label, e.canonical_depth,
       e.is_live, e.is_dead, e.is_won, e.dead_reason_code, e.depth_reached, e.is_mapped,
       d.owner_id, o.display_name AS owner_name, o.email_norm AS owner_email,
       d.company_id, c.canonical_name AS company_name,
       d.dealname, ls.label AS lead_source, d.segment, d.vertical_id,
       d.cost_usd, d.amount, d.is_archived,
       d.hs_created_at, date(d.hs_created_at, '+330 minutes') AS created_ist_day,
       d.entered_stage_at, d.hs_updated_at
FROM deal d
JOIN pipeline p         ON p.account_id = d.account_id AND p.pipeline_id = d.pipeline_id
JOIN v_stage_effective e ON e.account_id = d.account_id AND e.pipeline_id = d.pipeline_id AND e.stage_id = d.stage_id
LEFT JOIN owner o       ON o.owner_id = d.owner_id AND o.account_id = d.account_id
LEFT JOIN company c     ON c.company_id = d.company_id
LEFT JOIN lead_source ls ON ls.lead_source_id = d.lead_source_id;

-- v_funnel_stage_counts: occupancy NOW per native stage of every reporting funnel (zero-count stages included,
-- archived deals excluded).  Matches the HubSpot board columns (M18).
CREATE VIEW v_funnel_stage_counts AS
SELECT p.account_id, p.pipeline_id, p.funnel_slug, p.label AS pipeline_label,
       e.stage_id, e.stage_label, e.stage_order, e.is_closed, e.is_deleted,
       e.canonical_code, e.canonical_sort, e.dead_reason_code, e.sub_row, e.is_mapped,
       COUNT(d.deal_id) AS deals
FROM pipeline p
JOIN v_stage_effective e ON e.account_id = p.account_id AND e.pipeline_id = p.pipeline_id
LEFT JOIN deal d ON d.account_id = e.account_id AND d.pipeline_id = e.pipeline_id AND d.stage_id = e.stage_id AND d.is_archived = 0
WHERE p.is_reporting_funnel = 1 AND (e.is_deleted = 0 OR d.deal_id IS NOT NULL)      -- a tombstone is listed only while a deal still sits in it
GROUP BY p.account_id, p.pipeline_id, e.stage_id;

-- v_funnel_canonical_counts: occupancy NOW rolled up to the canonical ladder (dead split by reason).  Stages
-- that are not mapped yet show up under canonical_code 'UNMAPPED' so they are never silently dropped.
CREATE VIEW v_funnel_canonical_counts AS
SELECT p.account_id, p.pipeline_id, p.funnel_slug,
       COALESCE(e.canonical_code, 'UNMAPPED') AS canonical_code,
       COALESCE(e.dead_reason_code, '')       AS dead_reason_code,
       MIN(COALESCE(e.canonical_sort, 9999))  AS canonical_sort,
       COUNT(d.deal_id)                       AS deals
FROM pipeline p
JOIN v_stage_effective e ON e.account_id = p.account_id AND e.pipeline_id = p.pipeline_id
LEFT JOIN deal d ON d.account_id = e.account_id AND d.pipeline_id = e.pipeline_id AND d.stage_id = e.stage_id AND d.is_archived = 0
WHERE p.is_reporting_funnel = 1 AND (e.is_deleted = 0 OR d.deal_id IS NOT NULL)
GROUP BY p.account_id, p.pipeline_id, COALESCE(e.canonical_code, 'UNMAPPED'), COALESCE(e.dead_reason_code, '');

-- v_stage_event_enriched: the event store joined to the canonical mapping.  The basis of every entries metric.
CREATE VIEW v_stage_event_enriched AS
SELECT ev.event_id, ev.deal_id, ev.account_id, ev.pipeline_id, p.funnel_slug,
       ev.to_stage_id AS stage_id, e.stage_label,
       e.canonical_code, e.canonical_sort, e.dead_reason_code, e.depth_reached,
       e.flag_attempt, e.flag_connected, e.flag_interested, e.counts_in_effort_kpis, e.is_entry, e.is_mapped,
       ev.from_pipeline_id, ev.from_stage_id,
       ev.entered_at, ev.ist_day, ev.source_type, ev.is_human, ev.actor_user_id, ev.actor_owner_id, ev.event_origin
FROM deal_stage_event ev
JOIN pipeline p          ON p.account_id = ev.account_id AND p.pipeline_id = ev.pipeline_id
JOIN v_stage_effective e ON e.account_id = ev.account_id AND e.pipeline_id = ev.pipeline_id AND e.stage_id = ev.to_stage_id;

-- v_deal_cohort: report cohort membership per (deal, pipeline).  A deal joins a cluster at the first moment it sat in
-- one of the pipeline's OWN stages (NOT createdate).  in_scope = joined on/after pipeline.cohort_start AND NOT
-- (joined on cohort_exclude_migration_on having been created before that day, i.e. the bulk migration).
CREATE VIEW v_deal_cohort AS
SELECT j.deal_id, j.account_id, j.pipeline_id,
       j.joined_at, date(j.joined_at, '+330 minutes') AS joined_ist_day,
       date(d.hs_created_at, '+330 minutes')          AS created_ist_day,
       CASE
         WHEN p.cohort_start IS NOT NULL AND date(j.joined_at, '+330 minutes') < p.cohort_start THEN 0
         WHEN p.cohort_exclude_migration_on IS NOT NULL
              AND date(j.joined_at, '+330 minutes') = p.cohort_exclude_migration_on
              AND (d.hs_created_at IS NULL OR date(d.hs_created_at, '+330 minutes') < p.cohort_exclude_migration_on) THEN 0   -- unknown creation date on the migration day cannot be proven NEW: out of scope
         ELSE 1
       END AS in_scope
FROM (SELECT deal_id, account_id, pipeline_id, MIN(entered_at) AS joined_at
        FROM deal_stage_event GROUP BY deal_id, account_id, pipeline_id) j
JOIN deal d     ON d.deal_id = j.deal_id AND d.account_id = j.account_id
JOIN pipeline p ON p.account_id = j.account_id AND p.pipeline_id = j.pipeline_id;

-- v_funnel_entries_daily: Entries (de-duplicated on deal + stage + actor + IST day) and distinct Deals entered,
-- Human vs Automated, per native stage and IST day.  This is exactly what rpt_funnel_daily materialises
-- (cohort_scope 'all').  Human = source_type CRM_UI.  A tombstoned stage with a stage_id alias is grouped under its live
-- successor (one native row per rung, deduplicated across the old and new id).
CREATE VIEW v_funnel_entries_daily AS
SELECT ist_day, account_id, pipeline_id, stage_id,
       entries_human, entries_auto, entries_human + entries_auto AS entries_total,
       deals_entered_human, deals_entered_all
FROM (
  SELECT ev.ist_day, ev.account_id, ev.pipeline_id, COALESCE(sa.stage_id, ev.to_stage_id) AS stage_id,
         COUNT(DISTINCT CASE WHEN ev.is_human = 1 THEN ev.deal_id || '|' || COALESCE(ev.actor_user_id, '') END) AS entries_human,
         COUNT(DISTINCT CASE WHEN ev.is_human = 0 THEN ev.deal_id || '|' || COALESCE(ev.actor_user_id, '') END) AS entries_auto,
         COUNT(DISTINCT CASE WHEN ev.is_human = 1 THEN ev.deal_id END) AS deals_entered_human,
         COUNT(DISTINCT ev.deal_id) AS deals_entered_all
  FROM deal_stage_event ev
  LEFT JOIN stage_alias sa ON sa.alias_kind = 'stage_id' AND sa.account_id = ev.account_id AND sa.pipeline_id = ev.pipeline_id AND sa.alias_value = ev.to_stage_id
  GROUP BY ev.ist_day, ev.account_id, ev.pipeline_id, COALESCE(sa.stage_id, ev.to_stage_id)
);

-- same, restricted to the pipeline's report cohort (cohort_scope 'in_scope')
CREATE VIEW v_funnel_entries_daily_in_scope AS
SELECT ist_day, account_id, pipeline_id, stage_id,
       entries_human, entries_auto, entries_human + entries_auto AS entries_total,
       deals_entered_human, deals_entered_all
FROM (
  SELECT ev.ist_day, ev.account_id, ev.pipeline_id, COALESCE(sa.stage_id, ev.to_stage_id) AS stage_id,
         COUNT(DISTINCT CASE WHEN ev.is_human = 1 THEN ev.deal_id || '|' || COALESCE(ev.actor_user_id, '') END) AS entries_human,
         COUNT(DISTINCT CASE WHEN ev.is_human = 0 THEN ev.deal_id || '|' || COALESCE(ev.actor_user_id, '') END) AS entries_auto,
         COUNT(DISTINCT CASE WHEN ev.is_human = 1 THEN ev.deal_id END) AS deals_entered_human,
         COUNT(DISTINCT ev.deal_id) AS deals_entered_all
  FROM deal_stage_event ev
  LEFT JOIN stage_alias sa ON sa.alias_kind = 'stage_id' AND sa.account_id = ev.account_id AND sa.pipeline_id = ev.pipeline_id AND sa.alias_value = ev.to_stage_id
  JOIN v_deal_cohort c ON c.deal_id = ev.deal_id AND c.pipeline_id = ev.pipeline_id AND c.in_scope = 1
  GROUP BY ev.ist_day, ev.account_id, ev.pipeline_id, COALESCE(sa.stage_id, ev.to_stage_id)
);

-- v_funnel_entries_daily_canonical: the combined-ladder view.  De-duplicates on (deal, CANONICAL stage, actor, IST day, human/auto)
-- first, so two native stages of one rung (1st interest sent + follow up) count once per deal and actor per day.
-- Written as ONE aggregate level over the base tables (COUNT(DISTINCT deal|actor[|human]) instead of SELECT DISTINCT in a subquery):
-- SQLite cannot push `WHERE ist_day = ?` through a DISTINCT-over-view subquery, which made every daily call a full scan of the
-- event store (320 ms at 200k events, linear in history); in this shape the day filter reaches ix_dse_ist_day (3 ms).
CREATE VIEW v_funnel_entries_daily_canonical AS
SELECT ev.ist_day, ev.account_id, ev.pipeline_id, p.funnel_slug,
       COALESCE(e.canonical_code, 'UNMAPPED')  AS canonical_code,
       COALESCE(e.dead_reason_code, '')        AS dead_reason_code,
       COUNT(DISTINCT CASE WHEN ev.is_human = 1 THEN ev.deal_id || '|' || COALESCE(ev.actor_user_id, '') END) AS entries_human,
       COUNT(DISTINCT CASE WHEN ev.is_human = 0 THEN ev.deal_id || '|' || COALESCE(ev.actor_user_id, '') END) AS entries_auto,
       COUNT(DISTINCT ev.deal_id || '|' || COALESCE(ev.actor_user_id, '') || '|' || ev.is_human)              AS entries_total,
       COUNT(DISTINCT CASE WHEN ev.is_human = 1 THEN ev.deal_id END) AS deals_entered_human,
       COUNT(DISTINCT ev.deal_id)                                    AS deals_entered_all
FROM deal_stage_event ev
JOIN pipeline p          ON p.account_id = ev.account_id AND p.pipeline_id = ev.pipeline_id
JOIN v_stage_effective e ON e.account_id = ev.account_id AND e.pipeline_id = ev.pipeline_id AND e.stage_id = ev.to_stage_id
GROUP BY ev.ist_day, ev.account_id, ev.pipeline_id, p.funnel_slug, COALESCE(e.canonical_code, 'UNMAPPED'), COALESCE(e.dead_reason_code, '');

-- v_deal_first_human_event: the earliest-ever human (CRM_UI) event per deal - the "net new engaged" anchor (M17).
CREATE VIEW v_deal_first_human_event AS
SELECT ev.deal_id, ev.account_id, MIN(ev.entered_at) AS first_human_at,
       date(MIN(ev.entered_at), '+330 minutes') AS first_human_ist_day
FROM deal_stage_event ev
WHERE ev.is_human = 1
GROUP BY ev.deal_id, ev.account_id;

-- v_deal_stage_dwell: every stage visit with the instant the deal left it (next event) and the dwell in hours.
-- exited_at NULL = still there (or last known).
CREATE VIEW v_deal_stage_dwell AS
SELECT event_id, deal_id, account_id, pipeline_id, to_stage_id AS stage_id, entered_at, ist_day, is_human,
       exited_at,
       CASE WHEN exited_at IS NULL THEN NULL
            ELSE ROUND((julianday(exited_at) - julianday(entered_at)) * 24.0, 3) END AS dwell_hours
FROM (
  SELECT ev.*, LEAD(ev.entered_at) OVER (PARTITION BY ev.deal_id ORDER BY ev.entered_at, ev.event_id) AS exited_at
  FROM deal_stage_event ev
);

-- v_unmapped_stage: stages of reporting funnels that resolve to NO stage_map row - neither their own nor, for a tombstone, via
-- stage_alias (see v_stage_effective) - with the deals / events riding on them.  Non-empty = the report footer must say so
-- (unmapped stage = loud failure).
CREATE VIEW v_unmapped_stage AS
SELECT s.account_id, s.pipeline_id, s.stage_id, s.label AS stage_label, s.is_deleted, s.label_source, s.first_seen_at,
       (SELECT COUNT(*) FROM deal d WHERE d.account_id = s.account_id AND d.pipeline_id = s.pipeline_id AND d.stage_id = s.stage_id) AS deals_now,
       (SELECT COUNT(*) FROM deal_stage_event ev WHERE ev.account_id = s.account_id AND ev.pipeline_id = s.pipeline_id AND ev.to_stage_id = s.stage_id) AS events
FROM v_stage_effective e
JOIN stage s    ON s.account_id = e.account_id AND s.pipeline_id = e.pipeline_id AND s.stage_id = e.stage_id
JOIN pipeline p ON p.account_id = s.account_id AND p.pipeline_id = s.pipeline_id
WHERE e.is_mapped = 0 AND p.is_reporting_funnel = 1;

-- v_contact_mobile_gate: the +91 mobile gate per contact.  Only contacts with has_indian_mobile = 1 are pushable leads.
-- (is_indian_mobile requires the importer to have classified the number phone_type = 'mobile' - see contact_phone.)
CREATE VIEW v_contact_mobile_gate AS
SELECT c.contact_id, c.account_id, c.hs_contact_id, c.company_id, c.email_norm, c.do_not_contact,
       EXISTS (SELECT 1 FROM contact_phone p WHERE p.contact_id = c.contact_id AND p.is_indian_mobile = 1 AND p.is_placeholder = 0) AS has_indian_mobile,
       (SELECT p.phone_e164 FROM contact_phone p
         WHERE p.contact_id = c.contact_id AND p.is_indian_mobile = 1 AND p.is_placeholder = 0
         ORDER BY p.is_primary DESC, p.phone_id LIMIT 1) AS best_mobile_e164,
       (SELECT COUNT(*) FROM contact_phone p WHERE p.contact_id = c.contact_id) AS phone_count
FROM contact c;

-- v_suppression_active: suppression entries in force right now.
CREATE VIEW v_suppression_active AS
SELECT suppression_id, kind, match_type, match_value_norm, company_id, account_id, reason, list_name, added_at, expires_at
FROM suppression
WHERE is_active = 1 AND (expires_at IS NULL OR expires_at > strftime('%Y-%m-%dT%H:%M:%fZ','now'));

-- v_company_survivor: follows merged_into_company_id chains to the surviving golden company (hops guarded at 20;
-- a cycle yields no row, which v_origin_coverage-style checks should flag).
CREATE VIEW v_company_survivor AS
WITH RECURSIVE chain(company_id, cur_id, hops) AS (
  SELECT company_id, company_id, 0 FROM company
  UNION ALL
  SELECT ch.company_id, c.merged_into_company_id, ch.hops + 1
    FROM chain ch JOIN company c ON c.company_id = ch.cur_id
   WHERE c.merged_into_company_id IS NOT NULL AND ch.hops < 20
)
SELECT ch.company_id, ch.cur_id AS survivor_company_id, ch.hops
FROM chain ch
JOIN company s ON s.company_id = ch.cur_id AND s.merged_into_company_id IS NULL;

-- v_company_golden: live golden companies with their primary strong identifiers and portal footprint.
CREATE VIEW v_company_golden AS
SELECT c.company_id, c.canonical_name, c.name_norm, c.hq_city, c.hq_country, c.india_hq, c.status,
       (SELECT i.value_norm FROM company_identifier i WHERE i.company_id = c.company_id AND i.is_strong = 1 AND i.identifier_type = 'root_domain'
         ORDER BY i.is_primary DESC, i.identifier_id LIMIT 1) AS root_domain,
       (SELECT i.value_norm FROM company_identifier i WHERE i.company_id = c.company_id AND i.is_strong = 1 AND i.identifier_type = 'linkedin_company'
         ORDER BY i.is_primary DESC, i.identifier_id LIMIT 1) AS linkedin_company,
       (SELECT i.value_norm FROM company_identifier i WHERE i.company_id = c.company_id AND i.is_strong = 1 AND i.identifier_type = 'cin'
         ORDER BY i.is_primary DESC, i.identifier_id LIMIT 1) AS cin,
       (SELECT GROUP_CONCAT(l.account_id) FROM (SELECT DISTINCT account_id FROM company_account_link x WHERE x.company_id = c.company_id ORDER BY account_id) l) AS accounts,
       (SELECT COUNT(*) FROM company_account_link x WHERE x.company_id = c.company_id) AS portal_links
FROM company c
WHERE c.merged_into_company_id IS NULL;

-- v_origin_coverage: provenance audit.  rows_without_origin should be 0 for every imported entity type.
CREATE VIEW v_origin_coverage AS
SELECT 'company' AS entity_type, COUNT(*) AS rows_total,
       SUM(CASE WHEN EXISTS (SELECT 1 FROM origin_ref r WHERE r.entity_type = 'company' AND r.entity_id = t.company_id) THEN 0 ELSE 1 END) AS rows_without_origin
  FROM company t
UNION ALL
SELECT 'company_account_link', COUNT(*),
       SUM(CASE WHEN EXISTS (SELECT 1 FROM origin_ref r WHERE r.entity_type = 'company_account_link' AND r.entity_id = t.link_id) THEN 0 ELSE 1 END)
  FROM company_account_link t
UNION ALL
SELECT 'contact', COUNT(*),
       SUM(CASE WHEN EXISTS (SELECT 1 FROM origin_ref r WHERE r.entity_type = 'contact' AND r.entity_id = t.contact_id) THEN 0 ELSE 1 END)
  FROM contact t
UNION ALL
SELECT 'deal', COUNT(*),
       SUM(CASE WHEN EXISTS (SELECT 1 FROM origin_ref r WHERE r.entity_type = 'deal' AND r.entity_id = t.deal_id) THEN 0 ELSE 1 END)
  FROM deal t
UNION ALL
SELECT 'tam_company_verdict', COUNT(*),
       SUM(CASE WHEN EXISTS (SELECT 1 FROM origin_ref r WHERE r.entity_type = 'tam_company_verdict' AND r.entity_id = t.verdict_id) THEN 0 ELSE 1 END)
  FROM tam_company_verdict t
UNION ALL
SELECT 'cost_ledger', COUNT(*),
       SUM(CASE WHEN EXISTS (SELECT 1 FROM origin_ref r WHERE r.entity_type = 'cost_ledger' AND r.entity_id = t.cost_id) THEN 0 ELSE 1 END)
  FROM cost_ledger t
UNION ALL
SELECT 'suppression', COUNT(*),
       SUM(CASE WHEN EXISTS (SELECT 1 FROM origin_ref r WHERE r.entity_type = 'suppression' AND r.entity_id = t.suppression_id) THEN 0 ELSE 1 END)
  FROM suppression t
UNION ALL
SELECT 'owner', COUNT(*),
       SUM(CASE WHEN EXISTS (SELECT 1 FROM origin_ref r WHERE r.entity_type = 'owner' AND r.entity_id = t.owner_id) THEN 0 ELSE 1 END)
  FROM owner t;

-- v_dq_open: open data-quality findings summarised for the report footer.
CREATE VIEW v_dq_open AS
SELECT rule_code, severity, COUNT(*) AS issues, SUM(occurrences) AS occurrences, MAX(last_seen_at) AS last_seen_at
FROM ops_dq_issue
WHERE status IN ('open', 'acknowledged')
GROUP BY rule_code, severity;

-- v_sync_freshness: age of the last successful sync per source / scope / object (report footer: data freshness).
CREATE VIEW v_sync_freshness AS
SELECT source_system, scope, object_type, cursor_name, account_id, last_status, last_success_at, last_attempt_at, last_error, rows_synced,
       CASE WHEN last_success_at IS NULL THEN NULL
            ELSE ROUND((julianday('now') - julianday(last_success_at)) * 24.0, 2) END AS hours_since_success
FROM ops_sync_state;

-- v_note: engagement rows that are notes (the note-classifier corpus).
CREATE VIEW v_note AS
SELECT engagement_id, account_id, hs_engagement_id, owner_id, occurred_at, ist_day, subject, body_text, note_bucket, note_bucket_version,
       is_human_written, is_archived
FROM engagement
WHERE kind = 'note';

-- v_rpt_funnel_daily_canonical: the persisted facts rolled up to the canonical ladder.  Entries are summed across the
-- native stages of a rung; distinct-deal counts cannot be summed, so use v_funnel_entries_daily_canonical for those.
CREATE VIEW v_rpt_funnel_daily_canonical AS
SELECT ist_day, account_id, pipeline_id, cohort_scope, canonical_code, COALESCE(dead_reason_code, '') AS dead_reason_code,
       SUM(entries_human) AS entries_human, SUM(entries_auto) AS entries_auto, SUM(entries_total) AS entries_total,
       SUM(occupancy_eod) AS occupancy_eod
FROM rpt_funnel_daily
GROUP BY ist_day, account_id, pipeline_id, cohort_scope, canonical_code, COALESCE(dead_reason_code, '');

-- v_legacy_snapshot_rows: the legacy snapshot JSON flattened to (snapshot, metric, row label, value) for cross-checks.
CREATE VIEW v_legacy_snapshot_rows AS
SELECT s.snapshot_id, s.source_family, s.account_id, s.pipeline_id, s.owner_hs_id, s.snapshot_day, s.is_bootstrap, s.is_seeded,
       'dashboard_flow' AS metric, j.key AS row_label, CAST(j.value AS REAL) AS value
  FROM rpt_legacy_snapshot s, json_each(s.dashboard_flow_json) j WHERE s.dashboard_flow_json IS NOT NULL
UNION ALL
SELECT s.snapshot_id, s.source_family, s.account_id, s.pipeline_id, s.owner_hs_id, s.snapshot_day, s.is_bootstrap, s.is_seeded,
       'flow', j.key, CAST(j.value AS REAL)
  FROM rpt_legacy_snapshot s, json_each(s.flow_json) j WHERE s.flow_json IS NOT NULL
UNION ALL
SELECT s.snapshot_id, s.source_family, s.account_id, s.pipeline_id, s.owner_hs_id, s.snapshot_day, s.is_bootstrap, s.is_seeded,
       'current_state', j.key, CAST(j.value AS REAL)
  FROM rpt_legacy_snapshot s, json_each(s.current_state_json) j WHERE s.current_state_json IS NOT NULL
UNION ALL
SELECT s.snapshot_id, s.source_family, s.account_id, s.pipeline_id, s.owner_hs_id, s.snapshot_day, s.is_bootstrap, s.is_seeded,
       'cumulative', j.key, CAST(j.value AS REAL)
  FROM rpt_legacy_snapshot s, json_each(s.cumulative_json) j WHERE s.cumulative_json IS NOT NULL;

-- =============================================================================================
-- Data-quality / review views (the doctor and the report footer read these)
-- =============================================================================================

-- v_company_mobile_gate: the +91 mobile gate for organisation-level numbers (company_phone), live golden companies only.
CREATE VIEW v_company_mobile_gate AS
SELECT c.company_id, c.canonical_name,
       EXISTS (SELECT 1 FROM company_phone p WHERE p.company_id = c.company_id AND p.is_indian_mobile = 1 AND p.is_placeholder = 0) AS has_indian_mobile,
       (SELECT p.phone_e164 FROM company_phone p
         WHERE p.company_id = c.company_id AND p.is_indian_mobile = 1 AND p.is_placeholder = 0
         ORDER BY p.is_primary DESC, p.phone_id LIMIT 1) AS best_mobile_e164,
       (SELECT COUNT(*) FROM company_phone p WHERE p.company_id = c.company_id) AS phone_count
FROM company c
WHERE c.merged_into_company_id IS NULL;

-- v_phone_gate_review: numbers the +91 gate rejected only because they could not be classified - worth a human look.
--   e164_mobile_shape_unclassified : valid +91 6-9xxxxxxxxx E.164 whose phone_type is not mobile/landline/... (still 'unknown')
--   raw_indian_shape_without_e164  : phone_raw looks Indian (+91 / 0091 / 10 digits) but no E.164 was derived
CREATE VIEW v_phone_gate_review AS
SELECT 'contact' AS owner_kind, contact_id AS owner_id, phone_id, phone_raw, phone_e164, phone_type, 'e164_mobile_shape_unclassified' AS reason
  FROM contact_phone
 WHERE is_placeholder = 0 AND phone_type = 'unknown' AND phone_e164 GLOB '+91[6-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]'
UNION ALL
SELECT 'contact', contact_id, phone_id, phone_raw, phone_e164, phone_type, 'raw_indian_shape_without_e164'
  FROM contact_phone
 WHERE is_placeholder = 0 AND phone_e164 IS NULL
   AND (replace(replace(phone_raw, ' ', ''), '-', '') GLOB '+91*' OR replace(replace(phone_raw, ' ', ''), '-', '') GLOB '0091*'
        OR replace(replace(phone_raw, ' ', ''), '-', '') GLOB '[6-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]')
UNION ALL
SELECT 'company', company_id, phone_id, phone_raw, phone_e164, phone_type, 'e164_mobile_shape_unclassified'
  FROM company_phone
 WHERE is_placeholder = 0 AND phone_type = 'unknown' AND phone_e164 GLOB '+91[6-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]'
UNION ALL
SELECT 'company', company_id, phone_id, phone_raw, phone_e164, phone_type, 'raw_indian_shape_without_e164'
  FROM company_phone
 WHERE is_placeholder = 0 AND phone_e164 IS NULL
   AND (replace(replace(phone_raw, ' ', ''), '-', '') GLOB '+91*' OR replace(replace(phone_raw, ' ', ''), '-', '') GLOB '0091*'
        OR replace(replace(phone_raw, ' ', ''), '-', '') GLOB '[6-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]');

-- v_identifier_survivor: where a company_identifier effectively points once merge chains are followed (identifiers stay on the
-- tombstone until the merge procedure moves them; lookups by identifier should go through this view).
CREATE VIEW v_identifier_survivor AS
SELECT i.identifier_id, i.identifier_type, i.value_norm, i.is_strong, i.company_id, sv.survivor_company_id,
       CASE WHEN sv.survivor_company_id = i.company_id THEN 0 ELSE 1 END AS on_tombstone
FROM company_identifier i
LEFT JOIN v_company_survivor sv ON sv.company_id = i.company_id;

-- v_pipeline_without_stages: a reporting pipeline with ZERO stage rows is not "unmapped" (nothing to map) yet every report on it is
-- empty - a fresh database before the first HubSpot sync, or a sync that failed.  doctor() warns on any row.
CREATE VIEW v_pipeline_without_stages AS
SELECT p.account_id, p.pipeline_id, p.funnel_slug, p.label
FROM pipeline p
WHERE p.is_reporting_funnel = 1
  AND NOT EXISTS (SELECT 1 FROM stage s WHERE s.account_id = p.account_id AND s.pipeline_id = p.pipeline_id);

-- v_deal_state_drift: deal.stage_id / pipeline_id (the CURRENT state, written by the deals sync) disagree with the newest event of
-- the event store.  Not an error by itself (sync lag) but a standing difference means events are missing.  Archived deals excluded.
CREATE VIEW v_deal_state_drift AS
SELECT d.deal_id, d.account_id, d.hs_deal_id, d.pipeline_id AS deal_pipeline_id, d.stage_id AS deal_stage_id,
       l.pipeline_id AS event_pipeline_id, l.to_stage_id AS last_event_stage_id, l.entered_at AS last_event_at
FROM deal d
JOIN (SELECT deal_id, pipeline_id, to_stage_id, entered_at,
             ROW_NUMBER() OVER (PARTITION BY deal_id ORDER BY entered_at DESC, event_id DESC) AS rn
        FROM deal_stage_event) l ON l.deal_id = d.deal_id AND l.rn = 1
WHERE d.is_archived = 0 AND (d.stage_id <> l.to_stage_id OR d.pipeline_id <> l.pipeline_id);

-- v_deal_company_drift: deal.company_id (golden) vs the golden company of the primary deal_company link.
CREATE VIEW v_deal_company_drift AS
SELECT d.deal_id, d.account_id, d.hs_deal_id, d.company_id AS deal_company_id, l.company_id AS link_company_id, l.link_id
FROM deal d
JOIN deal_company dc ON dc.deal_id = d.deal_id AND dc.is_primary = 1
JOIN company_account_link l ON l.link_id = dc.link_id
WHERE d.company_id IS NOT l.company_id;

-- v_dse_near_duplicate: several events of one deal entering the same stage within the same second - almost always the same move
-- imported twice (stage_transitions_csv at second precision + hubspot_history at millisecond precision).
CREATE VIEW v_dse_near_duplicate AS
SELECT deal_id, account_id, pipeline_id, to_stage_id, substr(entered_at, 1, 19) AS entered_second,
       COUNT(*) AS events, COUNT(DISTINCT event_origin) AS origins, GROUP_CONCAT(DISTINCT event_origin) AS event_origins, MIN(event_id) AS first_event_id
FROM deal_stage_event
GROUP BY deal_id, account_id, pipeline_id, to_stage_id, substr(entered_at, 1, 19)
HAVING COUNT(*) > 1;

-- v_event_pipeline_guess: events whose pipeline was NOT derived from hard evidence (pipeline_basis deal_current / manual) - the report
-- footer discloses these because a wrong guess mis-credits exactly the shared MAIN stage ids (Interested, Negotiation, Won).
CREATE VIEW v_event_pipeline_guess AS
SELECT account_id, pipeline_id, pipeline_basis, COUNT(*) AS events, COUNT(DISTINCT deal_id) AS deals
FROM deal_stage_event
WHERE pipeline_basis IN ('deal_current', 'manual')
GROUP BY account_id, pipeline_id, pipeline_basis;

-- v_deal_canonical_reach: per deal and canonical rung - when it first / last entered it and how many entries.  "Distinct deals that
-- ever reached >= rung X" is one join: JOIN canonical_stage c ON c.canonical_code = r.canonical_code WHERE c.depth >= X.
CREATE VIEW v_deal_canonical_reach AS
SELECT ev.deal_id, ev.account_id, e.canonical_code, e.canonical_depth, e.canonical_sort,
       MIN(ev.entered_at) AS first_at, MAX(ev.entered_at) AS last_at, COUNT(*) AS entries
FROM deal_stage_event ev
JOIN v_stage_effective e ON e.account_id = ev.account_id AND e.pipeline_id = ev.pipeline_id AND e.stage_id = ev.to_stage_id
WHERE e.canonical_code IS NOT NULL
GROUP BY ev.deal_id, ev.account_id, e.canonical_code;
