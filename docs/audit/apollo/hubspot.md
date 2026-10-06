# Apollo credit forensics: hubspot repo, 29 Sep to 4 Oct 2026 (IST)

Scope: every Apollo.io credit spent by the `hubspot` repo (main portal: itsvc-tam, TAMBuildSpecs, crm_mirror/enrich, godown, lh2-pipeline) from 2026-09-29 00:00 IST to 2026-10-04 (now), and what each spend bought. Repo copy `legacy/hubspot`, raw Claude session `chat_context/raw/hubspot/2bf3c003-...jsonl` (live copy in `~/.claude/projects/-Users-bhanu-Desktop-hubspot/` read up to 4 Oct 21:18 IST), local logs in `/tmp`, SQLite call logs. No network calls, nothing under `legacy/` or `db/` modified, no secrets quoted.

Deliverables: this file, `hubspot_ledger.csv` (74 rows, extra column `data_location_now`), `hubspot_balance_readings.csv` (35 readings). In the CSV notes `[MEASURED]` / `[ESTIMATED]` is the first token. `L<n>` = 0-based line number in the raw jsonl.

## 1. Headline

| | Credits |
|---|---:|
| MEASURED (script's own credit counter in a log that reconciles with Apollo, or an Apollo balance delta, or an exact call log) | **7,580** |
| ESTIMATED (reconstructed from outcomes, call rates, code logic or chat claims) | **5,511** |
| **Total attributable to the hubspot repo** | **13,091** (plausible range 11.8k to 14.6k) |
| Apollo pool consumed 29 Sep 10:34 IST (479 left) to 4 Oct 00:36 IST (7,842 left) | 42,637 (user: 50,469 minus ~7,800 = 42,669) |
| Sibling repos, from their own audits | companyOps 6,286; RapidActionTeam ~1 |
| **Not explained by any repo audited so far** | **~23,260 (55%)** |

1. By IST day: 29 Sep 215, 30 Sep 2,296 (UK Proptech batch 2, CEO sheet, wrong-tier fixes, NVIDIA), 1 Oct 4,234 (India COBOL), 2 Oct 34, 3 Oct 4,819 (itsvc org search plus the first half of the 250-POC reveal), 4 Oct 1,493 (second half of the POC reveal, all before 00:36 IST). Nothing was spent after 00:36 IST on 4 Oct: the 4 Oct session was told "DO NOT USE APOLLO" and made zero Apollo calls.
2. The 3 Oct org-search discovery (itsvc-tam) is the largest single unknown: ~3,200 credits (range 2,400 to 4,200) on a free-assumed endpoint, never baselined. The 250-POC run is the best measured: 3,044 credits for 249 contacts (12.2 per contact).
3. India COBOL (1 Oct) cost ~4,160 credits for 46 HubSpot deals (~90 credits per deal), roughly 45% of it measured from logs and 45% reconstructed because logs were overwritten or processes were killed.
4. ~1,000 credits (about 8% of the total) were spent on runs whose output was lost, killed, duplicated or never produced (section 9).
5. The cross-check that matters: between the two Apollo readings 22:34:23 IST (93,354 consumed) and 00:35:57 IST (96,428) on 3-4 Oct the balance fell 3,074, and this repo's rows for that interval sum to 3,074 (2,935 from logged runs and probes plus a 137 residual that I attributed to the still-running reveal process; without the residual the gap is 139 credits, 4.5%). So the end of the window is explained by this repo. The unexplained ~23k all sits between 29 Sep 10:35 IST and 3 Oct 22:34 IST, most of it in the phone-reveal and "Exports" categories (section 8.4).

## 2. Method, evidence hierarchy, time zones

Evidence ranked: (1) Apollo balance deltas read in the chat (`credit_usage_stats`, `api_profile`); (2) exact call logs (`tam_corpus.sqlite` `source_attempt`); (3) the script's own credit counter printed in `/tmp/*.log` or in the chat (verified against Apollo only for the 3 Oct POC run, where counter 2,871 + test 36 + residual 137 = balance delta 3,045, i.e. within ~1%); (4) outcome counts times code logic (1 per unlock, 8 per reveal, 1 per non-empty org search call); (5) agent statements.

Rules applied: a log line, its chat message and the output CSV are one spend; dry-runs that still make paid calls are counted; blocked (classifier-denied) runs are 0; no spend is attributed without a call site and a time.

Time zones: all times in the ledger are IST (UTC+5:30). `/tmp` file mtimes and the repo's file mtimes are IST; raw jsonl timestamps are UTC. In run names, times with a `Z` or `UTC` suffix are UTC. Day boundaries matter: the IST day 3 Oct (00:00 IST = 2 Oct 18:30 UTC) cuts the 250-POC run in two, whereas the UTC day does not (section 4).

Sources that have NO Apollo entries in the window: `itsvc-tam/itsvc.db` `cost_ledger` (0 rows, the ledger writer was never wired), `tam_corpus.sqlite` `budget_ledger` (0 rows), `godown/founder_id/search_ledger.db` and `lh2-pipeline` `pipeline.sqlite` quota table (SignalHire only, last row July), `godown/shutdown-radar` (not modified), launchd (`ai.lh2.cadoutreach` is an email job). The 167 subagent transcripts never call `api.apollo.io` (their hits are prompts or `cat` of scripts). No other session id exists for this repo.

## 3. Balance timeline (full list in `hubspot_balance_readings.csv`)

| When (IST) | Reading | Source |
|---|---|---|
| 25 Sep | 27,524 -> 27,330 (CAD-100 run: 194) | quoted doc, pre-window |
| 28 Sep 09:10 | 11,101 left; dial used 29,428; unified used 31,340 | RAT audit (api_profile), pre-window |
| 29 Sep 10:34:50 | **479** left; dial used 35,524; lead used 1,908; unified used 37,440 | RAT audit (api_profile) |
| 29 Sep 10:35 | this repo's 2 liveness probes: -10 | derived |
| 29 Sep ~10:37 | **469** left (53,811 of 54,280 used). UI buckets: Phone 35,464, Exports 16,365, Waterfall 1,175, Email 786 | user dashboard (L58853, L58993) |
| 29 Sep 12:19 to 12:39 | HTTP 422 "insufficient credits" (sibling repo), then funded again: the 50,000 top-up lands (inferred by the companyOps audit) | companyOps audit |
| 29 Sep 14:47 / 16:09 / 17:54 | agent says ~371 / ~315 / ~310 left | STALE: subtracts from 469, ignores the top-up |
| 3 Oct 22:34:23 | **10,926** left; limit 104,280; consumed 93,354; dial pool 104,000/104,000 (stale); api_profile: dial used 55,676, lead used 1,928, unified 57,612 | L66285 |
| 3 Oct 22:34:50 | consumed 93,360 (10 org calls = 6 credits) | L66290 |
| 3 Oct 22:35:50 | consumed 93,363, left 10,917 | L66306 |
| 3 Oct 22:55:43 | 93,374 -> 93,383 (one reveal probe = 9 lead credits, 0 dial) | L66579 |
| 4 Oct 00:35:57 | consumed **96,428**, left 7,852; +30 s: 96,438, left 7,842 | L68031 |
| 4 Oct (user) | "~7,800 left" | matches the 00:36 IST reading |

Reconciliation of the user's figures: 54,280 + 50,000 = 104,280 exactly, so the top-up is confirmed in Apollo's own limit. 104,280 - 96,438 = 7,842 = the user's ~7,800. Consumption since the 469 point is 96,438 - 53,811 = 42,627 (user's 42,669 is the same figure rounded). The "allowance 104,280, consumed ~93.4k, 10.9k left at 3 Oct" in the chat summary is the pool view (not cumulative-plan accounting): it reconciles. The agent's last chat line "96,000+ remaining" was a slip for 96,000+ consumed (96,428 consumed, 7,852 left).

