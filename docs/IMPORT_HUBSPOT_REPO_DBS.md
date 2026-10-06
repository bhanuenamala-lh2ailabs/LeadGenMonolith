# Import: the SQLite databases of the `hubspot` repo

Module: `leadgen/legacy_import/hubspot_repo_dbs.py` - tests: `tests/test_import_hubspot_repo_dbs.py` - sources: `legacy/hubspot/**` (opened `mode=ro&immutable=1`, never modified).
The companyOps `tam.sqlite3` is imported by `leadgen/legacy_import/tam.py`, not here.

| name (`--only`) | source file (under `legacy/hubspot/`) | mirror prefix | tables | what it is |
|---|---|---|---|---|
| `itsvc` | `itsvc-tam/itsvc.db` | `legacy_itsvc_` | 12 | India IT-services universe from Apollo org search (entities, candidates, rule classifications, site text) |
| `corpus` | `TAMBuildSpecs/_corpus/data/tam_corpus.sqlite` | `legacy_corpus_` | 19 (+3 views) | multi-vertical corpus: `uk_proptech`, `india_cobol_ip` (companies, identifiers, ICP scores + components, EAV indicators) |
| `pipeline` | `lh2-pipeline/data/pipeline.sqlite` | `legacy_pipeline_` | 7 | lh2 pipeline: goodfirms companies, SignalHire people, daily quotas |
| `gmaps` | `lh2-pipeline/data/gmaps_cache.sqlite` | `legacy_gmaps_` | 2 | Google Places tile cache + monthly call counter |
| `resolver` | `godown/founder_id/resolver.sqlite` | `legacy_resolver_` | 3 | founder / CIN resolver |
| `radar` | `godown/shutdown-radar/data/radar.sqlite` | `legacy_radar_` | 10 | Indian startup shutdown radar (candidates, MCA entities, evidence, tiers, liveness, prequal) |
| `searchledger` | `godown/founder_id/search_ledger.db` | `legacy_searchledger_` | 2 | SignalHire `searchByQuery` call ledger |
| `itdirs` | `godown/itdirs/state/scrape_state.sqlite` | `legacy_itdirs_` | 3 | IT-directory scraper state |

## Commands

```bash
cd /Users/bhanu/Desktop/LeadGenMonolith
PY=.venv/bin/python
$PY -m leadgen.db migrate                              # 0001..0014 must be applied
# 1. look first (writes nothing; verbatim = plan, canonical = full mapping inside a rolled-back transaction)
$PY -m leadgen.legacy_import.hubspot_repo_dbs --dry-run
# 2. verbatim mirrors of all 8 databases (~25 s, 2.9 M rows)
$PY -m leadgen.legacy_import.hubspot_repo_dbs --only-verbatim --backup
# 3. canonical mapping (~3 min)
$PY -m leadgen.legacy_import.hubspot_repo_dbs --only-canonical
$PY -m leadgen.db analyze                              # planner statistics after a bulk load
$PY -m leadgen.db doctor
```

Flags: `--db PATH` (default `$LEADGEN_DB` / `db/leadgen.sqlite`), `--only itsvc,corpus` (comma separated or repeated), `--only-verbatim` / `--only-canonical`,
`--dry-run`, `--limit N` (canonical: first N rows of each source table - smoke test), `--batch N` (verbatim rows per transaction, default 20000), `--backup`
(`VACUUM INTO db/backup/...` first), `--legacy-root DIR`, `--json`, `-v`.  Exit code 0 ok, 2 pre-flight/parity failure (nothing half-written: see "Resume").
Logs and reports contain counts and internal ids only - no names, phones, e-mails, URLs (a test greps the log, stdout and `ops_dq_issue`).
Order does not matter between this importer and `tam.py`: both link only through strong identifiers, so whichever runs second links to the first
(`python -m leadgen.legacy_import.hubspot_repo_dbs` before or after `python -m leadgen.legacy_import.tam`).  Run the canonical stage AFTER the HubSpot
sync has created its golden companies if you want the HubSpot `hs_company` / `lh2_domain` identifiers to be linked too (they are `company_account_link`
rows, matched on `root_domain` only when the sync writes that identifier; this importer never touches HubSpot rows).

## Verbatim layer

