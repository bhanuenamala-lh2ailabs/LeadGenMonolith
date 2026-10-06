# Apollo credit forensics: hubspot repo, 20 to 28 Sep 2026 (IST), before the 29 Sep top-up

Scope: every Apollo.io credit the `hubspot` repo (main portal: `godown/`, `crm_mirror/enrich`, `lh2-pipeline`, `itsvc-tam`, `TAMBuildSpecs`, `rat_cad_discovery`) spent from 2026-09-20 00:00 IST to 2026-09-28 23:59 IST, what each spend bought, and who or what could have spent the credits that cannot be traced. Repo copy `legacy/hubspot`; raw session `chat_context/raw/hubspot/2bf3c003-90a4-4ea2-84fd-4ba8ed554b6b.jsonl` (cited as `L<n>` = 0-based line number); sibling raw sessions for companyOps and RapidActionTeam (RAT) read only to apportion the gap. No network calls, nothing under `legacy/` or `db/` modified, no key value printed.

Companion files in this folder: `hubspot_ledger.csv` (46 rows, first token of `notes` is `[MEASURED]` or `[ESTIMATED]`), `hubspot_balance_readings.csv` (17 rows). A different, later audit covering 29 Sep onward is `../hubspot.md`; the two do not overlap.

## 1. Headline

