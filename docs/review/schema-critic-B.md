# Schema critic B - legacy fidelity and downstream needs

Scope: can `db/migrations/0001..0014` hold the real data? Reviewed 2026-10-04 against the legacy copies under `legacy/**` (all opened `mode=ro&immutable=1`), `docs/discovery/*`, `data/gsheets_catalog.json`.
Method: migrated throwaway DBs under `db/_scratch/` (deleted at the end), loaded the real data into the `legacy_*` tables, replayed real rows through the canonical CHECKs, compared structure and content. `db/leadgen.sqlite` was never created or touched. No secret values were read or printed; no network calls.

Because `db/leadgen.sqlite` does not exist yet, every "edit 0002/0005" fix below can be made in place. Once a real DB has applied them, use new `0015_*.sql` files instead.

## 0. Verdict

The verbatim layer is sound: every legacy table, index and view is covered, the generator is reproducible, and a full load of all 9 databases is lossless (value and `typeof` identical, rowids preserved) with one exception (finding 1). The canonical layer fits the HubSpot mirror, the 241 legacy snapshot JSONs, the live Cluster stage list, the sheets catalog and a full Gmail API message, but I found 3 high, 8 medium and a handful of low issues, listed by severity in section 3.

## 1. What was verified and passed

### 1.1 `legacy_*` DDL coverage (9 registered databases)
Registry in `tools/gen_legacy_ddl.py` matches the 10 SQLite files that exist under `legacy/` (scanned by file header, not by extension). The 10th is `tam.sqlite3.bak-20260929-140234`, intentionally excluded; I confirmed it is a strict subset of `tam.sqlite3` (0 company / crm_push / funnel_event ids missing from the live file, 0 changed names or domains). `tam.db` is a 0-byte stub. All `-wal` files are 0 bytes, so `immutable=1` loses no data.

| DB | tables | explicit idx | views | legacy PK-less tables | result |
|---|---|---|---|---|---|
| tam | 17 | 16 | 0 | none | columns / defaults / PK / FK / indexes identical (+`_import_run_id` last) |
| itsvc | 12 | 6 | 0 | captures, classifications, partner_badges | identical |
| corpus | 19 | 9 | 3 (v_accounts, v_corpus_summary, v_source_health) | none | identical; the 3 views return the same row counts and first rows as the source (302,963 / 2 / 28) |
| pipeline | 7 (+sqlite_sequence) | 7 | 0 | none | identical, AUTOINCREMENT kept |
| gmaps | 2 | 0 | 0 | none | identical |
| radar | 10 | 4 | 0 | none | identical |
| resolver | 3 (11 untyped columns) | 0 | 0 | walls | identical, BLOB affinity preserved |
| searchledger | 2 (+sqlite_sequence) | 1 | 0 | events | identical, AUTOINCREMENT kept |
| itdirs | 3 | 0 | 0 | counters | identical |

Searched for and found NONE of: WITHOUT ROWID tables, virtual/FTS tables, triggers, generated columns, COLLATE clauses, columns named rowid/oid/_rowid_/`_import_run_id`, NULL values in non-INTEGER primary keys, FK violations (except finding 1), `quick_check` errors. Only the three corpus views exist as non-table objects. Autoindexes (sqlite_autoindex_*) are carried by the PK/UNIQUE clauses in the table text.
Keyword hunt: legacy columns named `key` (tam.categories, pipeline.cache), `query` (gmaps.tiles) and canonical `ops_audit_log.action` are SQLite fallback keywords, legal unquoted, and none is reserved in PostgreSQL either. No Postgres-reserved word is used as a table or column name anywhere (canonical or legacy).

### 1.2 Generator reproducibility
`tools/gen_legacy_ddl.py --check` returns OK for all 9 files (also under different `PYTHONHASHSEED`). `render_migration()` called twice is byte-identical and equals the committed file; the built-in `verify()` returns no problems for all 9. I also fed `rewrite_ddl` adversarial DDL (WITHOUT ROWID, STRICT, quoted names with spaces, composite FKs, `REFERENCES` inside a CHECK string, line comments before the closing paren, CTE/subquery/comma-join views, partial/expression indexes): all rewritten correctly. Triggers and virtual tables raise loudly (`NotImplementedError` / `ValueError`), which is the right behaviour.

