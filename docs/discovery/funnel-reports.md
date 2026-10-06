# Discovery: daily / funnel report generators across the three repos, and the unified report spec

Slug: `funnel-reports`. Written 2026-10-04. Read-only discovery.

Sources read: `legacy/companyOps`, `legacy/RapidActionTeam`, `legacy/hubspot` (verbatim copies).
Live facts below (portal ids, pipelines, stages, owners, deal counts, property lists) were
verified on 2026-10-04 with read-only HubSpot GETs and read-only search POSTs, using the keys in
the consolidated `.env`. No secret value is printed or stored here. No HubSpot writes were made.
Claude chat transcripts were not read.

---

## 0. Headline findings

1. **There are 3 portals, 5 funnels (pipelines), ~15 distinct report generators, and no committed scheduler for any daily funnel mail.**
   GitHub Actions only build and deploy dashboards. The mails are run by hand or by Windows Task Scheduler on a laptop.
2. **Every generator uses the same engine.** It reads each deal's `dealstage` history
   (`propertiesWithHistory=dealstage`), converts each entry's timestamp to an IST calendar day,
   and counts "entries" into a stage set. The same engine has drifted into at least 6 incompatible definitions
   (see section 5). The biggest splits are:
   - human-only (`sourceType == CRM_UI`) versus every source;
   - event counts versus distinct deals ever-reached versus current occupancy;
   - an incremental `cumulative` chain versus recomputing from history;
   - what counts as "leads engaged".
3. **Several reports are stale or broken against today's live portals** (details in section 6):
   - `companyOps/opsdata/daily_fullfunnel_report.py` looks for pipeline label `"Company Ops Data"`.
     That pipeline no longer exists (live labels are "Company Ops Cluster 1 India" and "Company Ops Cluster 2 India"),
     so it raises `StopIteration` on its first call.
     Its `TRACK_EMAILS` also names `kartik.pillai@lh2.ai`, who is an owner in the **RAT** portal, not the CompanyOps portal.
     Active CompanyOps owners Vaishnavi Kannan, Anuj Chahar and Manit Rastogi are not tracked.
   - `companyOps/dashboard/build_ops_dashboard.py` is hard-wired to pipeline `2464812771` (Cluster 2) only. Cluster 1 (1,135 deals) is not on the dashboard.
   - `hubspot/crm_mirror/enrich/full_funnel_report.py` ("whole portal") hard-codes stage ids from the pre-restage Campaign pipeline.
     The live "CoOps ( Global )" pipeline has new stage ids, so it is ignored by that report except for three shared ids.
     Two live Coding stages, `Dropped - Pretraining` and `Call Attempted (retired)`, are also unmapped.
   - `companyOps/opsdata/company_ops_cluster_report_send.py` needs `n8n_cloud_url` / `n8n_cloud_api_key`.
     Those are not in the consolidated `.env`, so it cannot run as-is. It is the legacy n8n-backed twin of the snapshot report.
   - Hard-coded paths: `coopsglobal_seed_backfill.py` and `coopsglobal_backfill_history.py` use `sys.path.insert(0, "/Users/bhanu/Desktop/hubspot/crm_mirror/enrich")`.
