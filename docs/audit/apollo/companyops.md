# Apollo credit audit: companyOps repo, 29 Sep 2026 00:00 IST to 4 Oct 2026

Scope: every Apollo.io credit spent by the `companyOps` repo (`legacy/companyOps`, live path `/Users/bhanu/Desktop/companyOps`) in the window. Read-only forensic work, no network calls, nothing under `legacy/` or `db/` modified.

Files in this folder from this audit:

| File | Content |
|---|---|
| `companyops.md` | this report |
| `companyops_ledger.csv` | 105 rows: 99 aggregated ledger rows (one per run window x segment x endpoint) plus 6 unledgered-estimate rows. Columns as requested, plus `data_location_now` and `usage_bucket`. |
| `companyops_ledger_rows.csv` | all 1,950 raw `cost_ledger` rows in the window (supporting detail, one row per Apollo call), tagged with run window and activity |
| `companyops_balance_readings.csv` | 66 readings: Apollo-side statements, funded/exhausted events, plus the repo's own running totals from the logs |

## 1. Headline

| | Credits |
|---|---:|
| MEASURED: rows in the repo's own `cost_ledger` (a call really returned HTTP 2xx and the code wrote a row) | **6,272** |
| ESTIMATED: real calls with no ledger row (manual probes, see section 7) | **14** (range ~5 to ~47; the upper end is the 31 unverifiable startup probes) |
| **Total attributable to companyOps** | **~6,286** |
| User-stated consumption 29 Sep to 4 Oct, all repos and people (50,469 minus ~7,800) | ~42,669 |
| Gap NOT explained by the companyOps repo | **~36,380 (about 85%)** |

All 6,272 measured credits fall on three IST days. Nothing from this repo on 1, 2 or 4 Oct, and nothing before 10:54 IST on 29 Sep.

| IST day | Window of activity | Credits measured |
|---|---|---:|
| Tue 29 Sep | 10:54:17 to 16:35:28 | 3,911 |
| Wed 30 Sep | 11:06:27 to 17:19:19 | 2,343 |
| Sat 3 Oct | 22:53:17 to 22:53:47 | 18 |

## 2. Method, time zones and traps