* Every table of every source DB -> `legacy_<db>_<table>` (migrations 0007-0014), same columns and values, plus `_import_run_id`.  The source **rowid is copied explicitly**
  (PK-less tables: `origin_ref.source_pk` = rowid; for `INTEGER PRIMARY KEY` tables the rowid is the id itself).
* Streaming: keyset pagination in rowid order (`WHERE rowid > ? ORDER BY rowid LIMIT batch`), one transaction per batch, parents before children (FK graph of the mirrors).
* **Idempotent / resumable.**  One `ops_import_run` per (source_name `hubspot_repo:<db>:verbatim`, sha256 of the source file).
  * run `succeeded` and every table still matches -> "already imported", nothing written, no new run row;
  * run unfinished (`running` / `aborted` / `failed` / `partial`) -> **resumed**: the same run id, each table continues after `max(rowid)` of this run, so a re-run can never duplicate;
  * mirror holds rows of another run / hand-made rows (e.g. the source file changed) -> `ImportFailure`, nothing written (reverse the old run with `DELETE ... WHERE _import_run_id = ?` yourself; the importer never deletes from `legacy_*`).
* **Parity** (checked per table after loading, failure -> run `partial` + exit 2): row count, `SUM(rowid)` and the non-NULL count of every column, source vs mirror; corpus views `v_accounts`, `v_corpus_summary`, `v_source_health` are not copied (the migration recreates them over the mirrors) - their row counts are compared with the source views.
* **Foreign keys** are switched OFF (outside any transaction) for the load and ON again afterwards, because the sources already violate their own FKs.  After the load every `PRAGMA foreign_key_check` hit is recorded as `ops_dq_issue(rule_code='legacy_fk_orphan', severity='warn')` and stays in the table: radar `candidate_entity` rowid 10783 and 10785 (CINs without an `entity` row, both human `override` decisions).  `db.doctor()` treats `legacy_*` orphans as warnings.
* Virtual / FTS tables (none in these 8 databases) would be skipped and listed in the report (`skipped_virtual`).

## Canonical layer

Reads the source databases directly (not the mirrors), so the stages are independent.  Every row of every canonical table below has an `origin_ref`
(`source_system` = db name, `source_table` = ORIGINAL table name, `source_pk` = pk, composite joined with `|`, rowid for PK-less tables); `company_signal` has no origin_ref entity type, its provenance is `source_system` + `import_run_id`.

### Linking rules (companies)

1. Keys are normalised with `leadgen.norm` (`norm_domain`, `norm_linkedin_company`, `norm_cin`/`norm_llpin`; Companies House numbers upper-cased; Apollo org ids lower-cased).  A key that cannot be normalised is not stored; it is counted in `ops_dq_issue(identifier_unnormalisable)`.
2. **Strong keys only auto-link**: `cin`, `llpin`, `companies_house`, `google_place_id`, `apollo_org`, `linkedin_company`, `root_domain` (priority in that order).  A record is linked to the first golden company that owns any of its strong keys, else a new golden company is created and its keys become `company_identifier` rows (first of each type `is_primary`).
3. **Shared keys never link.**  A domain / LinkedIn slug claimed by >= 3 distinct entities of one source (hyatt.com x 13, iima.ac.in x 12 in itsvc), and a built-in list of generic hosts (gmail.com, linkedin.com, goodfirms.co ...), are stored as **weak** identifiers (`is_strong = 0`, never primary) on each company and never used to link.  Keys with source confidence < 0.9 (corpus `domain` rows at 0.7) are weak too; unconfirmed radar CIN matches (fuzzy, `is_confirmed = 0`) are weak.
4. **Conflicts**: a record whose strong keys point at two different golden companies is linked by the highest-priority key and `merge_candidate(match_kind='identifier_conflict')` is queued; the foreign key is not stolen.
5. **Name-only matches** (same name after dropping legal-form words, different source systems, compatible country) -> `merge_candidate(match_kind='name_norm', score 0.60-0.95, evidence_json = name key / sources / country)`; groups larger than 6 are skipped (reported).  Never merged automatically.
6. Linking fills only NULL golden fields (never overwrites); `origin_ref.match_method` records the key that linked (`root_domain`, `cin`, ...) or `new`, and the confirming source also gets an `origin_ref` on the `company_identifier` row.

