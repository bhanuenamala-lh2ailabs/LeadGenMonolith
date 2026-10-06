# Schema review - Critic A (production quality and canonical model)

Scope: `db/migrations/0001..0005` (foundation, canonical, hsraw, rpt, ext), `leadgen/db.py`, `tests/test_schema.py`.
Legacy mirrors (0006-0014) only sampled; they belong to the legacy critic.

Method: migrated throwaway DBs under `db/_scratch/` (since deleted), loaded a realistic world (3 accounts, 5 reporting pipelines plus the RAT stock one,
stage ids reused across the two MAIN pipelines, a deleted LinkedIn stage resolved only via `stage_alias`, an archived deal, a deal that went
dead -> live -> dead, the same company in `main` and `rat`, a US mobile number), then a 60k-deal / 220k-event synthetic set for `EXPLAIN QUERY PLAN`
and timings. Every claim below marked **[verified]** was reproduced; every SQL fix marked **[fix verified]** was applied to a patched copy of
the migrations (all 14 apply cleanly) and re-probed. The 66 existing tests pass today, and none of the defects below are caught by them.
The real `db/leadgen.sqlite` was never created or touched.

**The single most important point:** `db/leadgen.sqlite` does not exist yet, so nothing is "applied". Fix H1-H6, M1-M6 by editing 0001-0005 **now**.
After the first real `migrate`, SQLite cannot alter a CHECK or a generated column, so every one of these becomes a table-rebuild migration, and
(see H2) the runner cannot rebuild a parent table safely.

What is solid (verified, no action): composite FKs really block cross-portal references (deal/stage/owner/pipeline); a stage id shared by two MAIN
pipelines never leaks across them in any view; IST day boundary (`18:45Z` -> next IST day) is right; dead->live->dead gives 2 entries / 1 distinct
deal; archived deals leave occupancy but keep history; a failed migration rolls back atomically including `user_version`; 4 concurrent
first-time `migrate()` processes serialise correctly; `PRAGMA` set is applied on every connection; the audit-log UPDATE/DELETE triggers work.

Ranking: **H** = wrong numbers / data loss / safety-gate bypass; **M** = real hazard, needs a decision; **L** = hygiene.

---------------------------------------------------------------------------------------------------------------------------------

## H1. Timestamp CHECK `LIKE '____-__-__T__:__:__%Z'` validates nothing; invalid values give `ist_day = NULL` silently  [verified]

114 column checks use it (14 + 64 + 8 + 8 + 20 across 0001-0005). `LIKE` is case-insensitive and `%` matches anything. Accepted into `deal_stage_event.entered_at`:

| value | stored? | `ist_day` |
|---|---|---|
| `2026-13-45T99:99:99Z` | yes | NULL |
| `2026-10-04t12:00:00z` (lowercase) | yes | NULL |
| `2026-10-04T12:00:00+05:30Z` | yes | NULL |
| `2026-10-04T12:00:00 garbage Z` | yes | NULL |
| `2026-10-04T00:00:00Z` (no ms) next to `...00.000Z` | yes | `2026-10-04` |
| `2026-10-04T00:00:00.123456789Z` | yes | `2026-10-04` |

Consequences: (a) an event with a malformed time is stored, has a NULL generated `ist_day`, and falls out of every `GROUP BY ist_day` report with no
error; (b) mixed precision breaks ordering/range queries because `.` (0x2E) sorts before `Z` (0x5A): `00:00:00.000Z < 00:00:00Z`, so
`entered_at >= '..T00:00:00.000Z'` includes/excludes the wrong second; my probe returned 6 of 7 rows including 4 garbage rows in an `entered_at` day range;
(c) behaviour changes with `PRAGMA case_sensitive_like` (a per-connection setting, so the CHECK is not even deterministic across writers).

Fix (one canonical format: 24 chars, millisecond, `Z`; importers pad second-precision sources to `.000Z`). **[fix verified]** - rejects all rows above except the valid one, and `2026-02-30` too:

```sql
-- replace every   <col> LIKE '____-__-__T__:__:__%Z'   with:
(<col> GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9].[0-9][0-9][0-9]Z'
 AND strftime('%Y-%m-%dT%H:%M:%fZ', <col>) = <col>)
```

A one-line regex over the five files does it (`(\b[a-z_]+\b) LIKE '____-__-__T__:__:__%Z'`). Test fixtures then need `.000Z` literals (most already have them).
Add a regression test: insert `'2026-13-45T99:99:99Z'`, `'...t...z'`, `'...Z'` without ms - all must raise `IntegrityError`.

Two sibling bugs of the same family (`date(x) = x` yields NULL on garbage, and a NULL CHECK passes) **[verified]**: `stage_alias.valid_to`
(0002:117) accepted `'2026-99-99'` and `'abcdefghij'`; `rpt_report_run.window_end` (0004:21) accepted `'abcdefghij'`. The test named
`test_date_columns_reject_non_dates_instead_of_passing_on_null` only covers the columns that already use `IS`. Fix: `date(valid_to) IS valid_to`,
`date(window_end) IS window_end` **[fix verified]**.

## H2. The event store can be destroyed by an upsert, a delete, or a future migration  [verified]

