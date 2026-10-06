# LeadGenMonolith — Architecture (binding spec)

One repo, one database, one daily report, three HubSpot portals / five funnels.
Facts behind every decision are in `docs/discovery/*.md` (read the relevant one before changing anything).

## 0. Ground truth
| Portal | id | Key env var | Pipelines (id) |
|---|---|---|---|
| MAIN (codebase acquisition) | 246754894 | HUBSPOT_KEY_MAIN | Coding (`default`), CoOps (Global) (`2425754306`) |
| COMPANYOPS (ops-data build portal) | 246897735 | HUBSPOT_KEY_COMPANYOPS | Company Ops Cluster 1 India (`default`), Cluster 2 India (`2464812771`) |
| RAT (Rapid Action Team) | 247485022 | HUBSPOT_KEY_RAT | Rapid Action Team (`2575252183`) (+ stock default w/ 1 sample deal: ignore) |

* Pipeline ids and stage ids are only unique **within a portal** (and 3 stage ids are shared between pipelines in MAIN). Every key in the DB is therefore scoped by `account_id` (our slug: `main`, `companyops`, `rat`) and, for stages, by `pipeline_id`.
* Never look anything up by pipeline *label* (the label "Company Ops Data" no longer exists and broke scripts). Use ids; refresh labels from the API.
* The portals are **independent accounts**. The same company may legitimately exist in two portals (37 domains already do). Never auto-dedupe across portals; link them via a golden `company` record and keep per-portal rows.

## 1. Database: SQLite, single file `db/leadgen.sqlite`
* SQLite 3.51 (FTS5 + JSON1 available), Python 3.9 compatible code only (no `X | Y` types, no `tomllib`).
* PRAGMAs on every connection: `journal_mode=WAL`, `foreign_keys=ON`, `busy_timeout=30000`, `synchronous=NORMAL`.
* **Versioned migrations**: `db/migrations/NNNN_name.sql`, applied in order by `leadgen.db.migrate()`; applied versions + checksum in `schema_migration`. Never edit an applied migration; add a new one. `PRAGMA user_version` mirrors the latest.
* Portable DDL: only types that map to Postgres (`INTEGER`, `TEXT`, `REAL`, `NUMERIC`-as-TEXT where exactness matters), `CHECK` constraints, FKs, partial/expr indexes where useful. Timestamps are ISO-8601 UTC text (`...Z`); IST derived at report time. Money in USD as REAL with a `currency` column. JSON blobs in `*_json` TEXT columns guarded by `CHECK (json_valid(...))`.
* Use `STRICT` tables for the canonical layer.

### Layers (table-name prefix = layer)
| Prefix | Layer | Rule |
|---|---|---|
| (none) / `dim_`, `fact_` … | **canonical** | Normalised, constrained, the only layer the report/app code reads. |
| `hsraw_` | HubSpot raw | One row per (account, object type, hs id) with the full API JSON + `fetched_at`. Lets canonical be rebuilt without re-pulling. |
| `legacy_tam_*`, `legacy_corpus_*`, `legacy_itsvc_*`, `legacy_pipeline_*`, `legacy_radar_*`, `legacy_resolver_*`, … | **verbatim imports** of every old SQLite DB (tables + indexes, same columns, + `_import_run_id`). Nothing is dropped. Canonical rows link back through `origin_ref`. |
| `ext_gsheet_*`, `ext_gmail_*` | external pulls (Sheets catalog/tabs/rows, Gmail threads/messages/labels/attachments, with FTS5 indexes). |
| `rpt_` | report layer: daily funnel facts, report runs, snapshot imports of the legacy JSON history. |
| `ops_` | operations: `ops_import_run`, `ops_sync_state` (HubSpot cursors), `ops_dq_issue` (data-quality findings), `ops_audit_log`, `schema_migration`. |