### Mapping per database

| source | canonical |
|---|---|
| **itsvc** `entities` (+ `candidates` joined on linkedin+name, `site_text`, `classifications`) | `company` (name, legal name, headcount band, founded year from `extra_json`, first_seen; `hq_country` stays unknown - the Apollo universe is "India search", not provably India HQ); identifiers `root_domain`, `linkedin_company`, `apollo_org`, `cin` (156 CINs scraped from `site_text`, confidence 0.8), gstin/pan when well-formed; `candidates.phone_e164` -> `company_phone` (31,616 are not E.164: normalised or kept raw); the 206 real `rules` classifications -> `tam_company_verdict` (vertical `india_it_services`, bucket `unscored` because itsvc has no ICP bucket, score/tier from `rules_applied_json`, flags in `details_json`); the **61,593 `pre-classification-prior` rows are NOT imported** (stay in `legacy_itsvc_classifications`; one aggregate DQ issue); `site_text` -> signals `site_fetch_status`, `site_http_code`; `suppression` / `cost_ledger` mapped (0 rows today) |
| **corpus** `company` + `company_identifier` + `icp_score` (+ components) + `indicator` | `company` (CH status mapped to the 7-value enum, original in `status_detail`; GB/IN country; headcount; founded year); identifiers `companies_house`, `apollo_org`, `root_domain`, `linkedin_company`; verticals `uk_proptech`, `india_cobol_ip` (with `origin_ref`); `icp_score` -> `tam_company_verdict` (`Fit`->fit, `Maybe`->maybe, `Unclear`/`Weak`->`unscored`, original label in `native_bucket`, band P1-P4, components in `details_json`, `scored_by` rules_v1 -> method `rules`); `indicator` (652k) -> `company_signal` by `indicator_def.slug`; `contact`, `suppression`, `budget_ledger` mapped (0 rows today); `company_source`, `source*`, `stage_run`, `verify_item`, `icp_rule/segment`, `raw_document` stay legacy-only |
| **pipeline** `companies`, `people`, `quota` | `company` keyed by domain (hq India -> `IN`/`yes`); gate -> `tam_company_verdict` (vertical `india_it_services`: gate fail -> `out` with the gate reason, gate pass -> `unscored`, never `fit`); `people` -> `contact` (name, role, e-mail, person LinkedIn `in/<slug>`, `attrs_json` = confidence/sources/notes) + `contact_phone` (E.164, type, +91 gate); a person whose domain has no company is kept with `company_id` NULL and reported; `quota` -> `cost_ledger` (signalhire `search_call` / `credit`, usd NULL); `raw_listings`, `cache`, `crm_feedback` (0 rows) legacy-only |
| **gmaps** `tiles`, `usage` | every place -> `company` + `google_place_id` (+ `root_domain` from `websiteUri`, linking to existing companies) + `company_phone` (Google `nationalPhoneNumber`) + signals `gmaps_rating`, `gmaps_review_count`; places repeated in overlapping tiles resolve to the same company; `usage` -> one `cost_ledger` row (google_maps `places_call`, month total at the first of the month, usd NULL) |
| **resolver** `companies` | `company` by domain + `cin`; founder (`data` JSON) -> `contact` (title, person LinkedIn, `attrs_json` = DIN, identity_status, resolver confidence, source quote/urls); signals `resolver_stage`, `resolver_status` |
| **radar** `candidate` + `candidate_entity` + `entity` + `scored` + `prequal` + `liveness` + `evidence` | one `company` per candidate brand; confirmed CIN / LLPIN matches (`exact`, `exact_api`, `override`, `is_confirmed`) -> strong `cin` / `llpin` (16 LLPINs live in the `cin` column), unconfirmed -> weak; legal name / status (`Struck Off`->`struck_off`, `Under Liquidation`->`liquidation` ...) / registration year / state from `entity`; liveness domain -> strong `root_domain`; legal entities no candidate points at get their own company; **signals** `distress_tier` (+confidence, rationale), `shutdown_date`, `shutdown_year`, `shutdown_reason`, `sector`, `investors`, `prequal_*`, `github_org`, `liveness_score`, `dns_resolves`, `http_status`, `ssl_expired`, `discovery_source`, `evidence_<kind>` (count + latest snippet/url) - radar tiers are distress signals, not ICP verdicts |
| **searchledger** `calls` | one `cost_ledger` row per call (signalhire `search_call`, qty 1, usd NULL; the HTTP status, profiles and size are in `note`); `events` legacy-only |
| **itdirs** | scraper state only: verbatim layer only (one info DQ issue) |