`deal_stage_event`, `deal_owner_event`, `rpt_engaged_daily`, `deal_contact`, `deal_company`, `engagement_assoc` all hang off `deal` with `ON DELETE CASCADE`.
The architecture says "any past day can be recomputed" from `deal_stage_event`; one of these paths silently erases that:

1. `INSERT OR REPLACE INTO deal (...)` (the idiom a sync loop reaches for): events 1 -> 0. REPLACE deletes the parent row and the FK cascade fires.
   (`ON CONFLICT DO UPDATE` is safe.) Also: `DELETE FROM deal WHERE deal_id=?` -> events 1 -> 0.
2. **Migration runner + rebuild**: `_apply_one` runs inside `BEGIN IMMEDIATE` with `foreign_keys=ON`; `PRAGMA foreign_keys=OFF` is a no-op inside a
   transaction. The only way to change a CHECK/FK/generated column in SQLite is create-new/copy/`DROP TABLE`/rename. Under FK=ON `DROP TABLE deal`
   performs an implicit DELETE that cascades: events 2 -> 0 (verified inside a rolled-back txn). So the first schema fix after go-live
   is a data-loss event, and the runner offers no sanctioned way to do it.
3. Same REPLACE problem elsewhere because `recursive_triggers` is OFF: `INSERT OR REPLACE INTO company` left `origin_ref` orphaned and cascaded away
   `company_identifier` (identifiers 1 -> 0, origin_ref stayed 1); `INSERT OR REPLACE INTO ops_audit_log (audit_id,...)` **rewrote an audit row**
   (the append-only triggers do not fire for REPLACE); `INSERT OR REPLACE INTO stage` cascaded away `stage_map` (and `stage_alias`) so the stage turns
   "unmapped".

Fixes:

```sql
-- (a) history must never cascade from a deal; deals are archived (is_archived), not deleted.   [fix verified: REPLACE and DELETE now raise]
FOREIGN KEY (deal_id, account_id) REFERENCES deal(deal_id, account_id) ON DELETE RESTRICT   -- deal_stage_event, deal_owner_event
-- keep CASCADE only for pure association rows (deal_contact/deal_company/engagement_assoc) and drop it for rpt_engaged_daily -> deal/owner.

-- (b) connection pragma (db.PRAGMAS):  ("recursive_triggers", "ON")   so REPLACE fires delete triggers; [fix verified: audit REPLACE now raises]
--     BUT the 20 *_touch triggers recurse infinitely with it on (147 of 300 rapid updates failed "too many levels of trigger recursion").
--     Change every touch trigger's guard at the same time:
CREATE TRIGGER trg_deal_touch AFTER UPDATE ON deal FOR EACH ROW
WHEN NEW.updated_at = OLD.updated_at AND OLD.updated_at <> strftime('%Y-%m-%dT%H:%M:%fZ','now')
BEGIN UPDATE deal SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE rowid = NEW.rowid; END;   -- [fix verified, 300/300 ok]
```

(c) Runner: support a rebuild protocol. A migration file whose first line is `-- migrate: foreign_keys=off` is applied by the runner as:
`PRAGMA foreign_keys=OFF` (outside any txn) -> `BEGIN IMMEDIATE` -> statements -> `PRAGMA foreign_key_check` (abort + rollback if any row) -> `COMMIT` ->
`PRAGMA foreign_keys=ON`. Also run `PRAGMA foreign_key_check` after *every* migration, not only in `doctor`. Add a test that a rebuild migration of
`deal` preserves `deal_stage_event`.
(d) Add a test that greps `leadgen/` for `INSERT OR REPLACE` / `REPLACE INTO` and fails (cheap, catches the idiom before it ships).

## H3. Deleted/aliased stages are not wired into any view: the LinkedIn case lands in `UNMAPPED`  [verified]

`stage_alias` is referenced by **zero** views (`sqlite_master` check). The model offers two overlapping answers for the deleted ids
(`4080987861`, `4132224744`, ~3,000 deals): a tombstone `stage` row + its own `stage_map` row, or a `stage_alias` pointing at the successor. The
author's own test (`test_stage_alias_tombstones_and_history_only_stages`) creates the alias, does *not* create a `stage_map` row for the tombstone, and
asserts the stage is in `v_unmapped_stage`; i.e. the documented scenario is a standing "loud failure". In my scenario the alias-only event showed
as canonical `UNMAPPED` in `v_funnel_entries_daily_canonical` and in the Native view as a separate unlabelled row, while the restored stage counted
as `LI_SENT`; the combined headline silently splits one rung in two.

`stage_alias` itself accepts: an alias target that is itself `is_deleted=1`; a `stage_id` alias whose value equals a **live** stage id (shadowing);
a non-numeric `stage_id` alias value; alias value == its own target. All **[verified]**. `valid_from/valid_to` are never consulted.

Decision to make, then enforce: pick *one* mechanism. Recommended: tombstone stays the identity (events keep the true historical stage id) and
`stage_alias(kind='stage_id')` supplies the **mapping inheritance + native grouping**, resolved in `v_stage_effective`. **[fix verified]** (canonical
daily then shows LI_SENT on both days):

