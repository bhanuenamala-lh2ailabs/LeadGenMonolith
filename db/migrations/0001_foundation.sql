-- 0001_foundation.sql
-- Foundation layer: migration ledger, account registry, operations tables, provenance.
--
-- Conventions used by EVERY migration (see docs/ARCHITECTURE.md section 1):
--   * Timestamps are ISO-8601 UTC text in ONE canonical shape: exactly 24 characters, millisecond precision,
--     'Z' suffix ("2026-10-04T12:34:56.789Z").  They are validated with
--       (col IS strftime('%Y-%m-%dT%H:%M:%fZ', col) AND col NOT GLOB '*T24:*')
--     i.e. the value must survive a round trip through SQLite's own date parser, which rejects impossible
--     dates, lower-case t/z, offsets, junk, missing or extra fractional digits (so text order == time order).
--     Importers pad second-precision sources to '.000Z'.  Defaults use strftime('%Y-%m-%dT%H:%M:%fZ','now').
--     IST is derived at report time (generated ist_day columns), never stored as the primary value.
--   * Calendar dates (no time) are 'YYYY-MM-DD' validated with  (length(col) = 10 AND date(col) IS col).
--   * Booleans are INTEGER 0/1 with CHECK (col IN (0,1)).
--   * *_json columns are TEXT guarded by CHECK (json_valid(col)).
--   * Canonical-layer tables are STRICT.  Money is USD REAL with an explicit basis column.
--   * Every key that HubSpot hands out is scoped by account_id (our slug: main / companyops / rat).
--   * Each file is applied exactly once, inside one transaction, by leadgen.db.migrate().  DDL is written
--     WITHOUT "IF NOT EXISTS" on purpose: if an object of that name already exists (a hand-made table, a
--     half-restored database) the migration fails loudly instead of silently keeping a divergent definition.
--     leadgen.db.doctor() additionally compares sqlite_master against a freshly migrated in-memory schema.
--     An applied migration is NEVER edited (its sha256 is stored in schema_migration);
--     to change the schema add the next-numbered file.  A migration that has to REBUILD a table (SQLite cannot
--     alter a CHECK / FK / generated column) starts with the line  "-- migrate: foreign_keys=off"  - see leadgen.db.
--   * Surrogate keys of entities that other tables reference polymorphically (origin_ref) or that reports quote
--     (company, contact, deal, verdict, audit rows) are AUTOINCREMENT so a deleted id is never handed out again.
--   * History is never cascaded away: foreign keys from event tables to their parent are ON DELETE RESTRICT.
--     Never write INSERT OR REPLACE / REPLACE INTO (it is a DELETE + INSERT that fires cascades); use
--     INSERT ... ON CONFLICT DO UPDATE.  tests/test_schema.py greps leadgen/ for the idiom.

-- ---------------------------------------------------------------------------------------------
-- schema_migration: ledger of applied migration files (written by the runner, not by this file).
-- ---------------------------------------------------------------------------------------------
CREATE TABLE schema_migration (
  version         INTEGER PRIMARY KEY CHECK (version > 0),  -- migration number (also PRAGMA user_version)
  name            TEXT    NOT NULL CHECK (name <> ''),
  checksum_sha256 TEXT    NOT NULL CHECK (length(checksum_sha256) = 64 AND checksum_sha256 NOT GLOB '*[^0-9a-f]*'),
  applied_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (applied_at IS strftime('%Y-%m-%dT%H:%M:%fZ', applied_at) AND applied_at NOT GLOB '*T24:*'),
  execution_ms    INTEGER CHECK (execution_ms IS NULL OR execution_ms >= 0)  -- how long it took
) STRICT;

