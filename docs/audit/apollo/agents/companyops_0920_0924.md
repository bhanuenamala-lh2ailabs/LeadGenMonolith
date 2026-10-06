# Apollo credit audit, agent by agent: companyOps, 20 to 24 Sep 2026 (IST)

Slice: Claude Code agents active 2026-09-20 00:00 to 2026-09-24 23:59 IST in the `companyOps` repo. One session, `7bb0eacd-76f8-4007-b5de-d906ec869ddc`: the main agent plus 21 subagents. Read-only forensics, no network calls, nothing under `legacy/` or `db/` modified, no key or token value reproduced (the raw transcript does contain them, for example the `.env` grep at line 7714; that is a secret-hygiene issue for the transcript itself).

Companion CSV: `docs/audit/apollo/agents/companyops_0920_0924.csv` (49 rows: 28 main-agent work packages M01 to M28, 21 subagents). Billing rules: `docs/audit/apollo/agents/endpoint_billing_table.md`. Neighbouring report for 29 Sep onward: `docs/audit/apollo/companyops.md`.

## 1. Headline

| | Credits |
|---|---:|
| **Estimated Apollo credits spent in this slice (central)** | **4,737** |
| Range (low to high) | 3,316 to 5,257 |
| Recorded in the repo's `cost_ledger` | **0** |
| Packages where an agent said or printed an after-the-run figure (chat or script log, not persisted): central of those packages | 756 (the agents stated 1,001; they over-count empty searches) |
| Packages where nobody ever stated an after-the-run figure: central | **3,981** |

Why the ledger is 0: `legacy/companyOps/tam/data/tam.sqlite3` `cost_ledger` has 2,634 rows, ids contiguous, and the first row is `2026-09-28 12:13:42` UTC. The imported copy `legacy_tam_cost_ledger` in `db/leadgen.sqlite` has the same range. The ledger (`tam/leadgen/costs.py`) did not exist yet; every Apollo call in this slice was made by ad-hoc inline Python or by the old scripts in `opsdata/`, `apollo_test/`, `apollo_proptech/`, `adtech_discovery/`, `mobility_discovery/`, `construction_discovery/`, none of which write a ledger row. The earlier report `companyops.md` section 2 item 5 says those folders were "not run"; that is true only for its own window (29 Sep onward). They were all run in this slice.

The 21 subagents made **zero** Apollo calls (they only read JSON files and classify). All spend is the main agent's.

## 2. Method and assumptions

1. Agents enumerated from the raw session: main-agent records cut at 2026-09-19T18:30Z to 2026-09-24T18:30Z (6,968 records, 760 mention Apollo, last Apollo call 19:23 IST on 24 Sep; the session has no record on 20 Sep), and every `subagents/agent-*.jsonl` whose first record falls in the window (21 of 180). Every `tool_use` input of every subagent was searched for `api.apollo`, `urllib`, `requests.`, `curl`, `apollo_api_key`, `mixed_`, `people/match`: no hit.
2. Main-agent Apollo activity was found from every Bash/Write/Edit that names Apollo or runs a script that does (`enrich_and_push`, `backfill_*`, `resolve_domains`, `richness_map*`, `collect_*`, `verify_*`, `phone_enrich_apollo_signalhire`). The scripts' own code was read in the repo copy and in the Write/Edit records.
3. Call counts come from, in order of preference: the repo's cached raw responses (`apollo_test/raw` 111 files, `apollo_proptech/raw` 278, `adtech_discovery/raw_apollo` 44, `construction_discovery/raw_apollo` 2) and the richness-map JSON outputs (which record `total_entries` per call, hence which calls returned an organisation); script loops (e.g. 303 targets, `no_linkedin=0`); printed progress and final lines in tool results; HubSpot deal `createdate` stamps for the adtech push timeline.
4. Credit model (from `endpoint_billing_table.md`): `mixed_people/api_search` and `webhook_result` polls 0; `mixed_companies/search` 1 per call that returns at least one organisation (empty = 0); `people/match` 1; `people/match` with `reveal_phone_number` +8, treated as billed when Apollo accepts the request even if no number comes back (measured on RAT, HS, CO); `organizations/bulk_enrich` 1 per domain.
5. **Central** = that model applied to the counted calls. **Low** = the strictest reading: phone reveals billed only when a number was returned, an already-enriched person is not re-billed, bulk_enrich billed per organisation returned. **High** = every search call billed whether or not it returned data, every possible Apollo phone fallback made, bulk_enrich billed per domain submitted. Where a count is not recoverable (SignalHire-first runs, M17, M18, M25) the central value assumes about one third of persons needed the Apollo fallback and is marked low confidence.
6. Not modelled anywhere (could add credits): an extra Email credit on every `reveal_personal_emails: true` call (all of M01, M03, M04, M05, M13 and every match in the adtech push); re-billing of repeat enrichment of the same person is in central but not in low.
7. Times: CSV `start_ts_ist` and `end_ts_ist` are IST; every call in the slice falls on the same calendar date in IST and UTC (earliest 06:06Z, latest 13:53Z), so the UTC-day totals below equal the IST-day totals.

## 3. Ranked by untracked credits (central)

All of it is untracked in the ledger; the right-hand column shows whether the agent at least said a figure afterwards.

