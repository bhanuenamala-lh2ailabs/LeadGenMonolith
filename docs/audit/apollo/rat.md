# Apollo credit forensics - RapidActionTeam (RAT) repo, 29 Sep - 4 Oct 2026 (IST)

Scope: every Apollo.io credit that the RAT repo (CAD/BIM/AEC + COBOL lead pipeline) consumed from 2026-09-29 00:00 IST to 2026-10-04, and what it was spent on.
Method: static read of all Apollo call sites in the repo copy, audit/ JSONs and logs, leads/ and enriched CSVs, the full raw Claude session (3,551 events), and `db/leadgen.sqlite` (read-only). No network calls were made. No secrets are quoted.

Companion files (same folder): `rat_ledger.csv` (one row per spend or zero-spend event, with `data_location_now`), `rat_balance_readings.csv` (every Apollo balance reading found).

## 1. Headline answers

1. **RAT's own scripts consumed essentially zero Apollo credits in the window.**
   - Measured: 0 credits.
   - Estimated: about 1 credit, range 0-3. These were five ad-hoc `people/match` lookups for one named person on 1 Oct 14:15-14:21 IST.
   - No enrichment script (`enrich_cad_founders.py`, `icp_enrich_and_push.py`, `discover_founders_and_push.py`, `phone_enrich_apollo_signalhire.py`) was run after 28 Sep 13:11 IST.
   - The 29 Sep-1 Oct pushes (`push_all_to_owner.py`, `poll_push_cobol.py`, reassign/revert scripts) have no Apollo call sites at all. They only import `to_e164_india()` from the phone module.
2. **The "11,101 to 479 in one day" reading is real, but it is a 28 Sep 09:10 IST to 29 Sep 10:34 IST event, almost all of it before the window.** It is not a 29 Sep event.
   - 11,101 at 2026-09-28 09:10:25 IST (chat, api_profile).
   - 479 at 2026-09-29 10:34:50 IST (chat, api_profile).
   - Delta 10,622, of which RAT's own audited `enrich_cad_founders` waves explain 938, plus a 3-credit smoke test (941, about 9%).
   - The rest (9,681) is unattributed. 2,716 of it fell during RAT's own working hours on 28 Sep (09:16-13:11 IST). 6,965 fell between 28 Sep 13:11 IST and 29 Sep 10:34 IST, when RAT ran no Apollo job.
   - How much of that 6,965 fell after 29 Sep 00:00 IST cannot be determined from RAT evidence (somewhere between 0 and 6,965).
3. **The 50,000 top-up (29 Sep) and everything after it is not visible in RAT evidence.**
   - RAT evidence contains no balance reading after 479 (29 Sep 10:34:50 IST).
   - The first post-top-up spend visible anywhere locally is in the sibling TAM `cost_ledger` (not RAT). It starts at 29 Sep 10:54:17 IST and shows 3,911 credits on 29 Sep and 2,343 on 30 Sep.
4. **COBOL leads in the RAT folder were bought by the sibling `hubspot` repo, not by RAT.** Oct 1 phone reveals of 70 leads are in the RAT folder's `india_cobol_ip_*_enriched_*.csv` files.
   - The notes file states 252 credits for the first 12 leads.
   - The rest is an estimate of about 1,200 more, upper-bounded for one process at 900 by `--max-credits 900`.
   - I list these separately and exclude them from RAT totals. They are real account spend in the window, but they belong to the other repo's budget.
5. **The 144 held-pool leads pushed 29-30 Sep cost zero credits at push time.** The phones were bought 31 Aug-2 Sep by the sibling repo: 129 of 158 via SignalHire, 22 Apollo, 5 Apollo+SignalHire.
6. **Cross-check against the user's 42,669:** RAT explains about 0.002% of it. If the user compares the Apollo usage page day by day, the 29 Sep-4 Oct spend should be attributed to the other repos and users, not to RAT. The only "RAT-folder" Apollo spend is the sibling COBOL work on 1 Oct, which can account for at most about 1,500 credits.

## 2. Credit model, verified against the code and measured data

