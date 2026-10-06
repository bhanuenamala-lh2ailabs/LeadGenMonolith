-- 0002_canonical.sql
-- Canonical layer: the ONLY layer report / app code reads.  All tables STRICT.
--
-- Scoping rules baked into the keys
--   * account_id (main | companyops | rat) scopes every HubSpot key.  Pipeline ids and stage ids are only
--     unique inside a portal, and three stage ids are shared by the two MAIN pipelines, so stage is keyed
--     (account_id, pipeline_id, stage_id) and every table that points at a stage uses that whole key.
--   * Child rows that carry their own account_id are tied to their parent with a COMPOSITE foreign key
--     (parent_id, account_id) -> parent(parent_id, account_id), so a deal_stage_event can never reference a
--     deal / stage / owner of another portal.
--   * HubSpot ids are stored as TEXT (opaque; legacy stores use text, 'default' is a pipeline id) and
--     constrained to digits where they are always numeric.  Surrogate INTEGER keys are used for high-volume
--     entities (company, contact, deal, owner, ...).
--   * The same company may legitimately live in two portals.  `company` is the GOLDEN record;
--     `company_account_link` keeps one row per portal company.  Auto-link only on strong identifiers
--     (root_domain, linkedin company, CIN, google place id, hs ids); name-only matches go to merge_candidate.
--   * Provenance: every canonical row is reachable from origin_ref (source system + table + pk) - see the
--     v_origin_coverage view; deal_stage_event carries its own event_origin.
--   * Placeholders (itsvc 'pre-classification-prior', cost_ledger.usd_est = 0, ...) are never imported as facts:
--     is_placeholder = 1 marks them, and CHECKs keep a placeholder from masquerading as real data.
--   * Event / history tables (deal_stage_event, deal_owner_event) hang off `deal` with ON DELETE RESTRICT: a deal is
--     ARCHIVED (is_archived = 1), never deleted, and INSERT OR REPLACE on a parent is blocked instead of silently
--     erasing the history.  Typed child rows that are not history (identifiers, phones) are RESTRICTed too, so a
--     REPLACE of their parent cannot wipe them; pure association rows (deal_contact, deal_company, engagement_assoc)
--     still cascade.
--   * Merge procedure (companies): move company_account_link.company_id, company_identifier.company_id, contact.company_id,
--     deal.company_id, tam_company_verdict.company_id, suppression.company_id and cost_ledger.company_id from the loser to the
--     survivor (links BEFORE deals - trg_deal_company_consistency_* compares them), resolve UNIQUE clashes first, then set
--     company.merged_into_company_id / merged_at on the loser and write an ops_audit_log row.  v_identifier_survivor
--     resolves identifiers that still sit on a tombstone.  Cycles are rejected by trg_company_merge_no_cycle.

-- ---------------------------------------------------------------------------------------------
-- owner: a HubSpot owner, per account.  hs_owner_id (the "owner id" on a deal) and hs_user_id (the
-- "userId" that stage-history updatedByUserId carries) are different namespaces even though they are
-- numerically equal in today's three portals - never assume it.  Unified human identity is the email
-- (email_norm), NOT the owner id: the same person has different owner ids in different portals.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE owner (
  owner_id      INTEGER PRIMARY KEY,  -- surrogate key of the owner (per account)
  account_id    TEXT    NOT NULL REFERENCES account(account_id),
  hs_owner_id   TEXT    NOT NULL CHECK (hs_owner_id <> '' AND hs_owner_id NOT GLOB '*[^0-9]*' AND hs_owner_id NOT GLOB '0*'),  -- HubSpot owner id (the id on a deal)
  hs_user_id    TEXT    CHECK (hs_user_id IS NULL OR (hs_user_id <> '' AND hs_user_id NOT GLOB '*[^0-9]*' AND hs_user_id NOT GLOB '0*')),  -- HubSpot userId (what stage history updatedByUserId carries); a different namespace
  email_norm    TEXT    CHECK (email_norm IS NULL OR (email_norm = lower(trim(email_norm)) AND instr(email_norm, '@') > 1)),  -- lower-case email: the cross-portal identity of the person
  first_name    TEXT,  -- given name
  last_name     TEXT,  -- family name
  display_name  TEXT    NOT NULL CHECK (display_name <> ''),  -- name shown in reports
  role          TEXT,                                           -- free text: caller, pod lead, closer, ...
  is_archived   INTEGER NOT NULL DEFAULT 0 CHECK (is_archived IN (0, 1)),
  created_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at) AND created_at NOT GLOB '*T24:*'),
  updated_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (updated_at IS strftime('%Y-%m-%dT%H:%M:%fZ', updated_at) AND updated_at NOT GLOB '*T24:*'),
  UNIQUE (account_id, hs_owner_id),
  UNIQUE (account_id, hs_user_id),            -- NULLs are distinct, so a missing userId is fine
  UNIQUE (owner_id, account_id)               -- target of composite FKs
) STRICT;
CREATE INDEX ix_owner_email ON owner(email_norm);

-- ---------------------------------------------------------------------------------------------
-- pipeline: a deal pipeline of one portal.  Never look a pipeline up by label (the label
-- "Company Ops Data" no longer exists); labels are refreshed from the API and old ones are kept in
-- previous_labels_json.  funnel_slug is our stable code for the five reporting funnels.
-- cohort_start / cohort_exclude_migration_on are the per-pipeline report cohort config (Cluster 2:
-- bulk migration on 2026-09-15 is excluded from the "in scope" inception).
-- ---------------------------------------------------------------------------------------------
CREATE TABLE pipeline (
  account_id                    TEXT    NOT NULL REFERENCES account(account_id),
  pipeline_id                   TEXT    NOT NULL CHECK (pipeline_id <> '' AND pipeline_id = trim(pipeline_id)),   -- 'default' or numeric
  label                         TEXT    NOT NULL CHECK (label <> ''),
  funnel_slug                   TEXT    UNIQUE CHECK (funnel_slug IS NULL OR (funnel_slug <> '' AND funnel_slug NOT GLOB '*[^a-z0-9_]*')),  -- stable code of the five reporting funnels (NULL = not a reporting funnel)
  display_order                 INTEGER,
  is_reporting_funnel           INTEGER NOT NULL DEFAULT 1 CHECK (is_reporting_funnel IN (0, 1)),   -- 0 = ignored by every report (RAT stock pipeline)
  is_archived                   INTEGER NOT NULL DEFAULT 0 CHECK (is_archived IN (0, 1)),
  cohort_start                  TEXT    CHECK (cohort_start IS NULL OR (length(cohort_start) = 10 AND date(cohort_start) IS cohort_start)),  -- first IST day of the report cohort
  cohort_exclude_migration_on   TEXT    CHECK (cohort_exclude_migration_on IS NULL OR (length(cohort_exclude_migration_on) = 10 AND date(cohort_exclude_migration_on) IS cohort_exclude_migration_on)),  -- IST day of a bulk migration whose pre-existing deals are excluded from 'in scope'
  previous_labels_json          TEXT    NOT NULL DEFAULT '[]' CHECK (json_valid(previous_labels_json) AND json_type(previous_labels_json) = 'array'),
  hs_created_at                 TEXT    CHECK (hs_created_at IS NULL OR (hs_created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', hs_created_at) AND hs_created_at NOT GLOB '*T24:*')),
  labels_refreshed_at           TEXT    CHECK (labels_refreshed_at IS NULL OR (labels_refreshed_at IS strftime('%Y-%m-%dT%H:%M:%fZ', labels_refreshed_at) AND labels_refreshed_at NOT GLOB '*T24:*')),
  created_at                    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at) AND created_at NOT GLOB '*T24:*'),
  updated_at                    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (updated_at IS strftime('%Y-%m-%dT%H:%M:%fZ', updated_at) AND updated_at NOT GLOB '*T24:*'),
  PRIMARY KEY (account_id, pipeline_id)
) STRICT;

-- ---------------------------------------------------------------------------------------------
-- stage: a stage of a pipeline.  Keyed (account_id, pipeline_id, stage_id): stage ids are reused across
-- pipelines (Interested 4018854633, Commercial Negotiation 4018854637, Closed/Won 4018854642 exist in
-- both MAIN pipelines).  Deleted stages are TOMBSTONED (is_deleted = 1), not removed, because they still
-- appear in deal history (e.g. the deleted Cluster LinkedIn sent / LinkedIn connected ids ~3,000 deals
-- passed through).  A stage first seen in history rather than in the pipelines API is inserted with
-- label_source = 'history' and shows up in v_unmapped_stage until someone maps it.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE stage (
  account_id            TEXT    NOT NULL,
  pipeline_id           TEXT    NOT NULL,  -- pipeline the stage belongs to
  stage_id              TEXT    NOT NULL CHECK (stage_id <> '' AND stage_id = trim(stage_id)),  -- HubSpot stage id (unique only inside a pipeline)
  label                 TEXT    NOT NULL CHECK (label <> ''),
  display_order         INTEGER,
  is_closed             INTEGER NOT NULL DEFAULT 0 CHECK (is_closed IN (0, 1)),       -- HubSpot metadata.isClosed
  hs_probability        REAL    CHECK (hs_probability IS NULL OR (hs_probability >= 0 AND hs_probability <= 1)),  -- HubSpot win probability 0..1
  is_deleted            INTEGER NOT NULL DEFAULT 0 CHECK (is_deleted IN (0, 1)),      -- tombstone: gone from the API, kept for history
  label_source          TEXT    NOT NULL DEFAULT 'api' CHECK (label_source IN ('api','history','alias_seed','manual')),  -- api / history (first seen in deal history) / alias_seed / manual
  previous_labels_json  TEXT    NOT NULL DEFAULT '[]' CHECK (json_valid(previous_labels_json) AND json_type(previous_labels_json) = 'array'),
  first_seen_at         TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (first_seen_at IS strftime('%Y-%m-%dT%H:%M:%fZ', first_seen_at) AND first_seen_at NOT GLOB '*T24:*'),
  last_seen_in_api_at   TEXT    CHECK (last_seen_in_api_at IS NULL OR (last_seen_in_api_at IS strftime('%Y-%m-%dT%H:%M:%fZ', last_seen_in_api_at) AND last_seen_in_api_at NOT GLOB '*T24:*')),
  created_at            TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at) AND created_at NOT GLOB '*T24:*'),
  updated_at            TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (updated_at IS strftime('%Y-%m-%dT%H:%M:%fZ', updated_at) AND updated_at NOT GLOB '*T24:*'),
  PRIMARY KEY (account_id, pipeline_id, stage_id),
  FOREIGN KEY (account_id, pipeline_id) REFERENCES pipeline(account_id, pipeline_id)
) STRICT;
CREATE INDEX ix_stage_label ON stage(account_id, pipeline_id, label);

-- ---------------------------------------------------------------------------------------------
-- stage_alias: historical spellings / ids that resolve to a CURRENT stage of the same pipeline.
--   alias_kind = 'label'    : a label that was renamed (v2 PUT renames, "Call Attempted" -> "No Pickup", spelling
--                             variants "Dead/ColdCall/WrongFit" vs "Dead: Cold Call / Wrong Fit")
--   alias_kind = 'stage_id' : a deleted / replaced stage id found in history, pointing at its successor
-- Labels are matched case-insensitively with surrounding whitespace removed (alias_value is stored normalised).
-- DESIGN (one mechanism, not two): the TOMBSTONE `stage` row (is_deleted = 1) stays the identity - events keep the true
-- historical stage id.  The stage_id alias then says "this tombstone inherits the stage_map row and the native
-- grouping of its live successor": v_stage_effective resolves it, so v_stage_event_enriched / v_unmapped_stage /
-- the native entries views never show the old LinkedIn ids as UNMAPPED.  A tombstone with its OWN stage_map row
-- keeps that row (own mapping wins).  trg_stage_alias_ck_* enforce: target is live, alias value is a numeric
-- tombstone id of the same pipeline, never a live id (no shadowing), never the target itself.
-- valid_from / valid_to are INFORMATIONAL provenance (when the id was retired / replaced); no view consults them.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE stage_alias (
  alias_id      INTEGER PRIMARY KEY,  -- surrogate key
  account_id    TEXT    NOT NULL,
  pipeline_id   TEXT    NOT NULL,  -- pipeline of the stage
  alias_kind    TEXT    NOT NULL CHECK (alias_kind IN ('label', 'stage_id')),  -- label (a renamed label) or stage_id (a deleted tombstone id)
  alias_value   TEXT    NOT NULL CHECK (alias_value <> '' AND alias_value = trim(alias_value)  -- the old label (lower-case) or the tombstone stage id
                                        AND (alias_kind <> 'label' OR alias_value = lower(alias_value))),
  stage_id      TEXT    NOT NULL,                         -- the stage the alias resolves to
  reason        TEXT,
  valid_from    TEXT    CHECK (valid_from IS NULL OR (length(valid_from) = 10 AND date(valid_from) IS valid_from)),   -- informational
  valid_to      TEXT    CHECK (valid_to   IS NULL OR (length(valid_to)   = 10 AND date(valid_to)   IS valid_to)),     -- informational
  created_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at) AND created_at NOT GLOB '*T24:*'),
  UNIQUE (account_id, pipeline_id, alias_kind, alias_value),
  FOREIGN KEY (account_id, pipeline_id, stage_id) REFERENCES stage(account_id, pipeline_id, stage_id) ON DELETE RESTRICT,
  CHECK (valid_from IS NULL OR valid_to IS NULL OR valid_from <= valid_to)
) STRICT;
CREATE INDEX ix_stage_alias_target ON stage_alias(account_id, pipeline_id, stage_id);

CREATE TRIGGER trg_stage_alias_ck_ins BEFORE INSERT ON stage_alias
WHEN NEW.alias_kind = 'stage_id'
BEGIN
  SELECT RAISE(ABORT, 'stage_alias: the target stage must be live (is_deleted = 0)')
   WHERE (SELECT is_deleted FROM stage WHERE account_id = NEW.account_id AND pipeline_id = NEW.pipeline_id AND stage_id = NEW.stage_id) = 1;
  SELECT RAISE(ABORT, 'stage_alias: a stage_id alias value must be numeric and different from its target')
   WHERE NEW.alias_value GLOB '*[^0-9]*' OR NEW.alias_value = NEW.stage_id;
  SELECT RAISE(ABORT, 'stage_alias: a stage_id alias value must be an existing TOMBSTONE stage (is_deleted = 1) of the same pipeline, never a live id')
   WHERE NOT EXISTS (SELECT 1 FROM stage WHERE account_id = NEW.account_id AND pipeline_id = NEW.pipeline_id AND stage_id = NEW.alias_value AND is_deleted = 1);