-- ---------------------------------------------------------------------------------------------
-- account: one row per HubSpot portal.  account_id is our slug and the scoping key everywhere.
-- Lives in the foundation layer (not in 0002) because ops_* tables reference it.
-- env_key is the NAME of the env var holding the private-app token; a CHECK keeps token-looking
-- values (lowercase, dashes, ...) out of this column.  Secrets never live in the database.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE account (
  account_id        TEXT    PRIMARY KEY
                            CHECK (length(account_id) BETWEEN 2 AND 32 AND account_id NOT GLOB '*[^a-z0-9_]*' AND account_id GLOB '[a-z]*'),
  portal_id         INTEGER NOT NULL UNIQUE CHECK (portal_id > 0),  -- HubSpot portal (hub) id
  name              TEXT    NOT NULL CHECK (name <> ''),
  env_key           TEXT    NOT NULL UNIQUE CHECK (env_key NOT GLOB '*[^A-Z0-9_]*' AND env_key GLOB '[A-Z]*'),  -- NAME of the env var holding the private-app token (never the token)
  hubspot_timezone  TEXT    NOT NULL CHECK (hubspot_timezone <> ''),  -- informational; reports use IST
  ui_domain         TEXT,  -- HubSpot UI host for deep links
  currency          TEXT    NOT NULL DEFAULT 'USD' CHECK (length(currency) = 3 AND currency = upper(currency)),
  is_active         INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
  created_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at) AND created_at NOT GLOB '*T24:*'),
  updated_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (updated_at IS strftime('%Y-%m-%dT%H:%M:%fZ', updated_at) AND updated_at NOT GLOB '*T24:*')
) STRICT;

INSERT INTO account (account_id, portal_id, name, env_key, hubspot_timezone, ui_domain, currency) VALUES
  ('main',       246754894, 'MAIN (codebase acquisition)',        'HUBSPOT_KEY_MAIN',       'Asia/Calcutta', 'app-na2.hubspot.com', 'USD'),
  ('companyops', 246897735, 'COMPANYOPS (ops-data build portal)', 'HUBSPOT_KEY_COMPANYOPS', 'US/Eastern',    'app-na2.hubspot.com', 'USD'),
  ('rat',        247485022, 'RAT (Rapid Action Team)',            'HUBSPOT_KEY_RAT',        'US/Eastern',    'app-na2.hubspot.com', 'USD');

-- ---------------------------------------------------------------------------------------------
-- ops_import_run: one row per importer / sync / pull / report execution.  Every legacy_* row
-- (_import_run_id), hsraw_* row, deal_stage_event and origin_ref row can be traced to the run that
-- wrote it, which makes a bad import reversible (DELETE ... WHERE _import_run_id = ?).
-- ---------------------------------------------------------------------------------------------
CREATE TABLE ops_import_run (
  import_run_id       INTEGER PRIMARY KEY,
  kind                TEXT    NOT NULL  -- legacy_import / hubspot_sync / gsheets_pull / gmail_pull / snapshot_import / report / seed / maintenance / other
                              CHECK (kind IN ('legacy_import','hubspot_sync','gsheets_pull','gmail_pull','snapshot_import','report','seed','maintenance','other')),
  source_name         TEXT    NOT NULL CHECK (source_name <> ''),   -- e.g. 'tam', 'hubspot:main:deals', 'gmail:bhanu'
  source_path         TEXT,                                          -- file/dir read (relative to repo root when possible)
  source_fingerprint  TEXT,                                          -- sha256 / size+mtime of the input, for "already imported?" checks
  account_id          TEXT    REFERENCES account(account_id),        -- set for HubSpot-scoped runs
  status              TEXT    NOT NULL DEFAULT 'running'
                              CHECK (status IN ('running','succeeded','partial','failed','aborted')),
  started_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (started_at IS strftime('%Y-%m-%dT%H:%M:%fZ', started_at) AND started_at NOT GLOB '*T24:*'),
  finished_at         TEXT    CHECK (finished_at IS NULL OR (finished_at IS strftime('%Y-%m-%dT%H:%M:%fZ', finished_at) AND finished_at NOT GLOB '*T24:*')),
  rows_read           INTEGER CHECK (rows_read     IS NULL OR rows_read     >= 0),  -- rows read from the source
  rows_written        INTEGER CHECK (rows_written  IS NULL OR rows_written  >= 0),  -- rows inserted / updated
  rows_skipped        INTEGER CHECK (rows_skipped  IS NULL OR rows_skipped  >= 0),  -- rows skipped as unchanged or filtered
  rows_rejected       INTEGER CHECK (rows_rejected IS NULL OR rows_rejected >= 0),  -- rows rejected (also reported to ops_dq_issue)
  error               TEXT,  -- error text of a failed run
  params_json         TEXT    NOT NULL DEFAULT '{}' CHECK (json_valid(params_json) AND json_type(params_json) = 'object'),
  tool_version        TEXT,  -- version of the tool that ran
  CHECK (finished_at IS NULL OR finished_at >= started_at),
  CHECK ((status = 'running') = (finished_at IS NULL))          -- a finished run has an end time; a running one does not
) STRICT;
CREATE INDEX ix_import_run_kind   ON ops_import_run(kind, source_name, started_at);
CREATE INDEX ix_import_run_status ON ops_import_run(status, started_at);

