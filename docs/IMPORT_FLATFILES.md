# Flat-file importer (CSV / XLSX / JSON "excel-sheet databases")

Code: `leadgen/legacy_import/flatfiles.py` · migration: `db/migrations/0015_flatfile_import.sql` · tests: `tests/test_import_flatfiles.py`.

The three legacy repos each kept their own "databases" as spreadsheets and JSON dumps. This importer moves all of it into the one SQLite database in four
independent, re-runnable passes. Nothing under `legacy/` is ever modified; no HubSpot / Google call is made; cell values are never printed (counts only).

| Pass (`--only`) | Reads | Writes | Notes |
|---|---|---|---|
| `snapshots` | RAT `snapshots/rat_*.json`, hubspot `crm_mirror/data/snapshots/*.json`, companyOps `crm_mirror/data/snapshots/full_funnel_cluster{1,2}_*.json` | `rpt_legacy_snapshot` | for cross-checking the recomputed report only |
| `whale` | the four whale CSVs | `suppression(kind='never_push')`, `origin_ref`, `ops_audit_log` | HARD RULE: never pushable |
| `files` | every business CSV / XLSX (all sheets) + the stale HubSpot JSON mirrors | `ext_file_catalog`, `ext_file_row` (migration 0015) | staging only, never canonical |
| `link` | `ext_file_row` + `company_identifier` + `contact` | `ext_file_row.company_id/contact_id`, `suppression.company_id` | strong keys only; re-run after other importers |

## Running it

```bash
.venv/bin/python -m leadgen.db migrate                      # applies 0015 (takes the usual pre-migration backup)
.venv/bin/python -m leadgen.legacy_import.flatfiles --dry-run             # parse everything, print counts, write nothing (works before 0015 too)
.venv/bin/python -m leadgen.legacy_import.flatfiles --only snapshots
.venv/bin/python -m leadgen.legacy_import.flatfiles --only whale          # do this first: it is the safety rule
.venv/bin/python -m leadgen.legacy_import.flatfiles --only files          # ~45 s, ~620k rows, adds roughly 340 MB to the database
.venv/bin/python -m leadgen.legacy_import.flatfiles --only link           # again after company / contact importers have run
```

Options: `--db PATH` (default `$LEADGEN_DB` or `db/leadgen.sqlite`), `--legacy-root DIR`, `--include SUBSTR` (files pass: only matching paths),
`--force` (reload even if the sha256 is unchanged), `--keep-duplicates` (store rows of byte-identical copies too), `--full` (print the per-file list). Exit
code 2 = database missing / not migrated. The write passes refuse to run without migration 0015; a missing database is never created.

Recommended order on the real database: `migrate` -> `--only whale` -> `--only snapshots` -> `--only files` -> (company/contact importers) -> `--only link`.

## 1. Snapshots -> `rpt_legacy_snapshot`

241 files, 8 series. `payload_json` is the whole file; `flow_json`, `current_state_json`, `dashboard_flow_json`, `cumulative_json`, `engaged_deal_ids_json`
(+ generated `engaged_count`) are the parsed blocks (NULL when the file has no such block). `is_bootstrap` / `is_seeded` copy the file's `bootstrap` /
`seeded` flag. The legacy `cumulative` chain is kept but is not trusted for headline numbers (see `docs/discovery/funnel-reports.md`).

| Files | `source_family` | account | `pipeline_id` | `owner_hs_id` | Count |
|---|---|---|---|---|---|
| RAT `snapshots/rat_<d>.json` | `rat` | `rat` | `2575252183` | - | 5 |
| hubspot `full_funnel_<d>.json` | `main_full` | `main` | NULL (whole portal) | - | 27 |
| hubspot `coding_funnel_<d>.json` | `main_coding` | `main` | `default` | - | 16 |
| hubspot `full_funnel_coopsglobal_<d>.json` | `main_coopsglobal` | `main` | `2425754306` | - | 7 |
| hubspot `full_funnel_owner_<id>_<d>.json` | `main_owner` | `main` | NULL (see below) | file name (`unassigned` allowed) | 135 |
| hubspot `hubspot_2026-07-29.json` | `main_hubspot_mirror` | `main` | NULL | - | 1 (counts only: deals 1,197 / contacts 1,358 / companies 871) |
| companyOps `full_funnel_cluster1_<d>.json` | `companyops_cluster` | `companyops` | `default` | - | 25 |
| companyOps `full_funnel_cluster2_<d>.json` | `companyops_cluster` | `companyops` | `2464812771` | - | 25 |