END;
CREATE TRIGGER trg_stage_alias_ck_upd BEFORE UPDATE ON stage_alias
WHEN NEW.alias_kind = 'stage_id'
BEGIN
  SELECT RAISE(ABORT, 'stage_alias: the target stage must be live (is_deleted = 0)')
   WHERE (SELECT is_deleted FROM stage WHERE account_id = NEW.account_id AND pipeline_id = NEW.pipeline_id AND stage_id = NEW.stage_id) = 1;
  SELECT RAISE(ABORT, 'stage_alias: a stage_id alias value must be numeric and different from its target')
   WHERE NEW.alias_value GLOB '*[^0-9]*' OR NEW.alias_value = NEW.stage_id;
  SELECT RAISE(ABORT, 'stage_alias: a stage_id alias value must be an existing TOMBSTONE stage (is_deleted = 1) of the same pipeline, never a live id')
   WHERE NOT EXISTS (SELECT 1 FROM stage WHERE account_id = NEW.account_id AND pipeline_id = NEW.pipeline_id AND stage_id = NEW.alias_value AND is_deleted = 1);
END;

-- ---------------------------------------------------------------------------------------------
-- canonical_stage: the common ladder (config/canonical_stages.yaml mirrors this table).  Rungs 0..13 plus
-- DEAD.  depth is monotone ("how far did it get"); rank_label is the spec's name (0, 0b, 1, 1b, ...).
-- Derived rungs (MEETING_HELD, EVALUATED) have no native stage: they are computed from event history.
-- flag_* mean "ENTERING this rung counts in the attempt / connected / interested effort KPIs (M2/M3)"; they are nested
-- for a rung (interested => connected => attempted) but they are NOT a depth property: ASSET_REQUESTED and deeper carry 0/0/0
-- because entries there are not effort-KPI events.  "Distinct deals that ever attempted/connected/interested" must be computed as
-- "reached any rung with depth >= the first flagged rung" - v_deal_canonical_reach gives the per-deal reach in one join.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE canonical_stage (
  canonical_code   TEXT    PRIMARY KEY CHECK (canonical_code <> '' AND canonical_code NOT GLOB '*[^A-Z0-9_]*'),  -- stable UPPER_CASE rung code (mirrors config/canonical_stages.yaml)
  rank_label       TEXT    NOT NULL UNIQUE CHECK (rank_label <> ''),  -- the spec's rung name (0, 0b, 1, 1b, ... 13, X)
  depth            REAL    NOT NULL UNIQUE,  -- monotone 'how far did it get' (DEAD = 100)
  sort_order       INTEGER NOT NULL UNIQUE,
  label            TEXT    NOT NULL CHECK (label <> ''),
  definition       TEXT    NOT NULL CHECK (definition <> ''),  -- what counts as an entry into this rung
  is_live          INTEGER NOT NULL CHECK (is_live IN (0, 1)),
  is_dead          INTEGER NOT NULL CHECK (is_dead IN (0, 1)),
  is_won           INTEGER NOT NULL CHECK (is_won  IN (0, 1)),
  is_derived       INTEGER NOT NULL DEFAULT 0 CHECK (is_derived IN (0, 1)),
  is_entry         INTEGER NOT NULL DEFAULT 0 CHECK (is_entry   IN (0, 1)),   -- entries count from ANY source
  flag_attempt     INTEGER NOT NULL DEFAULT 0 CHECK (flag_attempt    IN (0, 1)),
  flag_connected   INTEGER NOT NULL DEFAULT 0 CHECK (flag_connected  IN (0, 1)),
  flag_interested  INTEGER NOT NULL DEFAULT 0 CHECK (flag_interested IN (0, 1)),
  CHECK (is_live + is_dead + is_won = 1),                                      -- exactly one of live / dead / won
  CHECK (is_derived = 0 OR is_live = 1),
  CHECK (flag_interested <= flag_connected AND flag_connected <= flag_attempt)
) STRICT;

-- ---------------------------------------------------------------------------------------------
-- canonical_dead_reason: canonical dead taxonomy.  Flags say whether dying for this reason still counts as an
-- attempt / a live connect in the effort KPIs (Wrong fit: never dialled -> no).
-- ---------------------------------------------------------------------------------------------
CREATE TABLE canonical_dead_reason (
  dead_reason_code       TEXT    PRIMARY KEY CHECK (dead_reason_code <> '' AND dead_reason_code NOT GLOB '*[^A-Z0-9_]*'),  -- stable UPPER_CASE code (mirrors config/canonical_stages.yaml)
  sort_order             INTEGER NOT NULL UNIQUE,
  label                  TEXT    NOT NULL CHECK (label <> ''),
  definition             TEXT    NOT NULL CHECK (definition <> ''),  -- when a dead native stage maps to this reason
  flag_attempt           INTEGER NOT NULL CHECK (flag_attempt   IN (0, 1)),
  flag_connected         INTEGER NOT NULL CHECK (flag_connected IN (0, 1)),
  counts_in_effort_kpis  INTEGER NOT NULL DEFAULT 1 CHECK (counts_in_effort_kpis IN (0, 1)),  -- 0 = excluded from every effort KPI and shown under Retired (RETIRED_ADMIN)
  CHECK (flag_connected <= flag_attempt)
) STRICT;

-- ---------------------------------------------------------------------------------------------
-- stage_map: native stage -> canonical stage (+ dead reason and depth reached for dead stages).  One row per
-- stage; a stage without a row is "unmapped" and is a LOUD failure (v_unmapped_stage, ops_dq_issue,
-- report footer).  flag_* are NULLABLE per-stage overrides of the canonical / dead-reason flags (CoOps
-- "Communicated / Wrong Fit" was already communicated, so it is an attempt).  sub_row keeps native detail
-- under a shared rung (Sample Extraction, Demand Fulfillment under NEGOTIATION).
-- Integrity that a CHECK cannot express (dead_reason_code iff canonical DEAD; derived rungs are never a
-- target) is enforced by the triggers below.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE stage_map (
  account_id        TEXT    NOT NULL,
  pipeline_id       TEXT    NOT NULL,  -- pipeline of the stage
  stage_id          TEXT    NOT NULL,  -- native stage being mapped
  canonical_code    TEXT    NOT NULL REFERENCES canonical_stage(canonical_code),  -- canonical rung the stage maps to
  dead_reason_code  TEXT    REFERENCES canonical_dead_reason(dead_reason_code),  -- dead reason (required iff the rung is DEAD)
  depth_reached     REAL    CHECK (depth_reached IS NULL OR (depth_reached >= 0 AND depth_reached < 100)),   -- dead stages only
  sub_row           TEXT,  -- native detail kept under a shared rung (Sample Extraction under NEGOTIATION)
  flag_attempt      INTEGER CHECK (flag_attempt   IS NULL OR flag_attempt   IN (0, 1)),
  flag_connected    INTEGER CHECK (flag_connected IS NULL OR flag_connected IN (0, 1)),
  flag_interested   INTEGER CHECK (flag_interested IS NULL OR flag_interested IN (0, 1)),
  map_source        TEXT    NOT NULL DEFAULT 'config' CHECK (map_source IN ('config', 'manual', 'auto')),  -- config (YAML) / manual / auto
  note              TEXT,
  created_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at) AND created_at NOT GLOB '*T24:*'),
  updated_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (updated_at IS strftime('%Y-%m-%dT%H:%M:%fZ', updated_at) AND updated_at NOT GLOB '*T24:*'),
  PRIMARY KEY (account_id, pipeline_id, stage_id),
  FOREIGN KEY (account_id, pipeline_id, stage_id) REFERENCES stage(account_id, pipeline_id, stage_id) ON DELETE RESTRICT,
  CHECK (dead_reason_code IS NOT NULL OR depth_reached IS NULL)
) STRICT;
CREATE INDEX ix_stage_map_canonical ON stage_map(canonical_code, dead_reason_code);
CREATE INDEX ix_stage_map_dead ON stage_map(dead_reason_code) WHERE dead_reason_code IS NOT NULL;

CREATE TRIGGER trg_stage_map_validate_ins BEFORE INSERT ON stage_map
BEGIN
  SELECT RAISE(ABORT, 'stage_map: a derived canonical stage cannot be a mapping target')
   WHERE (SELECT is_derived FROM canonical_stage WHERE canonical_code = NEW.canonical_code) = 1;
  SELECT RAISE(ABORT, 'stage_map: dead_reason_code is required for, and only allowed on, the DEAD canonical stage')
   WHERE (SELECT is_dead FROM canonical_stage WHERE canonical_code = NEW.canonical_code) <> (NEW.dead_reason_code IS NOT NULL);
  SELECT RAISE(ABORT, 'stage_map: a DEAD stage needs depth_reached (only RETIRED_ADMIN may omit it)')
   WHERE NEW.dead_reason_code IS NOT NULL AND NEW.dead_reason_code <> 'RETIRED_ADMIN' AND NEW.depth_reached IS NULL;
END;
CREATE TRIGGER trg_stage_map_validate_upd BEFORE UPDATE ON stage_map
BEGIN
  SELECT RAISE(ABORT, 'stage_map: a derived canonical stage cannot be a mapping target')
   WHERE (SELECT is_derived FROM canonical_stage WHERE canonical_code = NEW.canonical_code) = 1;
  SELECT RAISE(ABORT, 'stage_map: dead_reason_code is required for, and only allowed on, the DEAD canonical stage')
   WHERE (SELECT is_dead FROM canonical_stage WHERE canonical_code = NEW.canonical_code) <> (NEW.dead_reason_code IS NOT NULL);
  SELECT RAISE(ABORT, 'stage_map: a DEAD stage needs depth_reached (only RETIRED_ADMIN may omit it)')
   WHERE NEW.dead_reason_code IS NOT NULL AND NEW.dead_reason_code <> 'RETIRED_ADMIN' AND NEW.depth_reached IS NULL;
END;

-- ---------------------------------------------------------------------------------------------
-- lead_source: HubSpot lead_source option universe (43 options in MAIN alone, drifting).  code is the normalised
-- label (lowercase, non-alphanumerics collapsed to '_') so spelling variants collapse.
-- vertical: TAM category / vertical (tam's 13 India sector categories, corpus 'uk_proptech' / 'india_cobol_ip',
-- RAT's CAD / COBOL tracks).  Hierarchical via parent_vertical_id.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE lead_source (
  lead_source_id  INTEGER PRIMARY KEY,  -- surrogate key
  code            TEXT    NOT NULL UNIQUE CHECK (code <> '' AND code = lower(code) AND code NOT GLOB '*[^a-z0-9_]*'),  -- normalised label: lower-case, non-alphanumerics collapsed to '_'
  label           TEXT    NOT NULL CHECK (label <> ''),
  channel         TEXT,                                           -- apollo, sales_nav, scrape, outflo, tracxn, nasscom, manual, ...
  is_active       INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
  created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at) AND created_at NOT GLOB '*T24:*'),
  updated_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (updated_at IS strftime('%Y-%m-%dT%H:%M:%fZ', updated_at) AND updated_at NOT GLOB '*T24:*')
) STRICT;

CREATE TABLE vertical (
  vertical_id         INTEGER PRIMARY KEY,  -- surrogate key
  slug                TEXT    NOT NULL UNIQUE CHECK (slug <> '' AND slug = lower(slug) AND slug NOT GLOB '*[^a-z0-9_]*'),  -- stable lower-case code
  name                TEXT    NOT NULL CHECK (name <> ''),
  parent_vertical_id  INTEGER REFERENCES vertical(vertical_id),  -- parent in the vertical hierarchy
  region              TEXT,  -- region the vertical targets
  country_code        TEXT    CHECK (country_code IS NULL OR (length(country_code) = 2 AND country_code = upper(country_code))),  -- ISO country the vertical targets
  hubspot_segment     TEXT    CHECK (hubspot_segment IS NULL OR (hubspot_segment <> '' AND hubspot_segment = trim(hubspot_segment))),   -- the HubSpot `segment` value this vertical stands for (tam categories.hubspot_segment)
  description         TEXT,  -- what the vertical is
  is_active           INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
  created_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at) AND created_at NOT GLOB '*T24:*'),
  updated_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (updated_at IS strftime('%Y-%m-%dT%H:%M:%fZ', updated_at) AND updated_at NOT GLOB '*T24:*'),
  CHECK (parent_vertical_id IS NULL OR parent_vertical_id <> vertical_id)
) STRICT;
CREATE UNIQUE INDEX ux_vertical_hubspot_segment ON vertical(hubspot_segment) WHERE hubspot_segment IS NOT NULL;   -- deal.segment -> vertical_id is deterministic
CREATE INDEX ix_vertical_parent ON vertical(parent_vertical_id) WHERE parent_vertical_id IS NOT NULL;

-- ---------------------------------------------------------------------------------------------
-- company: the GOLDEN company record (cross-portal, cross-source).  Typed identifiers live in
-- company_identifier, per-portal HubSpot rows in company_account_link.  A merge leaves a tombstone:
-- merged_into_company_id points at the survivor (follow the chain; v_company_survivor does it).
-- ---------------------------------------------------------------------------------------------
CREATE TABLE company (
  company_id              INTEGER PRIMARY KEY AUTOINCREMENT,  -- surrogate key of the golden company (never reused)
  canonical_name          TEXT    NOT NULL CHECK (canonical_name <> ''),  -- display name chosen for the golden record
  name_norm               TEXT    NOT NULL CHECK (name_norm <> '' AND name_norm = lower(name_norm)),  -- lower-case comparison name (name-only matches go to merge_candidate, never auto-link)
  legal_name              TEXT,  -- registered legal name when known
  website                 TEXT,  -- raw website URL as seen
  hq_city                 TEXT,  -- headquarters city
  hq_state                TEXT,  -- headquarters state / region
  hq_country              TEXT    CHECK (hq_country IS NULL OR (length(hq_country) = 2 AND hq_country = upper(hq_country))),   -- ISO 3166-1 alpha-2
  india_hq                TEXT    NOT NULL DEFAULT 'unknown' CHECK (india_hq IN ('yes', 'no', 'unknown')),  -- is the HQ in India: yes / no / unknown (India-only sourcing rule)
  employee_count          INTEGER CHECK (employee_count IS NULL OR employee_count >= 0),  -- headcount when known
  headcount_band          TEXT,  -- source's headcount bucket text ('11-50')
  founded_year            INTEGER CHECK (founded_year IS NULL OR founded_year BETWEEN 1800 AND 2100),  -- year of incorporation / founding
  funding_stage           TEXT,  -- source's funding stage text
  total_funding_usd       REAL    CHECK (total_funding_usd IS NULL OR total_funding_usd >= 0),  -- total funding raised, USD
  status                  TEXT    NOT NULL DEFAULT 'unknown'
                                  CHECK (status IN ('active','dissolved','liquidation','struck_off','administration','acquired','unknown')),
  status_detail           TEXT,  -- the source's original status text when it is mapped to the status enum
  status_as_of            TEXT    CHECK (status_as_of IS NULL OR (length(status_as_of) = 10 AND date(status_as_of) IS status_as_of)),  -- date the status was observed
  merged_into_company_id  INTEGER REFERENCES company(company_id),  -- tombstone pointer to the survivor of a merge (follow the chain: v_company_survivor)
  merged_at               TEXT    CHECK (merged_at IS NULL OR (merged_at IS strftime('%Y-%m-%dT%H:%M:%fZ', merged_at) AND merged_at NOT GLOB '*T24:*')),
  is_placeholder          INTEGER NOT NULL DEFAULT 0 CHECK (is_placeholder IN (0, 1)),
  first_seen_at           TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (first_seen_at IS strftime('%Y-%m-%dT%H:%M:%fZ', first_seen_at) AND first_seen_at NOT GLOB '*T24:*'),
  created_at              TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at) AND created_at NOT GLOB '*T24:*'),
  updated_at              TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (updated_at IS strftime('%Y-%m-%dT%H:%M:%fZ', updated_at) AND updated_at NOT GLOB '*T24:*'),
  CHECK (merged_into_company_id IS NULL OR merged_into_company_id <> company_id),
  CHECK ((merged_into_company_id IS NULL) = (merged_at IS NULL))
) STRICT;
CREATE INDEX ix_company_name_norm ON company(name_norm);
CREATE INDEX ix_company_merged    ON company(merged_into_company_id) WHERE merged_into_company_id IS NOT NULL;
CREATE INDEX ix_company_country   ON company(hq_country, india_hq);