### 1.3 Real rows into `legacy_*`
* Sampled load: ~1,000 rows per table (first 150, last 150, evenly strided middle) with `_import_run_id` set: 75 tables, 0 insert errors (CHECKs, UNIQUEs and partial unique indexes such as `legacy_tam_ux_crm_deal` all accepted the real data). The only FK violations are the expected "child sampled without parent" ones.
* Full load via `ATTACH` + `INSERT ... SELECT rowid, cols, run_id` for every table of all 9 DBs: counts equal and an `EXCEPT` over `(rowid, every column, typeof(every column))` returns 0 rows for every table. Whole tam load takes ~3 s; scratch DB ends at ~874 MB (legacy_tam_pages 264 MB, itsvc site_text 66 MB, corpus company 58 MB).
* `leadgen.db.doctor()` on the loaded DB: integrity ok, FTS ok, only the radar FK finding below.

### 1.4 Canonical fit that passed (real data, replayed through the CHECKs)
* `legacy/hubspot/crm_mirror/data/{deals,contacts,companies}.json` (1,197 / 1,358 / 871): all inserted into stage (22 history stages) / owner / lead_source / company + company_account_link / contact + contact_phone / deal / deal_contact / deal_company with zero errors and zero FK violations; v_deal_current, v_stage_effective, v_unmapped_stage, v_contact_mobile_gate run (986 of the contacts carry an Indian mobile). Custom deal properties (poc, source_tab, hs_priority, loc, pr_count, outflo_*, metadata_link) land in `props_json`.
* `docs/discovery/companyops-pipelines-live.json`: all 66 stages (33 + 33, no shared ids) insert into `stage` after casting probability/isClosed from strings.
* Snapshot JSONs: all 241 files (MAIN 186, COMPANYOPS 50, RAT 5) insert into `rpt_legacy_snapshot` with every top-level key mapped (`flow`, `current_state`, `dashboard_flow`, `cumulative`, `engaged_deal_ids`, `bootstrap`->is_bootstrap, `seeded`->is_seeded, `owner_id/owner_name`, `cumulative_note`/`generated`/counts into `payload_json`). Date in file equals date in file name for all 241.
* Backfill checkpoints (`cluster{1,2}_history.json`, 15,323 stage events): all timestamps are ms-ISO `Z`, no duplicate (deal, ts, stage), sourceType in CRM_UI / INTEGRATION / MERGE_OBJECTS, `sourceId`/`updatedByUserId` nullable, so `deal_stage_event` columns (`source_type`, `source_id`, `actor_user_id`) fit. Stage-id coverage is finding 10.
* `data/gsheets_catalog.json`: all 1,570 spreadsheets / 1,860 tabs insert into `ext_gsheet_catalog` / `ext_gsheet_tab` (no duplicate ids or (spreadsheet, sheetId)); a 200 KB cell and the FTS5 insert/`integrity-check` work.
* Gmail: a full `format=full` style message (multipart/mixed > alternative + PDF + inline PNG, 4 labels incl. a user label, historyId, internalDate ms, threadId, RFC822 ids) maps into account / label / thread / message / message_label / attachment; FTS insert, UPDATE and DELETE triggers and `integrity-check` pass; an upper-case `from_addr` is rejected as intended.

## 2. How the severity is ranked
High = deterministic import failure, data with no home that matters to the stated goals, or silently wrong report numbers. Medium = information loss or an importer rule that must be decided before writing the importer. Low = polish / documentation.

## 3. Findings (ranked)

### HIGH