```sql
DROP VIEW IF EXISTS v_stage_effective;   -- (and re-create dependants, or do this in the pre-release edit of 0004)
CREATE VIEW v_stage_effective AS
SELECT s.account_id, s.pipeline_id, s.stage_id, s.label AS stage_label, s.display_order AS stage_order, s.is_closed, s.is_deleted,
       sm.canonical_code, cs.label AS canonical_label, cs.depth AS canonical_depth, cs.sort_order AS canonical_sort,
       cs.is_live, cs.is_dead, cs.is_won, cs.is_entry, sm.dead_reason_code, dr.label AS dead_reason_label, sm.depth_reached, sm.sub_row,
       COALESCE(sm.flag_attempt, dr.flag_attempt, cs.flag_attempt)       AS flag_attempt,
       COALESCE(sm.flag_connected, dr.flag_connected, cs.flag_connected) AS flag_connected,
       COALESCE(sm.flag_interested, cs.flag_interested)                  AS flag_interested,
       COALESCE(dr.counts_in_effort_kpis, 1)                             AS counts_in_effort_kpis,
       CASE WHEN sm.stage_id IS NULL THEN 0 ELSE 1 END                   AS is_mapped,
       sa.stage_id                                                       AS resolved_via_alias_stage_id,
       COALESCE(sa.stage_id, s.stage_id)                                 AS native_group_stage_id      -- successor id for native-row merging
FROM stage s
LEFT JOIN stage_alias sa ON sa.alias_kind='stage_id' AND sa.account_id=s.account_id AND sa.pipeline_id=s.pipeline_id AND sa.alias_value=s.stage_id
LEFT JOIN stage_map sm ON sm.account_id=s.account_id AND sm.pipeline_id=s.pipeline_id
      AND sm.stage_id = CASE WHEN EXISTS (SELECT 1 FROM stage_map o WHERE o.account_id=s.account_id AND o.pipeline_id=s.pipeline_id AND o.stage_id=s.stage_id)
                             THEN s.stage_id ELSE sa.stage_id END
LEFT JOIN canonical_stage cs ON cs.canonical_code=sm.canonical_code
LEFT JOIN canonical_dead_reason dr ON dr.dead_reason_code=sm.dead_reason_code;

-- v_unmapped_stage must use it too (it joins stage_map directly today):  FROM v_stage_effective e JOIN stage s ... WHERE e.is_mapped = 0 AND p.is_reporting_funnel = 1
```

Tighten `stage_alias` (triggers, because the rules cross tables):

```sql
CREATE TRIGGER trg_stage_alias_ck BEFORE INSERT ON stage_alias
BEGIN
  SELECT RAISE(ABORT,'stage_alias: target stage must be live (is_deleted=0)')
   WHERE NEW.alias_kind='stage_id' AND (SELECT is_deleted FROM stage WHERE account_id=NEW.account_id AND pipeline_id=NEW.pipeline_id AND stage_id=NEW.stage_id)=1;
  SELECT RAISE(ABORT,'stage_alias: stage_id alias must be a numeric, non-live id')
   WHERE NEW.alias_kind='stage_id' AND (NEW.alias_value GLOB '*[^0-9]*' OR NEW.alias_value=NEW.stage_id
         OR EXISTS (SELECT 1 FROM stage WHERE account_id=NEW.account_id AND pipeline_id=NEW.pipeline_id AND stage_id=NEW.alias_value AND is_deleted=0));
END;
```

(The same trigger for UPDATE.) Also add `ON DELETE RESTRICT` instead of CASCADE from `stage` to `stage_map`/`stage_alias`: REPLACE/DELETE of a stage currently erases its mapping silently.
Also hide dead rows: `v_funnel_stage_counts` lists tombstoned stages with 0 deals; add `WHERE p.is_reporting_funnel=1 AND (e.is_deleted=0 OR EXISTS(...deals...))` or filter in the consumer (L-priority).

## H4. Report-critical queries only use an index if `ANALYZE` has run; the canonical view cannot push the day filter  [verified, EXPLAIN + timings]

220k events, 60k deals (no `sqlite_stat1`, which is what a fresh database and every migration leave you with):

| query | plan as shipped | ms | after `ANALYZE` | with fix |
|---|---|---|---|---|
| `SELECT * FROM v_funnel_entries_daily WHERE ist_day=?` (all pipelines) | `SCAN ev USING INDEX ix_dse_stage_time` = full scan | 521-625 | 6 (skip-scan) | **14.6** |
| same, one account+pipeline | `SEARCH ... ix_dse_stage_time (account_id,pipeline_id)` = whole pipeline | 27 | 0.7 | - |
| `v_funnel_entries_daily_canonical WHERE ist_day=?` | `SCAN ev USING INDEX ix_dse_day` = full scan | 301-355 | **312 (still full scan)** | **3.3** (filter inside) |
| per-owner events for a day (all owners) | `SCAN deal_stage_event` | 21 | 0.8 | 0.9 |
| `v_funnel_entries_daily_in_scope WHERE ist_day=?` | cohort `GROUP BY` over the whole event table every call | 158 | 108 | see M10 |
| distinct deals ever at canonical INTERESTED | `SEARCH sm (ix_stage_map_canonical)` -> `SEARCH ev (ix_dse_stage_time)` | 18 | 19 | ok, see below |
| current-stage snapshot / canonical counts | covering `ix_deal_stage`, partial index used for the `LEFT JOIN ... is_archived=0` | 36 / 21 | 4 / 20 | ok |