-- ---------------------------------------------------------------------------------------------
-- company_identifier: typed strong/weak keys of a golden company.  A STRONG identifier (is_strong = 1) is unique over
-- (identifier_type, value_norm): it belongs to exactly one golden company; a clash is a merge candidate, never a silent
-- overwrite.  WEAK rows (is_strong = 0) may repeat across companies - that is where a domain shared by several distinct
-- companies (agencies, hotel chains: hyatt.com claimed by 13 LinkedIn companies) is recorded as evidence without ever
-- auto-linking.  Weak rows are never primary and never used by v_company_golden.
-- Normalisation contract (enforced by CHECK): root_domain = lowercase registrable domain, no scheme / www. / path;
-- linkedin_company = 'company/<slug>' (or school/, showcase/), lowercase, no trailing slash; cin = 21-char upper;
-- hs_company = '<account_id>:<hs company id>'.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE company_identifier (
  identifier_id    INTEGER PRIMARY KEY,  -- surrogate key
  company_id       INTEGER NOT NULL REFERENCES company(company_id) ON DELETE RESTRICT,  -- owning golden company
  identifier_type  TEXT    NOT NULL  -- kind of key (root_domain, linkedin_company, cin, llpin, gstin, pan, google_place_id, hs_company ...)
                           CHECK (identifier_type IN ('root_domain','linkedin_company','cin','llpin','gstin','pan','google_place_id','hs_company',
                                                      'apollo_org','companies_house','duns','vat','tracxn','crunchbase','other')),
  value_norm       TEXT    NOT NULL CHECK (value_norm <> '' AND value_norm = trim(value_norm)),  -- normalised value (the CHECKs encode each type's contract); strong ones are globally unique
  value_raw        TEXT,  -- the value as the source spelled it
  confidence       REAL    NOT NULL DEFAULT 1.0 CHECK (confidence >= 0 AND confidence <= 1),
  is_strong        INTEGER NOT NULL DEFAULT 1 CHECK (is_strong IN (0, 1)),      -- strong keys may auto-link; weak ones may not
  is_primary       INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0, 1)),
  is_placeholder   INTEGER NOT NULL DEFAULT 0 CHECK (is_placeholder IN (0, 1)),
  source_system    TEXT,
  created_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at) AND created_at NOT GLOB '*T24:*'),
  updated_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (updated_at IS strftime('%Y-%m-%dT%H:%M:%fZ', updated_at) AND updated_at NOT GLOB '*T24:*'),
  UNIQUE (company_id, identifier_type, value_norm),
  CHECK (is_primary = 0 OR is_strong = 1),
  CHECK (identifier_type <> 'root_domain' OR (value_norm = lower(value_norm) AND instr(value_norm, '.') > 1
         AND value_norm NOT GLOB '*[^a-z0-9.-]*' AND value_norm NOT GLOB 'www.*' AND value_norm NOT GLOB '*.'
         AND value_norm NOT GLOB '.*' AND value_norm NOT GLOB '-*' AND value_norm NOT GLOB '*..*' AND value_norm NOT GLOB '*-.*' AND value_norm NOT GLOB '*.-*')),
  CHECK (identifier_type <> 'linkedin_company' OR (value_norm = lower(value_norm)
         AND (value_norm GLOB 'company/*' OR value_norm GLOB 'school/*' OR value_norm GLOB 'showcase/*')
         AND value_norm NOT GLOB '*/' AND value_norm NOT GLOB '* *' AND value_norm NOT GLOB '*[?#]*')),
  CHECK (identifier_type <> 'cin' OR (length(value_norm) = 21 AND value_norm NOT GLOB '*[^A-Z0-9]*')),
  CHECK (identifier_type <> 'llpin' OR value_norm GLOB '[A-Z][A-Z][A-Z]-[0-9][0-9][0-9][0-9]'),     -- LLP identification number, e.g. AAE-7433 (radar puts 16 of these in its cin column)
  CHECK (identifier_type <> 'hs_company' OR (value_norm GLOB '[a-z]*:[0-9]*' AND substr(value_norm, instr(value_norm, ':') + 1) NOT GLOB '*[^0-9]*'
         AND substr(value_norm, instr(value_norm, ':') + 1) NOT GLOB '0*'))
) STRICT;
CREATE INDEX ix_company_identifier_company ON company_identifier(company_id, identifier_type);
CREATE UNIQUE INDEX ux_company_identifier_strong ON company_identifier(identifier_type, value_norm) WHERE is_strong = 1;
CREATE INDEX ix_company_identifier_value ON company_identifier(identifier_type, value_norm);
CREATE UNIQUE INDEX ux_company_identifier_primary ON company_identifier(company_id, identifier_type) WHERE is_primary = 1;

-- ---------------------------------------------------------------------------------------------
-- company_account_link: golden company <-> the company object of one portal.  The PER-PORTAL row: the
-- same company may exist in two portals (37 domains already do) and is never auto-deduped across them.
-- link_method records which strong key justified the link.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE company_account_link (
  link_id         INTEGER PRIMARY KEY,  -- surrogate key of the per-portal company row
  company_id      INTEGER NOT NULL REFERENCES company(company_id),  -- the golden company this portal company belongs to
  account_id      TEXT    NOT NULL REFERENCES account(account_id),
  hs_company_id   TEXT    NOT NULL CHECK (hs_company_id <> '' AND hs_company_id NOT GLOB '*[^0-9]*' AND hs_company_id NOT GLOB '0*'),  -- HubSpot company id inside this portal (digits, no leading zero)
  hs_name         TEXT,  -- company name as HubSpot has it
  hs_domain       TEXT,                                       -- as HubSpot has it (raw)
  lh2_domain      TEXT    CHECK (lh2_domain IS NULL OR (lh2_domain <> '' AND lh2_domain = lower(trim(lh2_domain)))),   -- custom unique key property (HubSpot enforces uniqueness per portal)
  hs_created_at   TEXT    CHECK (hs_created_at IS NULL OR (hs_created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', hs_created_at) AND hs_created_at NOT GLOB '*T24:*')),
  hs_updated_at   TEXT    CHECK (hs_updated_at IS NULL OR (hs_updated_at IS strftime('%Y-%m-%dT%H:%M:%fZ', hs_updated_at) AND hs_updated_at NOT GLOB '*T24:*')),
  is_archived     INTEGER NOT NULL DEFAULT 0 CHECK (is_archived IN (0, 1)),
  link_method     TEXT    NOT NULL DEFAULT 'hs_id'  -- which strong key justified the link to the golden company
                          CHECK (link_method IN ('hs_id','root_domain','linkedin_company','cin','google_place_id','manual','new_golden')),
  confidence      REAL    NOT NULL DEFAULT 1.0 CHECK (confidence >= 0 AND confidence <= 1),
  created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at) AND created_at NOT GLOB '*T24:*'),
  updated_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (updated_at IS strftime('%Y-%m-%dT%H:%M:%fZ', updated_at) AND updated_at NOT GLOB '*T24:*'),
  UNIQUE (account_id, hs_company_id),
  UNIQUE (link_id, account_id)
) STRICT;
CREATE INDEX ix_company_link_company ON company_account_link(company_id);
CREATE UNIQUE INDEX ux_company_link_lh2_domain ON company_account_link(account_id, lh2_domain) WHERE lh2_domain IS NOT NULL;

-- ---------------------------------------------------------------------------------------------
-- contact: a person.  Portal contacts have (account_id, hs_contact_id); people that only exist in OutFlo /
-- pipeline.people / resolver data have both NULL until pushed.  The same human legitimately appears once
-- per portal.  Phones live in contact_phone.  Only `email_norm` / `linkedin_person_norm` are normalised
-- keys (lowercase; 'in/<slug>'); the raw spellings stay in email_raw / linkedin_raw.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE contact (
  contact_id            INTEGER PRIMARY KEY AUTOINCREMENT,  -- surrogate key of the person (never reused)
  account_id            TEXT    REFERENCES account(account_id),
  hs_contact_id         TEXT    CHECK (hs_contact_id IS NULL OR (hs_contact_id <> '' AND hs_contact_id NOT GLOB '*[^0-9]*' AND hs_contact_id NOT GLOB '0*')),  -- HubSpot contact id in this portal (NULL until pushed)
  company_id            INTEGER REFERENCES company(company_id),  -- golden company the person works for
  first_name            TEXT,  -- given name
  last_name             TEXT,  -- family name
  full_name             TEXT,  -- full name as the source had it
  job_title             TEXT,  -- job title as written
  seniority             TEXT,  -- seniority bucket
  contact_role          TEXT,  -- role in the buying process
  spoc_type             TEXT    CHECK (spoc_type IS NULL OR spoc_type IN ('Primary', 'Secondary')),  -- Primary / Secondary single point of contact
  email_raw             TEXT,  -- email as the source spelled it
  email_norm            TEXT    CHECK (email_norm IS NULL OR (email_norm = lower(trim(email_norm)) AND instr(email_norm, '@') > 1)),  -- lower-cased trimmed email (the lookup key)
  email_status          TEXT,  -- deliverability status from the source
  linkedin_raw          TEXT,  -- LinkedIn URL as the source had it
  linkedin_person_norm  TEXT    CHECK (linkedin_person_norm IS NULL OR (linkedin_person_norm = lower(linkedin_person_norm)  -- 'in/<slug>' lower-case (the lookup key)
                                       AND linkedin_person_norm GLOB 'in/*' AND linkedin_person_norm NOT GLOB '*/')),
  do_not_contact        INTEGER NOT NULL DEFAULT 0 CHECK (do_not_contact IN (0, 1)),  -- 1 = must never be contacted (mirrors HubSpot / suppression)
  attrs_json            TEXT    NOT NULL DEFAULT '{}' CHECK (json_valid(attrs_json) AND json_type(attrs_json) = 'object'),   -- source-specific extras with no column (founder DIN, resolver confidence, PECR fields, poc ...)
  hs_created_at         TEXT    CHECK (hs_created_at IS NULL OR (hs_created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', hs_created_at) AND hs_created_at NOT GLOB '*T24:*')),
  hs_updated_at         TEXT    CHECK (hs_updated_at IS NULL OR (hs_updated_at IS strftime('%Y-%m-%dT%H:%M:%fZ', hs_updated_at) AND hs_updated_at NOT GLOB '*T24:*')),
  is_archived           INTEGER NOT NULL DEFAULT 0 CHECK (is_archived IN (0, 1)),
  is_placeholder        INTEGER NOT NULL DEFAULT 0 CHECK (is_placeholder IN (0, 1)),
  source_system         TEXT,
  created_at            TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at) AND created_at NOT GLOB '*T24:*'),
  updated_at            TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (updated_at IS strftime('%Y-%m-%dT%H:%M:%fZ', updated_at) AND updated_at NOT GLOB '*T24:*'),
  UNIQUE (account_id, hs_contact_id),
  UNIQUE (contact_id, account_id),
  CHECK ((account_id IS NULL) = (hs_contact_id IS NULL))
) STRICT;
CREATE INDEX ix_contact_company  ON contact(company_id);
CREATE INDEX ix_contact_email    ON contact(email_norm)            WHERE email_norm IS NOT NULL;
CREATE INDEX ix_contact_linkedin ON contact(linkedin_person_norm)  WHERE linkedin_person_norm IS NOT NULL;

-- ---------------------------------------------------------------------------------------------
-- contact_phone: one row per number.  phone_e164 is validated E.164; is_indian_mobile is a STORED GENERATED
-- column = "+91 followed by a 10-digit mobile-range number (6-9...) AND the importer classified it phone_type = 'mobile'".
-- It is the +91 MOBILE GATE.  The digit rule alone cannot be exact (Bangalore 80 / Ahmedabad 79 landlines have the same
-- shape as mobiles), so the gate FAILS CLOSED: phone_type 'unknown' / 'landline' / 'voip' / 'tollfree' never pass; the importer
-- derives the type (libphonenumber) and lists every +91 mobile-shaped number it could not classify in
-- v_phone_gate_review.  country_iso2, when known, must agree with the +91 prefix.  Mobile-only: landline / email-only
-- contacts are not wanted leads (ARCHITECTURE section 3.4).  A number that cannot be parsed keeps phone_raw and a NULL e164.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE contact_phone (
  phone_id          INTEGER PRIMARY KEY,  -- surrogate key
  contact_id        INTEGER NOT NULL REFERENCES contact(contact_id) ON DELETE RESTRICT,  -- owning contact
  phone_raw         TEXT    NOT NULL CHECK (phone_raw <> ''),  -- number exactly as the source had it
  phone_e164        TEXT    CHECK (phone_e164 IS NULL OR (phone_e164 GLOB '+[1-9]*' AND phone_e164 NOT GLOB '+*[^0-9]*' AND length(phone_e164) BETWEEN 8 AND 16)),  -- validated E.164 (NULL when it could not be parsed)
  country_iso2      TEXT    CHECK (country_iso2 IS NULL OR (length(country_iso2) = 2 AND country_iso2 = upper(country_iso2))),  -- ISO country of the number when known; must agree with a +91 prefix
  phone_type        TEXT    NOT NULL DEFAULT 'unknown' CHECK (phone_type IN ('mobile','landline','voip','tollfree','unknown')),  -- mobile / landline / voip / tollfree / unknown, set by the importer (libphonenumber); only 'mobile' can open the +91 gate
  is_indian_mobile  INTEGER GENERATED ALWAYS AS (COALESCE(phone_e164 GLOB '+91[6-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]' AND phone_type = 'mobile', 0)) STORED,
  is_primary        INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0, 1)),
  is_placeholder    INTEGER NOT NULL DEFAULT 0 CHECK (is_placeholder IN (0, 1)),
  source_system     TEXT,
  verified_at       TEXT    CHECK (verified_at IS NULL OR (verified_at IS strftime('%Y-%m-%dT%H:%M:%fZ', verified_at) AND verified_at NOT GLOB '*T24:*')),
  created_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at) AND created_at NOT GLOB '*T24:*'),
  updated_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (updated_at IS strftime('%Y-%m-%dT%H:%M:%fZ', updated_at) AND updated_at NOT GLOB '*T24:*'),
  CHECK (phone_e164 IS NULL OR country_iso2 IS NULL OR ((phone_e164 GLOB '+91*') = (country_iso2 = 'IN')))
) STRICT;
CREATE UNIQUE INDEX ux_contact_phone_number  ON contact_phone(contact_id, COALESCE(phone_e164, phone_raw));
CREATE UNIQUE INDEX ux_contact_phone_primary ON contact_phone(contact_id) WHERE is_primary = 1;
CREATE INDEX ix_contact_phone_e164   ON contact_phone(phone_e164) WHERE phone_e164 IS NOT NULL;
CREATE INDEX ix_contact_phone_gate   ON contact_phone(contact_id) WHERE is_indian_mobile = 1;