-- ---------------------------------------------------------------------------------------------
-- ops_sync_state: resumable cursors.  One row per (source system, scope, object type, cursor name),
-- e.g. ('hubspot','main','deals','hs_lastmodifieddate') or ('gmail','bhanu.enamala@lh2.ai','messages','historyId').
-- cursor_value is opaque to the database.  HubSpot incremental sync re-reads in full the history of
-- every deal with hs_lastmodifieddate >= cursor - 24h (docs/discovery/funnel-reports.md 4.1).
-- ---------------------------------------------------------------------------------------------
CREATE TABLE ops_sync_state (
  source_system     TEXT    NOT NULL CHECK (source_system IN ('hubspot','gsheets','gmail','other')),
  scope             TEXT    NOT NULL CHECK (scope <> ''),   -- account slug for HubSpot, mailbox / drive account otherwise
  object_type       TEXT    NOT NULL CHECK (object_type <> ''),  -- object synced (deals, contacts, messages ...)
  cursor_name       TEXT    NOT NULL DEFAULT 'default' CHECK (cursor_name <> ''),  -- which cursor (hs_lastmodifieddate, historyId ...)
  account_id        TEXT    REFERENCES account(account_id),
  cursor_value      TEXT,  -- opaque cursor value
  last_attempt_at   TEXT    CHECK (last_attempt_at IS NULL OR (last_attempt_at IS strftime('%Y-%m-%dT%H:%M:%fZ', last_attempt_at) AND last_attempt_at NOT GLOB '*T24:*')),
  last_success_at   TEXT    CHECK (last_success_at IS NULL OR (last_success_at IS strftime('%Y-%m-%dT%H:%M:%fZ', last_success_at) AND last_success_at NOT GLOB '*T24:*')),
  last_status       TEXT    NOT NULL DEFAULT 'never' CHECK (last_status IN ('never','running','ok','error')),  -- never / running / ok / error
  last_error        TEXT,  -- error text of the last failed attempt
  rows_synced       INTEGER NOT NULL DEFAULT 0 CHECK (rows_synced >= 0),  -- rows synced by the last successful run
  import_run_id     INTEGER REFERENCES ops_import_run(import_run_id) ON DELETE SET NULL,
  updated_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (updated_at IS strftime('%Y-%m-%dT%H:%M:%fZ', updated_at) AND updated_at NOT GLOB '*T24:*'),
  PRIMARY KEY (source_system, scope, object_type, cursor_name),
  CHECK (source_system <> 'hubspot' OR (account_id IS NOT NULL AND scope = account_id)),
  CHECK (last_status <> 'ok' OR last_success_at IS NOT NULL)
) STRICT;