| Action | Code / evidence | Credits |
|---|---|---|
| `mixed_people/api_search` | free discovery (code comments; repo-wide) | 0 |
| `organizations/enrich` | RAT code says "free"; the sibling `cost_ledger` bills `org_enrich` at 1 each (1,983 credits over 236 rows). **Unverified, see gaps.** | 0 or 1 |
| `people/match` by id or LinkedIn URL (identity unlock, optionally `reveal_personal_emails`) | measured on 28 Sep: waves with 0 direct-dial still consumed credits (e.g. wave 12:04: 22 credits, dd 0) | 1 |
| `people/match` with `reveal_phone_number=true` and `webhook_url` | measured: `direct_dial_used` rises in multiples of 8 in every wave (424 = 53 x 8) | 8 on top of the unlock |
| Poll `GET /webhook_result/{request_id}` (up to 4 polls x 4 s) | not billed (assumption, consistent with the audits) | 0 |
| `users/api_profile?include_credit_usage=true` | balance read | 0 |

- **Failed reveals are billed.** In the 12:52 wave, `direct_dial` rose 128 (16 reveals) but only 2 leads ended with an Apollo phone.
- **Retry behaviour (`_apollo_call`, retries=3):** only network exceptions are retried (with backoff). HTTP errors are returned without retry. A timeout after Apollo had already processed a reveal could therefore double-charge. This cannot be measured here and is not seen in the audits.
- **Gating:**
  - `enrich_cad_founders.py` has no dry-run. Every invocation spends. Its caps are `--target` (default 200) and `--max-extra` (default 150). It runs with `--max-extra 0`.
  - `icp_enrich_and_push.py` and `discover_founders_and_push.py` are dry-run by default, `--apply` to spend. The dry run of `discover_founders_and_push.py` still calls `api_search`, which is free. `icp_enrich_and_push.py` has `--target-valid` and `--max-extra`.
  - `push_all_to_owner.py`, `poll_push_cobol.py` and `import_*.py` never call Apollo.
  - `discover_cad_leads.py` calls `organizations/enrich` once per candidate domain. `discover_cad_supply_leads.py` has no Apollo calls (its log says so).

## 3. Day by day (IST)

Notation: **M** = measured (balance delta or audit JSON), **E** = estimated (code or statement), **sib** = sibling repo (hubspot), shown only for context and not counted in RAT totals.

### 2026-09-29 (Tue)

**What ran (Apollo-relevant):**
- 10:34:20-10:34:50 IST: usage forensics, an inline script after the user asked "how many mobile reveals / people lookups ... out of credits".
  - Calls: `users/api_profile` x2, plus probes `usage_transactions`, `usage_transactions/search` and `billing/usage` (404) and `teams/current` (422).
  - Result: 479 remaining, direct_dial_used 35,524, lead credits used 1,908, unified used 37,440.
  - Credits: 0 (read-only).
  - Purpose: user question about the credit drain.
  - Data location: stdout and chat only. The 938/424/514 split is in `chat_context/summaries/RapidActionTeam.md` section 5.
- 14:25:05 IST: reassign of 27 Harsha cold-call deals to Prerna (the chat says 28). HubSpot only, 0 credits. Data: pipeline `2575252183` (portal 247485022), `audit/reassign_harsha_to_prerna_coldcall_20260929T142505.json`.
- Funnel report and mail: no Apollo.

**Account-level, not RAT:** the 6,965-credit fall 7,444 to 479 straddles 00:00 IST (see section 4). The sibling TAM ledger shows spend resuming at 10:54:17 IST after the top-up, 3,911 credits that day (not RAT, not counted).

**RAT credits:** M 0, E 0.

### 2026-09-30 (Wed)

**What ran:**
- 10:49:10 IST: reassign of 61 deals to Prerna.
- 10:51:57 IST: revert of the same 61 deals.
- 11:20:40 IST: `push_all_to_owner.py --apply` of `leads/CAD_held_pool_icp_pass.csv`. 144 rows: 142 new deals plus 2 matched existing, owner Prerna, `lead_category=CAD`. Zero Apollo calls.
  - The phones were enriched 31 Aug-2 Sep outside RAT. Enrich source per row: signalhire 116, apollo 21, apollo+signalhire 5, blank 2.
  - Data location: 142 live CAD deals (`hs_created_at` 30 Sep IST) in pipeline `2575252183`. Deal and contact ids are in `audit/push_all_to_owner_20260930T112040.json`. The database rows are `deal` with `account_id='rat'`.
- 13:27:08 IST: `discover_cad_supply_leads.py --list A`. Apify, Google Maps and website fetch only. The log says "No Apollo calls made". 450 candidates in `leads/CAD_SUPPLY_ListA_candidates_2026-09-30.csv`.
- 14:39:38 IST: reassign of 139 "unexplained" CAD deals from Harsha to Prerna. Those deals were created by a sibling auto_push, not RAT, so their enrichment credits (if any) are sibling spend.