-- ---------------------------------------------------------------------------------------------
-- company_phone: organisation-level numbers (itsvc candidates, Google Places nationalPhoneNumber, Tracxn, tam office_phone):
-- the bulk of real phone data belongs to a COMPANY, not a person.  Same E.164 / gate rules as contact_phone, so the +91
-- mobile gate is stored (never recomputed from legacy_itsvc_*).  Dummies such as '+91 12345 67890' or '0123456789' are
-- loaded with is_placeholder = 1 and never open the gate; non-E.164 spellings keep phone_raw with a NULL e164.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE company_phone (
  phone_id          INTEGER PRIMARY KEY,  -- surrogate key
  company_id        INTEGER NOT NULL REFERENCES company(company_id) ON DELETE RESTRICT,  -- owning golden company
  phone_raw         TEXT    NOT NULL CHECK (phone_raw <> ''),  -- number exactly as the source had it
  phone_e164        TEXT    CHECK (phone_e164 IS NULL OR (phone_e164 GLOB '+[1-9]*' AND phone_e164 NOT GLOB '+*[^0-9]*' AND length(phone_e164) BETWEEN 8 AND 16)),  -- validated E.164 (NULL when it could not be parsed)
  country_iso2      TEXT    CHECK (country_iso2 IS NULL OR (length(country_iso2) = 2 AND country_iso2 = upper(country_iso2))),  -- ISO country of the number when known; must agree with a +91 prefix
  phone_type        TEXT    NOT NULL DEFAULT 'unknown' CHECK (phone_type IN ('mobile','landline','voip','tollfree','unknown')),  -- mobile / landline / voip / tollfree / unknown, set by the importer (libphonenumber); only 'mobile' can open the +91 gate
  is_indian_mobile  INTEGER GENERATED ALWAYS AS (COALESCE(phone_e164 GLOB '+91[6-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]' AND phone_type = 'mobile', 0)) STORED,
  is_primary        INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0, 1)),
  is_placeholder    INTEGER NOT NULL DEFAULT 0 CHECK (is_placeholder IN (0, 1)),
  source_system     TEXT,
  verified_at       TEXT    CHECK (verified_at IS NULL OR (verified_at IS strftime('%Y-%m-%dT%H:%M:%fZ', verified_at) AND verified_at NOT GLOB '*T24:*')),
  created_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at) AND created_at NOT GLOB '*T24:*'),
  updated_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (updated_at IS strftime('%Y-%m-%dT%H:%M:%fZ', updated_at) AND updated_at NOT GLOB '*T24:*'),
  CHECK (phone_e164 IS NULL OR country_iso2 IS NULL OR ((phone_e164 GLOB '+91*') = (country_iso2 = 'IN')))
) STRICT;
CREATE UNIQUE INDEX ux_company_phone_number  ON company_phone(company_id, COALESCE(phone_e164, phone_raw));
CREATE UNIQUE INDEX ux_company_phone_primary ON company_phone(company_id) WHERE is_primary = 1;
CREATE INDEX ix_company_phone_e164 ON company_phone(phone_e164) WHERE phone_e164 IS NOT NULL;
CREATE INDEX ix_company_phone_gate ON company_phone(company_id) WHERE is_indian_mobile = 1 AND is_placeholder = 0;

-- ---------------------------------------------------------------------------------------------
-- deal: the CURRENT state of a HubSpot deal (history lives in deal_stage_event).  The deal, not the company,
-- is the dedup unit in HubSpot.  Archived deals are kept (is_archived = 1) - they still hold history.
-- (account_id, pipeline_id, stage_id) is a composite FK to stage, owner is (owner_id, account_id).
-- `amount` is stored but NEVER reported: the money field is `cost` (cost_usd); the unresolved cost-vs-amount
-- conflict is flagged in the report footer.  entered_stage_at mirrors hs_v2_date_entered_current_stage and
-- must NOT be used as a report filter (it holds only the latest move).  props_json keeps the curated
-- portal-specific custom properties (gmeet1_link, one_pager_results, discovery_call_link, ...).
-- ---------------------------------------------------------------------------------------------
CREATE TABLE deal (
  deal_id            INTEGER PRIMARY KEY AUTOINCREMENT,  -- surrogate key of the deal (never reused)
  account_id         TEXT    NOT NULL REFERENCES account(account_id),
  hs_deal_id         TEXT    NOT NULL CHECK (hs_deal_id <> '' AND hs_deal_id NOT GLOB '*[^0-9]*' AND hs_deal_id NOT GLOB '0*'),  -- HubSpot deal id in this portal (digits, no leading zero)
  pipeline_id        TEXT    NOT NULL,  -- CURRENT pipeline (with stage_id: composite FK to stage)
  stage_id           TEXT    NOT NULL,  -- CURRENT stage (history lives in deal_stage_event)
  owner_id           INTEGER,  -- current owner (same portal, composite FK)
  company_id         INTEGER REFERENCES company(company_id),     -- golden company; must agree with the PRIMARY deal_company link (trg_deal_company_consistency_*)
  dealname           TEXT,  -- deal name as HubSpot has it
  lead_source_id     INTEGER REFERENCES lead_source(lead_source_id),  -- HubSpot lead_source option, normalised
  vertical_id        INTEGER REFERENCES vertical(vertical_id),  -- TAM vertical / category
  segment            TEXT,                                    -- scraped_type / segment marker
  tam_source         TEXT,                                    -- pipeline_source / source_tab provenance property
  lh2_domain         TEXT,  -- custom unique-key property (company domain)
  linkedin_company_norm TEXT CHECK (linkedin_company_norm IS NULL OR linkedin_company_norm = lower(linkedin_company_norm)),  -- 'company/<slug>' lower-case
  cost_usd           REAL    CHECK (cost_usd IS NULL OR cost_usd >= 0),  -- the money field reported on (cost); amount is never reported
  amount             REAL    CHECK (amount IS NULL OR amount >= 0),  -- HubSpot amount, stored but NEVER reported (cost-vs-amount conflict, see report footer)
  currency           TEXT    NOT NULL DEFAULT 'USD' CHECK (length(currency) = 3 AND currency = upper(currency)),
  is_archived        INTEGER NOT NULL DEFAULT 0 CHECK (is_archived IN (0, 1)),
  archived_at        TEXT    CHECK (archived_at IS NULL OR (archived_at IS strftime('%Y-%m-%dT%H:%M:%fZ', archived_at) AND archived_at NOT GLOB '*T24:*')),
  hs_created_at      TEXT    CHECK (hs_created_at IS NULL OR (hs_created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', hs_created_at) AND hs_created_at NOT GLOB '*T24:*')),
  hs_updated_at      TEXT    CHECK (hs_updated_at IS NULL OR (hs_updated_at IS strftime('%Y-%m-%dT%H:%M:%fZ', hs_updated_at) AND hs_updated_at NOT GLOB '*T24:*')),
  hs_closed_at       TEXT    CHECK (hs_closed_at  IS NULL OR (hs_closed_at IS strftime('%Y-%m-%dT%H:%M:%fZ', hs_closed_at) AND hs_closed_at NOT GLOB '*T24:*')),
  entered_stage_at   TEXT    CHECK (entered_stage_at IS NULL OR (entered_stage_at IS strftime('%Y-%m-%dT%H:%M:%fZ', entered_stage_at) AND entered_stage_at NOT GLOB '*T24:*')),
  props_json         TEXT    NOT NULL DEFAULT '{}' CHECK (json_valid(props_json) AND json_type(props_json) = 'object'),
  is_placeholder     INTEGER NOT NULL DEFAULT 0 CHECK (is_placeholder IN (0, 1)),
  created_at         TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at) AND created_at NOT GLOB '*T24:*'),
  updated_at         TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (updated_at IS strftime('%Y-%m-%dT%H:%M:%fZ', updated_at) AND updated_at NOT GLOB '*T24:*'),
  UNIQUE (account_id, hs_deal_id),
  UNIQUE (deal_id, account_id),                               -- target of composite FKs
  FOREIGN KEY (account_id, pipeline_id, stage_id) REFERENCES stage(account_id, pipeline_id, stage_id),
  FOREIGN KEY (owner_id, account_id) REFERENCES owner(owner_id, account_id),
  CHECK (is_archived = 1 OR archived_at IS NULL)
) STRICT;
-- report: occupancy now = deals per (account, pipeline, stage)
CREATE INDEX ix_deal_stage      ON deal(account_id, pipeline_id, stage_id) WHERE is_archived = 0;
CREATE INDEX ix_deal_stage_all  ON deal(account_id, pipeline_id, stage_id);
CREATE INDEX ix_deal_owner      ON deal(owner_id, account_id) WHERE owner_id IS NOT NULL;
CREATE INDEX ix_deal_company    ON deal(company_id) WHERE company_id IS NOT NULL;
CREATE INDEX ix_deal_lead_src   ON deal(lead_source_id) WHERE lead_source_id IS NOT NULL;
CREATE INDEX ix_deal_created    ON deal(account_id, pipeline_id, hs_created_at);
CREATE INDEX ix_deal_updated    ON deal(account_id, hs_updated_at);                       -- incremental sync
CREATE INDEX ix_deal_domain     ON deal(account_id, lh2_domain) WHERE lh2_domain IS NOT NULL;
CREATE INDEX ix_deal_vertical   ON deal(vertical_id) WHERE vertical_id IS NOT NULL;

-- ---------------------------------------------------------------------------------------------
-- deal_owner_event: owner-assignment history (hubspot_owner_id propertiesWithHistory).  Needed for the
-- "assigned to person" metric: ACTIVITY follows the actor (deal_stage_event.actor_owner_id), ASSIGNMENT
-- follows the owner-change history.  owner_id NULL = became unassigned.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE deal_owner_event (
  owner_event_id  INTEGER PRIMARY KEY,  -- surrogate key
  deal_id         INTEGER NOT NULL,  -- the deal (RESTRICT: history is never cascaded away)
  account_id      TEXT    NOT NULL,
  owner_id        INTEGER,  -- owner assigned at assigned_at; NULL = became unassigned
  assigned_at     TEXT    NOT NULL CHECK (assigned_at IS strftime('%Y-%m-%dT%H:%M:%fZ', assigned_at) AND assigned_at NOT GLOB '*T24:*'),
  ist_day         TEXT    GENERATED ALWAYS AS (date(assigned_at, '+330 minutes')) STORED NOT NULL,
  source_type     TEXT    CHECK (source_type IS NULL OR (source_type <> '' AND source_type = upper(source_type))),  -- HubSpot sourceType of the change, upper-case
  actor_user_id   TEXT,  -- HubSpot user id (updatedByUserId) who made the change
  event_origin    TEXT    NOT NULL DEFAULT 'hubspot_history'  -- where the row came from (hubspot_history, backfill_checkpoint, ...)
                          CHECK (event_origin IN ('hubspot_history','hubspot_current','backfill_checkpoint','legacy_import','synthetic')),
  import_run_id   INTEGER REFERENCES ops_import_run(import_run_id) ON DELETE SET NULL,
  UNIQUE (deal_id, assigned_at),
  FOREIGN KEY (deal_id, account_id)  REFERENCES deal(deal_id, account_id) ON DELETE RESTRICT,
  FOREIGN KEY (owner_id, account_id) REFERENCES owner(owner_id, account_id)
) STRICT;
CREATE INDEX ix_deal_owner_event_owner ON deal_owner_event(account_id, owner_id, ist_day);

