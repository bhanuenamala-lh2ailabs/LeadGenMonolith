-- 0020_owner_stage_snapshot.sql
-- Daily snapshot of open deals per owner and stage, taken from the synced HubSpot data, so "who was at which stage at the start of a day"
-- is a table read rather than a per-deal history crawl.  One row per (snapshot_day, account, pipeline, owner, stage).
-- Written by tools/owner_stage_snapshot.py after each sync.  Counts only; no deal or contact details.
CREATE TABLE ext_owner_stage_snapshot (
  snapshot_day   TEXT    NOT NULL CHECK (snapshot_day GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),  -- IST day the snapshot describes (taken at the day's end)
  account_id     TEXT    NOT NULL,
  pipeline_id    TEXT    NOT NULL,
  owner_id       TEXT    NOT NULL,
  stage_id       TEXT    NOT NULL,
  deals          INTEGER NOT NULL CHECK (deals >= 0),
  taken_at       TEXT    NOT NULL CHECK (taken_at IS strftime('%Y-%m-%dT%H:%M:%fZ', taken_at)),
  PRIMARY KEY (snapshot_day, account_id, pipeline_id, owner_id, stage_id)
) STRICT;
