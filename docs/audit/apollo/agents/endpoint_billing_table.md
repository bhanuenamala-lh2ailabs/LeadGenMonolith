# Apollo endpoint billing truth table (companyOps, RapidActionTeam, hubspot)

Built 2026-10-04. Read-only. Sources: every `.py` call site under `legacy/` (excluding `.venv`, `site-packages`, `node_modules`), the three audits in `docs/audit/apollo/` (`hubspot.md`, `companyops.md`, `rat.md`), and Apollo public docs pages (docs pages only, no call to `api.apollo.io`). Companion file: `endpoint_billing_table.csv` (same rows, one per endpoint, with full file:line lists).

Citation caveat. The docs pages were read with a fetch tool that summarises the page. The quoted sentences are the tool's verbatim extraction of each page's credit line and are consistent across the several pages that repeat them (People Enrichment, API Pricing), but re-open the URL before pasting a quote into anything external. The Apollo Knowledge Base article (`knowledge.apollo.io/hc/en-us/articles/9527776320781-What-Are-Credits-`) returned HTTP 403, so it is cited only as it appeared in a search result; everything below is therefore anchored on docs.apollo.io. Abbreviations: HS = legacy/hubspot, CO = legacy/companyOps, RAT = legacy/RapidActionTeam.

## 1. Truth table (compact)

Legend: BILLS = always costs credits; CONDITIONAL = depends on data returned; FREE; UNDOCUMENTED = no credit statement found.

