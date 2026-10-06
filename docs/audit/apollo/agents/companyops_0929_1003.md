# companyOps agent-wise Apollo audit: spend NOT in the cost_ledger (29 Sep 00:00 IST to 4 Oct 06:00 IST)

Session `7bb0eacd-76f8-4007-b5de-d906ec869ddc` (repo `companyOps`). Read-only, no network calls, nothing under `legacy/` or `db/` modified, no key or token value printed or written. No tool call was denied. Companion file: `companyops_0929_1003.csv` (102 rows: 21 main-agent call paths, 81 subagents).

This report is additive to `docs/audit/apollo/companyops.md` (6,272 measured credits, all from `cost_ledger`). Its question is different: what Apollo spend is **not** in that ledger, agent by agent.

## 1. Headline

| | Low | Central | High |
|---|---:|---:|---:|
| Credits tracked in `cost_ledger` (re-derived, agrees with first audit) | 6,272 | 6,272 | 6,272 |
| Untracked, evidence-based rows (M05 to M08, M09, M10, M15, M16) | 4 | 14 | 69 |
| Untracked, mechanism-only rows (M17 kill-in-flight, M18 silent retries) | 0 | 0 | 27 |
| **Untracked total** | **4** | **14** | **96** |
| Model risk, excluded above: Email-bucket credit if `reveal_personal_emails=true` is billed separately (M19) | 0 | 0 | up to 1,338 |

1. **The cost_ledger is call-complete for every call made by the repo code.** Four independent tie-outs between run-summary counters in `tam/logs/*.jsonl` and ledger rows match to the unit (section 4). Ledger gaps exist only for calls that bypassed `costs.charge()`: hand-typed probes and one ad-hoc script.
2. **Central untracked spend is 14 credits (range 4 to 96).** The companyOps repo cannot account for the ~36,000 credits the first audit could not explain. The missing consumption is not hiding in unledgered companyOps calls.
3. **No subagent called Apollo.** All 81 subagents in the window are classification agents reading local JSON; their 2,198 tool calls contain no HTTP client, no key, no Apollo call, and 0 WebSearch/WebFetch.
4. **One finding is new versus the first audit:** `scratchpad/repair_titles.py` (30 Sep 15:55 IST) spent up to 24 credits on `people/match` with no ledger row, and the assistant first called the match "free" (M16). The first audit missed it because it looked only at ledger rows and `costs.charge` call sites.
5. The 31 "START-PROBES" in the first audit shrink to **at most 29**: the probe code was added at 12:37 IST on 29 Sep, so earlier starts did not probe, and the 12:37 probe itself was refused with 422 (free).

## 2. Agents in scope (step a)

| Agent | Window (IST) | Count | Apollo calls |
|---|---|---:|---|
| `main` (session 7bb0eacd) | 29 Sep 10:33 to 3 Oct 22:54; 909 assistant text blocks, 1,005 tool calls in range (746 Bash) | 1 | yes, see section 3 |
| Subagents 29 Sep 11:40-11:48 (fintech/edtech batch classifiers) | 11:40 to 11:48 | 18 | none |
| 29 Sep 12:41-12:46 (edtech batches, 3 coordinators + 10 per-batch) | 12:41 to 12:46 | 13 | none |
| 29 Sep 13:02-13:37 (fintech/edtech/healthcare batches) | 13:02 to 13:37 | 12 | none |
| 29 Sep 16:00-16:36 (healthcare/fintech batches) | 16:00 to 16:36 | 13 | none |
| 29 Sep 17:11-18:40 (manufacturing/adtech/construction/ecommerce) | 17:11 to 18:40 | 19 | none |
| 29 Sep 19:09-19:57 (ecommerce) | 19:09 to 19:57 | 3 | none |
| 30 Sep 13:11-13:20 (ecommerce) | 13:11 to 13:20 | 3 | none |

The subagents directory has 180 agent transcripts (360 files counting `.meta.json`); 81 overlap the window, 99 ended before 29 Sep 00:00 IST. Per-agent id, task, first/last timestamp and tool mix are in the CSV. There is no subagent activity on 1-3 Oct.

## 3. Every Apollo call path, belief versus billing, tracked or not (steps b, c)