**H1. Radar load fails with `foreign_keys=ON` (2 legacy orphans).**
`legacy/hubspot/godown/shutdown-radar/data/radar.sqlite` has 2 rows in `candidate_entity` whose `cin` has no `entity` row: rowid 10783 (candidate 10799, `U72900KA2019PTC128848`) and 10785 (candidate 10821, `U74999DL2016PTC298050`), both `match_method='override'`, `is_confirmed=1`, i.e. human decisions. `INSERT ... SELECT` of the whole radar DB inside one transaction with `defer_foreign_keys=ON` fails at COMMIT ("FOREIGN KEY constraint failed"); every other DB commits. The connection PRAGMAs in `leadgen.db.connect` force `foreign_keys=ON`.
Fix (importer, no schema change): open the legacy load with `PRAGMA foreign_keys=OFF` (must be set outside a transaction), load all tables, run `PRAGMA foreign_key_check`, and write each hit to `ops_dq_issue` (`fingerprint='legacy_fk_orphan|legacy_radar_candidate_entity|10783'`, `rule_code='legacy_fk_orphan'`, severity `warn`) instead of dropping the row. Add a test that loads radar and asserts exactly 2 dq issues. Optional hardening: have `gen_legacy_ddl.py` emit a header line listing the known orphans so the next reader is not surprised.

**H2. No home for company-level phone numbers, so the +91 mobile gate cannot be stored for them.**
`contact_phone` requires a `contact`, but the bulk of real phone data is attached to organisations: `itsvc.candidates.phone_e164` has 31,621 values of which 24,227 are Indian mobiles after stripping spaces (4,868 +91 landline-like, 263 toll-free, 1,993 foreign), plus the Google Places cache (`gmaps_cache.tiles.places` carries `nationalPhoneNumber`, 44 tiles), Tracxn phones, `tam.companies.office_phone` (empty today). The architecture says the +91 mobile gate is a stored boolean; for these numbers it would have to be recomputed from `legacy_itsvc_candidates` every time. Related traps in the same column: the column is named `phone_e164` but 31,616 values are NOT E.164 (e.g. `+91 98765 43210`, `+91 80 4092 1014`) and 368 are obvious dummies (`+91 12345 67890` x100, `+91 98765 43210` x33, `0123456789`, `123456789`) that must be loaded with `is_placeholder=1`.
Fix: add to `0002_canonical.sql` (after `contact_phone`):
```sql
CREATE TABLE IF NOT EXISTS company_phone (
  phone_id          INTEGER PRIMARY KEY,
  company_id        INTEGER NOT NULL REFERENCES company(company_id) ON DELETE CASCADE,
  phone_raw         TEXT    NOT NULL CHECK (phone_raw <> ''),
  phone_e164        TEXT    CHECK (phone_e164 IS NULL OR (phone_e164 GLOB '+[1-9]*' AND phone_e164 NOT GLOB '+*[^0-9]*' AND length(phone_e164) BETWEEN 8 AND 16)),
  country_iso2      TEXT    CHECK (country_iso2 IS NULL OR (length(country_iso2) = 2 AND country_iso2 = upper(country_iso2))),
  phone_type        TEXT    NOT NULL DEFAULT 'unknown' CHECK (phone_type IN ('mobile','landline','voip','tollfree','unknown')),
  is_indian_mobile  INTEGER GENERATED ALWAYS AS (COALESCE(phone_e164 GLOB '+91[6-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]', 0)) STORED,
  is_primary        INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0, 1)),
  is_placeholder    INTEGER NOT NULL DEFAULT 0 CHECK (is_placeholder IN (0, 1)),
  source_system     TEXT,
  verified_at       TEXT    CHECK (verified_at IS NULL OR verified_at LIKE '____-__-__T__:__:__%Z'),
  created_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
  updated_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (updated_at LIKE '____-__-__T__:__:__%Z')
) STRICT;
CREATE UNIQUE INDEX IF NOT EXISTS ux_company_phone_number ON company_phone(company_id, COALESCE(phone_e164, phone_raw));
CREATE INDEX IF NOT EXISTS ix_company_phone_gate ON company_phone(company_id) WHERE is_indian_mobile = 1 AND is_placeholder = 0;
```
plus `'company_phone'` in the `origin_ref.entity_type` CHECK (0001), its validate/delete triggers (0002 near line 889-979) and a `trg_company_phone_touch`. Also extend `v_contact_mobile_gate` (or add `v_company_mobile_gate`) so reports see both.