**RAT credits:** M 0, E 0. No balance reading in RAT evidence on this day.
**Not RAT but visible locally:** sibling TAM ledger 2,343 credits.

### 2026-10-01 (Thu)

**What ran:**
- 13:21:19 IST: `push_all_to_owner.py --category COBOL_high --apply`. 11 new deals plus 1 existing. Reverted at 13:23 IST, with 11 deals and 11 contacts archived (`audit/revert_cobol_high_top12_20261001T132334.json`). 0 credits.
- 13:58:21 IST: `discover_cad_supply_leads.py --list B`, 159 candidates. The log says no Apollo calls. 0 credits.
- 14:03:06 IST: push of 25 COBOL leads (24 new, 1 existing). 0 credits.
- 14:15:42-14:20:37 IST, the **only RAT Apollo calls in the window**: five `people/match` calls plus two `mixed_people/api_search` calls (the second scoped to `nvidia.com`). They looked up one named person's email.
  - Calls: `people/match` by name and org (Nvidia, empty), by name and org (AssemblyAI, email returned), by LinkedIn URL (returned), by LinkedIn URL with `reveal_phone_number=false` (returned), and by name and org (Nvidia again, empty). Plus a SignalHire search, which is not Apollo.
  - **E 1** (range 0-3). No balance reading brackets these calls.
  - Data location: not persisted anywhere in the repo. The email was only quoted in chat.
- 14:24-17:36 IST: `poll_push_cobol.py` loop (3-minute cycles, HubSpot only), with 29/31-row re-pushes, a removal catch-up at 14:28 and the 20-deal removal at 17:36. 0 credits. Data: pipeline `2575252183`, state in `audit/_poll_cobol_batch3_state.json`.

**Sibling work whose output landed in the RAT folder (not RAT, sib):**
- `enrich_top50.py` (finished by about 13:18 IST): **252 credits, stated** in `india_cobol_ip_NOTES.md`. 12 leads with Apollo mobiles (`india_cobol_ip_top12_enriched_2026-10-01.csv`).
- `enrich_batch2.py` (about 13:22-13:34 IST): 16 leads with Apollo phones. **E about 336** (range 144-400).
- `enrich_batch3.py --target 25 --max-credits 900` (pid 86739, started 13:41 IST, seen running in the 13:59 IST `ps` output). Final file has 42 Apollo-phone rows. **E about 882**, with the process capped at 900 by its own counter. The file grew later via sibling tail refresh (mtime 17:37), so some rows may come from later sibling batches.
- Sibling `hubspot/` also holds `india_cobol_batch4/5_enriched.csv` and `enrich_batch6.py` (18:30 IST). These are outside this repo's evidence and belong to the sibling audit.

**RAT credits:** M 0, E 1. **Sibling (context):** 252 stated plus about 1,200 estimated.

### 2026-10-02, 03 and 04 (Fri-Sun)

- No RAT audit file, log, CSV, snapshot or chat event after Oct 1 19:35 IST (last snapshot and Gmail token mtime). The repo copy's directory mtime is Oct 4 19:31 because it was copied.
- **RAT credits: M 0, E 0, but "no evidence" is not "no spend".** Any ad-hoc RAT work done outside this one session (another chat, the Apollo UI) leaves no trace in the repo.
- Cross-reference: the database `cost_ledger` has 2 `people_match` and 2 `phone_reveal` rows (18 credits) at 4 Oct 04:23 IST with `source_system='tam'`. They are not RAT: `account_id` is null and no row links to a RAT company.

### 3.1 Per-day totals

| date (IST) | Apollo calls by endpoint (RAT) | credits measured | credits estimated | balance readings that day |
|---|---|---|---|---|
| 2026-09-29 | `users/api_profile` x2 (0 cr), usage probes x4 (404/422, 0 cr), `people/match` 0, `api_search` 0, `organizations/enrich` 0 | 0 | 0 | 479 (10:34:50), direct_dial_used 35,524, unified 37,440 |
| 2026-09-30 | none | 0 | 0 | none |
| 2026-10-01 | `people/match` x5, `mixed_people/api_search` x2, `api_profile` 0 | 0 | 1 (0-3) | none |
| 2026-10-02 | none found | 0 | 0 | none |
| 2026-10-03 | none found | 0 | 0 | none |
| 2026-10-04 | none found | 0 | 0 | none |
| **Window total (RAT)** | | **0** | **1 (0-3)** | |
| Context: sibling COBOL work in RAT folder (not RAT) | `api_search` free, `people/match` unlock and reveal | 0 | 252 stated + about 1,200 est. | none |

