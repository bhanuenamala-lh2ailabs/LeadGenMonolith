# Agent-wise Apollo credit audit - RapidActionTeam (RAT) Claude Code session

Scope: the one Claude Code session in `/Users/bhanu/Desktop/RapidActionTeam` (session `f5956ec3-d7ff-4cf0-9d76-d2d7c4b901bc`, 25 Sep 11:30Z to 1 Oct 14:05Z, 3,551 raw events). Every Apollo.io call the agent made, directly or through scripts it wrote or ran, and whether its credits were tracked. All times IST. No key or token values are quoted. No network calls were made. `legacy/` and `db/` were not modified.

Companion file: `rat.csv` (16 rows, columns as specified). It complements `docs/audit/apollo/rat.md`, which is the day-by-day view. Event numbers (`event N`) are 0-based line indexes in the raw jsonl, the same numbering `rat.md` uses (the 11,101 reading is event 397).

## 1. Headline answers

1. **Agents and tools.** One main agent. The `Agent` tool was used 0 times, so there were no subagents. Tool counts: Bash 240, Read 98, Edit 18, WebSearch 13, Write 9, Monitor 5, ToolSearch 4, TaskStop 3, AskUserQuestion 1. Every Apollo call came from Bash (inline python or the repo scripts).
2. **Apollo-touching activity exists only in 4 clusters.** 28 Sep 09:10-13:11 (probe, smoke test, 4 discovery runs, a phone backfill, 14 enrichment waves), 29 Sep 10:34 (two balance reads plus four dead URLs), and 1 Oct 14:15-14:20 (five `people/match` and two `api_search` for one person). Nothing else, including 25-27 Sep, 28 Sep after 13:11, 30 Sep, and 1 Oct apart from the 14:15-14:20 cluster (section 6).
3. **Org-lookup exposure if `organizations/enrich` is billed at 1 credit per call** (the RAT code treats it as free; never measured for this endpoint):

   | scenario | org-lookup calls (= credits) | share of the 10,622 fall |
   |---|---|---|
   | free (RAT belief) | 0 | 0% |
   | **low** | **764** | 7.2% |
   | **central** | **1,041** | 9.8% |
   | **high, balance-capped** | **about 1,254** | 11.8% |
   | high, raw (uncapped assumptions) | 1,557 | 14.7% |

   The agent told the user on 29 Sep that it made "about 353" free lookups. The real count is 2.2 to 4.4 times higher because the 353 is the number of rows in the two final candidate CSVs. Four overlapping discovery runs each repeated the same Mumbai/Pune/Bangalore domains with no cache (section 3).
4. **The balance data cannot decide whether org lookups are billed.** They cap it: the cost per lookup is at most about 1.6 to 2.2 credits (section 4). A per-call cost of 0 and of 1 are both consistent.
5. **Of the 10,622-credit fall, the RAT-driven part is about 6% (everything free) to about 16-18% (org lookups billed).** At least 82% is not RAT under every scenario, including the whole 6,965-credit fall from 28 Sep 13:11 to 29 Sep 10:34 (RAT made no Apollo call in that interval).
6. **Belief vs actual.** Yes, the agent believed a call that is very likely billable was free, on weak grounds (section 5). It also left 8 reveal attempts of the 11:06 backfill untracked (72 credits central, range 8-76), and it understated its own untracked calls when it reported them.

## 2. Every Apollo call, by cluster

Credits per call used: `mixed_people/api_search` 0 (free; sibling measured 3 Oct), `people/match` unlock 1, `people/match` with `reveal_phone_number` +8 on top, billed on request even on a miss (sibling measured 3 Oct and 29 Sep), `webhook_result` poll 0, `users/api_profile` 0, `organizations/enrich` 0 believed / 1 if billed (unverified). `docs/audit/apollo/agents/endpoint_billing_table.md` does not exist yet; the above is taken from `hubspot.md` section 7 and `companyops.md`.