**H3. `deal_stage_event.pipeline_id` cannot be derived from `propertiesWithHistory=dealstage` alone for the 3 shared MAIN stage ids.**
Interested `4018854633`, Commercial Negotiation `4018854637`, Closed/Won `4018854642` exist in both MAIN pipelines (verified in funnel-reports.md 14 and hubspot-main-funnel.md). A history entry only has `value=<stage id>`; the DSE table needs `(account_id, pipeline_id, to_stage_id)` with a composite FK, and `hsraw_object.history_json` is documented as holding only `dealstage, hubspot_owner_id`. For a deal that ever moved between Coding and CoOps ( Global ) the importer would have to guess, which silently mis-counts exactly the rungs that headline the report (Interested, Negotiation, Won). STAGE_TRANSITIONS.csv has the same problem (it carries the old pipeline LABEL `Scraped`/`Campaign` per deal, not per event).
Fix: (a) fetch `propertiesWithHistory=dealstage,hubspot_owner_id,pipeline` and update the comment in `0003_hsraw.sql` and `docs/ARCHITECTURE.md`; (b) specify the resolution order in the importer docs: stage id unique inside the account -> that pipeline; else the `pipeline` property value in effect at `entered_at` (latest history entry with timestamp <= entered_at); else the deal's current pipeline; else raise `ops_dq_issue(rule_code='ambiguous_stage_pipeline')` and skip the event instead of guessing; (c) optionally add `pipeline_resolution TEXT CHECK (pipeline_resolution IN ('unique_stage','pipeline_history','deal_current','manual'))` to `deal_stage_event` so the footer can count the guessed ones.

### MEDIUM

**M1. Timestamp CHECK `LIKE '____-__-__T__:__:__%Z'` accepts garbage, and for `deal_stage_event` that silently drops the event from every report.**
Verified on a scratch DB: `entered_at='2026-13-45T99:99:99Z'` and `'2026-10-04T10:00:00+05:30Z'` both INSERT successfully and get `ist_day = NULL`; every report index/filter on `ist_day` then skips them. (The legacy CSV conversions in M5 are the most likely source.)
Fix: in `deal_stage_event`, `deal_owner_event`, `engagement` (all three have a generated `ist_day`) change the CHECK to `CHECK (entered_at LIKE '____-__-__T__:__:__%Z' AND datetime(entered_at) IS NOT NULL)` and declare `ist_day ... STORED NOT NULL`. Same `AND datetime(col) IS NOT NULL` for every other `...LIKE '____-__-__T__:__:__%Z'` column, which can be done mechanically with the authoring script that generated these files.

**M2. Real values the canonical CHECKs reject, so the importers need explicit normalisers (measured on real data).**
| Source field | Real values | Canonical rule | Effect |
|---|---|---|---|
| tam.companies.hq_country | `'India'` 20,278, `'IN'` 458 | `length=2 AND =upper` | 7,943 of the 27,723 live (non-merged) tam companies fail to insert (replayed). Map names to ISO-3166-1 alpha-2 |
| tam.companies.root_domain | `"google.com`, `"adtelligent.com`, `"pubnative.net`, `pegàsehealth.com`, `<U+200B><U+200B>google.com` | `NOT GLOB '*[^a-z0-9.-]*'` | 6 reject; strip quotes/zero-width chars, punycode IDN, else `ops_dq_issue` |
| itsvc candidates/entities.domain | `''` for 3,392 rows (not NULL) | `value_norm <> ''` | map `''` to NULL |
| radar.entity.cin | 16 values are LLPINs (`AAE-7433`, 8 chars) in the cin column | cin = 21 chars `[A-Z0-9]` | route 8-char hyphenated values to `identifier_type='llpin'` |
| resolver.companies.cin | `''` for 98 rows | | map to NULL |
| LinkedIn company URLs | pct-encoded slugs (`63-moons-%e2%84%a2`, itsvc 36, tam 11), quoted slugs (`company/'aastha-health-care'`), 356 itsvc + 4 corpus `school/` URLs | `company/|school/|showcase/` lowercase, no `?#`, no trailing `/` | decide: decode pct-encoding, strip quotes, keep `school/` |
| contact.linkedin (mirror contacts.json) | 51 are `/company/` URLs, 1 `/pub/` | `linkedin_person_norm GLOB 'in/*'` | route company URLs to `company_identifier`, keep `pub/` in `linkedin_raw` only |
| phones (mirror contacts.json) | 149 of 1,139 not E.164 (`+91 72593 18319`, `9742044482`, `+91-7042813998`) | E.164 CHECK | strip separators, assume `+91` for 10-digit nationals, keep `phone_raw`, `phone_e164 NULL` if unparsable |
| signalhire_results.item | 77 of 529 are hashes, not LinkedIn URLs | | do not parse as URL |
| corpus icp_score.match_label | `Unclear` 300,464, `Weak` 792, `Maybe` 88, `Fit` 1 | `icp_bucket IN (fit,maybe,out,unscored)` | no bucket for Unclear/Weak. See M3 |
| corpus.company.status | 11 UK Companies House values (`active - proposal to strike off` 12,872, `live but receiver manager ...` 320, `receivership`, `voluntary arrangement`...) | 7-value enum | map to enum, keep original in `status_detail` (column exists) |
Timestamp formats per legacy DB (all canonical columns require `...T..:..:..Z` UTC): tam `YYYY-MM-DD HH:MM:SS` (naive, SQLite `datetime('now')`, i.e. UTC); itsvc `...T..:..:...ffffffZ` (microseconds); corpus `...+00:00`; pipeline `...ffffff+00:00`; radar naive `YYYY-MM-DDTHH:MM:SS`, plus date-only, `YYYY-MM`, `YYYY` and RFC-822 HTTP dates; resolver/searchledger/itdirs epoch floats; `data/gsheets_catalog.json` `generated` is `+0530` local time (convert before writing `catalog_fetched_at`, else the CHECK rejects it). Add all of this to `docs/DATA_DICTIONARY.md` / the importer specs.

