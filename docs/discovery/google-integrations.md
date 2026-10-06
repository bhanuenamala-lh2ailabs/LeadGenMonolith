# Google Sheets / Drive / Gmail integrations - discovery

Date: 2026-10-04. Method: grep of `legacy/*` (read-only), live read-only tests (OAuth refresh grant, Drive `files.list`, Drive `about`, Sheets `spreadsheets.get` with `fields=` only, Gmail `users.getProfile` attempt). No writes, no sends, no cell data downloaded. No secret values recorded in this file.

## 1. Integration scripts in legacy/

### Sheets / Drive (all in `legacy/hubspot/`)

| Script | Credential | Access | Sheet / purpose |
|---|---|---|---|
| `crm_mirror/enrich/gsheets.py` (helper: `svc()`, `find()`, `read()`, `write()`) | OAuth user token `hubspot/sheets_token.json` (copy: `secrets/hubspot_sheets_token.json`), authorized as bhanu.enamala@lh2.ai. Scopes `spreadsheets` + `drive.readonly`. Client `1014768808395-...` (GCP project `lh2-pipeline`). | read + write cells; Drive read/metadata | Used by every script below. `--auth` runs an interactive browser consent. |
| `crm_mirror/sync.py` (line ~199) | SA `lh2-bot-351@lh2-pipeline.iam.gserviceaccount.com`, scope `spreadsheets.readonly` | READ | Tracxn `12BLV3nv1d9Is4UHN113YVhiTBNilHe-9phIMMCEKS4A` (feeds the CRM mirror) |
| `crm_mirror/enrich/source_audit_pull.py` | SA lh2-bot, `spreadsheets.readonly`; key file `lh2-pipeline-9982aa4422f5.json` | READ every tab (`values.get` of `'<tab>'`) | `private_codebase_tracker` `1B9in9qK1V3IyjoyjYSqwn0gGRyoP9qVlMBGKKheoEhM` + `tracxn` `12BLV3nv...`; dumps to `crm_mirror/sources/_audit/*.json` |
| `lh2-pipeline/src/lh2_pipeline/export/sheets_sync.py` (+ `sheetsSyncSpec.md`, `tests/test_sheets.py`) | SA lh2-bot, scopes spreadsheets+drive via gspread; creds from `google_service_account.json` or env JSON in CI | WRITE (append "Qualified Leads", overwrite "Under Review", append "Pipeline Stats") | Key comes from env `GOOGLE_SHEETS_KEY` (blank in `config.yaml`). Tab names match live sheet **ITservices_ScrapedLeads** `19PE9VroacFFaeATFgEqmj0jDuU2PRJSzxZyVypBPjLo` (tabs Sheet1, Qualified Leads, Under Review, Pipeline Stats). Per `SA_GOOGLE_ACCESS.md` the SA lost access to that sheet on 2026-07-29 (verified below: SA sees only 2 files). |
| `crm_mirror/enrich/enrich_ceo_leads_sheet.py` | gsheets.py user token | READ `Leads!A1:G50`, WRITE `Leads!F1`+result columns | `1SI4GGdrAEvd-pfun2iULhz3yh46h8LYGkGTZCqy9VIE` "ceo leads " (tabs Leads, Read me). Credit-spending (Apollo/SignalHire) - do not re-run. |
| `crm_mirror/enrich/enrich_telehealth_sheet.py` | user token | READ `India!A1:P40`, WRITE enrichment cols | `17IkH_Ej1kAPk5q3EAZuiL5QTWg_hwDPfW-td4WKcMD0` "MidTier_Telehealth_India_BD_Nepal_SEA" (tabs Reference Benchmark, Summary, India, Bangladesh, Nepal, Southeast Asia, Legend) |
| `enrich_nvidia_sheet.py`, `enrich_openai_sheet.py`, `enrich_amazon_sheet.py`, `enrich_xai_sheet.py` | user token | READ `A:E`, WRITE B..E per row (+ header `A1:F1` in nvidia/openai/amazon) | all four use the same ID `1xuoBloYzQOg3j27UQC0t6l9AXjFw9IxAGknlbYNsgOo` "Reachout list \| LH2 AI". Tabs: NVIDIA, xAI, OpenAI, Amazon. NOTE: the four scripts all hard-code `TAB` = 'NVIDIA' per their docstrings; confirm each script's `TAB` before reuse. |
| `crm_mirror/enrich/anthropic_poc.py` | user token | WRITE `Sheet1!A1` (full overwrite) | `1URrL2rrKo7ECS9UxMI9KRLIf3_1cpQiMahJ4RDoL8xE` "Antrhopic_PoC" |
| `godown/scientific_sheet_write.py` | user token | WRITE: creates tab `POC Enrichment`, `values.update` + formatting batchUpdate | `1bz5sZmG_7mfRefUZld2wErxRfV9AcPNBSS6eXmyxbLE` "master_companies" (tabs master_companies.csv, POC Enrichment) |
| `godown/intern_screen/write_ranks.py` | user token | READ `Sheet1!A1:A1000`; WRITE `Sheet1!H1:K<n>`, creates/writes tab `claude_screen_ranked` | `1IPA45kJ6yTsBo8d33DM0Ay_CIfBa6jrj_wrh3A4_uiQ` "Founder's Office(Strategy & Ops) - Automation Intern" (tabs Sheet1, ats_results_20260526_121715, claude_screen_ranked). Contains personal applicant data. |
| `godown/intern_screen/classify_students.py` | user token | READ `Sheet1!A1:L200`; WRITE `Sheet1!L<row>` | same intern sheet |
| `godown/intern_screen/pull_resumes.py` | user token (drive.readonly) | Drive `files.get/get_media/export` of applicant resume files | resume file IDs from the sheet's drive links (`godown/intern_hiring/applicants_raw.json`) |