## 4. Per-day totals

IST days (the way the ledger is cut). "Calls" are approximate: reveal-bearing `people/match` calls cost 9 (1 unlock + 8 reveal); name/ID matches cost 1; `mixed_companies/search` costs 1 per non-empty call; `mixed_people/api_search` is free and not counted.

| Date (IST) | Calls by endpoint | Credits M | Credits E | Total | Balance readings that day |
|---|---|---:|---:|---:|---|
| Tue 29 Sep | `people/match` ~167 (~160 unlock-only, ~7 reveal-bearing); org search 0 | 202 | 13 | 215 | 479 (10:34), 469 (~10:37), 53,811 consumed (UI), buckets; top-up ~12:38 (inferred); 3 stale agent estimates |
| Wed 30 Sep | `people/match` ~790 (~570 unlock/identity, ~216 reveal-bearing); org search 0 | 1,999 | 297 | 2,296 | none from Apollo |
| Thu 1 Oct | `people/match` ~915 (~485 unlock/identity, ~430 reveal-bearing); org search ~313 billed calls (46 exact + ~270 in size checks) | 2,370 | 1,864 | 4,234 | none from Apollo |
| Fri 2 Oct | `people/match` 34 name matches | 34 | 0 | 34 | none |
| Sat 3 Oct | org search ~3,200 billed calls (of ~4,217 requests incl. free empty ones); `people/match` ~395 (~224 unlock, ~174 reveal-bearing, mostly the POC run until midnight) | 1,619 | 3,200 | 4,819 | 10,926 / 93,354 (22:34), 93,360, 93,363, 93,374, 93,383; request counter 4,217 (19:56) and 4,231 |
| Sun 4 Oct | `people/match` ~330 (~166 unlock, ~166 reveal-bearing) until 00:36 IST, then 0 | 1,356 | 137 | 1,493 | 96,428 consumed / 7,852 left (00:35:57), 96,438 (00:36:27) |
| **Total** | | **7,580** | **5,511** | **13,091** | |

If the Apollo website buckets by UTC day instead: 29 Sep 215 (202 M / 13 E), 30 Sep 2,296, 1 Oct 4,234, 2 Oct 34, 3 Oct **6,312** (2,975 M / 3,337 E; includes the entire POC reveal and org search), 4 Oct 0. If it buckets by IST day use the table above. The 3 Oct vs 4 Oct split of the POC run (1,515 before midnight IST, 1,356+137 after) is interpolated from the agent's progress messages at 18:27:36Z (cr 1,413), 18:28:45Z (1,476), 18:31:05Z (1,548); the run total is measured.

Split by Apollo bucket (this repo, everything): phone-reveal category ~7,940 credits (8 per reveal-bearing call), non-phone (unlocks and org search) ~5,150. Before the 22:34 IST reading on 3 Oct: ~5,220 phone, ~4,800 non-phone.

## 5. Day by day (what ran, credits, purpose, where the data is now)

Table legend: M = measured, E = estimated; Conf. = h/m/l. Full evidence refs and data locations per row are in `hubspot_ledger.csv`.