-- ---------------------------------------------------------------------------------------------
-- ops_dq_issue: data-quality findings (unmapped stage, placeholder row skipped, bad phone, domain
-- shared by several companies, ...).  fingerprint makes detectors idempotent: UPSERT on it instead of
-- inserting the same finding every run.  Rows are resolved, never deleted.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE ops_dq_issue (
  dq_issue_id    INTEGER PRIMARY KEY,  -- surrogate key
  fingerprint    TEXT    NOT NULL UNIQUE CHECK (fingerprint <> ''),     -- e.g. 'unmapped_stage|main|default|4018854633'
  rule_code      TEXT    NOT NULL CHECK (rule_code <> ''),  -- detector that raised it (unmapped_stage, legacy_fk_orphan, ambiguous_stage_pipeline ...)
  severity       TEXT    NOT NULL CHECK (severity IN ('info','warn','error')),  -- info / warn / error
  account_id     TEXT    REFERENCES account(account_id),
  entity_type    TEXT,  -- kind of entity concerned
  entity_ref     TEXT,                                                   -- canonical id or source key, free text
  message        TEXT    NOT NULL CHECK (message <> ''),  -- human-readable finding
  details_json   TEXT    NOT NULL DEFAULT '{}' CHECK (json_valid(details_json)),
  status         TEXT    NOT NULL DEFAULT 'open' CHECK (status IN ('open','acknowledged','resolved','wontfix')),
  occurrences    INTEGER NOT NULL DEFAULT 1 CHECK (occurrences >= 1),  -- how often the detector saw it (upsert on fingerprint)
  first_seen_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (first_seen_at IS strftime('%Y-%m-%dT%H:%M:%fZ', first_seen_at) AND first_seen_at NOT GLOB '*T24:*'),
  last_seen_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (last_seen_at IS strftime('%Y-%m-%dT%H:%M:%fZ', last_seen_at) AND last_seen_at NOT GLOB '*T24:*'),
  resolved_at    TEXT    CHECK (resolved_at IS NULL OR (resolved_at IS strftime('%Y-%m-%dT%H:%M:%fZ', resolved_at) AND resolved_at NOT GLOB '*T24:*')),
  import_run_id  INTEGER REFERENCES ops_import_run(import_run_id) ON DELETE SET NULL,
  CHECK ((status IN ('resolved','wontfix')) = (resolved_at IS NOT NULL))
) STRICT;
CREATE INDEX ix_dq_issue_open ON ops_dq_issue(status, severity, rule_code);
CREATE INDEX ix_dq_issue_entity ON ops_dq_issue(entity_type, entity_ref);

-- ---------------------------------------------------------------------------------------------
-- ops_audit_log: append-only trail of consequential actions (imports committed, suppression changes,
-- manual merges, report mails sent / dry-run).  Triggers below make UPDATE and DELETE fail.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE ops_audit_log (
  audit_id       INTEGER PRIMARY KEY AUTOINCREMENT,  -- surrogate key (never reused; the table is append-only)
  at             TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (at IS strftime('%Y-%m-%dT%H:%M:%fZ', at) AND at NOT GLOB '*T24:*'),  -- when (UTC)
  actor          TEXT    NOT NULL CHECK (actor <> ''),           -- 'cli', 'sync:hubspot', 'user:bhanu.enamala@lh2.ai'
  action         TEXT    NOT NULL CHECK (action <> ''),          -- 'suppression.add', 'merge.accept', 'report.mail.dry_run'
  entity_type    TEXT,  -- kind of entity acted on
  entity_ref     TEXT,  -- id / key of the entity acted on
  account_id     TEXT    REFERENCES account(account_id),
  before_json    TEXT    CHECK (before_json IS NULL OR json_valid(before_json)),
  after_json     TEXT    CHECK (after_json  IS NULL OR json_valid(after_json)),
  note           TEXT,
  import_run_id  INTEGER REFERENCES ops_import_run(import_run_id) ON DELETE SET NULL
) STRICT;
CREATE INDEX ix_audit_at     ON ops_audit_log(at);
CREATE INDEX ix_audit_entity ON ops_audit_log(entity_type, entity_ref);

CREATE TRIGGER trg_audit_no_update BEFORE UPDATE ON ops_audit_log
BEGIN
  SELECT RAISE(ABORT, 'ops_audit_log is append-only');
END;
CREATE TRIGGER trg_audit_no_delete BEFORE DELETE ON ops_audit_log
BEGIN
  SELECT RAISE(ABORT, 'ops_audit_log is append-only');
END;