* `snapshot_day` = the file's `date` (or `generated`); a disagreement with the file name raises `ops_dq_issue legacy_snapshot_date_mismatch`. Non-numeric metric
  values -> `legacy_snapshot_non_numeric_metric`; unparsable file -> `legacy_snapshot_unreadable` (error, run `partial`).
* The per-owner files carry no pipeline in the file; they predate the CoOps pipeline and have the Coding vocabulary, but `pipeline_id` is left NULL rather than guessed.
* Idempotent on the table's unique key `(family, account, pipeline, owner, day)`: same `source_sha256` = skip, changed = in-place update.
* `v_legacy_snapshot_rows` flattens the stored blocks for `rpt_legacy_crosscheck`.

## 2. Whale list -> `suppression(kind='never_push')`

Files: `hubspot/reserve/whale_list_27_NEVER_PUSH_TO_HUBSPOT.csv`, `hubspot/temporary/Whale_List_Untouched_FINAL.csv`, `hubspot/temporary/Whale_List_30.csv`,
`hubspot/godown/prequal/prequal_out/whales.csv` (any other `whale*.csv` under `legacy/` is picked up too). The union is imported: the 30-company lists include 3 companies that were
later found in HubSpot and dropped from the 27-company list; they stay blocked.

Per row, through `leadgen.norm`: `domain` -> `domain`, `name` -> `company_name`, `contact_linkedin` -> `linkedin_person` (or `linkedin_company`), `contact_email` -> `email`, `contact_phone` -> `phone`.
Un-normalisable values are never stored raw (fail closed: reported as `whale_value_rejected` / counted in `rows_without_any_value`). Rows are global (`account_id` NULL),
`reason` = standing rule text, `list_name` = the contributing file names, `origin_ref` (`csv:<file>`) per row and value, one `ops_audit_log` row (`suppression.add`, counts only) per run that changed anything.
A deactivated whale row is **re-activated** by the next run (and a `whale_reactivated` warning is raised): the hard rule beats a stale switch-off. `link_files()` later fills `suppression.company_id`
for whale domains that are strong company keys. The whale CSVs are also staged in `ext_file_*` with `is_never_push = 1` - for reference only; every outbound path must still call `leadgen.suppression.assert_pushable()`.

## 3. Flat files -> `ext_file_catalog` / `ext_file_row`

Discovery walks `legacy/{companyOps,RapidActionTeam,hubspot}` (skipping `node_modules`, `.venv`, `.git`, ...) for `.csv .tsv .xlsx .xlsm .xls` plus the nine JSON mirror / index files below.
Per file: sha256 + text-encoding detection in one streaming pass (utf-8 / utf-8-sig / cp1252 / latin-1, whole file checked), delimiter from the header line (`, ; tab |`), csv streamed
(multi-line quoted cells, NUL bytes, huge cells), xlsx via openpyxl read-only (every sheet, cached values, dates -> ISO text, whole-number floats -> int), leading one-cell title rows skipped,
numeric-looking header rows treated as data (`col_N` headers), duplicate headers suffixed `__2`. `row_json` omits empty cells; `rows` counts non-blank data rows; `columns_json` = `{sheet: [headers]}`.

**Roles** (`role_for`, first match wins; detail in the code): `pool` = reserve / holding / held-pool / RAT `leads/` / `lanes/` / whale lists; `source_of_truth` = `data/imports`, `exports/source_files`, `audit/`, IBBI manual,
`tam/seeds`, `index/sheet_worked_exclude.json`; `mirror` = `ceo_reality_check` (HubSpot Main extract 2026-08-30), archived `DEALS_*` extracts, the `crm_mirror/data` JSON; `regenerable` = `tam/exports`, `shutdown-radar*` out/cache/raw,
`gujarat_qualify/apify_out*`, `prequal_out`, `itsvc-tam/out`, `_corpus/data`, `companyOps/pipeline`; everything else under `exports/` / campaign-leads exports / `temporary/` / root CSVs = `export`.

**Skipped** (catalogued with `role='skipped'` + `skip_reason`, no rows): empty files, `~$` lock files, credential-like names (`client_secret`, `token`, ...), `.xls` (no reader), files that fail to parse
(`unreadable: <ExceptionClass>`, `flatfile_unreadable` DQ error), and files > 50 MB that are not a list of companies / contacts (none today: the one big file, `uk_proptech_accounts.csv`
(52 MB, 301k UK companies with `root_domain` / `website`), is a company list and is loaded; it is also the bulk of the added database size).

