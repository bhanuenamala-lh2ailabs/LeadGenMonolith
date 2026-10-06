-- 0003_hsraw.sql
-- HubSpot raw layer: the full API JSON of every HubSpot object, so the canonical layer can be rebuilt
-- without re-pulling (a 6k-deal history pull takes ~10 minutes and per-deal history cannot always be
-- reproduced later).  Written ONLY by the read-only HubSpot sync; nothing here is ever sent back to HubSpot.
--
--   hsraw_object           one row per (account, object type, HubSpot id): the CURRENT payload
--   hsraw_object_revision  every PREVIOUS payload, filled automatically by a trigger when the payload changes
--
-- payload_sha256 CONTRACT: sha256 of the canonical JSON of (properties, associations, history) TOGETHER, so a refresh of history_json
-- alone still changes the hash and therefore archives the previous payload in hsraw_object_revision.
--
-- Rebuild bookkeeping: canonical_payload_sha256 is the payload hash the canonical layer was last built from;
-- a row is "dirty" (needs re-projection) while payload_sha256 IS NOT canonical_payload_sha256 - see the
-- partial index ix_hsraw_dirty and the importer's incremental rebuild.

CREATE TABLE hsraw_object (
  raw_id                  INTEGER PRIMARY KEY,  -- surrogate key
  account_id              TEXT    NOT NULL REFERENCES account(account_id),
  object_type             TEXT    NOT NULL  -- deals / contacts / companies / notes / tasks / calls / meetings / emails / owners / pipelines / properties / users
                                  CHECK (object_type IN ('deals','contacts','companies','notes','tasks','calls','meetings','emails',
                                                         'owners','pipelines','properties','users')),
  hs_id                   TEXT    NOT NULL CHECK (hs_id <> '' AND hs_id = trim(hs_id)),   -- numeric for CRM objects, 'default'/numeric for pipelines
  properties_json         TEXT    NOT NULL CHECK (json_valid(properties_json) AND json_type(properties_json) = 'object'),
  associations_json       TEXT    CHECK (associations_json IS NULL OR json_valid(associations_json)),
  history_json            TEXT    CHECK (history_json IS NULL OR json_valid(history_json)),     -- propertiesWithHistory (deals: dealstage, hubspot_owner_id, pipeline).  `pipeline` is REQUIRED: a dealstage history entry carries only the stage id, and three ids are shared by the two MAIN pipelines, so deal_stage_event.pipeline_id is resolved from the pipeline history in effect at each entered_at
  history_fetched_at      TEXT    CHECK (history_fetched_at IS NULL OR (history_fetched_at IS strftime('%Y-%m-%dT%H:%M:%fZ', history_fetched_at) AND history_fetched_at NOT GLOB '*T24:*')),
  history_error           TEXT,                                                                  -- surfaced in the report footer (history_failures)
  is_archived             INTEGER NOT NULL DEFAULT 0 CHECK (is_archived IN (0, 1)),
  hs_created_at           TEXT    CHECK (hs_created_at IS NULL OR (hs_created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', hs_created_at) AND hs_created_at NOT GLOB '*T24:*')),
  hs_updated_at           TEXT    CHECK (hs_updated_at IS NULL OR (hs_updated_at IS strftime('%Y-%m-%dT%H:%M:%fZ', hs_updated_at) AND hs_updated_at NOT GLOB '*T24:*')),
  fetched_at              TEXT    NOT NULL CHECK (fetched_at IS strftime('%Y-%m-%dT%H:%M:%fZ', fetched_at) AND fetched_at NOT GLOB '*T24:*'),
  payload_sha256          TEXT    NOT NULL CHECK (length(payload_sha256) = 64 AND payload_sha256 NOT GLOB '*[^0-9a-f]*'),
  canonical_payload_sha256 TEXT   CHECK (canonical_payload_sha256 IS NULL OR length(canonical_payload_sha256) = 64),
  canonical_applied_at    TEXT    CHECK (canonical_applied_at IS NULL OR (canonical_applied_at IS strftime('%Y-%m-%dT%H:%M:%fZ', canonical_applied_at) AND canonical_applied_at NOT GLOB '*T24:*')),
  import_run_id           INTEGER REFERENCES ops_import_run(import_run_id) ON DELETE SET NULL,
  created_at              TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at) AND created_at NOT GLOB '*T24:*'),
  UNIQUE (account_id, object_type, hs_id),
  CHECK (history_fetched_at IS NOT NULL OR history_json IS NULL)
) STRICT;
-- incremental canonical rebuild / incremental sync cursor lookups
CREATE INDEX ix_hsraw_updated ON hsraw_object(account_id, object_type, hs_updated_at);
CREATE INDEX ix_hsraw_dirty   ON hsraw_object(account_id, object_type) WHERE canonical_payload_sha256 IS NOT payload_sha256;
-- deals whose history failed or was never read
CREATE INDEX ix_hsraw_history_gap ON hsraw_object(account_id) WHERE object_type = 'deals' AND (history_fetched_at IS NULL OR history_error IS NOT NULL);
CREATE INDEX ix_hsraw_run ON hsraw_object(import_run_id) WHERE import_run_id IS NOT NULL;

CREATE TABLE hsraw_object_revision (
  revision_id      INTEGER PRIMARY KEY,  -- surrogate key
  raw_id           INTEGER NOT NULL REFERENCES hsraw_object(raw_id) ON DELETE CASCADE,  -- the object whose previous payload this is
  payload_sha256   TEXT    NOT NULL CHECK (length(payload_sha256) = 64),
  properties_json  TEXT    NOT NULL CHECK (json_valid(properties_json)),
  associations_json TEXT   CHECK (associations_json IS NULL OR json_valid(associations_json)),
  history_json     TEXT    CHECK (history_json IS NULL OR json_valid(history_json)),
  fetched_at       TEXT    NOT NULL CHECK (fetched_at IS strftime('%Y-%m-%dT%H:%M:%fZ', fetched_at) AND fetched_at NOT GLOB '*T24:*'),
  superseded_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (superseded_at IS strftime('%Y-%m-%dT%H:%M:%fZ', superseded_at) AND superseded_at NOT GLOB '*T24:*'),
  import_run_id    INTEGER REFERENCES ops_import_run(import_run_id) ON DELETE SET NULL,
  UNIQUE (raw_id, payload_sha256)
) STRICT;
CREATE INDEX ix_hsraw_revision_raw ON hsraw_object_revision(raw_id, fetched_at);

-- Keep the previous payload whenever the hash changes.  (Retention / pruning is an application decision.)
CREATE TRIGGER trg_hsraw_object_revision AFTER UPDATE OF payload_sha256 ON hsraw_object
FOR EACH ROW WHEN OLD.payload_sha256 <> NEW.payload_sha256
BEGIN
  INSERT OR IGNORE INTO hsraw_object_revision (raw_id, payload_sha256, properties_json, associations_json, history_json, fetched_at, import_run_id)
  VALUES (OLD.raw_id, OLD.payload_sha256, OLD.properties_json, OLD.associations_json, OLD.history_json, OLD.fetched_at, OLD.import_run_id);
END;
