# Google pulls: Sheets and Gmail (read-only)

Code: `leadgen/google/{auth,sheets,gmail}.py`. Tests: `tests/test_google.py` (fakes only, no network). Tables: `ext_gsheet_*`, `ext_gmail_*` (`db/migrations/0005_ext.sql`). Discovery facts: `docs/discovery/google-integrations.md`.

## Rules the code enforces

* **Read only.** `auth.ReadOnlyGuard` wraps every service in an allow-list proxy (`auth.ALLOWED_CALLS`): Sheets `spreadsheets.get`, `values.get/batchGet`; Drive `files.list/get`, `about.get`; Gmail `getProfile`, `labels.list/get`, `messages.list/get/attachments.get`, `threads.list/get`, `history.list`. Anything else raises `WriteBlocked` before a request exists, even though the Sheets tokens carry the write-capable `spreadsheets` scope. `tests/test_google.py` also scans the three source files for mutating method names and for any scope other than `*.readonly`.
* **No impersonation.** The service account is used as itself (`service_account_credentials` has no subject parameter). No domain-wide delegation anywhere.
* **Secrets.** A stale access token is refreshed in memory and written back only to the file it came from (atomic replace, `chmod 600`). Nothing prints or logs token / client-secret values; `token_status()` reports booleans, scopes and expiry only.
* **Quota.** Sheets: `RateLimiter` at 50 reads/min (limit is 60/min/user/project), 429 / 5xx / rate-limit-403 retried with exponential backoff and jitter (5 s doubling, cap 90 s, 8 retries, honours `Retry-After`). Gmail: a unit throttle (150 of 250 units/s; `messages.get` = 5), retries per sub-request inside a batch.
* **Short DB transactions** (`BEGIN IMMEDIATE`, 4,000 rows max, `busy_timeout` 30 s) because HubSpot importers write to the same file concurrently. No `INSERT OR REPLACE`.

## Sheets

```
.venv/bin/python -m leadgen.google.sheets catalog            # data/gsheets_catalog.json -> ext_gsheet_catalog / ext_gsheet_tab (no network)
.venv/bin/python -m leadgen.google.sheets plan [--include-bulk]
.venv/bin/python -m leadgen.google.sheets pull [--include-bulk] [--only ID] [--force] [--per-minute 50] [--max-sheets N] [--max-runtime SEC] [--no-drive-refresh] [--no-csv]
.venv/bin/python -m leadgen.google.sheets sample -n 10       # first rows of N bulk sheets, prints only, stores nothing
.venv/bin/python -m leadgen.google.sheets status
```

How a pull works: Drive `files.list` (1000/page, user token and service account) refreshes `modified_time` and adds new spreadsheets; then one `values.batchGet` per spreadsheet (`UNFORMATTED_VALUE`, `dateTimeRenderOption=FORMATTED_STRING`, one range per tab; tabs with more than 20,000 allocated rows are read in 20,000-row chunks, requests are capped at 2.5 M allocated cells). The API trims trailing empty rows, so a 1000x26 grid costs only its populated rows; fully empty rows are dropped, trailing empty cells are trimmed, row numbers are kept. `ext_gsheet_row.cells_json` is the row array, `text_flat` feeds FTS, `row_sha256` / `ext_gsheet_tab.content_sha256` give the content hash, `fetched_at` / `pulled_at` the provenance (tab-level via `tab_pk`; header row 1 in `header_json`).

**Incremental / resumable.** `ops_sync_state('gsheets', 'bhanu.enamala@lh2.ai', 'values', <spreadsheet_id>)` stores the Drive `modifiedTime` that was pulled. A spreadsheet whose `modifiedTime` has not moved (and has no pending/error tab) is skipped without any API call; a changed one is re-read and only rows whose hash changed are written (vanished rows deleted). An interrupted or partly failed run is simply started again. Verified live: a second `pull` made 0 value reads (422 unchanged). A tab renamed since the catalog (HTTP 400 "Unable to parse range") triggers one `spreadsheets.get` to refresh the tab list.