| # | Time (IST) | Action | Apollo endpoint, calls | Tracked? | Credits |
|---|---|---|---|---|---|
| 1 | 28 Sep 09:10:07 | org probe `akshaengg.com` (event 391) | `organizations/enrich` x1 | no (balance read 14 s later) | 0 or 1 |
| 2 | 09:10:21-25 | health plus balance read (11,101) | `auth/health`, `api_profile` | n/a | 0 |
| 3 | 09:13:14-09:16:07 | smoke discovery, Pune x 1 keyword, 2 runs | `organizations/enrich` x4-7 (4 domain rows in the CSV) | no | 0 or 4-7 |
| 4 | 09:16:14-27 | smoke enrich, 6 companies | `api_search` x6, `people/match` unlock x2 | yes, before/after: 3 credits | 3 (2 structural, 1 unexplained) |
| 5 | 09:17:08-22 | P0 `nohup` launch, killed after ~14 s | probably none | no | 0-3 |
| 6 | 09:17:28-11:29:52 | P1: full 300-combo run, killed at combo ~71, 366 candidates | `organizations/enrich` x300-684 | no | 0 or 300-684 |
| 7 | 10:49:46-11:36:07 | P2: batch1 first launch, concurrent with P1, killed at 26/45, ~140 candidates | `organizations/enrich` x113-262 | no | 0 or 113-262 |
| 8 | 11:06:05-11:08:44 | Prerna no-phone backfill, 10 contacts | `people/match` reveal x8 (SignalHire had no phone for 8), email-only x<=4, `webhook_result` polls | **no** | 8 / 72 / 76 |
| 9 | 11:36:36-12:37 | P3: batch1 relaunch, 179 candidates (147 with a domain) | `organizations/enrich` x159-275 | no | 0 or 159-275 |
| 10 | 11:44:15-13:10:54 | 14 `enrich_cad_founders.py` waves, 350 companies examined | `api_search` x350 (free), unlock x166, reveal x53, 28 balance reads | **yes**, before/after in each audit JSON: 938 | 938 (590 structural + 348 excess) |
| 11 | 12:38:56-13:09 | P4: batch2, 17 cities x 15 kw, 174 candidates (all with a domain) | `organizations/enrich` x188-325 | no | 0 or 188-325 |
| 12 | 29 Sep 10:34:13-50 | usage forensics | `api_profile` x2, 4 dead URLs (404/404/404/422) | n/a | 0 |
| 13 | 1 Oct 14:15:42-14:20:37 | one named person | `people/match` x5, `api_search` x2 | no | 1 / 3 / 5 |

What did not call Apollo: `discover_cad_supply_leads.py` List A (450) and List B (159) (log: "No Apollo calls made"), `discover_founders_and_push.py` and `icp_enrich_and_push.py` (read on 25 Sep, never executed), `push_all_to_owner.py`, `poll_push_cobol.py`, every `import_*.py`, and all reassign/revert scripts. A grep of every `.py` file finds `api.apollo.io` only in `discover_cad_leads.py`, `enrich_cad_founders.py` and `phone_enrich_apollo_signalhire.py`.

### Wave detail (row 10)

Each wave = one `enrich_cad_founders.py` run. Unlock = identity `people/match` (calls are every examined company that passed the free title search). Structural = unlocks + direct-dial. Excess = credits minus structural.

| wave start | examined | unlocks | reveals (8 cr) | credits | structural | excess |
|---|---|---|---|---|---|---|
| 11:44:15 | 15 | 8 | 1 | 17 | 16 | 1 |
| 11:46:42 | 10 | 2 | 0 | 2 | 2 | 0 |
| 11:52:45 | 12 | 7 | 3 | 37 | 31 | 6 |
| 11:54:07 | 9 | 5 | 2 | 24 | 21 | 3 |
| 12:03:22 | 21 | 6 | 0 | 22 | 6 | 16 |
| 12:07:53 | 8 | 6 | 0 | 14 | 6 | 8 |
| 12:09:57 | 9 | 4 | 0 | 4 | 4 | 0 |
| 12:32:48 | 65 | 28 | 6 | 120 | 76 | 44 |
| 12:37:21 | 30 | 19 | 8 | 109 | 83 | 26 |
| 12:43:33 | 30 | 13 | 10 | 129 | 93 | 36 |
| 12:49:30 | 30 | 19 | 16 | 190 | 147 | 43 |
| 12:55:54 | 40 | 15 | 4 | 103 | 47 | 56 |
| 13:00:57 | 30 | 12 | 1 | 35 | 20 | 15 |
| 13:09:24 | 41 | 22 | 2 | 132 | 38 | 94 |
| **total** | **350** | **166** | **53** | **938** | **590** | **348** |