Docs referencing IDs without calling the API: `docs/reference/SA_GOOGLE_ACCESS.md` (Tracxn, Private Codebase Tracker, and 3 revoked: Supply Funnel SoP doc `1aj-d_IlHFyHOUdnrgHYc5mb6gpAnIijORZt8Vyna5j4`, Outflo Reachout sheet `1CDgrOx72H9b9uw_A2OdzDWKAJMF-KwnFSOETbDoTtP0`, ITservices_ScrapedLeads), `crm_mirror/rules/{tracxn_sheet,private_codebase_tracker}.md`. Private Codebase Tracker rule file: tab `Pipeline Tracker` (gid 411280464), enrichment in `IT Services Firms`.
Bulk of `drive.google.com/open?id=...` URLs found under `hubspot/godown/**` (intern applicant resumes, CAD/India profile JSON dumps) are scraped/third-party data, not integration config. Two further 44-char strings that matched are an HTML snapshot / Colorado WARN research note (public external sheets, not ours).

companyOps and RapidActionTeam contain NO Sheets code: they only hold a copy of `sheets_token.json` (companyOps, unused by code) and Gmail send code.

### Gmail

| Script | Repo | Scope / token | Notes |
|---|---|---|---|
| `opsdata/gmail_auth.py` | companyOps | PKCE desktop flow, scope `gmail.readonly`, writes `.gmail_token.json`; docstring: "sign in AS KARTIK" | token file = `secrets/companyops_gmail_readonly_token.json` (authorized_as kartik.pillai@lh2.ai, client `107044297252-...`, GCP project `lh2-companyops-gmail`) |
| `opsdata/gmail_pull_push.py` | companyOps | reads with the readonly token; list q `in:sent`, selects mails containing a calendly link; `maxResults=100` | RETIRED 2026-09-08 (v4 SOP dropped email branch). Reference only. |
| `opsdata/company_ops_cluster_report_send.py`, `opsdata/full_funnel_dashboard_mail.py`, `opsdata/send_csv_mail.py` | companyOps | `bhanu_gmail_token.json`, scope `gmail.send` only, `messages/send`; recipient bhanu.enamala@lh2.ai | daily report mailers |
| `email_transport.py` | RapidActionTeam | `gmail.send` token (`.gmail_token.json`/`bhanu_gmail_token.json`), SA fallback with `with_subject()` (domain-wide delegation, never confirmed set up) | |
| `crm_mirror/enrich/gmail_sender.py`, `lh2-pipeline/dashboard/gmail_sender.py` | hubspot | `gmail.send`; GitHub Actions `daily-report.yml` writes `GMAIL_TOKEN_JSON`/`GMAIL_CLIENT_JSON` secrets to files | `IMPERSONATE` SA fallback exists |

## 2. Token inventory (secrets/) and live results

All access tokens stored in files are long expired; the test is whether the refresh token still mints new ones.