### Canonical entities (minimum)
`account` · `owner` (HubSpot owner, per account) · `pipeline` · `stage` (+ `stage_alias` for deleted/renamed ids) · `canonical_stage` (the common ladder) and `stage_map` (account/pipeline/stage → canonical stage + live/dead + dead_reason) · `company` (golden) + `company_identifier` (domain, linkedin, CIN, place_id, hs company id… typed, confidence, source) + `company_account_link` (golden company ↔ portal company id) · `contact` (+ `contact_phone` with E.164, `is_indian_mobile`; **+91 mobile gate** is a stored boolean, not an afterthought) · `deal` (per account/pipeline; current stage, owner, source, segment, tam_source, dates) · `deal_stage_event` (**the event store**: account, deal, from_stage, to_stage, entered_at, source_type e.g. CRM_UI/API/AUTOMATION, so any past day can be recomputed) · `deal_contact`/`deal_company` associations · `note` / `engagement` · `lead_source` / `vertical` (TAM category) · `tam_company_verdict` (category, ICP bucket, score, reason — never delete rejects) · `cost_ledger` (Apollo/SignalHire/Apify/Maps credits) · `suppression` (kinds: `never_push` [whale list], `dnc`, `competitor`, …) · `merge_candidate` (weak matches queued for a human, never auto-merged) · `origin_ref` (system, table, id → canonical row).
* `deal_stage_event` needs `propertiesWithHistory=dealstage,hubspot_owner_id,pipeline`: a dealstage history entry carries only the stage id, so the importer resolves `pipeline_id` per event (unique stage id → pipeline-property history → deal's current pipeline, disclosed in `pipeline_basis`; unresolvable events are skipped with a DQ issue, never guessed). History tables hang off `deal` with `ON DELETE RESTRICT`; never `INSERT OR REPLACE`. Details and every schema decision: `docs/review/schema-decisions.md`, `docs/DATA_DICTIONARY.md`.
* Dedup rules: auto-link only on strong keys (root_domain, linkedin company url, CIN, google place id, hs ids). Name-only matches go to `merge_candidate`.
* Do **not** import placeholder values as facts (itsvc `pre-classification-prior` rows, `cost_ledger.usd_est=0`) — mark `is_placeholder=1` or leave in `legacy_*` only.

## 2. Repo layout
```
.env  .env.example  secrets/            credentials (gitignored)
config/                                  accounts.yaml, stage_map.yaml, report.yaml, canonical_stages.yaml (DATA, not code)
db/migrations/ db/leadgen.sqlite         schema + the database
leadgen/                                 python package: config, db, hubspot/, legacy_import/, google/, reports/, cli
reports/daily/<YYYY-MM-DD>/              generated unified report (html, md, csv, json)
data/ (gsheets, gmail, hs_snapshots)     pulled artefacts (gitignored)
legacy/ archive/ chat_context/           untouched copies, git bundles, chat history
docs/                                    ARCHITECTURE.md, DATA_DICTIONARY.md, RUNBOOK.md, discovery/
tests/                                   pytest
```
CLI: `.venv/bin/python -m leadgen <cmd>`: `migrate`, `doctor`, `sync hubspot [--account X]`, `import-legacy [--only X]`, `pull gsheets`, `pull gmail`, `report daily [--date D] [--send]`.

## 3. Hard rules carried over from the three repos (enforced in code)
1. **HubSpot is read-only from the monolith.** No code path under `leadgen/` may POST/PATCH/DELETE to HubSpot except read-only search POSTs. Legacy scripts keep their own `--apply` discipline.
2. **No automated mail except the one daily report**, and mail defaults to **dry-run**. `--send` goes to `bhanu.enamala@lh2.ai` unless `--to` names someone else. No schedulers/cron are installed by this repo.
3. Whale list (`whales.csv`, `Whale_List_Untouched_FINAL.csv`) → `suppression(kind='never_push')`; never pushable.
4. +91 mobile gate; mobile-only (landline/email-only are not wanted leads).
5. Apollo/SignalHire/Apify are credit-spending: the monolith's sync/pull code never calls them. Ask first.
6. India-only sourcing rule for any scraper work.
7. IST day boundaries for reporting; portal timezones differ (COMPANYOPS/RAT US/Eastern, MAIN Asia/Calcutta).
8. Secrets never printed, logged or written outside `.env`/`secrets/`.

## 4. Daily unified funnel report
* Built **from `deal_stage_event`** (rebuildable for any day; fixes the "snapshot gap" and "window must end today" problems). Not from labels, not from legacy snapshots.
* One metric dictionary (see `docs/discovery/funnel-reports.md` §unified spec and its conflict list): every metric defined once in `config/report.yaml`, computed once in `leadgen/reports/metrics.py`.
* Sections: (1) combined headline across all five funnels using the canonical ladder, (2) per-portal/per-pipeline detail with native stage names, (3) per-owner activity, (4) data-freshness & anomalies (sync age, unmapped stages, missing snapshots), (5) Gmail/Sheets context if present.
* Outputs: `reports/daily/<date>/funnel.html`, `.md`, `.csv`, `.json`; facts persisted in `rpt_funnel_daily` so trend/Roll7 reads from the DB.
* Legacy snapshot JSONs are imported to `rpt_legacy_snapshot` and used only to **cross-check** the new numbers.