The 11:44:15, 11:46:42 and 12:09:57 waves have excess 0 or 1, so one unlock costs exactly 1 credit, including `reveal_personal_emails=true`. Excess does not correlate with unlocks that returned an email, with free-search skips, or with the number of reveals. It rises over the session (about 15-60 credits/min in waves from 12:03 on). It is therefore either concurrent RAT org lookups (if billed) or other consumers of the shared key.

## 3. Organisation lookups: complete census

Mechanism: `discover_cad_leads.py` `apollo_org_headcount()` is called once for each candidate that passes the ICP check and has a domain, after the new-name dedupe. The dedupe set is per process, and there is no cross-run cache. Retries are only on network exceptions (up to 4), not on HTTP errors.

| run | window | combos done | candidate rows | rows with domain | org lookups low / central / high |
|---|---|---|---|---|---|
| smoke | 09:14:58-09:16:07 | 1 | 6 | 4 | 4 / 6 / 7 |
| P1 full run | 09:17:28-11:29:52 | ~70.5 of 300 | 366 | 278 / 310 / 366 | 300 / 430 / 684 |
| P2 batch1 (1st) | 10:49:46-11:36:07 | 26 of 45 | ~140 | 105 / 115 / 140 | 113 / 159 / 262 |
| P3 batch1 (2nd) | 11:36:36-12:37 | 45 of 45 | 179 | 147 (exact) | 159 / 204 / 275 |
| P4 batch2 | 12:38:56-13:09 | 255 of 255 | 174 | 174 (exact) | 188 / 241 / 325 |
| P0, probe | | | | | 0 / 0 / 3 and 1 / 1 / 1 |
| **total** | | about 400 combos | about 865 | about 710 / 750 / 830 | **764 / 1,041 / 1,557** |

Assumptions (the only soft inputs):
- The CSVs contain only rows whose headcount was inside 11-500, unknown, or had no domain. A lookup that returned a headcount outside 11-500 was dropped before the CSV, so the CSV under-counts calls. I add that drop as a factor on the verified lookups: 1.08 (10% of found records out of band), 1.39 (35%), 1.87 (55%). Verified rows are 71% of domain rows (111 of 147 in P3, 117 of 174 in P4).
- P1 and P2 candidate rows are known from the chat's progress prints (P1 366 at kill, P2 140 at 11:35:21); the domain share is taken from P3 (82% on the same cities) and P4 (100% on other cities): 0.76 / 0.85 / 1.0.
- A lookup that Apollo answered with no organisation may not be billed (sibling `companyops.md` charges per domain submitted, "if Apollo bills only on success, true credits are lower"). 31% of domain rows have unknown headcount, which is why the low case is below the central one.
- Avoidable duplicates: the Mumbai/Pune/Bangalore domains were looked up up to three times (P1, P2, P3). About 310 of the central 1,041 calls are repeats of a domain already looked up in an earlier run.

The sibling `hubspot.md` shows `mixed_companies/search` was "free, verified repeatedly" until measured on 3 Oct at 1 credit per call returning an org (10 calls / 553 orgs = 6 credits). It also shows 44 names paid twice, so repeats are billed there.

## 4. Balance reconstruction, 28 Sep to 29 Sep 10:34

Total fall: 11,101 (28 Sep 09:10:25) to 479 (29 Sep 10:34:50) = **10,622**. Intervals:

| id | interval | balance | delta | minutes | per min | RAT structural | RAT other candidates | non-structural residual |
|---|---|---|---|---|---|---|---|---|
| A | 09:10:25 -> 09:16:14 | 11,101 -> 11,092 | -9 | 5.8 | 1.5 | 0 | smoke org lookups 4-7 | 9 |
| B | 09:16:14 -> 09:16:27 | 11,092 -> 11,089 | -3 | 0.2 | | 2 | 1 unexplained | 1 |
| C | 09:16:27 -> 11:44:15 | 11,089 -> 10,129 | -960 | 147.8 | 6.5 | 0 | P1, P2, P3 start, backfill | 960 |
| D | 11:44:15 -> 13:10:54 | 10,129 -> 7,444 | -2,685 | 86.6 | 31 | 590 | P3 rest, P4 | 2,095 |
| E | 13:10:54 -> 29 Sep 10:34:50 | 7,444 -> 479 | -6,965 | 1,284 | 5.4 | 0 | none | 6,965 |
| | **total** | | **-10,622** | | | **592** | | |

Interval D, period by period (wave start to next wave start, so each period contains one wave):

| from | to | drop | structural | residual | domain rows added by discovery | minutes | residual / min |
|---|---|---|---|---|---|---|---|
| 11:44:15 | 11:46:42 | 26 | 16 | 10 | 8 | 2.5 | 4 |
| 11:46:42 | 11:52:45 | 186 | 2 | 184 | 10 | 6.0 | 30 |
| 11:52:45 | 11:54:07 | 86 | 31 | 55 | 7 | 1.4 | 40 |
| 11:54:07 | 12:03:22 | 220 | 21 | 199 | 17 | 9.2 | 22 |
| 12:03:22 | 12:07:53 | 93 | 6 | 87 | 7 | 4.5 | 19 |
| 12:07:53 | 12:09:57 | 44 | 6 | 38 | 7 | 2.1 | 18 |
| 12:09:57 | 12:32:48 | 353 | 4 | 349 | 53 | 22.8 | 15 |
| 12:32:48 | 12:37:21 | 214 | 76 | 138 | 25 | 4.6 | 30 |
| 12:37:21 | 12:43:33 | 484 | 83 | 401 | about 57 | 6.2 | 65 |
| 12:43:33 | 12:49:30 | 259 | 93 | 166 | 31 | 6.0 | 28 |
| 12:49:30 | 12:55:54 | 254 | 147 | 107 | 40 | 6.4 | 17 |
| 12:55:54 | 13:00:57 | 247 | 47 | 200 | 30 | 5.1 | 40 |
| 13:00:57 | 13:09:24 | 87 | 20 | 67 | 41 | 8.4 | 8 |

### What the numbers say