Everything is linear in the event count: 5M events is seconds per daily section. Two causes: (1) every day-grained index has `ist_day` *after*
`account_id, pipeline_id`, so a day filter without both prefix columns relies on skip-scan, which SQLite only picks with stats; (2) the canonical view
sits on `SELECT DISTINCT` over a view, and SQLite does not push the outer `WHERE ist_day` through it (confirmed: same query with the filter written
inside the subquery: 3.3 ms).

Fixes:

```sql
-- 0002 (replace ix_dse_day; keeps is_human for the human/auto split)
CREATE INDEX ix_dse_ist_day ON deal_stage_event(ist_day, account_id, pipeline_id, to_stage_id);       -- [fix verified: 'SEARCH ev USING INDEX ix_dse_ist_day (ist_day=?)', no ANALYZE needed]
CREATE INDEX ix_dse_ist_day_actor ON deal_stage_event(ist_day, actor_owner_id) WHERE actor_owner_id IS NOT NULL;   -- per-owner day; replaces ix_dse_actor's (actor, day) only if you never query one owner over a long range - otherwise keep both
-- covering for "distinct deals that ever reached stage X" (no table lookup):
CREATE INDEX ix_dse_stage_time ON deal_stage_event(account_id, pipeline_id, to_stage_id, entered_at, deal_id);   -- append deal_id
```

For the canonical headline do not rely on the view for day-restricted reads; ship a parameter-friendly CTE in `leadgen/reports/metrics.py`
(filter inside the DISTINCT subquery; **[fix verified]** 24 rows in 3.3 ms vs 355 ms):

```sql
SELECT ist_day, account_id, pipeline_id, canonical_code, dead_reason_code, SUM(is_human) entries_human, SUM(1-is_human) entries_auto, COUNT(*) entries_total,
       COUNT(DISTINCT CASE WHEN is_human=1 THEN deal_id END) deals_entered_human, COUNT(DISTINCT deal_id) deals_entered_all
FROM (SELECT DISTINCT ev.ist_day, ev.account_id, ev.pipeline_id, COALESCE(e.canonical_code,'UNMAPPED') canonical_code, COALESCE(e.dead_reason_code,'') dead_reason_code,
             ev.deal_id, COALESCE(ev.actor_user_id,'') ak, ev.is_human
      FROM deal_stage_event ev
      JOIN pipeline p ON p.account_id=ev.account_id AND p.pipeline_id=ev.pipeline_id AND p.is_reporting_funnel=1
      LEFT JOIN v_stage_effective e ON e.account_id=ev.account_id AND e.pipeline_id=ev.pipeline_id AND e.stage_id=ev.to_stage_id
      WHERE ev.ist_day BETWEEN :d0 AND :d1)
GROUP BY 1,2,3,4,5;
```

Also: ship `PRAGMA optimize` (cheap) at `connect()` close and `ANALYZE` after every bulk load / at the end of `migrate()` and `import-legacy`
(`sqlite_stat1` is then maintained; with it the skip-scan plans above kick in). `doctor` should warn if `sqlite_stat1` is absent or older than N days.
Index weight to know: `deal_stage_event` 35 MB vs 47 MB of indexes at 220k rows; `ix_dse_from_stage` (6.4 MB) exists only to serve a stage delete that
RESTRICT forbids anyway - drop it.

## H5. The +91 mobile gate can be true for landlines and for a number whose country says US  [verified]

`is_indian_mobile` is derived from the E.164 digits only. Accepted and gated *in*: `('+919876543211', 'IN', 'landline')` -> 1, `('+919876543212', 'US', 'mobile')` -> 1.
Raw-only numbers (`phone_e164 IS NULL`, e.g. `'+91 98765 43213'`) get 0 (fails closed - good, but nothing flags it). Note the digit rule cannot be made exact:
Bangalore (80) and Ahmedabad (79) landlines are 10 national digits starting 7/8, identical in shape to mobiles, so the gate must use the type that the
importer derives (libphonenumber) as well.

```sql
is_indian_mobile INTEGER GENERATED ALWAYS AS (
  COALESCE(phone_e164 GLOB '+91[6-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]' AND phone_type IN ('mobile','unknown'), 0)) STORED,
-- table-level (at the end of the column list)
CHECK (phone_e164 IS NULL OR country_iso2 IS NULL OR (phone_e164 GLOB '+91*') = (country_iso2 = 'IN'))
```

**[fix verified]**: landline -> gate 0; IN/US mismatch rejected. Policy question for the owner: should `phone_type='unknown'` + 79/80 prefix pass? Safest
is gate = `phone_type='mobile'` only and a `ops_dq_issue` for every `+91` number with `unknown` type. Also add a DQ rule "raw number with `+91`/`0091`/10 digits but NULL e164".

## H6. Synthetic / backfill events are counted as human activity  [verified]

`is_human` = `source_type='CRM_UI'` only. An `event_origin IN ('synthetic','backfill_checkpoint','hubspot_current')` row inserted with `source_type='CRM_UI'` has `is_human=1` and
inflates "calls attempted" / per-person activity. Nothing says which `source_type` a synthesized row must carry.