**M3. `tam_company_verdict` cannot hold what the verdict sources carry.**
Missing with real data: tam `company_categories.regulated_status` / `regulatory_sensitivity` (7,641 rows, e.g. `SEBI:Stock Broker (equity)` / "handles client funds/PII" - a compliance signal), `parked_as` (2,203, `individual_practitioner`), `funnel_stage/bucket/reason`; tam `classifications.evidence_quote` (6,897), `has_own_tech_product`, `company_type`, `keyword_score/hits`, multi-valued `segments`; itsvc `classifications.{subsegments,tags,exclusion_flags,tech_stack,erp_platforms}_json`, `rules_applied_json` (band letter `A` + score 85/92 live inside this JSON); corpus `match_label`, `score_raw`, multi-slug `segments` JSON, `rationale`. `icp_band` allows only P1..P4 (itsvc bands are letters A..; tam `priority_tier` A-D maps to `priority_tier`, fine). No bucket for corpus `Unclear`/`Weak`. itsvc has no fit/maybe/out at all.
Fix: add to `tam_company_verdict`: `evidence_quote TEXT`, `native_bucket TEXT` (verbatim `Fit/Maybe/Out/Unclear/Weak`), `details_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(details_json) AND json_type(details_json)='object')`. Rule for the importer: bucket `unscored` (never `fit`) whenever the source has no ICP bucket; corpus `Unclear/Weak` -> `unscored` + `native_bucket`. Document that `segment` is the primary segment and the rest goes to `details_json`.

**M4. `company_identifier` UNIQUE (type, value) forbids storing shared domains as weak evidence.**
itsvc has 30 domains claimed by several DISTINCT LinkedIn companies (119 candidates: `hyatt.com` 13, `iima.ac.in` 12, `ihg.com` 11). Because the UNIQUE covers weak rows too, the only choices are to merge unrelated entities (wrong) or drop the evidence. The header comment already says such domains must not be loaded as root_domain; but there is nowhere to record them.
Fix: replace the table-level `UNIQUE (identifier_type, value_norm)` by
```sql
CREATE UNIQUE INDEX IF NOT EXISTS ux_company_identifier_strong ON company_identifier(identifier_type, value_norm) WHERE is_strong = 1;
CREATE INDEX        IF NOT EXISTS ix_company_identifier_value  ON company_identifier(identifier_type, value_norm);
```
and load shared domains with `is_strong=0` (never auto-link on weak). Cross-source overlap measured for sizing: tam x itsvc 4,017 domains, tam x corpus 438, itsvc x corpus 679; no LinkedIn duplicates inside tam live rows or inside itsvc.