-- ---------------------------------------------------------------------------------------------
-- deal_stage_event: THE EVENT STORE.  One row per dealstage history entry (propertiesWithHistory=dealstage,
-- stage_transitions CSV, backfill checkpoints).  Every report number is computed from this table, so any past
-- day can be recomputed exactly; nothing depends on hs_v2_date_entered_current_stage or on chained snapshots.
--   * entered_at   : UTC instant, millisecond ISO text.  ist_day is a STORED GENERATED column (UTC+05:30):
--                    the IST calendar day is computed in the database, never from the portal timezone.
--   * source_type  : HubSpot sourceType, upper-case (CRM_UI, INTEGRATION, IMPORT, API, ...).  is_human is
--                    STORED GENERATED = (source_type = 'CRM_UI').  Never silently drop automation: reports show
--                    Human / Automated / Total.
--   * pipeline_id  : pipeline of the stage ENTERED (a deal moved between pipelines has history in both).  HubSpot's
--                    propertiesWithHistory=dealstage returns only the stage VALUE, never the pipeline, and three stage ids are
--                    shared by the two MAIN pipelines - so the importer must RESOLVE it and record how in pipeline_basis:
--                    unique_stage_id (the id exists in exactly one pipeline of the account), pipeline_history (the `pipeline`
--                    property history entry in effect at entered_at; fetch propertiesWithHistory=dealstage,hubspot_owner_id,pipeline),
--                    deal_current (fallback: the deal's current pipeline - a GUESS), manual.  An event that cannot be resolved
--                    is NOT inserted: the importer writes ops_dq_issue(rule_code='ambiguous_stage_pipeline') and skips it.
--                    v_event_pipeline_guess counts the deal_current / manual rows for the report footer.
--   * actor_user_id: HubSpot USER id (updatedByUserId); actor_owner_id is the resolved owner of the same account.
--   * from_*       : previous stage, derived by the importer from the previous history entry (NULL on the first).  A move
--                    to the same stage id of the SAME pipeline is not a transition (CHECK); a pipeline change that keeps a
--                    shared stage id (Coding -> CoOps, Interested) is.
--   * event_origin : where the row came from.  'synthetic' / 'hubspot_current' rows (state snapshots, not history) can
--                    never claim source_type 'CRM_UI' (CHECK), so they cannot inflate human activity - give them
--                    source_type 'SYNTHETIC'.  stage_transitions_csv rows have second precision and IST wall-clock source
--                    time (converted to UTC '.000Z'); import them only for deals without hubspot_history rows, or dedupe on
--                    (deal_id, to_stage_id, substr(entered_at,1,19)) - see ix_dse_dedupe_sec / v_dse_near_duplicate.
-- Idempotent re-import key: UNIQUE (deal_id, entered_at, pipeline_id, to_stage_id).
-- ---------------------------------------------------------------------------------------------
CREATE TABLE deal_stage_event (
  event_id         INTEGER PRIMARY KEY,  -- surrogate key
  deal_id          INTEGER NOT NULL,  -- the deal (RESTRICT: history is never cascaded away)
  account_id       TEXT    NOT NULL,
  pipeline_id      TEXT    NOT NULL,  -- pipeline of the stage ENTERED; resolved by the importer, see pipeline_basis
  to_stage_id      TEXT    NOT NULL,  -- stage entered (with account_id, pipeline_id: composite FK to stage, tombstones allowed)
  from_pipeline_id TEXT,  -- pipeline of the previous stage (NULL on the first event)
  from_stage_id    TEXT,  -- previous stage, derived from the previous history entry (NULL on the first)
  entered_at       TEXT    NOT NULL CHECK (entered_at IS strftime('%Y-%m-%dT%H:%M:%fZ', entered_at) AND entered_at NOT GLOB '*T24:*'),
  ist_day          TEXT    GENERATED ALWAYS AS (date(entered_at, '+330 minutes')) STORED NOT NULL,
  source_type      TEXT    NOT NULL CHECK (source_type <> '' AND source_type = upper(source_type) AND source_type = trim(source_type)),  -- HubSpot sourceType, upper-case (CRM_UI, INTEGRATION, IMPORT, API, MERGE_OBJECTS, SYNTHETIC)
  is_human         INTEGER GENERATED ALWAYS AS (source_type = 'CRM_UI') STORED,
  source_id        TEXT,  -- HubSpot sourceId (workflow / integration / user reference)
  actor_user_id    TEXT    CHECK (actor_user_id IS NULL OR (actor_user_id <> '' AND actor_user_id NOT GLOB '*[^0-9]*' AND actor_user_id NOT GLOB '0*')),  -- HubSpot USER id (updatedByUserId), digits
  actor_owner_id   INTEGER,  -- the same actor resolved to an owner of this portal
  pipeline_basis   TEXT    NOT NULL CHECK (pipeline_basis IN ('unique_stage_id','pipeline_history','deal_current','manual')),   -- how pipeline_id was resolved; no default on purpose
  event_origin     TEXT    NOT NULL DEFAULT 'hubspot_history'  -- hubspot_history / hubspot_current / stage_transitions_csv / backfill_checkpoint / legacy_import / synthetic
                           CHECK (event_origin IN ('hubspot_history','hubspot_current','stage_transitions_csv','backfill_checkpoint','legacy_import','synthetic')),
  import_run_id    INTEGER REFERENCES ops_import_run(import_run_id) ON DELETE SET NULL,
  created_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at) AND created_at NOT GLOB '*T24:*'),
  UNIQUE (deal_id, entered_at, pipeline_id, to_stage_id),
  FOREIGN KEY (deal_id, account_id)                       REFERENCES deal(deal_id, account_id) ON DELETE RESTRICT,
  FOREIGN KEY (account_id, pipeline_id, to_stage_id)      REFERENCES stage(account_id, pipeline_id, stage_id) ON DELETE RESTRICT,
  FOREIGN KEY (account_id, from_pipeline_id, from_stage_id) REFERENCES stage(account_id, pipeline_id, stage_id) ON DELETE RESTRICT,
  FOREIGN KEY (actor_owner_id, account_id)                REFERENCES owner(owner_id, account_id),
  CHECK ((from_stage_id IS NULL) = (from_pipeline_id IS NULL)),
  CHECK (from_stage_id IS NULL OR NOT (from_pipeline_id = pipeline_id AND from_stage_id = to_stage_id)),
  CHECK (event_origin NOT IN ('synthetic','hubspot_current') OR source_type <> 'CRM_UI')
) STRICT;
-- by deal: served by the UNIQUE index above (deal_id, entered_at, ...).
-- report: entries into a stage over a time range; deal_id appended so "distinct deals that ever reached stage X" is covering
CREATE INDEX ix_dse_stage_time ON deal_stage_event(account_id, pipeline_id, to_stage_id, entered_at, deal_id);
-- report: everything that happened on an IST day (daily flow, rebuild of one day).  ist_day LEADS so `WHERE ist_day = ?` is an index
-- SEARCH even without ANALYZE statistics (a skip-scan is only chosen when sqlite_stat1 exists).
CREATE INDEX ix_dse_ist_day    ON deal_stage_event(ist_day, account_id, pipeline_id, to_stage_id);
-- per-person activity (credited to the actor): one owner over a range, and all owners for one day
CREATE INDEX ix_dse_actor      ON deal_stage_event(actor_owner_id, ist_day) WHERE actor_owner_id IS NOT NULL;
CREATE INDEX ix_dse_ist_day_actor ON deal_stage_event(ist_day, actor_owner_id) WHERE actor_owner_id IS NOT NULL;
-- cohort join (first event of a deal in a pipeline) and the first human event of a deal become index probes, not table scans
CREATE INDEX ix_dse_deal_pipe_time ON deal_stage_event(deal_id, pipeline_id, entered_at);
CREATE INDEX ix_dse_first_human    ON deal_stage_event(deal_id, entered_at) WHERE is_human = 1;
-- near-duplicate detection across event_origins (CSV second precision vs API millisecond precision)
CREATE INDEX ix_dse_dedupe_sec ON deal_stage_event(deal_id, to_stage_id, substr(entered_at, 1, 19));
CREATE INDEX ix_dse_run        ON deal_stage_event(import_run_id) WHERE import_run_id IS NOT NULL;
-- (no index on from_stage: a stage can never be deleted while an event references it - ON DELETE RESTRICT - so it would cost 6 MB per
--  220k events to serve an operation that is forbidden anyway)

-- ---------------------------------------------------------------------------------------------
-- deal_contact / deal_company: HubSpot associations.  company side points at the PORTAL company row
-- (company_account_link), not the golden record.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE deal_contact (
  deal_id      INTEGER NOT NULL,  -- the deal
  contact_id   INTEGER NOT NULL,  -- the contact (same portal, composite FK)
  account_id   TEXT    NOT NULL,
  is_primary   INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0, 1)),
  assoc_label  TEXT,  -- HubSpot association label
  created_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at) AND created_at NOT GLOB '*T24:*'),
  PRIMARY KEY (deal_id, contact_id),
  FOREIGN KEY (deal_id, account_id)    REFERENCES deal(deal_id, account_id)       ON DELETE CASCADE,
  FOREIGN KEY (contact_id, account_id) REFERENCES contact(contact_id, account_id) ON DELETE CASCADE
) STRICT;
CREATE INDEX ix_deal_contact_contact ON deal_contact(contact_id);

CREATE TABLE deal_company (
  deal_id      INTEGER NOT NULL,  -- the deal
  link_id      INTEGER NOT NULL,  -- the PORTAL company row (company_account_link), not the golden company
  account_id   TEXT    NOT NULL,
  is_primary   INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0, 1)),
  assoc_label  TEXT,  -- HubSpot association label
  created_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at) AND created_at NOT GLOB '*T24:*'),
  PRIMARY KEY (deal_id, link_id),
  FOREIGN KEY (deal_id, account_id) REFERENCES deal(deal_id, account_id)                 ON DELETE CASCADE,
  FOREIGN KEY (link_id, account_id) REFERENCES company_account_link(link_id, account_id) ON DELETE CASCADE
) STRICT;
CREATE INDEX ix_deal_company_link ON deal_company(link_id);
-- HubSpot's PRIMARY company association is single-valued per deal.  (deal_contact has no such rule: HubSpot allows several labelled
-- contact associations, so it is not constrained.)
CREATE UNIQUE INDEX ux_deal_company_primary ON deal_company(deal_id) WHERE is_primary = 1;

-- ---------------------------------------------------------------------------------------------
-- engagement: HubSpot notes (kind = 'note' - the corpus the note classifier is regression-tested against:
-- 5,113 + 1,849 + 561), tasks, calls (only a handful exist), meetings, emails.  note_bucket holds the output
-- of the single unified note classifier (NULL = not classified yet); is_human_written separates system notes.
-- Associations are in engagement_assoc (a note can hang off several deals).
-- ---------------------------------------------------------------------------------------------
CREATE TABLE engagement (
  engagement_id        INTEGER PRIMARY KEY,  -- surrogate key
  account_id           TEXT    NOT NULL REFERENCES account(account_id),
  hs_engagement_id     TEXT    NOT NULL CHECK (hs_engagement_id <> '' AND hs_engagement_id NOT GLOB '*[^0-9]*' AND hs_engagement_id NOT GLOB '0*'),  -- HubSpot engagement id (digits, no leading zero)
  kind                 TEXT    NOT NULL CHECK (kind IN ('note','task','call','meeting','email','sms','other')),  -- note / task / call / meeting / email / sms / other
  owner_id             INTEGER,  -- owner of the engagement
  occurred_at          TEXT    NOT NULL CHECK (occurred_at IS strftime('%Y-%m-%dT%H:%M:%fZ', occurred_at) AND occurred_at NOT GLOB '*T24:*'),            -- hs_timestamp
  ist_day              TEXT    GENERATED ALWAYS AS (date(occurred_at, '+330 minutes')) STORED NOT NULL,
  subject              TEXT,  -- subject / title
  body_text            TEXT,  -- plain-text body
  body_sha256          TEXT    CHECK (body_sha256 IS NULL OR length(body_sha256) = 64),
  status               TEXT,                                                                          -- tasks: NOT_STARTED / COMPLETED ...
  note_bucket          TEXT,  -- output of the unified note classifier (NULL = not classified yet)
  note_bucket_version  TEXT,  -- version of the classifier that produced note_bucket
  is_human_written     INTEGER NOT NULL DEFAULT 1 CHECK (is_human_written IN (0, 1)),
  is_archived          INTEGER NOT NULL DEFAULT 0 CHECK (is_archived IN (0, 1)),
  hs_created_at        TEXT    CHECK (hs_created_at IS NULL OR (hs_created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', hs_created_at) AND hs_created_at NOT GLOB '*T24:*')),
  hs_updated_at        TEXT    CHECK (hs_updated_at IS NULL OR (hs_updated_at IS strftime('%Y-%m-%dT%H:%M:%fZ', hs_updated_at) AND hs_updated_at NOT GLOB '*T24:*')),
  created_at           TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at) AND created_at NOT GLOB '*T24:*'),
  updated_at           TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (updated_at IS strftime('%Y-%m-%dT%H:%M:%fZ', updated_at) AND updated_at NOT GLOB '*T24:*'),
  UNIQUE (account_id, kind, hs_engagement_id),
  UNIQUE (engagement_id, account_id),
  FOREIGN KEY (owner_id, account_id) REFERENCES owner(owner_id, account_id)
) STRICT;
CREATE INDEX ix_engagement_day   ON engagement(account_id, kind, ist_day);
CREATE INDEX ix_engagement_owner ON engagement(owner_id, ist_day) WHERE owner_id IS NOT NULL;

-- exactly one association per row: deal XOR contact XOR portal company
CREATE TABLE engagement_assoc (
  assoc_id       INTEGER PRIMARY KEY,  -- surrogate key
  engagement_id  INTEGER NOT NULL,  -- the engagement
  account_id     TEXT    NOT NULL,
  deal_id        INTEGER,  -- associated deal (exactly one of deal / contact / link is set)
  contact_id     INTEGER,  -- associated contact
  link_id        INTEGER,  -- associated portal company
  FOREIGN KEY (engagement_id, account_id) REFERENCES engagement(engagement_id, account_id) ON DELETE CASCADE,
  FOREIGN KEY (deal_id, account_id)       REFERENCES deal(deal_id, account_id)             ON DELETE CASCADE,
  FOREIGN KEY (contact_id, account_id)    REFERENCES contact(contact_id, account_id)       ON DELETE CASCADE,
  FOREIGN KEY (link_id, account_id)       REFERENCES company_account_link(link_id, account_id) ON DELETE CASCADE,
  CHECK ((deal_id IS NOT NULL) + (contact_id IS NOT NULL) + (link_id IS NOT NULL) = 1)
) STRICT;
CREATE UNIQUE INDEX ux_engagement_assoc_deal    ON engagement_assoc(engagement_id, deal_id)    WHERE deal_id    IS NOT NULL;
CREATE UNIQUE INDEX ux_engagement_assoc_contact ON engagement_assoc(engagement_id, contact_id) WHERE contact_id IS NOT NULL;
CREATE UNIQUE INDEX ux_engagement_assoc_company ON engagement_assoc(engagement_id, link_id)    WHERE link_id    IS NOT NULL;
CREATE INDEX ix_engagement_assoc_deal    ON engagement_assoc(deal_id)    WHERE deal_id    IS NOT NULL;
CREATE INDEX ix_engagement_assoc_contact ON engagement_assoc(contact_id) WHERE contact_id IS NOT NULL;