```sql
CHECK (event_origin IN ('hubspot_history','stage_transitions_csv') OR source_type <> 'CRM_UI')   -- [fix verified: rejects 'synthetic' + CRM_UI]
```

(and use `source_type='SYNTHETIC'` for those rows). Related: the `stage_transitions_csv` origin has no actor, so a CSV-sourced and an API-sourced copy of the same
move (second vs millisecond timestamp) are not caught by the UNIQUE key and dedupe only because of the `(deal, stage, actor, day)` collapse, which treats a NULL actor
as a *different* actor; keep one origin per (deal, window) or normalise to ms before insert.

## H7. The `suppression` safety gate stores un-normalised values  [verified]

The whale list (`never_push`) is the one hard "never push" rule. The match is an exact string compare against `match_value_norm`, and the CHECKs are weaker than the ones on
`company_identifier`. Accepted: domain `'https://acme.com/path'`, `'acme..com'`; `company_name` `'ACME Corp'` (upper-case, so a lower-cased probe never matches); `linkedin_company`
`'Company/ACME/'`. A row that is stored but can never match is a silent bypass.

```sql
CHECK (match_type <> 'domain' OR (match_value_norm = lower(match_value_norm) AND instr(match_value_norm,'.') > 1 AND match_value_norm NOT GLOB '*[^a-z0-9.-]*'
        AND match_value_norm NOT GLOB '*..*' AND match_value_norm NOT GLOB '.*' AND match_value_norm NOT GLOB '*.' AND match_value_norm NOT GLOB 'www.*')),   -- [fix verified]
CHECK (match_type <> 'company_name' OR match_value_norm = lower(match_value_norm)),
CHECK (match_type <> 'linkedin_company' OR (match_value_norm = lower(match_value_norm) AND (match_value_norm GLOB 'company/*' OR match_value_norm GLOB 'school/*' OR match_value_norm GLOB 'showcase/*') AND match_value_norm NOT GLOB '*/')),
CHECK (match_type <> 'linkedin_person'  OR (match_value_norm = lower(match_value_norm) AND match_value_norm GLOB 'in/*' AND match_value_norm NOT GLOB '*/')),
```

Also `ux_suppression_entry` includes `kind`, so the same domain can be both `never_push` and `dnc` and an account-specific copy of a global row is allowed; fine, but
`v_suppression_active` should not be the only enforcement point: add a `suppression_check(match_type, value)` helper in Python used by every outbound path, tested with the
whale CSV rows, and a unique `(match_type, match_value_norm) WHERE kind='never_push' AND is_active=1` so one whale row cannot be duplicated per account.

---------------------------------------------------------------------------------------------------------------------------------

## M1. Event pipeline attribution is not derivable from HubSpot history  (design)