### Phones, costs, suppression

* `contact_phone` / `company_phone`: E.164 via `norm.norm_phone_e164` (raw kept; NULL e164 when unsure).  `phone_type` comes from `phonenumbers` when it is installed, otherwise from a deliberately conservative table: `+91 9xxxxxxxxx` -> mobile; `6/7/8xxxxxxxxx` -> mobile unless the number starts with an overlapping fixed-line STD code (79 Ahmedabad, 80 Bangalore, 15 three-digit codes) -> `unknown`; `+91 1-5xxxxxxxxx` -> landline; everything non-Indian -> `unknown`.  Dummies (`+91 12345 67890`, `0123456789`, repeated digits ...) -> `is_placeholder = 1`, type `unknown`.  The +91 gate (`is_indian_mobile`) is the schema's generated column: only `mobile` opens it; `unknown` numbers show up in `v_phone_gate_review`.  Installing `phonenumbers` and re-running only improves the classification of new rows (existing rows are not rewritten).
* `cost_ledger`: **usd is NULL / `usd_basis='unknown'` unless the source has a positive price** (a source 0 is "not recorded"); quantities are real (`qty`, `credits` for SignalHire credits).  Nothing is a placeholder.
* `suppression` (corpus + itsvc, both empty today): `match_type` is taken from the source column/shape, values go through `leadgen.norm`; **a value that cannot be normalised is NOT imported and raises an `error`-severity DQ issue** (a silently wrong list entry would be a bypass).

## Data-quality issues written (`ops_dq_issue`, fingerprint `<rule>|hubspot_repo|<db>`, one aggregate row each with up to 5 example source pks)

`legacy_fk_orphan` (one row per orphan), `placeholder_not_imported`, `shared_key_weak`, `identifier_unnormalisable`, `email_unnormalisable`, `phone_not_e164`, `company_without_name`, `canonical_row_rejected` (a row refused by a canonical CHECK - rolled back on its own SAVEPOINT, the rest continues), `contact_without_company`, `verdict_company_missing`, `indicator_orphan`, `suppression_unnormalisable`, `cost_usd_unknown`, `cost_without_time`, `name_group_too_large`, `no_canonical_mapping`.

## Decisions

* The verbatim and canonical stages share no state besides `ops_import_run`; canonical reads the sources, so `--only-canonical` works on a database whose mirrors are not loaded (it warns that `origin_ref` will point at legacy rows that are not there yet).
* Idempotency of the canonical stage is by `origin_ref` (an already-bound source row is skipped), not by run fingerprint; every invocation writes a new `ops_import_run` row (its counters show `rows_skipped`), identifiers/phones/signals/suppression use `ON CONFLICT DO NOTHING` on their natural unique keys.  A row that violates a canonical constraint is rolled back on its own SAVEPOINT and counted (`canonical_row_rejected`); in-memory caches are rolled back with it.
* No `INSERT OR REPLACE`, no deletes from `legacy_*`, no HubSpot / Google / vendor calls, no secrets read.
* itsvc `hq_country` stays unknown; pipeline gate fail -> `out` (reject kept with reason), pass -> `unscored`; corpus `Weak`/`Unclear` -> `unscored` (never `fit`).

## Validated run (fresh migrated scratch database, real sources, 2026-10-04)

Verbatim 21.7 s, canonical 2 min 55 s; re-run: verbatim 0.5 s ("already imported"), canonical 47 s with **identical** row counts in every table; `doctor` ok (the 2 documented radar FK orphans are the only warnings).

Verbatim, source rows = loaded rows = mirrored rows, content check (rowid sum + non-NULL counts per column) ok for **all 64 tables, 2,905,881 rows**:

| db | rows | db | rows |
|---|---|---|---|
| itsvc | candidates 61,799 - captures 61,799 - classifications 61,799 - entities 61,799 - raw_records 61,799 - site_text 7,824 - (cities, cost_ledger, entity_members, er_decisions, partner_badges, suppression 0) | pipeline | cache 1,391 - companies 5,369 - people 1,480 - quota 6 - raw_listings 5,455 - (crm_feedback, no_domain 0) |
| corpus | company 302,963 - company_identifier 311,027 - company_source 303,087 - icp_score 301,345 - icp_score_component 640,833 - indicator 652,128 - indicator_def 57 - icp_rule 33 - icp_segment 11 - raw_document 2 - source 28 - source_attempt 6,142 - stage_run 13 - verify_item 32 - vertical 2 - (budget_ledger, company_link, contact, suppression 0); views v_accounts 302,963 / v_corpus_summary 2 / v_source_health 28 = source | radar | candidate 10,957 - candidate_entity 10,809 (2 orphans loaded + reported) - entity 10,816 - evidence 11,018 - liveness 83 - prequal 400 - run_stat 11 - scored 10,957 - (backfill_progress, dpiit_startup 0) |
| gmaps | tiles 44 - usage 1 | resolver | companies 420 - counters 4 - walls 27 |
| searchledger | calls 2,038 - events 39 | itdirs | pages 34 - (counters, redirects 0) |

Canonical result (all eight databases, empty target): company 378,704 - company_identifier 508,738 - company_phone 32,128 - contact 1,830 - contact_phone 540 - tam_company_verdict 306,920 - company_signal 703,850 - cost_ledger 2,045 - merge_candidate 226 - origin_ref 1,253,065 - vertical 3 - suppression 0 (both source suppression tables are empty).  0 rows rejected.

Cross-store overlap actually achieved (golden companies that carry an origin from two or more databases: 2,980 in 2 + 229 in 3 + 11 in 4 = **3,220**):

| pair | companies | link keys used (links made) |
|---|---|---|
| itsvc x pipeline | 2,061 | root_domain 2,150 (pipeline side) |
| corpus x itsvc | 679 | apollo_org 671 + root_domain 28 (identifiers confirmed by 2+ sources: apollo_org 671, linkedin_company 671, root_domain 3,220) |
| pipeline x resolver | 371 | root_domain 380 (resolver side) |
| gmaps x itsvc / pipeline / resolver / corpus | 205 / 101 / 15 / 7 | google_place_id 994 (same place in overlapping tiles), root_domain 268 |
| corpus x pipeline / resolver | 149 / 23 | root_domain |
| itsvc x resolver / radar | 118 / 4 | root_domain, cin 1 |

These match the discovery measurements (itsvc x pipeline 2,061, itsvc x corpus 679, pipeline x resolver 372).  Radar links to the others only through 4 domains (+2 shared CINs between radar candidates, 1 resolver CIN): the shutdown universe is a different population (struck-off startups).  `merge_candidate`: 224 name-only (`name_norm`), 1 `identifier_conflict` (a resolver CIN owned by another company), 1 contact `linkedin_person`.  Weak (not linked) identifiers: 92 shared itsvc domains, 4 corpus, 7 gmaps, 5 unconfirmed radar CINs.

DQ issues after the run: `legacy_fk_orphan` 2 (radar rowid 10783, 10785), `phone_not_e164` 179 phones kept raw, `identifier_unnormalisable` 9, `placeholder_not_imported` 1 (61,593 rows), `cost_usd_unknown` (2,045 cost rows), `shared_key_weak`, `no_canonical_mapping` (itdirs).
Phone gate on the scratch run: company_phone mobile 21,249 / landline 4,838 / unknown 5,808 / placeholder 233 (itsvc + Google Places); contact_phone mobile 357 / landline 28 / unknown 155 (pipeline.people; all other numbers are non-Indian or ambiguous).

## Open items

* `phonenumbers` is not installed: the 6/7/8-series classification is the conservative approximation above.  `v_phone_gate_review` lists what stays closed.
* itsvc company phones are office numbers: they open `company_phone` but never `contact_phone`.
* The pipeline `segment` column holds goodfirms description text, not a segment: it is kept verbatim in `legacy_pipeline_companies` only.
* HubSpot sync linkage (`company_account_link`) is not done here; once HubSpot companies are canonical, run `--only-canonical` again - it is idempotent and links nothing it already linked.