**Credentials.** `hubspot_sheets_token` first, `companyops_sheets_token` as fallback (same account bhanu.enamala@lh2.ai, same OAuth client, so they share one quota and parallelising them gains nothing), `sa_lh2bot` for sheets only it can see (the Private Codebase Tracker). Readers are built lazily, so a fallback token is only touched when needed.

CSV copies: `data/gsheets/<spreadsheet_id>__<tab>.csv` (row numbers preserved, skipped empty rows become blank lines; gitignored).

### Pull policy (what was pulled and why)

| Group | Spreadsheets | Decision |
|---|---|---|
| hard-coded ids (11 from the discovery doc) | 9 pulled | Private Codebase Tracker (SA only), Tracxn, ITservices_ScrapedLeads, master_companies, ceo leads, MidTier_Telehealth, Reachout list, Antrhopic_PoC, Outflo Reachout |
| hard-coded, not pulled | 2 | `1IPA45k...` intern-hiring sheet: **hard-excluded** (applicant PII, `ext_gsheet_exclusion`). `1aj-d_IlH...` "Supply Funnel SoP" is a Google **Doc**, not a spreadsheet, and is not in the catalog / not readable via the Sheets API (the SA lost it, see SA_GOOGLE_ACCESS.md); it needs the Docs/Drive export API and a human decision |
| human-owned, everything else | 413 pulled | owned by bhanu, shobit.gupta, vaibhav.mongia, ishpreet.sood, yash.wani, upneet, ashish.ranjan ... |
| auto-generated bulk (owner purunjay.choudhary) | 1,145 catalogued, **0 pulled** | see below |
| excluded after reviewing titles | 2 added to `ext_gsheet_exclusion` | "Automation Intern - LH2 AI Labs (Responses)" (owner hr@lh2holdings.com, intern applicant form responses) and "Creds" (shobit.gupta; named like a credentials sheet). Never pulled; the database triggers re-force `excluded/0` even if someone flips the flag. The title regex `responses|creds|credentials|passwords|applicant(s)|resume(s)` also marks future matches `pii_class='personal'` and keeps them out of the plan |

**Bulk decision.** 30 of the 1,145 purunjay sheets were sampled (10 with seed 7 printed, 20 more header-only): all 30 have the identical 12-column schema `Project Name, Project Code, Language, No of Lines of Code, No of Commits, No of PRs, Merged PRs, Ci/CD, Test frameworks, Total Files, Test Files, Source Files` with 1-3 rows of per-repo code metrics for a generated project code (`ToolSmith-12`, `FarmLoanCollections-171` ...). One tab, ~1000x24 grid, created by a script, not curated by a person. Decision: catalogued, not pulled (they are regenerable repo statistics and would add ~1,150 reads and ~1,150 tabs of noise to FTS). Switch on with `pull --include-bulk` (about 25 minutes at 50 reads/min) if wanted.

### Results of the real run (2026-10-04)

* Catalog: 1,570 spreadsheets, 1,860 tabs loaded; 3 excluded.
* Pulled: **422 spreadsheets, 709 tabs (699 with data, 10 empty), 340,840 rows, 181.7 MB of row JSON**, 424 value reads + ~3 Drive list calls + 1 sample read set, 0 failures, 1 transient 429 absorbed by backoff. CSVs: 709 files, 164 MB. The database grew to ~710 MB (rows are stored three times: `cells_json`, `text_flat`, FTS).
* Biggest: Immigration_Tech_07 Document Summary Sheet 244,300 rows, Tracxn 20,104 rows (5 tabs), Brand Feeds 5,892, Outflo Reachout 2,875, Private Codebase Tracker 2,014 rows (13 tabs).
* Not pulled: 1,145 bulk, 3 excluded, 1 Doc (not a spreadsheet).
* `doctor`: ok; `v_gsheet_excluded_rows` empty.