| # | Endpoint | Docs rule | Measured | Verdict | Ledgered by (and how) |
|---|---|---|---|---|---|
| 1 | POST `/people/match`, no reveal (id, name+domain, linkedin) | 1 credit if demographics/email found; 0 if nothing found | 1 per matched call; no-match NOT isolated | CONDITIONAL | CO cost_ledger (+1 on any HTTP<400); HS script counters (+1 per call); RAT api_profile delta. Not tracked: HS/godown/*, CO discovery folders |
| 2 | POST `/people/match` + `reveal_phone_number` + `webhook_url` | +8 "if a mobile phone is returned" | 8 per reveal-bearing call, +1 unlock = 9; failed/empty reveals billed; 3 Oct landed in lead credits with 0 direct_dial | CONDITIONAL (docs) vs BILLS-ON-ACCEPTANCE (measured) | CO (+8 only if `request_id` and no sync mobile); HS (+8 unconditional per call); RAT balance delta. Not tracked: godown reveal scripts, sync-mobile path |
| 3 | `reveal_personal_emails=true` (sent on every CO match, most HS reveals) | "potentially consumes credits" | never isolated; Email bucket exists (786 pre-window) | UNDOCUMENTED | nobody |
| 4 | `run_waterfall_email/phone` | vendor-dependent, may bill with no data | not used (grep: none) | CONDITIONAL | n/a |
| 5 | POST `/people/bulk_match` (10 per call) | same as #1/#2 per person | no data; no run in window | CONDITIONAL | nobody (HS/godown/apollo_phone_reveal.py) |
| 6 | GET `/webhook_result/{id}` | 0 credits | consistent with 0 (HS reconciles within ~1 percent) | FREE | not counted (correct) |
| 7 | POST `/mixed_people/api_search` | 0 credits; no emails/phones | measured 0 (HS 3 Oct, per_page 25 and 1) | FREE | not counted (correct) |
| 8 | POST `/mixed_companies/search` | 1 credit per page, up to 100 results | 1 per call returning >=1 org, 0 if empty, per_page irrelevant | BILLS | CO cost_ledger only in `sources/apollo_orgs.py`; every other caller books 0 |
| 9 | POST `/organizations/bulk_enrich` (10 domains) | "1 credit per organization" | NOT measured by Apollo; CO books per domain submitted | BILLS (unit ambiguous) | CO cost_ledger in `outreach.py` only |
| 10 | GET `/organizations/enrich` | 1 credit per organization | NOT measured; RAT code says "free" | BILLS (docs) | nobody |
| 11 | POST `/organizations/search` (legacy path) | not in reference index | not measured | UNDOCUMENTED | nobody |
| 12 | GET `/users/api_profile?include_credit_usage=true` | 0 credits | free; dial counters stale since 3 Oct | FREE | used as a meter by RAT, godown reveal runner |
| 13 | POST `/usage_stats/credit_usage_stats` | 0 credits, master key | worked 3 Oct, the reliable meter | FREE | not in code |
| 14 | POST `/usage_stats/api_usage_stats` | 0 credits | 404 on this key | FREE | n/a |
| 15 | GET `/auth/health` | no credit sentence | reports healthy with zero balance | UNDOCUMENTED (presumed FREE) | n/a |
| 16 | `/organizations/{id}/job_postings`, `/organizations/{id}`, `/people/{id}`, `/news_articles/search` | 1 credit per page / company / person / page | not used | BILLS | n/a (would be unledgered) |
| 17 | `/contacts/search`, `/accounts/search` | 0 credits | not used | FREE | n/a |

## 2. Per-endpoint detail with citations

### 2.1 `/people/match` (rows 1, 2, 3, 4)
- URL: https://docs.apollo.io/reference/people-enrichment
- Quotes: "Credits are charged only if credit-consuming data is found: 1 credit for demographics or email, plus 8 credits if a mobile phone is returned." / "If no credit-consuming data is found, the request consumes 0 credits." / "Apollo doesn't charge a demographic credit when `match_confidence` is `none`. Email and mobile phone credit usage isn't determined by `match_confidence`." / waterfall: "some vendors consume credits per lookup even when no data is found." / `reveal_personal_emails` and `reveal_phone_number`: "This potentially consumes credits". `webhook_url` is required for phone reveal and waterfall. The page does not say whether the 8 credits are taken when the request is accepted or when the phone arrives.
- Re-enrichment: https://docs.apollo.io/docs/convert-enriched-people-to-contacts : "If you call Apollo API to enrich data for the same people in the future, you can potentially use more credits to access the same data you already accessed." The suggested fix is the Create a Contact endpoint.
- Call volume drivers: HS COBOL scripts `for p in cand[:2]` (up to 2 unlock+reveal attempts per company), 8 polls x 7 s each; HS `reveal_pocs.py` loops a 2,034-company pool with cap 3,500; CO `outreach.py` per lead and per rep, restarts re-run screening; CO `repair.py` tests repeated the same person ids; dry-run flags in HS `anthropic_poc.py`, `enrich_telehealth_sheet.py`, `enrich_ceo_leads_sheet.py` still pay; RAT `_apollo_call` retries network exceptions up to 3 times (a timeout after Apollo accepted a reveal could double-bill).
- Call sites (full list in CSV): CO `tam/leadgen/outreach.py:45,719-752` (also through `repair.py:137`, `backfill_linkedin.py:128`, `cobol_poc.py:79`); HS `TAMBuildSpecs/India/India_COBOL_IP/enrich_top50.py:82`, `enrich_batch2.py:82`, `enrich_batch3.py:82`, `enrich_batch4.py:82`, `enrich_batch5.py:82`, `enrich_batch6.py:252`, `fix_missing_contacts.py:75,80`; `TAMBuildSpecs/UK/UK_Proptech/build_leads.py:132-136`; `crm_mirror/enrich/*.py` (anthropic_poc:114, telehealth:116, ceo_leads:128, wrong_tier:101/97, fix_or_remove:92, nvidia/amazon/openai/xai name+domain unlocks); `itsvc-tam/itsvc/reveal_pocs.py:148,154`; 17 `godown/{india_startups_ops,cad_leads_2,gujarat_qualify}/*enrich*.py` scripts (unlock only); RAT `enrich_cad_founders.py:92,107`, `phone_enrich_apollo_signalhire.py:121,162,253`; CO `opsdata/phone_enrich_apollo_signalhire.py:61`, `adtech_discovery/enrich_and_push.py:118`, `backfill_gaps.py:60`, `manufacturing_discovery/enrich_and_push.py:118`.
- Measured facts used: HS 3 Oct 22:55 probe 93,374 to 93,383 = 9 lead credits, 0 direct dial (L66579); RAT 28 Sep waves, direct_dial rises in multiples of 8; RAT 12:52 wave 16 reveals = 128 dial credits but 2 phones; HS 29 Sep two probes 479 to 469 (1 + 9); CO UI Mobile bucket 35,464 = 4,433 x 8.

### 2.2 `/people/bulk_match` (row 5)
- URL: https://docs.apollo.io/reference/bulk-people-enrichment . Quotes: "1-9 credits per person without waterfall enrichment" and "You can enrich up to 10 people per request". Same per-person condition as 2.1.
- Only caller: HS `godown/apollo_phone_reveal.py:138,142,196` (BATCH_SIZE 10, both reveals true, 429 triggers a 60 s wait then a second POST of the same batch). No audit shows it running in the window.

### 2.3 `/webhook_result/{request_id}` (row 6)
- URL: https://docs.apollo.io/reference/poll-webhook-result.md . Credit consumption "0 credits". No statement about phone credits on this page.
- Callers poll up to 4 to 8 times per reveal (CO `outreach.py:709`, RAT `phone_enrich_apollo_signalhire.py:137,173`, HS reveal scripts).

### 2.4 `/mixed_people/api_search` (row 7)
- URL: https://docs.apollo.io/reference/people-api-search . Quotes: "Credit usage: 0 credits", "This endpoint doesn't return email addresses or phone numbers", example limit "600 times per hour", display limit 50,000 records (100 per page, 500 pages).
- Callers: 20 sites (CO `outreach.py:44,334`, HS COBOL/UK/crm_mirror scripts, `itsvc-tam/itsvc/reveal_pocs.py:141`, RAT). Volume is one call per company in loops; never counted anywhere (acceptable for credits, but request volume is unknown).

### 2.5 `/mixed_companies/search` (row 8)
- URL: https://docs.apollo.io/reference/organization-search . Quote: "This endpoint consumes 1 Apollo credit per page, with up to 100 results per page." Display limit 50,000 records.
- Billing-relevant request parameters: `page`, `per_page` (does not change the price), keyword/location/employee-range filters (each partition is a separate paid call).
- Volume drivers: HS `itsvc-tam/itsvc/sources/apollo_org.py:31` (28 cities x 7 ranges x 15 keywords = 2,940 partitions, up to 5 pages each, `_post` retries 5 times on 429/5xx/exceptions); HS `discover_apollo_orgs.py:24` (10 keywords x pages) and `discover_apollo_orgs2.py:24` (26 keywords); `filter_jobs_candidates.py:25,47,68` and `filter_jobs_candidates2.py:10,32,53` (2 calls per company); `enrich_batch6.py:31,183-194` (`verify_size`, 1 to 2 calls per success); `UK_Proptech/pool_apollo_orgs.py:33` (max 6 pages per query); `rat_cad_discovery/config/{track2_apollo_full,richness_map,tracks3_6_apollo}.py`; CO `sources/apollo_orgs.py:14,87`, `apollo_test/apollo_account_search.py:34`, `construction_discovery/apollo_client.py:10`, `manufacturing_discovery/apollo_client.py:10`, `mobility_discovery/resolve_domains.py:13`, `adtech_discovery/richness_map_adtech.py` (via search_page).

### 2.6 `/organizations/bulk_enrich` and `/organizations/enrich` (rows 9, 10)
- URLs: https://docs.apollo.io/reference/bulk-organization-enrichment ("1 credit per organization"; "up to ten companies with a single API call"; 600 calls per hour; no sentence on duplicate or not-found domains) and https://docs.apollo.io/reference/organization-enrichment ("1 credit per organization"; 600 per hour).
- Callers: CO `outreach.py:780,~890` (chunks of 10, charged `len(domains)`), CO `construction_discovery/collect_apollo_candidates.py:58`, `apollo_proptech/verify_proptech.py:99` (counts organisations returned), `adtech_discovery/enrich_and_push.py:33`, `manufacturing_discovery/apollo_client.py:78`; HS `godown/shutdown-radar-us/pipeline/enrich_headcount.py:39,81`; RAT `discover_cad_leads.py:180` (one `organizations/enrich` per candidate domain) and `phone_enrich_apollo_signalhire.py:109` (`apollo_resolve_org_id`, used by `discover_founders_and_push.py`).

### 2.7 Meters and unused endpoints (rows 11 to 17)
- `/users/api_profile`: https://docs.apollo.io/reference/get-current-user-profile.md "0 credits"; `include_credit_usage=true` returns lead, direct dial, export, AI, power-up usage and `total_unified_credits_used`.
- `/usage_stats/credit_usage_stats`: https://docs.apollo.io/reference/view-credit-usage-stats.md "0 credits"; master key; fields limit/consumed/left_over per credit type.
- `/usage_stats/api_usage_stats`: https://docs.apollo.io/reference/view-api-usage-stats "0 credits"; CO `doctor.py:27` calls it; 404 on this key per audits.
- `/auth/health`: https://docs.apollo.io/reference/authentication.md : only an example call, no credit statement.
- Job postings https://docs.apollo.io/reference/organization-jobs-postings.md "1 credit per page"; complete org info https://docs.apollo.io/reference/get-complete-organization-info.md "1 credit per company"; complete person info https://docs.apollo.io/reference/get-complete-person-info.md "1 credit per person"; news https://docs.apollo.io/reference/news-articles-search.md "1 Apollo credit per page, with up to 25 results per page"; contacts/accounts search "0 credits" (https://docs.apollo.io/reference/search-for-contacts.md, https://docs.apollo.io/reference/search-for-accounts.md). Consolidated list: https://docs.apollo.io/docs/api-pricing.md . Rate limits (per team, per endpoint, per minute/hour/day; enrichment endpoints consume credits): https://docs.apollo.io/reference/rate-limits .
- None of these billing-unused endpoints appears in the code. `emailer_campaigns`, `labels`, `contacts` create, `accounts`, `opportunities` and `sequences` are also not called.

## 3. Disagreements: documentation vs measured behaviour vs repo assumption

1. People match with no data. Docs: 0 credits. CO ledger books 1 per any HTTP<400 (`outreach.py:734`, so a no-match is charged); HS booked its 1-call "balance_liveness_probe" as 1 (measured delta of the 2 probes was 10 = 1 + 9, so the first probe did bill 1, but whether that call matched anyone is not recorded). Status: unresolved; CO's ledger may over-count, or Apollo bills what the docs say it does not. CO's 31 startup probes and the "funded" bogus-name probes are counted as 0 for the same reason.
2. Phone reveal on an empty result. Docs: 8 credits "if a mobile phone is returned". Measured: RAT 12:52 wave, 16 reveals billed 128 dial credits but only 2 leads ended with an Apollo phone; HS 29 Sep reveals that stayed `result_pending` were billed; HS 3 Oct probes billed 9 on a reveal. Caveat: "mobile phone returned" may mean Apollo returned a number the script then discarded (landline, non-+91, bad status), so the RAT and HS numbers are strong evidence only that script-visible failures are billed. CO's 3 Oct hand-ledgered probe pair has "true bill 2 to 18" and was never settled. Treat "failed reveal is billed" as measured but not isolated; the docs sentence literally says otherwise.
3. Credit bucket for reveals. Docs do not say which meter takes the 8. Earlier reconciliation used direct_dial (Mobile) counters in multiples of 8 (RAT 28 Sep; UI Mobile 35,464 = 4,433 x 8). On 3 Oct 22:55 a reveal moved 9 lead credits and 0 direct_dial (HS). Any ledger that reconciles reveals against the direct_dial counter after 3 Oct will show 0.
4. Organisation search. Docs say 1 credit per page; measured says 1 per call that returns >=1 organisation, 0 when empty, per_page irrelevant. These agree (docs are silent on empty pages). The disagreement is with the repos: HS `discover_apollo_orgs*.py` print "0 credits spent", `filter_jobs_candidates*.py` call it "free org search", `pool_apollo_orgs.py:88-90` registers it `is_paid=0 / 0 credits (search only)`, itsvc `apollo_org.py` claimed free until 3 Oct 22:36, RAT/CO `richness_map` files say "free richness map". Contrast: CO `sources/apollo_orgs.py:3` ("1 credit per page") and `config.yaml unit_costs` already had it right.
5. bulk_enrich unit. Docs say "1 credit per organization" and do not state whether that is per domain submitted or per organisation found. CO's ledger books per domain submitted; `verify_proptech.py:100` books per organisation returned. The brief's "billed per domain submitted" is a ledger convention, never validated against an Apollo balance delta (companyops.md section 3 says the same).
6. organizations/enrich. Docs: 1 credit per organization. RAT `discover_cad_leads.py:175-177`: "Free lookup (organizations/enrich, confirmed 0-credit in the reference methodology)". Unmeasured; rat.md section 4 hypothesises this explains hundreds of unattributed credits on 28 Sep.
7. Re-enrichment. Docs: calling enrichment again for the same people "can potentially use more credits". Repos have no cache of paid lookups; restarts re-pay (HS batch3, batch6 P1 to P4, CEO run #1 then #2, `repair.py` repeated ids). This is consistent with docs, not a disagreement, but it is spend nobody budgets.
8. API-key access. Docs: `credit_usage_stats` needs a master key or explicit access; audits saw 404/422 for usage endpoints on 29 Sep but `credit_usage_stats` worked on 3 Oct. `api_usage_stats` is 404 on this key. Does not change billing, only whether it can be metered by the key in use.
9. Agreements: `mixed_people/api_search` free (docs 0 = measured 0); `webhook_result` 0 (docs 0, consistent with counters); `users/api_profile` 0.

## 4. Each repo's own cost logic: what it counts and what it ignores

| Ledger / counter | Counts | Ignores or counts as 0 |
|---|---|---|
| CO `tam.sqlite3 cost_ledger` via `costs.charge` (`outreach.py:734,752,896`; `sources/apollo_orgs.py:87`) | `people_match` 1 (any HTTP<400); `phone_reveal` 8 (when `request_id` and no synchronous mobile); `org_enrich` len(domains); `org_search_page` 1 per non-empty page | api_search, webhook_result (fine); 14 EST manual probes; 31 startup probes (`_apollo_has_credits`); reveal credit when the match response already contains a mobile (`if reveal_phone and not mob`); email credit for `reveal_personal_emails`; requests that time out after Apollo processed them (charge happens only after a response); `repair.py`/`backfill_linkedin.py` bypass `vendors_disabled`; all legacy discovery folders (`adtech_discovery`, `manufacturing_discovery`, `construction_discovery`, `apollo_proptech`, `mobility_discovery`, `apollo_test`, `opsdata`) never write cost_ledger (they print their own totals) |
| HS `itsvc-tam/itsvc/cost_ledger.py` (`log_spend`, `check_budget`) | nothing: defined, never called, 0 rows. It is also USD-denominated while Apollo spend is credits. `pipeline.py:211` prints a worst-case estimate only | everything (3 Oct org search ~3,200 credits had no ledger row) |
| HS corpus `budget_ledger` (`TAMBuildSpecs/_corpus/schema.sql:317`) | nothing: table exists, no writer, 0 rows. `source_attempt` logs calls with no credits. `register_source(is_paid=0, cost_model="0 credits")` encodes the wrong org-search rule | everything |
| HS per-script counters (`credits += 1` unlock, `+= 8` reveal; e.g. `enrich_top50.py:127,135`, `reveal_pocs.py:214,220`) | 1 per `apollo_unlock` call, 8 per `apollo_reveal` call, unconditionally (even if no `request_id`) | `mixed_companies/search` (discover scripts print "0 credits spent"; `filter_jobs_candidates*`; `enrich_batch6.verify_size`); misses in unbuffered or killed runs (`reveal_pocs.py` prints only on success: 137 credits left no trace); `--dry-run` still pays; the ceo-leads script guesses `len(domains)` for name+domain tries |
| HS `godown/*` scripts (gujarat_qualify, india_startups_ops, cad_leads_2, apollo_phone_reveal, shutdown-radar-us enrich_headcount) | `apollo_reveal_runner.py` keeps a local `bal -= 1` per match attempt and re-reads `api_profile` | no counters at all in the other scripts; reveal and bulk_match volume unknown |
| RAT `enrich_cad_founders.py` (`api_profile` before/after, audit JSON) | whole-account balance delta and direct_dial delta per wave (includes other users' concurrent spend) | `discover_cad_leads.py` org enrich (assumed free); `icp_enrich_and_push.py` and `discover_founders_and_push.py` record no balance; nothing in RAT after 28 Sep is metered |

## 5. Structurally untracked spend (ranked)

1. `mixed_companies/search` in every script except CO `sources/apollo_orgs.py`: HS itsvc org discovery (~3,200 credits, reconstructed, range 2,400 to 4,200), COBOL discovery (46 exact, "0 credits spent" printed), COBOL size gates (~250 plus ~90 plus ~110, `verify_size`, `filter_jobs_candidates*`), UK `pool_apollo_orgs.py`, RAT `rat_cad_discovery/*`, CO `richness_map*`. Paged, partition-multiplied, and registered as free in corpus metadata.
2. `organizations/enrich` in RAT discovery (and `apollo_resolve_org_id`): docs say 1 credit, RAT code books 0, no meter.
3. `people/match` in scripts with no counter: 17 `godown/*enrich*.py` files, `rat_cad_discovery/config/enrich_both_tracks.py`, CO `adtech_discovery`/`manufacturing_discovery` `enrich_and_push.py` and `backfill_*`.
4. Phone-reveal edge cases: godown `apollo_phone_reveal.py` and `batch_enrich_100.py`; CO's synchronous-mobile path; HS reveal misses and killed runs; HS `--dry-run` runs that pay; HS reveals whose counter is `+8` even if Apollo returned no `request_id` (over-count).
5. `people/bulk_match` (godown) and `organizations/bulk_enrich` outside CO `outreach.py` (shutdown-radar-us, CO discovery folders): no counter.
6. Possible extra email credit on every `reveal_personal_emails: true` match (CO always, HS on reveal calls): not modelled anywhere.
7. Probes and no-match calls: CO 31 startup probes, "funded" probes, HS liveness probes, ad-hoc agent calls in all three chats (each 0 or 1 credit; counted inconsistently).
8. Repeat payments: restarts, duplicates, dry-runs, killed workers re-enriching the same people. Docs say repeats can bill; no repo caches paid results, and no repo converts enriched people into contacts.
9. Retried requests: RAT `_apollo_call` network retries, itsvc `_post` retries (up to 5), godown bulk_match 429 resubmit: a timeout after Apollo accepted can double-bill; the CO ledger only writes on a returned response.
10. Ledgers with no sink: itsvc `cost_ledger` and corpus `budget_ledger` (0 rows); the daily 750 cap lives only in CO's cost_ledger, so HS and RAT spend is invisible to it.
11. Outside any endpoint ledger: UI exports and other users on the same key. hubspot.md section 8.4 finds ~23,260 credits (55 percent of the 29 Sep to 4 Oct drain) not explained by any repo, split roughly evenly between phone-reveal and unlock/export categories.

## 6. One-time cheap live tests (NOT performed; no Apollo calls were made)

Use `POST /usage_stats/credit_usage_stats` before and after each test (0 credits) and test sequentially so deltas are attributable; total budget under ~45 credits.

| ID | Test | Resolves | Max cost |
|---|---|---|---|
| T1 | `people/match` with a bogus name and domain (no reveal), 3 times | docs "0 credits if nothing found" vs ledger 1 per call (disagreement 1) | 3 |
| T2 | `reveal_phone_number` on one person known to have no mobile; wait for `webhook_result` status `success` with empty phones; check lead vs direct_dial buckets | failed reveal billed? which bucket? (disagreements 2 and 3) | 9 |
| T3 | Same fresh person: match with `reveal_personal_emails` false, then a second fresh person with true | extra email credit? | 2 |
| T4 | `bulk_enrich` with 10 domains: 4 real, 4 nonexistent, 2 duplicates | billed per submitted, per unique, or per found (disagreement 5) | 10 |
| T5 | one `organizations/enrich` for a known domain | RAT "free" claim (disagreement 6) | 1 |
| T6 | one `organizations/search` (legacy path) with `per_page` 1 | unknown alias billing | 1 |
| T7 | Repeat an already-unlocked `people/match` id | re-enrichment charge (restarts) | 1 |
| T8 | `reveal_phone_number` on a person whose phone Apollo already returns synchronously | is +8 billed when CO ledgers 0 | 9 |
| T9 | `mixed_companies/search` with a page number beyond `total_pages` | empty-page billing for paged loops | 0 or 1 |
| T10 | `people/bulk_match` with 2 people, reveal false | per-person billing, and whether the godown script is safe to reuse | 2 |

Not cheaply testable: double-billing on retried requests after a timeout; infer it from the account's request counter (`x-24-hour-usage` header) against credits if it recurs.

## 7. Files

- `/Users/bhanu/Desktop/LeadGenMonolith/docs/audit/apollo/agents/endpoint_billing_table.md`
- `/Users/bhanu/Desktop/LeadGenMonolith/docs/audit/apollo/agents/endpoint_billing_table.csv`

No file under `legacy/` or `db/` was modified; no secrets were printed; no tool call was denied.