| | Credits |
|---|---:|
| MEASURED (Apollo balance delta or Apollo's own `credits_consumed`) | **207** |
| ESTIMATED (call counts from logs and outputs times the credit model below) | **12,749** |
| **Attributable to the hubspot repo, 20-28 Sep** | **12,956** (plausible range 10.5k to 15k) |
| of which org-search calls whose billing is unproven (`mixed_companies/search`, 0 to 1 credit per call) | 1,108 |
| Apollo pool consumed in the cycle at 29 Sep 10:34 IST (54,280 - 479) | 53,801 (UI: 53,811) |
| Chart sum 21-28 Sep (user screenshot, UTC days) | ~54.2k |
| Sibling repos, own evidence (companyOps 21-25 Sep ~4.5k, 28 Sep 1,994 ledger; RAT 28 Sep 941) | ~7.5k |
| **Not explained by any local session or script** | **~33.8k of 54.2k (62%)** |

Five facts carry the report:

1. **The unexplained mass is in the first four days of the cycle.** The hubspot session made **zero Apollo calls on 21 and 22 Sep** (checked by scanning every assistant tool call for `api.apollo.io`; the only 21 Sep hit is the Write of a methodology document). The Apollo chart shows 12.5k and 6.8k on those two UTC days. companyOps explains at most ~2.7k of 21 Sep (a 303-contact phone-reveal run) and ~0.1k of 22 Sep.
2. **25 Sep to 27 Sep is almost fully explained by this repo.** Between the two balance readings 18,593 (25 Sep 11:49:46 IST) and 11,104 (27 Sep 21:26:57 IST) Apollo consumed 7,489 credits; the hubspot runs in that interval sum to ~6,240 (83%), ~86% with a sibling adtech job. The credit model is therefore about right.
3. **Spend is session-bound, not continuous.** 27 Sep 21:27 to 28 Sep 09:10 IST (11h43m, no session open) consumed 3 credits. 26 Sep (Saturday, no session of any repo) consumed none. During the CAD founder run on 24 Sep 14:52:58-14:59:52 IST the balance fell 194 and the script's own count is 194, so no third party was spending in those 7 minutes either. Whatever spent the other 62% did it in bursts, not as a steady background drain.
4. **The 29 Sep in-chat audit under-counted this window by about 2.2x.** It summed ~5,782 for 22-29 Sep (L58834) and omitted the Apollo-first UK and Australia batches of 23-24 Sep, UK batches 3-5, the mobility and CAD headcount checks (about 1,130 org-enrich credits), the rat_cad_discovery enrichment and the Shagufta headcount lookups. Even so, the repo explains only 24% of the chart total (the "~9%" quoted on 29 Sep referred to the Exports bucket alone).
5. **Unexplained credit type differs by day.** D21 is phone-reveal dominated (>= 8.2k phone credits, i.e. >= 1,000 reveal requests, with ~1.3k exports). D27's gap is export-only (~1.5k). The Sep-22 'Waterfall' and 'Email' bars (1.1k, 0.8k) equal the whole-cycle bucket totals (1,175, 786), so those two credit types were consumed on a single day and no local script sets any waterfall flag.

## 2. Method, evidence hierarchy, conventions

Evidence ranked: (1) Apollo balance deltas (`api_profile num_credits_remaining`) and Apollo's own `credits_consumed`; (2) exact call logs (corpus `source_attempt`); (3) script counters and per-lead log lines in the chat; (4) output files (row counts, `phone_source` columns); (5) code logic times outcome counts.

Credit model (validated, see section 5):

| Action | Credits | Basis |
|---|---|---|
| `people/match` by id or LinkedIn URL, no reveal (identity unlock, "Exports" bucket) | 1 | measured 194 for 194 calls (24 Sep) |
| `people/match` with `reveal_phone_number` + noop `webhook_url` (poll `webhook_result`) | +8 ("Phone numbers" bucket), billed on request even when no number comes back | Apollo `credits_consumed: 8` (L52026); RAT 28 Sep waves: 16 reveals, 2 delivered, 128 dd credits |
| `organizations/enrich?domain=` | 1 per domain | measured 5 for 5 (25 Sep, L55391) |
| `mixed_people/api_search` | 0 | all repos |
| `mixed_companies/search` | 0 to 1 per call | docs say 1 per page; 3 Oct measurement 0.6 per call; D25 reproduces the chart only if ~0. Carried as 1.0 and flagged low confidence; section 4 shows totals with and without |
| polling `webhook_result`, `users/api_profile` | 0 | |

Time: ledger times are IST; UTC day = IST minus 5h30 (chart buckets). All hubspot activity falls on the same calendar date in both zones. Raw jsonl timestamps are UTC. Scratchpad logs and scripts (`/private/tmp/claude-501/-Users-bhanu-Desktop-hubspot/.../scratchpad`) and `/tmp` logs of this period were deleted by temp cleanup; what remains are the echoes inside the raw jsonl, `rat_cad_discovery/`, `TAMBuildSpecs/_corpus/data/leads.log`, `tam_corpus.sqlite` and `db/leadgen.sqlite`.

Weekdays: 20 Sun, 21 Mon, 22 Tue, 23 Wed, 24 Thu, 25 Fri, 26 Sat, 27 Sun, 28 Mon.

## 3. Per UTC day: traced vs Apollo chart

Chart = user screenshot (+-0.3k). "Sibling" = companyOps/RAT evidence for the same day (their own transcripts or ledgers; not in the hubspot ledger). "hubspot total" = hubspot_ledger.csv.

| UTC day (IST window) | Chart total (exports/phones) | hubspot MEASURED | hubspot ESTIMATED | hubspot total (org-search part) | Sibling | Gap vs chart | Confidence |
|---|---|---:|---:|---:|---:|---:|---|
| 21 Sep (21 05:30 to 22 05:30) | 12.5k (1.9/10.6) | 0 | 0 | 0 | companyOps <= 2.7k | **~9.8k** | high that hubspot = 0 |
| 22 Sep | 6.8k (1.2/3.7 + 1.1 + 0.8 other) | 0 | 0 | 0 | companyOps ~0.1k | **~6.7k** | high that hubspot = 0 |
| 23 Sep | 5.6k (1.6/4.0) | 8 | 1,693 | 1,701 (0) | companyOps 405 | **~3.5k** | medium |
| 24 Sep | 12.4k (5.0/7.4) | 194 | 3,116 | 3,310 (242) | companyOps ~1.1k | **~8.0k** | medium |
| 25 Sep | 3.0k (1.5/1.5) | 5 | 4,003 | 4,008 (776) | companyOps adtech <= 0.2k | **-1.2k** (over-traced) | medium-low |
| 26 Sep | 0 | 0 | 0 | 0 | 0 | 0 | high |
| 27 Sep | 4.6k (1.9/2.7) | 0 | 2,725 | 2,725 (48) | 0 | **~1.9k** | medium |
| 28 Sep | 9.3k (3.8/5.5) | 0 | 1,212 | 1,212 (42) | companyOps 1,994; RAT 941 | **~5.2k** | medium |
| **Total** | **54.2k** | **207** | **12,749** | **12,956 (1,108)** | **~7.5k** | **~33.8k** | |

Modelled credit type of the hubspot spend vs the chart (phone = 8-credit reveals; export = unlocks and org-enrich):

| UTC day | hubspot phone / export | Chart phone / export | Unexplained phone / export |
|---|---|---|---|
| 23 | 1.51k / 0.19k | 4.0k / 1.6k | 2.5k / 1.4k |
| 24 | 2.54k / 0.52k (+0.24k search) | 7.4k / 5.0k | 4.9k / 4.2-4.5k |
| 25 | 1.54k / 1.69k (+0.78k search) | 1.5k / 1.5k | -0.04k / -0.2k |
| 27 | 2.30k / 0.38k | 2.7k / 1.9k | 0.4k / 1.5k |
| 28 | ~0.9k / ~0.25k | 5.5k / 3.8k | ~4.6k / ~3.5k |

## 4. Balance anchors: where the credits went between readings

Pool 54,280 (renewal ~20 Sep, no top-up before 29 Sep 12:38 IST). Readings come from the hubspot chat (A1, A2, A3, A5) and from the RAT session on the same account (A4, A6, A7), see `hubspot_balance_readings.csv`.

| Interval (IST) | Consumed | hubspot traced | Sibling traced | Unexplained | Sessions open on this laptop |
|---|---:|---:|---:|---:|---|
| P0: renewal ~20 Sep -> A1 24 Sep 14:52:58 (27,524) | 26,756 | 2,987 | ~3.5k | **~20.3k (76%)** | hubspot 21 Sep 10-14, 22 Sep 10-11/15-17/20-21, 23 Sep 11-12/15-20, 24 Sep 07-08/10-14; companyOps 21 Sep 10-16, 22 Sep 11-19, 23 Sep 11-19, 24 Sep 10-14 |
| P1: A1 -> A2 25 Sep 11:49:46 (18,593) | 8,931 | 2,517 | ~0.8k | **~5.6k (63%)** | hubspot 24 Sep 14:53-20:54, then idle 14h until 25 Sep 10:48; companyOps 24 Sep to ~21:00 |
| P2: A2 -> A3 27 Sep 21:26:57 (11,104) | 7,489 | 6,240 | ~0.2k | **~1.0k (14%)** | hubspot 25 Sep 11:50-20:09 and 27 Sep 20:03-21:30; companyOps 25 Sep to 16; none 26 Sep |
| P3: A3 -> A4 28 Sep 09:10:25 (11,101) | 3 | 0 | RAT smoke 3 | 0 | none (11h43m idle) |
| P4: A4 -> A5 28 Sep 10:55:56 (10,689) | 412 | 0 | ~3 | **~409** | RAT 08-10, companyOps 08-10, hubspot 08:22-08:30 (HubSpot only) |
| P5: A5 -> A6 28 Sep 13:10:54 (7,444) | 3,245 | 135 | RAT waves ~938 | **~2.2k (67%)** | RAT 08-14, companyOps 08-13, hubspot 10:55-13:09 |
| P6: A6 -> A7 29 Sep 10:34:50 (479) | 6,965 | 1,077 | companyOps 1,994 | **~3.9k (56%)** | companyOps 16-22, RAT 17/19-20, hubspot 17:08, 20:22-23:08 |
| Total | 53,801 | 12,956 | ~7.5k | **~33.4k (62%)** | |

P6 split by credit counter (RAT api_profile): `direct_dial_used` +4,552 (569 reveals) of which ~1.9k explained, ~2.7k unexplained (~340 reveals); non-dd +2,413 of which ~1.2k explained.

## 5. Chart vs balance consistency (why the per-day gaps carry about +-1.5k)

Cumulative consumed at the anchors does not fully agree with the chart's cumulative: at A2 (25 Sep 06:19 UTC) the balance says 35,687 consumed, the chart's cumulative through the end of 24 Sep is 37.3k. So the D23/D24 bars are over-read by at least 1.6k (or one of them is bucketed differently). The chart and the balances agree on D25+D26+D27 (7.6k vs 7.49k) and on D28 within 1.3k (balance 10.6k, chart 9.3k). Report the per-day gaps as +-1.5k. In particular the D25 over-trace (-1.2k) is within that noise and is also what you get if org searches are free; the D25 core (non-search) trace reproduces the chart in both buckets (phone 1.54k vs 1.5k, export 1.69k vs 1.5k), which is what validates the model.

## 6. Day by day (IST, with UTC re-bucketing)

Session activity is from event timestamps in the three raw sessions. "Idle" below means no Claude session of any of the three repos had an event in that hour.

### Sun 20 Sep
No session, no chart bar. Cycle renewal. Nothing to trace. Confidence high that this repo spent 0.

### Mon 21 Sep (UTC day 21 = 21 Sep 05:30 to 22 Sep 05:30)
- hubspot session 10:46-14:53 IST: Cold Call counts, 102 deals moved Yuktha -> Lamiya, seat list, a Gmail-token question, new stage "Dropped - Pretraining". At 14:42-14:43 it read `godown/apollo_reveal/apollo_reveal_runner.py` and wrote `docs/reference/APOLLO_SIGNALHIRE_PHONE_REVEAL_METHODOLOGY.md` for the sibling project. **No Apollo call.** (ledger row 1)
- companyOps session (not this repo): 14:25-15:36 IST ran `people/match` over 303 fintech companies (pass 1) and then an Apollo-first phone reveal over the same 303 (213 Apollo mobiles, 56 SignalHire); 21 Sep 14:47 test shows `credits_consumed: 8`. At most 303 + 303x8 = 2.7k credits.
- Chart 12.5k = 1.9k exports + 10.6k phones. Gap ~9.8k, of which >= 8.2k phone credits.
- Time of day: both sessions ended before ~16:30 IST; 13 hours of that UTC day (16:30-05:30) and 4.5 hours before 10:00 had no session. No balance reading exists before 24 Sep.
- Confidence: hubspot = 0 (high). Gap attribution: none possible from local evidence.

### Tue 22 Sep (UTC day 22)
- hubspot session 10:57-21:11: HubSpot-only (CAD counts, API-scope list, 20-lead CSV, snapshot backfill). Hit the Claude org monthly spend limit 20:16-21:10. **No Apollo call.** (ledger row 2)
- companyOps: 11:36-11:38 IST email enrichment of 105 contacts (`people/match reveal_personal_emails`, 94 Apollo hits), ~110 credits.
- Chart 6.8k (1.2k/3.7k) plus 1.1k and 0.8k of two other credit types. Those two equal the cycle totals of the Waterfall (1,175) and Email (786) buckets: all such credits of the cycle were spent on this UTC day. `run_waterfall` appears in none of the three repos nor in the three chats.
- Gap ~6.7k. Confidence high that hubspot = 0.

### Wed 23 Sep (UTC day 23: 23 05:30 to 24 05:30). hubspot 1,701 (M 8, E 1,693)
All Apollo-first scripts (reveal for every lead, SignalHire only as fallback), which is why the chart's phone bar is large.

| IST | Run | Apollo calls | Credits | Evidence |
|---|---|---:|---:|---|
| 12:14:31 | `global_coops_enrich_push.py` (Outflo Singapore/Australia/Georgia connected leads) | 16 | 144 E | L51681, L51698, L51702 |
| 16:34:53-16:53 | `uk_campaign_enrich_push.py` (UK export, stop at 50 valid) | 57 | 513 E | L51878, L51967 |
| 18:23:14 | ad-hoc phone reveal for one LinkedIn URL | 1 | 8 M + 1 E | L52026 `credits_consumed: 8` |
| 19:01:57-~20:12 | `aus_campaign_enrich_push.py` (Australia export, 50+50 valid) | ~115 (105-125) | 1,035 E | L52085, L52339, L52395 |

companyOps same day: 405 (fintech smoke 11 + full run 298 + richness map 96, 13:43-14:37 IST).
Chart 5.6k (1.6k/4.0k). Gap ~3.5k (2.5k phone, 1.4k export). Confidence medium. If every uncertain count is at its high end the hubspot figure rises by only ~170.

### Thu 24 Sep (UTC day 24: 24 05:30 to 25 05:30). hubspot 3,310 (M 194, E 3,116)
Before 14:50 the batches were Apollo-first; at 14:50 the user fixed the protocol ("apollo for people search, signalhire first for enrichment", L53739) and at 17:23 "actually use signalhire first" (L54005), so batches 3-4 and the Australia run call Apollo only after a SignalHire miss.

| IST | Run | Apollo calls | Credits | Evidence |
|---|---|---:|---:|---|
| 10:46-11:02 | CAD 3D-design test: richness map, candidate pulls (org search, "confirmed free" only from headers) | ~90 searches | 90 E, low | L52644-L52878 |
| 13:20:32-14:21 | `enrich_push.py` UK batch 2 (Apollo first, crashed at 71 processed, resumed) | 132 | 1,188 E | L53317, L53382, L53500 |
| 13:26 | CAD_150 pull | 8 searches | 8 E | L53348 |
| 14:52:58-14:59:52 | `cad_founder_pipeline.py` (227 companies, 100 pass) | 194 unlocks | **194 M** (27,524 -> 27,330) | L53819/L53820 |
| 15:31-16:06 | `backfill_email.py` (SignalHire first; Apollo on 12 misses) | 12 | 12 E | L53889, L53923 |
| 17:18:56-17:28 | `uk_batch3_akarsh.py` (78 attempts, 28 SignalHire hits) | 50 | 450 E | L54033, L54037 |
| 17:42:49-18:09 | `australia_full_run.py` (121 attempts, run 1 killed after 15) | ~59 | 531 E | L54162, L54218, L54276 |
| 18:09:23-18:46 | `uk_batch4_5050.py` (139 attempts) | ~77 | 693 E | L54280, L54439 |
| 18:10 | `gap_fill.py` CAD pan-India (8 cities x 6 industries x 3 bands) | 144 searches | 144 E, low | L54308 |

companyOps same day: proptech richness 200 + collect 59 (12:08-12:18), adtech richness ~33, construction 88 (18:06), mobility domain resolution 209 + ~500 searches (16:28-19:11): ~1.1k, mostly org searches.
Chart 12.4k (5.0k/7.4k). Gap ~8.0k (4.9k phone, 4.2-4.5k export). Confidence medium. Process census at 24 Sep 14:21 IST (L53494): the only non-IDE Python processes were `maps_discovery.py --run` (started 14:09, companyOps adtech, Apify/Maps), `serve.py` (since 24 Aug) and `auto_push.py --threshold 10 --poll 30` (since 21 Aug). `auto_push.py` reads local state files and writes HubSpot only (no Apollo import); neither of the other two calls Apollo.
Idle: hubspot 20:54 -> 10:48 (14h) and companyOps after ~20:00. P1's 5.6k unexplained cannot be placed in the idle hours by the P3 precedent (3 credits in 11h43m idle); it most plausibly fell in 24 Sep 14:59-20:54 IST, concurrent with the batches above.

### Fri 25 Sep (UTC day 25: 25 05:30 to 26 05:30). hubspot 4,008 (M 5, E 4,003; 776 of it org-search)
All between 11:34 and 14:31 IST.

| IST | Run | Calls | Credits |
|---|---|---:|---:|
| 11:34-11:47 | `mobility_discovery/resolve_domains.py` (sibling script run from this session, resumed) | 487 name searches | 487 E (290-487) |
| 11:49:37-11:49:52 | org-enrich probes | 5 + 1 | **5 M** (18,593 -> 18,588) + 1 E |
| 11:50-12:04 | `mobility_icp_pipeline` v1/v2/v3: headcount 592 domains, 96 unlocks, ~26 reveals | | 592 + 96 + 208 E |
| 12:24-12:54 | `cad_100_more_pipeline.py` (542 domains, 51/100 qualified) | 542 enrich, ~118 unlocks, ~55 reveals | 542 + 118 + 440 E |
| 12:27-14:31 | `rat_cad_discovery` (richness 23, track 2 pull 9, enrich 159 unlocks + 52 reveals, tracks 3-6 29) | | 23 + 9 + 159 + 416 + 29 E |
| 13:39-13:55 | `uk_batch5_akarsh.py` (93 attempts) | ~60 | 540 E |
| 14:09-14:20 | Shagufta headcount: 25 enrich, 228 name searches, 90 enrich | | 343 E |

The `rat_cad_discovery` output files are the best-documented estimate in the window: 159 rows (one per unlock), 23 `phone_source=apollo` plus 29 rows with LinkedIn URL but no phone (Apollo tried and failed) = 52 reveal requests.
Chart 3.0k (1.5k/1.5k). Traced is above the chart by ~1.2k (see section 5). Confidence medium-low (large sums of estimates, org-search billing unknown). Hubspot idle: 20:09 -> next 25 Sep 10:48 had no Apollo calls; the Claude org spend-limit message at 10:48 IST blocked the session until 11:31.

### Sat 26 Sep
No session of any repo. Chart 0. Balance flat. Strong evidence that nothing runs unattended on this machine.

### Sun 27 Sep (UTC day 27: 27 05:30 to 28 05:30). hubspot 2,725 (E)
- 20:03-21:30 IST only. 20:17 `sg_vertical_richness.py` (24 searches); 21:11 `sg_discovery.py` (~24 pages); 21:13:15-21:26 `sg_pipeline.py`: 1,371 companies processed, 381 unlocks (1 each), 287 reveal requests after 94 SignalHire hits (8 each = 2,296), 200 callable (106 Apollo, 94 SignalHire), 134 email-only. Evidence L56937, L56966, L56970, L58832.
- Reading 11,104 at 21:26:57 (A3) is after the run; no reading before it, so the run is E not M.
- Chart 4.6k (1.9k/2.7k). The balance anchors (11,101 at 09:10 next morning) prove all D27 spend happened before 21:27 IST. Gap ~1.9k, mostly exports (~1.5k), unexplained and placed in 27 Sep 05:30-21:13 IST, a period in which no session was open until 20:03. Confidence medium.

### Mon 28 Sep (UTC day 28: 28 05:30 to 29 05:30). hubspot 1,212 (E)
- 08:22-08:30 SG push to HubSpot only (0 Apollo). 10:55:56 balance 10,689 (A5). 11:24 refreshed Outflo data; 11:32-11:35 `sg_outflo_push.py`: 19 leads, 4 SignalHire hits, 15 Apollo reveals (135 E). 11:44-11:45 the user stopped a UK batch before it started (L57547).
- 17:08 a Sales Nav URL discussion (no Apollo); 20:22-20:36 reports; 21:36 user: "build this ... DO NOT use apollo at all" (L58134); **22:02:49 user lifts the block** (queued prompt L58435: "use apollo poc search then signalhire ... waterfall"). 22:06-22:08 probes (8 calls) and `pool_apollo_orgs.py` (34 org-search calls, `source_attempt` ids 2673-2706); 22:10:06 two-lead test (~2); 22:10:44-22:22 `build_leads.py --target 98`: the script's own counter says `apollo credits ~1033` for 98 leads (61 SignalHire waterfall, 37 Apollo reveals, 8 no POC, 22 no UK mobile). Not validated by a balance reading. The CSV on disk was overwritten on 30 Sep (50 rows, 657 credits), so lead-level detail is lost.
- Sibling spend the same UTC day: RAT audited waves 938 + smoke 3 (08:00-13:10 IST), companyOps `cost_ledger` 1,994 (20:00-22:09 IST).
- Chart 9.3k (3.8k/5.5k); balance-based D28 = 10.6k. Gap 5.2-6.5k. The unexplained part sits in P4/P5/P6: 409 (09:10-10:56), ~2.2k (10:56-13:10, while RAT discovery and companyOps ran, i.e. concurrent with sessions), ~3.9k (13:11 -> next day 10:35, of which ~2.7k is direct-dial/phone).

## 7. Who or what could have spent the untraced credits

Evidence classes asked for: (1) time of day vs session active/idle, (2) credit type vs this repo's scripts, (3) burn pattern vs this repo's code, (4) other users, machines, automation.

### 7.1 Time of day
- Idle windows show almost no spend: P3 (11h43m idle) 3 credits; Saturday 26 Sep none; 24 Sep 14:53-14:59 balance delta equals the script count exactly. A constant-rate third-party drain is therefore excluded; the unexplained spend arrives in bursts.
- Bursts of 5-10k in a UTC day (D21, D22, D24) are far above what this repo can produce in the hours in which its sessions were open (its own biggest day is 4.0k). On 21 Sep neither the hubspot nor the companyOps session was open for ~17 of the 24 UTC-day hours, and one burst of >= 1,000 reveals would take ~6-11 h single-threaded (one reveal per 20-40 s with the 8 s poll), or 1-2 h with 8 workers.
- Sessions of companyOps (21-25 Sep, 28 Sep) and RAT (25 Sep evening, 28 Sep) use the same key and are each concurrent with unexplained intervals, so a share of the gap may be unlogged calls of those sessions (companyOps `cost_ledger` only starts 28 Sep and books only the TAM pipeline; RAT audit already lists ~2.7k "between waves" on 28 Sep).

### 7.2 Credit type
- This repo's scripts always pair 1 export with 8 phone credits per Apollo-first reveal (8:1), or only exports (headcount, unlocks). D21's unexplained ratio is ~6:1 phone:export, D24 ~1.1:1, D27 0.3:1 (exports only), D28 ~1.3:1. The D27 export-only gap and the single-day Waterfall/Email bars do not match any script here. Export-heavy and waterfall/email credits fit Apollo-UI actions (CSV export, bulk "enrich", waterfall enrichment) better than the API pipelines.
- A ratio near 8:1 with few exports (D21) fits re-revealing already-exported contacts, either through the API (scripts re-calling `people/match` with `reveal_phone_number` for known people) or by clicking "access mobile" in the UI.

### 7.3 Burn pattern vs code
- Code facts that raise spend relative to what the operator saw: scripts bill 9 per Apollo-first attempt even when no UK/AU number is delivered; SignalHire-first scripts still call Apollo on every miss; pipelines run 8 workers with `acall` retries (tries=4 on 429/5xx/timeouts: a timeout after a processed reveal can double-bill; unquantified, bounded to ~10-20% of reveal spend, so <= ~0.8k over the window); the agent assumed org search and org enrich were "free" until a balance test on 25 Sep (enrich) and 3 Oct (search).
- None of these closes a 33k gap.

### 7.4 Other users, machines, automation (checked)
- **Git, hubspot repo**: 3 commits, all Bhanu Enamala <bhanu.enamala@lh2.ai> (8 Sep x2, 18 Sep). Every script that called Apollo in the window is uncommitted (`archive/git_bundles/hubspot.uncommitted_at_copy.txt`); the older Apollo code (`godown/apollo_reveal`, `apollo_phone_reveal.py`, `gujarat_qualify/*enrich*`) arrived in the initial commit. No other author, no branch.
- **Git, siblings that share the key**: companyOps has other committers: Nandan <sreenandanms04@gmail.com> (8 Sep, 23 Sep), ChPuru <puruchoudhary9b@gmail.com> (Aug, dashboards and Actions), corpDev <corpdev@lh2holdings.com> (6-10 Aug, built the Company Ops pipeline incl. OutFlo sync), plus Bhanu under three laptop host names. RAT's only commit (25 Sep 15:00 IST) is by Nandan and contains the Apollo-calling module `phone_enrich_apollo_signalhire.py`, the scripts that use it (`icp_enrich_and_push.py`, `discover_founders_and_push.py`) and an `.env.example` with `apollo_api_key`. So at least two other people have Apollo-calling code; whether they hold the key was not testable.
- **Same key everywhere**: the Apollo key value in `legacy/hubspot/.env`, `legacy/companyOps/.env` and `legacy/RapidActionTeam/.env` is identical (compared by hash; value not shown). The RAT `.env` has the same mtime as hubspot's (29 Sep 12:51), so it was copied. Any machine with a copy spends from the same pool.
- **GitHub workflows**: `.github/workflows/deploy-dashboard.yml` (HubSpot dashboard, hourly) and `lh2-pipeline/.github/workflows/{daily-report,deploy-dashboard,monthly-run,nightly-enrich}.yml` reference only `HUBSPOT_API_KEY`, `GMAIL_*`, `SIGNALHIRE_API_KEY`; none reference Apollo. companyOps workflows (`deploy_ops_dashboard.yml`, `outflo-sync.yml`) and RAT's `dashboard.yml` contain no Apollo reference.
- **Local automation**: `crontab` empty; `~/Library/LaunchAgents` has only `ai.lh2.cadoutreach` (email) and Google updater plists; long-running `auto_push.py` and `serve.py` do not call Apollo.
- **Interns and others**: `INTERN_TASK.md` (17 Aug take-home: free tiers only, 25-company CSV, no Apollo mention), `PURUNJAY_DEPLOY_TASK.md` (Cloudflare, GitHub secrets RELAY_*, HubSpot webhooks; no Apollo), `godown/intern_hiring` (applicant JSON) and `godown/intern_screen` (resume scoring) contain no Apollo call. No document in the repo hands the Apollo key to an intern. The Apollo sales thread shows a 3-seat Organization quote and a chosen Professional seat + add-on; the number of seats and users with UI access on the live plan is not in the repo.
- **A file produced outside the session**: `RAT_CAD_Tracks1_2_Unassigned.xlsx` appeared in the repo root on 28 Sep 12:26 IST (openpyxl, created 25 Sep 09:25 UTC) derived from the CAD list this session sent to Sreenandan; the agent concluded someone ran it through the RAT validator "outside my visibility" (L57662). It shows that at least one other person or session was processing Apollo-sourced lead lists, but it contains no Apollo spend evidence.
- **Other Claude projects on the machine**: `~/.claude/projects` also holds `files--1-`, `taskRESEARCH`, `Downloads-ddb`, `Downloads-cherry`; none contains an `api.apollo.io` call in 18-29 Sep.
- **Not testable offline**: Apollo web-UI use (CSV exports, mobile reveals, waterfall enrichment, sequences), HubSpot-Apollo native sync, any machine other than this laptop, and the Apollo "Team members/Action" log. The 29 Sep chat already named "human bulk UI exports" as the hypothesis; nothing local contradicts or confirms it.

### 7.5 Gap blocks (largest first)

| Block | Credits | When (IST) | What the local evidence allows |
|---|---:|---|---|
| G1 D21 | ~9.8k (>= 8.2k phone) | 21 Sep 05:30 - 22 Sep 05:30 | Sessions open only 10:00-16:30; no hubspot call; phone-dominated; consistent with ~1,000+ reveal requests or UI mobile reveals; companyOps explains <= 2.7k |
| G2 D22 | ~6.7k incl. waterfall 1.1k + email 0.8k | 22 Sep 05:30 - 23 Sep 05:30 | No hubspot call; waterfall and email credit types never used by any local script; points to UI/enrichment feature or other holder of the seat/key |
| G3 P1 | ~5.6k | 24 Sep 14:59 - 25 Sep 11:49 | Spend almost certainly 24 Sep 14:59-20:54 (idle precedent); concurrent with hubspot batches 3-4 and companyOps jobs; model cannot absorb it |
| G4 D23 | ~3.5k | 23 Sep 05:30 - 24 Sep 05:30 | Concurrent with the hubspot Apollo-first batches; phone 2.5k, export 1.4k unexplained; part may be under-counted retries and companyOps |
| G5 P6 | ~3.9k (dd ~2.7k) | 28 Sep 13:11 - 29 Sep 10:35 | RAT idle of Apollo jobs per its audit; companyOps ledger covers only the TAM pipeline (28 transcript events on 28 Sep, 13:24-20:33 IST, mention `api.apollo.io`: inline probes and scripts that the ledger does not book) |
| G6 P5 | ~2.2k | 28 Sep 10:56 - 13:10 | Overlaps RAT discovery and companyOps morning session; RAT audit already calls out the gaps between waves |
| G7 D27 | ~1.5-1.9k (exports) | 27 Sep 05:30 - 21:13 | No session until 20:03; export-only; could also be SG pipeline calls billed more than modelled (e.g. a second export on each reveal-by-URL = +287) |
| G8 P2/P4 | ~1.0k + ~0.4k | 25 Sep 14:31 - 27 Sep 21:13; 28 Sep 09:10-10:56 | Small; within estimation error of the traced runs |

## 8. Events without local evidence of purpose

For this repo every ledger event has a stated purpose in the chat; none is purposeless. The credit-spending events that have **no local trace at all** are the gap blocks G1-G8 above. Items inside the ledger whose existence is certain but whose **credit amount** is unsupported:

1. All `mixed_companies/search` rows (242 on 24 Sep, 776 on 25 Sep incl. the 487 mobility-resolve calls, 48 on 27 Sep, 42 on 28 Sep): 0 to 1 credit per call.
2. Aus batch scan count (23 Sep, ~115 +-10) and the UK/Australia SignalHire-hit shares on 24-25 Sep batches (50-77 Apollo calls each, +-15).
3. SG pipeline reveals (287 assumes every POC with a LinkedIn URL and no SignalHire hit was revealed) and the `build_leads.py` 1,033 counter (unvalidated).
4. Mobility and cad_100_more reveal counts (26, 55) and the cad_100_more headcount pass (542 enrich) which relied on no cache.
5. Any in-flight double billing at the UK batch-2 crash (one call, +9, not counted).

## 9. What the 29 Sep in-chat audit missed

Its table (L58834): CAD 194 (placed on 25 Sep; actually 24 Sep 14:52 IST), mobility ~20, Singapore 2,677, SG Outflo 139, UK Proptech 1,033, companyOps 1,719 = 5,782. It omitted: UK batch 2 (1,188), UK batches 3-5 and the Australia run (2,214), the 23 Sep batches (1,701), mobility headcount and unlock (688 beyond reveals), cad_100_more (1,100), rat_cad_discovery (636), Shagufta headcount (343), org-search calls (1,108 under the unproven rate) and 21-24 Sep sibling spend. It also told the user on 28 Sep 10:56 that "a real top-up happened" between the readings 27,524 and 18,593; that was a misreading of two dates (24 Sep and 25 Sep): the balance simply fell, there was no top-up.

## 10. Confidence, gaps, and what would settle the question

- High: zero hubspot Apollo calls on 21, 22, 26 Sep; balance anchors; the 194 and 5 measured items; session-bound behaviour.
- Medium: per-run call counts that come from printed log lines (23 Sep batches, UK batch 2, SG, UK Proptech); the credit model (reproduces D25 in both buckets).
- Low: org-search billing; SignalHire-hit shares; D21-D24 gap attribution.
- Not available: Apollo UI usage by user/action, the credit CSV export, the API-key list, other machines. These are the only sources that can distinguish a second person, the UI, or an Apollo-side automation from this repo's own pipelines. A same-day export of Apollo's "Credit usage -> Team members / Action" for 21-25 Sep, and the key's created-by/last-used metadata, would close G1-G4 (about 29k of the gap).

Files written: `docs/audit/apollo/pre/hubspot.md`, `docs/audit/apollo/pre/hubspot_ledger.csv`, `docs/audit/apollo/pre/hubspot_balance_readings.csv`.