**Duplicates**: the same bytes under several paths (the RAT_CAD workbook x5, WARN state caches, `reserve/` copies of `godown` outputs, ...) are catalogued once per path; rows are stored on the copy with
the best role (source_of_truth > pool > mirror > export > regenerable, then no ` (1)` suffix, then path) and the others point at it with `dup_of_file_id` (`rows_loaded = 0`). `--keep-duplicates` loads every copy.

**JSON mirrors** (`is_stale_mirror = 1`, `role='mirror'`): `hubspot/crm_mirror/data/{deals,contacts,companies}.json` (1,197 / 1,358 / 871 records, frozen 2026-07-29) and
`index/{by_domain,by_linkedin,by_name,deal_names,hubspot_all_names}.json` (one row per key; the key is read as domain / LinkedIn / company name). `index/sheet_worked_exclude.json` (246 already-called companies, **not**
rebuildable from HubSpot) is `source_of_truth`, one row per `detail` entry. These rows never touch `company` / `contact` / `deal`; the live HubSpot pull stays the only source of canonical data.

**Extracted lookup keys** (header-name driven, values normalised with `leadgen.norm`): `company_domain_norm` (domain / website / url columns; generic hosts such as linkedin.com, gmail.com, google.com are dropped),
`linkedin_norm` (`company/<slug>` from any LinkedIn column or a LinkedIn company URL typed into a website column), `person_linkedin_norm` (`in/<slug>`), `email_norm` (first valid), `phone_e164` (first valid; 10-digit numbers assumed +91 except in
US/UK/Romania folders; **no mobile classification** - the +91 mobile gate belongs to `contact_phone`), `company_name_norm` (display / weak matching only, never a link key). Name-derived domain guesses
(`shutdown-radar-us/`, `shutdown_companies_*`: "The French Gourmet" -> `the.com`) are kept in `row_json` but not extracted (`flatfile_guessed_domain` info issue).

Idempotency: the catalog key is the repo-relative path. Same sha256 (and same duplicate state) = skipped (only role / flags refreshed); changed bytes = the file's rows are deleted and reloaded in one transaction, keeping the `file_id`;
`--force` reloads all. Each file is its own transaction, so a bad file never leaves partial rows.

Useful queries:

```sql
SELECT role, COUNT(*) files, SUM(rows) rows, SUM(rows_loaded) loaded FROM ext_file_catalog GROUP BY role;
SELECT * FROM v_ext_file_summary WHERE rows_linked_company > 0 ORDER BY rows_linked_company DESC;
SELECT path, skip_reason FROM ext_file_catalog WHERE role = 'skipped';
SELECT json_extract(row_json, '$.Company') FROM ext_file_row WHERE company_domain_norm = 'example.com';
```

## 4. `link_files()`

A full recompute (safe at any time, no-op when nothing changed; merge survivors are followed through `v_identifier_survivor`):

* company: strong `company_identifier` `root_domain` <- `company_domain_norm` and `linkedin_company` <- `linkedin_norm`. One key is enough; if both resolve to **different** companies the row stays unlinked (counted as `company_ambiguous`).
  Weak identifiers (`is_strong = 0`) are ignored. `company_link_method` records which key(s) justified the link.
* contact: `contact.linkedin_person_norm` <- `person_linkedin_norm`, `contact.email_norm` <- `email_norm`, only when exactly one contact owns the value.
* links that no longer resolve (identifier removed, ambiguity introduced) are cleared; `linked_at` records the change.
* never-push domains get `suppression.company_id` when the domain is a strong company key (the block itself never changes).
* `--dry-run` reports the counts without writing. Until companies exist (the canonical importers have not run yet), every count is 0.

## Limits / things to know

* `row_json` holds personal data exactly as the legacy files did; do not log or export it. Counts only in reports.
* Files are keyed by path: moving a file inside `legacy/` creates a new catalog row (the old one stays until deleted by hand).
* xlsx blank-row numbering depends on openpyxl (fully empty rows are skipped), csv `row_no` follows the file.
* Sheets with a wrong declared size are read with `reset_dimensions()`; macro/legacy `.xls` workbooks are not readable (none present).
* Three diverged versions of "Company Ops scrap100" exist (`data-inventory.md`); they are staged as three files, not merged - dedupe by `person_linkedin_norm` when needed.