-- ---------------------------------------------------------------------------------------------
-- origin_ref: provenance for every canonical row.  (entity_type, entity_id) is the canonical row;
-- (source_system, source_table, source_pk) is the legacy / external row it came from.  A canonical
-- row may have many origins (a company merged from tam + itsvc + corpus has three); a source row may
-- feed several canonical entities (one tam company -> company + company_identifier rows).
-- entity_id is polymorphic, so referential integrity is enforced by triggers created in
-- 0002_canonical.sql (insert is validated; deleting a canonical row removes its origin_ref rows).
-- source_system examples: 'tam', 'itsvc', 'corpus', 'pipeline', 'radar', 'resolver', 'gmaps',
-- 'searchledger', 'hubspot:main', 'hubspot:companyops', 'hubspot:rat', 'csv:whales.csv', 'gsheet:<id>'.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE origin_ref (
  origin_ref_id   INTEGER PRIMARY KEY,  -- surrogate key
  entity_type     TEXT    NOT NULL  -- which canonical table entity_id points into (polymorphic: validated by triggers)
                          CHECK (entity_type IN ('company','company_identifier','company_account_link','contact','contact_phone','deal',
                                                 'engagement','tam_company_verdict','cost_ledger','suppression','lead_source','vertical','owner',
                                                 'company_phone')),
  entity_id       INTEGER NOT NULL,  -- primary key of the canonical row
  source_system   TEXT    NOT NULL CHECK (source_system <> ''),
  source_table    TEXT    NOT NULL CHECK (source_table <> ''),  -- the ORIGINAL table name ('companies'), not the legacy_<db>_ prefixed one
  source_pk       TEXT    NOT NULL CHECK (source_pk <> ''),   -- composite keys joined with '|'; PK-less legacy tables use their rowid
  match_method    TEXT,                                        -- 'root_domain', 'linkedin_company', 'cin', 'hs_id', 'new', ...
  confidence      REAL    CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
  import_run_id   INTEGER REFERENCES ops_import_run(import_run_id) ON DELETE SET NULL,
  created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at) AND created_at NOT GLOB '*T24:*'),
  UNIQUE (entity_type, entity_id, source_system, source_table, source_pk)
) STRICT;
-- reverse lookup: "which canonical rows did this legacy row become?" (idempotent re-imports use this)
CREATE INDEX ix_origin_ref_source ON origin_ref(source_system, source_table, source_pk);
-- a legacy row feeds ONE canonical row for the 1:1 entity types (a second binding would defeat idempotent re-imports).
-- company_identifier / contact_phone / company_phone legitimately fan out from one source row, so they are excluded.
CREATE UNIQUE INDEX ux_origin_one_to_one ON origin_ref(entity_type, source_system, source_table, source_pk)
  WHERE entity_type IN ('company','contact','deal','engagement','owner','tam_company_verdict','cost_ledger','suppression','company_account_link');

-- ---------------------------------------------------------------------------------------------
-- updated_at maintenance (one trigger per mutable table).  The guard has two halves:
--   NEW.updated_at = OLD.updated_at   the writer did not set updated_at itself (a writer may set it explicitly, e.g. to mirror
--                                     HubSpot's lastmodified)
--   OLD.updated_at <> now             stops the trigger re-firing on its own UPDATE when recursive_triggers=ON (connect() sets it,
--                                     so INSERT OR REPLACE fires delete triggers).  Bulk upserts should set updated_at
--                                     explicitly (... DO UPDATE SET ..., updated_at = excluded.updated_at) to skip the 2nd UPDATE.
-- ---------------------------------------------------------------------------------------------
CREATE TRIGGER trg_account_touch AFTER UPDATE ON account
FOR EACH ROW WHEN NEW.updated_at = OLD.updated_at AND OLD.updated_at <> strftime('%Y-%m-%dT%H:%M:%fZ','now')
BEGIN
  UPDATE account SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE rowid = NEW.rowid;
END;
CREATE TRIGGER trg_ops_sync_state_touch AFTER UPDATE ON ops_sync_state
FOR EACH ROW WHEN NEW.updated_at = OLD.updated_at AND OLD.updated_at <> strftime('%Y-%m-%dT%H:%M:%fZ','now')
BEGIN
  UPDATE ops_sync_state SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE rowid = NEW.rowid;
END;