### Tue 29 Sep: audit, then three small enrichments. Day total 215 (M 202 / E 13)

Context: 10:33 IST the user reports "out of credits" and asks for the split of usage since 22 Sep. The agent finds no usage endpoint (`usage_stats/api_usage_stats` 404, `credit_usage`/`users/credit_usage` 404/422), runs two liveness probes (10 credits, confirmed by the 479 -> 469 drop), and reconstructs spend from artefacts (section 8.1). Everything before 10:35 IST (UK batch-1 1,033 credits ended 28 Sep 22:22 IST; Singapore 27 Sep; CAD-100 25 Sep) is outside the window. No Apollo call from this repo between 00:00 and 10:35 IST.

| Time IST | Run / action | Credits | M/E | Conf. | Purpose |
|---|---|---:|:-:|:-:|---|
| 10:35 | agent ad-hoc (audit): balance_liveness_probe | 1 | M | m | Audit: test whether Apollo still serves paid calls when UI showed 'ou… |
| 10:35 | agent ad-hoc (audit): reveal_liveness_probe | 9 | M | m | Audit: test whether mobile reveals still accepted |
| 14:37 | enrich_telehealth_sheet.py: telehealth --dry-run (still makes paid calls) | 2 | M | m | Telehealth India BD sheet: find POC email/mobile |
| 14:38 | enrich_telehealth_sheet.py: telehealth live --max-credits 380 | 98 | M | h | Telehealth India BD sheet (35 companies) POC contact enrichment |
| 15:58 | agent ad-hoc (Anand Sutaria): linkedin lookup 1 person | 1 | E | m | User asked for one CEO's contact (IMS Learning Resources CEO) |
| 15:58 | agent ad-hoc (Anand Sutaria): phone reveal (noop webhook) | 8 | E | l | Same person: mobile reveal |
| 16:00 | agent ad-hoc (Anthropic roster): 2 name+domain test matches | 2 | E | m | Test cheapest way to resolve a 44-name Anthropic roster |
| 16:01 | anthropic_poc.py: live run incl. mobile/personal-email reveal (BLOCKED) | 0 | M | h | Anthropic PoC roster incl. mobiles |
| 16:05 | anthropic_poc.py: --no-phone --dry-run (still pays) | 44 | M | h | Anthropic data-sales PoC roster: LinkedIn/title/work email |
| 16:06 | anthropic_poc.py: --no-phone live | 44 | M | h | Anthropic data-sales PoC roster: LinkedIn/title/work email |
| 17:53 | agent ad-hoc (Anthropic follow-up): 3 named people (+1 misspelling) | 4 | M | h | Emails for Hadley Mouritzen, Genesis Apice, Michael Pearson (x2 spell… |
| 17:54 | agent ad-hoc (Anthropic follow-up): Genesis Apice org_name retry | 1 | E | m | Resolve Genesis Apice employer |
| 18:04 | agent ad-hoc (Anthropic follow-up): Genesis Apice by LinkedIn URL | 1 | E | m | Verify Genesis Apice employer via LinkedIn |

Data now: telehealth results are in the Google Sheet tab `India` (gid 1567101871) and `telehealth_india_enriched_2026-09-29.csv` (35 rows); the Anthropic roster is in sheet `Antrhopic_PoC` and `anthropic_poc_2026-09-29.csv` (44 rows; 44 matched, 36 emails, 0 phones); Anand Sutaria, Hadley Mouritzen, Genesis Apice, Michael Pearson exist only in the chat. Nothing from this day went to HubSpot. Two attempts to run the Anthropic mobile/personal-email reveal (10:31:37Z and 10:34:24Z) were denied by the Claude Code permission classifier before executing: 0 credits.

### Wed 30 Sep: UK Proptech batch 2, CEO sheet, wrong-tier fixes, NVIDIA. Day total 2,296 (M 1,999 / E 297)

| Time IST | Run / action | Credits | M/E | Conf. | Purpose |
|---|---|---:|:-:|:-:|---|
| 11:04 | agent ad-hoc (Chinese AI-lab rost…: 17 names -> 12 emails | 27 | E | h | Apoorv's Tencent/ByteDance/Qwen roster: work emails |
| 14:50 | agent ad-hoc: balance_liveness_probe | 1 | E | m | Check Apollo alive before UK batch 2 |
| 14:50 | build_leads.py: leads2_yuktha.log (--target 50 --workers 6, 17.3 min) | 990 | M | h | UK Proptech lead build: 50 CEO-tier POCs with UK mobiles pushed to Yu… |
| 15:42 | enrich_ceo_leads_sheet.py: --dry-run (killed by 5 min tool timeout; dry-run still pays) | 35 | E | l | CEO leads sheet (48 proptech/fintech CEOs): email+mobile |
| 15:47 | enrich_ceo_leads_sheet.py: live run #1 pid 21442 (killed 43.7 min later) | 220 | E | l | CEO leads sheet: email+mobile (first attempt) |
| 16:32 | enrich_ceo_leads_sheet.py: live run #2 (Apollo-only, cap 350, pid 28543) | 350 | M | h | CEO leads sheet (48 rows): 34 emails, 32 phones |
| 17:22 | fix_wrong_tier_pocs.py: round 1 (42 wrong-tier deals, cap 380) | 225 | M | h | Replace wrong-tier POCs (HR/sales) on 92 deals pushed 30 Sep: CEO/CTO… |
| 17:39 | fix_wrong_tier_round2.py: round 2 (26 Yuktha rows, cap 300) | 111 | M | h | Second pass on still-wrong-tier UK POCs |
| 17:54 | enrich_nvidia_sheet.py: 20-name test, cap 20 | 20 | M | h | NVIDIA reference sheet: designation/company/LinkedIn/email |
| 17:54 | enrich_nvidia_sheet.py: remaining 303 names, cap 340 | 303 | M | h | NVIDIA reference sheet (323 names) |
| 18:07 | agent ad-hoc (no-contact fix): 3 name+domain matches + 1 retry | 4 | E | m | Find LinkedIn for 3 UK deals pushed without contact (Darren Jones, Da… |
| 18:11 | fix_or_remove_yuktha_uk.py: 38 deals fix-or-remove, cap 300 (crashed on DNS error ~12:49Z) | 10 | E | l | Fix-or-archive wrong-tier UK deals |

Data now: 50 UK_Proptech deals (owner Yuktha, pipeline CoOps (Global) `2425754306`, created 14:51-15:07 IST; 2 archived by `fix_or_remove`; 11 UK contacts replaced by the POC-fix rounds, plus 7 US `Cold Call ( Proptech US )` deals that were not built by this repo; `db/leadgen.sqlite` `deal` lead_source `uk_proptech`: 150 total = 100 on 28 Sep + 50 on 30 Sep); `uk_proptech_leads.csv` (50 rows); `poc_correction_2026-09-30.csv` (42 rows); CEO sheet `ceo leads` tab Leads F-G (40 rows) and `ceo_leads_enriched_2026-09-30.csv` (46 rows); NVIDIA tab of reference sheet `1xuoBloY...` (323 rows); the Chinese-lab emails went out by mail to apoorv.vashist@lh2.ai. The lost spend here: CEO dry-run (35) and live run #1 (~220) wrote nothing, because the script batched sheet writes to the end and was killed at 43.7 min, then run #2 paid again for the same 48 people.

### Thu 1 Oct: India COBOL-IP TAM and two sheet tabs. Day total 4,234 (M 2,370 / E 1,864)

| Time IST | Run / action | Credits | M/E | Conf. | Purpose |
|---|---|---:|:-:|:-:|---|
| 11:36 | enrich_nvidia_sheet.py: 19 new names | 19 | M | h | NVIDIA reference sheet: 19 added names (342 total) |
| 11:41 | agent ad-hoc (NVIDIA mismatch che…: Preeti Chadha, Shaan Jain re-match | 2 | E | m | Investigate wrong emails on 2 NVIDIA rows |
| 11:52 | enrich_amazon_sheet.py: 10-name test + 26 remaining (36 names) | 36 | M | h | Amazon reference sheet tab: designation/company/LinkedIn/email |
| 12:38 | discover_apollo_orgs.py: cobolapollo-1790838524 (10 keywords, 16 pages) | 16 | M | m | COBOL IP TAM: Apollo org discovery. Script printed '0 credits spent'… |
| 12:58 | enrich_top50.py: top-50 COBOL candidates, cap 400 | 252 | M | h | India COBOL-IP owners: CEO/Founder + +91 mobile (21 cr/enriched lead) |
| 13:22 | enrich_batch2.py: batch2 (90 feeder, target 20, cap 700) | 315 | M | h | India COBOL-IP: 20 more enriched leads |
| 13:35 | discover_apollo_orgs2.py: cobolapollo-1790841891 (26 keywords, 32 pages) | 30 | M | m | COBOL IP TAM: expanded org discovery (1,452 companies) |
| 13:38 | enrich_batch3.py: batch3 first launch (killed after ~3 min to get unbuffered ou… | 50 | E | l | India COBOL-IP batch3 (restarted: same feeder re-processed) |
| 13:41 | enrich_batch3.py: batch3 (120 feeder, target 25, cap 900) | 297 | M | h | India COBOL-IP: 25 enriched leads |
| 14:04 | agent ad-hoc (org-search size pro…: 2 org-name probes + 1 + 14 names x<=2 calls | 17 | E | l | Check whether org-search headcount filter works; verify sizes of gian… |
| 14:05 | filter_jobs_candidates.py: size-verify 225 LinkedIn-jobs companies | 50 | E | m | COBOL jobs-posting candidates: drop >500 headcount |
| 14:09 | enrich_batch4.py: batch4 (19 job-posting companies, cap 250) | 90 | M | h | COBOL: companies hiring COBOL devs |
| 14:12 | enrich_batch5.py: batch5 (4 title-level companies) | 0 | M | h | COBOL: 4 'COBOL developer' title matches |
| 14:15 | enrich_batch6.py: sequential launches P1+P2 (P1 killed 08:46:40Z, P2 killed 08:… | 340 | E | l | COBOL jobs-pool enrichment (LinkedIn Jobs/Naukri sourced, incl. many… |
| 14:25 | enrich_batch6.py verify_size(): per-success size gate (hidden org-search calls) | 90 | E | l | COBOL jobs-pool: headcount gate before CSV write |
| 14:26 | enrich_batch6.py: sequential launch P3 (killed 08:59:37Z to add mega-brand gate) | 160 | E | l | COBOL jobs-pool enrichment (restart; repeats ACROSSTEK/Mercans) |
| 14:29 | enrich_batch6.py: sequential launch P4 pid 96252 (killed 09:22:26Z to paralleli… | 375 | E | m | COBOL jobs-pool enrichment (BNY, Netflix, Chubb, Eurofins... giants) |
| 14:52 | enrich_batch6.py: 4-worker parallel run w1 (cap 225 each) | 162 | M | h | COBOL jobs-pool enrichment: remaining 93 companies split over 4 worke… |
| 14:52 | enrich_batch6.py: 4-worker parallel run w2 (cap 225 each) | 135 | M | h | COBOL jobs-pool enrichment: remaining 93 companies split over 4 worke… |
| 14:52 | enrich_batch6.py: 4-worker parallel run w3 (cap 225 each) | 180 | M | h | COBOL jobs-pool enrichment: remaining 93 companies split over 4 worke… |
| 14:52 | enrich_batch6.py: 4-worker parallel run w4 (cap 225 each) | 180 | M | h | COBOL jobs-pool enrichment: remaining 93 companies split over 4 worke… |
| 15:12 | enrich_batch6.py: web-search feeder (3 companies, cap 50) | 45 | M | h | COBOL: 3 companies found by a web-search sub-agent |
| 15:16 | enrich_batch6.py: batch7 w3 (cap 180) | 180 | M | h | COBOL jobs-pool batch7 (81 companies, only w3 survived) |
| 16:12 | filter_jobs_candidates2.py: size-verify 718 fresh Naukri/LinkedIn companies | 110 | E | m | COBOL jobs-posting candidates round 2: drop >500 |
| 16:14 | enrich_batch6.py: batch8 w1 (cap 400; ran alone 10:44-11:19Z, killed 11:28Z) | 140 | E | l | COBOL batch8 (540 weak-signal companies) worker 1 |
| 16:49 | enrich_batch6.py: batch8 w2-w6 relaunch (killed 11:28:07Z on user 'stop every p… | 180 | E | l | COBOL batch8 workers 2-6 |
| 17:13 | enrich_batch6.py: batch9 w1 (79-company priority list; cap 150) | 150 | M | h | COBOL batch9 priority |
| 17:13 | enrich_batch6.py: batch9 w2 (cap 150) | 148 | M | h | COBOL batch9 priority |
| 17:13 | enrich_batch6.py: batch9 w3 first run (killed 11:48Z to add rotation logic) | 150 | E | m | COBOL batch9 priority |
| 17:18 | enrich_batch6.py: batch9b w3 remaining 10 | 135 | M | h | COBOL batch9 priority |
| 18:29 | fix_missing_contacts.py: attach contacts to 21 bare COBOL deals | 200 | E | l | COBOL: deals pushed with no contact object -> re-enrich |

Data now: `india_cobol_top50/batch2/3/4/5_enriched.csv` in `legacy/hubspot`, `RapidActionTeam/india_cobol_ip_{top12,batch2,batch3}_enriched_2026-10-01.csv`, 46 HubSpot deals (Coding pipeline `default`, stage Cold Call `3992480462`, lead_source `COBOL`, owners Lamiya `96574824` then half moved to Yuktha `96573782`; 10 later got contact objects via `fix_missing_contacts.py`, 8 of the 10 are recruiters/HR); discovery rows in `tam_corpus.sqlite` (vertical `india_cobol_ip`, 994 + 1,452 companies, imported to `legacy_corpus_*` in `db/leadgen.sqlite`). Amazon and NVIDIA tabs in the reference sheet.

How the 1 Oct COBOL total (4,160) is built: enrichment runs that ended with a final script counter 2,269 (M: 252, 315, 297, 90, 0, 45, 4-worker run 657, batch7 w3 180, batch9 148 + 150 + 135); enrichment reconstructed from killed or log-overwritten runs 1,595 (E: batch3 first launch 50, batch6 launches P1-P4 875, batch8 320, batch9 w3 first run 150, fix_missing_contacts 200); org-search side calls 296 (46 exact from the discovery call log, ~250 from the size checks). `discover_apollo_orgs.py` printed "0 credits spent", and `filter_jobs_candidates*.py` and `verify_size()` call `mixed_companies/search` and describe it as free: all of it is billed.

### Fri 2 Oct: OpenAI tab. Day total 34 (M 34 / E 0)

| Time IST | Run / action | Credits | M/E | Conf. | Purpose |
|---|---|---:|:-:|:-:|---|
| 12:15 | enrich_openai_sheet.py: 32 names (cap 70) | 32 | M | h | OpenAI reference sheet tab |
| 12:18 | enrich_openai_sheet.py: 2 more names | 2 | M | h | OpenAI reference sheet tab |

Data now: OpenAI tab (gid 1880644822) of the reference sheet; designation/company/LinkedIn/work email, no mobiles.

### Sat 3 Oct (IST): itsvc org-search discovery, xAI, credit-cost tests, start of the POC reveal. Day total 4,819 (M 1,619 / E 3,200)

| Time IST | Run / action | Credits | M/E | Conf. | Purpose |
|---|---|---:|:-:|:-:|---|
| 18:54 | pipeline.py: --sample 2 (Pune) + --sample 3 (Pune) smoke tests | 8 | E | l | itsvc TAM build: smoke tests |
| 18:55 | pipeline.py: full run #1 pid 67391 (killed after 2.3 min: headcount bug) | 320 | E | l | itsvc TAM (every Indian IT-services company) - discovery |
| 18:57 | agent ad-hoc: inspect org-search response keys | 1 | E | m | Find headcount field in response |
| 18:59 | pipeline.py: full run #2 pid 68407 (2,940 partitions, 25.7 min) | 2,860 | E | l | itsvc TAM: Apollo org discovery of Indian IT-services universe (61,79… |
| 19:56 | agent ad-hoc: response-header probe for request counters | 1 | E | m | Look for credit info in headers |
| 22:27 | enrich_xai_sheet.py: 50 names (cap 110) | 50 | M | h | xAI reference sheet tab |
| 22:34 | agent ad-hoc (credit-cost test): 10 org-search calls (balance before/after) | 6 | M | h | User asked: does org search spend credits? |
| 22:35 | agent ad-hoc (credit-cost test): 3 single-call tests + 1 stats call | 3 | M | h | Per-call vs per-org billing test |
| 22:44 | agent ad-hoc (reveal probe): reveal on Amazatic CTO (no phones returned) | 9 | E | l | Does a reveal work while direct_dial=0? |
| 22:55 | agent ad-hoc (reveal probe 2): reveal probe with before/after balance | 9 | M | h | Prove reveals bill 9 lead credits, 0 direct_dial |
| 23:05 | agent ad-hoc: inspect org record keys again | 1 | E | m | Look for location fields |
| 23:17 | reveal_pocs.py: --target 3 --max-credits 60 test | 36 | M | h | itsvc POC reveal: smoke test |
| 23:19 | reveal_pocs.py: full run --target 250 --max-credits 3500 (part 1: until 00:00… | 1,515 | M | m | itsvc-tam: 250 city-diverse CEO/Founder/MD/CTO POCs with +91 mobiles… |

Data now: `itsvc-tam/itsvc.db` (`raw_records` source `apollo_org` 61,799 rows, `candidates`, `entities`, classification, crawl text; 138 MB), `itsvc-tam/out/hubspot_ready.csv`, `not_entering_hubspot.csv`, mirrored in `db/leadgen.sqlite` `legacy_itsvc_*` (61,799 each); run #1 data was deleted; xAI tab (gid 952064717); `poc_callable_250.csv` is the POC output (see Sun 4 Oct).

### Sun 4 Oct (IST): rest of the POC reveal, then nothing. Day total 1,493 (M 1,356 / E 137)

| Time IST | Run / action | Credits | M/E | Conf. | Purpose |
|---|---|---:|:-:|:-:|---|
| 00:00 | reveal_pocs.py: full run (part 2: 00:00 IST to last logged win 00:28 IST) | 1,356 | M | m | itsvc POC reveal (continued) |
| 00:28 | reveal_pocs.py: full run (part 3: failed attempts for the 250th contact until… | 137 | E | m | itsvc POC reveal: the process kept paying for ~7.7 min for one more c… |
| 20:44 | hubspot Claude session (Shutdown…: user instruction 'DO NOT USE APOLLO at all' | 0 | M | h | Shutdown-India TAM accounts built from registry data only |

Data now: `legacy/hubspot/itsvc-tam/out/poc_callable_250.csv` (249 data rows, CEO/Founder/MD/CTO-tier, +91 mobile, city-diverse; flagged non-ICP rows: ADCC Academy, TechnoKraft Training, Fireblaze AI School, Pesto Tech, AnalytixLabs, Weboin, SERP WIZARD, Petroleum Engineers Associates, NSUT Incubation, Namo Padmavati Outsourcing). Not pushed to HubSpot, not imported into `db/leadgen.sqlite` (the legacy_itsvc tables only hold discovery). The reveal process was killed at 00:36:01 IST (pid 87856). From 20:44 IST the same session was asked to build Shutdown-India accounts "DO NOT USE APOLLO": zero calls.

## 6. By purpose

| Purpose | M | E | Total | Notes |
|---|---:|---:|---:|---|
| India COBOL-IP TAM (discovery, enrichment, size checks, fixes) | 2,315 | 1,845 | 4,160 | 46 deals, ~90 credits/deal |
| itsvc org-search TAM discovery | 0 | 3,188 | 3,188 | 61,799 distinct orgs, ~0.05 credit/org |
| itsvc POC reveal (250 CEO mobiles) | 2,907 | 137 | 3,044 | 249 contacts, 12.2 credits/contact |
| UK Proptech batch 2 + wrong-tier fixes | 1,326 | 14 | 1,340 | 990 for the 50 leads (19.8/lead) + 336 fixing 18 of 42 wrong-tier POCs (18.7/fix) |
| CEO-leads and telehealth sheets | 450 | 255 | 705 | 40 + 35 rows |
| Reference rosters (NVIDIA 342, Amazon 36, OpenAI 34, xAI 50, Anthropic 96 incl. 44 names paid twice, China labs 27) | 554 | 33 | 587 | 1 credit per person, no mobiles |
| Probes, tests, audits | 28 | 39 | 67 | liveness, billing tests, headers |
| **Total** | **7,580** | **5,511** | **13,091** | |

Day x purpose (IST, credits): COBOL 1 Oct 4,160; itsvc org search 3 Oct 3,188; POC reveal 3 Oct 1,551 + 4 Oct 1,493; UK Proptech 30 Sep 1,340; CEO/telehealth 29 Sep 100, 30 Sep 605; rosters 29 Sep 96, 30 Sep 350, 1 Oct 57, 2 Oct 34, 3 Oct 50; probes 29 Sep 19, 30 Sep 1, 1 Oct 17, 3 Oct 30.

## 7. Cost semantics and call-site facts

| Endpoint / action | Cost | Basis |
|---|---|---|
| `mixed_people/api_search` | 0 | measured 3 Oct (L66359: per_page 25 and 1, credits ZERO) |
| `mixed_companies/search` | **1 lead credit per call that returns at least 1 org; 0 for an empty result; independent of `per_page` and of org count** | measured 3 Oct 22:34 IST: 10 calls / 553 orgs = 6 credits; 1 call 10 orgs = 1; per_page=1 = 1; 0 results = 0. Until 22:36 IST that day the code (`itsvc/sources/apollo_org.py`, `discover_apollo_orgs*.py`, `filter_jobs_candidates*.py`, `enrich_batch6.verify_size`) and the agent said "free, verified repeatedly": it had never been measured |
| `people/match` by id / name+domain / LinkedIn ("unlock") | 1 | scripts, audit; 3 Oct probe 9 = 1 + 8 |
| `people/match` + `reveal_phone_number` (+ `reveal_personal_emails`, dummy webhook) | +8 on top of the unlock = 9 lead credits, 0 from the stale `direct_dial_credit` meter | measured 3 Oct 22:55 IST (93,374 -> 93,383). A reveal request that stayed `result_pending` was still billed on 29 Sep (479 -> 469 for two probes). Whether a *completed* reveal with no phone is billed is not isolated, but the 29 Sep evidence points to "billed on acceptance". Script counters assume 8 per attempt and reconcile with Apollo within ~1% on the one run that could be checked |
| `GET webhook_result/{request_id}` | 0 | assumption; consistent with counters |
| `usage_stats/credit_usage_stats` (POST), `users/api_profile?include_credit_usage=true` | 0 | the only credit-balance endpoints; `usage_stats/api_usage_stats` is 404 on this key |

Behaviours that cost credits without producing output (all with call sites in the code):
- `--dry-run` on `enrich_telehealth_sheet.py`, `anthropic_poc.py`, `enrich_ceo_leads_sheet.py` only skips the sheet write, not the paid Apollo calls (44 credits duplicated on the Anthropic list alone).
- No cache of previously paid lookups: every restart re-pays (batch3 first launch, batch6 launches P1-P4 re-processing Mercans, ACROSSTEK, FinBox; CEO run #1 then #2).
- Killed processes lose everything buffered: CEO run #1; batch8 workers; POC reveal prints only on success so its last ~8 minutes of misses (137 credits) left no trace except the balance.
- `unlock then reject`: `enrich_batch6.py` unlocks (and in places reveals) first and only then rejects companies over 500 staff or mega-brands (Hero FinCorp, eClerx, Aditya Birla Capital, Bounteous, Capgemini), paying for giants.
- Org-search paging up to 5 pages per partition, retries on 429/5xx are not billed unless a 200 with orgs returns.
- HubSpot 429 crashes at startup of parallel workers (batch7 w1/w2/w4, batch8 w2-w6): no Apollo calls, no spend.

## 8. The three leads from the chat summary, verified

### 8.1 29 Sep audit: "only ~9% of 16,365 Exports explained by scripts"
Confirmed as stated in the chat (L58925, L58993): scripts accounted for ~1,433 of 16,365 Exports (CAD-100 194, Mobility ~20, Singapore 381, UK 164, SG OutFlo 19, others), 91% unexplained; Phone 35,464 = 4,433 reveals x 8; the agent's reconstruction of 22 Sep to 29 Sep script spend was ~5,782 against ~26,900 consumed since the 25 Sep 27,330 reading. Verified against local evidence: the UK 1,033 run was 28 Sep 22:10-22:22 IST (`leads.log`), the Singapore run 27 Sep, so none of it is in this window. New with the API counters (RAT's 479 reading, my 3 Oct reading): pool consumed minus api_profile `total_unified_credits_used` = 53,801 - 37,440 = **16,361**, equal to the UI "Exports" figure 16,365. So "Exports" in the UI is simply the part of the pool that is not dial/lead/export in the API view (plain unlocks and org enrich/search are in it); it is not necessarily human bulk exports. The same difference at 3 Oct 22:34 is 93,354 - 57,612 = **35,742**: Exports grew by ~19,380 in the window, while this repo's non-phone spend before that reading is ~4,800 and companyOps' is 2,894.

### 8.2 3 Oct org-search discovery (`mixed_companies/search` is not free)
Timeline (IST, 3 Oct): 18:54 smoke tests (2 and 3 partitions); **18:55:09 full run #1** (pid 67391) killed 18:57:30 after the headcount-field bug, DB deleted; **18:59:04 full run #2** (pid 68407): 2,940 partitions = 28 cities x 7 size ranges x 15 keywords, <= 5 pages of 100, 1,542 s, 139,720 org rows, 61,799 distinct, 6,207 deduped against HubSpot/local, band A (51-500 and domain) 7,824. 19:20 the user asked "be double sure Apollo is 0 credits"; the agent checked the code and the `x-24-hour-usage` header (4,217 requests) and answered $0. 22:33 the user pushed again; the first real measurement (22:34:23) showed 10 calls = 6 credits.
How many credits: the 24 h request counter was 4,217 at 19:56 and 4,231 at 22:35, and covers runs #1 and #2, the smoke tests and a few probes. Run #1 ran ~140 s at the run-#2 rate (~2.4 calls/s) = ~340 calls, run #2 therefore ~3,860 calls. Credits = calls that returned orgs; empty partitions (count E unknown, E <= 1,757 because at least 1,183 partitions yielded new records) are free. Central 3,200 (run #1 ~320, run #2 ~2,860, tests ~20), range 2,400 to 4,200. The agent's own estimate was ~3,000 (2,800-3,400) Output: `itsvc.db` as above. Because the first measurement came after the run, no exact number exists; ask Apollo's usage page for 3 Oct (UTC) to settle it: expected org-search part ≈ 3,200 of the day's ≈ 6,300 from this repo.
The COBOL discovery on 1 Oct is the same endpoint: 16 + 32 calls in `source_attempt`, 46 credits (2 empty calls free), while the script printed "0 credits spent".

### 8.3 The 250-POC reveal
`itsvc/reveal_pocs.py`, user-authorised at 23:17 IST ("looks good do the ceo such person reveal and +91 callable 250"). Smoke test 3 targets: 36 credits (23:17-23:19). Full run started 23:19:29 IST with cap 3,500, pool 2,034 companies: 249 delivered contacts, cr=2,871 at 249/250 (last log 00:28:22 IST), 12 credits per success in the sample and 11.5 average; ~9 per success when the first candidate works, 18-36 when candidates fail. After 249 the process spent a further ~137 credits in ~7.7 minutes on the 250th (no log line on misses), until killed 00:36:01 IST. Balance arithmetic: 93,383 (22:55:43) -> 96,428 (00:35:57) = 3,045 = 36 + 2,871 + 137 + 1 (an org-search call). The agent's closing line "96,000+ remaining" should read 96,000+ consumed. About 2,700 of the 3,044 are phone-reveal category credits (8 per reveal), the rest unlocks.

### 8.4 Where the remaining 23k could be (cross-repo view, not this repo's evidence)
Using Apollo's own counters at 29 Sep 10:34:50 IST (RAT reading) and 3 Oct 22:34:23 IST (mine), pool consumed +39,553: dial counter +20,152 (35,524 -> 55,676, at 8 each = 2,519 reveals) and non-dial +19,401. Explained by repos: dial = hubspot ~5,220 + companyOps 3,360 = 8,580, leaving ~11,570 unexplained (~1,450 reveals); non-dial = hubspot ~4,800 + companyOps 2,894 + RAT ~1 = 7,700, leaving ~11,700 unexplained. In other words the unknown consumer does both big reveal batches and big unlock/enrich/search work, between 29 Sep and 3 Oct 22:34 IST. The best check is the Apollo usage screen by Team member / API key / Action and by day; the API key behind `.env` `apollo_api_key` is shared by the three repos, so key-level usage would separate API from UI.

## 9. Gaps and the biggest unexplained or wasted items

Biggest unexplained: (a) ~23,260 credits (55%) not attributable to hubspot, companyOps (6,286) or RAT (~1), concentrated before 3 Oct 22:34 IST, about half phone-reveal and half unlock/export category (8.4). (b) Inside this repo, the org-search count (+/- 900). (c) COBOL batch6 sequential launches and batch8 (+/- 150 each).

Spend with no persisted output or lost log (credits): CEO dry-run 35 + live run #1 220; batch3 first launch 50; batch8 workers 2-6 180 (no rows written); itsvc run #1 320 + smoke 8 (database deleted); Anthropic dry-run duplicate 44; fix_or_remove (crashed) 10; POC misses 137; probes ~70; repeated re-processing in batch6 restarts >= 60. Total ~1,100.

Spend with no local evidence of purpose: none inside this repo. Every row has a chat-stated purpose and a call site. The only purposeless credits are the ~23k above, which have no local call site at all.

Other gaps:
- The agent's own in-chat balance figures (371, 315, 310 on 29 Sep) are stale; the user's balance chain and the top-up time (12:37-12:39 IST) come from the companyOps audit.
- `/tmp` logs (volatile) hold the COBOL/UK/CEO counters; they are quoted here with file mtimes. The batch6 sequential log was overwritten four times; launches P1-P3 are reconstructed from monitor events in the chat (cr counters at 08:47-08:58Z).
- Script counters were verified against Apollo only on 3 Oct. Whether failed reveals are billed is supported only by the 29 Sep probe (billed while pending).
- Apollo may attribute usage by its own clock; the Apollo UI day boundary (IST, UTC or US) is unknown; both cuts are given in section 4.
- `reveal_personal_emails: true` is sent with every reveal; if Apollo bills an extra email credit it is outside the 1/8 model (the UI showed an "Email" bucket of 786 before the window).

## 10. Permission-classifier denials and skipped items
- 29 Sep 10:31:37Z and 10:34:24Z (16:01 and 16:04 IST): `anthropic_poc.py` live run with mobile/personal-email reveal, denied by the auto-mode classifier both times; the agent stopped. A read-back of the sheet at 10:38Z was also denied. No credits.
- No tool call of this audit was denied.

## 11. Confidence summary
High: telehealth 98, Anthropic 88 + 5, UK batch 2 990, CEO run #2 350, fix rounds 225 + 111, NVIDIA 342, Amazon 36, OpenAI 34, xAI 50, COBOL runs with a final counter (top50, batch2, batch3, batch4, batch5, 4 workers, batch7 w3, batch9 w1/w2/w3b, web feeder), POC test + run (balance-reconciled), 3 Oct billing tests, COBOL discovery 46 (call log). Medium: org-search size checks, batch6 P4, 29 Sep probes. Low: CEO dry-run/run #1, batch3 first launch, batch6 P1-P3, batch8, batch9 w3 first run, fix_missing_contacts, fix_or_remove, itsvc run #1 and run #2 (the single biggest uncertainty, central 3,200).

## 12. Files
- `docs/audit/apollo/hubspot.md` (this file)
- `docs/audit/apollo/hubspot_ledger.csv`
- `docs/audit/apollo/hubspot_balance_readings.csv`
Sibling deliverables read for the cross-check: `companyops.md`, `rat.md` in the same folder.