**M5. Stage-history CSV (`godown/ceo_reality_check/STAGE_TRANSITIONS.csv`, 7,918 rows) will double-count against API history.**
It has IST wall-clock timestamps to the SECOND (`2026-07-15 20:33:27`), stage LABELS for some rows (`Cold Call`, `Dead/ColdCall/WrongFit`, `Call Attempted (retired)`) and ids for others (`3992480464`), and the old pipeline labels `Scraped`/`Campaign`. The DSE idempotency key is `(deal_id, entered_at, pipeline_id, to_stage_id)` with ms-precision API timestamps, so `...:27.000Z` never collides with `...:27.412Z` and both rows would land. This CSV is the only record if property history expires, so it must be importable.
Fix: (a) importer converts IST->UTC and writes `event_origin='stage_transitions_csv'`; (b) imports it only for deals that have no `hubspot_history` events, or dedupes on `(deal_id, to_stage_id, substr(entered_at,1,19))` before insert; (c) resolves labels through `stage_alias(alias_kind='label')` (needs aliases for `call attempted (retired)` etc. seeded before the import); (d) add `ix_dse_dedupe_sec ON deal_stage_event(deal_id, to_stage_id, substr(entered_at,1,19))` or a doctor check for near-duplicates.

**M6. No canonical home for signals / evidence (EAV) data.**
corpus `indicator` (652,128 rows + `indicator_def` 57 with evidence_snippet/url), tam `signals` (17,032: mx_provider, techs, uses_jira/slack/asana/clickup, open_jobs), radar `evidence` (11,018) / `scored` (tier confirmed 2,734 / probable 8,055, shutdown_date, sector) / `liveness` / `prequal`, itsvc site flags. These drive `tam_company_verdict` but nothing canonical holds them, so a deal or report cannot say "which distress tier / what evidence" without reading `legacy_*`. Acceptable only if declared.
Fix (pick one and document it in ARCHITECTURE section 1): either "signals stay legacy-only, reachable via `origin_ref`", or add
```sql
CREATE TABLE company_signal (
  signal_id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES company(company_id) ON DELETE CASCADE,
  signal_code TEXT NOT NULL CHECK (signal_code <> '' AND signal_code = lower(signal_code)),   -- e.g. distress_tier, uses_jira, cobol_loc
  value_bool INTEGER, value_num REAL, value_text TEXT, confidence REAL CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
  evidence_snippet TEXT, evidence_url TEXT, observed_at TEXT CHECK (observed_at IS NULL OR observed_at LIKE '____-__-__T__:__:__%Z'),
  source_system TEXT NOT NULL, import_run_id INTEGER REFERENCES ops_import_run(import_run_id) ON DELETE SET NULL,
  UNIQUE (company_id, signal_code, source_system, observed_at)) STRICT;
```
Also not covered: the sourcing funnel (`tam.funnel_events` 224,846 rows, `company_categories.funnel_*`) and the push log (`tam.crm_pushes` 1,126 rows with status `pushed/contact_corrected/removed_duplicate/removed_off_icp/removed_too_big`, owner id, pipeline id, dealstage id, pushed_at). These are legacy-only by design; state it, and specify the derived canonical effects: `removed_off_icp`/`removed_too_big`/`removed_duplicate` -> `suppression(kind='off_icp'/'too_big'/'duplicate')`, `pushed` -> `suppression(kind='delivered')`, deal link via `deal(hs_deal_id)`. The push rows carry no portal: `pipeline='2464812771'`/`'default'` plus owner ids `1680156xx..` imply `companyops` (245 rows have blank pipeline/owner/stage), so the importer must pin `account_id='companyops'` for tam and note that `'default'` there is Cluster 1.