-- ---------------------------------------------------------------------------------------------
-- tam_company_verdict: classification of a company within a vertical - category segment, ICP bucket / band,
-- score, reason.  NEVER DELETE REJECTS: a reject is an `out` verdict with its reason, kept forever (the
-- company FK has no cascade).  History is kept: is_current = 1 marks the live verdict per (company, vertical).
-- Placeholders (itsvc 'pre-classification-prior' rows, all builds_software = 1) are is_placeholder = 1, which the
-- CHECK forces to be 'unscored' - they can never be mistaken for a real verdict.
-- icp_bucket rule for importers: a source with NO ICP bucket (itsvc has no fit/maybe/out at all; corpus 'Unclear'/'Weak')
-- yields 'unscored', never 'fit'; the source's own label is kept verbatim in native_bucket.  `segment` is the PRIMARY segment;
-- multi-valued segments, tags, exclusion flags, tech stack, rule hits, keyword scores and the like go to details_json.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE tam_company_verdict (
  verdict_id       INTEGER PRIMARY KEY AUTOINCREMENT,  -- surrogate key (never reused)
  company_id       INTEGER NOT NULL REFERENCES company(company_id),  -- company judged (no cascade: rejects are kept forever)
  vertical_id      INTEGER REFERENCES vertical(vertical_id),  -- vertical the verdict is for
  segment          TEXT,  -- PRIMARY segment
  icp_bucket       TEXT    NOT NULL CHECK (icp_bucket IN ('fit', 'maybe', 'out', 'unscored')),  -- fit / maybe / out / unscored
  icp_band         TEXT    CHECK (icp_band IS NULL OR icp_band IN ('P1', 'P2', 'P3', 'P4')),  -- P1..P4
  priority_tier    TEXT,  -- source's priority tier (tam A-D)
  score            REAL,  -- numeric ICP score from the source
  confidence       REAL    CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
  reason_code      TEXT,  -- machine reason
  reason           TEXT,
  evidence_url     TEXT,  -- page that evidences the verdict
  evidence_quote   TEXT,                                           -- verbatim snippet that justified the verdict (tam classifications.evidence_quote)
  native_bucket    TEXT,                                           -- the source system's own bucket label, verbatim (Fit/Maybe/Out/Unclear/Weak ...)
  details_json     TEXT    NOT NULL DEFAULT '{}' CHECK (json_valid(details_json) AND json_type(details_json) = 'object'),   -- source-specific extras (regulated_status, parked_as, band letter, rules_applied ...)
  verdict_method   TEXT    NOT NULL CHECK (verdict_method IN ('rules', 'llm', 'manual', 'import', 'placeholder')),  -- rules / llm / manual / import / placeholder
  model            TEXT,  -- LLM model that judged
  prompt_version   TEXT,  -- prompt version that judged
  is_placeholder   INTEGER NOT NULL DEFAULT 0 CHECK (is_placeholder IN (0, 1)),
  is_current       INTEGER NOT NULL DEFAULT 1 CHECK (is_current IN (0, 1)),
  decided_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (decided_at IS strftime('%Y-%m-%dT%H:%M:%fZ', decided_at) AND decided_at NOT GLOB '*T24:*'),
  created_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at) AND created_at NOT GLOB '*T24:*'),
  updated_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (updated_at IS strftime('%Y-%m-%dT%H:%M:%fZ', updated_at) AND updated_at NOT GLOB '*T24:*'),
  CHECK (is_placeholder = 0 OR (icp_bucket = 'unscored' AND verdict_method = 'placeholder')),
  CHECK (verdict_method <> 'placeholder' OR is_placeholder = 1)
) STRICT;
CREATE UNIQUE INDEX ux_verdict_current ON tam_company_verdict(company_id, COALESCE(vertical_id, 0)) WHERE is_current = 1;
CREATE INDEX ix_verdict_bucket ON tam_company_verdict(vertical_id, icp_bucket) WHERE is_current = 1;
CREATE INDEX ix_verdict_company ON tam_company_verdict(company_id);                  -- all verdicts of a company incl. history; FK support

-- ---------------------------------------------------------------------------------------------
-- company_signal: the evidence / signal (EAV) data that drives verdicts - corpus indicators (652k), tam signals (uses_jira,
-- mx_provider, open_jobs ...), radar distress tiers / shutdown dates / liveness, itsvc site flags - so "which distress tier / what
-- evidence" is answerable without reading legacy_*.  One row = one observation of one signal for one golden company from one source
-- at one time; the value is typed (bool / number / text).  Funnel-event history (tam.funnel_events) and the CRM push log
-- (tam.crm_pushes) stay legacy-only; their derived effects are suppression rows (removed_off_icp -> kind 'off_icp', removed_too_big ->
-- 'too_big', removed_duplicate -> 'duplicate', pushed -> 'delivered').
-- ---------------------------------------------------------------------------------------------
CREATE TABLE company_signal (
  signal_id         INTEGER PRIMARY KEY,  -- surrogate key
  company_id        INTEGER NOT NULL REFERENCES company(company_id) ON DELETE RESTRICT,  -- company the signal is about
  signal_code       TEXT    NOT NULL CHECK (signal_code <> '' AND signal_code = lower(signal_code) AND signal_code NOT GLOB '*[^a-z0-9_.]*'),   -- distress_tier, uses_jira, cobol_loc ...
  value_bool        INTEGER CHECK (value_bool IS NULL OR value_bool IN (0, 1)),  -- boolean value of the signal (uses_jira)
  value_num         REAL,  -- numeric value of the signal (open_jobs, loc)
  value_text        TEXT,  -- text value of the signal (distress_tier = confirmed)
  confidence        REAL    CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
  evidence_snippet  TEXT,  -- verbatim text that evidences the signal
  evidence_url      TEXT,  -- page the evidence came from
  observed_at       TEXT    CHECK (observed_at IS NULL OR (observed_at IS strftime('%Y-%m-%dT%H:%M:%fZ', observed_at) AND observed_at NOT GLOB '*T24:*')),
  source_system     TEXT    NOT NULL CHECK (source_system <> ''),
  import_run_id     INTEGER REFERENCES ops_import_run(import_run_id) ON DELETE SET NULL,
  created_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at) AND created_at NOT GLOB '*T24:*'),
  CHECK (value_bool IS NOT NULL OR value_num IS NOT NULL OR value_text IS NOT NULL)
) STRICT;
CREATE UNIQUE INDEX ux_company_signal_obs ON company_signal(company_id, signal_code, source_system, COALESCE(observed_at, ''));
CREATE INDEX ix_company_signal_code ON company_signal(signal_code, company_id);

-- ---------------------------------------------------------------------------------------------
-- cost_rate / cost_ledger: credit-spending vendors (Apollo, SignalHire, Apify, Google Maps, LinkedIn search ...).
-- qty is in the vendor's own unit; usd is NULL when unknown (never 0 as a stand-in: the legacy usd_est = 0 rows
-- load with usd NULL and usd_basis 'unknown' - the QUANTITIES are real spend signals, only the price is missing).
-- is_placeholder = 1 is for rows that are fake as a whole (CHECK: a placeholder carries no price); a real call with an
-- unknown price is is_placeholder = 0.  `qty` = calls/units, `credits` = vendor credits (phone_reveal burns 8 per call).
-- cost_rate prices a unit so usd can be back-filled and re-priced later.  Money is REAL USD (ARCHITECTURE section 1):
-- aggregate with ROUND(SUM(usd), 6).
-- ---------------------------------------------------------------------------------------------
CREATE TABLE cost_rate (
  rate_id         INTEGER PRIMARY KEY,  -- surrogate key
  vendor          TEXT    NOT NULL CHECK (vendor <> '' AND vendor = lower(vendor)),  -- lower-case vendor (apollo, signalhire, apify, google_maps ...)
  unit            TEXT    NOT NULL CHECK (unit <> ''),  -- billing unit (org_enrich, phone_reveal, tile ...)
  usd_per_unit    REAL    NOT NULL CHECK (usd_per_unit >= 0),  -- price of one unit in USD
  effective_from  TEXT    NOT NULL CHECK (length(effective_from) = 10 AND date(effective_from) IS effective_from),  -- first day this price applies
  source_note     TEXT,  -- where the price came from
  created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at) AND created_at NOT GLOB '*T24:*'),
  UNIQUE (vendor, unit, effective_from)
) STRICT;

CREATE TABLE cost_ledger (
  cost_id        INTEGER PRIMARY KEY AUTOINCREMENT,  -- surrogate key (never reused)
  vendor         TEXT    NOT NULL CHECK (vendor <> '' AND vendor = lower(vendor)),   -- apollo, signalhire, apify, google_maps, linkedin_search, ...
  unit           TEXT    NOT NULL CHECK (unit <> ''),                                 -- org_enrich, phone_reveal, search_call, tile, ...
  qty            REAL    NOT NULL CHECK (qty >= 0),  -- quantity in the vendor's own unit (calls, tiles, ...)
  credits        REAL    CHECK (credits IS NULL OR credits >= 0),  -- vendor credits burned (phone_reveal burns 8 per call)
  usd            REAL    CHECK (usd IS NULL OR usd >= 0),  -- price in USD; NULL = unknown (never 0 as a stand-in)
  usd_basis      TEXT    NOT NULL DEFAULT 'unknown' CHECK (usd_basis IN ('actual', 'estimated', 'unknown')),  -- actual / estimated / unknown; unknown iff usd is NULL
  currency       TEXT    NOT NULL DEFAULT 'USD' CHECK (length(currency) = 3 AND currency = upper(currency)),
  occurred_at    TEXT    NOT NULL CHECK (occurred_at IS strftime('%Y-%m-%dT%H:%M:%fZ', occurred_at) AND occurred_at NOT GLOB '*T24:*'),
  company_id     INTEGER REFERENCES company(company_id) ON DELETE SET NULL,  -- company the spend was for, when attributable
  vertical_id    INTEGER REFERENCES vertical(vertical_id),  -- vertical the spend was for, when attributable
  account_id     TEXT    REFERENCES account(account_id),
  run_ref        TEXT,                                                                -- vendor / pipeline run id (free text)
  note           TEXT,
  is_placeholder INTEGER NOT NULL DEFAULT 0 CHECK (is_placeholder IN (0, 1)),
  source_system  TEXT,
  created_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at) AND created_at NOT GLOB '*T24:*'),
  CHECK ((usd IS NULL) = (usd_basis = 'unknown')),
  CHECK (is_placeholder = 0 OR usd IS NULL)
) STRICT;
CREATE INDEX ix_cost_vendor_time ON cost_ledger(vendor, occurred_at);
CREATE INDEX ix_cost_company     ON cost_ledger(company_id) WHERE company_id IS NOT NULL;
CREATE INDEX ix_cost_vertical    ON cost_ledger(vertical_id) WHERE vertical_id IS NOT NULL;
CREATE INDEX ix_cost_account     ON cost_ledger(account_id) WHERE account_id IS NOT NULL;

-- ---------------------------------------------------------------------------------------------
-- suppression: never-push / do-not-contact / competitor lists.  kind 'never_push' = the whale list
-- (whales.csv, Whale_List_Untouched_FINAL.csv, whale_list_27_NEVER_PUSH_TO_HUBSPOT.csv): NEVER pushable.
-- match_value_norm is normalised like the matching identifier (domain lowercase, email lowercase, phone E.164,
-- linkedin 'company/<slug>' or 'in/<slug>').  The match is an EXACT string compare against match_value_norm, so a stored
-- but un-normalised value would silently never match (a bypass of the one hard 'never push' rule): the CHECKs below
-- enforce the same normalisation contract as company_identifier, and leadgen.norm.* + leadgen.suppression.is_suppressed()
-- are the only sanctioned way to build / probe a value.  account_id NULL = global; set it for portal-specific
-- suppression (e.g. a HubSpot unsubscribe).  Enforce before ANY outbound write.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE suppression (
  suppression_id    INTEGER PRIMARY KEY AUTOINCREMENT,  -- surrogate key (never reused)
  kind              TEXT    NOT NULL  -- never_push (whale list) / dnc / competitor / unsubscribed / duplicate / off_icp / too_big / delivered / sheet_worked / bad_contact / other
                            CHECK (kind IN ('never_push','dnc','competitor','unsubscribed','duplicate','off_icp','too_big','delivered','sheet_worked','bad_contact','other')),
  match_type        TEXT    NOT NULL CHECK (match_type IN ('domain','email','phone','linkedin_company','linkedin_person','cin','company_name','company_id')),  -- what match_value_norm is: domain / email / phone / linkedin_company / linkedin_person / cin / company_name / company_id
  match_value_norm  TEXT    NOT NULL CHECK (match_value_norm <> '' AND match_value_norm = trim(match_value_norm)),  -- normalised value, compared EXACTLY (CHECKs enforce the contract; probe through leadgen.suppression)
  company_id        INTEGER REFERENCES company(company_id),  -- golden company when known
  account_id        TEXT    REFERENCES account(account_id),
  reason            TEXT,
  list_name         TEXT,                                          -- source list / file the entry came from
  is_active         INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
  added_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (added_at IS strftime('%Y-%m-%dT%H:%M:%fZ', added_at) AND added_at NOT GLOB '*T24:*'),
  expires_at        TEXT    CHECK (expires_at IS NULL OR (expires_at IS strftime('%Y-%m-%dT%H:%M:%fZ', expires_at) AND expires_at NOT GLOB '*T24:*')),
  created_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at) AND created_at NOT GLOB '*T24:*'),
  updated_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (updated_at IS strftime('%Y-%m-%dT%H:%M:%fZ', updated_at) AND updated_at NOT GLOB '*T24:*'),
  CHECK (match_type <> 'domain' OR (match_value_norm = lower(match_value_norm) AND instr(match_value_norm, '.') > 1 AND match_value_norm NOT GLOB '*[^a-z0-9.-]*'
         AND match_value_norm NOT GLOB '*..*' AND match_value_norm NOT GLOB '.*' AND match_value_norm NOT GLOB '*.' AND match_value_norm NOT GLOB '-*'
         AND match_value_norm NOT GLOB '*-.*' AND match_value_norm NOT GLOB '*.-*' AND match_value_norm NOT GLOB 'www.*')),
  CHECK (match_type <> 'email'  OR (match_value_norm = lower(match_value_norm) AND instr(match_value_norm, '@') > 1 AND match_value_norm NOT GLOB '* *')),
  CHECK (match_type <> 'company_name' OR match_value_norm = lower(match_value_norm)),
  CHECK (match_type <> 'linkedin_company' OR (match_value_norm = lower(match_value_norm) AND (match_value_norm GLOB 'company/*' OR match_value_norm GLOB 'school/*' OR match_value_norm GLOB 'showcase/*')
         AND match_value_norm NOT GLOB '*/' AND match_value_norm NOT GLOB '* *' AND match_value_norm NOT GLOB '*[?#]*')),
  CHECK (match_type <> 'linkedin_person' OR (match_value_norm = lower(match_value_norm) AND match_value_norm GLOB 'in/*' AND match_value_norm NOT GLOB '*/'
         AND match_value_norm NOT GLOB '* *' AND match_value_norm NOT GLOB '*[?#]*')),
  CHECK (match_type <> 'phone'  OR (match_value_norm GLOB '+[1-9]*' AND match_value_norm NOT GLOB '+*[^0-9]*')),
  CHECK (match_type <> 'cin'    OR (length(match_value_norm) = 21 AND match_value_norm NOT GLOB '*[^A-Z0-9]*')),
  CHECK (match_type <> 'company_id' OR company_id IS NOT NULL),
  CHECK (expires_at IS NULL OR expires_at >= added_at)
) STRICT;
CREATE UNIQUE INDEX ux_suppression_entry ON suppression(kind, match_type, match_value_norm, COALESCE(account_id, ''));
CREATE UNIQUE INDEX ux_suppression_never_push ON suppression(match_type, match_value_norm) WHERE kind = 'never_push' AND is_active = 1;   -- one whale row per value, not one per account
CREATE INDEX ix_suppression_lookup ON suppression(match_type, match_value_norm) WHERE is_active = 1;
CREATE INDEX ix_suppression_account ON suppression(account_id) WHERE account_id IS NOT NULL;
CREATE INDEX ix_suppression_company ON suppression(company_id) WHERE company_id IS NOT NULL;