## 4. Reconstruction of the 11,101 to 479 episode

The episode started on 28 Sep, before the window. The numbers are exact where they come from audit JSON `apollo_before`/`apollo_after`. Times are IST.

| Interval | Balance | Delta | RAT-audited | Unattributed |
|---|---|---|---|---|
| 28 Sep 09:10:25 reading | 11,101 | | | |
| to 09:16 smoke start | 11,092 | -9 | | 9 |
| smoke test (6 candidates) | 11,092 to 11,089 | -3 | 3 | |
| to wave 1 start 11:44:15 | 10,129 | -960 | | 960 |
| 14 enrichment waves 11:44-13:10 | 10,129 to 7,444 | -2,685 total across waves and gaps | 938 | 1,747 (13 gaps, 30-375 credits each) |
| 28 Sep 13:10:57 to 29 Sep 10:34:50 | 7,444 to 479 | -6,965 | 0 | 6,965 |
| **Total** | 11,101 to 479 | **-10,622** | **941 (8.9%)** | **9,681 (91.1%)** |

The 14 waves' 938 credits split into 424 direct-dial (53 reveals x 8) and 514 identity unlocks. This matches the chat. The 1,544 `direct_dial_used` rise during 28 Sep 09:10-13:11 IST (29,428 to 30,972) is 424 RAT plus 1,120 elsewhere.

Observations that bear on the unattributed part:
- **The `num_credits_remaining` drop (10,622) is larger than the unified counter rise (+6,100).** About 4,500 credits left the "remaining" figure without moving the lead/direct-dial counters the API exposes. The API also shows `num_export_credits_used` = 2, whereas the user's Apollo UI screenshot (quoted in chat) shows Exports 16,365. The API counters do not reflect UI exports. This supports the chat's hypothesis of bulk UI exports, but nothing in RAT evidence proves it.
- **Possible RAT contribution via "free" org enrich (hypothesis, low confidence).** RAT's discovery ran Apollo `organizations/enrich` once per candidate domain during exactly the intervals where credits vanished without RAT audits (about 09:17 to about 11:30 IST: 366+ candidates before it was killed; then batch1 and batch2 until 13:09 IST). The sibling `cost_ledger` bills the same endpoint at 1 credit per call. If it is billed here too, RAT discovery plausibly explains several hundred to a thousand-plus of the 2,716 same-day unattributed credits. The 9 credits between the 11,101 reading and the smoke-test start (during which RAT made about 4 domain lookups) is weakly consistent with that. It is **not provable** from local data. A controlled before/after read around a single `organizations/enrich` call would settle it. This matters for any future RAT discovery runs.
- **Concurrent users:** the sibling TAM ledger shows 1,994 credits spent 28 Sep 17:43-22:09 IST (275 org_search pages, 425 org_enrich, 230 people_match, 133 phone_reveal x 8). The pasted analysis in chat lists UK PropTech (about 1,033 credits) and other runs. They fall inside the 6,965 interval and explain at most about 3,000 of it.

## 5. Evidence inventory (what was mined and what it showed)