`propertiesWithHistory=dealstage` returns only the stage id (value, timestamp, sourceType, sourceId, updatedByUserId), never the pipeline. `deal_stage_event.pipeline_id` is `NOT NULL`
and part of the FK and the idempotency key. For ids that exist in two MAIN pipelines (`4018854633`, `4018854637`, `4018854642`) and for deleted ids, the importer must guess
(deal's current pipeline), which silently mis-credits a deal that moved Coding -> CoOps. Add provenance so the report can disclose it:

```sql
ALTER TABLE deal_stage_event ADD COLUMN pipeline_basis TEXT NOT NULL DEFAULT 'exact'
  CHECK (pipeline_basis IN ('exact','unique_stage_id','inferred_from_deal','ambiguous'));   -- pre-release: put it in the CREATE TABLE
-- and v_unmapped / report footer: COUNT(*) WHERE pipeline_basis IN ('inferred_from_deal','ambiguous')
```

## M2. `deal.company_id` and `deal_company` (and `contact.company_id`) can disagree  [verified]

Two paths to "the company of a deal": golden `deal.company_id` and portal-level `deal_company -> company_account_link -> company`. Probe: `deal.company_id = Other`, primary
`deal_company` link resolves to `Acme`: accepted. Same for `contact.company_id`. Decide one source of truth: make `deal.company_id` a view column
(`v_deal_current.company_id` from the primary `deal_company`), or add a trigger on `deal_company`/`deal` that rejects a primary link whose `company_account_link.company_id <> deal.company_id`,
plus a DQ rule. Also `deal_contact`/`deal_company` allow two `is_primary=1` rows per deal **[verified]**:

```sql
CREATE UNIQUE INDEX ux_deal_company_primary ON deal_company(deal_id) WHERE is_primary = 1;
CREATE UNIQUE INDEX ux_deal_contact_primary ON deal_contact(deal_id) WHERE is_primary = 1;   -- only if HubSpot's "primary" label is single-valued; check the portals
```

## M3. Company merge: cycles allowed; identifiers stay on the tombstone  [verified]

`A -> B` then `B -> A` are both accepted (`CHECK` only blocks self); `v_company_survivor` then returns nothing for either (header says "a cycle yields no row" - but no check flags it).
After a merge `company_identifier` rows stay on the tombstoned company, so `SELECT company_id FROM company_identifier WHERE value_norm='a.com'` returns the dead record.

```sql
CREATE TRIGGER trg_company_merge_no_cycle BEFORE UPDATE OF merged_into_company_id ON company
WHEN NEW.merged_into_company_id IS NOT NULL
BEGIN
  SELECT RAISE(ABORT,'company merge would create a cycle')
   WHERE EXISTS (WITH RECURSIVE up(id,hops) AS (SELECT NEW.merged_into_company_id,1
                   UNION ALL SELECT c.merged_into_company_id, up.hops+1 FROM up JOIN company c ON c.company_id=up.id
                   WHERE c.merged_into_company_id IS NOT NULL AND up.hops<50)
                 SELECT 1 FROM up WHERE id=NEW.company_id);
END;                                                                                   -- [fix verified]
-- merge procedure (document + test): UPDATE company_identifier SET company_id=:survivor WHERE company_id=:loser (resolve UNIQUE clashes first); same for company_account_link, contact, deal, verdicts.
```

Related hygiene: `root_domain` accepts `'a..com'`, `'-x.com'` **[verified]**; add `NOT GLOB '*..*' AND NOT GLOB '-*' AND NOT GLOB '*-.*'`.

## M4. TEXT HubSpot ids allow leading zeros  [verified]

`NOT GLOB '*[^0-9]*'` lets `'0123'` and `'123'` both exist as different owners/deals/contacts/engagements/companies (and in `UNIQUE(account_id, hs_*_id)`). Add `AND hs_deal_id NOT GLOB '0[0-9]*'`
(same for `hs_owner_id`, `hs_user_id`, `hs_contact_id`, `hs_company_id`, `hs_engagement_id`), or normalise in a single Python `norm_hs_id()`.

## M5. Nothing detects deal state vs event log drift  [verified]

`UPDATE deal SET stage_id='A'` with the last event `to_stage_id='I'`: accepted, no flag. Also `from_stage_id = to_stage_id` self-transitions accepted. Provide a view and make `doctor` count it:

```sql
CREATE VIEW v_deal_state_drift AS
SELECT d.deal_id, d.account_id, d.pipeline_id, d.stage_id AS deal_stage_id, l.pipeline_id AS event_pipeline_id, l.to_stage_id AS last_event_stage_id, l.entered_at
FROM deal d JOIN (SELECT deal_id, pipeline_id, to_stage_id, entered_at,
                         ROW_NUMBER() OVER (PARTITION BY deal_id ORDER BY entered_at DESC, event_id DESC) rn FROM deal_stage_event) l
  ON l.deal_id=d.deal_id AND l.rn=1
WHERE d.is_archived=0 AND (d.stage_id <> l.to_stage_id OR d.pipeline_id <> l.pipeline_id);
```

## M6. Pipeline with zero stages is not "unmapped"; no `stage_map` seed or YAML yet

Fresh DB: 5 reporting pipelines, 0 `stage` rows, so `v_unmapped_stage` is empty and `doctor` is green while every report is empty. `config/` has no `stage_map.yaml`
(only `accounts.yaml`, `canonical_stages.yaml`) although the architecture lists it. Add a view/DQ rule `pipeline WHERE is_reporting_funnel=1 AND NOT EXISTS (SELECT 1 FROM stage ...)`
to `doctor` warnings and to the report footer, and generate `stage_map` from YAML in `seed_reference_data` (stage rows come from the sync).
Also `stage_map`: a DEAD stage may have `depth_reached NULL` **[verified]** - require it unless the reason is `RETIRED_ADMIN`:
add to the validate triggers `SELECT RAISE(ABORT,'DEAD stage needs depth_reached') WHERE NEW.dead_reason_code IS NOT NULL AND NEW.dead_reason_code <> 'RETIRED_ADMIN' AND NEW.depth_reached IS NULL;`.

## M7. Migration runner and `db.py` hazards

1. **Race reports the wrong thing** [verified]: 4 concurrent `migrate()` calls each returned "applied 14" although only one applied. `_apply_one` returns `None` both when it applied and when it lost the race.
   Return a bool and only append when True.
2. **`doctor()`/`seed_reference_data()` create the database** [verified]: `doctor('/typo/path.sqlite')` created the file and reported `ok: True`
   (only `status()` guards `exists`). `python -m leadgen.db doctor` before the first migrate would create an empty `db/leadgen.sqlite`. Make `_open` use
   `connect(..., readonly=True)` for `doctor` (use a writable connection only for the FTS integrity-check, and only when the file exists) and refuse `create` outside `migrate`.
3. **No pre-migration backup.** Before applying anything to a non-empty database: `VACUUM INTO 'db/backup/leadgen-pre-NNNN-<ts>.sqlite'` (or `sqlite3.Connection.backup`). No down-migrations exist, so the backup is the rollback.
4. **`IF NOT EXISTS` on every CREATE masks drift**: a pre-existing hand-made `deal` with different columns is kept and the ledger still says applied. Either drop `IF NOT EXISTS` from migrations (the ledger already guarantees once-only) or
   have `doctor` compare the live `sqlite_master` against the schema produced by migrating `:memory:` (store `schema_sha256` in `schema_migration`).
5. **No version guard.** STRICT needs SQLite >= 3.37, generated columns >= 3.31, `PRAGMA table_list` >= 3.37, FTS5/JSON1. `connect()` should raise if `sqlite3.sqlite_version_info < (3, 37, 0)` with a clear message
   (the dev box has 3.51; a CI container may not).
6. **`transaction(con, immediate=False)` is a footgun** [verified]: a deferred transaction that read, then tries to write after another writer committed, fails *instantly* with `database is locked` (0.00 s) regardless of `busy_timeout`.
   Make `immediate=True` the only supported mode for writers and `BEGIN` (deferred) only on `readonly=True` connections. A real writer-vs-writer conflict waits correctly (measured 0.58 s with a 500 ms timeout).
7. Autocommit (`isolation_level=None`) plus `executemany` outside `transaction()` commits per statement; importers must use the context manager. Document it in the module docstring.
8. Pragmas to add: `journal_size_limit=67108864` (WAL does not shrink otherwise), `wal_autocheckpoint=1000` explicit, `PRAGMA optimize` on close, `trusted_schema=OFF`, `cell_size_check=ON`. A long report read blocks checkpoints (WAL grows) - keep report reads short.
9. `split_sql_statements`: two statements on one physical line (`CREATE TABLE a(x); CREATE TABLE b(y);`) raises `sqlite3.Warning: You can only execute one statement at a time` -> converted to `MigrationError` and rolled back (verified, safe), but the message is cryptic; detect `complete_statement` returning True mid-line and raise a clear error.
10. Checksum is over raw bytes; `core.autocrlf` or an editor re-save changes it. Add `.gitattributes`: `db/migrations/*.sql text eol=lf`.

## M8. Smaller key/cardinality gaps

* `origin_ref`: UNIQUE includes `entity_id`, so one legacy row can be bound to two different canonical companies (**[verified]**: `tam|companies|1` -> company A and company C accepted), defeating the "idempotent re-import" the table exists for. For 1:1 entities:
  `CREATE UNIQUE INDEX ux_origin_one_to_one ON origin_ref(entity_type, source_system, source_table, source_pk) WHERE entity_type IN ('company','contact','deal','engagement','owner','tam_company_verdict','cost_ledger','suppression','company_account_link');`
  (`company_identifier`/`contact_phone` legitimately fan out from one source row.)
* `company_account_link.lh2_domain` is documented as HubSpot's unique key, but the index is non-unique and the value is not normalised:
  `CREATE UNIQUE INDEX ux_company_link_lh2_domain ON company_account_link(account_id, lh2_domain) WHERE lh2_domain IS NOT NULL;` plus `CHECK (lh2_domain IS NULL OR lh2_domain = lower(trim(lh2_domain)))`.
* `rpt_legacy_crosscheck UNIQUE(snapshot_id, report_run_id, metric, row_label)`: NULL `report_run_id` (also set by `ON DELETE SET NULL`) makes the key non-unique. Use
  `CREATE UNIQUE INDEX ... ON rpt_legacy_crosscheck(snapshot_id, COALESCE(report_run_id,0), metric, row_label)`.
* `ops_import_run`: `status='succeeded'` with `finished_at NULL` is accepted [verified]; replace the second CHECK with `CHECK ((status = 'running') = (finished_at IS NULL))`. Stale `running` rows (a crashed importer) are never reaped: add a `doctor` warning for `running` older than N hours (same for `ops_sync_state.last_status='running'`).
* Owner identity across portals is only an email string; `rpt_owner_daily` "per person" will join on `email_norm`. Owners with NULL email (deactivated) cannot be unified. Consider a `person(person_id, email_norm UNIQUE)` and `owner.person_id`.
* `hsraw_object_revision` trigger fires on `payload_sha256` change only; if `history_json` is refreshed without changing the hash, the previous history is overwritten without a revision. Define `payload_sha256` as the hash of properties+associations+history, or fire on `OF history_json` too.
* AUTOINCREMENT absent on surrogate keys: after deleting the max `company_id` the next insert reuses it (**[verified]**), and `origin_ref` is polymorphic (no FK), so a stale external reference (report JSON, a CSV with ids) can silently point at a different entity. Since canonical rows are tombstoned not deleted this is theoretical for company/deal; use `AUTOINCREMENT` for `company_id`, `contact_id`, `deal_id`, `verdict_id` if hard deletes remain possible.

## M9. Touch triggers cost and one `updated_at` sentinel

Bulk `UPDATE deal` of 60k rows: 215 ms with `trg_deal_touch`, 76 ms without (2.8x; a second UPDATE per row plus index maintenance of `ix_deal_updated`). Acceptable now, but sync upserts should set `updated_at` explicitly
(`... DO UPDATE SET ..., updated_at = excluded.updated_at`) so the trigger's `WHEN` is false. If H2(b) is adopted, rewrite the guard as shown there.

## M10. Cohort and first-human views are full scans

`v_deal_cohort` (inside `v_funnel_entries_daily_in_scope`) `GROUP BY deal_id, pipeline_id` over the whole event table on every call (108-158 ms at 220k events); `v_deal_first_human_event` the same (40 ms).
Linear in history, not in the day asked for. Materialise `deal_cohort(deal_id, pipeline_id, joined_at, in_scope)` in the sync (it changes only when a deal's first event in a pipeline changes), or add
`CREATE INDEX ix_dse_deal_pipe_time ON deal_stage_event(deal_id, pipeline_id, entered_at);` and `CREATE INDEX ix_dse_first_human ON deal_stage_event(deal_id, entered_at) WHERE is_human = 1;`
so `MIN()` becomes an index probe. Also `v_deal_cohort.in_scope` returns 1 when `hs_created_at` is NULL (NULL comparison falls to `ELSE 1`): a deal with unknown creation date joined on the migration day is counted in scope.

---------------------------------------------------------------------------------------------------------------------------------

## L. Low priority / hygiene

* `canonical_stage` flag semantics: `ASSET_REQUESTED..PAYMENT` carry attempt/connected/interested = 0 while `MEETING_BOOKED` is 1/1/1, contradicting the comment "interested => connected => attempted" as a *depth* property. It matches the spec (flag = "this stage's entries count in M2/M3"), but "distinct deals ever attempted" must be computed as "reached any stage with depth >= first flagged rung", not by flag. Clarify the comment and expose `v_deal_canonical_reach(deal_id, canonical_code, first_at, last_at, entries)` so the cumulative funnel is one join, not a repeated 4-way join.
* Money is `REAL` (`cost_usd`, `cost_ledger.usd`, `usd_per_unit * qty`): float drift on SUM. Matches the architecture sentence but a DBA would store integer micro-USD (`usd_micros INTEGER`) for the ledger. At minimum `ROUND(SUM(usd),6)` in views.
* Legacy mirrors: 75 tables x `_import_run_id` FK with **no index** - the stated reversal `DELETE FROM legacy_x WHERE _import_run_id=?` and any `DELETE FROM ops_import_run` full-scan every table. `CREATE INDEX ... ON legacy_x(_import_run_id)` in the generator, or document that reversal is by drop-and-reload. The mirrors also keep the source `ON DELETE CASCADE` (e.g. `legacy_corpus_company` -> 6 child tables) in an archive that must "never drop anything"; strip actions to NO ACTION in the generator. `legacy/radar` has 2 FK orphans in `candidate_entity -> entity` (`PRAGMA foreign_key_check`), so a verbatim load under `foreign_keys=ON` fails unless the importer uses `PRAGMA defer_foreign_keys` and reports them to `ops_dq_issue` as the header says.
* `stage_map`, `pipeline`, `owner` etc. have `updated_at` touch triggers but `canonical_stage`/`canonical_dead_reason` have none and no `valid_from`: re-mapping a stage rewrites the canonical meaning of all history. Acceptable (rebuildable) but `rpt_funnel_daily.canonical_code` is denormalised, so persisted facts can disagree with the views until rebuilt: add `rpt_report_run.stage_map_sha256` and have the report refuse trend lines that span a mapping change.
* `v_funnel_stage_counts` / `canonical_counts` include tombstoned stages with 0 deals (board columns that no longer exist).
* `account.hubspot_timezone` uses `US/Eastern` (a legacy alias); `America/New_York` is the canonical IANA name. Informational only.
* Foreign keys with no covering index (hot only on parent delete, so only noise): `rpt_owner_daily.canonical_code`, `rpt_funnel_daily.dead_reason_code`, `stage_map.dead_reason_code`, `vertical.parent_vertical_id`, `deal.vertical_id`, `cost_ledger.vertical_id`/`account_id`, `suppression.account_id`.
* Tests worth adding for everything above: invalid timestamps, REPLACE/DELETE of a deal with events, alias-only tombstone in the canonical view, landline/US mismatch in `contact_phone`, `suppression` normalisation, merge cycle, rebuild-migration preserves events, `doctor()` on a missing path does not create the file, `EXPLAIN QUERY PLAN` for `WHERE ist_day = ?` on an unanalysed database must show `SEARCH ... ix_dse_ist_day`.

---------------------------------------------------------------------------------------------------------------------------------

## Suggested order of work (all before the first real `migrate`)

1. H1 timestamp CHECK + `IS` fixes (mechanical regex over 0001-0005), re-run the suite with `.000Z` fixtures.
2. H2 RESTRICT on history FKs + `recursive_triggers` + new touch-trigger guard + runner rebuild protocol / `foreign_key_check`.
3. H3 alias-aware `v_stage_effective` and `v_unmapped_stage`, `stage_alias` triggers, stage RESTRICT.
4. H4 indexes (`ix_dse_ist_day`, covering `ix_dse_stage_time`), `ANALYZE`/`optimize` hooks, CTE for the canonical headline.
5. H5 phone gate, H6 synthetic-event CHECK, H7 suppression CHECKs (these three protect business rules).
6. M1-M6 model decisions (pipeline basis, deal-company source of truth, merge cycle trigger, id zero padding, drift view, empty-pipeline DQ).
7. M7 runner/db.py items; M8-M10 and L as time allows.

All SQL fixes marked "[fix verified]" were exercised on a patched copy of the 14 migrations; the patched set applied cleanly and passed the probes listed. I did not run the author's 66 tests
against the patched set (several fixtures use second-precision timestamps and would need `.000Z`).