**M7. `ext_gsheet_*`: missing sheet time zone, default pulls everything, owner is single-valued.**
Catalog fields vs columns: `timeZone` (1,569 of 1,570; `Asia/Calcutta` 1,248, `America/Los_Angeles` 300, `Etc/GMT` 19, `Europe/Madrid` 2) has no column, and it matters for any date cell / formatted value. `owners` is an array in the JSON (all 1,570 happen to have exactly 1, so `owner_email` fits today). `ownedByMe_hubspot_token`, `sharedDriveId` value (all null) are dropped (fine). 37 sheets have no `lastModifyingUser` (nullable, fine). Privacy: `pull_enabled` DEFAULT 1 and `pii_class` DEFAULT 'normal' mean a bulk pull reads 1,529 sheets owned by other people, including the intern-hiring sheet `1IPA45kJ6yTsBo8d33DM0Ay_CIfBa6jrj_wrh3A4_uiQ` with applicant PII, unless someone remembers to flip it. google-integrations.md section 6 says to exclude it.
Fix: add `time_zone TEXT` and `owners_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(owners_json) AND json_type(owners_json)='array')` to `ext_gsheet_catalog`; change `pull_enabled` default to 0 (opt-in allowlist in `config/gsheets_pull.yaml`) and seed `pii_class='excluded', pull_enabled=0` for the intern sheet id in the catalog importer; add a doctor check that no `ext_gsheet_row` exists for an excluded catalog row. Storage note: rows are stored three times (`cells_json`, `text_flat`, FTS index) and 46.6 M allocated cells exist (one workbook alone 5.7 M); keep the allowlist small or make FTS opt-in per tab.

**M8. `ext_gmail_*` does not keep everything in a full Gmail message.**
Covered: id, threadId, labelIds (via `ext_gmail_message_label`; the importer must upsert unknown `Label_*` ids first or the FK rejects the row), historyId, internalDate (ms -> ISO ms), sizeEstimate, snippet, From/To/Cc/Subject/Message-ID/In-Reply-To, attachment part id / attachmentId / filename / mime / size, raw .eml on disk.
Not stored: HTML-only mail body (only `body_text`; many marketing/auto-reply mails have no text/plain part), `Date` header (sender clock vs internalDate), `Bcc`, `Reply-To`, `Sender`, `References` (conversation threading beyond In-Reply-To), `List-Unsubscribe`, `Delivered-To`, display names (by design), inline-image `Content-ID` / disposition, full MIME part tree. `to_addrs/cc_addrs` are space-separated, which is ambiguous for quoted local parts. Everything is recoverable only if `raw_path` files are kept.
Fix: add `body_html TEXT`, `date_header_at TEXT`, `bcc_addrs TEXT`, `reply_to TEXT`, `references_hdr TEXT`, `headers_json TEXT CHECK (headers_json IS NULL OR json_valid(headers_json))` (full header list incl. List-*) to `ext_gmail_message`; `content_id TEXT`, `disposition TEXT CHECK (disposition IS NULL OR disposition IN ('attachment','inline'))` to `ext_gmail_attachment`. Keep addresses comma-separated or document the separator. Also note no `gmail.readonly` credential exists (google-integrations.md section 4) so this table is untestable end-to-end today.

### LOW / notes

**L1. `tools/gen_legacy_ddl.py` footguns.** Default mode (no flag) overwrites the committed migrations, with no guard against replacing a file whose checksum is already in `schema_migration`; `ChecksumMismatchError` catches it only at the next `migrate()`. `--check` needs the live legacy copies and returns 1 with SKIP when they are absent (CI without `legacy/`). Fix: make `--check` the default, require `--write`, and refuse `--write` when the target exists and differs unless `--allow-change`; print a reminder to add a new numbered migration instead. Latent: a `-- comment` after the final `)` in a legacy `sql` would swallow the generated `;` (not triggered by any current source).

**L2. `_import_run_id` is nullable and unindexed in every legacy table.** The advertised undo `DELETE ... WHERE _import_run_id = ?` is a full scan (fine for one-off), and `DELETE FROM ops_import_run` with FKs on scans all 60+ children. Optional: `CHECK (_import_run_id IS NOT NULL)` is impossible without breaking the verbatim DDL promise, so enforce it in the loader and add a doctor check `SELECT count(*) FROM legacy_x WHERE _import_run_id IS NULL`.