-- ---------------------------------------------------------------------------------------------
-- merge_candidate: weak matches (name-only, name + city, phone clashes, ...) queued for a HUMAN.  Never
-- auto-merged.  (entity_type, left_id, right_id) is ordered left < right so a pair is stored once.  left_id /
-- right_id are polymorphic; a trigger validates they exist.  Accepting a candidate is done by merging the
-- companies / contacts (tombstone + identifier move) and recording the decision here and in ops_audit_log.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE merge_candidate (
  merge_candidate_id  INTEGER PRIMARY KEY,  -- surrogate key
  entity_type         TEXT    NOT NULL CHECK (entity_type IN ('company', 'contact')),  -- company or contact
  left_id             INTEGER NOT NULL,  -- smaller id of the pair (CHECK left < right: a pair is stored once)
  right_id            INTEGER NOT NULL,  -- larger id of the pair
  match_kind          TEXT    NOT NULL CHECK (match_kind <> ''),      -- name_norm, name_city, phone, email_domain, shared_domain, ...
  score               REAL    CHECK (score IS NULL OR (score >= 0 AND score <= 1)),  -- 0..1 similarity
  evidence_json       TEXT    NOT NULL DEFAULT '{}' CHECK (json_valid(evidence_json)),
  status              TEXT    NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'accepted', 'rejected', 'deferred')),
  decided_by          TEXT,  -- who accepted / rejected it
  decided_at          TEXT    CHECK (decided_at IS NULL OR (decided_at IS strftime('%Y-%m-%dT%H:%M:%fZ', decided_at) AND decided_at NOT GLOB '*T24:*')),
  decision_note       TEXT,  -- why
  import_run_id       INTEGER REFERENCES ops_import_run(import_run_id) ON DELETE SET NULL,
  created_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at) AND created_at NOT GLOB '*T24:*'),
  updated_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (updated_at IS strftime('%Y-%m-%dT%H:%M:%fZ', updated_at) AND updated_at NOT GLOB '*T24:*'),
  UNIQUE (entity_type, left_id, right_id, match_kind),
  CHECK (left_id < right_id),
  CHECK ((status = 'open') = (decided_at IS NULL))
) STRICT;
CREATE INDEX ix_merge_candidate_open  ON merge_candidate(status, entity_type) WHERE status = 'open';
CREATE INDEX ix_merge_candidate_right ON merge_candidate(entity_type, right_id);

-- ---------------------------------------------------------------------------------------------
-- Reference-data seeds.  Mirrors config/accounts.yaml and config/canonical_stages.yaml (the YAML is the
-- editable source of truth; leadgen.db.seed_reference_data() upserts it and tests/test_schema.py fails on
-- drift).  Seeding here keeps a freshly migrated database self-sufficient: a deal can be inserted
-- without running any Python first.  INSERT OR IGNORE keeps a re-run from clobbering later edits.
-- ---------------------------------------------------------------------------------------------
INSERT INTO pipeline (account_id, pipeline_id, label, funnel_slug, display_order, is_reporting_funnel, cohort_start, cohort_exclude_migration_on, previous_labels_json) VALUES
  ('main', 'default', 'Coding', 'coding', 1, 1, NULL, NULL, '["Scraped"]'),
  ('main', '2425754306', 'CoOps ( Global )', 'coops_global', 2, 1, NULL, NULL, '["Campaign"]'),
  ('companyops', 'default', 'Company Ops Cluster 1 India', 'cluster1', 1, 1, NULL, NULL, '[]'),
  ('companyops', '2464812771', 'Company Ops Cluster 2 India', 'cluster2', 2, 1, '2026-09-15', '2026-09-15', '["Company Ops Data"]'),
  ('rat', '2575252183', 'Rapid Action Team', 'rat', 1, 1, NULL, NULL, '[]'),
  ('rat', 'default', 'Deals pipeline', NULL, 2, 0, NULL, NULL, '[]');

INSERT INTO canonical_stage (canonical_code, rank_label, depth, sort_order, label, definition, is_live, is_dead, is_won, is_derived, is_entry, flag_attempt, flag_connected, flag_interested) VALUES
  ('SOURCED', '0', 0.0, 1, 'Sourced / Assigned', 'Lead assigned to a caller and entered the funnel (Cold Call, Cold Lead, Cold called assigned). Any source counts; this is the new-lead entry row.', 1, 0, 0, 0, 1, 0, 0, 0),
  ('LI_SENT', '0b', 0.5, 2, 'Outreach sent: LinkedIn', 'LinkedIn connection request sent (channel entry, automation-dominated via OutFlo). Not a row in the legacy Cluster report; shown in its own Human / Automated sub-section.', 1, 0, 0, 0, 1, 0, 0, 0),
  ('NO_ANSWER', '1', 1.0, 3, 'Attempted: no answer', 'A dial was attempted and nobody picked up (No Pickup, No Response, Callback, the retired "Callback +1 day"). The loop stage; a deal may re-enter it many times.', 1, 0, 0, 0, 0, 1, 0, 0),
  ('LI_CONNECTED', '1b', 1.5, 4, 'Connected: LinkedIn', 'LinkedIn connection accepted. Counts as an attempt (outreach happened) but not as a live connect.', 1, 0, 0, 0, 0, 1, 0, 0),
  ('REPLIED', '2', 2.0, 5, 'Connected / Replied', 'A human at the prospect answered or replied (Replied, Communicated). Coding has no such stage; a move into Interested satisfies attempted + connected + interested in one event.', 1, 0, 0, 0, 0, 1, 1, 0),
  ('INTERESTED', '3', 3.0, 6, 'Interested / first interest', 'Prospect expressed interest (Interested, 1st interest sent, 1st interest follow up).', 1, 0, 0, 0, 0, 1, 1, 1),
  ('MEETING_BOOKED', '4', 4.0, 7, 'Meeting booked', 'A discovery call / VC / GMeet slot was fixed or rescheduled (GMeet Fixed, VC Fixed, VC Rescheduled, Discovery call, Call rescheduled). Booked is not held.', 1, 0, 0, 0, 0, 1, 1, 1),
  ('MEETING_HELD', '5', 5.0, 8, 'Meeting held', 'DERIVED, no native stage. A deal with a meeting-booked entry that later reached an artefact rung or a post-meeting dead reason, and never entered No-show or Cancelled. Credited to the meeting day when gmeet1_date / discovery_call_date is populated, else the booking day.', 1, 0, 0, 1, 0, 0, 0, 0),
  ('ASSET_REQUESTED', '6', 6.0, 9, 'Asset / sample requested', 'The deliverable to evaluate was requested or sent (Script Shared, 1 Pager Shared, One pager requested / follow up, Sample Requested / Follow Up).', 1, 0, 0, 0, 0, 0, 0, 0),
  ('ASSET_RECEIVED', '7', 7.0, 10, 'Asset / sample received', 'The prospect''s output came back (Script Results Received, 1 Pager Output Received, One pager received, Sample Received).', 1, 0, 0, 0, 0, 0, 0, 0),
  ('EVALUATED', '8', 8.0, 11, 'Evaluated', 'DERIVED, no native stage. The deal left ASSET_RECEIVED for a post-evaluation outcome (a quality dead reason, or a move to negotiation / LOI).', 1, 0, 0, 1, 0, 0, 0, 0),
  ('NEGOTIATION', '9', 9.0, 12, 'Negotiation / LOI', 'Commercial negotiation or LOI (Commercial Negotiation, LOI, LOI Signed, Negotiation). CoOps ( Global ) Sample Extraction and Demand Fulfillment sit here as sub-rows (stage_map.sub_row).', 1, 0, 0, 0, 0, 0, 0, 0),
  ('CONTRACT', '10', 10.0, 13, 'Contract signed', 'Contract signed (Deal Contract Signed, Contract Signed, Contract signed). Not the same as Won.', 1, 0, 0, 0, 0, 0, 0, 0),
  ('DELIVERED', '11', 11.0, 14, 'Delivered / handover', 'Data delivered or handed over (Data Migration Done, Metadata Matched, Data Extraction Done, Ops data handover done).', 1, 0, 0, 0, 0, 0, 0, 0),
  ('PAYMENT', '12', 12.0, 15, 'Payment initiated', 'Payment initiated (Payment Initiation). CoOps ( Global ) has no such stage.', 1, 0, 0, 0, 0, 0, 0, 0),
  ('WON', '13', 13.0, 16, 'Won', 'Closed/Won, and only Closed/Won (Closed / Won with spaces in RAT).', 0, 0, 1, 0, 0, 0, 0, 0),
  ('DEAD', 'X', 100.0, 17, 'Dead', 'Terminal loss. Every dead native stage maps here and carries a dead reason plus the depth reached before dying (stage_map.dead_reason_code, stage_map.depth_reached). A revived deal can die more than once, so count distinct deals as well as entries.', 0, 1, 0, 0, 0, 0, 0, 0);

INSERT INTO canonical_dead_reason (dead_reason_code, sort_order, label, definition, flag_attempt, flag_connected, counts_in_effort_kpis) VALUES
  ('WRONG_FIT', 1, 'Wrong fit (screened)', 'Screened out without ever being dialled.', 0, 0, 1),
  ('BAD_CONTACT', 2, 'Bad contact (wrong or invalid number)', 'A dial happened but the number or contact is wrong/invalid.', 1, 0, 1),
  ('NO_RESPONSE', 3, 'No response / unreachable', 'Never answered or replied after repeated attempts, or the requested artefact never arrived.', 1, 0, 1),
  ('NOT_INTERESTED', 4, 'Not interested (declined)', 'Prospect declined, at any stage up to and including discovery.', 1, 1, 1),
  ('NO_SHOW', 5, 'No-show', 'Booked meeting not attended by the prospect.', 1, 1, 1),
  ('MEETING_CANCELLED', 6, 'Meeting cancelled before held', 'Meeting cancelled; no effort spent, excluded from "meetings held".', 1, 1, 1),
  ('REJECTED_BY_LH2', 7, 'Rejected by LH2 / quality fail', 'LH2 rejected the lead after a meeting or after evaluating the asset (wrong fit after call, low data quality, failed extraction, requirements not met).', 1, 1, 1),
  ('PRIVACY', 8, 'Privacy / compliance', 'Prospect cannot proceed for privacy or compliance reasons.', 1, 1, 1),
  ('COMMERCIAL', 9, 'Commercial (price or terms)', 'Price or contractual terms not agreed during negotiation / LOI.', 1, 1, 1),
  ('RETIRED_ADMIN', 10, 'Retired / administrative', 'Not a funnel outcome (Dead - Email Campaign / Branch Retired, Dropped - Pretraining, Call Attempted (retired)). Excluded from every effort KPI and shown under "Retired".', 0, 0, 0);

-- ---------------------------------------------------------------------------------------------
-- updated_at maintenance: one trigger per mutable table (WHEN NEW.updated_at = OLD.updated_at lets a writer set
-- the value explicitly and stops the trigger re-firing on its own UPDATE).
-- ---------------------------------------------------------------------------------------------
CREATE TRIGGER trg_owner_touch AFTER UPDATE ON owner
FOR EACH ROW WHEN NEW.updated_at = OLD.updated_at AND OLD.updated_at <> strftime('%Y-%m-%dT%H:%M:%fZ','now')
BEGIN
  UPDATE owner SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE rowid = NEW.rowid;
END;
CREATE TRIGGER trg_pipeline_touch AFTER UPDATE ON pipeline
FOR EACH ROW WHEN NEW.updated_at = OLD.updated_at AND OLD.updated_at <> strftime('%Y-%m-%dT%H:%M:%fZ','now')
BEGIN
  UPDATE pipeline SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE rowid = NEW.rowid;
END;
CREATE TRIGGER trg_stage_touch AFTER UPDATE ON stage
FOR EACH ROW WHEN NEW.updated_at = OLD.updated_at AND OLD.updated_at <> strftime('%Y-%m-%dT%H:%M:%fZ','now')
BEGIN
  UPDATE stage SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE rowid = NEW.rowid;
END;
CREATE TRIGGER trg_stage_map_touch AFTER UPDATE ON stage_map
FOR EACH ROW WHEN NEW.updated_at = OLD.updated_at AND OLD.updated_at <> strftime('%Y-%m-%dT%H:%M:%fZ','now')
BEGIN
  UPDATE stage_map SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE rowid = NEW.rowid;
END;
CREATE TRIGGER trg_lead_source_touch AFTER UPDATE ON lead_source
FOR EACH ROW WHEN NEW.updated_at = OLD.updated_at AND OLD.updated_at <> strftime('%Y-%m-%dT%H:%M:%fZ','now')
BEGIN
  UPDATE lead_source SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE rowid = NEW.rowid;
END;
CREATE TRIGGER trg_vertical_touch AFTER UPDATE ON vertical
FOR EACH ROW WHEN NEW.updated_at = OLD.updated_at AND OLD.updated_at <> strftime('%Y-%m-%dT%H:%M:%fZ','now')
BEGIN
  UPDATE vertical SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE rowid = NEW.rowid;
END;
CREATE TRIGGER trg_company_touch AFTER UPDATE ON company
FOR EACH ROW WHEN NEW.updated_at = OLD.updated_at AND OLD.updated_at <> strftime('%Y-%m-%dT%H:%M:%fZ','now')
BEGIN
  UPDATE company SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE rowid = NEW.rowid;
END;
CREATE TRIGGER trg_company_identifier_touch AFTER UPDATE ON company_identifier
FOR EACH ROW WHEN NEW.updated_at = OLD.updated_at AND OLD.updated_at <> strftime('%Y-%m-%dT%H:%M:%fZ','now')
BEGIN
  UPDATE company_identifier SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE rowid = NEW.rowid;
END;
CREATE TRIGGER trg_company_account_link_touch AFTER UPDATE ON company_account_link
FOR EACH ROW WHEN NEW.updated_at = OLD.updated_at AND OLD.updated_at <> strftime('%Y-%m-%dT%H:%M:%fZ','now')
BEGIN
  UPDATE company_account_link SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE rowid = NEW.rowid;