| File | Client / project | Account | Scopes | Refresh | Minimal call result |
|---|---|---|---|---|---|
| `companyops_gmail_readonly_token.json` | `107044297252` / lh2-companyops-gmail | kartik.pillai@lh2.ai (`authorized_as` field) | gmail.readonly | **FAILS: HTTP 400 `invalid_grant`** (revoked or expired; file dated 2026-08-07) | Gmail not callable. Mailbox size unknown. |
| `companyops_gmail_send_token.json` (= legacy `companyOps/bhanu_gmail_token.json`) | `1014768808395` / lh2-pipeline | no `account` field; legacy name and recipients imply bhanu.enamala@lh2.ai (cannot verify: send-only scope has no profile endpoint) | gmail.send | OK (expires_in 3599) | `users.getProfile` -> 403 insufficient scopes (as expected) |
| `hubspot_gmail_send_token_crm_mirror.json` | `1014768808395` | same, unverified | gmail.send | OK | 403 on profile |
| `hubspot_gmail_send_token_dashboard.json` | `1014768808395` | same, unverified | gmail.send | OK | 403 on profile |
| `rat_gmail_send_token.json` | `1014768808395` | same, unverified | gmail.send | OK | 403 on profile |
| `companyops_sheets_token.json` | `1014768808395` | **bhanu.enamala@lh2.ai** (Drive `about`) | spreadsheets, drive.readonly | OK | Drive `about` OK; `files.list` OK -> 1,569 spreadsheets |
| `hubspot_sheets_token.json` | `1014768808395` | **bhanu.enamala@lh2.ai** | spreadsheets, drive.readonly | OK | Drive `files.list` -> same 1,569 spreadsheets (identical set to companyops_sheets_token; effectively duplicates) |
| `hubspot_service_account_lh2bot.json` | GCP lh2-pipeline | lh2-bot-351@lh2-pipeline.iam.gserviceaccount.com | requested drive.readonly + spreadsheets.readonly | OK (SA JWT) | `files.list` -> 2 sheets: Tracxn, Private Codebase Tracker (read-only for SA). Matches `SA_GOOGLE_ACCESS.md`. |
| `*_client_secret*.json` (3 files) | OAuth installed-app clients `107044297252` (companyops, 2 identical copies) and `1014768808395` (lh2pipeline) | n/a | n/a | n/a | needed to mint new tokens |

Drive quota for bhanu account (from `about`): limit ~5.96 TB, usage ~1.62 TB (domain pooled storage, not just this account).
Conclusion: **no valid gmail.readonly credential exists.** The only readonly token is Kartik's and is dead. All live Gmail tokens are send-only and cannot read any mail.

## 3. Sheets catalog

File: `/Users/bhanu/Desktop/LeadGenMonolith/data/gsheets_catalog.json` (metadata only: id, name, owners, modified/created time, lastModifyingUser, canEdit, url, visible_to, tabs with sheetId/hidden/rows/cols).

