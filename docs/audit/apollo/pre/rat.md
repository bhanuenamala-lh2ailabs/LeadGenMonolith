# Apollo credit forensics - RapidActionTeam (RAT), pre-top-up window 20 Sep 00:00 IST - 28 Sep 23:59 IST 2026

Scope: every Apollo.io credit that the RAT repo (CAD/BIM/AEC + COBOL lead pipeline), or work done for the RAT team, consumed before the 29 Sep top-up, what each credit was spent on, and who or what could have spent the credits that cannot be traced.
Companion files (same folder): `rat_ledger.csv` (61 rows), `rat_balance_readings.csv` (every Apollo balance reading found, plus the user's usage-chart day totals). The 29 Sep-4 Oct audit is `../rat.md`; this file extends it and corrects it in four places (section 8).
Method: static read of the repo copy (code, `audit/`, `leads/`, logs, `.env*` timestamps only, no values), the full raw RAT Claude session (3,551 events, 25 Sep 11:30Z - 1 Oct 14:05Z), the git bundle (cloned to scratchpad), `db/leadgen.sqlite` read-only, and, for cross-checks only, the sibling `hubspot` chat (`chat_context/raw/hubspot/...`) and its `rat_cad_discovery/` folder. No network calls. No key or token value is quoted. Nothing under `legacy/` or `db/` was modified.
Notation: **M** = measured (a balance delta or an audit file), **E** = estimated (code, counts and a billing model). Times are IST unless marked Z (UTC). Apollo's chart buckets by UTC day, so one UTC day = 05:30 IST to 05:30 IST the next morning.

## 1. Headline answers

1. **The RAT repo spent no Apollo credit before 28 Sep 09:10 IST.** The repo was created 25 Sep 15:00:41 IST (single "Initial commit", author `Nandan <sreenandanms04@gmail.com>`, remote `github.com/nandanlh2/RapidActionTeam`, `Co-Authored-By: Claude Sonnet 5`), cloned onto this laptop at about 15:58 IST, and the RAT chat starts 25 Sep 17:00 IST. The 25 Sep chat only builds the funnel report (HubSpot-only). The first Apollo call in the RAT chat is an `organizations/enrich` probe at 28 Sep 09:10:07 IST, 18 seconds before the first balance read. 21-27 Sep: **RAT repo M 0, E 0.**
2. **RAT-purpose work was done from the sibling `hubspot` repo/session, not from the RAT repo, and it is the only RAT-related spend before 28 Sep.**
   - CAD-100 founders list, 24 Sep 14:53-14:59 IST: **M 194** (27,524 -> 27,330). The user's pasted analysis dates this run "Sep 25"; it is 24 Sep in both IST and UTC.
   - RAT CAD ICP Tracks 1 and 2 (`rat_cad_discovery/`, the source of `leads/RAT_CAD_Tracks1_2_Combined.csv`), 25 Sep 13:25-13:37 IST: **E 575** (159 identity unlocks + 52 Apollo phone-reveal fallbacks x 8), range 340-630.
3. **28 Sep is the only day the RAT repo itself spent credits: M 941 plus E about 850, about 1.8k of the 9.3k Apollo shows for UTC 28 Sep (19%).**
   - M 941 = 14 audited enrichment waves (938) + the 6-candidate smoke test (3). This is a balance delta over RAT's run windows, not a count of RAT calls. RAT's code made 166 unlock calls and 47 phone-reveal attempts in those waves, which models to 166 + 376 = 542 credits. About 396 of the 938 therefore may be non-RAT spend that landed inside the wave windows (section 5.3).
   - E about 850 = Apollo `organizations/enrich` headcount lookups made by `discover_cad_leads.py` (5 runs, 866 retained candidates). The earlier audit marked this "unverified, assumed free". New evidence says billed, about 1 credit per found organisation (section 5.1). Range for the whole RAT repo on 28 Sep: 1.0k-2.1k.
4. **About 83% of the 10,622 credits that left the pool between the 11,101 and 479 readings is not RAT.** Attribution of the three intervals is in section 4. RAT M+E is 1,787 (16.8%, range 10-20%); non-RAT is about 8,835.
5. **Everything the chart shows for 21-24 Sep (about 37k) and 25-27 Sep (7.6k) is non-RAT on the evidence.** For 25-27 Sep the balance anchors are tight enough to say so: 18,588 (25 Sep 11:49 IST) -> 11,104 (27 Sep 21:27 IST) -> 11,101 (28 Sep 09:10 IST), a drop of 7,484 then 3, against chart bars of 3.0k + 0 + 4.6k = 7.6k. RAT-purpose share of that window is the 575 above.
6. **Who spent the rest cannot be settled from this repo.** What the evidence supports (section 6): the sibling `hubspot` Claude session (same laptop, same Apollo key, documented large runs on 24, 25, 27 and 28 Sep), the `companyOps` TAM runs on the evening of 28 Sep, one or more unseen operators holding the RAT HubSpot token (a 66-deal CAD push at 29 Sep 01:28-01:32 IST with no audit file), and possibly humans in the Apollo UI. "AshishCluster" is not a candidate before 29 Sep and, per `../key_exposure.md`, is just the name of the RAT HubSpot private app.

## 2. What I mined and what each source says

| Source | What it gave |
|---|---|
| Git bundle `archive/git_bundles/RapidActionTeam.bundle` | One commit b4757ce, 25 Sep 15:00:41 +0530, author/committer Nandan (`sreenandanms04@gmail.com`), 21 files incl. all importers and both Apollo call sites (`icp_enrich_and_push.py`, `discover_founders_and_push.py`). `uncommitted_at_copy.txt` lists the 28 Sep work (`discover_cad_leads.py`, `enrich_cad_founders.py` untracked). |
| `legacy/RapidActionTeam/audit/` (55 files) | Earliest file is 28 Sep 11:08:43 (backfill). 14 `enrich_cad_founders_*.json` carry `apollo_before/after`. 3 discovery logs. No audit file exists for 20-27 Sep. Audit dir is gitignored, so Nandan's earlier runs (23-25 Sep) would not have been copied. |
| `.env.example` (25 Sep 15:59:13), `.env` (29 Sep 12:51:32) | Timestamps only. 25 Sep 15:59:13 is the clone/checkout time of most files. The `.env` change is after the window. |
| `leads/`, `RAT_CAD_Tracks1_2_Unassigned.xlsx` | The xlsx was created by `openpyxl` on 25 Sep (09:25Z); it and `leads/RAT_CAD_Tracks1_2_Combined.csv` are copies of `legacy/hubspot/rat_cad_discovery/output/`. The 159 rows (138 `source=apollo` org search, 21 `google_maps`) were enriched on 25 Sep 13:25-13:37 IST by `enrich_both_tracks.py` in the hubspot repo. |
| RAT raw chat | Every balance reading and every Apollo tool call. Balance readings are 28 Sep 03:40:25Z (11,101), the 14 waves, and 29 Sep 05:04:50Z (479). One `ps aux` at 28 Sep 05:58:32Z lists every python process on the laptop (section 6). |
| `db/leadgen.sqlite` | `cost_ledger` has no `account_id='rat'` row and no Apollo row before 28 Sep 12:13Z (the 28 Sep Apollo rows are `source_system='tam'`, i.e. companyOps). `deal` table, `account_id='rat'`: 399 deals created 22-25 Sep before the RAT chat started (22 Sep 1, 23 Sep 222, 24 Sep 126, 25 Sep 51), then 230 on 28 Sep. |
| Sibling `hubspot` chat | Four readings the RAT audit did not have: 18,593/18,588 (25 Sep 06:19:53Z), 11,104 (27 Sep 15:57:00Z), 10,689 (28 Sep 05:26:04Z), 27,524/27,330 (24 Sep). Also the only direct billing test of `organizations/enrich`. |
| Usage chart (user screenshot, via coordinator) | Day totals by UTC day; used as bars, not as readings. |
| Usage-panel screenshot (the one with 16,365) | In the RAT chat it arrives as text, not an image: the user pasted another session's analysis at 29 Sep 10:48:58 IST (L2335) quoting Exports 16,365 / Mobile 35,464 / Email 786 and listing script-attributable export credits of 1,433 (9%): CAD 100 194, Singapore 381, UK PropTech 164, SG OutFlo 19, Mobility ICP 20, companyOps people_match 230 and org_enrich 425. The image itself is in the hubspot chat at 10:37:40 IST (L58838) and the companyOps chat at 10:48:51 IST (L25704). It shows the pool panel: Phone numbers 35,464, Exports 16,365, Waterfall Enrichment 1,175, Email 786, Available 469, total 53,811 of 54,280, with a "Team members" tab and an "Action" toggle. No per-day or per-user figures are in the paste. |

## 3. Day by day, re-bucketed to Apollo's UTC days

"RAT-purpose" = work done for the RAT team from the sibling hubspot repo. The two traced categories are never added to each other in the ledger.

| UTC day (IST span) | Apollo chart: total (exports / phones) | RAT repo traced | RAT-purpose, sibling repo | RAT share of the day | Balance anchors and notes |
|---|---|---|---|---|---|
| 21 Sep (05:30 21 -> 05:30 22) | 12.5k (1.9 / 10.6) | M 0, E 0 | none | 0% | No reading exists inside this cycle before 24 Sep. First RAT-portal deal is 22 Sep 14:20 IST. |
| 22 Sep | 6.8k (1.2 / 3.7, + 1.9 other types) | M 0, E 0 | none | 0% | The "other types 1.9k" equals the UI's Waterfall 1,175 + Email 786 = 1,961 (the pool bucket totals on 29 Sep), so those two buckets plausibly landed on this single day. No script in the three repos uses waterfall. |
| 23 Sep | 5.6k (1.6 / 4.0) | M 0, E 0 | none | 0% | RAT portal: 116 deals 12:24-12:32 IST and 88 deals 18:24-18:35 IST, created through the RAT HubSpot token from CSVs that were already enriched. Importers have no Apollo call. |
| 24 Sep | 12.4k (5.0 / 7.4) | M 0, E 0 | **M 194** CAD-100 (14:53-14:59 IST) | 1.6% | 27,524 -> 27,330. Then 8,737 more left by 25 Sep 11:49 IST. RAT portal: 29 deals 13:00-13:05, 95 deals 15:06-15:12 IST (CAD-100 import, 5 min after the list was requested for Sreenandan). |
| 25 Sep | 3.0k (1.5 / 1.5) | M 0, E 0 (chat is HubSpot/funnel-report only) | **E 575** Tracks 1+2 (13:25-13:37 IST) | 19% (all sibling) | 18,593 -> 18,588 at 11:49:53 IST (organizations/enrich test: 6 domains, 5 credits). RAT portal: 50 deals 14:48-14:52 IST. Repo created 15:00:41 IST. |
| 26 Sep | none | 0 | 0 | n/a | Consistent with the balance: between 25 Sep 11:50 and 27 Sep 21:27 IST only 7,484 left and the chart puts 7.6k on 25 and 27 Sep. |
| 27 Sep | 4.6k (1.9 / 2.7) | M 0, E 0 | none | 0% | All of it falls before 21:27 IST (11,104 then; 11,101 at 09:10 IST on 28 Sep). The sibling hubspot Singapore run is 21:13-21:27 IST (8 workers, 1,491 companies, 200 callable): it is the only run in that window; its Apollo cost is not audited here. |
| 28 Sep (UTC; ends 05:30 IST on 29 Sep) | 9.3k (3.8 / 5.5) | **M 941, E about 846** (+1 probe, 0-1) = **about 1.79k** | none | **19%** (range 11-23%) | Everything in this bar is after 09:10:25 IST (only 3 credits left between 27 Sep 21:27 and 28 Sep 09:10 IST). By 13:10:54 IST the pool is at 7,444 (3,657 spent). Of the bar, about 5.6k falls after 13:10:54 IST, with no RAT job. By bucket: RAT about 0.45k of the 5.5k phones (8%) and about 1.3k of the 3.8k exports (35%). |
| (29 Sep UTC, reference only) | not in the target set | 0 | none | n/a | 479 at 10:34:50 IST. About 1.3k left between 05:30 and 10:34:50 IST (6,965 minus the 5.6k above). |
| Sum, 21-28 Sep UTC | about 54.2k | M 941 + E about 850 | M 194 + E 575 | RAT repo 3.3%; with RAT-purpose 4.7% | Pool: 54,280 - 469 = 53,811 consumed on 29 Sep. |

Cross-check of the bars against the balance anchors (this is why I trust the UTC mapping):
- 25 Sep 11:50 IST -> 27 Sep 21:27 IST: balance drop 7,484; chart 25 Sep (3.0k) + 26 Sep (0) + 27 Sep (4.6k) = 7.6k. Agreement within 0.1k.
- 27 Sep 21:27 IST -> 28 Sep 09:10 IST: 3 credits, so UTC 28 Sep 00:00-03:40 is empty and the whole 9.3k bar is after 09:10 IST.
- 28 Sep 09:10 IST -> 29 Sep 10:34:50 IST: 10,622; bar 9.3k leaves about 1.3k for UTC 29 Sep 00:00-05:04.
- 21-24 Sep side does not close: anchors imply about 28.6k consumed before the 24 Sep 27,524 reading, a pool opening of 54,280 implies 26.8k. A 1.9k tension, larger than the bar error (+-0.3k each) alone explains. Possible causes: the cycle opened above 54,280 (carry-over), a bar read error, or credits charged under a different timestamp. Not RAT-relevant, noted for honesty.

## 4. Balance-interval attribution (what the three readings say)

All rows of `rat_ledger.csv` sum exactly to these measured drops (per-interval sums checked in code).

| Interval (IST) | Readings | Drop | RAT M | RAT E (point) | Non-RAT residual | Notes |
|---|---|---|---|---|---|---|
| 24 Sep 14:59:54 -> 25 Sep 11:49:53 | 27,330 -> 18,593 | 8,737 | 0 | 0 | 8,737 | No RAT repo, no RAT Apollo call. Sibling hubspot session ran UK, Australia, CAD and Company Ops work this day. |
| 25 Sep 11:49:53 -> 27 Sep 21:27:00 | 18,588 -> 11,104 | 7,484 | 0 | **575 RAT-purpose, sibling** | 6,909 | Tracks 1+2 at 13:25-13:37 IST on 25 Sep. Sibling Singapore run 27 Sep 21:13-21:27 IST. |
| 27 Sep 21:27:00 -> 28 Sep 09:10:25 | 11,104 -> 11,101 | 3 | 0 | 0 | 3 | Nothing happening. |
| **A. 28 Sep 09:10:25 -> 13:10:54** | **11,101 -> 7,444** | **3,657** | **941** | **846** | **1,870** | RAT working hours (see 5). RAT M 941 = smoke 3 + waves 938. RAT E 846 = discovery org-enrich 5 + 366 + 141 + 23 + 275 (net of wave overlap) + backfill 36. Residual 1,870 = 4 + 394 + 1,472 (section 5). |
| **B. 28 Sep 13:10:54 -> 29 Sep 10:34:50** | **7,444 -> 479** | **6,965** | 0 | 0 | **6,965** | No RAT Apollo job after 13:09:15 (discovery) and 13:10:54 (last wave). Chart split: 5,643 up to 05:30 IST (UTC 28 Sep), 1,322 after (UTC 29 Sep). |
| A + B | 11,101 -> 479 | 10,622 | 941 (8.9%) | 846 (8.0%) | 8,835 (83.2%) | The earlier `../rat.md` said 941 explained and 9,681 unattributed; the new E moves 846 of that into RAT. |

What B contains from sibling evidence (context, not audited here; other agents hold the ledgers): `companyOps` TAM cost_ledger 1,994 credits 28 Sep 17:43-22:09 IST (org_search_page 275, people_match 230, phone_reveal 133 x 8, org_enrich 425); `hubspot` UK PropTech about 1,033 credits (agent-stated; log read 22:23 IST, summary 22:37 IST); `hubspot` SG OutFlo 19 leads. That is at most about 3.0k. That leaves about 2.6k for 28 Sep 13:10 -> 29 Sep 05:30 IST and about 1.3k for 05:30 -> 10:34:50 IST with no local evidence of source. direct_dial_used rose 4,552 in B (569 reveals) while `lead_credits_used` rose only 4, so B is mostly phone reveals, which is the profile of a script or UI reveal, not of a bulk export.

## 5. 28 Sep in detail (the only RAT-repo spend)

### 5.1 Discovery (`discover_cad_leads.py`): organizations/enrich is billed

The script calls `organizations/enrich?domain=` once per ICP-passing candidate that has a domain. The code comment says "free ... confirmed 0-credit". That is contradicted by:
- The sibling hubspot session measured it on 25 Sep: 6 domains, 18,593 -> 18,588 (5 credits) and wrote "costs 1 credit/domain, confirmed empirically - not free, unlike org search" (hubspot L55391/L55395).
- The sibling `cost_ledger` bills `org_enrich` at 1 credit per call.
- A natural experiment inside this window. Between 09:16:27 and 10:56:04 IST only RAT discovery was running on the laptop (run0 alone until 10:49:46). The balance fell 400 (11,089 -> 10,689; the 10,689 reading is the hubspot session's at 05:26:04Z). RAT had about 305 retained candidates by then, i.e. about 1.3 credits per retained candidate as an upper bound. The balance fell again by 560 between 10:56 and 11:44 when two discovery processes ran at once.

Runs (retained = candidates written after ICP and headcount filters; out-of-band headcount drops are not logged, so billed calls are bracketed):

| Run (IST) | Retained | Gross E point (low-high) | Share inside RAT wave windows (already in wave M) |
|---|---|---|---|
| Smoke (Pune) 09:14:58-09:16:07 | 6 (4 with domain) | 5 (4-6) | 0 |
| run0, all 300 combos, 09:17:28-11:29:52 (killed at combo 71) | 366 | 366 (236-476) | 0 |
| batch1 attempt 1, 10:49:46-11:36:07 (kill -9, file deleted, re-run) | about 141 | 141 (79-233) | 0 |
| batch1 final, 11:36:36-12:37:08 | 179 (147 with domain; 111 verified) | 179 (116-233) | 8.5% |
| batch2, 12:38:56-13:09:15 | 174 (all with domain; 117 verified) | 174 (112-226) | 22% |
| Total | 866 retained | 865 (547-1,174) gross | about 55 |

Waste: run0 and batch1 attempt 1 covered the same three cities (about 120 candidates re-billed), and attempt 1 was thrown away. Nothing is cached between runs, so every restart re-bills the same domains.
Ledger E for discovery is net of wave overlap: 5 + 366 + 141 + 23 (before 11:44:15) + 275 (inside the 13 gaps) = 810. With the backfill (36) the RAT E is 846.

### 5.2 Backfill of Prerna's 10 no-phone contacts (11:08:43 IST, inline script)

The audit JSON shows that 8 of the 10 contacts had no SignalHire phone, so `apollo_mobile()` (a phone reveal request) ran 8 times, and `apollo_email()` ran for the contacts with no email on file. The earlier audit counted 2 calls from the agent's own statement. E 36 (range 0-80). `direct_dial_used` rose 152 (19 reveals) in 09:16-11:44, so at most 8 of those 19 reveals are the backfill and at least 11 are not RAT. None of the 10 recovered a +91 mobile; the 10 deals and contacts were then archived (a bulk archive was denied once by the auto-mode classifier and executed after an explicit user "delete the 10").

### 5.3 The 14 waves: what the 938 is

Wave start times are tool-call times; end times are audit-file write times. Per wave, direct_dial reveals (8 credits each) versus RAT's own fallback attempts (rows with a LinkedIn URL where SignalHire gave no phone):

| Total over 14 waves | Value |
|---|---|
| Identity-unlock calls (`people/match` by id, 1 credit if billed) | 166 |
| RAT phone-reveal attempts (8 credits each) | 47 = 376 |
| RAT own spend, code model | 166 + 376 = 542 |
| Balance delta over wave windows | 938 (direct dial 424 = 53 reveals; non-dd 514) |
| Excess over the model | 396 |
| Phone reveals billed in the 13 inter-wave gaps | 121 (968 credits) |

Reading: the dd rise inside waves (53 reveals) is close to RAT's 47 attempts, so the dd inside waves is mostly RAT's. But non-dd credits (514) are 3x the 166 unlock calls, and later waves are worse (wave 14: 22 unlock calls, 116 non-dd credits in 90 seconds, with discovery already finished). Some of the 938 is therefore non-RAT spend that happened to land inside RAT's windows. The ledger still books the full 938 as RAT M because the wave windows are the evidence the earlier audit used; this file flags it as an upper bound.
In the gaps, 121 reveals (968 dd) were billed while RAT's whole 14 waves made 47 attempts: at least 80 reveals in the gaps are not RAT. Peak: 33 reveals (264 credits) in 5.0 minutes (12:38:36-12:43:33), i.e. 6.7 per minute. A sequential poll-based reveal script like RAT's tops out at about 3-4 per minute (4 polls of 4 s per reveal), so this was either a concurrent script (the sibling Singapore pipeline used 8 workers) or a person bulk-revealing in the Apollo UI.

### 5.4 Residual in A, by sub-interval

| Sub-interval | Drop | RAT E | Non-RAT | dd reveals billed |
|---|---|---|---|---|
| 09:10:25 -> 09:16:14 | 9 | 5 | 4 | 0 |
| 09:16:27 -> 11:44:15 (hubspot read 10,689 at 10:56:04) | 960 | 566 | 394 | 19 (152) |
| 11:44:15 -> 13:10:54, waves | 938 | (in M) | (in M) | 53 (424) |
| 11:44:15 -> 13:10:54, 13 gaps | 1,747 | 275 | 1,472 | 121 (968) |

## 6. Who or what could have spent the credits RAT cannot trace

### 6.1 Identities seen around the RAT repo (evidence in this repo)

- **Apollo key owner**: `users/api_profile` returns `Analytics Labs <analytics@lh2.ai>` (team id present in RAT chat L398). All three repos read the same key name. A shared login makes Apollo's "Team members" view collapse to one name (see `../key_exposure.md` section 5).
- **Bhanu Enamala**: owns this laptop; `bhanu` is the unix user in the `ps aux` output; asked every question in the RAT chat; the sibling hubspot repo's remote is `bhanuenamala-lh2ailabs/hubspot_scraping`.
- **Nandan / Sreenandan**: sole git author of the RAT repo (`sreenandanms04@gmail.com`; the lh2 mailbox `sreenandan.m@lh2.ai` received the CAD-100 and Tracks CSVs). The RAT-portal deal timestamps show someone importing each CSV minutes after it was sent: 24 Sep 15:06-15:12 IST (CAD-100, 95 deals) 5 minutes after the request, 25 Sep 14:48-14:52 IST (Tracks, 50 deals) 19 minutes after the email. The importers have no Apollo call, so this is HubSpot-only, but it proves a second operator was holding the RAT token and working on 23-25 Sep from a machine that is not in this repo's evidence. His local audit files are gitignored and absent.
- **Rahul Dhali**: the RAT Claude session's `userEmail` is `rahul.dhali@lh2holdings.com` (the companyOps sessions show `bhanu.enamala@lh2.ai`). A third Claude login ran the RAT session on this laptop. No evidence ties him to any Apollo spend.
- **Callers and RAT-portal owners** (DB `owner` table: Kartik Pillai, Harsha A, Ashish Ranjan, Prerna Jain, Rohan Danny Machado): they work in HubSpot; the caller SOP in the hubspot repo tells callers to do an "Apollo lookup" on wrong numbers, which spends 8 credits per mobile in the UI.

### 6.2 Concurrent automation on this laptop at 28 Sep 11:28 IST (`ps aux`, RAT chat L748)

| Process | Started | Repo | Calls Apollo? |
|---|---|---|---|
| `discover_cad_leads.py` run0 | 09:17 | RAT | Yes (org enrich) |
| `discover_cad_leads.py` batch1 | 10:49 | RAT | Yes (org enrich) |
| `push_outflo.py --dry-run --new-only` | 11:24 | hubspot/crm_mirror/enrich | No call sites; dry run |
| `maps_discovery.py --run --tier tier2` | 11:22 | a companyOps/hubspot discovery folder | No Apollo reference in the companyOps copies (Google Maps) |
| `auto_push.py --threshold 10 --poll 30` | 21 Aug 07:55 | hubspot/godown/founder_id | **No** Apollo reference in the file or in `searchq_enrich.py`; it pushes already-enriched leads to HubSpot. It is the likely source of "mystery" pushes, not of credit spend |
| `serve.py` | 24 Aug | hubspot | No |

So at 11:28 IST nothing else on this laptop was able to spend Apollo credits in the background. The residual of 394 in 09:16-11:44 (and the 19 dd reveals, at least 11 not RAT) must therefore come from an interactive Claude session (the hubspot session checked the balance at 10:56 IST and the user asked "recently we had 50k credits right?"), another machine, or the Apollo UI.

### 6.3 Authors and function of the "foreign automation"

- `auto_push.py` (hubspot repo, mtime 21 Aug): authored in the hubspot repo (Bhanu's repo). It is a 30-second polling watcher that pushes batches of 10 enriched leads alternating Yuktha/Lamiya. No Apollo call site.
- `AshishCluster` (HubSpot `hs_object_source_detail_1`, source id 54087015): first appears in the RAT chat on 1 Oct (76 hits, all 1 Oct). Per `../key_exposure.md` it is the name of the RAT HubSpot private app whose token every script uses, so anyone holding the RAT token shows up under that name. It is not a separate person, and for 20-28 Sep it explains nothing.
- The only unexplained RAT-portal creation event in this window: **66 CAD deals created 29 Sep 01:28:22-01:32:40 IST** at a 4-second cadence (49 to Prerna, 17 to Harsha; 58 of 66 contacts have +91 mobiles; 16 of them show up in the 29 Sep 14:25 Harsha -> Prerna reassign). None is in any RAT audit file; company names (pumps, valves, automation, medical devices) are mostly absent from every local CSV/JSON (only Kishor Pumps and Neilsoft appear in `rat_cad_discovery/raw/track6_apollo.json` and `track3_apollo.json`). Whoever pushed them also enriched them, probably with Apollo and/or SignalHire, on 28 Sep evening. If all 58 mobiles were Apollo reveals, that is up to about 520 credits; at the sibling 62% SignalHire / 38% Apollo mix about 200. The ledger row has credits 0 to avoid double counting.

### 6.4 Credit-spending events with no local evidence of purpose

1. 28 Sep 11:46-13:10 IST: 121 phone reveals (968 credits) and about 450 non-dd credits between RAT waves, beyond RAT's discovery.
2. 28 Sep 13:10:54 -> 29 Sep 05:30 IST: 5,643 credits. About 3.0k has sibling-ledger candidates; about 2.6k has none.
3. 29 Sep 05:30 -> 10:34:50 IST: 1,322 credits. No sibling ledger row exists in this slice (companyOps TAM spend resumes 10:54 IST).
4. 24 Sep 14:59 -> 25 Sep 11:49 IST: 8,737 credits; 25 Sep 11:50 -> 27 Sep 21:27 IST: 6,909 (after removing the RAT-purpose 575). The hubspot chat describes large runs in both windows, but this repo has no ledger for them.
5. 21-24 Sep (about 37k on the chart): no balance anchor and no RAT-side event at all.
6. The Waterfall (1,175) and Email (786) buckets: no local script uses Apollo's waterfall parameters (`../key_exposure.md`), and the chart's "other types ~1.9k" sits on 22 Sep.
7. The 66-deal overnight push in 6.3.

## 7. Confidence

| Claim | Confidence |
|---|---|
| RAT repo made no Apollo call before 28 Sep 09:10 IST | High (chat has none; repo created 25 Sep; audit starts 28 Sep) |
| CAD-100 = 194 credits on 24 Sep (RAT-purpose, sibling) | High |
| Tracks 1+2 = 575 on 25 Sep (RAT-purpose, sibling) | Medium-low (counts are exact, billing per attempt is a model; range 340-635) |
| 941 measured on 28 Sep | High as a balance delta; medium as "RAT's own" (396 may be non-RAT) |
| Org-enrich billed at about 1/found org, RAT discovery about 810-850 net | Medium-low (independent empirical test + trajectory fit, no RAT-side read bracketing a single call) |
| Backfill 8 reveals not 2 | High for the call count (code and audit JSON); low for the cost |
| 7,444 -> 479 splits 5.6k before / 1.3k after 05:30 IST on 29 Sep | Medium (chart-based, +-0.4k) |
| 25-27 Sep spend is non-RAT | Medium-high (anchors and chart agree within 0.1k) |
| 21-24 Sep spend is non-RAT | Medium (no RAT-side event at all, but the anchors do not close by 1.9k) |
| Identity of any non-RAT spender | Low |

## 8. Corrections to `../rat.md` and gaps

Corrections:
1. Org-enrich: moved from "unverified/free" to "probably billed", adding about 810 credits of E to 28 Sep (section 5.1).
2. Backfill: 8 Apollo reveal calls, not 2.
3. The 938 waves are a balance delta, not a proof of RAT-only spend (section 5.3).
4. The window question "how much of the 6,965 fell after 29 Sep 00:00 IST" is answered by the chart split, but note midnight in the chart is 05:30 IST: 5,643 before 05:30 IST on 29 Sep and 1,322 after.
5. The pasted analysis dates CAD-100 "Sep 25"; it is 24 Sep.

Gaps and what would close them:
1. The Apollo UI "Team members" and "Action" views for 28 Sep 11:46-13:10 IST, 28 Sep 13:11 IST - 29 Sep 10:34 IST, and the 24-27 Sep days, filtered to Phone numbers and Exports. The 121 gap reveals are the best fingerprint (multiples of 8, up to 4/min).
2. A controlled before/after read around one `organizations/enrich?domain=` call would settle the 810.
3. Nandan's machine/session (the importers and the 29 Sep 01:30 IST push) and any Apollo key use there.
4. The sibling hubspot ledgers for 24, 25, 27 and 28 Sep (not mined here beyond the readings and run descriptions).
5. Whether Apollo bills a phone reveal that returns nothing; the waves suggest yes, the per-wave dd versus attempts mismatch (for example wave 12:52: 8 attempts, 16 reveals billed) is unresolved.
6. Opening balance and date of the cycle (the 21-24 Sep tension of 1.9k).

## 9. Files written

- `/Users/bhanu/Desktop/LeadGenMonolith/docs/audit/apollo/pre/rat.md` (this file)
- `/Users/bhanu/Desktop/LeadGenMonolith/docs/audit/apollo/pre/rat_ledger.csv`
- `/Users/bhanu/Desktop/LeadGenMonolith/docs/audit/apollo/pre/rat_balance_readings.csv`

Ledger rules: sum `repo=RapidActionTeam` rows for RAT-repo totals (M 941 + E 847 = 1,788). `hubspot(RAT-purpose)` rows are RAT-purpose work done by the sibling repo (M 194, E 575 + 0). `ACCOUNT-UNATTRIBUTED` rows are measured-interval residuals so that each interval sums exactly to its balance drop; they are not RAT. Notes start with [MEASURED] or [ESTIMATED].
