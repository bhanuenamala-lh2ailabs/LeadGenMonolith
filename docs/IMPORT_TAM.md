# Legacy import: companyOps TAM (`tam.sqlite3`)

Code: `leadgen/legacy_import/tam.py` - tests: `tests/test_import_tam.py` - source: `legacy/companyOps/tam/data/tam.sqlite3` (opened `mode=ro&immutable=1`; never modified; `tam.db`, the `.bak` and the `-wal`/`-shm` files are ignored).

## Run

```
.venv/bin/python -m leadgen.legacy_import.tam --dry-run                 # counts only, writes nothing
.venv/bin/python -m leadgen.legacy_import.tam --only verbatim           # 17 tables -> legacy_tam_*
.venv/bin/python -m leadgen.legacy_import.tam --only canonical          # golden companies, verdicts, signals, costs, suppression
.venv/bin/python -m leadgen.legacy_import.tam --only link               # crm_pushes -> COMPANYOPS deals (re-run after the HubSpot sync)
.venv/bin/python -m leadgen.legacy_import.tam                           # all three in order
```

Options: `--db PATH` (default `$LEADGEN_DB` or `db/leadgen.sqlite`), `--source PATH`, `--batch-rows N` (verbatim, default 50,000), `--reload-verbatim`, `--report-json PATH`, `-q`.
The target must already be migrated (`python -m leadgen.db migrate`); the importer never creates or migrates a database. Output carries counts and internal numeric ids only (no names, domains, e-mails, secrets).
Measured on a scratch copy: all three stages 26 s wall (verbatim 7 s incl. the 640 MB sha256, canonical 12 s).

## Stage 1 - verbatim layer (`legacy_tam_*`, migration 0006)

* Every one of the 17 tables, same columns and values, plus `_import_run_id` -> `ops_import_run` (kind `legacy_import`, source_name `tam`, `source_fingerprint` = sha256 of the source file). Reverse a load with `DELETE FROM legacy_tam_<t> WHERE _import_run_id = ?`.
* Streamed (`fetchmany(2000)`), committed per 50,000 rows or 32 MB, whichever comes first. Foreign keys are switched OFF outside any transaction for the load, restored afterwards; `PRAGMA foreign_key_check` per table; each orphan -> `ops_dq_issue(rule_code='legacy_fk_orphan')`, never dropped (the source has 0).
* Tables whose key is not a rowid alias (`signals`, `classifications`, `company_categories`, `category_sources`, `signalhire_results`) get their source `rowid` copied explicitly (origin_ref contract).
* Resumable / idempotent per table: `ops_sync_state(source_system='other', scope='tam', object_type=<table>, cursor_name='verbatim_sha256')` holds `cursor_value = source sha256`, `last_status='ok'`. A table that is complete for the same sha256 *and* has the same row count is skipped; an unfinished table (`running`/`error`) is emptied and reloaded in full; a changed source file reloads everything. `--reload-verbatim` forces it.
* Verification after every table: row count AND the sum of the byte length of every column (`length(CAST(col AS BLOB))`) must equal the source, otherwise the run fails. A one-off md5 over `repr(row)` in rowid order, source vs target, matched for all 17 tables (values, types and rowids identical).
* Pre-flight: every source table must have its `legacy_tam_` twin with exactly `source columns + _import_run_id`; the source schema sha is compared to the one in the 0006 header (warning only).

Scratch run, source vs loaded (all equal, checksums verified, 0 FK orphans):

| table | rows | table | rows |
|---|---|---|---|
| categories | 13 | pages | 43,523 |
| stage_runs | 31 | funnel_events | 224,846 |
| companies | 41,363 | crm_pushes | 1,126 |
| category_sources | 15 | cost_ledger | 2,634 |
| company_categories | 28,625 | signalhire_results | 529 |
| classifications | 16,905 | contacts / outreach_batches / salesnav_import / async_jobs | 0 each |
| signals | 17,032 | **total** | **426,099** |
| raw_sources | 49,457 | | |

## Stage 2 - canonical layer