## Gmail (code complete, NOT run: needs your consent)

Current state: `secrets/gmail_readonly_token.json` does not exist. The only inbox-capable token (kartik.pillai) is revoked (`invalid_grant`) and the other Gmail tokens are `gmail.send` only.

```
.venv/bin/python tools/gmail_auth.py                          # 1. browser consent as bhanu.enamala@lh2.ai, scope gmail.readonly only
.venv/bin/python -m leadgen.google.gmail --check              # 2. offline token status (add --online to refresh + call users.getProfile)
.venv/bin/python -m leadgen.google.gmail --pull --max-messages 200   # 3. small first run, then
.venv/bin/python -m leadgen.google.gmail --pull               # 4. full pull; later runs are incremental
```

If Google blocks the consent screen ("access blocked"), add your address as a test user on the OAuth consent screen of the client you used (default client `companyops_client_secret.json`; alternative `--client secrets/hubspot_client_secret_lh2pipeline.json`, project lh2-pipeline, which yields durable tokens for your account per the discovery doc). A consent screen left in Testing mode makes refresh tokens expire after 7 days.

What `--pull` does: `users.getProfile` (records `messagesTotal`, `historyId`) and `labels.list/get`; `messages.list` 500 per page with `includeSpamTrash`; the ids not yet in the DB are fetched with `messages.get(format=full)` in HTTP batches of 25 and stored (headers From/To/Cc/Bcc/Reply-To/Subject/Date/Message-ID/In-Reply-To/References + full header list, nested multipart `text/plain` and `text/html` decoded with the part charset; HTML-only mail keeps `body_html` and gets a text rendering in `body_text` for FTS; Date -> UTC; addresses lower-case, comma separated). Attachments: metadata always; bytes up to 25 MB to `data/gmail/attachments/<messageId>/<partId>_<name>` (larger ones are skipped and logged to `ops_dq_issue` rule `gmail_attachment_too_large`; inline-data parts need no extra call). Each message's full API response is kept gzip'd at `data/gmail/raw/<id[:2]>/<id>.json.gz` (`raw_path`, `raw_sha256`; note the schema comment says `.eml.gz`, but `format=full` yields a JSON MIME tree, not RFC822). Threads are derived (count, first/last time, latest snippet).

Resume and incremental: after each listing page the next `pageToken` is saved (`ops_sync_state gmail/<mailbox>/messages/full_page_token`), the pre-listing `historyId` is saved as `full_start_history_id` and becomes the cursor `historyId` only when the whole listing finished with no unreadable message. Later runs call `users.history.list` (message added -> fetch, label added/removed -> re-read labels with `format=minimal`, message deleted -> counted, row kept). A 404 (history older than about a week) falls back to a full listing that skips known ids; run `--refresh-labels` afterwards to re-sync label sets. A pull stopped with `--max-messages` or a crash resumes where it stopped; re-running never duplicates (upserts on `(account, message_id)`; FTS by triggers).

Limits: attachments are capped per file (25 MB), not in total; `refresh-labels` costs 5 units per stored message; the FTS index does not cover `body_html` (by schema design).

## Unresolved

* Gmail needs the human consent step above; nothing was run against Gmail.
* Supply Funnel SoP (`1aj-d_IlH...`) is a Doc: not pulled.
* The bulk of 1,145 auto-generated sheets is a one-flag decision (`--include-bulk`).
* `config/gsheets_pull.yaml` (owned elsewhere) was left empty; the policy above lives in `sheets.py` (`HARD_CODED_SHEETS`, `BULK_OWNER`, `PII_TITLE_RE`) and the allow-list file, if filled, is honoured as an override that also pulls bulk-owned ids listed there.
* Other human-owned sheets may still hold personal data (cold-call lead lists with phone numbers, `Buyers List`, `UK_Calling` ...). They were pulled as business data; review before sharing the database.