1. Primary evidence is `legacy/companyOps/tam/data/tam.sqlite3`, table `cost_ledger` (opened `mode=ro&immutable=1`). It is written by `costs.charge()` immediately after each successful Apollo response. 2,634 rows, ids contiguous 1 to 2634, so nothing was deleted. The Sep 29 14:02 IST backup (`tam.sqlite3.bak-20260929-140234`, 1,128 rows) matches the live table row for row. The imported copy in `db/leadgen.sqlite` (`cost_ledger`, `legacy_tam_cost_ledger`) has the same 2,634 apollo rows.
2. **`cost_ledger.at` is UTC** (SQLite `datetime('now')`). I verified this against the run logs, which are IST: for example the ledger total of 5,905 at 11:05:31 UTC equals the log line "apollo credits this month: 5905" printed at 16:35:31 IST. Window start 29 Sep 00:00 IST = 28 Sep 18:30 UTC. Every 28 Sep ledger row (1,994 credits, last at 22:09 IST) is before the window and excluded.
3. **Exception: the two Oct 3 probe rows were written in local IST** (`datetime('now','localtime')`), so `22:53` there is already IST (17:23 UTC). The monolith import in `db/leadgen.sqlite` labels them `2026-10-03T22:53Z`, which is wrong by 5.5 h. Naively reading them as UTC pushes them to 4 Oct 04:23 IST.
4. Secondary evidence: `tam/logs/run_2026-09-29.jsonl` and `run_2026-09-30.jsonl` (IST), the raw Claude session `chat_context/raw/companyOps/7bb0eacd-...jsonl` (UTC timestamps; every command that launched a paid run, and the user's messages), `crm_pushes` and `funnel_events` in the same DB (what the spend produced), and the exports folder. The 200-char-clipped markdown transcript was not needed.
5. Subagents (`subagents/*.jsonl`, 360 files) never call `api.apollo.io`; their "apollo" hits are company names in classification verdicts. `audit/` (28 files) has nothing after 16 Sep. The legacy discovery folders (`adtech_discovery`, `manufacturing_discovery`, `construction_discovery`, `mobility_discovery`, `apollo_proptech`, `apollo_test`, `pipeline`) and `opsdata/phone_enrich_apollo_signalhire.py` have no file modified in the window, so they were not run.
6. No double counting: a ledger row, the log summary line and the chat message about the same run are treated as one spend. Chat or log numbers are used only to (a) label a ledger row, or (b) add spend that has no ledger row.

## 3. Cost model, verified from code

| Ledger unit | Endpoint | Credits charged by code | Where |
|---|---|---|---|
| `org_enrich` | `POST /api/v1/organizations/bulk_enrich`, chunks of 10 domains (`for i in range(0, len(todo), 10)`) | `len(domains)` per call, 1 per domain | `outreach.py` `enrich_headcount` ~line 896 |
| `people_match` | `POST /api/v1/people/match` with `reveal_personal_emails: true` and `linkedin_url` (or `id`) | 1 per HTTP<400 response | `outreach.apollo_match` line 734 |
| `phone_reveal` | same call with `reveal_phone_number: true` + placeholder `webhook_url`; result polled free via `GET /api/v1/webhook_result/{top-level request_id}` | **+8**, charged when Apollo returns a `request_id`, even if the polled result has no number | `outreach.apollo_match` line 752 |
| (not in window) `org_search_page` | `POST /api/v1/mixed_companies/search`, 100 orgs/page | 1 per non-empty page | `sources/apollo_orgs.py` |
| free | `POST /api/v1/mixed_people/api_search` (obfuscated previews), `auth/health`, `webhook_result`, `usage_stats` (404) | 0 | `apollo_people_search`; not ledgered |

Cross-checks that support 1 / 1 / 8:
* `config.yaml` unit_costs says "MEASURED against the live APIs on 2026-09-29" (1/1/8). No Apollo balance delta for these exists in this repo's chat (Apollo has no balance endpoint), so the per-call amounts rest on that older measurement.
* The Apollo usage screen the user pasted on 29 Sep 10:48 IST shows "Mobile 35,464". 35,464 / 8 = 4,433 reveals exactly, which fits 8 credits per reveal in Apollo's own accounting.
* On 29 Sep the chat reported 1,842 at 13:46 IST with a breakdown of 961 org_enrich, 616 phone_reveal, ~265 people_match; the ledger at that moment gives 961 / 616 / 267 = 1,844. Consistent.

Known biases of the ledger (cannot be resolved locally):
* Over-count risk: `people_match` is charged on any 2xx even if no person matched; `org_enrich` is charged per domain submitted, not per organisation returned; `phone_reveal` is charged on `request_id` even when the job returns no number. If Apollo bills only on success, true credits are lower.
* Under-count risk: `reveal_personal_emails: true` is sent on every match. If Apollo also bills an "Email" credit (the account shows a separate Email counter, 786 before the window), that is outside the 1-credit model. Not quantifiable here.
* Unledgered calls (section 7).

## 4. Call sites, triggers and gates

| Call site | Trigger | Default batch | Gated by `vendors_disabled`? | Budget logic |
|---|---|---|---|---|
| `tam/leadgen/outreach.py run()` (`cli outreach`) | manual, long-running; Bhanu's push-to-reps task | headcount 10 domains/call; one match (+reveal) per lead; screening depth raised to 1,200 per segment at 13:36 IST on 29 Sep | yes: `vendor_key()` returns no key when disabled (added 30 Sep), plus `check_enabled` per lead | `check_monthly` 10,000 (raised from 2,000), per-run 300, daily `apollo_credits_per_day: 750` re-read per lead from the ledger; overridden by `--apollo-budget` (2,194 / 2,000 / 3,000 / 10,000 were passed). Phone reveal default **OFF** since 13:46 IST 29 Sep, `--allow-phone-reveal` to enable (used from 15:48 IST). Startup `_apollo_has_credits()` fires one bogus `people/match`. |
| `tam/leadgen/repair.py` (`cli repair`) | manual; wrong-number repair | one match per deal (1 credit, no reveal) | **no** (reads the key directly, so it ignores `vendors_disabled`) | daily budget check against ledger |
| `tam/leadgen/backfill_linkedin.py` | manual | one match per contact | **no** | ledger-based |
| `tam/leadgen/cobol_poc.py --vendor apollo` | manual; COBOL-India list | match + reveal per company | yes (`vendor_key`) | `--max-credits` (200 default, 1,600 passed) |
| `tam/leadgen/sources/apollo_orgs.py` (`cli discover`) | manual | 100 orgs/page | yes (`check_enabled`) | per-run 300 |
| `tam/leadgen/doctor.py` | manual | none | no | free endpoint |
| `opsdata/phone_enrich_apollo_signalhire.py`, `*_discovery/`, `apollo_*` | manual scripts, old | per script | no | none; not run in window |

`vendors_disabled` history in the window (from the chat's config edits): disabled 29 Sep 17:35 IST (user: "DO NOT use apollo or signalhire"), Apollo re-enabled 30 Sep 11:03 IST ("no free u can use apollo"), SignalHire re-enabled 16:10 IST. **The file today reads `vendors_disabled: []`, i.e. Apollo is NOT disabled in the repo copy**, contrary to the belief that it was switched off after 29 Sep. The ledger is consistent with the disabled interval: last spend 29 Sep 16:35 IST, next 30 Sep 11:06 IST.

## 5. Day by day (IST)

Each run window below is `W..` in `companyops_ledger.csv`, with ledger ids in `evidence_ref`. Credits are all MEASURED (ledger) unless marked EST. "Deals" = rows in `crm_pushes` status `pushed` in the same time slice.

### Mon 28 Sep 18:30 UTC to Tue 29 Sep 10:53 IST
No Apollo call at all (first ledger id of the window is 685 at 10:54:17 IST). The 28 Sep run ended at 22:09 IST against the repo's own 2,000 monthly cap, 1,994 credits, outside the window.

### Tue 29 Sep: 3,911 measured + 13 estimated

Balance context: user wrote at 10:33 IST that the account was out of credits and at 10:51 IST that he was buying more. Calls nevertheless succeeded from 10:54 IST. Apollo then returned HTTP 422 "insufficient credits" from 12:19:14 IST (first), the last charged call before that is ledger id 997 at 12:19:10 IST. 124 match calls and 5 bulk_enrich calls were refused (not charged) until 12:34:24 IST. Funded again by 12:39:03 IST (id 998), confirmed by billable probes 12:45 and 12:52 IST.

| Run (IST) | What ran | org_enrich | match | reveal (calls) | Credits | Purpose and result |
|---|---|---:|---:|---:|---:|---|
| W01 10:54 to 11:14 | `outreach fintech,edtech --per-rep 60` | 42 | 128 | 336 (42) | 506 | Fintech 167, EdTech 339. Run restarted 11:01 IST; repo month-to-date reached 2,500 at 11:14 |
| W02 11:41 to 11:44 | outreach fintech,edtech 70, then edtech-only | 24 | 17 | 64 (8) | 105 | Halted by assistant after 7 attempts, 0 pushes |
| W03 12:02 | manual `bulk_enrich` probe (setu.co) | 1 | 0 | 0 | 1 | "Is Apollo funded" check |
| EST 12:02:40 | manual `people/match` + phone reveal probe | | | | **9 EST** | same check, NOT in ledger (assistant text says it was) |
| W04 12:11 to 12:19 | optimised website-first run | 192 | 76 | 88 (11) | 356 | EdTech 224, Fintech 132. 49 pushed; then 422s begin |
| W05 12:39 to 12:46 | SignalHire-first runs `shtest`, `sh100` | 41 | 11 | 40 (5) | 92 | after top-up; killed 12:46 |
| EST 12:45, 12:52 | bogus-name funded-probes (x2) | | | | 0 EST (range 0 to 2) | |
| EST 12:52:36, 12:52:46 | `mixed_companies/search` x2 (does search return headcount? no) | | | | **2 EST** | |
| EST 12:54:54 | `bulk_enrich` kredx.com, innoviti.com | | | | **2 EST** | |
| W06 13:16 to 13:26 | `outreach` first full chain with free SignalHire headcount hint | 45 | 19 | 56 (7) | 120 | |
| W07 13:34 to 13:45 | `outreach` deep headcount screen (675 fintech + edtech/healthcare) | 616 | 16 | 32 (4) | 664 | **the 1,844-credit breach of the 750/day cap**: 961 org_enrich (headcount) + 616 reveals. Stopped by assistant 13:46 |
| W08 14:35 to 14:45 | `outreach` a1/a2 with `--apollo-budget 2194` | 73 | 48 | 0 | 121 | user authorised +350 |
| W08r1 14:47 to 14:52 | `repair --rep manit` dry-run and tests | 0 | 21 | 0 | 21 | same person ids repeat; wasted tests |
| W08r2 14:53 to 14:56 | `repair --rep manit` (25 deals) | 0 | 23 | 0 | 23 | 6 Manit numbers fixed (SignalHire reveal) |
| W09 15:04 to 15:15 | repair x3 reps + outreach f1/f2 resumed | 73 | 142 | 0 | 215 | repair part 33 |
| W10a 15:32 to 15:47 | outreach stalled (SignalHire callback dead) + LinkedIn backfill test | 0 | 49 | 0 | 49 | 0 pushes for ~49 credits |
| W10b 15:49 to 16:01 | `outreach ap1/ap2 --allow-phone-reveal` | 73 | 170 | 440 (55) | 683 | Apollo-only chain |
| W11 16:14 to 16:35 | `outreach r1/r2 --allow-phone-reveal` after classification refill | 267 | 176 | 512 (64) | 955 | last Apollo call of the day 16:35:28 |
| **Total 29 Sep** | | **1,447 (164 calls)** | **896** | **1,568 (196)** | **3,911** | EST 13 (+0 to 2 probes) |

By purpose, 29 Sep (ledger): Fintech 1,900 (headcount 942, match 358, reveal 600), EdTech 1,424 (286 / 346 / 792), Healthcare 508 (218 / 114 / 176), wrong-number repair 77, manual probe 1, LinkedIn backfill test 1.
Deals created that day: Fintech 135, EdTech 85, Healthcare 38 (258 pushed, 2 later removed off-ICP). That is 14.1, 16.8 and 13.4 ledger credits per pushed deal, before counting the many matches/reveals that did not clear the +91 mobile and LinkedIn gates (funnel_events: 521 `not_pushed_no_india_mobile` and 615 `not_pushed_no_linkedin` across the window).

Where it lives: deals and contacts for the Fintech push sit in HubSpot pipeline **2464812771** (Company Ops Cluster 2 India), owners Vaishnavi 168341981 and Tanisha 168015618; EdTech and Healthcare deals in pipeline **`default`** (Company Ops Cluster 1 India), owners Amisha 168015679, Manit 168609107. Local record: `tam.sqlite3 crm_pushes` (deal_id, contact_id, owner, pipeline, segment, name|title|phone in `note`), mirrored in `db/leadgen.sqlite legacy_tam_crm_pushes`. The local `contacts` table is empty. Headcount values are in `companies.employee_count`. Apollo payloads for people who did not make it into HubSpot were not stored.
Repair credits changed existing HubSpot contacts (8 `contact_corrected` rows on 29 Sep).

### Wed 30 Sep: 2,343 measured + 1 estimated

Balance context: Apollo was funded all day; no 422. The repo's daily cap of 750 was already passed on the previous day, and the cap was overridden with `--apollo-budget 2000/3000`.

| Run (IST) | What ran | org_enrich | match | reveal (calls) | Credits | Purpose and result |
|---|---|---:|---:|---:|---:|---|
| W12 11:06 to 11:10 | `outreach --only-rep amisha` | 3 | 29 | 80 (10) | 112 | Amisha fill; 0 pushed that run |
| EST 11:09:49 | `people/match` by email for one pasted contact | | | | **1 EST** | LinkedIn lookup, NOT in ledger |
| W13 11:26 to 11:48 | Amisha fill (relaunched) + Manit fill from 11:38 | 9 | 125 | 328 (41) | 462 | EdTech 308, Healthcare 154; Amisha +30, Manit +13 |
| W14 13:18 to 13:39 | Manit/R Kalyan fills (ecommerce, healthcare, edtech) | 99 | 166 | 552 (69) | 817 | Ecommerce 207, Healthcare 368, EdTech 242 |
| W15 13:55 to 14:36 | slow Manit EdTech tail | 0 | 24 | 64 (8) | 88 | pushes #28 to #36 |
| W16 16:41 to 16:42 | `cobol_poc --vendor apollo` smoke test, 4 companies | 0 | 4 | 32 (4) | 36 | "36 credits for one callable lead" |
| W17 16:43 to 17:19 | `cobol_poc --vendor apollo --max-headcount 499 --max-credits 1600` | 0 | 92 | 736 (92) | 828 | 167 companies, 91 POCs, 57 callable. Chat reports exactly 828. |
| **Total 30 Sep** | | **111 (26 calls)** | **440** | **1,792 (224)** | **2,343** | EST 1 |

By purpose, 30 Sep (ledger): COBOL-India POC list 864, Healthcare 567, EdTech 705, E-commerce 207.
Deals created: E-commerce 11, EdTech 34, Healthcare 55 (100 pushed; 15 later removed as off-ICP or duplicate). Credits per pushed deal: 18.8, 20.7, 10.3.
The COBOL run is a list, not a CRM push: `legacy/companyOps/exports/cobol_sub499_poc.csv` (167 rows: 91 found / 65 no decision-maker / 10 no domain / 1 wrong title) and `exports/cobol_sub499_poc_callable.csv` (57 rows). The 57 rows were emailed to ishpreet.sood@lh2.ai; nothing was pushed to HubSpot. `exports/cobol_remaining_168_with_poc.csv` is the earlier SignalHire attempt (168 rows, all `quota_exhausted`, 0 credits).
Apollo stayed enabled in config from 11:03 IST onward (see section 4).

### Thu 1 Oct, Fri 2 Oct: 0 credits
No ledger row, no log file, no command. 1 Oct work was the edtech overlap check (HubSpot reads), 2 Oct the fintech TAM plan, where Apollo `bulk_enrich` on 5,617 domains (~5,617 credits) was priced but deliberately not run.

### Sat 3 Oct: 18 measured (medium confidence)
| Run (IST) | What | Credits | Notes |
|---|---|---:|---|
| 22:52 to 22:53 | two ad-hoc `people/match` + phone reveal probes at user's request ("are we out of direct dial credits") | 2 x (1 + 8) = 18 | Probe 1 polled the wrong (nested) `request_id`, wasted. Probe 2 succeeded but returned no number. Recorded in the ledger by hand with local-time stamps. Apollo may not actually have billed the 8 credits when no number came back, so true cost is 2 to 18. |
Data: nothing persisted. Result: direct-dial credits available (HTTP 200 + `request_id`, no 422).

### Sun 4 Oct: 0 credits. The ledger has no row dated 4 Oct IST (the only 04:23 hits are the mis-stamped Oct 3 probes).

## 6. Per-day totals

| Date (IST) | Calls by endpoint (ledgered) | Measured credits | Estimated credits | Balance readings that day (see CSV) |
|---|---|---:|---:|---|
| 2026-09-29 | `bulk_enrich` 164 calls / 1,447 domains; `people/match` 896 (of which 196 with phone reveal => 196 webhook polls); `mixed_companies/search` 0 ledgered (+2 unledgered); `api_search` free, uncounted | 3,911 (Exports-type 2,343, Mobile-type 1,568) | 13 (9 reveal probe, 2 search, 2 bulk_enrich); 0 to 2 for funded-probes | 10:33 "out of credits"; 10:48 usage screen Exports 16,365 / Mobile 35,464 / Email 786; 12:02 funded; 12:19 first 422; 12:34 last 422; 12:37 startup probe says out; 12:39 funded again; 12:45 and 12:52 funded; repo day-to-date 1,844 (13:46), 2,009 (15:04), 2,956, 3,522; Sept MTD 5,905 at 16:35 |
| 2026-09-30 | `bulk_enrich` 26 / 111 domains; `people/match` 440 (224 with phone reveal) | 2,343 (Exports-type 551, Mobile-type 1,792) | 1 | none from Apollo; repo day-to-date 0 (11:04), 574 (13:17), 966 (13:33); Sept MTD 7,384 before COBOL, 8,248 at 17:19 (chat said 8,262) |
| 2026-10-01 | none | 0 | 0 | none |
| 2026-10-02 | none | 0 | 0 | none |
| 2026-10-03 | `people/match` 2 (both with phone reveal) | 18 (Exports-type 2, Mobile-type 16) | 0 | 22:52 direct-dial "available" (no number); repo October total 18 |
| 2026-10-04 | none | 0 | 0 | user statement: ~7,800 left (task brief, not in this chat) |
| **Total** | `bulk_enrich` 190 calls / 1,558 domains; `people/match` 1,338 (422 with phone reveal) | **6,272** | **~14** | |

`usage_bucket` (Exports-type vs Mobile) is my mapping to the three Apollo counters (Exports, Mobile, Email): `people_match` and `org_enrich` assumed to land in Exports, `phone_reveal` in Mobile. This is an assumption to check against the Apollo usage page; the 35,464 = 4,433 x 8 identity supports the Mobile mapping, the Exports mapping is untested. If the Email counter is billed per revealed personal email it adds extra credits to Email that this ledger does not model.

### By purpose (ledger, 29 Sep to 3 Oct)

| Purpose | Credits | Deals/outputs |
|---|---:|---|
| Fintech TAM push (Cluster 2) | 1,900 | 135 deals |
| EdTech TAM push (Cluster 1) | 2,129 | 119 deals (+12 removed) |
| Healthcare and Medtech push (Cluster 1) | 1,075 | 93 deals |
| E-commerce push (Cluster 1) | 207 | 11 deals |
| COBOL-India POC enrichment (CSV for Ishpreet Sood) | 864 | 91 POCs, 57 callable, no CRM push |
| Wrong-number repair of live deals | 77 | 8 corrected contacts |
| Probes and tests (29 Sep manual, 3 Oct) | 19 | none |
| LinkedIn backfill test | 1 | none |
| **Total** | **6,272** | |

Deal counts are `crm_pushes` rows with status `pushed` (EdTech and Healthcare 85+34 and 38+55). Credit-per-deal ratios overstate the true unit cost, because headcount screening (org_enrich) and failed matches are bought for companies that never become deals; 1,447 + 111 = 1,558 headcount credits produced no deal by themselves.

## 7. Credit-spending events with no (or weak) local evidence

| Event | Credits | Why weak |
|---|---|---|
| 29 Sep 12:02:40 IST probe: `people/match` + phone reveal | ~9 EST | assistant said "charged to the ledger"; only the 1-credit org_enrich (id 890) was recorded |
| 29 Sep 12:52 IST `mixed_companies/search` x2 | ~2 EST | no ledger row; result shown only in chat |
| 29 Sep 12:54 IST `bulk_enrich` x2 domains | ~2 EST | no ledger row |
| 30 Sep 11:09 IST `people/match` by email | ~1 EST | no ledger row |
| 29 Sep 12:45 and 12:52 IST funded-probes (bogus name) and 31 per-run-start probes (20 on 29 Sep, 11 on 30 Sep) | 0 EST, upper bound 33 | bogus no-match calls; Apollo's billing for no-match is unverified. Counted as 0. |
| `mixed_people/api_search` calls (free per code and docs) | 0 | not counted anywhere, so volume unknown |
| Possible Email-bucket charges for `reveal_personal_emails: true` | unknown | not modelled |
| Matches and reveals that produced no deal (about 1,200 matches, ~350 reveals) | included in 6,272 | purpose known (lead attempt), but the Apollo response was not saved: `contacts` table empty, only `cost_ledger.note` holds the Apollo person id, so what each credit bought cannot be reconstructed |
| 3 Oct probes: 18 recorded, true bill unknown | 2 to 18 | see above |

Not a credit event but a trap: `repair.py` and `backfill_linkedin.py` bypass `vendors_disabled`, so a "disabled" Apollo could still be spent through them.

## 8. Balance timeline

The repo never obtained an Apollo balance number (no usage endpoint: `usage_stats` returns 404; `auth/health` reports healthy even when empty). Only these exist (full list in `companyops_balance_readings.csv`):

| IST | Reading | Source |
|---|---|---|
| 25 Sep (pre-window) | 27,524 to 27,330 (194, a different repo's run) | pasted by user |
| 29 Sep 10:33 | "out of credits" | user |
| 29 Sep 10:48 | Usage screen: Exports 16,365, Mobile 35,464, Email 786 | user paste |
| 29 Sep 12:19 to 12:34 | HTTP 422 insufficient credits | run log |
| 29 Sep 12:39 | funded again | ledger id 998 |
| 3 Oct 22:52 | direct-dial available | probe |
| 4 Oct (task brief) | ~7,800 left; 469 + 50,000 added 29 Sep | user |

Delta reconciliation: **no run in this repo has a before/after Apollo balance**, so no per-run delta check was possible. Two derived checks instead:
* Credits drawn from the first successful call (10:54:17 IST) to the exhaustion (12:19:10 IST) = 506 + 105 + 1 + 356 = 968 ledger + 9 estimated = 977. If nobody else drew on the account, the spendable balance at 10:54 was at least ~977, which is more than the "469 existing" credits the user quoted. So either part of the top-up had already landed before 10:54 IST, or the 469 is a different pool or time.
* Exhaustion happened 17 minutes after a probe at 12:02 IST showed funded, with ~365 credits spent in between. At 12:02 the balance was therefore about 365 (+ whatever Apollo's refusal threshold holds back).

## 9. Biggest unexplained gaps and caveats

1. **~36,380 credits of the ~42,669 consumed since 29 Sep cannot be attributed to companyOps.** This repo accounts for ~6,286 (about 15%). The 29 Sep audit finding stands: scripts explain only a small share of Apollo's counters; the pasted note proposed UI list exports by a person, and nothing here contradicts that.
2. **29 Sep is the day to inspect on Apollo's Team/Usage page.** The repo spent 3,911 that day between 10:54 and 16:35 IST, of which 1,568 should show under Mobile and ~2,343 under Exports. The day-to-date totals quoted in chat during the day (1,842, 2,956 ...) are mid-day snapshots of the same ledger, not separate spend.
3. Several earlier numbers are month-to-date, not daily: 2,500 / 2,962 / 5,905 (Sept MTD including 1,994 on 28 Sep). The true window total is 6,272.
4. The user's belief "Apollo disabled after 29 Sep": the config was disabled only between 17:35 IST on 29 Sep and 11:03 IST on 30 Sep, and is open in the repo now.
5. Ledger vs Apollo billing assumptions (section 3). If Apollo does not bill failed reveals or no-match calls, true spend is below 6,272; if Email credits bill separately it is above.
6. The imported `db/leadgen.sqlite` copy stores `phone_reveal` as qty 1 / credits 8 and mislabels the two Oct 3 rows as UTC; sums by `credits` agree with this report.
7. Minor chat misquotes: "7,438" (real 7,384) and "8,262" (real 8,248, ledger sum through 17:19 IST 30 Sep).

## 10. Confidence

* High: existence, timing and counts of the 1,950 ledgered calls recorded by the code in `cost_ledger` and their segment (ledger `category_id`), and the 828-credit COBOL run (ledger = the script's own count = chat).
* Medium: credits per call (cost model not balance-verified in this repo), attribution of NULL-category rows to repair (time window + code path), the four manual probes, and the 3 Oct probes.
* Low: the LinkedIn-backfill row, per-run-start probes, usage-bucket mapping, anything about Email credits.