4. **No stage-entered-per-stage properties exist** on any of the three custom pipelines (verified via `GET /crm/v3/properties/deals`).
   Only `hs_v2_date_entered_current_stage` exists (RAT's *default* "Deals pipeline" has `hs_v2_date_entered_<stage>`, but that pipeline holds 1 deal and is unused).
   The only universal source of "moved into stage on day D" is `propertiesWithHistory=dealstage`.
   The unified design must store history-derived events locally and must not depend on `hs_v2_date_entered_current_stage` as a filter.
5. **HubSpot holds 0 call objects in these funnels.** All dial activity is inferred from stage moves plus note text.
   Every repo states this caveat. The unified report must keep it.

---

## 1. Live portal / pipeline / owner inventory (verified 2026-10-04)

| Portal id | Repo | Account tz (HubSpot) | Pipelines (id: label, stages, deals) |
|---|---|---|---|
| **246754894** ("MAIN") | `hubspot` | Asia/Calcutta | `default`: **Coding**, 28 stages, 4,821 deals. `2425754306`: **CoOps ( Global )**, 30 stages, 1,380 deals |
| **246897735** ("COMPANYOPS") | `companyOps` | US/Eastern | `default`: **Company Ops Cluster 1 India**, 33 stages, 1,135 deals. `2464812771`: **Company Ops Cluster 2 India**, 33 stages, 6,440 deals |
| **247485022** ("RAT") | `RapidActionTeam` | US/Eastern | `2575252183`: **Rapid Action Team**, 30 stages, 1,236 deals. `default`: "Deals pipeline" (HubSpot stock, 7 stages, 1 deal, unused) |

Note counts (notes/search total): MAIN 5,113, COMPANYOPS 1,849, RAT 561. These are the corpora the note classifier must be regression-tested against.

Owners by portal (id, name, email). Active owners are the people the report can be sliced by.
- **MAIN:** 95472647 Bhanu Enamala, 96573782 Yuktha Anand, 96574824 Lamiya Saleem, 166262056 Shobit Gupta, 166322228 Ishpreet Sood, 168572330 Shagufta Khan, 168572679 Akarsh G B.
- **COMPANYOPS:** 166909452 Anu Meena, 168015618 Tanisha Sharma, 168015679 Amisha Pujari, 168340300 Anuj Chahar, 168341981 Vaishnavi Kannan, 168609107 Manit Rastogi.
- **RAT:** 96316911 Kartik Pillai, 98906502 Harsha A, 166322218 Ashish Ranjan, 168541136 Prerna Jain, 168829625 Rohan Danny Machado.

Stage history identifies the actor by HubSpot **user id** (`updatedByUserId`), not owner id.
All scripts bridge through `GET /crm/v3/owners` (`userId` to `id`). In every portal today the two ids are numerically equal, but the bridge must stay.

### 1.1 Live stage lists (ordered)

**Coding (MAIN default):** Cold Call, No Pickup, Interested, GMeet Fixed, Script Shared, Script Results Received, Commercial Negotiation, LOI, Deal Contract Signed, Data Migration Done, Metadata Matched, Payment Initiation, **Closed/Won**, then dead (all `isClosed=true`):
Dead/ColdCall/Not Interested, Dead/ColdCall/WrongFit, Dead/ColdCall/WrongNumber, Dead/ColdCall/NoPickup, Dead/Interested/NoShow, Dead/GMeet/NoShow, Dead/GMeet/Cancelled, Dead/GMeet/wrong fit, Dead/GMeet/Privacy Concerns, Dead/ScriptShared/NoShow, Dead/ResultsReceived/WrongFit-Rejected, Dead/Negotiation/Pricing, Dead/Negotiation/Contractual, **Dropped - Pretraining**, **Call Attempted (retired)**.

**CoOps ( Global ) (MAIN 2425754306):** Cold Lead, Communicated, No Response, Interested, VC Fixed, VC Rescheduled, 1 Pager Shared, 1 Pager Output Received, Commercial Negotiation, LOI Signed, Sample Extraction, Demand Fulfillment, Contract Signed, Data Extraction Done, **Closed/Won**, then dead: Communicated / Invalid Contact, Communicated / Not Interested, Communicated / Wrong Fit, Communicated / No Response, Interested / Not Interested, VC / No Show, VC / Rejected by LH2, VC / Not Interested, 1 Pager / No Response, 1 Pager Output / Low Quality, Negotiation / Pricing Not Agreed, Negotiation / Contractual Not Agreed, Sample Extraction / Failed, Sample Extraction / Rejected by LH2, Demand Fulfillment / Requirements Not Met.
Shared ids with the old Campaign pipeline: Interested `4018854633`, Commercial Negotiation `4018854637`, Closed/Won `4018854642`.

**Company Ops Cluster 1 and Cluster 2 (COMPANYOPS; identical labels, different stage ids per pipeline):** LinkedIn sent, Cold called assigned, LinkedIn connected, No pickup, Retired: Callback +1 day (use No pickup + task instead), Replied, 1st interest sent, 1st interest follow up, Discovery call, Call rescheduled, One pager requested, One pager follow up, One pager received, LOI signed, Contract signed, Ops data handover done, Payment initiation, **Closed/Won**, then dead: Cold Call / Wrong Fit, / Wrong Number, / Not Interested, / No Pickup; Replied / Not Interested; 1st Interest / No Response, / Not Interested; Discovery Call / No Show, / Rejected by LH2, / Not Interested; One Pager / Not Received, / Low Data Quality; LOI / Pricing Not Agreed, / Contractual Not Agreed; Email Campaign / Branch Retired.
Two LinkedIn stage ids (`4080987861` LinkedIn sent, `4132224744` LinkedIn connected) were deleted in the v5 restructure. They still appear in history of ~3,000 deals, and `build_ops_dashboard.py` injects them via `DELETED_STAGE_LABEL`.

**Rapid Action Team (RAT 2575252183):** Cold Called Assigned, No Pickup, Callback, Replied, 1st Interest Sent, 1st Interest Follow Up, Discovery Call, Call Rescheduled, Sample Requested, Sample Follow Up, Sample Received, Negotiation, Contract Signed, Ops Data Handover Done, Payment Initiation, **Closed / Won** (note the spaces), then 14 dead: Dead: ColdCall/{WrongFit, WrongNumber, NotInterested, NoPickup}, Replied/NotInterested, 1stInterest/{NoResponse, NotInterested}, DiscoveryCall/{RejectedByLH2, NotInterested, NoShow}, Sample/{NotReceived, LowDataQuality}, Negotiation/{PricingNotAgreed, ContractualNotAgreed}.

Property facts (custom, for compliance / value sections): CompanyOps has `gmeet_link`, `gmeet1_link`, `one_pager_results`, `cost`, `lead_source`, `replied_at`, `loi_signed_date`, `one_pager_sent_date`, `onepager_received_at`, `token_paid_at`, `payment_initiated_at`. MAIN has `lead_source`, `scraped_type`, `cost`, `gmeet1_link`, `script_link`, `script_output_link`, `script_status`. RAT has `lead_category`, `cost`, `discovery_call_date`, `discovery_call_link`, `sample_requested_date`, `sample_received_date`, `contract_signed_date`, `data_handover_date`.

---

## 2. Inventory of report generators

Legend: **Status** = LIVE (runs against current portal), STALE (will fail or silently mis-count), LEGACY (superseded).
"Day" is always the IST calendar day unless stated. All use stdlib HTTP, no SDK.

### 2.1 companyOps repo (portal 246897735)

#### C1. `opsdata/full_funnel_report.py`: snapshot writer. LIVE
- **Writes** `crm_mirror/data/snapshots/full_funnel_{cluster1|cluster2}_{YYYY-MM-DD}.json`, one per cluster per day.
- **Clusters:** `("cluster1","default")`, `("cluster2","2464812771")`.
- **Candidate pull:** `POST deals/search` with `pipeline = X AND hs_lastmodifieddate >= "{today}T00:00:00Z"`.
  Note this is **UTC midnight**, so edits between 00:00 and 05:30 IST are not candidates.
  Then narrow to deals whose `hs_v2_date_entered_current_stage` falls on today's IST date.
  Then `GET deals/{id}?propertiesWithHistory=dealstage` per candidate.
  This is only valid for a window ending today (a later move overwrites the property).
- **"Moved into stage today":** a history entry whose timestamp's IST date equals today.
- **Rows** (`DASHBOARD_ROWS`, 14 + Dead). Each is `(row, {stage labels}, any_source)`:

  | Row | Stages | any_source |
  |---|---|---|
  | Leads Assigned | Cold called assigned | **True (entry row)** |
  | LinkedIn Connected | LinkedIn connected | |
  | No Pickup / Callback | No pickup, Retired: Callback +1 day | |
  | Replied | Replied | |
  | 1st Interest | 1st interest sent, 1st interest follow up | |
  | Discovery Call Scheduled | Discovery call, Call rescheduled | |
  | One Pager Requested | One pager requested | |
  | One Pager Follow Up | One pager follow up | |
  | One Pager Received | One pager received | |
  | LOI Signed | LOI signed | |
  | Contract Signed | Contract signed | |
  | Ops Data Handover | Ops data handover done | |
  | Payment Initiation | Payment initiation | |
  | Closed/Won | Closed/Won | |
  | Dead | any label starting `Dead` | |

  `LinkedIn sent` is **not a row**, even though it is an entry stage (Cluster 2 holds 2,015 deals there).
- **Counting:** a history entry increments its row if the row is `any_source` or `sourceType == CRM_UI`.
  `engaged` is a **separate** boolean, true only if the entry is `CRM_UI` (any stage, entry row included).
  This keeps API bulk pushes out of "engaged".
- **Snapshot JSON:** `{date, dashboard_flow{row:int}, current_state{stage_label:int}, cumulative{row:int}, engaged_deal_ids[...], bootstrap:bool}`.
  - `current_state`: live `total` per stage via `search` with `total:true`.
  - `cumulative` = yesterday's `cumulative` + today's flow.
  - **Day-1 bootstrap:** entry row = all deals in pipeline, Dead = live dead count, Closed/Won = live count, all other rows = *today's live occupancy* (an approximation).
- **Reality check:** 25 daily files per cluster (2026-09-09 to 2026-10-03, 50 files).
  Four per cluster carry `bootstrap:true` (09-26, 09-27, 09-28, 10-02; written by `full_funnel_backfill_day.py`); 09-26 and 09-27 are zero-flow backfills.
  The 2026-10-03 files hold `cumulative` Leads Assigned 6,440 (C2) and 1,135 (C1), equal to the live total deal counts, because the chain was re-based on bootstrap days (an entry-row event count and a deal count coincide only if every deal entered the funnel via a logged stage change).

#### C2. `opsdata/full_funnel_dashboard_mail.py`: HTML mail from snapshots. LIVE
- Reads today + 13 prior snapshots, **zero HubSpot calls**. If today's snapshot is missing it prints "no snapshot for today" per cluster.
- **Top strip:**
  - WON TO DATE = `cumulative["Closed/Won"]`, with "+N this week" vs the snapshot 7 days ago.
  - 7-DAY LEADS ENGAGED = size of the **set union** of `engaged_deal_ids` over the last 7 daily snapshots (partial window allowed, labelled "[n of 7 days]").
  - DAILY LEADS ENGAGED = `len(today.engaged_deal_ids)`, with a chip vs yesterday.
- **Table columns:** Inception (cumulative), Roll7 (sum of `dashboard_flow`), Prev7 (days 8 to 14), Change, Today, Yest, Change. Changes use `pct_change` (prev 0/None gives "flat" or +100%).
- **Recipient:** default `--to bhanu.enamala@lh2.ai`. **Subject:** "Company Ops Cluster 1 & 2 India - Daily Report - {Mon DD, YYYY}".
- **Transport:** Gmail API directly with `bhanu_gmail_token.json` (gmail.send scope). Without `--send` it is a dry run.
- Order of operation: run C1 first, then C2.

#### C3. `opsdata/company_ops_cluster_report_send.py`: legacy n8n-backed twin. LEGACY (not runnable)
- Same mail, same live pull (looks pipelines up **by label**), but persists daily JSON in an **n8n Cloud Data Table** (`cieuZhQTdDCB2sgP`) and needs `n8n_cloud_url` / `n8n_cloud_api_key`.
- Its own row set (`CO_DASHBOARD_ROWS`) differs from C1: "Cold Lead Assigned" **and** "Outreach Sent" both list `Cold called assigned` (double-counted);
  "Outreach Sent" also includes `LinkedIn sent`; the three One Pager stages are merged into one "One Pager" row; there is no LinkedIn Connected / Handover split.
- Roll7/Prev7 uses `all(...)` (blank if any day is missing).
- Sends to `bhanu.enamala@lh2.ai`. Keep as reference only.

#### C4. `opsdata/cluster_cutoff_report.py`: reconstruct from full history with a cluster-start cutoff. LIVE (manual)
- Reads **every deal's full stage history** (6,177 deals took ~10 min; supports `--cache` JSON and thread pool of 10).
- **Cluster membership** = first moment a deal sat in one of the pipeline's *own* stages (NOT `createdate`).
- **Cutoffs:** cluster2 default cutoff `2026-09-15`. Optional `--migration-date`: drop deals that "joined" on that date having been **created before it** (the bulk migration).
- Same row set and counting rules as C1 (entry row any-source; others CRM_UI; engaged = any CRM_UI that day).
- **Inception** = count of *entries* since the cutoff (never decreases; exceeds occupancy by design). Roll7/Prev7 = sums over IST days.
- **Outputs:** `--out-json`, `--out-html`, `--email` (via `send_csv_mail.send`). Reports `drop_reason` counts and `history_failures`.
- This is the **only** report that applies a cohort/cutoff rule; it was created because Cluster 2 was populated by a bulk migration on 2026-09-15 (Bhanu's instruction of 2026-09-30).

#### C5. `opsdata/full_funnel_backfill_day.py` and `full_funnel_backfill_range.py`: gap recovery. LIVE (manual)
- `backfill_day YYYY-MM-DD`: reconstructs one past day for both clusters from full history (`batch/read`, 50 ids per call).
  Writes `bootstrap:true`. Its cumulative is the bootstrap-style approximation (live counts as-of end of that day).
- `backfill_range N`: one history pass (checkpointed to `crm_mirror/data/backfill_checkpoints/clusterX_history.json`), then replays N days.
  Cumulative is a **true event count to date** (`bootstrap:false`), and "Leads Assigned" = deals created by end of day.
- Both compute "as-of end of day" with `T23:59:59+00:00` (**UTC**, not IST), unlike the flow logic.
- Neither applies the Cluster 2 cutoff of C4.

#### C6. `opsdata/daily_fullfunnel_report.py`: per-person KPIs + engaged + notes + compliance. **STALE**
- Ported from the main portal's `daily_fullfunnel_mail.py` following `docs/methodology/daily_funnel_report_methopdology.md`.
- **Window:** today or `--week`. It aborts for any window not ending today.
- **People:** `TRACK_EMAILS` = {kartik.pillai, anu.meena, tanisha.sharma, amisha.pujari}. Stale (see headline 3).
- **Three layers:**
  1. **Stage KPIs** (explicit stage-label sets, non-cascading, CRM_UI only, credited to the actor via `updatedByUserId` to owner):

     | KPI | Stages |
     |---|---|
     | li_sent | LinkedIn sent |
     | li_conn | LinkedIn connected |
     | att | No pickup, Dead: Cold Call / {Wrong Number, Not Interested, No Pickup}, Replied |
     | conn | Dead: Cold Call / Not Interested, Replied |
     | interest_sent | 1st interest sent |
     | disc_fixed | Discovery call |
     | disc_done | Dead: Discovery Call / {No Show, Rejected by LH2, Not Interested}, One pager requested |
     | op_requested | One pager requested |
     | op_received | One pager received |
     | eval_done | Dead: One Pager / Low Data Quality, LOI signed |
     | loi | LOI signed |
     | neg_done | Dead: LOI / {Pricing, Contractual}, Contract signed |
     | contract | Contract signed |
     | handover | Ops data handover done |
     | payment | Payment initiation |
     | won | Closed/Won |

     Wrong Fit is deliberately absent from `att` (screened, never dialled). "Cold called assigned" and the other dead stages are in no KPI.
  2. **Note-only activity:** `ops_note_rules.classify()` (ordered regex, first match wins). `BUCKET_MAP` maps `System note`, `One pager received`, `Interest email sent`, `First email sent`, `Meeting fixed` to `None`.
     A `None` bucket does not add to note-only counts, but the note's deal still joins "engaged". Buckets shown: Chase sent, No pickup, Wrong number, Wrong contact, Not interested, Disqualified, Company too small, Pitching own services, Callback booked, WhatsApp outreach, Already engaged, Invalid lead, No data held, Privacy concern, Sample discussed, Prospect shared contact, Call done, Other.
  3. **Leads engaged** = per owner, the deduplicated *set* of deal ids from (a) a CRM_UI stage move that landed on a METRICS stage and (b) any note by that owner associated to the deal.
     Via-stage = (a); note-only = engaged minus via-stage; Attainment = engaged / `TARGET` (100 per person per day).
     **Gotcha:** `eng[oid].add(did)` is inside the `for k in metrics_for(lab)` loop, so a CRM_UI move into a stage in *no* KPI set (Cold called assigned, Call rescheduled, Dead: Cold Call / Wrong Fit, Dead: Replied / Not Interested, Dead: 1st Interest / *, Dead: One Pager / Not Received, One pager follow up, Retired: Callback) does not count as engaged. The snapshot report C1 and the other repos count any CRM_UI move.
- **Net new:** a deal whose earliest-ever CRM_UI history entry falls in the window and was made by the tracked owner. Reject set subtracted = deals with a stage label ending `Wrong Number` **or** a note bucket in {Wrong number, Wrong contact, Invalid lead}.
- **Compliance (live snapshot, not windowed):** per tracked owner, deals currently at or past Discovery call without `gmeet_link`/`gmeet1_link`, and deals currently at or past One pager received without `one_pager_results`.
- **Output:** inline-styled HTML table e-mail written to `daily_fullfunnel.html` (or `weekly_fullfunnel.html`), with a full generated glossary ("How This Report Is Calculated"). **No mail transport is wired in this script**; it only writes the HTML file.

#### C7. `dashboard/build_ops_dashboard.py` + `docs/OPS_DASHBOARD_METRIC_SPEC.md`: live web dashboard. LIVE (Cluster 2 only)
- Reads **every deal** in pipeline `OPS_PIPELINE_ID` (default `2464812771`), `propertiesWithHistory=dealstage,hubspot_owner_id` per deal, plus batched notes.
- **Outputs** `dashboard/ops_dashboard_data.json` (rows `{id, o, nm, c, r, sl, won, dead, src, dvr, cost, occ[], m{metric:[[IST day, actor owner]...]}, asg[]}`) and `index.html`. Deployed to GitHub Pages by `.github/workflows/deploy_ops_dashboard.yml`.
- **Counting rule is different from C1/C6.** The headline is `countEver` = **distinct deals that have ever entered a qualifying stage**, narrowed by the IST day of the event.
  The muted "N now" is `countNow` (current occupancy). Trend charts use `countEvents` (every entry; de-duplicated only on identical `(stage, person, day)`).
- **Every source counts** (explicit rule: filtering to CRM_UI zeroed the top of the pipeline, "board read 5 where HubSpot read 879").
- **Metrics (`METRICS`, each a set of stages):**

  | Metric | Stages |
  |---|---|
  | outreach | LinkedIn sent, Cold called assigned |
  | outreachLi | LinkedIn sent |
  | outreachColdCall | Cold called assigned |
  | outreachReplied | Replied, 1st interest follow up |
  | vcSetup | Discovery call |
  | onePagerSent | One pager received |
  | loiSigned | LOI signed |
  | contractSigned | Contract signed |
  | dealWon | Closed/Won |

  Derived metric `vcAttended` = `vcSetup` events for deals that never entered `Dead: Discovery Call / No Show`, credited to the *call's* day.
  Note-derived metrics (`interestSent`, `onePagerReceived`) come from `ops_note_rules.METRIC_BUCKETS` (`Interest email sent`, `One pager received`).
- **Actor vs owner:** activity follows the actor (`updatedByUserId`; blank for integration moves); assignment (`asg`) follows the *owner-change history* (`hubspot_owner_id` history), falling back to `createdate`.
- **Depth/ranking:** `_SEQ` and `_DEAD_SEQ` rank how far a deal got; a dead stage inherits the depth it reached. `ALIAS` maps the v2 PUT-renamed labels forward. An unknown stage prints a loud WARNING.
- **Spec drift:** the spec text still describes v4 ("Contract signed is now the closed-won stage"). The code (v5, 2026-09-15) restored `Closed/Won` as `dealWon` and lists 4 stages the board does not chart.
- **Cron** `9,39 4-13 * * *` and `1,11,21,41,51 13 * * *` UTC (09:39 to 19:09 IST every 30 min, plus extra attempts after the 18:30 IST close). Repository secret `HUBSPOT_API_KEY` (portal 246897735).

#### C8. Supporting items in companyOps
- `opsdata/outflo_pull_push.py` + `.github/workflows/outflo-sync.yml` (daily 05:00 UTC = 10:30 IST, 4 odd-minute attempts): OutFlo to HubSpot lead sync. This is *not* a report, but it is why the LinkedIn rows are mostly `INTEGRATION`-sourced, which drives the "every source counts" rule.
- `opsdata/cluster_report_definitions_pdf.py` + `docs/Cluster2_Report_Metric_Definitions.md/.pdf`: the human-facing glossary for the Cluster 1/2 mail. It documents the 15 rows, the five columns, "Inception = entries not deals", "partial windows can mislead (do not read % as performance unless both windows show 7 of 7)", and a UI-reproducibility table (Leads Engaged is **not** reproducible in the HubSpot UI).
- `docs/methodology/company_ops_funnel_report_methodology.md` and `daily_funnel_report_methopdology.md`: the porting guides for C1/C2 and C6.
- `dashboard/ops_note_rules.py`, `check_note_rules.py`: note classifier tuned to 68 notes at the time (the portal now has 1,849).
- Leftover: `daily_fullfunnel.html` (rendered sample), 25 snapshot files per cluster, `backfill_checkpoints/cluster{1,2}_history.json`.

### 2.2 RapidActionTeam repo (portal 247485022)

Everything is a generic port of the main portal's design with only `PIPELINE_LABEL` and `METRICS` customised.

#### R1. `rapidactionteam_full_funnel_report.py` (+ `rapidactionteam_funnel_core.py`): snapshot writer. LIVE
- **Writes** `snapshots/rat_{YYYY-MM-DD}.json`: `{date, flow{stage:int}, current_state{stage:int}, dashboard_flow{row:int}, cumulative{row:int}, engaged_deal_ids[...], seeded?}`.
- **Candidates:** `pipeline = RAT AND hs_lastmodifieddate >= IST-midnight (epoch ms)`. Correct IST midnight, unlike the companyOps and main scripts. Then narrowed on `hs_v2_date_entered_current_stage` to today.
- **Rows** (built from `pipeline_sync.LIVE_STAGES`): one row per live stage except `1st Interest Follow Up` (hidden); `No Pickup` + `Callback` folded into **"No Pickup/Callback"**; `Discovery Call` shown as "Discovery Call Scheduled"; all 14 dead stages as one **Dead** row. The entry stage `Cold Called Assigned` is `any_source`.
- **Counting:** the same engaged-vs-flow split as C1. `flow` (raw per-stage) counts CRM_UI or entry stage.
- **Cumulative:** incremental. The script **refuses to write when the chain is broken** (no snapshot for yesterday) unless `--seed` (full-history baseline) or `--allow-gap`. A same-day re-run backs today's flow out of `cumulative` to avoid double counting.
- **Reality:** 5 snapshots: 2026-09-25, 09-28 (**seeded**), 09-29, 09-30, 10-01. 09-26, 09-27, 10-02 and 10-03 are absent. `engaged_deal_ids` sizes: 101, 172, 147, 118, 89. 10-01 shows Cold Called Assigned flow 59, cumulative 1,117, against 1,236 deals live today.

#### R2. `rapidactionteam_dashboard_mail.py`: HTML mail from snapshots. LIVE
- Same layout as C2 (Inception / Rolling 7 / Daily) with a 3-box strip.
- **Headline box = "SAMPLES RECEIVED TO DATE"** = `cumulative["Sample Received"]` (not Closed/Won), with a "+N this week" subtitle.
- **Roll7 / Prev7:** partial windows allowed, with a notice. `engaged_union` is a set union.
- **Recipients:** `--send --to a@x,b@y` is required. There is no default. It sends via `email_transport.send` (Gmail OAuth, then service account, then SMTP, then local outbox `audit/email_outbox/`).
- If today's snapshot is missing it exits and sends nothing.

#### R3. `rapidactionteam_daily_report.py` + `rapidactionteam_report_core.py`: per-owner activity report. LIVE
- **`collect()`** works like C6: the same candidate narrowing, history CRM_UI only, user to owner map, notes by `hs_timestamp`, `/crm/v4/associations/notes/deals/batch/read`.
- **Window:** `--date D` (must not be in the future) and `--week`. It **aborts if the window does not end today**.
- **People:** *all* owners in the portal (no tracked list).
- **KPIs** (`METRICS`, explicit sets): out = Cold Called Assigned; conn = Replied, Dead: ColdCall/NotInterested; int = 1st Interest Sent, 1st Interest Follow Up, Dead: 1stInterest/{NoResponse, NotInterested}; dc = Discovery Call, Dead: DiscoveryCall/{NoShow, RejectedByLH2, NotInterested}; smp = Sample Requested, Sample Follow Up, Sample Received, Dead: Sample/{NotReceived, LowDataQuality}; neg = Negotiation, Dead: Negotiation/*; won = Contract Signed.
  `Call Rescheduled` and `Callback` are in no KPI by design. There is **no `att` (attempted) KPI**.
- **Engaged** includes *any* CRM_UI move (`stage_deals`/`engaged` added before the label check), plus note-associated deals. **Net new** = engaged deals whose earliest CRM_UI touch is in the window, minus deals with a "Bad number" note.
- **Notes:** `rapidactionteam_note_rules.py` is **starter rules only** (8 buckets: Bad number, Not decision maker, Not interested, No pickup, No reply, Discovery call, Sample sent, Interested). The file itself says it must be tuned from real notes. Unclassified notes are listed in an appendix.
- **Output:** plain text + minimal HTML. `--send --to` required; subject "Rapid Action Team -- Daily Activity Report -- {date}".

#### R4. `dashboard/build_ops_dashboard.py` (RAT): live stage-count dashboard. LIVE (thin)
- Writes `dashboard/data.json` with live stage counts, `total_deals`, 7-day note-bucket totals. **No stage-history metrics.** Deployed by `.github/workflows/dashboard.yml` (`0 */6 * * *`, Pages). The workflow writes `.env` from secret `HUBSPOT_API_KEY`.

#### R5. Supporting
- `funnel report setup.md`: the from-scratch build guide (the canonical write-up of the snapshot design): two scripts + one JSON per day; Signal A (engaged, CRM_UI only) kept separate from Signal B (row counts, entry row any-source); incremental cumulative with seed; set-union for 7-day engaged; 8 pitfalls (including stage-id reuse across pipelines).
- `pipeline_sync.py` defines the stage contract (`LIVE_STAGES`, `DEAD_STAGES`), also used by report core. `build.md` is the design spec.

### 2.3 hubspot repo (portal 246754894)

#### H1. `crm_mirror/enrich/daily_fullfunnel_mail.py` (+ `daily_activity_report.py`): the original per-person report. LIVE for Coding, with caveats
- **People (hard-coded):** Ishpreet 166322228, Shobit 166262056, Lamiya 96574824, Yuktha 96573782. Output "LH2 full-funnel daily update", 6:30 pm IST.
- **Window:** `--date` and `--week`. A past `--date` is *allowed* despite the `hs_v2_date_entered_current_stage` caveat, so it can silently under-count (companyOps and RAT abort instead).
- **KPIs (`daily_activity_report.METRICS`, "COPIED VERBATIM" from `lh2-pipeline/dashboard/build_dashboard.py`):**

  | KPI | Stages |
  |---|---|
  | att | No Pickup, Dead/ColdCall/{WrongNumber, Not Interested, NoPickup}, **Interested** |
  | conn | Dead/ColdCall/Not Interested, Interested |
  | gm | GMeet Fixed |
  | vc | Dead/GMeet/{NoShow, wrong fit, Privacy Concerns}, Script Shared |
  | ss | Script Shared |
  | rr | Script Results Received |
  | ev | Dead/ResultsReceived/WrongFit-Rejected, Commercial Negotiation |
  | cn | Commercial Negotiation |
  | neg | Dead/Negotiation/{Pricing, Contractual}, Deal Contract Signed |
  | dcs | Deal Contract Signed |
  | won | Closed/Won |

  `Dead/ColdCall/WrongFit` is not an attempt (never dialled). `Dead/GMeet/Cancelled` is not a "VC done" (no time spent).
  Note: this KPI vocabulary does **not** know "Dead/Interested/NoShow", "Dead/ScriptShared/NoShow", "LOI", "Data Migration Done", "Metadata Matched", "Payment Initiation".
- **Engaged:** every CRM_UI stage move credited to a tracked owner (any stage) + any note association. **Net new** = earliest ever CRM_UI event is in the window and not a reject. Reject = label ends `WrongNumber` OR note is "Bad number" OR matches `WRONG_SPOC` (a shared regex also used by `monthly_wow_report.py`).
- **Notes:** `lh2-pipeline/dashboard/note_rules.py` (imported, never copied; relocated 2026-08-14 so CI can find it).
  `BUCKET_MAP` collapses "Vetted - in/out" to "Lead vetted" and maps Meeting fixed, Script shared, Interested, Script received to `None`.
- **Scope:** `--pipeline <id>` filters candidates and note-deal associations. Without it, it runs on the **whole portal** with Coding vocabulary. CoOps Global moves would then be matched on label (e.g. `Interested` counts as Calls attempted *and* connected).
- **Output:** HTML + text, `crm_mirror/enrich/daily_fullfunnel.html`. `--send --to` via `gmail_sender` (OAuth, service account, SMTP, outbox).

#### H2. `crm_mirror/enrich/coops_fullfunnel_mail.py`: same script with CoOps KPIs. LIVE (CoOps Global)
- Same code as H1, but it **mutates `daily_activity_report.METRICS` in place** (so `metrics_for` picks it up) to: comm = Communicated; int = Interested; vcf = VC Fixed; ops = 1 Pager Shared; opr = 1 Pager Output Received; cn = Commercial Negotiation; loi = LOI Signed; cs = Contract Signed; won = Closed/Won.
  No dead stage is in any set. The stages Sample Extraction, Demand Fulfillment, Data Extraction Done, VC Rescheduled, No Response are in no KPI set.
- **People:** Akarsh 168572679, Shobit 166262056, Yuktha 96573782. `--pipeline 2425754306` is the intended scoping.

#### H3. `crm_mirror/enrich/full_funnel_report.py` + `full_funnel_dashboard_mail.py`: "whole portal" snapshot dashboard. **STALE ids**
- Hard-coded `FLOW_STAGE_IDS` / `TERMINAL_STAGE_IDS` as `[Scraped id, Campaign id]` pairs (12 flow and 14 terminal labels). **No `pipeline` filter** on candidate search; `current_state` counts use `dealstage IN ids` only.
- **Rows** (`DASHBOARD_ROWS`): Leads Assigned (Cold Call, any source), No Pickup, Interested, GMeet Fixed, Script Shared, Script Results Received, Commercial Negotiation, LOI, Deal Contract Signed, Closed/Won, Dead (all terminal labels except Closed/Won). Data Migration Done, Metadata Matched and Payment Initiation are not rows.
- **Snapshot:** `crm_mirror/data/snapshots/full_funnel_{date}.json` = `{date, flow, current_state, cumulative, dashboard_flow, engaged_deal_ids}`. 27 daily files (2026-09-03 to 09-25, 09-28 to 10-01). 09-26/27 and 10-02/03 are missing.
- **Mail:** a 3-strip "Wins to Date / 7-Day Leads Engaged / Daily Leads Engaged" plus the table. Default `--to bhanu.enamala@lh2.ai`.
  Partial windows allowed; the daily % baseline uses `last_nonzero` (walk back to the most recent non-zero day) instead of a straight yesterday comparison.
- `_histories()` fetches histories with 12 worker threads (the sequential version took ~10 min for ~710 candidates).
- **Other snapshot families in the same folder:** `coding_funnel_YYYY-MM-DD.json` (16 files), `full_funnel_owner_{ownerid|unassigned}_YYYY-MM-DD.json` (135 files; `{date, owner_id, owner_name, flow, current_state}` from an earlier per-owner run), `hubspot_2026-07-29.json`, plus 7 `full_funnel_coopsglobal_*.json` files.

#### H4. `crm_mirror/enrich/coding_funnel_report.py`: Coding flow + current-state report (spec of 17 Sep 2026). LIVE (Coding)
- **Scope** by `lead_source IN CODING_SOURCES` (11 sources, e.g. "Apollo Search ( IT Services )", "Outflo Outreach ( IT Services )", "NASSCOM ( IT Services )", "CAD_salesNav"), **not** by pipeline.
- **Two counting rules by stage type:**
  - FLOW stages (12 active): Today = deals that moved *into* the stage today (CRM_UI only, except Cold Call). Roll7 = sum of the daily flow.
  - CURRENT-STATE (14 terminal stages incl. Closed/Won): Today = deals sitting there now. Roll7 = the count *recorded 7 days ago* (a point-in-time read, not a sum).
- **Snapshot:** `coding_funnel_{date}.json` = `{date, flow, current_state}`, 15 contiguous days 2026-09-03..17, then one on 2026-10-01 (10-01 file reflects a later manual run). No `cumulative`, no `engaged_deal_ids`.
- Output: text table, optional `--send --to`.

#### H5. `crm_mirror/enrich/coopsglobal_full_funnel_report.py`, `coopsglobal_dashboard_mail.py`, `coopsglobal_seed_backfill.py`, `coopsglobal_backfill_history.py`: CoOps Global port. LIVE
- Hard-coded `COOPS = "2425754306"`. **Every query filters on `pipeline`** (the docstring notes that three stage ids are shared with the old Campaign pipeline).
- **Rows (12):** Cold Lead (any source), Communicated, No Response, Interested, VC Fixed, 1 Pager Shared, 1 Pager Output Received, Commercial Negotiation, LOI Signed, Contract Signed, Closed/Won, Dead (16 terminal labels).
  `VC Rescheduled`, `Sample Extraction`, `Demand Fulfillment`, `Data Extraction Done` are **not rows**.
- **Snapshot** `full_funnel_coopsglobal_{date}.json`: `{date, flow, current_state, cumulative, dashboard_flow, engaged_deal_ids}`. 7 files (2026-09-23, 24, 25, 28, 29, 30, 10-01).
  On 10-01: Cold Lead flow 36, cumulative 1,265; engaged 22.
- `seed_backfill` sets a true cumulative baseline from full history. `backfill_history` writes every historical day with real activity (Sep 23 onward) in one pass.
- **Mail:** "Company Ops Global Funnel -- {date}", default `--to bhanu.enamala@lh2.ai`. Roll7 sums whatever snapshot days exist.

#### H6. `build_dashboard.py` (root and `lh2-pipeline/dashboard/build_dashboard.py`, near-identical; the latter has more retry logic) + `docs/sop/DASHBOARD_METRIC_SPEC.md`. **STALE for CoOps Global**
- Reads **both** pipelines via `PIPELINES = {"default": "scraped", "2425754306": "campaign"}` and treats them as one: "Scraped and Campaign carry identical stage labels in identical order". **No longer true** since the CoOps Global restage.
- **Eight rules** (spec): count entries not occupancy; IST days; humans only (`CRM_UI`, "roughly 42% of stage-history entries are INTEGRATION"); de-dup per (stage, person, day); a metric is a set of stages; activity follows the actor, assignment follows the owner; both pipelines combined; historical aliases (`Call Attempted` to `No Pickup`).
- Output `dashboard_data.json` (rows with `m{metric: [[day, actor]...]}`, `asg[]`, `won_any[]` for sprint LoC), `index.html`; deployed hourly (`0 * * * *`) to Pages by `.github/workflows/deploy-dashboard.yml`.
- **Role panels** (spec section 0): GTM Analyst (att, conn to gm), Lead Manager/Pod Lead (vc, ss to rr, ev, cn), Lead Closer/Pod Head (neg, dcs, won). `PREFERRED` owner order is hard-coded; others append alphabetically.

#### H7. `crm_mirror/enrich/daily_report.py`: 18:30 IST tracker mail to the Supply Head. LIVE
- Reads `dashboard_data.json` (so mail and dashboard cannot disagree) and **refuses to send if the file is older than the reported day**.
- **Recipient** `shobit.gupta@lh2.ai` (test mailbox `bhanu.enamala@lh2.ai`). Documented schedule: `.github/workflows/daily-report.yml`, `cron: "0 13 * * *"` (13:00 UTC = 18:30 IST).
  **That workflow file is not in this copy of the repo** (only `deploy-dashboard.yml` is). The weekly edition "does not exist yet" per `DASHBOARD_REALTIME_BRIEF.md`.
- **Targets:** Yuktha/Lamiya daily "40 connected calls, at least 5 VCs set up"; Ishpreet weekly "40 VCs attended, 25 scripts shared, 15 script outputs received"; sprint LoC targets (40/60/80/100/120 per week, 27 Jul to 28 Aug).

#### H8. Others in this repo
- `daily_handover_mail.py`: per-caller (Lamiya, Yuktha, Ishpreet) assignment mail + .vcf, with role-based quota text. Not a funnel report.
- `analysis/KPI_DEFINITIONS.md` + `analysis/weekly/weekly_report.py` (+ `_data/kpi_history.json`): weekly **lead-quality** KPIs by source:
  QR = 1 - WrongFit/pushed; CR = 1 - unreachable/pushed; FAR; **ULR** (headline, target >= 80%); ER = Interested+ / contacted (target 25%); MR = GMeet+ / pushed (8%); AFD (mean level 0 to 11); WR; WNR = WrongNumber / dialled (<5% healthy, >15% stop pushing).
  Funnel levels `Cold Call 0 to Closed/Won 11` assume the old Coding vocabulary. Cohorts are by push date and only leads pushed >= 14 days ago count for ER/MR/AFD/WR.
- `ACTIVITY_REPORT_aug6-7.md`, `ACTIVITY_TRACKING_PLAN.md`, `DASHBOARD_REALTIME_BRIEF.md`, `docs/reference/{DAILY_FULLFUNNEL_METHODOLOGY, ROLLING_SNAPSHOT_DASHBOARD_METHODOLOGY, FUNNEL_REPORT_BUILD_FROM_SCRATCH, FUNNEL_DASHBOARD_TOP_STRIP_DECISION, COMPANY_OPS_DATA_DAILY_REPORT_IMPLEMENTATION}.md`: the lineage docs. These explain the design decisions (top-strip choice, gap-recovery procedure, the "Leads Assigned: 200" corruption incident).

### 2.4 Schedules and recipients, consolidated

| What | Scheduler (as committed/documented) | Recipient | Time |
|---|---|---|---|
| CompanyOps dashboard build/deploy | GH Actions cron (UTC) `9,39 4-13 * * *` + `1,11,21,41,51 13 * * *` | web (GH Pages) | 09:39 to 19:09 IST every 30 min, extra after 18:31 IST |
| CompanyOps OutFlo sync | GH Actions `3,17,33,49 5 * * *` | n/a | ~10:30 IST |
| Cluster 1/2 snapshot + mail | **none committed** (manual `--send`) | default `bhanu.enamala@lh2.ai` | written ~18:26 to 20:10 IST |
| RAT dashboard | GH Actions `0 */6 * * *` | web | every 6 h |
| RAT funnel report/mail | **none committed**; the daily report is "the ONLY automated recurring send this repo sanctions" (`email_transport.py`) | `--to` required | "late in the IST day" |
| MAIN dashboard | GH Actions `0 * * * *` | web | hourly |
| MAIN 18:30 tracker mail (H7) | documented `daily-report.yml` `0 13 * * *` (file missing here) | `shobit.gupta@lh2.ai` (test: bhanu) | 18:30 IST |
| MAIN funnel mails (H1/H2/H3/H5) | manual | default `bhanu.enamala@lh2.ai` | "6:30 pm IST" |

Policy that appears in all three repos' docs: **the only sanctioned automated recurring mail is the 18:30 IST daily report**. A 15-minute lead-notifier once fanned out 9 mails covering 1,040 deals. `schtasks` task "LH2 VCF lead notifier" must stay Disabled. The unified mailer must keep that discipline (one scheduled mail; everything else dry-run).

### 2.5 Snapshot/history storage formats (summary)

| Family | Path | Keys | Count |
|---|---|---|---|
| CompanyOps | `companyOps/crm_mirror/data/snapshots/full_funnel_{cluster1,cluster2}_{date}.json` | `date, dashboard_flow, current_state, cumulative, engaged_deal_ids, bootstrap` | 25 per cluster (09-09 to 10-03) |
| CompanyOps history cache | `.../backfill_checkpoints/cluster{1,2}_history.json` | `{deal_id: [history entries]}` | 2 |
| RAT | `RapidActionTeam/snapshots/rat_{date}.json` | `date, flow, current_state, dashboard_flow, cumulative, engaged_deal_ids, seeded?` | 5 |
| MAIN whole-portal | `hubspot/crm_mirror/data/snapshots/full_funnel_{date}.json` | `date, flow, current_state, cumulative, dashboard_flow, engaged_deal_ids` | 27 |
| MAIN Coding | `.../coding_funnel_{date}.json` | `date, flow, current_state` | 16 |
| MAIN CoOps Global | `.../full_funnel_coopsglobal_{date}.json` | same as whole-portal | 7 |
| MAIN per-owner | `.../full_funnel_owner_{id}_{date}.json` | `date, owner_id, owner_name, flow, current_state` | 135 |
| Dashboards | `ops_dashboard_data.json`, `dashboard_data.json`, `dashboard/data.json` | per-deal rows with `m{metric:[[day,actor]]}` | latest only |

Deal-level history is *not* stored anywhere durable except the two `backfill_checkpoints` caches. All other numbers are aggregates, so retroactive re-cuts are impossible from snapshots alone (the cutoff report C4 notes exactly this).

---

## 3. How each mechanic is implemented (cross-repo comparison)

| Mechanic | companyOps (C1/C6/C7) | RAT (R1/R3) | hubspot (H1/H3/H5/H6) |
|---|---|---|---|
| "Moved into stage today" | full dealstage history, entry day (IST) == day. Candidate pre-filter `hs_lastmodifieddate >= {today}T00:00:00Z` (UTC) then `hs_v2_date_entered_current_stage` day == today. C4/C5/C7 use all deals / all history | same, but candidate filter uses IST-midnight ms. Aborts if window not ending today | same as companyOps (UTC midnight prefilter). H1/H2 allow past `--date`. H6 reads all deals' history |
| Human vs automated | C1/C4/C5/C6: CRM_UI only (entry row any-source). **C7: all sources** | CRM_UI only (entry stage any-source) | CRM_UI only (Cold Call any-source in snapshots) |
| Attribution | actor via `updatedByUserId` to owner (C6, C7); C1 has no per-person split | actor | actor (H1, H6); assignment by owner (H6) |
| Day boundary | IST calendar date; portal tz is US/Eastern | IST; portal tz US/Eastern | IST; portal tz Asia/Calcutta |
| Engaged | C1: any CRM_UI move that day (distinct deals). C6: CRM_UI move into a METRICS stage OR note. C7: n/a | any CRM_UI move OR note | any CRM_UI move OR note (H1); any CRM_UI move (H3/H5) |
| Cumulative / inception | C1: incremental (day-1 bootstrap = live counts). C5 range: true events. C4: entries since cutoff. C7: ever-reached distinct deals | incremental with seed; refuses on gap | incremental; seed/backfill scripts for CoOps; whole-portal seeded Sep 3 |
| Roll7 / Prev7 | sum of `dashboard_flow`; engaged = union; partial windows labelled | sum / union; partial windows noted | sum / union (H3/H5 partial); Coding report: flow = sum, terminal = point-in-time |
| Dead stages | one "Dead" row (all `Dead*`) | one "Dead" row | one "Dead" row (snapshots); per-reason KPIs only in H1/H6 sets |
| Notes | `ops_note_rules.py` (corpus 68 notes at the time) | starter 8 rules | `note_rules.py` (from 106 notes) |
| Reject set (net new) | stage label ends "Wrong Number" or buckets Wrong number/contact, Invalid lead | "Bad number" note only | label ends "WrongNumber" or "Bad number" or `WRONG_SPOC` |
| Output | HTML mail (Gmail token); C6 HTML file only | text + HTML via `email_transport`; Pages dashboard | HTML mail via `gmail_sender`; Pages dashboard hourly |

---

## 4. UNIFIED REPORT SPEC

### 4.1 Design principles

1. **One event store, many views.** Pull `dealstage` history once per deal (batch read, 50 ids per call, checkpointed), store *every* entry as a row, and compute all reports with SQL from that store.
   Never use `hs_v2_date_entered_current_stage` as a filter and never chain `cumulative` from yesterday's file. Any past day can then be reported exactly. Incremental sync = deals where `hs_lastmodifieddate >= last_sync - 1 day`, re-reading their history in full.
2. **Key on `(portal_id, pipeline_id, stage_id)`** and map each to a canonical stage. Never use labels alone (labels collide, e.g. `Interested`, `Closed/Won`; ids are reused across pipelines).
3. **Keep source type on every event** (`CRM_UI`, `INTEGRATION`, `IMPORT`, `WORKFLOWS`, `API`, ...). Report both "human" and "automated" columns; do not make the choice silently.
4. **IST calendar day**, computed in code, never from portal timezone.
5. **Three distinct numbers per canonical stage, always labelled:** Entries (events), Deals entered (distinct), Occupancy now.
6. **Unmapped stage = loud failure.** At sync time compare live stages to the mapping; any new/renamed/deleted stage prints a warning and appears in the mail footer (pattern from `build_ops_dashboard.py`).
7. **One note classifier**, superset of the three, regression-tested against the stored notes (1,849 + 561 + 5,113).

### 4.2 Canonical funnel ladder

`Rank` is monotone depth (used for "how far did it get"). `Effort flags` say which cross-portal KPIs a *first entry into* that stage satisfies (non-cascading, as in all three methodology docs).
`A` = attempted contact, `C` = connected (a human at the prospect responded or declined live), `I` = interested.

| Rank | Canonical stage | Coding (MAIN) | CoOps ( Global ) (MAIN) | Cluster 1 / Cluster 2 (COMPANYOPS) | Rapid Action Team (RAT) | Flags |
|---|---|---|---|---|---|---|
| 0 | **Sourced / Assigned** (entry; any source counts) | Cold Call | Cold Lead | Cold called assigned | Cold Called Assigned | none |
| 0b | **Outreach sent: LinkedIn** (channel entry) | n/a | n/a | LinkedIn sent | n/a | none |
| 1 | **Attempted: no answer** (loop) | No Pickup; (retired) Call Attempted | No Response | No pickup; Retired: Callback +1 day | No Pickup; Callback | A |
| 1b | **Connected: LinkedIn** | n/a | n/a | LinkedIn connected | n/a | A |
| 2 | **Connected / Replied** | (no stage; implied by Interested) | Communicated | Replied | Replied | A, C |
| 3 | **Interested / first-interest** | Interested | Interested | 1st interest sent; 1st interest follow up | 1st Interest Sent; 1st Interest Follow Up | A, C, I |
| 4 | **Meeting booked** (discovery call / VC / GMeet) | GMeet Fixed | VC Fixed; VC Rescheduled | Discovery call; Call rescheduled | Discovery Call; Call Rescheduled | A, C, I |
| 5 | **Meeting held** (derived, no stage in any portal) | evidence: reached Script Shared or Dead/GMeet/{wrong fit, Privacy Concerns} | evidence: reached 1 Pager Shared or Dead: VC/{Rejected by LH2, Not Interested} | evidence: reached One pager requested or Dead: Discovery Call/{Rejected by LH2, Not Interested} (no `No Show`) | evidence: reached Sample Requested or Dead: DiscoveryCall/{RejectedByLH2, NotInterested} | |
| 6 | **Asset / sample requested** | Script Shared | 1 Pager Shared | One pager requested; One pager follow up | Sample Requested; Sample Follow Up | |
| 7 | **Asset / sample received** | Script Results Received | 1 Pager Output Received | One pager received | Sample Received | |
| 8 | **Evaluated** (derived: left rank 7 for a post-evaluation outcome) | Dead/ResultsReceived/WrongFit-Rejected or Commercial Negotiation | Dead: 1 Pager Output / Low Quality or Commercial Negotiation | Dead: One Pager / Low Data Quality or LOI signed | Dead: Sample/LowDataQuality or Negotiation | |
| 9 | **Negotiation / LOI** | Commercial Negotiation; LOI | Commercial Negotiation; LOI Signed; Sample Extraction; Demand Fulfillment | LOI signed | Negotiation | |
| 10 | **Contract signed** | Deal Contract Signed | Contract Signed | Contract signed | Contract Signed | |
| 11 | **Delivered / handover** | Data Migration Done; Metadata Matched | Data Extraction Done | Ops data handover done | Ops Data Handover Done | |
| 12 | **Payment initiated** | Payment Initiation | none | Payment initiation | Payment Initiation | |
| 13 | **Won** | Closed/Won | Closed/Won | Closed/Won | Closed / Won | |

Placement notes (decisions to confirm in section 7):
- CoOps Global's order differs: `Contract Signed` follows `LOI Signed`, `Sample Extraction`, `Demand Fulfillment`. Those three sit under rank 9 for reporting and keep their own sub-row.
- CoOps `Communicated` is mapped to rank 2 (connected). If the team uses it for "message sent, no reply yet", it should move to rank 1 (see open question).
- Coding has no `Replied` stage. A move into `Interested` satisfies A, C and I in one event, exactly as the Coding `att`/`conn` KPI sets already encode.

**Canonical dead taxonomy.** Every dead stage maps to `(reason, depth_reached)`. `depth_reached` is the highest canonical rank the deal reached before dying (as `_DEAD_SEQ` already does in the CompanyOps and MAIN builders).

| Canonical reason | Counts as attempt (A) | Counts as connected (C) | Source stages |
|---|---|---|---|
| **Wrong fit (screened)** | no (never dialled), except CoOps Wrong Fit which was already Communicated | no | Coding Dead/ColdCall/WrongFit; Cluster Dead: Cold Call / Wrong Fit; RAT Dead: ColdCall/WrongFit; CoOps Dead: Communicated / Wrong Fit |
| **Bad contact (wrong/invalid number)** | yes (a dial happened) | no | Dead/ColdCall/WrongNumber; Dead: Cold Call / Wrong Number; Dead: ColdCall/WrongNumber; Dead: Communicated / Invalid Contact |
| **No response / unreachable** | yes | no | Dead/ColdCall/NoPickup; Dead: Cold Call / No Pickup; Dead: ColdCall/NoPickup; Dead: Communicated / No Response; Dead: 1st Interest / No Response; Dead: 1stInterest/NoResponse; Dead: 1 Pager / No Response; Dead: One Pager / Not Received; Dead: Sample/NotReceived; Dead/ResultsReceived is *not* here (see quality) |
| **Not interested (declined)** | yes | yes | Dead/ColdCall/Not Interested; Dead: Cold Call / Not Interested; Dead: ColdCall/NotInterested; Dead: Replied / Not Interested; Dead: Replied/NotInterested; Dead: 1st Interest / Not Interested; Dead: 1stInterest/NotInterested; Dead: Interested / Not Interested; Dead: VC / Not Interested; Dead: Discovery Call / Not Interested; Dead: DiscoveryCall/NotInterested |
| **No-show** | yes | yes | Dead/Interested/NoShow; Dead/GMeet/NoShow; Dead/ScriptShared/NoShow; Dead: VC / No Show; Dead: Discovery Call / No Show; Dead: DiscoveryCall/NoShow |
| **Meeting cancelled before held** | yes | yes | Dead/GMeet/Cancelled (no effort spent; excluded from "meetings held") |
| **Rejected by LH2 / quality fail** | yes | yes | Dead: VC / Rejected by LH2; Dead: Discovery Call / Rejected by LH2; Dead: DiscoveryCall/RejectedByLH2; Dead/GMeet/wrong fit; Dead/ResultsReceived/WrongFit-Rejected; Dead: One Pager / Low Data Quality; Dead: Sample/LowDataQuality; Dead: 1 Pager Output / Low Quality; Dead: Sample Extraction / {Failed, Rejected by LH2}; Dead: Demand Fulfillment / Requirements Not Met |
| **Privacy / compliance** | yes | yes | Dead/GMeet/Privacy Concerns |
| **Commercial (price/terms)** | yes | yes | Dead/Negotiation/{Pricing, Contractual}; Dead: LOI / {Pricing Not Agreed, Contractual Not Agreed}; Dead: Negotiation / {Pricing Not Agreed, Contractual Not Agreed}; Dead: Negotiation/{PricingNotAgreed, ContractualNotAgreed} |
| **Retired / administrative** | no | no | Dead: Email Campaign / Branch Retired; Dropped - Pretraining; Call Attempted (retired). Excluded from all effort KPIs and shown under "Retired" |

`Retired: Callback +1 day (use No pickup + task instead)` is a *live* stage in the Cluster pipelines. It maps to rank 1 (Attempted: no answer), as `DASHBOARD_ROWS` already does.

### 4.3 Cross-portal metric set (one definition each)

All "entries" are **first entries per (deal, canonical stage, actor, IST day)**, i.e. the same de-dup the dashboards use (identical triple counts once). Each is shown in two columns: **Human** (`sourceType = CRM_UI`) and **Automated** (everything else), with **Total** = both. `Entry row` counts both columns in "Human + auto" always.

| # | Metric | Definition |
|---|---|---|
| M1 | New leads assigned | Entries into canonical rank 0 (and 0b), any source. Also report **distinct new deals** = deals whose first-ever stage entry is that day |
| M2 | Attempted | Entries into any stage with flag A (ranks 1, 1b, 2, 3, 4 and dead reasons flagged A). Wrong-fit excluded |
| M3 | Connected | Entries into any stage with flag C |
| M4 | Interested | Entries into rank 3 |
| M5 | Meetings booked | Entries into rank 4 |
| M6 | Meetings held | Deals with a rank-4 entry that never entered No-show/Cancelled, credited to the *meeting* day when known (`gmeet1_date`/`discovery_call_date` if populated, else the booking day), plus a separate "No-shows" line. Time-consumed variant (held + no-show) is shown in the footnote |
| M7 | Asset / sample requested | Entries into rank 6 |
| M8 | Asset / sample received | Entries into rank 7 |
| M9 | Evaluations done | Entries into rank 8 outcomes (dead-by-quality or move to rank 9) |
| M10 | Negotiation / LOI | Entries into rank 9 |
| M11 | Contracts signed | Entries into rank 10 |
| M12 | Delivered / handed over | Entries into rank 11 |
| M13 | Payments initiated | Entries into rank 12 |
| M14 | **Won** | Entries into rank 13 (`Closed/Won` only); cumulative "Won to date" |
| M15 | Dead by reason | Entries into each canonical dead reason |
| M16 | Leads engaged | Per person, the deduplicated set of deals with (a) any human stage move or (b) a classified, human-written note, credited to the actor/note owner; "via stage" and "note only" split; union over windows |
| M17 | Net new engaged | Engaged deals whose earliest-ever human event falls in the window, minus the reject set (wrong number / invalid contact / wrong contact / `WRONG_SPOC`) |
| M18 | Occupancy now | Deals currently in each canonical stage (matches HubSpot board columns) |
| M19 | Inception (ever-reached) | Distinct deals that ever entered the canonical stage since the funnel's cohort start |
| M20 | Roll7 / Prev7 / WoW | From the event store: Roll7 = sum over the 7 IST days ending today; Prev7 = the 7 before; engaged = set union. **No missing-day penalty** since the store covers all days |
| M21 | Compliance | Per portal, deals at/past meeting-held without a meeting link (`gmeet_link`/`gmeet1_link` for CompanyOps; `gmeet1_link` for MAIN; `discovery_call_link` for RAT) and at/past rank 7 without results (`one_pager_results` for CompanyOps; `script_output_link` for Coding) |
| M22 | Value | Count and sum of `cost` on Won and Contract-signed deals (see conflict 17); `amount` is not used |
| M23 | Target attainment | Engaged / target per person per day. Default 100 (CompanyOps SOP), overridable per role (GTM 40 connected + 5 meetings booked; Pod lead weekly 40 / 25 / 15) |
| M24 | Quality (weekly only) | Wrong-number rate = Bad contact / dialled; wrong-fit rate = Wrong fit / pushed; ULR as in `KPI_DEFINITIONS.md`, remapped to canonical ranks |

### 4.4 Report layout (one mail, 18:30 IST, plus a day-close finalisation)

1. **Header strip (combined, all five funnels):** Won to date (+N this week), Contracts signed (7 d), 7-day leads engaged (union), Daily leads engaged (+ chip vs yesterday). Each box shows its coverage ("5 of 5 funnels reporting").
2. **Combined funnel table:** rows = canonical stages (M1 to M15 plus Dead split by reason groups), columns = Inception | Roll7 | Prev7 | Change | Today | Yest | Change, with the Human vs Automated sub-columns collapsed to Total by default and a toggle in the HTML view.
3. **Per-portal / per-funnel sections** (same table), in this order:
   - **A. MAIN / Coding** (portal 246754894)
   - **B. MAIN / CoOps ( Global )**
   - **C. COMPANYOPS / Cluster 1 India** (portal 246897735)
   - **D. COMPANYOPS / Cluster 2 India** (with the 2026-09-15 cohort cutoff, see below)
   - **E. RAT / Rapid Action Team** (portal 247485022)
4. **People table** (one table, grouped by funnel): Leads engaged, Via stage, Note only, Net new, KPI events, Attainment.
5. **Note-only activity** (collapsed buckets from the unified classifier).
6. **Compliance** (M21).
7. **Occupancy now** (M18) and **dead breakdown**.
8. **Footer:** data freshness (last sync time per portal), unmapped stages, history read failures, the "no call objects in HubSpot" caveat, the glossary generated from the same mapping tables the numbers use (as in C6).

Subject: `LH2 Funnel — Daily Report — {Mon DD, YYYY} (IST)`. Recipient list is configuration (default: `bhanu.enamala@lh2.ai`; add `shobit.gupta@lh2.ai` once approved). One transport: Gmail OAuth, with an outbox fallback. **Dry-run by default**; `--send` required.

### 4.5 Per-portal rules

- **MAIN / Coding:** scope by `pipeline = default`, not by `lead_source` (H4 scoped by source; the sources list is a *segment*, not the funnel). Keep `lead_source` as a breakdown dimension. Map `Dropped - Pretraining` and `Call Attempted (retired)` to Retired. Alias `Dead/Gmeet/Privacy Concerns`. People: Yuktha, Lamiya, Ishpreet, Shobit, plus Shagufta, Akarsh, Bhanu from the live owner list (active = any event in window).
- **MAIN / CoOps ( Global ):** scope by `pipeline = 2425754306`. The three shared stage ids (`4018854633`, `4018854637`, `4018854642`) are resolved through `(pipeline_id, stage_id)`. Include the four unrowed stages as sub-rows under rank 9/11.
- **COMPANYOPS / Cluster 1:** pipeline `default`, no cutoff, 1,135 deals. Add to the dashboard.
- **COMPANYOPS / Cluster 2:** pipeline `2464812771`, 6,440 deals. Cohort: `cluster_start = 2026-09-15`; exclude deals that joined on that date having been created before it (bulk migration). Make the cohort filter a per-pipeline config (`cohort_start`, `exclude_migration_on`) and show **both** "in-scope" and "all history" inception.
  LinkedIn rows (`LinkedIn sent`, `LinkedIn connected`) are automation-dominated (OutFlo); show them in their own sub-section with a Human/Automated split so the top of funnel is visible.
- **RAT:** pipeline `2575252183` only (the default "Deals pipeline" has 1 deal and is ignored). Map `Closed / Won` (with spaces) by id. People: all five owners. Replace RAT's starter note rules with the unified classifier. Do not use "Samples received" as the headline; it is a rank-7 metric.

### 4.6 Data model (for the unified Postgres)

Table names are suggestions, to be reconciled with the schema-discovery output.
- `crm_portal(portal_id, name, tz_hubspot, ui_domain)`; `crm_pipeline(portal_id, pipeline_id, label)`; `crm_stage(portal_id, pipeline_id, stage_id, label, is_closed, display_order, canonical_stage, canonical_rank, dead_reason, flag_attempt, flag_connected, flag_interested, deleted)`.
- `crm_owner(portal_id, owner_id, user_id, email, name, archived)` and a global `person(email)`. Unified identity is by **email**, not owner id.
- `deal_stage_event(portal_id, deal_id, pipeline_id, stage_id, entered_at_utc, ist_day, source_type, source_id, actor_user_id, actor_owner_id, actor_email)` from `propertiesWithHistory=dealstage`.
  Unique key `(portal_id, deal_id, entered_at_utc, stage_id)`. Also store `hubspot_owner_id` history for assignment metrics.
- `deal_note(portal_id, note_id, deal_id, owner_id, created_utc, ist_day, bucket)` using the unified classifier.
- `deal_current(portal_id, deal_id, pipeline_id, stage_id, owner_id, createdate, lead_source, cost, links...)`.
- `funnel_daily(day, portal_id, pipeline_id, canonical_stage, entries_human, entries_auto, deals_human, deals_all, occupancy_end_of_day)` and `engaged_daily(day, portal_id, pipeline_id, owner_email, deal_id, via)` as materialised views (rebuildable), plus a `snapshot_import` table for the legacy snapshot JSON (kept for audit only).
- **Backfill sources:** full history re-pull replaces all legacy snapshot JSON; the existing checkpoints (`backfill_checkpoints/cluster{1,2}_history.json`) can seed COMPANYOPS without 10 min of API reads. Legacy JSON can be loaded into `snapshot_import` and diffed against the recomputed values as a regression test.

---

## 5. Conflicts and inconsistencies the unified report must resolve

Each item: what conflicts, where, and the recommended resolution.

| # | Conflict | Where | Recommended resolution |
|---|---|---|---|
| 1 | **Human-only vs all sources.** Snapshot/mail reports count only `CRM_UI` (except the entry row). The CompanyOps dashboard counts **every** source and measured CRM_UI moves at 5 vs 879 on the LinkedIn row. | C1/C6/R1/R3/H1/H3/H5/H6 vs C7 | Store the source on every event. Every flow metric shows Human, Automated and Total. "Engaged" and per-person KPIs are human-only. The combined table defaults to Total with the split visible. Never silently drop automation (OutFlo, Gmail import) |
| 2 | **Three different "counts".** Entries (event count) vs distinct deals ever reached vs occupancy; Coding report mixes flow (live stages) and point-in-time (terminal stages, Roll7 = value 7 days ago). | C1 vs C7 vs H4 | Define M18/M19/entries explicitly (4.3), label every column, and compute Roll7 always as a sum of daily entries |
| 3 | **Inception baseline.** Day-1 bootstrap = live occupancy (C1, C5 day); true event count (C5 range, R1 seed, H5 seed); entries since a cutoff (C4); ever-reached distinct (C7). The 10-03 files show Leads Assigned = live total deal count after bootstrap days. | CompanyOps, RAT, hubspot | Recompute inception from the event store for each funnel and cohort definition. Retire the chained `cumulative`. Keep `bootstrap` only in the legacy import table |
| 4 | **Cohort/cutoff applies to only one report.** Cluster 2's 2026-09-15 bulk migration is excluded in C4 only; C1/C7 include all history. | C4 vs C1, C7 | Per-pipeline `cohort_start` and `exclude_migration_on` config; show "in scope" and "all history" |
| 5 | **Candidate pre-filter and window.** `hs_v2_date_entered_current_stage` holds only the latest move, so past windows under-count (all repos know it); the prefilter value `"{today}T00:00:00Z"` is UTC midnight (misses 00:00 to 05:30 IST) in companyOps/main but IST-midnight ms in RAT; H1/H2 allow past dates silently. | all | Event store removes the dependence. Incremental sync uses `hs_lastmodifieddate >= last_sync - 24h` with full history re-read. Any date can be reported |
| 6 | **Day close.** Window = IST calendar day, but the sanctioned mail is 18:30 IST and snapshots were written 18:26 to 20:10 IST; `backfill_*` uses a UTC end of day (`T23:59:59+00:00`) for "as of". | C1, C5, docs | Fixed IST day [00:00, 24:00). Mail at 18:30 is labelled "through 18:30 IST". A final close at 00:05 IST next day rewrites that day. Use IST in all as-of logic |
| 7 | **"Leads engaged" has two definitions.** Snapshot (C1/R1/H3/H5): any CRM_UI stage move (no notes). Per-person (C6/R3/H1): stage OR note. Within C6, only moves landing on a METRICS stage count, undercounting vs everything else (e.g. Wrong Fit, Dead: Replied / Not Interested, Call rescheduled). | all | Engaged = any human stage move OR classified human note. Report split "via stage" and "note only". Fix the C6 gap |
| 8 | **Net new / reject set.** Reject definitions differ: stage suffix + 3 note buckets (C6), stage suffix + `WRONG_SPOC` + Bad number (H1), Bad number only (R3). Note-only deals can never be net new in any script (history only pulled for stage movers). | C6, H1, R3 | One reject set: Bad contact, Wrong contact, Invalid lead (stage or note), shared `WRONG_SPOC` regex. Compute earliest human event for **every** engaged deal from the event store |
| 9 | **Per-person KPI lists.** `Calls attempted`: Coding includes `Interested` and dead stages; Cluster includes `Replied` but not `Dead: Replied / Not Interested`; RAT has no attempted KPI. `Calls connected` likewise. | C6/H1/R3 | Use the canonical flags A and C (4.2). Document each stage's flags in the generated glossary |
| 10 | **"Discovery/VC done" is four things.** Coding `vc` = NoShow + wrong fit + Privacy + Script Shared (time-spent proxy). C6 `disc_done` = NoShow + Rejected + Not Interested + One pager requested. C7 `vcAttended` = booked minus no-show. R3 `dc` = Discovery Call (booked) + dead stages, i.e. "done" = booked. | H1, C6, C7, R3 | Separate Meetings booked (M5), Meetings held (M6, excludes no-show/cancel) and No-shows. Optional footnote "slots consumed" |
| 11 | **Won definition.** Closed/Won (Coding, CoOps, Cluster v5); `Contract signed` = closed-won in C7's spec text (v4); RAT headline = "Samples received" and RAT `won` KPI = Contract Signed; Coding `won` vs `dcs`. | C7 spec, R2, R3, H1 | Won = `Closed/Won` only. Contract signed and Payment are separate metrics. Header strip shows Won and Contracts signed. Fix the stale v4 sentence in the CompanyOps spec |
| 12 | **Entry row meaning.** "Leads Assigned" counts `Cold called assigned` entries from any source (C1), in C3 the same stage is double counted in two rows, the dashboard's `asg` counts owner-change events, `LinkedIn sent` is outside the C1 table despite being an entry. | C1, C3, C7 | M1 = entries into ranks 0/0b any source and distinct new deals; assignment (owner-change events) is a separate `Assigned to person` metric used in per-person sections only |
| 13 | **Dead rolled into one row** in snapshot reports; the per-reason view exists only in KPI sets. Also the single "Dead" cumulative double counts a deal that dies, is revived, and dies again. | C1/R1/H3/H5 | Dead by reason (4.2), distinct deals as well as entries |
| 14 | **Cross-pipeline label collision.** Whole-portal runs match on label: `Interested`/`Commercial Negotiation`/`Closed/Won` exist in both MAIN pipelines; Coding KPIs would count CoOps moves as attempted/connected. Three stage ids are shared. | H1, H3, H6 | Key on `(portal, pipeline, stage_id)`; every query carries a pipeline filter |
| 15 | **Stage-vocabulary drift.** `"Company Ops Data"` pipeline split into Cluster 1/2; Campaign became CoOps ( Global ) with new ids; H3 and H6 still assume identical labels/ids; Coding has two unmapped closed stages; C7 covers only Cluster 2. | C6, C7, H3, H6 | Live stage index at each sync; alias table for historical labels (v2 renames, deleted LinkedIn ids); WARN on unmapped; delete stale reports (section 6) |
| 16 | **Roll7/Prev7 with missing days.** `all(7)` required (C3, H3 build_report), blanked column; partial windows allowed elsewhere with labels; the Cluster 2 doc notes a -4% was an artefact of 4 to 5 days vs 7. Missing days: 09-26/27/10-02/10-03 across repos. | C2, R2, H3, H5 | From the event store all windows are complete. If a sync is stale the footer says so. Percent change shown only when both windows are full |
| 17 | **Money field.** `cost` ("Deal Cost USD") vs `amount`. MAIN has an unreconciled conflict; CompanyOps/RAT write `cost`. | C7 spec, H6 | Use `cost` everywhere; ignore `amount`. Flag deals with `amount` set and `cost` empty in the footer |
| 18 | **Percent-change semantics.** prev 0 shown as +100% (C2), as "flat" (C3), as "new" (R2), or replaced by last non-zero day (H3). | all mails | One function: show absolute deltas; percent only if prev >= a threshold (e.g. 10); prev 0 = "new" |
| 19 | **Note classifiers.** Three rule sets with different bucket names (`ops_note_rules` 20+ buckets; RAT 8 starter rules; `note_rules.py` ~12) and three `BUCKET_MAP`s. `WRONG_SPOC` exists only in the MAIN scripts. | C6, R3, H1 | One module, ordered, regression-tested against all 7.5k stored notes. Keep the ordering discipline (negations first; "received" before "sent"; "Interest email sent" before disqualifications). Map buckets that restate a stage KPI to `None` |
| 20 | **People.** Hard-coded per script (H1 4 people, H2 3 people, C6 stale emails, H6 `PREFERRED` ids); RAT uses all owners. A person can have owner ids in several portals. | all | `person` keyed by email, built from live owners of all three portals; include anyone with a human event in the window; configurable role/target |
| 21 | **Targets/roles.** CompanyOps `TARGET=100` leads engaged; MAIN uses GTM/Lead Manager/Closer quotas (40 connected + 5 VCs daily; weekly 40/25/15; sprint LoC); RAT has none. The MAIN SOP states the team now works the whole funnel. | C6, H7 | Targets table `(person|role, metric, period, value)`; attainment shown only where a target exists |
| 22 | **Timezones.** Portal tz: US/Eastern (COMPANYOPS, RAT), Asia/Calcutta (MAIN). All scripts compute IST in code but the backfill as-of logic uses UTC. | all | IST in code only; store `entered_at_utc` and `ist_day` |
| 23 | **Compliance fields** exist only in C6 and use portal-specific property names. | C6 | Per-portal property mapping (4.3 M21) |
| 24 | **Recipients and transport.** `bhanu.enamala` default (CompanyOps/MAIN funnel mails and tests), `shobit.gupta` (MAIN 18:30 tracker), RAT `--to` required, multiple Gmail token files. Policy: only one scheduled mail. | all | One recipient config; one OAuth token (Google token in `secrets/`); outbox fallback; dry-run default; keep the VCF notifier disabled |
| 25 | **Scheduling and gaps.** No committed scheduler for funnel mail; the MAIN daily-report workflow is referenced but absent; snapshots miss weekends and the last two days in every family; RAT refuses to write across a gap. | all | A scheduled idempotent job (retry-dense cron as in the CompanyOps workflows) that syncs, rebuilds, and sends; gap-free by construction |
| 26 | **History cost and failures.** Per-deal GETs (10 min for 6k deals), threads in some scripts, `history_failures` counted only in C4. | C1, C4, H3, H6 | Batch read 50 per call with checkpointing and concurrency (12 workers worked inside ~100 req/10 s); record failures per run and surface them in the footer |
| 27 | **Doc/code drift.** `OPS_DASHBOARD_METRIC_SPEC.md` says v4; `DASHBOARD_METRIC_SPEC.md` says "both pipelines identical"; `Cluster2_Report_Metric_Definitions.md` quotes 15 rows while C3 had 14 + Dead with different groupings. | docs | Generate the glossary from the mapping tables in code (C6 pattern) and delete hand-written copies |

---

## 6. Stale / broken items to resolve during consolidation

| Item | Problem | Action |
|---|---|---|
| `companyOps/opsdata/daily_fullfunnel_report.py` | Pipeline label "Company Ops Data" is gone, `TRACK_EMAILS` stale (Kartik is RAT), HTML written to disk only (no mail), engaged undercount (conflict 7) | Re-implement as the person section of the unified report over both clusters |
| `companyOps/opsdata/company_ops_cluster_report_send.py` | Needs n8n env + Data Table, double-counted rows | Retire; import its n8n table once if still reachable (needs keys not in `.env`; skipped) |
| `companyOps/dashboard/build_ops_dashboard.py` | Cluster 2 only (`2464812771`) | Parameterise by `(portal, pipeline)`; add Cluster 1 and all other funnels |
| `hubspot/.../full_funnel_report.py` + `full_funnel_dashboard_mail.py` | Stale Campaign ids, no pipeline filter, CoOps Global new ids unmapped | Replace by canonical mapping |
| `hubspot/build_dashboard.py` (+ `lh2-pipeline` copy) | "Both pipelines identical" assumption false | Replace by canonical mapping; keep per-role panels as a view |
| `coopsglobal_*` seeds | Hard-coded `/Users/bhanu/Desktop/hubspot/...` path | Delete after import |
| `hubspot/.github/workflows/daily-report.yml` | Referenced in docs, absent from the copy | Recreate as the unified job's schedule |
| RAT `snapshots/` | 09-26/27 missing; chain would have refused | Archive; recompute from history |
| Gmail tokens | Several copies (`bhanu_gmail_token.json` in CompanyOps and RAT; `gmail_token.json` in MAIN) | Use the consolidated token in `secrets/`; not touched here |

---

## 7. Open questions for the owner (answers change the mapping, not the architecture)

1. **CoOps Global `Communicated` / `No Response`:** is `Communicated` "first message sent" (rank 1) or "human replied" (rank 2)? The CoOps KPI set calls it "Communicated" but the dead stage "Communicated / No Response" suggests rank 1.
2. **Meeting held:** is there a manual field (`gmeet1_date`, `gmeet_outcome`, `discovery_call_date`) the callers populate consistently enough to use instead of the derived rule?
3. **Cluster 2 cohort rule:** should the unified Inception for Cluster 2 default to "since 2026-09-15 excluding migration" (C4) or all history (C1/C7)? The spec supports both; one needs to be the headline.
4. **Win headline for RAT:** keep "Won" (Closed/Won, currently 0) or a configurable "primary milestone" per funnel (RAT used Samples received)?
5. **Recipients:** should the unified 18:30 mail go to Shobit (Supply/Pod Head) as H7 does, or stay with Bhanu until validated?
6. **Targets:** confirm 100 engaged/person/day (CompanyOps SOP) for all funnels, or keep the MAIN role quotas.

---

## 8. Reference: files read (all under `/Users/bhanu/Desktop/LeadGenMonolith/legacy/`)

- companyOps: `opsdata/{daily_fullfunnel_report,full_funnel_report,full_funnel_dashboard_mail,cluster_cutoff_report,company_ops_cluster_report_send,full_funnel_backfill_day,full_funnel_backfill_range,send_csv_mail}.py`, `dashboard/{build_ops_dashboard,ops_note_rules}.py`, `docs/{OPS_DASHBOARD_METRIC_SPEC,Cluster2_Report_Metric_Definitions}.md`, `docs/methodology/*.md`, `.github/workflows/{deploy_ops_dashboard,outflo-sync}.yml`, `crm_mirror/data/snapshots/*`.
- RapidActionTeam: `rapidactionteam_{daily_report,dashboard_mail,full_funnel_report,funnel_core,note_rules,report_core}.py`, `pipeline_sync.py`, `email_transport.py`, `dashboard/build_ops_dashboard.py`, `funnel report setup.md`, `snapshots/*`, `.github/workflows/dashboard.yml`.
- hubspot: `crm_mirror/enrich/{daily_fullfunnel_mail,daily_activity_report,coops_fullfunnel_mail,full_funnel_report,full_funnel_dashboard_mail,coding_funnel_report,coopsglobal_*,daily_report,daily_handover_mail,gmail_sender}.py`, `build_dashboard.py`, `analysis/KPI_DEFINITIONS.md`, `docs/sop/DASHBOARD_METRIC_SPEC.md`, `docs/LH2_HUBSPOT_OPERATING_CONTEXT.md`, `DASHBOARD_REALTIME_BRIEF.md`, `.github/workflows/deploy-dashboard.yml`, `crm_mirror/data/snapshots/*`.
- Live read-only probes: `GET /account-info/v3/details`, `/crm/v3/pipelines/deals`, `/crm/v3/owners`, `/crm/v3/properties/deals`, and `POST /crm/v3/objects/{deals,notes}/search` (totals only), for all three portals.