- Spreadsheets accessible: **1,570** (1,569 via bhanu OAuth tokens, plus Private Codebase Tracker visible ONLY to the SA). 0 in shared drives, 41 owned by bhanu, 1,529 owned by others in the domain (shared with bhanu).
- Tabs: **1,860** (55 hidden). Allocated grid cells (rows x cols, NOT populated): ~46.6M.
- Owners: purunjay.choudhary@lh2.ai 1,145; shobit.gupta 213; vaibhav.mongia 66; ishpreet.sood 52; bhanu.enamala 41; yash.wani 11; upneet 9; ashish.ranjan 7; sreenandan.m 6; others <=3.
- Modified between 2026-05-25 and 2026-10-03 (Sep 2026: 1,060 files, Aug: 375). Looks like a large amount is auto-generated per-run sheets (e.g. purunjay's 1,145).
- Largest by allocated cells: Immigration_Tech_07 Document Summary Sheet (14 tabs, 5.7M), Sprint for Transcript (0.88M), Tracxn (0.71M), Meta_1_Billion_company_mapping (0.63M), Brand Feeds (0.48M), Private Codebase Tracker (0.41M).
- Cross-check of hard-coded IDs (all present with the access shown): master_companies (bhanu-owned, editable), intern sheet (owner kenisha.thacker@lh2holdings.com), Tracxn (owner external gmail account, SA + user visible), Private Codebase Tracker (owner aman.taneja@lh2.ai, **SA only**, bhanu token cannot see it), Anthropic_PoC (bhanu), Telehealth (ashish.ranjan), Reachout list (apoorv.vashist), CEO leads (vaishnavi.kannan), Outflo Reachout (yash.wani, now visible to bhanu tokens), ITservices_ScrapedLeads (bhanu-owned; tabs Sheet1, Qualified Leads, Under Review, Pipeline Stats).
- One transient 429 during enumeration was retried successfully; catalog is complete.

## 4. What is needed for the entire inbox

1. The existing readonly token (kartik.pillai@lh2.ai) is dead (`invalid_grant`). It is also a different mailbox from the user's own (bhanu.enamala@lh2.ai). Likely cause: the `lh2-companyops-gmail` OAuth consent screen is in Testing/External mode (7-day refresh-token expiry), or the grant was revoked; this is a hypothesis, not verified.
2. Missing scope on every live token: `https://www.googleapis.com/auth/gmail.readonly` (send tokens have only `gmail.send`; sheets tokens only spreadsheets+drive.readonly).
3. Mailbox size (`messagesTotal`, `threadsTotal`) could not be measured; no credential can call `users.getProfile`.
4. A new interactive OAuth consent is required (a human must click in a browser; this cannot be automated). Recommended: reuse client `1014768808395` (project lh2-pipeline), which demonstrably yields durable refresh tokens for bhanu (sheets token from 2026-09-15 and Oct-3 still refresh; the Gmail API is already enabled in that project because send works). Mint with scope `gmail.readonly` only. For Kartik's mailbox, Kartik must consent himself.
5. Attempt to test whether the SA `lh2-bot` has domain-wide delegation to read Gmail (`with_subject`) was blocked by the permission classifier and was not performed (see blocked).

## 5. Recommended bulk-pull plan (once a gmail.readonly token exists)

- Scope: `gmail.readonly` only. Store as `secrets/bhanu_gmail_readonly_token.json` (0600).
- Step 0: `users.getProfile` -> record messagesTotal/threadsTotal. Step 1: `users.labels.list` -> per-label counts.
- ID listing: `users.messages.list` with `maxResults=500` (max), `includeSpamTrash=true`, paginate via `nextPageToken`; 5 quota units/call. Persist IDs to disk first (resumable).
- Fetch: `users.messages.batchGet` does not exist on the REST API; use HTTP batch (multipart/mixed, up to 100 sub-requests, but Google advises <=50) of `users.messages.get?format=raw` (5 units each), or `format=full` for parsed headers. Raw RFC822 also keeps attachments; store as .eml gz, plus a metadata row (id, threadId, labelIds, internalDate, from/to/subject, size) in the Postgres/SQLite mail table.
- Quotas: per-user 250 quota units/second (15,000/min); project daily is 1,000,000,000 units. So `messages.get` max ~50 msgs/sec. Plan ~20-25 req/sec sustained with 4 worker threads and exponential backoff on 429/403 `rateLimitExceeded`. At 25 msg/s, 100k messages ~ 70 minutes, 500k ~ 6 h. Attachments > a few MB: fetch via `users.messages.attachments.get` only if needed (second pass).
- Incremental after first pass: record `historyId` from getProfile and use `users.history.list` daily.
- Sheets bulk pull (separate): ~1,570 spreadsheets / 1,860 tabs. Sheets default read quota is 300 requests/min per project and 60/min per user per project. Measured here: the catalog run (~1,570 `spreadsheets.get` calls, 4 threads) sustained only ~45-60 calls/min (~35 min total), consistent with the 60/min/user cap. Plan for <=50 calls/min per token. Use `values:batchGet` with one range per tab (`'<tab>'`, UNFORMATTED_VALUE), one request per spreadsheet; for big tabs (Immigration_Tech_07 etc., >100k rows) chunk by 20,000 rows. Expect ~1,600-2,500 calls, i.e. ~35-50 min. Running the bhanu token and the companyops token (same account, same client project) in parallel will NOT double the quota (same user + project). Skip nothing; filter noise later. Store raw per-sheet JSON under `data/gsheets/<id>.json` and a unified `sheet_cells` view.

## 6. Risks / notes

- Several enrichment scripts WRITE to shared sheets and spend Apollo/SignalHire credits; do not run them during consolidation. The intern sheet holds personal applicant data (resumes, phone numbers) - exclude from the shared DB or segregate.
- `sync.py`/`source_audit_pull.py` depend on the SA, which has only 2 sheets today; ITservices_ScrapedLeads sync (`sheets_sync.py`) can no longer write via SA unless the sheet is re-shared with lh2-bot-351@... Alternatively switch to the bhanu OAuth token.
- companyops_sheets_token.json and hubspot_sheets_token.json are the same account and scopes; keep one.
- Process note: while inspecting `secrets/` I printed the JSON with a redaction that did not cover the nested `installed.client_secret` in the three `*client_secret*.json` files, so those OAuth client secrets appeared once in this session's tool output. They were not written to any file or to this document. Consider them exposed to this session only; rotating is optional but cheap.
