# Apollo credit audit: companyOps repo, 20 Sep to 28 Sep 2026 IST (pre-top-up part of the cycle)

Scope: every Apollo.io credit the `companyOps` repo (`legacy/companyOps`, live path `/Users/bhanu/Desktop/companyOps`) can be shown to have spent between 2026-09-20 00:00 IST and 2026-09-28 23:59 IST, what each spend was for, and who or what could have spent the credits that cannot be traced. Read-only forensic work. No network calls. Nothing under `legacy/` or `db/` modified. No secret values printed or written.

Files from this audit (all in `docs/audit/apollo/pre/`):

| File | Content |
|---|---|
| `companyops.md` | this report |
| `companyops_ledger.csv` | 148 spend rows (required columns plus `credits_export`, `credits_phone`, `credits_low`, `credits_high`). One row per run / call batch; the 1,994 credits of the repo's own `cost_ledger` appear as 20 aggregated `evidence_type=ledger` rows. |
| `companyops_balance_readings.csv` | 56 rows: every Apollo-side balance reading found anywhere (own repo none; sibling repos' readings are included as cross-repo context), the repo's own running totals, and non-Apollo "numbers" that were mistaken for balances |

## 1. Headline

| | Credits |
|---|---:|
| Traced to companyOps, [MEASURED] (printed counts, raw cache files, repo `cost_ledger`) | **4,043** |
| Traced to companyOps, [ESTIMATED] (inferred call counts, assumed phone delivery) | **3,930** |
| **Total attributable to companyOps, 20 to 28 Sep** | **~7,970** (plausible band ~6,800 to ~9,100) |
| Apollo usage chart, sum of the 20-28 Sep UTC-day bars | ~54.2k (matches the pool figure 53,811 of 54,280 used at 29 Sep 10:37 IST) |
| **Not explained by companyOps** | **~46.2k (about 85%)** |

The repo explains about 15% of the cycle's burn before the 29 Sep top-up. The biggest single lesson: the repo's own `cost_ledger` only starts at **28 Sep 17:43:42 IST** (it did not exist before); everything earlier had to be rebuilt from the chat, raw cache files and run logs.

## 2. Method, time zones, traps

1. **Sources used.** `legacy/companyOps/tam/data/tam.sqlite3` (opened `mode=ro&immutable=1`; also the `.bak-20260929-140234` copy, identical for the 684 pre-29-Sep rows) tables `cost_ledger`, `stage_runs`, `crm_pushes`, `signalhire_results`, `signals`, `async_jobs`; `tam/logs/run_2026-09-28.jsonl`; file mtimes and raw Apollo cache folders (`apollo_test/raw`, `apollo_proptech/raw`, `adtech_discovery/raw_apollo`, `construction_discovery/raw_apollo`, `tam/cache/apollo_orgs`); the raw Claude session `chat_context/raw/companyOps/7bb0eacd-...jsonl` (5,058 events on 24-25 Sep alone) and its 360 subagent transcripts; the git bundle; `db/leadgen.sqlite` (`deal`, `contact`, `cost_ledger`) as the downstream footprint. Four parallel readers each combed one slice of the raw chat (22-23 Sep, 24-25 Sep, 28 Sep to 17:40, 28 Sep 17:30 to 29 Sep 10:50); I did 21 Sep, the repo, DB, git and cross-session analysis myself.
2. **Time zones.** `cost_ledger.at` is UTC. The Apollo chart buckets by UTC day, which is IST 05:30 to 05:30 next day. Every traced spend (08:44 to 22:10 IST) falls inside the same-numbered UTC day, so **no row moves across a UTC boundary**; the session was never active between 00:00 and 08:30 IST. The `date_utc` column proves it row by row.
3. **No double counting.** A printed script total, the chat's retelling of it and a raw-file count of the same run are one spend. Ledger rows (17:43 IST on 28 Sep onward) and pre-ledger chat reconstruction (before 13:30 IST) never overlap. The 20 probe-class HTTP 400 calls (uncharged) and free `mixed_people/api_search` / `webhook_result` / `usage_stats` calls are listed as 0-credit or omitted.
4. **MEASURED vs ESTIMATED.** MEASURED = call count or credits printed by the script, a ledger row, or an Apollo response (`credits_consumed`). ESTIMATED = inferred (call count from loop bounds / logs that no longer exist, or phone delivery assumed). The credit *rates* themselves (search 1 per page, bulk_enrich 1 per org, people/match 1, phone reveal +8) are the repo's and Apollo docs' claims; only the 8 is confirmed by an Apollo body (`credits_consumed: 8`, 21 Sep 14:47 IST, line 7850).
5. **Cost model in the code.** `people/match` 1 credit; with `reveal_phone_number` + placeholder `webhook_url` +8 (result polled free at `webhook_result/{top-level request_id}`); `organizations/bulk_enrich` 1 per org (batches of 10); `mixed_companies/search` 1 per page (the user believed it was free, the session first priced it as free on 24 Sep and then as 1 credit from the docs; never verified against a balance); `mixed_people/api_search` free (obfuscated previews). The older discovery scripts (`apollo_test`, `apollo_proptech`, `adtech_discovery`, `construction_discovery`, `manufacturing_discovery`, `mobility_discovery`, `opsdata/phone_enrich_apollo_signalhire.py`) never call `costs.charge`; only the `tam/` package does (introduced 28 Sep 16:35 to 17:19 IST).
6. **No balance was ever read by this repo.** The session never called an Apollo balance/usage endpoint with a readable result (the free `usage_stats/api_usage_stats` returned an unprinted 70-key payload at 28 Sep 16:40; on 29 Sep it returned 404). All pool readings below come from sibling sessions on the same laptop.

## 3. Per UTC day: traced vs the Apollo chart

Target = chart reading (+-0.3k). Exports = tan (export/enrichment), phones = teal (mobile reveals). "Other" on 22 Sep is the two small extra credit types.

| UTC day | Traced M | Traced E | Traced total (exports / phones) | Apollo target (exports / phones) | Gap | Traced share |
|---|---:|---:|---|---|---|---:|
| 21 Sep | 312 | 2,025 | 2,337 (609 / 1,728) | 12.5k (1.9k / 10.6k) | 10.2k (1.3k / 8.9k) | 19% |
| 22 Sep | 0 | 104 | 104 (96 / 8) | 6.8k (1.2k / 3.7k / other 1.1k + 0.8k) | 6.7k (1.1k / 3.7k / 1.9k other) | 2% |
| 23 Sep | 405 | 0 | 405 (405 / 0) | 5.6k (1.6k / 4.0k) | 5.2k (1.2k / 4.0k) | 7% |
| 24 Sep | 722 | 712 | 1,434 (1,314 / 120) | 12.4k (5.0k / 7.4k) | 11.0k (3.7k / 7.3k) | 12% |
| 25 Sep | 5 | 69 | 74 (74 / 0) | 3.0k (1.5k / 1.5k) | 2.9k (1.4k / 1.5k) | 2% |
| 26 Sep | 0 | 0 | 0 | none | 0 | n/a |
| 27 Sep | 0 | 0 | 0 | 4.6k (1.9k / 2.7k) | 4.6k (1.9k / 2.7k) | 0% |
| 28 Sep | 2,599 | 1,020 | 3,619 (2,211 / 1,408) | 9.3k (3.8k / 5.5k) | 5.7k (1.6k / 4.1k) | 39% |
| **Sum** | **4,043** | **3,930** | **7,973** | **54.2k** | **46.2k** | **15%** |

Plausible bands (traced): 21 Sep 2.3k to 3.1k; 22 Sep 9 to 116; 23 Sep 305 to 405 (305 if `mixed_companies/search` is not billed); 24 Sep 365 to ~1.6k (365 if search is not billed); 25 Sep ~74; 28 Sep ~3.0k to ~4.2k (ledger 1,994 may over-count by up to ~250; pre-ledger part 1,260 to 2,250).

Confidence: high on call counts for 23 Sep, 24 Sep search/enrich and the whole 28 Sep ledger; medium on 21 Sep phone delivery and 24 Sep batch-1 phone count; low on the 28 Sep 13:14 to 13:29 manufacturing runs (log lost) and on whether Apollo re-bills repeated matches/reveals of the same person.

## 4. Day by day (IST, with UTC re-bucketing)

Session activity is from the raw jsonl event timestamps. No events exist on 19, 20, 26, 27 Sep; the file mtime scan finds no companyOps file modified on 19 or 20 Sep.

### Sat 20 Sep (IST) = part of UTC 19/20 Sep
Nothing. Last session event before the window: 18 Sep 21:09 IST; next: 21 Sep 10:49 IST. Cycle renewed ~20 Sep (Apollo side). Traced: 0.

### Mon 21 Sep = UTC 21 Sep (chart 12.5k: 1.9k exports, 10.6k phones)
Session active 10:49 to 16:55 IST. Apollo work 14:25 to 15:36 IST (08:55 to 10:06 UTC), all in this UTC bucket.
* 14:23 user: enrich the 303 fintech companies (HubSpot/Tanisha list) "use apollo and signalhire".
* 14:25:17 to 14:36 pass 1: 303 `people/match` (`reveal_personal_emails`), 303/303 matched. [MEASURED count] 303 export credits.
* 14:36 to 14:46 debugging the phone-reveal blocker: three test reveals on one person (two with a broken poll, one confirmed `credits_consumed: 8`). 24 phone + 3 export credits; the confirmed one is MEASURED, the other two ESTIMATED.
* 14:49:39 to 15:36:24 full run (user pasted a "working methodology" at 14:46): every one of 303 people went through Apollo `people/match` **with phone reveal first**, SignalHire as fallback. Result `DONE total=303 apollo_hits=213 sh_hits=56 no_hit=34 +91_callable=250`. Export 303; phone 213 x 8 = 1,704 central (up to 303 x 8 = 2,424 if undelivered reveals are billed too). [ESTIMATED]
* 15:37 to 15:38: 250 deals pushed to Tanisha (HubSpot only). 16:47 to 16:50 a HubSpot association bug was fixed (no Apollo).
* Code committed 15:09 IST (`bb4d1b5`, `opsdata/phone_enrich_apollo_signalhire.py`) and pushed to GitHub; this is the only commit touching Apollo in the bundle.
* Traced **2,337** (range 2.3k to 3.1k): exports 609, phones 1,728. Gap **~10.2k: exports ~1.3k, phones ~8.9k**. Not a time-of-day match to anything: nothing from this repo ran in the other 23 hours of this bucket (05:30 to 14:25 IST, 15:36 IST to 05:30 IST next day).
* Downstream footprint: 464 contacts with phones were created that UTC day in the companyOps HubSpot portal; 250 are this repo's push (15:37 to 15:38). The other **214 contacts / 243 deals (Amisha 100 at 16:01 to 17:52, Vaishnavi "Proptech" 100 at 16:14 to 18:45, Vaishnavi 43 at 18:45 IST) were created after the last Apollo activity of this session, while no Claude session known to the audit was creating pushes** (the sibling hubspot session ended 14:53 IST that day, this session only did HubSpot repairs after 15:37). Origin unknown; at 9 credits each that is up to ~1.9k.

### Tue 22 Sep = UTC 22 Sep (chart 6.8k: 1.2k exports, 3.7k phones, plus ~1.1k and ~0.8k in two other credit types)
Session windows 11:06 to 11:58, 14:09 to 14:53, 15:05 to 15:24, 16:48 to 16:55, 17:34 to 19:16 IST.
* 11:36:17 to 11:38:12: "emails also must exist" for the 105 live Tanisha deals: 105 `people/match` (email only, `reveal_personal_emails`, SignalHire fallback). Log `apollo=94 sh=10 none=1`. These are the same people already run on 21 Sep, so Apollo may not re-bill. [ESTIMATED] 94 central (0 to 105).
* 11:38:33 one debug match (0 to 1).
* 15:24:43 user pastes a LinkedIn URL "get me his contact": `enrich_mobile` + second match. 9 to 10 credits.
* 17:34 to 19:16 medtech scraper (`pipeline/`), no Apollo.
* Traced **104** (9 to 116). Gap **~6.7k**, of which ~1.1k exports, ~3.7k phones and **~1.9k in the two other credit types**. Those two figures (~1.1k and ~0.8k) match the "Waterfall 1,175" and "Email 786" buckets that Apollo's UI showed for the whole cycle on 29 Sep, i.e. they were consumed entirely on this one UTC day. **No code in any of the three repos sharing the key uses Apollo's waterfall or email-finder parameters** (grep of every repo under `legacy/`; the `waterfall` hits are SignalHire's). That points to Apollo's web UI / a sequence / an integration used by a human, not to this repo.

### Wed 23 Sep = UTC 23 Sep (chart 5.6k: 1.6k exports, 4.0k phones)
Session windows 11:59, 13:25 to 13:44, 14:26 to 14:59, 16:40 to 18:45, 19:27 to 19:53 IST.
* 13:41 user pastes the "Apollo API account-list test, Fintech ICP" prompt ("dont overspend the apollo credits").
* 13:43:54 smoke: 1 `mixed_companies/search` (1 credit). 14:27:46 `bulk_enrich` smoke on 10 cached domains (10; raw file `unique_enriched_records=10`). 14:29:07 to 14:30:09 full run: 3 search pages + 30 `bulk_enrich` calls delivering **295 orgs**; script prints "search: 3 enrich: 295 total: 298". It overshot the 200 target (290 verified, CSV holds 200; ~90 credits of overshoot). 14:35 to 14:37 `richness_map.py`: 96 searches (`per_page=1`), 84 with hits, 12 empty.
* Assistant's own tally at 14:37:53: "Total credits spent this session: 405" = 1 + 10 + 298 + 96. Purpose: Fintech account list for Tanisha (196 India fintech after HubSpot dedupe; emailed to sreenandan.m@lh2.ai 14:56).
* After 14:59 only HubSpot reporting/Gmail, no Apollo.
* Traced **405** exports (305 to 405), phones 0. Gap **~5.2k: ~1.2k exports, ~4.0k phones**. This repo did no phone reveal on 23 Sep at all.
* Downstream footprint: contacts with phones created on 23 Sep UTC: companyops portal 420, main portal 207, RAT portal 223. None of the 420 companyops-portal contacts came from a push in this session (no HubSpot push in the chat on 23 Sep). The sibling hubspot session wrote `global_coops_enrich_push.py` (11:56 IST), `uk_campaign_enrich_push.py` (16:34) and `aus_campaign_enrich_push.py` (19:01) that day and tested a phone reveal at 18:23 (`credits_consumed: 8`).

### Thu 24 Sep = UTC 24 Sep (chart 12.4k: 5.0k exports, 7.4k phones)
Session windows 10:30 to 11:27, 12:06 to 13:49, 14:05 to 15:49, 16:03 to 16:48, 17:16 to 17:50, 18:00 to 19:23, 20:18 IST. User said "pause everything" at 19:11 (pkill of the last Apollo caller at 19:11:43; confirmed nothing running).
* 12:08 to 12:18 proptech test: 1 smoke + 200 pass-1 + 18 verify (killed) + 59 collect = 278 searches. 12:51 to 12:56 and 14:25 adtech: 1 + 33 + 10 searches. 13:08 a LinkedIn lookup (phone reveal, 10). 16:32 to 16:41 mobility `resolve_domains.py`: 209 searches. 18:02 to 18:07 construction: 1 + 28 + 9 searches + `bulk_enrich` of 79 domains. 18:59 to 19:11 mobility resolve on all 987 (killed at checkpoint ~500). Search total ~1,069 credits if billed.
* 14:42 to 15:30 adtech enrich+push for Vaishnavi: batch 1 (21 people) ran **Apollo-first phone reveal** (14:48:27 to 14:53:02; ~13 Apollo phones assumed, 146 credits ESTIMATED), later batches SignalHire-first (user rule 14:50:21). Headcount verification `bulk_enrich` 41 + 3 + 6 + 1 + 2 + 79 + 15 orgs, backfill rounds (8 + 24 credits).
* Traced **1,434** (1,314 exports, 120 phones); 365 if search is free; at most ~1.6k. Gap **~11.0k: ~3.7k exports, ~7.3k phones**. The repo did at most ~41 Apollo reveal attempts all day (max 328 phone credits).
* Pool readings (sibling hubspot session): **27,524 at 14:52:58 and 27,330 at ~14:59 IST** (a 194-credit CAD run by that session). The same session started the `cad_founder_pipeline.py` at 14:52:58, ran `verify_websites_gapfill.py` (pid 73775, project `/Users/bhanu/Desktop/hubspot`) from 18:23 to 19:22 and left it running when this session killed its own processes, and had the "spend it all" `apollo_reveal_runner.py` (hubspot repo) loaded on screen at 14:51.
* Seven adtech deals were created at 12:41 IST by something else, before this session pushed anything, with phones and emails on the contacts.
* Downstream footprint: contacts with phones created on 24 Sep UTC: companyops 197, main 488, RAT 119 (804). Even if every one needed an 8-credit Apollo reveal that is 6.4k, in the right order of magnitude for the 7.4k phone bar, and only ~13 of them are this repo's.

### Fri 25 Sep = UTC 25 Sep (chart 3.0k: 1.5k exports, 1.5k phones)
Session windows 10:45 to 10:46, 11:38 to 13:23, 14:20 to 14:47, 15:12 to 15:20, 15:57 to 16:05, 19:56 to 20:01 IST.
* 11:39 user "yes resume" adtech discovery. Only headcount `bulk_enrich` (~56 orgs, several duplicated) and `enrich_and_push` batches 6 to 14 (11:55 to 13:08): ~13 `people/match` and ~5 reveal attempts that returned nothing. No searches. The 76 mobility deals pushed at 12:11 (Mobility_ICP_Final.csv from the sibling hubspot session) used HubSpot API only.
* Traced **74** (all but ~5 ESTIMATED). Gap **~2.9k: 1.4k exports, 1.5k phones**. The sibling hubspot session ran this repo's `mobility_discovery/resolve_domains.py` at 11:34:38 to 11:47:54 (987/987, "487 credits spent") and several CAD scripts (`cad_100` 12:24, `track2_apollo_full.py` 13:24).

### Sat 26 Sep = UTC 26 Sep (chart: none)
No session events, no Apollo activity, no ledger. Consistent with the chart.

### Sun 27 Sep = UTC 27 Sep (chart 4.6k: 1.9k exports, 2.7k phones)
**No companyOps session events and no scheduled job; this repo spent 0.** The sibling hubspot session was active 20:03 to 21:29 IST on 27 Sep (Singapore vertical richness map 20:17, discovery 21:11, pipeline 21:13; reported "Apollo at 11,104 credits remaining" at 21:27:00). From 21:27 IST on 27 Sep to 09:10 IST on 28 Sep the pool moved by only 3 credits (11,104 to 11,101), so the 4.6k bar must have been spent before 21:27 IST on 27 Sep (chart UTC day = IST 05:30 Sun to 05:30 Mon), i.e. in daytime hours when no Claude session in any of the three repos is recorded as active. The Sunday daytime burn is the least attributable block of the whole cycle (human UI use or an unlogged process on the same key are the only candidates).

### Mon 28 Sep = UTC 28 Sep (chart 9.3k: 3.8k exports, 5.5k phones)
Session windows 08:31 to 13:44 IST (gap 10:16 to 10:39) and 16:32 to 22:32 IST; idle 13:44 to 16:32 and 22:32 onwards (until 29 Sep 10:33). No background Apollo process ran while the user was away. Pool-start reading: 11,101 at 09:10:25.
* **No ledger before 17:43:42 IST.** Pre-ledger spend (reconstructed, ~1,620; 1,260 to 2,250): 08:44:43 `manufacturing_discovery/richness_map.py --run` (20 searches, script says 20 credits; only 2 returned hits); 19 standalone `bulk_enrich` headcount checks 10:45 to 13:09 (~301 orgs); 17 small `enrich_and_push` runs 10:49 to 13:12 (90 companies, 81 persons, 53 SignalHire phones, 8 Apollo phones); 13:12:53 `bulk_enrich` of 452 relaxed-ICP domains (46 calls, ~417 orgs); 13:14:16 to 13:25:05 the 113-company run (73 pushed, 40 skipped; ~101 matches, ~38 reveal attempts, log lost); 13:25:06 to 13:28:59 a re-run of 69 skipped companies (7 recovered, none pushed). Of the ~1,620: ~1,280 export-type, ~340 phone-type. The 134 Manufacturing deals pushed (10:50 to 13:24) reconcile with these runs.
* **Ledger (17:43:42 to 22:09:50 IST, 1,994 credits, all MEASURED):** fintech org search 2 + 86 pages (17:43 to 17:49), edtech 101 and healthcare 86 pages (18:35 to 18:37) = 275; manual probe 20:00:29 (1 match + 8 reveal); outreach runs R1 to R5 (`leadgen outreach`, fintech + edtech, 20:03 to 22:09): `org_enrich` 425 credits (46 calls), `people_match` 230, `phone_reveal` 1,064 (133 reveals). 91 leads delivered; ~18.9 credits per delivered lead. Run stops: self-imposed cap (2,003 estimated against 2,000) at 22:09:57.
* Un-ledgered: 20 `people/match` returned HTTP 400 `WEBHOOK_URL_REQUIRED` (uncharged) at 19:53 to 19:55; one direct match at 19:55:49 (1 credit). Free calls (`api_search` ~210, `webhook_result` polls up to ~530, `usage_stats`) cost nothing. Possible over-count in the ledger up to ~250 (98 repeat matches for the same person, 19 repeat reveals, 3 empty pages).
* "1,725 credits left" (user, 18:53) was **not an Apollo balance**: the assistant had invented a 2,000/month cap at 18:40:46 and the check printed 2,000 minus the 275 org-search credits; corrected at 18:53:33. Real spend was already ~1,900 by 18:53 counting the pre-ledger manufacturing runs, which no one had added up.
* Traced **3,619**; gap **~5.7k: ~1.6k exports, ~4.1k phones** (at least 3.5k phone-type credits are not from this repo).
* **Pool reconciliation 09:10 to 13:10 IST: 11,101 to 7,444 = 3,657 consumed in four hours.** Of that, the RapidActionTeam audit's own before/after files account for 941 inside its enrichment waves; companyOps traced credits in the same window are about 580 (360 of them in the gaps between RAT waves). That leaves **~2,100 to 2,600 credits (about 60 to 70%) not attributable to either repo.** Examples: 12:38:36 to 12:43:33 the pool fell by 375 in five minutes (about 75 per minute) with no companyOps run and no RAT audited wave; 11:46:53 to 11:52:45: 184, nothing from companyOps. From 13:10 IST to 29 Sep 10:34 IST the pool fell a further 6,965 (7,444 to 479); companyOps accounts for ~1,100 (13:10 to 13:29) + 1,994 (ledger) = ~3.1k, leaving ~3.9k, of which the sibling hubspot session reports ~1,033 (UK proptech run 22:10 to 22:23 IST, 61 SignalHire + 37 Apollo reveals).

## 5. Who or what could have spent the untraced ~46k

Ordered by strength of evidence.

1. **Sibling Claude sessions sharing the same Apollo key (strong, but not quantified here).** The same Apollo key (identical SHA-256 prefix `594b5c6e08`, 22 characters) sits in `.env` of **companyOps, hubspot and RapidActionTeam** on the same laptop, so all three draw one pool. The raw chats show: the hubspot session ran Apollo scripts on 21, 23, 24, 25, **27** and 28 Sep (6, 11, 40, 31, 7, 18 raw lines mentioning `api.apollo.io`/`webhook_result`), including `global_coops`, `uk_campaign`, `aus_campaign` enrich-push scripts (23 Sep), `cad_founder_pipeline` (24 Sep 14:52), `apollo_reveal_runner.py` ("a deliberate one-shot spend it all run"), a foreign `verify_websites_gapfill.py` left running 24 Sep evening, `track2_apollo_full.py` (25 Sep), the Singapore pipeline (27 Sep, the **only** Apollo-calling activity found anywhere on a day this repo was silent) and a UK proptech run costing ~1,033 on 28 Sep 22:10. RapidActionTeam ran `enrich_cad_founders` waves on 28 Sep (941 credits inside audited waves 11:44 to 13:10) and Apollo lines on 25, 28 Sep. Downstream, contacts-with-phone created per UTC day across the three HubSpot portals (companyops + main + RAT) are 464 / 237 / 850 / 804 / 272 / 0 / 1,036 for 21-25, 27, 28 Sep: enough to explain the phone bars on 23, 24, 25, 28 Sep if most phones were Apollo-sourced, but **not enough on 21 Sep (phone bar 10.6k vs at most 3.7k), 22 Sep (3.7k vs 1.9k) and 27 Sep (2.7k vs zero contacts)**.
2. **Humans in Apollo's web app or another integration (strong for 21, 22, 27 Sep).** Apollo's own usage screen split the cycle into Phone numbers 35,464, Exports 16,365, Waterfall 1,175, Email 786; the scripts of all repos explain only a small part of the 16,365 exports (the 29 Sep chat found ~1,433 script-attributable, ~9%). The 1.9k "other-type" credits on 22 Sep equal the cycle's whole Waterfall and Email buckets and no repo uses those API parameters. The Sunday 27 Sep bar and the 21 Sep 16:01 to 18:45 IST HubSpot creations (243 deals, 214 contacts with phones) occurred with no Claude session running. Other HubSpot owners (Akarsh, Lamiya, Prerna, Harsha CAD, Shagufta) were creating 100 to 350 deals a day in other pipelines on 22 to 28 Sep, so other people were actively sourcing leads.
3. **This repo's own scripts: ruled out for most of it.** Credit **type**: this repo reveals phones only as an expensive fallback after SignalHire (the 28 Sep ledger shows 133 reveals; the 24 and 25 Sep sessions at most ~41 and ~5 attempts), except 21 Sep when it ran phone-first. The chart's phone bars of 7.4k (24 Sep) and 5.5k (28 Sep) would need 925 and 690 reveals. **Cadence**: the repo's phone reveals run at one per 8 to 10 seconds (1 per poll cycle); the untraced pool drops on 28 Sep (75/min) are faster than any single repo script. **Time of day**: the heaviest untraced blocks sit when this session was idle (21 Sep after 15:36; 27 Sep; 28 Sep 13:44 to 16:32 and 22:32 to 29 Sep 10:33; 25 Sep after 13:08).
4. **Automation / other authors: none found.** Git bundle authors: Bhanu (various MacBook hostnames), corpDev (`corpdev@lh2holdings.com`), ChPuru (Purunjay), Nandan. **Only Bhanu committed Apollo code** (`bb4d1b5`, 21 Sep 15:09 IST, `opsdata/phone_enrich_apollo_signalhire.py`); every other Apollo script (apollo_test, apollo_proptech, adtech/construction/manufacturing/mobility discovery, tam/) is untracked and exists only on the laptop. `.github/workflows` has two workflows (`outflo-sync.yml` cron 05:03/17/33/49 UTC, `deploy_ops_dashboard.yml` cron `9,39 4-13` etc.); neither references Apollo and their secrets are HubSpot and OutFlo only. No cron, launchd, n8n or ScheduleWakeup job calls Apollo (5 ScheduleWakeup calls on 24 Sep 14:32 to 15:23 were reason strings only). The 360 subagent transcripts contain no `api.apollo.io` call; the "Another Claude session sent a message" lines are this session's own classifier subagents. A colleague running the committed `phone_enrich_apollo_signalhire.py` with the shared key cannot be excluded (the user said on 21 Sep "someone will take a pull, tell which file to use for enrichment") and would leave no trace in the chat; the file needs only `apollo_api_key` in `.env`, which is also on the other repos' disks.

## 6. Biggest unexplained blocks (by size)

| Block | Credits | Evidence on who/what |
|---|---:|---|
| 21 Sep UTC phones | ~8.9k | Not enough HubSpot contacts in any portal; this repo did 1.7k-2.4k phone-first. Strong hint: Apollo UI/other lists not pushed to HubSpot. |
| 24 Sep UTC (afternoon to 05:30 IST 25 Sep) | ~11.0k | Chart-minus-pool arithmetic (27,524 left at 14:52 IST vs 24.9k charted for 21-23 Sep) implies ~10.5k burned between 14:52 IST and midnight UTC; hubspot session (CAD pipeline, gap-fill, foreign pid 73775 running to 19:22), 804 contacts with phones created across portals. |
| 28 Sep UTC | ~5.7k | RAT audited waves (941+), hubspot UK run (~1,033), plus ~2.1-2.6k that neither repo's logs explain between 09:10 and 13:10. |
| 23 Sep UTC | ~5.2k | 420 companyops-portal + 207 main + 223 RAT contacts with phones; hubspot session scripts; none from this repo. |
| 27 Sep UTC | ~4.6k | Sunday, no session; hubspot Singapore pipeline 20:03 to 21:29 IST is the only Apollo caller found, balance 11,104 at 21:27 IST. |
| 22 Sep UTC | ~6.7k (incl. 1.9k other types) | Other credit types (waterfall, email) unused by any code. |
| 25 Sep UTC | ~2.9k | Hubspot session `resolve_domains.py` (487 credits) and CAD scripts. |

## 7. Credit-spending events with no local evidence of purpose or origin

* Everything in section 6 other than the repo's own rows (no purpose can be tied to any file, log or chat line).
* The 243 deals / 214 contacts created on 21 Sep 16:01 to 18:45 IST in the companyops portal (Amisha 100, Vaishnavi Proptech 100 + 43) and the 7 adtech deals at 24 Sep 12:41 IST: contacts carry phones; origin script or person unknown.
* The three discovery runs whose results were thrown away or repeated: 23 Sep full run overshoot (~90 credits beyond the 200 target); 28 Sep 13:25 re-run of 69 skipped companies (7 recovered, none pushed; ~190 credits, duplicated people-search/match/reveal for people already tried); 28 Sep R1/R3 aborted runs (kills in flight); the 105-lead email pass on 22 Sep (people already enriched on 21 Sep).
* Credits possibly billed but invisible: Apollo reveals that were still pending when the 16-second poll gave up (the Aiplex case on 24 Sep returned no number at 15:29 and a number at 19:22), HTTP retries by `leadgen/http.py` (tenacity re-sends POSTs silently), and any billing of `mixed_companies/search` pages (about 1,170 calls across 23 to 24 Sep and 28 Sep morning, if billed).

## 8. Gaps and caveats

* The 21 Sep phone count depends on whether Apollo bills undelivered reveals (213 vs 303 requests).
* The scratchpad outputs (`apollo_pass1.json`, `phone_enrich_final.json`, the 22 Sep email JSONs, 28 Sep `/tmp/manufacturing_broad_*.log`) are deleted; the per-company results of the 13:14 and 13:25 manufacturing runs cannot be recovered.
* No Apollo balance exists in the companyOps chat; every pool reading is from a sibling repo. Chart reading error is +-0.3k per day, ~+-0.8k over the cycle; the pool readings imply ~1.7k less cumulative use than the chart sum at 27 Sep (consistent only if the ~1.9k of other-type credits on 22 Sep do not count against the lead-credit pool).
* The chart's UI type names ("exports" = enrichment/reveal-email, "phone numbers" = mobile) are taken from the user's labels; the 8-credit phone rate and 1-credit match rate are confirmed only for this account's API reveals.
* No permission-classifier denials were hit by this audit; two InfluGlue `bulk_enrich` calls in the original chat (25 Sep 12:27 and 12:44) were blocked by the auto-mode classifier and never ran.
* `tam/logs/run_2026-09-28.jsonl` timestamps are IST; `cost_ledger.at` is UTC (the two 3 Oct probe rows are local IST but are outside this window).