Billing rule used (from the brief and the code): `api_search` free; `mixed_companies/search` 1 per call returning at least one org; `people/match` 1; `bulk_enrich` 1 per domain; phone reveal +8, billed on request even when no number returns; HTTP 422 refused = 0. For a `people/match` that returns **no person** I carry two readings: Apollo bills only successful matches (low and central) or every HTTP 200 (high, which is also the repo ledger's convention).

| Row | Agent / path | IST time | Calls | Believed cost | Actual (central) | Tracked? | Untracked low / central / high |
|---|---|---|---:|---|---|---|---|
| M01 | main: `cli outreach` 29 Sep | 10:54-16:35 | 982 | 1/1/8, measured | 3,832 | yes, ledger | 0 / 0 / 0 |
| M02 | main: `cli repair` | 14:47-15:15 | 77 | "1 credit" | 77 | yes | 0 / 0 / 0 |
| M03 | main: `backfill_linkedin` test | 15:43 | 1 | "1 credit" | 1 | yes | 0 / 0 / 0 |
| M04 | main: probe 1, bulk_enrich setu.co | 12:02:24 | 1 | "exactly 1 credit" | 1 | yes, by hand | 0 / 0 / 0 |
| **M05** | main: probe 2, people/match + phone reveal (lalitkeshre) | 12:02:36 | 1 | "9 credits", and "I charged those checks to the ledger" | 9 | **no** (false claim) | 1 / **9** / 9 |
| M06 | main: 2 bogus-name "billable probes" | 12:45:36, 12:52:13 | 2 | free ("Apollo untouched") | 0 to 2 | no | 0 / 0 / 2 |
| M07 | main: `mixed_companies/search` x2 | 12:52:36, 12:52:46 | 2 | "1 credit per page" | 2 | no | 0 / 2 / 2 |
| M08 | main: `bulk_enrich` kredx + innoviti | 12:54:54 | 1 | "1 credit/org" | 2 | no | 2 / 2 / 2 |
| M09 | main: START-PROBES 29 Sep | 12:37 to 16:26 | 18 (+1 refused) | "at most one credit" | 0 to 18 | no | 0 / 0 / 18 |
| M10 | main: START-PROBES 30 Sep | 11:04 to 13:33 | 11 | same | 0 to 11 | no | 0 / 0 / 11 |
| M11 | main: 129 refused 422 calls (124 match, 5 bulk) | 12:19-12:34 | 129 | n/a | 0 | correctly not charged | 0 / 0 / 0 |
| M12 | main: free endpoints (auth/health, usage_stats, api_search, webhook_result) | various | unknown volume | free | 0 | n/a | 0 / 0 / 0 |
| M13 | main: `cli outreach` 30 Sep | 11:06-14:36 | 370 | 1/1/8 | 1,479 | yes | 0 / 0 / 0 |
| M14 | main: `cobol_poc --vendor apollo` | 16:41-17:19 | 96 | 9 per company | 864 | yes | 0 / 0 / 0 |
| M15 | main: people/match by email (Kuheli) | 11:09:46 | 1 | "(1 credit)" | 1 | **no** | 1 / 1 / 1 |
| **M16** | main: `repair_titles.py`, 59 api_search + 24 people/match | 15:55:43-15:56:27 | 24 | "0-credit search, 1-credit match under a ceiling"; "free match"; later "24 match credits I spent bought nothing" | 0 to 24 | **no** (own counter, no ledger row) | 0 / 0 / **24** |
| M17 | main: calls in flight when processes were killed (mechanism) | 29-30 Sep | up to 12 | n/a | unknown | n/a | 0 / 0 / 12 |
| M18 | main: silent http.py retries (mechanism) | 29-30 Sep | up to 15 | n/a | unknown | n/a | 0 / 0 / 15 |
| M19 | main: Email-bucket model risk (excluded from totals) | all | up to 1,338 | not modelled | unknown | n/a | 0 / 0 / up to 1,338 |
| M20 | main: user-requested direct-dial probes x2 | 3 Oct 22:52-22:53 | 2 | "Cost 9" each | 2 to 18 | yes, by hand | 0 / 0 / 0 |
| M21 | main: everything else, incl. 1-2 Oct (priced ~5,600 to ~32,600, not run) | | 0 | | 0 | n/a | 0 / 0 / 0 |
| S01-S81 | 81 classification subagents | see CSV | 0 | none | 0 | n/a | 0 / 0 / 0 |

Details on the untracked rows:

* **M05, the false "tracked" claim.** In one turn the assistant ran `bulk_enrich` (1 credit), then a second command that wrote one ledger row for the first call and, in the same command, fired `people/match` with `reveal_phone_number=true` (request_id issued, HTTP 200). It told the user "I charged those checks to the ledger ... 2,606 / 10,000". Only ledger id 890 (org_enrich, 1 credit) exists; ledger has nothing else between 06:30 and 06:36 UTC. The user had only asked "check once"; the 9-credit reveal was the assistant's own addition. Same event as the first audit's EST 9.
* **M16, `repair_titles.py`.** Written by the assistant into the session scratchpad (main line 33153), it imports nothing from `leadgen.costs`, calls Apollo through its own `urllib` helper and keeps a private `spent` counter (printed "credits spent: 24"). The ledger has **no rows at all** between 09:06:27 and 11:11:44 UTC on 30 Sep. All 24 results carry first-name-only names and no LinkedIn or email (the "LI=-, em=-" listing), so every match returned no person; that is why central is 0. If Apollo bills every HTTP 200 (the ledger's own rule) the miss is 24. The assistant's wording moved from "Apollo's free match" (10:27 UTC) to "the 24 match credits I spent bought nothing usable" (10:35 UTC), so even it did not know which.
* **M06, M09, M10, the bogus-name probes.** `_apollo_has_credits()` posts a `people/match` for "Zzq Zzq @ example-invalid-probe.test" at every outreach start (added 29 Sep 12:37:07 IST, main line 27130). The code comment says "costs nothing when the account is empty, and at most one credit when it is funded". 29 Sep: 19 preflight lines from 12:37, of which the 12:37:31 one answered 422 (free) and 18 could reach a funded account. 30 Sep: 11 starts, each followed within 2 s by an "apollo budget" line, so all 11 probed. The two manual probes at 12:45 and 12:52 are the same call. Whether Apollo bills a no-match is unverified here, hence 0 central, 31 high.
* **M07, M08, M15** are small, certain-to-have-run calls with no ledger row; the assistant's quoted cost was right but nothing recorded it. They sum to 5 (central).

## 4. Ledger versus log, per day (steps d, e)

The repo logs only failures and run-summary lines, not every call, so a call count per day cannot be read from the logs. What can be done is an exact tie-out for windows where every process in the window printed its summary line: `apollo_used + apollo_reveal` (match attempts) and `apollo_reveal` (reveal attempts) versus ledger `people_match` and `phone_reveal` rows.

| Window (IST) | Log summaries | Log match calls / reveals | Ledger match rows / reveal rows | Result |
|---|---|---|---|---|
| 29 Sep 15:49-16:01 | 16:00:00 (49 + 25), 16:01:28 (66 + 30) | 170 / 55 | 170 / 55 | exact |
| 29 Sep 16:14-16:35 | 16:20:51 (38 + 22), 16:30:25 (46 + 25), 16:35:31 (28 + 17) | 176 / 64 | 176 / 64 | exact |
| 30 Sep 11:04-11:11 | 11:11:14 (19 + 10) | 29 / 10 | 29 / 10 | exact |
| 30 Sep 13:17-13:20 | 13:20:25 (9 + 9) | 18 / 9 | 18 / 9 | exact |
| 30 Sep 11:26-11:48 | 11:37:45 (45 + 15), 11:48:32 (32 + 19) | 111 / 34 | 125 / 41 | ledger higher: a run killed and relaunched at 11:28 never printed a summary |

So where the logs and ledger can be compared, the ledger is never lower than the logs. A ledger gap would show as logs higher than ledger; none exists.

| Day (IST) | Ledger | Ledger calls (HTTP) | Refused 422 in logs (not billed) | Untracked (low / central / high) | Notes |
|---|---:|---|---:|---|---|
| 29 Sep | 3,911 | 164 bulk_enrich (1,447 domains) + 896 people/match (196 with reveal) | 124 match + 5 bulk | 3 / 13 / 33 (M05 1/9/9, M06 0/0/2, M07 0/2/2, M08 2/2/2, M09 0/0/18) | log "apollo credits this month" 5,905 less 28 Sep 1,994 = 3,911, same as ledger |
| 30 Sep | 2,343 | 26 bulk (111) + 440 people/match (224 with reveal) | 0 | 1 / 1 / 36 (M15 1/1/1, M10 0/0/11, M16 0/0/24) | no 422, no 5xx, no lock errors |
| 3 Oct | 18 | 2 people/match with reveal | 0 | 0 / 0 / 0 | hand-written rows, stamped in local IST (the DB import mislabels them as UTC); true bill 2 to 18 |
| 1, 2, 4 Oct | 0 | none | 0 | 0 / 0 / 0 | no Apollo command in the chat |

Per-day untracked figures above exclude M17 and M18 (kept separate because they cannot be dated).

Why the ledger is complete for code paths: `apollo_match()` and `enrich_headcount()` call `costs.charge()` immediately after any HTTP < 400 response, in autocommit, and nothing between response and charge can fail (no 5xx or "database is locked" appears in logs or tool results). `phone_reveal` is charged when a `request_id` is returned and no mobile came back synchronously; all 422 `phone_reveal` rows sit directly after a same-person `people_match` row and no unpaired reveal-style second match (same person, within 12 s) exists, so no reveal call appears to have gone unpriced. The only structural leaks are (a) a process killed between response and charge (M17), (b) retried-after-timeout calls (M18), and (c) anything that does not go through `costs.charge()` (M05 to M08, M15, M16).

## 5. False beliefs about cost (step c)

| Belief | Where | Reality |
|---|---|---|
| "I charged those checks to the ledger" | main line 26407 | false; 9 of the 10 credits of that turn were not recorded (M05) |
| "Apollo's free match found them" then "the 24 match credits I spent" | main lines 33166, 33172 | a `people/match` is never free; here it also returned nobody, and nothing was recorded (M16) |
| "Spend this round: 5 SignalHire credits ... Apollo untouched since it was empty" | main line 27393 | a billable-by-design probe call was made 55 s earlier (M06) |
| Bogus-name `people/match` "costs nothing when empty, at most one credit when funded" | outreach.py 362-380 | up to 29 startup probes plus 2 manual ones, never ledgered (M06, M09, M10) |
| "mixed_companies/search bills 1 credit per page" | main line 27410 | believed 1, ran twice, not recorded (M07) |
| "I deliberately probed someone we'd already revealed so the test bought no new information" | main line 34631 | a re-reveal is still a billable request; 18 recorded (M20, tracked) |
| `vendors_disabled` protects Apollo | config.yaml | true only after the 30 Sep 05:41 UTC `vendor_key()` change for outreach; `repair.py` and `backfill_linkedin.py` read the key directly before that. No repair or backfill ran while Apollo was disabled (29 Sep 17:35 to 30 Sep 11:03 IST), so no leak; the same mistake cost 433 SignalHire credits |

No subagent held any cost belief; none touched a paid API.

## 6. What this changes versus the first audit

* Measured 6,272 stands; no row added or removed.
* Estimated 14 stands as the central untracked figure (9 + 2 + 2 + 1), consistent with the first audit's ~14.
* New: `repair_titles.py` (0 to 24), START-PROBES recounted (29 not 31), explicit mechanisms (27) and the Email-bucket risk (1,338 max).
* First-audit item "12:45 and 12:52 funded probes 0 to 2" kept (M06).
* Not found: any Apollo call by a subagent, any call path other than `urllib`/`httpx` snippets in the main agent's Bash, `cli outreach/repair/backfill/cobol_poc`, and `repair_titles.py`. Scripts in `opsdata/`, `*_discovery/`, `apollo_proptech/`, `apollo_test/`, `pipeline/` were not run in the window (no command in the chat, no file modified).

## 7. Limitations

* Apollo returns no per-call billing data and the repo saved no Apollo payloads (`contacts` table empty), so central/low/high for no-match billing rest on documented Apollo behaviour, not on a measured balance delta.
* API call volume for free `api_search` and `webhook_result` is not logged anywhere.
* M17 and M18 are bounded by assumption, not observed.
* The ledger could over-count (reveals charged 8 although no number came back; `bulk_enrich` charged per submitted domain). That offsets, in unknown size, any untracked spend above.

* `endpoint_billing_table.md` (sibling audit) lists no-match `people/match` billing as unresolved (docs say 0, one HubSpot-repo probe billed 1 without proof it matched) and records a measured reveal billing even with no number on 3 Oct, which makes the 18 credits for M20 more likely right than over-counted. M16, M09, M10 and M06 therefore stay 0 central, 24 / 29 / 2 high until a balance delta settles it.

## 8. Evidence index

Raw session `chat_context/raw/companyOps/7bb0eacd-76f8-4007-b5de-d906ec869ddc.jsonl`: calls and results at lines 25669, 26401, 26404 (+26407 claim), 26985, 27330, 27406, 27411, 27416, 27468, 31325, 33153/33157/33167/33172, 33813-33912, 34603-34631; edits adding the probe at 27130-27133. Repo: `legacy/companyOps/tam/leadgen/{outreach,repair,backfill_linkedin,cobol_poc,costs,http}.py`, `tam/logs/run_2026-09-29.jsonl`, `run_2026-09-30.jsonl`, `tam/data/tam.sqlite3` (`cost_ledger`, opened `mode=ro&immutable=1`). Subagents: `.../subagents/agent-*.jsonl`.