- **Raw chat** (`chat_context/raw/RapidActionTeam/f5956ec3-....jsonl`, 25 Sep 11:30Z to 1 Oct 14:05Z). Every Apollo balance reading is listed in `rat_balance_readings.csv`. The readings are 11,101, the 14 audited waves plus the smoke test (before and after), and 479. The final session event is the 1 Oct funnel mail at 19:35 IST.
- **audit/**: 53 files. 14 `enrich_cad_founders_*` (the only ones with Apollo before/after), 16 `import_cad_100_run_*` (HubSpot writes only), plus pushes, reassigns, reverts, dedup/backfill/discovery logs. The 16 files dated 29 Sep or later contain no Apollo field. The only occurrences of the string "apollo" are the two supply-list logs saying "No Apollo calls made".
- **Enrichment CSV columns:**
  - `india_cobol_ip_{top12,batch2,batch3}_enriched`: 12 + 16 + 42 = 70 distinct leads, all `Enrich Source=apollo`, all with phones.
  - `hubspot_CAD_leads_held_pool_2026-09-30.csv`: 158 rows (signalhire 129, apollo 22, apollo+signalhire 5, blank 2). Enriched 31 Aug-2 Sep.
  - `leads/CAD_RAT_Unassigned_Ready42.csv` and the RAT_CAD_Tracks xlsx files (copies of the sibling file, 96 rows source=apollo, 13 google_maps) were enriched 25 Sep or earlier; no in-window spend.
- **snapshots/**: funnel counts only, no credit data.
- **TAMbuilds/CADTAMbuild.md**: spec only ("don't use Apollo yet"), List A/B built with no Apollo.
- **db/leadgen.sqlite**:
  - `cost_ledger` has no row with `account_id='rat'`. Apollo rows (`org_search_page` 275, `org_enrich` 236, `people_match` 1,568, `phone_reveal` 555 = 4,440 credits) are all from the sibling TAM import with null company link.
  - The `deal` table has 743 RAT deals created from 29 Sep IST. 558 of them appear in no `push_all_to_owner` audit (29 Sep 165, 30 Sep 352, 1 Oct 41, including 19 `Publishers`). They were created by sibling auto_push/AshishCluster integrations, so their enrichment credits are not RAT's. The other 185 come from RAT's own pushes (30 Sep: 142 CAD; 1 Oct: COBOL batches).
  - 687 of 744 RAT contact phone rows created since 29 Sep are Indian mobiles.

## 6. Confidence

| Claim | Confidence |
|---|---|
| RAT enrichment scripts made no Apollo call between 28 Sep 13:11 IST and 1 Oct 19:35 IST | High (the chat has no such call; no audit file; code grep) |
| 938 + 3 credits on 28 Sep waves/smoke | High (audit JSON deltas, measured) |
| The 11,101 to 479 numbers and times | High |
| Attribution of the 9,681 unattributed credits | Unknown; org-enrich hypothesis is low |
| 1 Oct Rajpreet lookups cost about 1 (0-3) | Low-medium (not bracketed by a balance read) |
| Sibling COBOL credits (252 stated, about 1,200 est.) | Medium for the 252; low for the estimate |
| Nothing on 2-4 Oct | Low: absence of evidence only |

## 7. Gaps and things that need follow-up

1. **No Apollo balance reading exists in RAT evidence after 479** (29 Sep 10:34:50 IST). The 50,000 top-up time, any later balance, and the 7,800 figure cannot be reconstructed from this repo. Use the Apollo UI usage view, split by API key, user and day.
2. **The split of the 6,965 credits between 28 Sep 13:11 IST and 29 Sep 00:00 IST versus 00:00-10:34 IST is unknowable here.** The sibling TAM ledger has no rows before 28 Sep 17:43 IST or between 28 Sep 22:09 IST and 29 Sep 10:54 IST, so overnight spend (if any) is not explained by any local ledger.
3. **The `.env` file was modified on 29 Sep 12:51 IST (key names only: HubSpot, Apollo, SignalHire, Google Maps, Apify).** If the Apollo key was rotated at the top-up, per-key usage in the Apollo UI will separate RAT's old key from any new key. I did not read values.
4. **Org-enrich billing is unverified** for RAT's endpoint. See 4. It decides whether RAT's 28 Sep discovery contributed hundreds of credits.
5. **Other chats and the Apollo web UI** (the "Exports 16,365" bucket) are invisible here. Any manual export or any RAT work outside this session would not appear.
6. **Sibling COBOL estimates** (336 and 882) use a simple yield assumption (252/12 = 21 credits per delivered lead). Real figures should come from the sibling `hubspot` repo's logs.
7. **Pre-window rows** (28 Sep) are included in `rat_ledger.csv` for context and tagged `[PRE-WINDOW ...]` in `notes`. Sibling rows are tagged `[SIBLING ...]` and the unattributed gap rows `[NOT-RAT...]`. Sum only rows with `repo=RapidActionTeam` and `date_ist >= 2026-09-29` to get the RAT window total (0 measured, 1 estimated).

## 8. Files written

- `/Users/bhanu/Desktop/LeadGenMonolith/docs/audit/apollo/rat.md` (this file)
- `/Users/bhanu/Desktop/LeadGenMonolith/docs/audit/apollo/rat_ledger.csv`
- `/Users/bhanu/Desktop/LeadGenMonolith/docs/audit/apollo/rat_balance_readings.csv`