Source of truth for this stage is the source file (not `legacy_tam_*`), so a dry run can simulate it on an in-memory schema. Each batch is one transaction; a re-run skips everything already imported (keyed by `origin_ref`, or by the table's UNIQUE index for `company_signal`/`suppression`). Provenance: every canonical row has `origin_ref(source_system='tam', source_table=<original name>, source_pk)`; `company_signal` has no origin_ref entity type, so its provenance is `source_system='tam'` + `import_run_id`.

| tam | canonical | rule |
|---|---|---|
| `categories` (13) | `vertical` | slug = key; `hubspot_segment` kept (unique); country `IN`; an existing vertical with the same slug, else the same hubspot_segment, is reused |
| `companies` (41,363) | `company` + `company_identifier` | one golden company per tam row **unless a strong identifier already belongs to a golden company** (then the row is linked to it: this is how tam rows meet companies created by the HubSpot sync, by `root_domain`). Name, hq (`India`/`IN` -> `IN`), `india_hq`, headcount, funding, first/last seen (UTC ms). Name-only matches are never auto-linked. |
| identifiers | `root_domain` (`norm_domain`: strips quotes / zero-width, punycode; `domain_confidence` -> confidence), `linkedin_company` (`norm_linkedin_company`), `google_place_id`, `apollo_org`, `cin`/`llpin`, `alt_domains` (weak) | strong, primary for companies created by this run. Unnormalisable values are not loaded (kept in `legacy_tam_companies`) and counted in a DQ issue. If a strong key of a row is owned by a *different* golden company it is stored as weak evidence and a `merge_candidate(match_kind='strong_identifier_conflict')` is queued (never silently overwritten) |
| `merged_into` (13,640 tombstones, 24 chains of depth 2) | `company.merged_into_company_id` + `merged_at` | tombstones are kept as company rows (no identifiers) and pointed at their parent; follow the chain with `v_company_survivor`. `merged_at` = tam `last_seen_at` (the source has no merge timestamp). Skipped if it would cycle or if the tombstone's golden company carries identity of its own |
| `company_categories` + `classifications` (28,625) | `tam_company_verdict` | **nothing dropped**: `Fit/Maybe/Out` -> `fit/maybe/out`; no bucket (21,728 keyword-scored, not yet judged) -> `unscored` with the numeric `score` kept, never `fit`. `native_bucket` = source label, `priority_tier` A-D, `segment`, `confidence`, `reason_code` = `funnel_bucket`, `reason` = text `funnel_reason` else classifier `notes`, `evidence_quote/url`, `model`, `prompt_version`, `decided_at` = classified_at / updated_at. `verdict_method` = `llm` when a classification bucket exists else `rules`. `details_json` = funnel stage/bucket/updated, regulated_status, regulatory_sensitivity, parked_as, parsed score components (`funnel_reason` JSON), the whole classification record (keyword score/hits, segments, classifier bucket, company_type, has_own_tech_product, pm_tooling_signals, notes) and the tam ids. company_categories wins over classifications (they disagree on 119 Maybe/Fit/Out rows; the classifier's own bucket is kept in `details_json.classification.classification_bucket`). A second verdict for the same (golden company, vertical) is stored with `is_current = 0`. |
| `signals` (17,032) | `company_signal` | EAV, NULLs skipped: `uses_jira/slack/asana/clickup/gworkspace` (bool; 0 is a real "not detected"), `jira_scrum_mentions`, `open_jobs` (num), `mx_provider` (text); `observed_at` = signals.updated_at. `mx_records`/`techs` stay legacy-only |
| `cost_ledger` (2,634) | `cost_ledger` | all rows are Apollo; `usd_est = 0` -> `usd NULL`, `usd_basis 'unknown'`, `is_placeholder 0`; the legacy `qty` holds credits, so `credits = qty` for every unit and `qty` = calls (`phone_reveal`: qty/8 = 1 call, 8 credits). Total credits 8,266 (matches the discovery doc). `vertical_id` from category, `run_ref = 'stage_run:<id>'`, `note` kept |
| `crm_pushes` statuses | `suppression` | `pushed` -> `delivered`, `removed_off_icp` -> `off_icp`, `removed_too_big` -> `too_big`, `removed_duplicate` -> `duplicate` (per the dictionary), `account_id='companyops'`, `list_name='tam.crm_pushes'`; match on domain, else LinkedIn, else company name (the 403 back-filled pushes without a company id carry only the name in `note`). `contact_corrected` (22) creates none |
| `funnel_events` (224,846) | **legacy only** | see decisions |
| `contacts`, `outreach_batches`, `salesnav_import`, `async_jobs` | none | 0 rows. `signalhire_results`, `pages`, `raw_sources`, `stage_runs`, `category_sources` stay in `legacy_tam_*` (no canonical home) |

Scratch run result: 41,359 `company` rows (41,363 tam rows; 4 rows are quoted/zero-width spellings of an earlier row's domain and fold into it), 33,136 `company_identifier` (20,191 root_domain, 12,866 linkedin_company, 79 apollo_org), 13,640 tombstone pointers, 13 `vertical`, 28,625 `tam_company_verdict` (fit 1,940 / maybe 2,072 / out 2,885 / unscored 21,728; 4 `is_current = 0`), 134,916 `company_signal`, 2,634 `cost_ledger` (all `usd NULL`), 1,101 `suppression` (delivered 1,068 [666 by domain, 402 by name], off_icp 25, too_big 3, duplicate 5), 107k `origin_ref`. `doctor`: ok, no `rows_without_origin`.

## Stage 3 - `link_crm_pushes()` (separable, re-runnable)

Reads only the target database (`legacy_tam_crm_pushes`, `deal` of account `companyops`, `origin_ref`). For each push whose `deal_id` exists in `deal`:
1. `origin_ref(deal <- tam.crm_pushes#id, match_method='hs_deal_id')`;
2. `origin_ref(company <- tam.crm_pushes#id, match_method='crm_push_deal')` on the tam golden company, or (the 403 pushes without a tam company) on the deal's own company;
3. if the deal's golden company is a different survivor than the tam company -> `merge_candidate(match_kind='tam_crm_push_deal', score 0.9)` for a human; never an auto-merge.

Pushes whose deal id is not in `deal` -> one `ops_dq_issue` each (`rule_code='tam_push_unmatched_deal'`, fingerprint `tam_push_unmatched_deal|<push id>`, severity warn); a later run marks them `resolved` once the deal appears. The `deal` table is never written (the HubSpot sync owns it). `company_account_link` cannot be created from a push (it needs the HubSpot *company* id, which `crm_pushes` does not carry): the link is `origin_ref` on deal and company plus the deal's own `deal.company_id`/`deal_company`. Dry run: counts of matched/unmatched deals.

## Decisions

* **funnel_events are not `deal_stage_event`.** They are 224,846 sourcing-funnel steps of a *company* (`score`, `resolve`, `classify`, `crawl`, `crm_push` ...) with TAM bucket vocabulary; they are not HubSpot stage moves (no deal, no portal, no pipeline/stage ids) and putting them in the event store would corrupt the daily funnel report. They stay in `legacy_tam_funnel_events`; the final per-(company, category) state is in `tam_company_verdict.details_json` (`funnel_stage`, `funnel_bucket`). Join recipe: `legacy_tam_funnel_events.entity_id` (entity_type `company`) -> `origin_ref(entity_type='company', source_system='tam', source_table='companies', source_pk=CAST(entity_id AS TEXT))`.
* **Strong-key auto-link also inside tam**: two tam rows that normalise to the same domain become ONE golden company (4 pairs: adtelligent.com, pubnative.net, google.com, openx.com). Their second verdict for the same vertical is kept with `is_current = 0`.
* **0.8 domain confidence stays strong** (1,965 resolver-found domains): the confidence is recorded on the identifier; change `is_strong` there if you want them to need review.
* `company.status` is `unknown` (tam has no status); `company_phone` is not imported (tam `office_phone` is empty everywhere; a DQ issue fires if it ever is not).
* Suppression rows are created by the canonical stage (they depend only on companies and pushes, not on deals). Skip them by not running the canonical stage on a database where they are unwanted; they are idempotent and removable via `origin_ref` (`entity_type='suppression', source_table='crm_pushes'`).

## Data-quality findings (aggregated `ops_dq_issue`, fingerprint `tam_import|<rule>`, ids only)

7,109 tam companies that are not merged away have no domain / LinkedIn / place id / apollo id (`tam_company_without_strong_identifier`, info: name-only, needs resolution); 4 pairs share a normalised domain (`tam_rows_share_strong_identifier`, info); 4 duplicate-key verdicts (`tam_verdict_not_current_duplicate_key`, info). Zero: unnormalisable domains/LinkedIn URLs, unmapped countries, FK orphans, merge cycles, rejected cost rows. Other rules that can fire on other data are listed in `CANONICAL_DQ_RULES`.

## Re-run / reversal

Everything is idempotent. To undo a stage: `DELETE FROM legacy_tam_<t> WHERE _import_run_id = ?` (verbatim) and, for canonical rows, delete by `origin_ref`/`import_run_id` (child tables first: verdicts, signals, identifiers, suppression, cost_ledger, then companies; `company_identifier`, `company_signal` are RESTRICT-linked to `company`).

## Not done / open

* No name-only duplicate detection inside tam (928 repeated `name_norm` among non-merged rows are different domains; tam's own dedupe already tombstoned the obvious ones) - candidates for a later `merge_candidate` pass.
* `tests/test_schema_hardening.py::test_data_dictionary_is_current` fails in the shared tree because `docs/DATA_DICTIONARY.md` is stale relative to the migrations (not touched by this importer).