END;
CREATE TRIGGER trg_contact_touch AFTER UPDATE ON contact
FOR EACH ROW WHEN NEW.updated_at = OLD.updated_at AND OLD.updated_at <> strftime('%Y-%m-%dT%H:%M:%fZ','now')
BEGIN
  UPDATE contact SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE rowid = NEW.rowid;
END;
CREATE TRIGGER trg_contact_phone_touch AFTER UPDATE ON contact_phone
FOR EACH ROW WHEN NEW.updated_at = OLD.updated_at AND OLD.updated_at <> strftime('%Y-%m-%dT%H:%M:%fZ','now')
BEGIN
  UPDATE contact_phone SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE rowid = NEW.rowid;
END;
CREATE TRIGGER trg_company_phone_touch AFTER UPDATE ON company_phone
FOR EACH ROW WHEN NEW.updated_at = OLD.updated_at AND OLD.updated_at <> strftime('%Y-%m-%dT%H:%M:%fZ','now')
BEGIN
  UPDATE company_phone SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE rowid = NEW.rowid;
END;
CREATE TRIGGER trg_deal_touch AFTER UPDATE ON deal
FOR EACH ROW WHEN NEW.updated_at = OLD.updated_at AND OLD.updated_at <> strftime('%Y-%m-%dT%H:%M:%fZ','now')
BEGIN
  UPDATE deal SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE rowid = NEW.rowid;
END;
CREATE TRIGGER trg_engagement_touch AFTER UPDATE ON engagement
FOR EACH ROW WHEN NEW.updated_at = OLD.updated_at AND OLD.updated_at <> strftime('%Y-%m-%dT%H:%M:%fZ','now')
BEGIN
  UPDATE engagement SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE rowid = NEW.rowid;
END;
CREATE TRIGGER trg_tam_company_verdict_touch AFTER UPDATE ON tam_company_verdict
FOR EACH ROW WHEN NEW.updated_at = OLD.updated_at AND OLD.updated_at <> strftime('%Y-%m-%dT%H:%M:%fZ','now')
BEGIN
  UPDATE tam_company_verdict SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE rowid = NEW.rowid;
END;
CREATE TRIGGER trg_suppression_touch AFTER UPDATE ON suppression
FOR EACH ROW WHEN NEW.updated_at = OLD.updated_at AND OLD.updated_at <> strftime('%Y-%m-%dT%H:%M:%fZ','now')
BEGIN
  UPDATE suppression SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE rowid = NEW.rowid;
END;
CREATE TRIGGER trg_merge_candidate_touch AFTER UPDATE ON merge_candidate
FOR EACH ROW WHEN NEW.updated_at = OLD.updated_at AND OLD.updated_at <> strftime('%Y-%m-%dT%H:%M:%fZ','now')
BEGIN
  UPDATE merge_candidate SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE rowid = NEW.rowid;
END;

-- ---------------------------------------------------------------------------------------------
-- origin_ref integrity.  entity_id is polymorphic, so a real FK is impossible: BEFORE INSERT / UPDATE triggers
-- verify the target exists, and AFTER DELETE triggers on every canonical table remove the row's origin_ref
-- entries.  (SQLite cannot declare this as a constraint.)
-- ---------------------------------------------------------------------------------------------
CREATE TRIGGER trg_origin_ref_entity_ins BEFORE INSERT ON origin_ref
BEGIN
  SELECT RAISE(ABORT, 'origin_ref: entity_type/entity_id does not reference an existing canonical row')
   WHERE NOT CASE NEW.entity_type
      WHEN 'company' THEN EXISTS (SELECT 1 FROM company WHERE company_id = NEW.entity_id)
      WHEN 'company_identifier' THEN EXISTS (SELECT 1 FROM company_identifier WHERE identifier_id = NEW.entity_id)
      WHEN 'company_account_link' THEN EXISTS (SELECT 1 FROM company_account_link WHERE link_id = NEW.entity_id)
      WHEN 'contact' THEN EXISTS (SELECT 1 FROM contact WHERE contact_id = NEW.entity_id)
      WHEN 'contact_phone' THEN EXISTS (SELECT 1 FROM contact_phone WHERE phone_id = NEW.entity_id)
      WHEN 'company_phone' THEN EXISTS (SELECT 1 FROM company_phone WHERE phone_id = NEW.entity_id)
      WHEN 'deal' THEN EXISTS (SELECT 1 FROM deal WHERE deal_id = NEW.entity_id)
      WHEN 'engagement' THEN EXISTS (SELECT 1 FROM engagement WHERE engagement_id = NEW.entity_id)
      WHEN 'tam_company_verdict' THEN EXISTS (SELECT 1 FROM tam_company_verdict WHERE verdict_id = NEW.entity_id)
      WHEN 'cost_ledger' THEN EXISTS (SELECT 1 FROM cost_ledger WHERE cost_id = NEW.entity_id)
      WHEN 'suppression' THEN EXISTS (SELECT 1 FROM suppression WHERE suppression_id = NEW.entity_id)
      WHEN 'lead_source' THEN EXISTS (SELECT 1 FROM lead_source WHERE lead_source_id = NEW.entity_id)
      WHEN 'vertical' THEN EXISTS (SELECT 1 FROM vertical WHERE vertical_id = NEW.entity_id)
      WHEN 'owner' THEN EXISTS (SELECT 1 FROM owner WHERE owner_id = NEW.entity_id)
      ELSE 0
    END;
END;
CREATE TRIGGER trg_origin_ref_entity_upd BEFORE UPDATE OF entity_type, entity_id ON origin_ref
BEGIN
  SELECT RAISE(ABORT, 'origin_ref: entity_type/entity_id does not reference an existing canonical row')
   WHERE NOT CASE NEW.entity_type
      WHEN 'company' THEN EXISTS (SELECT 1 FROM company WHERE company_id = NEW.entity_id)
      WHEN 'company_identifier' THEN EXISTS (SELECT 1 FROM company_identifier WHERE identifier_id = NEW.entity_id)
      WHEN 'company_account_link' THEN EXISTS (SELECT 1 FROM company_account_link WHERE link_id = NEW.entity_id)
      WHEN 'contact' THEN EXISTS (SELECT 1 FROM contact WHERE contact_id = NEW.entity_id)
      WHEN 'contact_phone' THEN EXISTS (SELECT 1 FROM contact_phone WHERE phone_id = NEW.entity_id)
      WHEN 'company_phone' THEN EXISTS (SELECT 1 FROM company_phone WHERE phone_id = NEW.entity_id)
      WHEN 'deal' THEN EXISTS (SELECT 1 FROM deal WHERE deal_id = NEW.entity_id)
      WHEN 'engagement' THEN EXISTS (SELECT 1 FROM engagement WHERE engagement_id = NEW.entity_id)
      WHEN 'tam_company_verdict' THEN EXISTS (SELECT 1 FROM tam_company_verdict WHERE verdict_id = NEW.entity_id)
      WHEN 'cost_ledger' THEN EXISTS (SELECT 1 FROM cost_ledger WHERE cost_id = NEW.entity_id)
      WHEN 'suppression' THEN EXISTS (SELECT 1 FROM suppression WHERE suppression_id = NEW.entity_id)
      WHEN 'lead_source' THEN EXISTS (SELECT 1 FROM lead_source WHERE lead_source_id = NEW.entity_id)
      WHEN 'vertical' THEN EXISTS (SELECT 1 FROM vertical WHERE vertical_id = NEW.entity_id)
      WHEN 'owner' THEN EXISTS (SELECT 1 FROM owner WHERE owner_id = NEW.entity_id)
      ELSE 0
    END;
END;
CREATE TRIGGER trg_company_origin_cleanup AFTER DELETE ON company
BEGIN
  DELETE FROM origin_ref WHERE entity_type = 'company' AND entity_id = OLD.company_id;
END;
CREATE TRIGGER trg_company_identifier_origin_cleanup AFTER DELETE ON company_identifier
BEGIN
  DELETE FROM origin_ref WHERE entity_type = 'company_identifier' AND entity_id = OLD.identifier_id;
END;
CREATE TRIGGER trg_company_account_link_origin_cleanup AFTER DELETE ON company_account_link
BEGIN
  DELETE FROM origin_ref WHERE entity_type = 'company_account_link' AND entity_id = OLD.link_id;
END;
CREATE TRIGGER trg_contact_origin_cleanup AFTER DELETE ON contact
BEGIN
  DELETE FROM origin_ref WHERE entity_type = 'contact' AND entity_id = OLD.contact_id;
END;
CREATE TRIGGER trg_contact_phone_origin_cleanup AFTER DELETE ON contact_phone
BEGIN
  DELETE FROM origin_ref WHERE entity_type = 'contact_phone' AND entity_id = OLD.phone_id;
END;
CREATE TRIGGER trg_company_phone_origin_cleanup AFTER DELETE ON company_phone
BEGIN
  DELETE FROM origin_ref WHERE entity_type = 'company_phone' AND entity_id = OLD.phone_id;
END;
CREATE TRIGGER trg_deal_origin_cleanup AFTER DELETE ON deal
BEGIN
  DELETE FROM origin_ref WHERE entity_type = 'deal' AND entity_id = OLD.deal_id;
END;
CREATE TRIGGER trg_engagement_origin_cleanup AFTER DELETE ON engagement
BEGIN
  DELETE FROM origin_ref WHERE entity_type = 'engagement' AND entity_id = OLD.engagement_id;
END;
CREATE TRIGGER trg_tam_company_verdict_origin_cleanup AFTER DELETE ON tam_company_verdict
BEGIN
  DELETE FROM origin_ref WHERE entity_type = 'tam_company_verdict' AND entity_id = OLD.verdict_id;
END;
CREATE TRIGGER trg_cost_ledger_origin_cleanup AFTER DELETE ON cost_ledger
BEGIN
  DELETE FROM origin_ref WHERE entity_type = 'cost_ledger' AND entity_id = OLD.cost_id;
END;
CREATE TRIGGER trg_suppression_origin_cleanup AFTER DELETE ON suppression
BEGIN
  DELETE FROM origin_ref WHERE entity_type = 'suppression' AND entity_id = OLD.suppression_id;
END;
CREATE TRIGGER trg_lead_source_origin_cleanup AFTER DELETE ON lead_source
BEGIN
  DELETE FROM origin_ref WHERE entity_type = 'lead_source' AND entity_id = OLD.lead_source_id;
END;
CREATE TRIGGER trg_vertical_origin_cleanup AFTER DELETE ON vertical
BEGIN
  DELETE FROM origin_ref WHERE entity_type = 'vertical' AND entity_id = OLD.vertical_id;
END;
CREATE TRIGGER trg_owner_origin_cleanup AFTER DELETE ON owner
BEGIN
  DELETE FROM origin_ref WHERE entity_type = 'owner' AND entity_id = OLD.owner_id;
END;

CREATE TRIGGER trg_merge_candidate_entity_ins BEFORE INSERT ON merge_candidate
BEGIN
  SELECT RAISE(ABORT, 'merge_candidate: left_id/right_id does not reference an existing row of entity_type')
   WHERE NOT CASE NEW.entity_type
      WHEN 'company' THEN (SELECT COUNT(*) FROM company WHERE company_id IN (NEW.left_id, NEW.right_id)) = 2
      WHEN 'contact' THEN (SELECT COUNT(*) FROM contact WHERE contact_id IN (NEW.left_id, NEW.right_id)) = 2
      ELSE 0
    END;
END;
CREATE TRIGGER trg_company_merge_cleanup AFTER DELETE ON company
BEGIN
  DELETE FROM merge_candidate WHERE entity_type = 'company' AND (left_id = OLD.company_id OR right_id = OLD.company_id);
END;
CREATE TRIGGER trg_contact_merge_cleanup AFTER DELETE ON contact
BEGIN
  DELETE FROM merge_candidate WHERE entity_type = 'contact' AND (left_id = OLD.contact_id OR right_id = OLD.contact_id);
END;

-- ---------------------------------------------------------------------------------------------
-- company merge integrity.  A merge chain A -> B -> A would make v_company_survivor return nothing for both; reject the cycle
-- when the pointer is written.
-- ---------------------------------------------------------------------------------------------
CREATE TRIGGER trg_company_merge_no_cycle BEFORE UPDATE OF merged_into_company_id ON company
WHEN NEW.merged_into_company_id IS NOT NULL
BEGIN
  SELECT RAISE(ABORT, 'company merge would create a cycle')
   WHERE EXISTS (WITH RECURSIVE up(id, hops) AS (
                   SELECT NEW.merged_into_company_id, 1
                   UNION ALL
                   SELECT c.merged_into_company_id, up.hops + 1 FROM up JOIN company c ON c.company_id = up.id
                    WHERE c.merged_into_company_id IS NOT NULL AND up.hops < 50)
                 SELECT 1 FROM up WHERE id = NEW.company_id);
END;

-- ---------------------------------------------------------------------------------------------
-- deal.company_id (golden) and the portal-level primary deal_company link are two paths to "the company of a deal"; they must
-- not disagree.  Checked whenever a primary link is written or deal.company_id changes.  (Changes to company_account_link.company_id
-- are not checked - the merge procedure moves links first, then deals - v_deal_company_drift reports any leftover disagreement.)
-- ---------------------------------------------------------------------------------------------
CREATE TRIGGER trg_deal_company_consistency_ins BEFORE INSERT ON deal_company
WHEN NEW.is_primary = 1
BEGIN
  SELECT RAISE(ABORT, 'deal_company: the primary link belongs to a different golden company than deal.company_id')
   WHERE (SELECT company_id FROM deal WHERE deal_id = NEW.deal_id) IS NOT NULL
     AND (SELECT company_id FROM deal WHERE deal_id = NEW.deal_id) <> (SELECT company_id FROM company_account_link WHERE link_id = NEW.link_id);
END;
CREATE TRIGGER trg_deal_company_consistency_upd BEFORE UPDATE ON deal_company
WHEN NEW.is_primary = 1
BEGIN
  SELECT RAISE(ABORT, 'deal_company: the primary link belongs to a different golden company than deal.company_id')
   WHERE (SELECT company_id FROM deal WHERE deal_id = NEW.deal_id) IS NOT NULL
     AND (SELECT company_id FROM deal WHERE deal_id = NEW.deal_id) <> (SELECT company_id FROM company_account_link WHERE link_id = NEW.link_id);
END;
CREATE TRIGGER trg_deal_company_consistency_deal BEFORE UPDATE OF company_id ON deal
WHEN NEW.company_id IS NOT NULL
BEGIN
  SELECT RAISE(ABORT, 'deal.company_id differs from the golden company of the primary deal_company link')
   WHERE EXISTS (SELECT 1 FROM deal_company dc JOIN company_account_link l ON l.link_id = dc.link_id
                  WHERE dc.deal_id = NEW.deal_id AND dc.is_primary = 1 AND l.company_id <> NEW.company_id);
END;