1. **Interval D contains far more consumption than RAT lookups can explain.** Residual 2,001 credits in 85 minutes (11:44-13:09) against about 333 domain rows. Even at the high lookup count and 1 credit each, RAT lookups cover at most about 28% (central 20%, low 16%) of the residual. In the period 12:37-12:43 the residual is 401 credits in 6.2 minutes, against about 57 lookups. So at least 72% of D's non-structural spend is not RAT, whichever way org lookups are billed.
2. **Interval C is the only place that bounds the unit cost of a lookup.** The whole drop is 960 and RAT lookups plus the backfill in C are about 606 + 72 = 678 (central). Even with zero other consumers, the cost per lookup is at most 960 / 606 = 1.6 (low-count basis 2.2). So multi-credit pricing is ruled out. The raw high case does not fit: 684 + 262 + 23 + 76 = 1,045 exceeds 960. I therefore cap the high case at about 1,254 total (662 in C, 584 later), assuming at least 1.5 credits/min of other consumption (the lowest background seen, in interval A).
3. **Background rate comparison for C** (what is left once RAT's own spend is removed):
   - If org lookups are free: 880 / 148 min = **6.0 credits/min** (backfill 72 removed).
   - Billed, low count: 3.1 credits/min.
   - Billed, central count: 1.9 credits/min.
   - Reference rates with no RAT activity at all: interval E 5.4 credits/min on average; interval E excluding the known sibling bursts (TAM ledger 1,994 credits 17:43-22:09 and UK PropTech 1,033 credits 22:10-22:22) is about 3.1 credits/min.
   - So the free case needs a daytime background about 2x the quiet-night rate; the billed-central case needs one 0.6x the quiet-night rate. Billed-low matches 3.1 exactly. **The data fit "billed with the low lookup count" and "free with a busy daytime key" about equally.** Called ambiguous, not resolved. A single controlled before/after on one `organizations/enrich` call would settle it.
4. **Interval A** (9 credits in 5.8 min with 4-7 lookups) fits both cases equally.
5. **Concurrent users are demonstrated.** `ps` at 11:28 showed three sibling-repo jobs on the same machine (`push_outflo.py --dry-run --new-only`, `maps_discovery.py --run --tier tier2`, `auto_push.py --threshold 10 --poll 30`). The agent itself said (29 Sep 10:35) that unrelated automation may draw from the same key. Burn rates of 65 credits/min at 12:37-12:43 are not explainable by RAT.

### How much of the 10,622 is plausibly RAT-driven

| component | free | billed low | billed central | billed high (capped) |
|---|---|---|---|---|
| structural enrichment (166 + 2 unlocks + 424 direct-dial), tracked | 592 | 592 | 592 | 592 |
| untracked backfill | 72 (8-76) | 72 | 72 | 76 |
| org lookups | 0 | 764 | 1,041 | 1,254 |
| **RAT total** | **664** | **1,428** | **1,705** | **1,922** |
| share of 10,622 | 6.3% | 13.4% | 16.1% | 18.1% |
| share of the 28 Sep 09:10-13:10 drop (3,657) | 18% | 39% | 47% | 53% |

The existing `rat.md` counted 941 as "RAT audited". That number mixes 592 structural credits with 349 in-wave excess credits that the RAT calls themselves cannot explain. Under the free case the 349 belongs to other consumers, so RAT's measured share is 592 (5.6%), not 941 (8.9%).

Not RAT in all scenarios: interval E (6,965), the rest of D (at least 1,500), and most of C.

## 5. Belief versus actual

| claim made | where | verdict |
|---|---|---|
| "Free Apollo org lookup ... confirmed $0 cost in the reference doc" | AskUserQuestion option, 28 Sep 09:06:45 (event 350) | The reference doc (CAD 100 methodology, read in session) tested `mixed_companies/search` and `mixed_people/api_search` with a before/after balance, not `organizations/enrich`. Extended without a measurement. |
| "`organizations/enrich`, confirmed 0-credit in the reference methodology" | `discover_cad_leads.py` docstring, line 176; module docstring line 17 | Same. Never measured on this endpoint. The agent's only RAT check was the 09:10:07 probe, which had no before-reading. |
| "Apollo's free org-lookup ... no credits spent", "this stage spends no Apollo people/SignalHire credits at all, so it is safe to run repeatedly" | assistant 09:11:21 and 09:17:50; docstring | Probably wrong. Sibling ledger bills org enrich at 1 per domain (1,983 credits over 236 rows in the database, per the earlier `rat.md`; not re-queried here), and the user's own spec `TAMbuilds/CADTAMbuild.md` line 683 says "Organization Enrich ... (credits; VERIFY)". Sibling measured `mixed_companies/search` at 1 per non-empty call on 3 Oct, overturning an earlier "confirmed free" claim of the same kind. |
| "re-spends the Apify/Places/Apollo-headcount calls for those 3 cities a second time (modest cost)" | assistant 10:49:44 | The re-run was disclosed as modest. In fact P1 and P2 ran in parallel, and P3 repeated the cities a third time. Roughly 310 central lookups are pure repeats. |
| "no live credits spent yet, just a read" | 11:05:33 | True for the read. The next command (11:06:05) spent: 8 reveal attempts through `apollo_mobile()`, then at most 4 email-only calls. Never recorded. |
| "free `organizations/enrich` headcount lookups (~353 calls total across discovery)" | 29 Sep 10:35 (event 2311) | Understated. The 353 is the final candidate count (179 + 174). Calls were about 765-1,250. |
| "the Prerna phone-backfill script (2 Apollo calls, untracked)" | same | Understated. 8 reveal attempts plus up to 4 email calls, about 10-12 calls and 72 credits central. |
| `mixed_people/api_search` is free | code comments | Correct (sibling measured 3 Oct). 350 + 2 calls, 0 credits. |
| "SignalHire first, Apollo fallback"; identity unlock "doesn't touch the direct-dial pool" | `enrich_cad_founders.py` | Billing: right on the 8-per-reveal arithmetic (424 = 53 x 8). But the unlock is not free: 166 credits, which the agent did report on 29 Sep. |
| Retry loops | `_apollo_call` (3), `_retry_get` (4) | Retry only on network exceptions with backoff. A timeout after Apollo processed a billed request could double-bill; not measurable. |

The agent did not report any wave cost to the user during the session until asked on 29 Sep 10:33 ("out of credits"). It also deleted the smoke-test audit JSON and the smoke CSV at 09:17:05 so the only record of that 3-credit run is the chat.

## 6. 29 Sep onward

Complete scan of every `tool_use` in the raw session after 28 Sep 13:11 IST for any Apollo endpoint, any import of the Apollo helper that calls it, and any background process.

- **29 Sep 10:34:13-50**: two `api_profile` reads (479 remaining, direct_dial_used 35,524, lead_credits_used 1,908, unified 37,440) plus four 404/422 URLs. 0 credits.
- **30 Sep**: `push_all_to_owner.py`, `discover_cad_supply_leads.py`, reassign and revert scripts. No Apollo call (script header and both logs state "No Apollo calls").
- **1 Oct 14:15:42-14:20:37**: five `people/match` (Nvidia name+org; AssemblyAI name+org, email returned; LinkedIn URL; LinkedIn URL with `reveal_phone_number=false`; Nvidia name+org again) and two `api_search` (free). One SignalHire search. Exposure 1 / 3 / 5 credits (one successful unlock; later re-matches of the same person may be re-billed, per the sibling's 44 repeated names). No balance read brackets it.
- **1 Oct 14:24-18:28**: `poll_push_cobol.py` loop every 3 minutes (Monitor), HubSpot only. 0.
- Rest of the repository: no Apollo call site was invoked. The COBOL enrichment CSVs in the RAT folder were produced by sibling-repo processes, not by this session.
- Absence caveat: anything done outside this session (another chat, the Apollo web UI) leaves no trace here.

Conclusion for 29 Sep+: nothing beyond the 10:34 reads and the 1 Oct lookup (0 measured, about 3 central).

## 7. Tool-call permissions

No tool call in this audit was denied. The original RAT session itself had two calls denied by the auto-mode classifier (bulk archive of 10 HubSpot deals and a dry run of `import_rat_cad_tracks.py`); neither touched Apollo.

## 8. Confidence

| claim | confidence |
|---|---|
| Call census (clusters, endpoints, counts of waves, unlocks, reveals) | high |
| 938 tracked credits, split 590 structural + 348 excess | high |
| Backfill made 8 reveal attempts and that it is untracked | high on the calls, medium on credits (72) |
| Org lookups 764 / 1,041 / 1,254 if billed at 1 | medium-low (OOB-drop factor and P1/P2 domain share are assumptions) |
| Whether `organizations/enrich` is billed | unknown; cost per call is bounded at 2 credits at most |
| At least 82% of the 10,622 fall is not RAT | high |
| 1 Oct lookup 1 / 3 / 5 | low-medium |

## 9. Files written

- `/Users/bhanu/Desktop/LeadGenMonolith/docs/audit/apollo/agents/rat.md`
- `/Users/bhanu/Desktop/LeadGenMonolith/docs/audit/apollo/agents/rat.csv`