| # | Pkg | What the agent did (IST) | Untracked low / **central** / high | Agent said afterwards |
|---:|---|---|---:|---|
| 1 | M03 | Fintech 303-company mobile enrichment: Apollo phone reveal first, SignalHire fallback (phone... (09-21 14:49) | 1,704 / **2,727** / 2,727 | nothing |
| 2 | M28 | Mobility Tech: full 987-company domain resolution restarted from scratch (killed at about co... (09-24 18:59) | 337 / **340** / 505 | nothing |
| 3 | M06 | Fintech Apollo account-list test: smoke search (1), org bulk_enrich smoke (10), full run to ... (09-23 13:43) | 309 / **309** / 313 | 309 credits |
| 4 | M01 | Fintech 303-company Apollo primary enrichment pass (people/match by LinkedIn URL, personal e... (09-21 14:25) | 303 / **303** / 303 | nothing |
| 5 | M16 | Adtech batch1_remaining: 21 companies, api_search + match by id + Apollo-first phone reveal ... (09-24 14:48) | 69 / **210** / 210 | nothing |
| 6 | M26 | Mobility Tech: resolve 209 candidate company names to domains via Apollo org name search (2n... (09-24 16:32) | 137 / **137** / 209 | 209 credits |
| 7 | M04 | Email enrichment pass for 105 surviving Tanisha deals (Apollo people/match first, SignalHire... (09-22 11:36) | 0 / **106** / 106 | nothing |
| 8 | M27 | Construction ICP Track A: smoke (1), richness map (28 combos, 9 non-zero), collect (9 search... (09-24 18:02) | 97 / **98** / 117 | 117 credits |
| 9 | M07 | Fintech richness map: 96 search-only calls (24 keywords x 4 headcount bands, per_page=1) (09-23 14:35) | 84 / **84** / 96 | 96 credits |
| 10 | M08 | Proptech Apollo test: smoke (1) + Pass-1 richness map (200 combos, 59 non-empty) (09-24 12:08) | 60 / **60** / 201 | 201 credits |
| 11 | M10 | Proptech collect_candidates.py: fetch real org records for every non-zero combo (59 calls, 9... (09-24 12:16) | 58 / **58** / 59 | 59 credits |
| 12 | M20 | Adtech headcount verification of 41 pushed deals via organizations/bulk_enrich (user: remove... (09-24 15:17) | 41 / **41** / 41 | nothing |
| 13 | M17 | Adtech batch2 first run (14 KEEPs, SignalHire-first phone), killed by the agent at company 1... (09-24 14:55) | 9 / **36** / 90 | nothing |
| 14 | M25 | Adtech backfill_live.py (10 companies, SignalHire-first phone, Apollo fallback) (09-24 19:21) | 8 / **35** / 53 | nothing |
| 15 | M22 | Adtech headcount re-checks and MiQ / Aiplex pushes (bulk_enrich 3 + MiQ run + 6-domain check... (09-24 15:20) | 13 / **32** / 32 | nothing |
| 16 | M02 | Phone-reveal probes on one person (webhook_url placeholder, poll with wrong id then correct ... (09-21 14:36) | 8 / **27** / 27 | nothing |
| 17 | M09 | Proptech verify_proptech.py launched and killed after ~30 s (search phase of verification) (09-24 12:15) | 18 / **18** / 18 | nothing |
| 18 | M11 | Adtech Track A: smoke (empty result) + richness map (33 combos, 18 non-zero) (09-24 12:51) | 18 / **18** / 34 | nothing |
| 19 | M23 | Adtech phone-gap retry for 6 contacts (SignalHire first, Apollo fallback) (09-24 15:29) | 0 / **18** / 18 | nothing |
| 20 | M24 | Adtech batch5 re-verification of 15 companies already known to fail the headcount rule (09-24 19:20) | 15 / **15** / 15 | nothing |
| 21 | M18 | Adtech batch2 remaining 5 companies (SignalHire-first phone) (09-24 15:04) | 3 / **12** / 30 | nothing |
| 22 | M05 | One-off probe: mobile + email for a single LinkedIn profile using the shared module (09-22 15:24) | 0 / **10** / 10 | nothing |
| 23 | M12 | Adtech collect_apollo_candidates.py: full org records for the 10 combos kept after dropping ... (09-24 14:25) | 10 / **10** / 10 | 10 credits |
| 24 | M13 | One-off probe: Apollo-first mobile + email match for a single LinkedIn profile (NeoDx contact) (09-24 13:08) | 9 / **10** / 10 | nothing |
| 25 | M15 | Adtech smoke deal #2 (Collectcent): api_search + match by id + Apollo-first phone reveal (Si... (09-24 14:47) | 1 / **10** / 10 | nothing |
| 26 | M21 | Adtech backfill_gaps.py (missing email/LinkedIn for pushed deals) + galleri5 api_search probe (09-24 15:18) | 0 / **8** / 8 | nothing |
| 27 | M19 | Adtech batch3_final2 (2 companies) and Ventes Avenues push (1) with headcount verification (09-24 15:08) | 4 / **4** / 4 | nothing |
| 28 | M14 | Adtech enrich_and_push: failed first runs (deprecated mixed_people/search 422; api_search pr... (09-24 14:46) | 1 / **1** / 1 | nothing |

Subagents (21 rows in the CSV): 0 / 0 / 0.

## 4. Totals by UTC day

| UTC day | Tracked in ledger | Untracked low | Untracked central | Untracked high | Of central, in packages where an agent stated a figure |
|---|---:|---:|---:|---:|---:|
| 2026-09-21 | 0 | 2,015 | 3,057 | 3,057 | 0 |
| 2026-09-22 | 0 | 0 | 116 | 116 | 0 |
| 2026-09-23 | 0 | 393 | 393 | 409 | 393 |
| 2026-09-24 | 0 | 908 | 1,171 | 1,675 | 363 |
| **Total** | **0** | **3,316** | **4,737** | **5,257** | **756** |

No Apollo call on 20 Sep. Per day: 21 Sep = Fintech phone enrichment (M01 to M03); 22 Sep = email pass (M04, M05); 23 Sep = Fintech account test (M06, M07); 24 Sep = Proptech, Adtech, Mobility, Construction (M08 to M28).

## 5. Agent by agent

### 5.1 Main agent (session 7bb0eacd), work packages in time order

**M01  Fintech 303-company Apollo primary enrichment pass (people/match by LinkedIn URL, personal email reveal)**  
2026-09-21 14:25:17 to 14:36:27 IST. Endpoints: POST /people/match (linkedin_url, reveal_personal_emails=true, reveal_phone_number=false).  
Billable units low/central/high: 303 / 303 / 303; credits 303 / **303** / 303 (1). Ledger: 0.  
Believed: none stated (no cost discussed; run launched as "Apollo primary enrichment pass over all 303 companies").  
Tracked how: none: no total printed or said; no cost_ledger row (ledger starts 2026-09-28 12:13 UTC).  
Evidence: chat_context/raw/companyOps/7bb0eacd-76f8-4007-b5de-d906ec869ddc.jsonl:7734 (Bash 9pE1wEVy, bg task bkdnum9hv); result :7772 ("303 apollo matched"); end :7769. Confidence: high.  
Notes: All 303 targets had a LinkedIn URL and all 303 matched (call count exact, loop has no skips). Same 303 people are enriched again in M03 and 105 of them again in M04: Apollo docs say repeat enrichment may bill again (rule 7 in endpoint_billing_table.md). Unmodelled risk: reveal_personal_emails=true may carry an extra Email credit (not in numbers).

**M02  Phone-reveal probes on one person (webhook_url placeholder, poll with wrong id then correct id)**  
2026-09-21 14:36:48 to 14:47:44 IST. Endpoints: POST /people/match reveal_phone_number=true (x3 accepted, x1 rejected 400), GET /webhook_result (free).  
Billable units low/central/high: 1 / 3 / 3; credits 8 / **27** / 27 (9 (1 match + 8 phone reveal)). Ledger: 0.  
Believed: agent quoted "credits_consumed: 8" for the one successful reveal; no running total.  
Tracked how: none: the "8 credits consumed" was quoted once in chat; the other two accepted reveals were never mentioned as spend.  
Evidence: chat_context/raw/companyOps/7bb0eacd-76f8-4007-b5de-d906ec869ddc.jsonl:7779 (5DMXkda1), :7792 (SPcWpU9R), :7796 (TtFrEdV1 -> 400, not billed), :7845 (gctJiLpr), :7850 result shows credits_consumed 8. Confidence: medium.  
Notes: Probe 1 (14:36:48) and probe 2 (14:37:20) returned HTTP 200 with a pending phone_enrichment request; the agent polled them with the wrong request id (400 invalid_request_id, polls are free), so their result was never read but the reveal was accepted (billed on acceptance per measured rule). Probe 3 (14:46:34) is the one that returned a number with credits_consumed 8. Probe 4 (no webhook_url, 14:38:34) was rejected with HTTP 400 = 0 credits. All three reveals target the same person already matched in M01, so a re-enrichment discount would put the floor near 8. Retry/double-bill: yes, 3 accepted reveals on one person.

**M03  Fintech 303-company mobile enrichment: Apollo phone reveal first, SignalHire fallback (phone_enrich_apollo_signalhire logic)**  
2026-09-21 14:49:39 to 15:36:24 IST. Endpoints: POST /people/match reveal_phone_number=true + webhook_url (placeholder) x303; GET /webhook_result polls (free).  
Billable units low/central/high: 213 / 303 / 303; credits 1,704 / **2,727** / 2,727 (9 (1 match + 8 phone) billed on acceptance). Ledger: 0.  
Believed: "credits are not the blocker" (user-pasted methodology: reveal "consumed general credits"); no per-run budget or total; progress lines showed hits, never credits.  
Tracked how: none: no cost_ledger row; progress printed apollo/sh/none hit counts only; final message reports "250 callable", not credits.  
Evidence: chat_context/raw/companyOps/7bb0eacd-76f8-4007-b5de-d906ec869ddc.jsonl:7869 (Bash PjxDL4r9, bg task bxcouibaf); result :8075 ("DONE. total=303 apollo_hits=213 sh_hits=56 no_hit=34 no_linkedin=0"); progress :7876..:8067. Confidence: high on call count, medium on price.  
Notes: apollo_mobile() is called for every one of 303 rows (no_linkedin=0, Apollo is tried first and SignalHire only when Apollo returns nothing), so 303 reveal-bearing calls. Central assumes billed on acceptance (matches measured RAT/HS/CO behaviour: failed reveals billed). Low assumes billed only when a number came back (213 hits x 8) and the match credit was a free repeat of M01. Poll window is 4 x 4 s: an unfinished reveal returns None and the row falls through to SignalHire although Apollo may still deliver and has already billed (see M23/M25 AiPlex, whose number only appeared on a later attempt). No automatic HTTP retry in this script, so no HTTP-level double bill. Biggest single untracked item in the slice.

**M04  Email enrichment pass for 105 surviving Tanisha deals (Apollo people/match first, SignalHire fallback) + 1 follow-up probe**  
2026-09-22 11:36:17 to 11:38:41 IST. Endpoints: POST /people/match (linkedin_url, reveal_personal_emails=true) x105 + 1 probe.  
Billable units low/central/high: 0 / 106 / 106; credits 0 / **106** / 106 (1). Ledger: 0.  
Believed: none stated (no credit discussion).  
Tracked how: none: no total, no ledger.  
Evidence: chat_context/raw/companyOps/7bb0eacd-76f8-4007-b5de-d906ec869ddc.jsonl:8590 (Bash kYH4qdb9, bg task bp27xxy19); result :8633 ("DONE. total=105 apollo=94 sh=10 none=1"); probe :8647 (JCrLXcUK). Confidence: high on call count, low on whether Apollo re-bills.  
Notes: Apollo is called for all 105 (SignalHire only when Apollo gave no email). All 105 persons were already matched in M01 and had a phone reveal in M03, so the floor of 0 assumes Apollo does not re-bill already-enriched people; the docs say it may. 94 returned an email, 11 did not (no-match calls may be 0 credits by docs but the CO ledger convention bills any 2xx).

**M05  One-off probe: mobile + email for a single LinkedIn profile using the shared module**  
2026-09-22 15:24:43 to 15:24:51 IST. Endpoints: POST /people/match reveal_phone_number=true (via enrich_mobile) + POST /people/match reveal_personal_emails.  
Billable units low/central/high: 0 / 2 / 2; credits 0 / **10** / 10 (9 + 1). Ledger: 0.  
Believed: none stated.  
Tracked how: none.  
Evidence: chat_context/raw/companyOps/7bb0eacd-76f8-4007-b5de-d906ec869ddc.jsonl:8744 (Bash 5Ue23crM), result :8750 (phone via apollo). Confidence: high.  
Notes: Phone returned via Apollo (success, so 8 billed even under the strictest reading). Person had been enriched in M01/M03/M04, so a repeat discount would give 0.

**M06  Fintech Apollo account-list test: smoke search (1), org bulk_enrich smoke (10), full run to 200 accounts (3 search pages + 295 bulk_enrich)**  
2026-09-23 13:43:54 to 14:30:28 IST. Endpoints: POST /mixed_companies/search x4 (1 smoke + 3 pages); POST /organizations/bulk_enrich 1 smoke batch (10) + 30 batches (295 domains); mixed_people/accounts search not used.  
Billable units low/central/high: 309 / 309 / 313; credits 309 / **309** / 313 (1 per search page; 1 per bulk_enrich domain). Ledger: 0.  
Believed: "costs exactly 1 credit", "1 credit per page", "search isn't literally free ... negligible", "Total credits spent this session: 405" (agent was right; the user brief said search is "free in the Apollo UI").  
Tracked how: printed in-script total and in chat (run: "298 credits (3 search + 295 enrich) ... total spend including smoke tests: 309"); NOT in cost_ledger (no table rows before 2026-09-28).  
Evidence: chat_context/raw/companyOps/7bb0eacd-76f8-4007-b5de-d906ec869ddc.jsonl:10093 (axTe3Yp5 smoke), :10165 (Ms7dfs74 enrich smoke), :10190 (2YwUhm4d full run), :10199-:10208 monitor events, :10230 summary; repo legacy/companyOps/apollo_test/raw/enrich_*.json (11 files), output/fintech_accounts_2026-09-23.csv. Confidence: high.  
Notes: Agent reported 309 for this part: tracked in chat only. Overshoot: loop checked target after each page, so 290 verified for a 200 target (about 90 wasted enrich credits, the agent said so). The three pages repeat the same query as the smoke and the first richness-map cell (duplicate spend of a few credits).

**M07  Fintech richness map: 96 search-only calls (24 keywords x 4 headcount bands, per_page=1)**  
2026-09-23 14:35:00 to 14:37:40 IST. Endpoints: POST /mixed_companies/search x96 (per_page=1).  
Billable units low/central/high: 84 / 84 / 96; credits 84 / **84** / 96 (1 per call returning >=1 org (0 if empty); per_page irrelevant). Ledger: 0.  
Believed: user brief: "free richness map"; agent: "search isn't literally free (1 credit/page) ... ~96 search credits", reported "96 credits spent" and "Total credits spent this session: 405".  
Tracked how: printed by script ("Total search credits spent on richness map: 96") and said in chat; not in cost_ledger.  
Evidence: chat_context/raw/companyOps/7bb0eacd-76f8-4007-b5de-d906ec869ddc.jsonl:10249 (Write richness_map.py), :10258 (DEW2Ke7L), :10276 result; legacy/companyOps/apollo_test/output/fintech_richness_map.json (84 of 96 combos non-zero). Confidence: high.  
Notes: Agent over-reports by 12 relative to the measured rule (12 empty combos are free). The 405 total in chat (1+10+298+96) equals M06 + M07 under the agent's own convention.

**M08  Proptech Apollo test: smoke (1) + Pass-1 richness map (200 combos, 59 non-empty)**  
2026-09-24 12:08:39 to 12:14:36 IST. Endpoints: POST /mixed_companies/search x201 (per_page=1).  
Billable units low/central/high: 60 / 60 / 201; credits 60 / **60** / 201 (1 per non-empty call). Ledger: 0.  
Believed: user brief: "search/browse is still free (no credits)", "Free richness map"; agent: "~210 combos/credits", "Pass 1 is done - 200 credits spent".  
Tracked how: printed by script/chat ("200 credits spent", smoke "1 credit"); not in cost_ledger.  
Evidence: chat_context/raw/companyOps/7bb0eacd-76f8-4007-b5de-d906ec869ddc.jsonl:11810 (o95fuU6o smoke), :11825 (Wj569bEg --pass1), :11929 summary; legacy/companyOps/apollo_proptech/output/proptech_richness_map_pass1.json (200 combos, 59 non-zero), raw/proptech_*.json (201 files, 60 non-empty). Confidence: high.  
Notes: Only 59 of 200 combos (plus the smoke) returned an organisation, so the measured-rule spend is 60 not 201.

**M09  Proptech verify_proptech.py launched and killed after ~30 s (search phase of verification)**  
2026-09-24 12:15:57 to 12:16:26 IST. Endpoints: POST /mixed_companies/search x18 (per_page=100); bulk_enrich phase never reached.  
Billable units low/central/high: 18 / 18 / 18; credits 18 / **18** / 18 (1). Ledger: 0.  
Believed: agent then said "Stopping it before it reaches the bulk_enrich step ... No enrich credits spent"; the 18 search credits were not mentioned.  
Tracked how: NOT tracked anywhere: no print of a total (script was killed before its summary), never reported in chat, no ledger.  
Evidence: chat_context/raw/companyOps/7bb0eacd-76f8-4007-b5de-d906ec869ddc.jsonl:11954 (za7KedEC), :11980 (NKBunzFT pkill); repo raw/verify_*.json = 18 files, all non-empty (1-6 orgs each). Confidence: high.  
Notes: Pure waste and a pure blind spot: the same non-zero combos were then re-fetched by collect_candidates.py (M10), so these 18 calls duplicate 18 of M10's queries.

**M10  Proptech collect_candidates.py: fetch real org records for every non-zero combo (59 calls, 92 unique domains)**  
2026-09-24 12:16:44 to 12:18:21 IST. Endpoints: POST /mixed_companies/search x59 (per_page=100).  
Billable units low/central/high: 58 / 58 / 59; credits 58 / **58** / 59 (1 per non-empty call). Ledger: 0.  
Believed: "92 unique domain candidates (59 credits, no enrich spent)".  
Tracked how: printed by script ("Search fetch done: 59 credits") and chat; not in cost_ledger.  
Evidence: chat_context/raw/companyOps/7bb0eacd-76f8-4007-b5de-d906ec869ddc.jsonl:11997 (BKwqQ64u), :12037 result; repo raw/collect_*.json = 59 files (58 non-empty). Confidence: high.  
Notes: No bulk_enrich was ever run for proptech in the slice (verified: no verify_enrich_batch* files in raw/).

**M11  Adtech Track A: smoke (empty result) + richness map (33 combos, 18 non-zero)**  
2026-09-24 12:51:26 to 12:56:42 IST. Endpoints: POST /mixed_companies/search x34 (per_page=1).  
Billable units low/central/high: 18 / 18 / 34; credits 18 / **18** / 34 (1 per non-empty call). Ledger: 0.  
Believed: user brief: "Confirm this stays free", "(search should be near-free)"; agent: "33 combos ~ 33 credits (near-free)".  
Tracked how: printed in chat as estimate; run log /tmp/adtech_trackA.log not preserved; not in cost_ledger.  
Evidence: chat_context/raw/companyOps/7bb0eacd-76f8-4007-b5de-d906ec869ddc.jsonl:12206 (PJxHda5s smoke), :12274 (ugqahYaV --run), :12264 estimate; repo output/adtech_richness_map.json (33 combos, 18 non-zero), raw_apollo/adtech_*.json (34 files, 18 non-empty). Confidence: high.  
Notes: Smoke combo had total_entries 0 (free by measured rule). 11 of the 33 combos were the 1-50 headcount band that the user then excluded (8 non-empty credits spent on a band that was dropped afterwards).

**M13  One-off probe: Apollo-first mobile + email match for a single LinkedIn profile (NeoDx contact)**  
2026-09-24 13:08:37 to 13:08:44 IST. Endpoints: POST /people/match reveal_phone_number=true (enrich_mobile) + POST /people/match reveal_personal_emails.  
Billable units low/central/high: 2 / 2 / 2; credits 9 / **10** / 10 (9 + 1). Ledger: 0.  
Believed: none stated.  
Tracked how: none.  
Evidence: chat_context/raw/companyOps/7bb0eacd-76f8-4007-b5de-d906ec869ddc.jsonl:12393 (13P1oULF), result :12394 (phone via apollo). Confidence: high.  
Notes: Phone returned via Apollo (success); first-time person, so no repeat discount.

**M12  Adtech collect_apollo_candidates.py: full org records for the 10 combos kept after dropping the 1-50 band**  
2026-09-24 14:25:49 to 14:26:12 IST. Endpoints: POST /mixed_companies/search x10 (per_page=100).  
Billable units low/central/high: 10 / 10 / 10; credits 10 / **10** / 10 (1). Ledger: 0.  
Believed: announced "33 more credits"; script printed "Done: 10 credits".  
Tracked how: printed by script and read by the agent (10 credits); not in cost_ledger.  
Evidence: chat_context/raw/companyOps/7bb0eacd-76f8-4007-b5de-d906ec869ddc.jsonl:12829 (3mfhUQXm), :12843 result; repo raw_apollo/collect_*.json = 10 files, all non-empty. Confidence: high.  
Notes: Repeats 10 of the 18 non-empty richness queries at per_page=100 (the richness map fetched only 1 record per combo).

**M14  Adtech enrich_and_push: failed first runs (deprecated mixed_people/search 422; api_search previews only) + probe people/match by id**  
2026-09-24 14:46:11 to 14:47:23 IST. Endpoints: POST /mixed_people/search (422 deprecated, not billed), POST /mixed_people/api_search (free) x3, POST /people/match by id x1.  
Billable units low/central/high: 1 / 1 / 1; credits 1 / **1** / 1 (0 for failed/api_search; 1 for the match). Ledger: 0.  
Believed: n/a.  
Tracked how: none.  
Evidence: chat_context/raw/companyOps/7bb0eacd-76f8-4007-b5de-d906ec869ddc.jsonl:13249/:13253 (422 error), :13270, :13275 (b8QFaz3m api_search), :13285 (DYNyAySB people/match id). Confidence: high.  
Notes: The only failed Apollo call in the slice is the 422 "endpoint is deprecated" (no billing). No 422 insufficient-credits, 429 or timeout appears in any tool result in this slice. Two later launches fail before any network call (FileNotFoundError .env at 14:46:18 and 19:20:42, TypeError in mobility script at 16:32:33).

**M15  Adtech smoke deal #2 (Collectcent): api_search + match by id + Apollo-first phone reveal (SignalHire answered)**  
2026-09-24 14:47:57 to 14:48:15 IST. Endpoints: POST /mixed_people/api_search (free), POST /people/match by id, POST /people/match reveal_phone_number=true.  
Billable units low/central/high: 1 / 2 / 2; credits 1 / **10** / 10 (1 + 9). Ledger: 0.  
Believed: n/a (this run used the shared enrich_mobile = Apollo first, 8 credits per reveal).  
Tracked how: none.  
Evidence: chat_context/raw/companyOps/7bb0eacd-76f8-4007-b5de-d906ec869ddc.jsonl:13315 (q7UyZS64), result :13316 ("phone ... (via signalhire)"). Confidence: high on structure, medium on price.  
Notes: Apollo returned no phone, SignalHire did, so Apollo's reveal was accepted and (by measured rule) billed for nothing. Low = match only.

**M16  Adtech batch1_remaining: 21 companies, api_search + match by id + Apollo-first phone reveal per person**  
2026-09-24 14:48:27 to 14:53:03 IST. Endpoints: POST /mixed_people/api_search (free) x21, POST /people/match by id x21, POST /people/match reveal_phone_number=true x21.  
Billable units low/central/high: 27 / 42 / 42; credits 69 / **210** / 210 (1 + 9 per person). Ledger: 0.  
Believed: before approval agent said "~1 credit/email + up to ~8 credits/phone reveal per person - worst case ~200 credits for this batch, likely less"; no actual total reported afterwards.  
Tracked how: none: no total printed or said after the run; no ledger.  
Evidence: chat_context/raw/companyOps/7bb0eacd-76f8-4007-b5de-d906ec869ddc.jsonl:13204/:13205 (estimate + approval), :13336 (ZQ68nib6 bg), :13469 result ("Pushed: 21 deals, 18 with a found email/phone"); repo output/adtech_keeps_batch1_remaining_pushed.json (21 persons, 16 phones). Confidence: medium.  
Notes: The process loaded the Apollo-first enrich_mobile at start; the SignalHire-first edit (14:50:42) did not affect this running process. All 21 persons had a LinkedIn URL so 21 reveals were attempted: actual central spend 210 versus the 200 "worst case" the user approved. Low: 21 matches plus 8 per Apollo phone hit with only the 6 visible "(via apollo)" lines proven.

**M17  Adtech batch2 first run (14 KEEPs, SignalHire-first phone), killed by the agent at company 10 of 14**  
2026-09-24 14:55:44 to 14:56:33 IST. Endpoints: api_search (free), POST /people/match by id x9, POST /people/match reveal_phone_number=true x0-9 (only when SignalHire misses).  
Billable units low/central/high: 9 / 12 / 18; credits 9 / **36** / 90 (1 per match; 9 per Apollo phone fallback). Ledger: 0.  
Believed: agent estimate before running: "~1-9 credits per person"; no total after.  
Tracked how: none: killed run, no output file, no total.  
Evidence: chat_context/raw/companyOps/7bb0eacd-76f8-4007-b5de-d906ec869ddc.jsonl:13558 (LGVbympP bg), :13605-:13643 monitor, :13660 (upTwUn2R pkill), :13687 result. Confidence: low.  
Notes: Matches certain for 8 companies with a contact (FirstHive, Hawky, Mailmodo, Factors, maino, Hector, Digithoughts, Stirista) plus Emovur (partial, killed). Number of Apollo phone fallbacks is not recoverable: 7 contacts ended with a phone (source not logged except Digithoughts via SignalHire). Central assumes about one third of persons needed the Apollo fallback (3 x 9). Emovur was then redone in M18 (duplicate).

**M18  Adtech batch2 remaining 5 companies (SignalHire-first phone)**  
2026-09-24 15:04:49 to 15:05:40 IST. Endpoints: api_search (free), POST /people/match by id x3, POST /people/match reveal_phone_number=true x0-3.  
Billable units low/central/high: 3 / 4 / 6; credits 3 / **12** / 30 (1 per match; 9 per Apollo phone fallback). Ledger: 0.  
Believed: none.  
Tracked how: none.  
Evidence: chat_context/raw/companyOps/7bb0eacd-76f8-4007-b5de-d906ec869ddc.jsonl:13748 (mzfo5Xwj bg), :13789-:13804 monitor, :13814 result ("Pushed: 5 deals, 3 with"); repo output/adtech_keeps_batch2_remaining5_pushed.json (3 persons, 1 phone). Confidence: low.  
Notes: Persons found: Emovur, Upcred, Aflog; Mobtions and galleri5 had no person. Two persons ended with no phone (Apollo fallback only if SignalHire returned nothing). Duplicate of Emovur from M17.

**M19  Adtech batch3_final2 (2 companies) and Ventes Avenues push (1) with headcount verification**  
2026-09-24 15:08:09 to 15:28:26 IST. Endpoints: api_search (free), POST /people/match by id x3, bulk_enrich 1 domain (Ventes run); SignalHire answered phones.  
Billable units low/central/high: 4 / 4 / 4; credits 4 / **4** / 4 (1). Ledger: 0.  
Believed: none.  
Tracked how: none.  
Evidence: chat_context/raw/companyOps/7bb0eacd-76f8-4007-b5de-d906ec869ddc.jsonl:13857 (Ea1fpTkG), :13859 result; :14187 (Pzoyw6Zz Ventes), :14189 result. Confidence: medium.  
Notes: Kayzen and Adsreverb phones came from SignalHire (Kayzen non-+91), so Apollo reveal was not called. Ventes: bulk_enrich 1 + match 1 + SignalHire phone.

**M20  Adtech headcount verification of 41 pushed deals via organizations/bulk_enrich (user: remove deals <=50 employees)**  
2026-09-24 15:17:21 to 15:17:53 IST. Endpoints: POST /organizations/bulk_enrich 5 batches (41 domains).  
Billable units low/central/high: 41 / 41 / 41; credits 41 / **41** / 41 (1 per domain). Ledger: 0.  
Believed: agent offered "~1/company, ~45 credits" and the user approved; no actual total reported.  
Tracked how: printed "resolved: 41 of 41" but no credit total; no ledger.  
Evidence: chat_context/raw/companyOps/7bb0eacd-76f8-4007-b5de-d906ec869ddc.jsonl:13870 (estimate), :13877 (user approval), :13973 (cZnWakkh), :13974 result. Confidence: high.  
Notes: All 41 domains returned an organisation, so submitted = returned.

**M21  Adtech backfill_gaps.py (missing email/LinkedIn for pushed deals) + galleri5 api_search probe**  
2026-09-24 15:18:51 to 15:19:53 IST. Endpoints: api_search (free) x10, POST /people/match by id x8.  
Billable units low/central/high: 0 / 8 / 8; credits 0 / **8** / 8 (1). Ledger: 0.  
Believed: none.  
Tracked how: none.  
Evidence: chat_context/raw/companyOps/7bb0eacd-76f8-4007-b5de-d906ec869ddc.jsonl:14015 (HuVYJjvH), :14016 result (8 "found:" persons, Adomantra and galleri5 none), :14034 probe. Confidence: high.  
Notes: All 8 persons had already been matched when the deals were created (repeat enrichment; floor 0 if Apollo does not re-bill).

**M22  Adtech headcount re-checks and MiQ / Aiplex pushes (bulk_enrich 3 + MiQ run + 6-domain check + Aiplex run)**  
2026-09-24 15:20:09 to 15:28:11 IST. Endpoints: POST /organizations/bulk_enrich (3 + 1 + 6 + 2 domains), POST /people/match by id x2, POST /people/match reveal_phone_number=true x2 (SignalHire missed both).  
Billable units low/central/high: 13 / 16 / 16; credits 13 / **32** / 32 (1 per domain; 1 per match; 9 per phone reveal). Ledger: 0.  
Believed: none.  
Tracked how: none.  
Evidence: chat_context/raw/companyOps/7bb0eacd-76f8-4007-b5de-d906ec869ddc.jsonl:14045 (RwofRg7G), :14082 (Np99SeW6 MiQ), :14164 (5yprzP3P), :14181 (oyYWbAiA Aiplex), results :14046 :14083 :14165 :14182. Confidence: medium.  
Notes: Both MiQ and Aiplex later proved to have no SignalHire result (M23, M25), so the Apollo fallback ran for each (9 each). The Ventes .in domain returned no organisation (low counts 1 of its 2 domains in the Aiplex run).

**M23  Adtech phone-gap retry for 6 contacts (SignalHire first, Apollo fallback)**  
2026-09-24 15:29:25 to 15:30:10 IST. Endpoints: POST /people/match reveal_phone_number=true x2 (AiPlex, MiQ; the other 4 were answered by SignalHire).  
Billable units low/central/high: 0 / 2 / 2; credits 0 / **18** / 18 (9). Ledger: 0.  
Believed: agent concluded "genuinely no phone available via either SignalHire or Apollo ... more enrichment attempts would not fix".  
Tracked how: none.  
Evidence: chat_context/raw/companyOps/7bb0eacd-76f8-4007-b5de-d906ec869ddc.jsonl:14223 (ftxUCRB8), :14224 result ("AiPlex | None | None ... MiQ Digital | None | None"). Confidence: high.  
Notes: Retry/double-bill case: AiPlex and MiQ had already had an Apollo reveal in M22 minutes earlier; AiPlex's number then appeared in M25 (19:22), so at least one of these reveals was probably billed and delivered after the 16 s poll gave up. Low = 0 (no number returned on this attempt).

**M26  Mobility Tech: resolve 209 candidate company names to domains via Apollo org name search (2nd launch after a TypeError)**  
2026-09-24 16:32:42 to 16:41:00 IST. Endpoints: POST /mixed_companies/search x209 (q_organization_name, per_page=3).  
Billable units low/central/high: 137 / 137 / 209; credits 137 / **137** / 209 (1 per call returning >=1 org). Ledger: 0.  
Believed: "209 search credits, cheap"; script prints "Credits spent this run: 209" (counts every call).  
Tracked how: printed by script/chat as 209 (over-count by the measured rule); not in cost_ledger; output/mobility_resolved.json has 137 resolved, 72 domain None.  
Evidence: chat_context/raw/companyOps/7bb0eacd-76f8-4007-b5de-d906ec869ddc.jsonl:14622 (W66DzNAA crashed, 0 calls), :14646 (ULfK2ERt), :14674 progress, :14845 summary; repo mobility_discovery/output/mobility_resolved.json (209 rows, 137 with domain). Confidence: medium.  
Notes: First launch (16:32:23) died on a TypeError before any request. Domain None means no organisation (or no domain) returned; those calls are free by the measured rule.

**M27  Construction ICP Track A: smoke (1), richness map (28 combos, 9 non-zero), collect (9 search) + bulk_enrich 79 domains**  
2026-09-24 18:02:49 to 18:07:25 IST. Endpoints: POST /mixed_companies/search x38 (1 smoke, 28 richness, 9 collect), POST /organizations/bulk_enrich 8 batches (79 domains).  
Billable units low/central/high: 97 / 98 / 117; credits 97 / **98** / 117 (1 per non-empty search call; 1 per enrich domain). Ledger: 0.  
Believed: "Step A1 richness map: 28 combos x 1 credit = 28 search credits"; after run "9 search + 79 bulk_enrich credits, well under budget".  
Tracked how: printed by script and stated in chat for collect (9 + 79) and richness (28); not in cost_ledger.  
Evidence: chat_context/raw/companyOps/7bb0eacd-76f8-4007-b5de-d906ec869ddc.jsonl:14977 (vThPqp9y smoke), :15006 (86hmXrP5), :15027 (mtsPM75s), :15031/:15039 summary; repo construction_discovery/output/richness_map.json, apollo_verified_candidates.json (78 verified). Confidence: high.  
Notes: 19 non-empty search calls (smoke + 9 richness + 9 collect); 28-combo map has 19 empty combos. bulk_enrich submitted 79 domains, 78 returned a verified record. Search calls here have retry-on-429/transient (3 tries): a timeout after Apollo accepted could double-bill but none is logged.

**M28  Mobility Tech: full 987-company domain resolution restarted from scratch (killed at about company 500 by "pause everything")**  
2026-09-24 18:59:00 to 19:11:43 IST. Endpoints: POST /mixed_companies/search x ~500-505 (q_organization_name, per_page=3).  
Billable units low/central/high: 337 / 340 / 505; credits 337 / **340** / 505 (1 per call returning >=1 org). Ledger: 0.  
Believed: "~987 Apollo search-only credits for domain resolution (cheap, same as before)", "~1 credit each".  
Tracked how: NOT tracked: killed before the "Credits spent" summary; progress lines only; no ledger.  
Evidence: chat_context/raw/companyOps/7bb0eacd-76f8-4007-b5de-d906ec869ddc.jsonl:15400 (agent plan), :15420 (Edit), :15424 (Md6XuVDL nohup), :15779 (event 500/987), :15783 (user "pause everything"), :15792 (ikNr8icf pkill); repo mobility_discovery/output/mobility_resolved_full.json (987 rows now, first 500 = 337 with domain, 145 of the first 500 are among the 209 already resolved in M26). Confidence: medium.  
Notes: The rewritten script starts a new output file so it re-queried 145 companies already paid for in M26 (109 of those 145 returned an organisation, so about 109 duplicate credits by the measured rule). Checkpoint every 20 rows means about 0-19 resolved rows after 500 were lost and re-paid when the run was resumed on 25 Sep (other slice).

**M24  Adtech batch5 re-verification of 15 companies already known to fail the headcount rule**  
2026-09-24 19:20:45 to 19:20:50 IST. Endpoints: POST /organizations/bulk_enrich 2 batches (15 domains).  
Billable units low/central/high: 15 / 15 / 15; credits 15 / **15** / 15 (1 per domain). Ledger: 0.  
Believed: none (agent was asked to "continue until exactly 50", launched the push script on a stale candidate file).  
Tracked how: printed "0 of 15 passed headcount>50 verification" but no credit total; no ledger.  
Evidence: chat_context/raw/companyOps/7bb0eacd-76f8-4007-b5de-d906ec869ddc.jsonl:15986 (pTWAPQqm), :15993 (747BkJmN), :15994 result. Confidence: high.  
Notes: All 15 domains returned a headcount (13 under 50 already known from M20/M22): pure repeat spend. The first launch of this script failed on a missing .env before any call (:15989).

**M25  Adtech backfill_live.py (10 companies, SignalHire-first phone, Apollo fallback)**  
2026-09-24 19:21:44 to 19:22:57 IST. Endpoints: api_search (free), POST /people/match by id x8, POST /people/match reveal_phone_number=true x2-5.  
Billable units low/central/high: 1 / 11 / 13; credits 8 / **35** / 53 (1 per match; 9 per Apollo phone fallback). Ledger: 0.  
Believed: none.  
Tracked how: none.  
Evidence: chat_context/raw/companyOps/7bb0eacd-76f8-4007-b5de-d906ec869ddc.jsonl:16023 (Write backfill_live.py), :16032 (aYWTUdmU), :16033 result; repo adtech_discovery/output/adtech_backfill_live_results.json. Confidence: medium.  
Notes: 8 contacts re-matched (all previously matched). Phone gaps for MiQ and Aiplex had no SignalHire result earlier so Apollo ran again (MiQ now 3 attempts, Aiplex 3); Aiplex finally returned a number "(via apollo)". Vertoz, Kayzen, Stirista had non-+91 SignalHire numbers so Apollo was not called. Loyalytics, BuzzOne, Manthan unknown (central counts 1 of those 3). Low = only the Aiplex success (8).

### 5.2 Subagents (21), all zero

Each is a classification worker started by the main agent with the prompt "Read the JSON file ... classify ...". Tools used: Read, Bash, Write, WebSearch, ToolSearch, Monitor, Agent, SubagentHandback. None used an HTTP client against Apollo.

| agent id | start IST | end IST | tool_use | task |
|---|---|---|---:|---|
| ad747a404be428e06 | 09-22 11:28 | 09-22 11:34 | 17 | Review 250 Tanisha fintech leads for wrong-fit (read files, classify) |
| a3141344bc3a9a71e | 09-22 18:54 | 09-22 19:00 | 17 | ICP/prefilter classification of icp_batch_1.json (read JSON, judge, hand back verdicts) |
| ac2f607424183f651 | 09-22 18:54 | 09-22 19:02 | 23 | ICP/prefilter classification of icp_batch_2.json (read JSON, judge, hand back verdicts) |
| ad18addbbdc65278c | 09-22 18:54 | 09-22 18:58 | 6 | ICP/prefilter classification of icp_batch_3.json (read JSON, judge, hand back verdicts) |
| ad660d9f513743026 | 09-22 19:11 | 09-22 19:13 | 55 | ICP/prefilter classification of unclear_batch_1.json (read JSON, judge, hand back verdicts |
| aa35914f628fac7af | 09-22 19:11 | 09-22 19:14 | 57 | ICP/prefilter classification of unclear_batch_2.json (read JSON, judge, hand back verdicts |
| ae9bf318100b12d93 | 09-22 19:11 | 09-22 19:14 | 60 | ICP/prefilter classification of unclear_batch_3.json (read JSON, judge, hand back verdicts |
| a107afe71a73c03be | 09-24 12:22 | 09-24 12:38 | 5 | ICP/prefilter classification of proptech_site_content.json (read JSON, judge, hand back ve |
| a15c02786ab299819 | 09-24 14:37 | 09-24 14:40 | 5 | ICP/prefilter classification of adtech_batch1.json (read JSON, judge, hand back verdicts) |
| a60ba9f5e06517422 | 09-24 14:51 | 09-24 14:55 | 20 | ICP/prefilter classification of adtech_batch2.json (read JSON, judge, hand back verdicts) |
| ad6c09a0a61807e32 | 09-24 14:52 | 09-24 14:53 | 6 | ICP/prefilter classification of adtech_batch2.json (read JSON, judge, hand back verdicts) |
| ac07fe690da07d837 | 09-24 14:52 | 09-24 14:54 | 5 | ICP/prefilter classification of adtech_batch2.json (read JSON, judge, hand back verdicts) |
| a9732bd27d2070d1a | 09-24 14:52 | 09-24 14:53 | 6 | ICP/prefilter classification of adtech_batch2.json (read JSON, judge, hand back verdicts) |
| a06b7bfccd1c0ad7b | 09-24 14:52 | 09-24 14:54 | 5 | ICP/prefilter classification of adtech_batch2.json (read JSON, judge, hand back verdicts) |
| ab5ddb2fdc8b433fc | 09-24 15:05 | 09-24 15:07 | 8 | ICP/prefilter classification of adtech_batch3.json (read JSON, judge, hand back verdicts) |
| a1cb16e23f5fda0a7 | 09-24 15:25 | 09-24 15:27 | 6 | ICP/prefilter classification of adtech_batch4.json (read JSON, judge, hand back verdicts) |
| ac500268fdd29d5d6 | 09-24 16:29 | 09-24 16:31 | 4 | ICP/prefilter classification of mobility_prefilter_batch1.json (read JSON, judge, hand bac |
| a0b68cc775f46b40d | 09-24 16:29 | 09-24 16:31 | 6 | ICP/prefilter classification of mobility_prefilter_batch2.json (read JSON, judge, hand bac |
| abe0be857948ab4dd | 09-24 16:29 | 09-24 16:31 | 4 | ICP/prefilter classification of mobility_prefilter_batch3.json (read JSON, judge, hand bac |
| a0962923c91d4396e | 09-24 16:29 | 09-24 16:31 | 4 | ICP/prefilter classification of mobility_prefilter_batch4.json (read JSON, judge, hand bac |
| a2f2eb712a6af5bfe | 09-24 16:29 | 09-24 16:31 | 4 | ICP/prefilter classification of mobility_prefilter_batch5.json (read JSON, judge, hand bac |

## 6. False or missing 'free' and cost beliefs

1. **The user's briefs called Apollo company search free; the agent mostly corrected it but later priced it wrongly.** Fintech brief (23 Sep 13:41): "company-level search/browse is free in the Apollo UI; verify whether the same holds via API". Proptech brief (24 Sep 12:06): "search/browse is still free (no credits)", "Free richness map". Adtech brief (24 Sep 12:47): "Confirm this stays free", "search should be near-free". The agent read the docs on 23 Sep 13:42 ("1 credit per page") and from then on counted every search page as a credit, so it never believed the search was free. The error is the opposite one: it treats **every call as one credit** ("~96 search credits", "209 search credits", "~987 search credits"), while the measured rule is 1 only if an organisation comes back. That over-counts M07, M08, M11, M26, M28 and M27 by 12, 141, 16, 72, about 165 and 19 credits respectively (high minus central in the CSV).
2. **Phone and email enrichment was never treated as a budget item at all** (M01 to M05, M13, M15, M16 to M25). The only cost sentences are pre-run estimates ("worst case ~200 credits for this batch", "~1-9 credits per person", "~45 credits" for headcount verification). No run ever printed or reported a total. The 303-company phone run (M03, central 2,727) was introduced with the user-pasted claim that credits are "not the blocker" and finished with "250 callable", no credits.
3. **"SignalHire first, Apollo for search" was believed to make the Apollo side cheap** (24 Sep 14:50, "use apollo for people search and use signalhire first for enrichment"). The people search is free, but `people/match` by id is still 1 credit per person and the Apollo fallback is a 9-credit reveal whenever SignalHire returns nothing. Result: the 21-company batch that ran Apollo-first (M16) cost about 210 against a 200 "worst case" the user approved; later runs add 2 to 10 fallbacks nobody counted.
4. **Killed or abandoned runs were assumed to have spent nothing.** M09 (verify_proptech killed after 30 s: 18 search credits), the agent then wrote "No enrich credits spent" and reported only the pass-1 and collect searches; M17 (batch2 killed at company 10); M28 (987-company run killed at about 500, never summarised).
5. **"Genuinely no phone available via either" (M23, 24 Sep 15:30)** was concluded from an Apollo reveal that had been billed; the number for AiPlex then appeared on a later attempt (M25). A failed 16-second poll is not a failed reveal.
6. **Wasted verification spend**: M24 re-verified 15 companies already known to fail the headcount rule (15 credits); M28 re-resolved 145 companies already resolved in M26.

## 7. Failed or blocked calls, retries, possible double billing

* Failed Apollo calls in the slice: exactly one HTTP 422, the deprecated `mixed_people/search` path (24 Sep 14:46:26, record 13253; not billed). Two phone-poll families returned HTTP 400 `invalid_request_id` (21 Sep, wrong request id; free) and one reveal without `webhook_url` was rejected 400 (21 Sep 14:38:34; not billed). No 422 insufficient-credits, no 429 and no timeout appears in any tool result of this slice.
* Launches that died before any network call: `.env` not found (14:46:18, 19:20:42), `TypeError` in `resolve_domains.py` (16:32:33). Zero credits.
* Retried or repeated calls that can bill twice: (a) M02, three accepted reveals on one person; (b) M23 and M25, the same two phone gaps (AiPlex, MiQ) tried at 15:20/15:28, 15:29 and 19:22, three attempts each, 16-second poll abandons a reveal that Apollo has already accepted; (c) M01 then M03 then M04 enrich the same 303/105 people repeatedly, and docs say repeat enrichment may bill again; (d) M09 then M10 run the same 18 queries twice; (e) M17 then M18 redo Emovur; (f) M28 re-queries 145 of M26's 209 companies (109 returned an organisation); (g) M06 sends the same query three times (smoke, richness cell, full run page 1); (h) library-level retries: `construction_discovery/apollo_client._post` retries timeouts and 429 up to 3 times and `apollo_account_search.search_page` retries once on 429, but no retry is logged, so no double bill is evidenced.

## 8. What is not countable, and bounds

* M17, M18, M25 (adtech SignalHire-first runs): the count of Apollo phone fallbacks is not logged. Bound: between 0 and persons-with-LinkedIn (9, 3, 5); worst case adds 81, 27, 45 credits over the matches. Central takes about one third.
* M03 price: 303 reveal-bearing calls are certain; 213 returned a number. Credits are 2,727 if billed on acceptance, 1,704 if billed only on delivery. The difference (1,023) is the largest single uncertainty and can only be settled by the Apollo Mobile counter for 21 Sep 14:49 to 15:36 IST, which this repo does not hold.
* M06: the full run counts 295 domains billed per organisation returned; if billed per submitted domain it is 299 (high).
* Other Claude sessions or users on the same Apollo key are outside this report; the main agent itself noticed a separate session (`-Users-bhanu-Desktop-hubspot`, script `verify_websites_gapfill.py`, scratch folder `cad_apollo_test`) running on the same machine at 24 Sep 19:11 IST (record 15796), which it did not touch.
* No Apollo balance or usage reading exists for 21 to 24 Sep in `companyops_balance_readings.csv` (earliest entry is dated 25 Sep), so none of these figures can be reconciled to Apollo's own counters.

## 9. Files

* `docs/audit/apollo/agents/companyops_0920_0924.md` (this file)
* `docs/audit/apollo/agents/companyops_0920_0924.csv`

No tool call was denied by a permission classifier during this analysis.