**L3. PK-less legacy tables need a defined `source_pk`.** itsvc `captures`, `classifications`, `partner_badges`, resolver `walls`, searchledger `events`, itdirs `counters`: copy `rowid` explicitly (works and was verified; the INSERT must list `rowid`), and define `origin_ref.source_pk = rowid`. Composite-PK tables join with `|` (safe today; values such as pipeline.quota provider/metric/window do not contain `|`). Re-running an import duplicates these tables (no PK to conflict on): the loader must key idempotency on `ops_import_run.source_fingerprint`, not on row conflicts. Also state whether `origin_ref.source_table` is the original (`companies`) or prefixed (`legacy_tam_companies`) name; pick the original and document it.

**L4. Stage ids in Cluster 2 history that the live pipeline does not know.** 4,205 of 14,830 events (3,334 of 5,510 deals) reference 8 stage ids absent from the live Cluster 2 list: `4080987861` (old LinkedIn sent, 2,831), `4132224744` (old LinkedIn connected, 1,115), `4111134400` (125), `4080987862` (125), `4080987868` (3), `4080987867` (3), `4111134403` (2), `4080987879` (1). The last six are not documented anywhere. They must exist as tombstone `stage` rows (`label_source='history'`, `is_deleted=1`) with `stage_map` rows before the DSE load, or the composite FK rejects the events and `v_unmapped_stage` fires. Cluster 1 history is 100% known ids.

**L5. itsvc placeholders.** 61,593 of 61,799 classification rows are `pre-classification-prior` (builds_software=1 for all) and 206 are real `rules v1` rows. As designed they become `verdict_method='placeholder'`, `is_placeholder=1`, `icp_bucket='unscored'`; do not materialise 61k placeholder verdicts, keep them in `legacy_itsvc_classifications` only (the architecture allows it). The same goes for the 368 dummy phone numbers (H2) and `tam.cost_ledger` (`usd_est=0` for all 2,634 rows; quantities are real, so load them with `usd NULL`, `usd_basis='unknown'` and decide whether `is_placeholder` should mark the row or only the price; as written `is_placeholder=1` flags the whole row as fake spend). `phone_reveal` rows carry 8 credits per row in `qty`, so map `qty` (calls) and `credits` separately.

**L6. `vertical` lacks the HubSpot segment mapping.** tam `categories.hubspot_segment` (13 values like `Fintech`, `EdTech`, `Enterprise SaaS/IT`) is what `crm_pushes.segment` / `deal.segment` carry. Add `hubspot_segment TEXT` to `vertical` (or seed an alias table) so deal.segment -> vertical_id is deterministic.

**L7. Contact fields with no column.** Founder `DIN` and resolver confidence (`resolver.companies.data` JSON), `pipeline.people.confidence/linkedin_confirmed/name_source`, corpus `contact` PECR/GDPR fields (`dnc_checked`, `opted_out`, `legal_basis_ref`; empty today), mirror `contact.company` text. Keep in legacy (`origin_ref`) or add `contact.attrs_json`. The `poc` deal property (owner id of the person of contact, differs from owner on 3 deals) is only in `props_json`.

**L8. Misc, no action needed.** The RAT/other `pipeline` seeds and the 22 mirror stage ids (`label 'stage <id>'` placeholders in my replay) need real labels from the API before `stage_map` can be complete. `rpt_legacy_snapshot`: file `full_funnel_owner_unassigned_*` has `owner_id: null` - importer must write `owner_hs_id='unassigned'` (the CHECK demands non-NULL for `main_owner`); `full_funnel_*` and `coding_funnel_*` both target pipeline `default` and are separated by `source_family`. `stage_alias.valid_from` uses `date(x) IS x` but `valid_to` uses `date(x) = x` (cosmetic). Scratch size note: a full legacy load is ~0.9 GB, so plan backup / VACUUM time accordingly.

## 4. Suggested order of fixes
1. H1 (loader rule + test), H3 (history fetch + resolution rule) - blocks correct data.
2. H2 (`company_phone`), M3 (verdict columns), M4 (identifier index), M1 (CHECK hardening) - schema edits to 0002 while no real DB exists.
3. M7 (`pull_enabled` default 0, time zone) and M8 (Gmail columns) in 0005.
4. Write the normaliser table from M2 and the CSV rules from M5 into the importer specs; decide M6.
5. L1 guard on the generator.
