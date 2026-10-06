# RapidActionTeam (RAT) HubSpot funnel - discovery

Evidence: code and docs in `legacy/RapidActionTeam` plus live read-only HubSpot calls on 2026-10-04 (GET, plus search POSTs and `batch/read`, which is read-only). Key used: `HUBSPOT_KEY_RAT` from the monolith `.env`. No secrets are printed here.
Nothing was written to HubSpot, Google or any enrichment vendor.

## 1. Portal identity (live)

| Field | Value |
|---|---|
| portalId | **247485022** |
| accountType | STANDARD |
| timeZone / utcOffset | US/Eastern (-04:00) |
| companyCurrency | USD (no additional currencies) |
| uiDomain / hosting | app-na2.hubspot.com / na2 |
| Private-app API quota | 250,000 calls/day (usage 0 at check time) |

- The key works: `/account-info/v3/details` returned 200.
- The repo's `.env` var `HUBSPOT_API_KEY` is byte-identical to `HUBSPOT_KEY_RAT` in the monolith `.env` (compared with `==`, not printed).
- The portal timezone is US/Eastern but all RAT report code uses **IST (UTC+5:30)** (`IST = timezone(timedelta(hours=5, minutes=30))`) for "today" boundaries. The unified report must pick one tz per portal and say which.
- The key lacks the email-object scope: `POST /crm/v3/objects/emails/search` returns 403 (missing scope). The repo never reads emails, so this does not affect it.
- INTEGRATION-sourced stage events all carry `sourceId` 54087015 (the private app). They make up the 1,227 non-human events in the stage history below.

## 2. Deal pipelines and stages (live, `GET /crm/v3/pipelines/deals`)

Two pipelines exist.

1. **"Rapid Action Team"**, id `2575252183`, created 2026-09-22, 30 stages (16 live, 14 dead). Holds 1,236 of the 1,237 deals.
2. `default` ("Deals pipeline"): the 7 HubSpot stock stages, holding 1 deal, "HubSpot - New Deal (Sample Deal)" (id 349961584363, stage `appointmentscheduled`, source CRM_UI). It is stock-account noise and ignored by all code. Every RAT query filters `pipeline == 2575252183`.

### RAT pipeline stages

`isClosed` and `probability` are exactly as returned live and match `pipeline_sync.py`. "Deals now" is the live count on 2026-10-04, and the Oct 1 snapshot is identical (see section 7).

| Order | Stage id | Label | Meaning | isClosed | prob | Deals now |
|---|---|---|---|---|---|---|
| 0 | 4332503773 | Cold Called Assigned | LIVE, entry point (assigned, not yet dialled) | false | 0.02 | 449 |
| 1 | 4332503774 | No Pickup | LIVE, first ring-out | false | 0.03 | 135 |
| 2 | 4332503775 | Callback | LIVE, retry in flight | false | 0.04 | 24 |
| 3 | 4332503776 | Replied | LIVE, prospect engaged | false | 0.08 | 27 |
| 4 | 4332503777 | 1st Interest Sent | LIVE | false | 0.12 | 78 |
| 5 | 4332503778 | 1st Interest Follow Up | LIVE (hidden in the funnel report) | false | 0.15 | 33 |
| 6 | 4332503779 | Discovery Call | LIVE, meeting booked | false | 0.25 | 8 |
| 7 | 4332503780 | Call Rescheduled | LIVE, a loop back to Discovery Call, not forward progress | false | 0.25 | 2 |
| 8 | 4332503781 | Sample Requested | LIVE | false | 0.35 | 3 |
| 9 | 4332503782 | Sample Follow Up | LIVE | false | 0.40 | 2 |
| 10 | 4332503783 | Sample Received | LIVE, headline traction metric | false | 0.50 | 9 |
| 11 | 4332503784 | Negotiation | LIVE | false | 0.65 | 8 |
| 12 | 4332503785 | Contract Signed | LIVE | false | 0.85 | 0 |
| 13 | 4332503786 | Ops Data Handover Done | LIVE | false | 0.90 | 0 |
| 14 | 4332503787 | Payment Initiation | LIVE | false | 0.95 | 0 |
| 15 | 4332503788 | Closed / Won | TERMINAL won (only closed stage that is not dead) | **true** | 1.0 | 0 |
| 16 | 4332503789 | Dead: ColdCall/WrongFit | DEAD | true | 0 | 60 |
| 17 | 4332503790 | Dead: ColdCall/WrongNumber | DEAD | true | 0 | 59 |
| 18 | 4332503791 | Dead: ColdCall/NotInterested | DEAD (covers both first call and callback) | true | 0 | 89 |
| 19 | 4332503792 | Dead: ColdCall/NoPickup | DEAD (callback also rang out) | true | 0 | 210 |
| 20 | 4332503793 | Dead: Replied/NotInterested | DEAD | true | 0 | 25 |
| 21 | 4332503794 | Dead: 1stInterest/NoResponse | DEAD | true | 0 | 7 |
| 22 | 4332503795 | Dead: 1stInterest/NotInterested | DEAD | true | 0 | 6 |
| 23 | 4332503796 | Dead: DiscoveryCall/RejectedByLH2 | DEAD | true | 0 | 1 |
| 24 | 4332503797 | Dead: DiscoveryCall/NotInterested | DEAD | true | 0 | 1 |
| 25 | 4332503798 | Dead: DiscoveryCall/NoShow | DEAD | true | 0 | 0 |
| 26 | 4332503799 | Dead: Sample/NotReceived | DEAD | true | 0 | 0 |
| 27 | 4332503800 | Dead: Sample/LowDataQuality | DEAD | true | 0 | 0 |
| 28 | 4332503801 | Dead: Negotiation/PricingNotAgreed | DEAD | true | 0 | 0 |
| 29 | 4332504762 | Dead: Negotiation/ContractualNotAgreed | DEAD | true | 0 | 0 |

Totals: 778 live, 0 won, 458 dead, 1,236 in the pipeline.

Funnel meaning, from `build.md` section 1 and `pipeline_sync.py`:

- The stage name is the outcome ("the outcome IS the stage"). There are no "attempted" stages and no separate lost-reason field.
- The dead label shape is `Dead: <where it died>/<why>`.
- `Dead: ColdCall/NoPickup` means the callback also rang out. `No Pickup` (live) means the first ring-out.
- Flow:
  - Cold Called Assigned goes to No Pickup, Replied, or a dead stage (WrongFit, WrongNumber, NotInterested).
  - No Pickup goes to Callback, which goes to Replied, Dead NotInterested, or Dead NoPickup.
  - Replied goes to 1st Interest Sent or Dead Replied/NotInterested.
  - 1st Interest Sent goes to 1st Interest Follow Up, then Discovery Call or a dead stage.
  - Discovery Call goes to Sample Requested, Call Rescheduled (loops back), or a dead stage.
  - Sample Requested goes to Sample Follow Up, then Sample Received or Dead Sample/NotReceived.
  - Sample Received goes to Negotiation or Dead Sample/LowDataQuality.
  - Negotiation goes to Contract Signed or a dead stage.
  - Contract Signed goes to Ops Data Handover Done, then Payment Initiation, then Closed / Won.
- Stage IDs are never hardcoded in the repo. All scripts resolve them by label from the pipeline whose label equals "Rapid Action Team".
- HubSpot's probability field is not load-bearing. Reporting uses stage labels only.
- The `default` pipeline's stage ids (`appointmentscheduled`, `closedwon`, ...) do not collide with RAT's numeric ids.

### Funnel-report row mapping (`rapidactionteam_funnel_core.py`)

`DASHBOARD_ROWS` is built from `LIVE_STAGES` plus one "Dead" row. The row order is:

1. Cold Called Assigned. This is the only `any_source` row, so bulk API pushes count as flow.
2. No Pickup/Callback (No Pickup and Callback folded together).
3. Replied.
4. 1st Interest Sent. 1st Interest Follow Up is hidden.
5. Discovery Call Scheduled (the relabelled Discovery Call).
6. Call Rescheduled.
7. Sample Requested.
8. Sample Follow Up.
9. Sample Received.
10. Negotiation.
11. Contract Signed.
12. Ops Data Handover Done.
13. Payment Initiation.
14. Closed / Won.
15. Dead (all 14 dead labels collapsed).

### Legacy daily-activity KPI sets (`rapidactionteam_report_core.METRICS`)

These are explicit and non-cascading, and Callback and Call Rescheduled are in none of them.

| KPI | Stage labels |
|---|---|
| out | Cold Called Assigned |
| conn | Replied, Dead: ColdCall/NotInterested |
| int | 1st Interest Sent, 1st Interest Follow Up, Dead: 1stInterest/NoResponse, Dead: 1stInterest/NotInterested |
| dc | Discovery Call, Dead: DiscoveryCall/NoShow, Dead: DiscoveryCall/RejectedByLH2, Dead: DiscoveryCall/NotInterested |
| smp | Sample Requested, Sample Follow Up, Sample Received, Dead: Sample/NotReceived, Dead: Sample/LowDataQuality |
| neg | Negotiation, Dead: Negotiation/PricingNotAgreed, Dead: Negotiation/ContractualNotAgreed |
| won | Contract Signed |

Note that `won` here means Contract Signed, not Closed / Won. The funnel-report path (`rapidactionteam_full_funnel_report.py`) uses its own `DASHBOARD_ROWS`.

## 3. Owners (live, `GET /crm/v3/owners`)

There are 5 owners and 0 archived. Role in the funnel is inferred from live data and the code, since no repo doc names roles explicitly.

| Owner id (= userId) | Name | Email | Role in funnel (evidence) |
|---|---|---|---|
| 168541136 | Prerna Jain | prerna.jain@lh2.ai | **Caller (primary)**. Owns 811 deals, 810 contacts, 365 notes and 28 of 37 tasks. Drove 988 of the 1,289 human stage moves. Default owner in the push scripts. |
| 98906502 | Harsha A | harsha.a@lh2.ai | **Caller (second)**. Owns 417 deals, 388 contacts and 196 notes. Made 273 human stage moves. Gets the COBOL pushes (`push_all_to_owner ... "Harsha A" --category Cobol`). |
| 96316911 | Kartik Pillai | kartik.pillai@lh2.ai | **Closer / negotiator**. Owns exactly the 8 deals in Negotiation, all moved by hand (28 human stage moves). 8 meetings. |
| 166322218 | Ashish Ranjan | ashish.ranjan@lh2.ai | Not a funnel actor. Owns 16 contacts and 131 API-created "GTM Analyst" meetings (July 2026, recruiting-style, unrelated). `india_cobol_ip_NOTES` / `poll_push_cobol` mention an "AshishCluster" process that edits the COBOL batch3 CSV. |
| 168829625 | Rohan Danny Machado | rohan.danny@lh2.ai | No records. |

- Blank-owner contacts: 160 of 1,374.
- Owner assignment rules in code (`import_cad_list.py`, `import_cad_100_callable_founders.py`, `discover_founders_and_push.py`):
  - New cold leads are alternated 50/50 between "Prerna Jain" and "Harsha A", via `OWNER_SPLIT_NAMES`.
  - `push_all_to_owner.py` pushes a whole CSV to one named owner.
  - Owner lookup is by display name through `owners_map()`, which builds the "First Last" display name.
  - **updatedByUserId equals owner id** here, but the code bridges them through `owners.userId` anyway. Keep that bridge.
- Audit logs show repeated manual rebalancing:
  - `reassign_harsha_to_prerna_coldcall_*` (27 + 61 deals).
  - `revert_prerna_to_harsha_coldcall_*` (61).
  - `reassign_harsha_unexplained_cad_to_prerna_*` (139).

## 4. Custom properties (live vs `properties_sync.py`)

### Deals: 12 custom properties, all present live and all in the sync contract

| Name | Type / field | Notes |
|---|---|---|
| lead_category | enumeration / select | Live options: **CAD, Cobol, COBOL_high, Publishers**. The sync script only lists `["CAD"]`; the other three were added by other scripts or by hand. |
| cold_call_assigned_date | date / date | Populated on 1,133 of 1,237 deals. |
| replied_at | datetime / date | Populated on 0 deals. |
| interest1_sent_date | date / date | 0 |
| discovery_call_date | datetime / date | 0 |
| discovery_call_link | string / text | Not counted. |
| call_rescheduled_date | date / date | Not counted. |
| sample_requested_date | date / date | 0 |
| sample_received_date | date / date | 0 |
| contract_signed_date | date / date | 0 |
| data_handover_date | date / date | Not counted. |
| cost | number / number | Defined as "THE ONE price property". Populated on **0** deals. Built-in `amount` is set on 1 deal, the stock sample deal. |

- Only `cold_call_assigned_date` is ever written. The stage-date properties are defined but unused. Stage timing comes from stage history, not from these fields.
- All custom deal properties are in group `dealinformation`.

### Contacts

- One custom property exists: `linkedin_account` (string/text). It is populated on 0 contacts and is not in the repo contract. It is a stock or leftover property.
- The repo relies on built-ins instead:
  - `hs_linkedin_url`: the per-person join key, set on 1,266 of 1,374 contacts.
  - `phone` (phonenumber): 1,236.
  - `mobilephone`: 95.
  - `email`: 1,180.
  - `jobtitle`: 1,052.
  - `hubspot_owner_id`: 1,214.
  - Phone prefixes: 1,235 start +91 and 1 starts +15. Hard rule: only `to_e164_india()` numbers (+91, mobile starting 6-9) may be written.

### Companies

- 0 custom properties. All 844 are source `COMPANIES`, meaning they were auto-created by HubSpot from contact email domains.
- `domain` is set on all 844. `country` is set on only 32.

## 5. Object counts (live, 2026-10-04)

| Object | Count |
|---|---|
| Deals | **1,237** (1,236 RAT + 1 default sample) |
| Contacts | **1,374** |
| Companies | **844** |
| Notes | 561 |
| Tasks | 37 |
| Meetings | 143 |
| Calls | 2 |

- The 2 calls and 2 of the tasks are HubSpot sample objects. The note "HubSpot holds no call-log objects" in the code is accurate.
- The 143 meetings are not funnel data: 141 are API-created GTM-analyst items owned by Ashish Ranjan, and 2 are sample items.
- Deals by `lead_category`: CAD 1,019, Cobol 95, Publishers 19, blank 104.
  - The 19 Publishers deals were created 2026-10-01. They were not created by any RAT-repo script, so they were pushed ad hoc, probably via `push_all_to_owner.py --category`.
  - The 104 blank-category deals are mostly 100 deals created 2026-09-29.
- Deals by create date (UTC):

| Date | Deals |
|---|---|
| 2026-09-22 | 1 |
| 2026-09-23 | 219 |
| 2026-09-24 | 116 |
| 2026-09-25 | 51 |
| 2026-09-28 | 216 |
| 2026-09-29 | 100 |
| 2026-09-30 | 475 |
| 2026-10-01 | 59 |

- Deal creation source: 1,227 INTEGRATION (the private app via scripts) and 10 CRM_UI.
  - The 10 CRM_UI deals include the stock sample deal and deals created by hand by Kartik for Essentia, Sriram Tharaka, KlimArt, Buildose and others.
- Last deal modification is 2026-10-02T00:08Z. The portal has been idle since then (see section 7).
- Contacts by source: INTEGRATION 1,229, API 141, CRM_UI 4. Contacts by owner: Prerna 810, Harsha 388, Ashish 16, none 160.
- Stage-history events (`propertiesWithHistory=dealstage`, 2,516 events): CRM_UI 1,289, INTEGRATION 1,227.
  - Human moves by user: Prerna 988, Harsha 273, Kartik 28.
  - The INTEGRATION events are the bulk pushes into the entry stage.
  - The repo's stage-history reading (`sourceType == CRM_UI`) is therefore load-bearing. Counting every event would roughly double the numbers.

## 6. Local files and "databases" in the repo, and their relationship to HubSpot

**HubSpot is the single source of truth.** There is no SQLite, Postgres or central database in this repo. The README and `build.md` say plainly that the dashboard must never disagree with HubSpot. All local files are inputs, mirrors or audit trails.

| Path | What it is | Direction |
|---|---|---|
| `leads/*.csv` (11 files, gitignored PII) | Lead-source staging. These are the inputs the push scripts read. | Input to HubSpot |
| root `hubspot_CAD_leads_held_pool_2026-09-30.csv` (158 rows) | Held CAD pool carried over from the sibling `hubspot` repo `temporary/` folder (Aug 31 to Sep 2 batches). Deduped against 5,824 deal names in the other portal and this repo's pool. Provenance in `hubspot_CAD_leads_held_pool_NOTES.md`. | Input, already pushed (see `leads/CAD_held_pool_icp_pass.csv`, 144 rows, pushed 2026-09-30 to Prerna) |
| root `india_cobol_ip_{top12,batch2,batch3}_enriched_2026-10-01.csv` (12, 16, 42 rows) | India COBOL-IP TAM build output from the sibling `hubspot` repo, enriched via Apollo. Provenance in `india_cobol_ip_NOTES.md`. | Input. top12 and batch3 were pushed to Harsha as `Cobol` / `COBOL_high`. |
| `RAT_CAD_Tracks1_2_Unassigned.xlsx` and `... (1).xlsx` | Identical copies (same md5), 109 rows plus a header, sheet `Unassigned`. Columns: status, poc_phone_e164, icp_track, source, company, domain, matched_keyword_group, hq_location_city, poc_*, notes. This is a hand-curated Excel extract of `RAT_CAD_Tracks1_2_Combined.csv` for unassigned callable leads. | Input / human worksheet. The `(1)` copy is a download duplicate. |
| `leads/RAT_CAD_Tracks1_2_Combined.csv` (159) | The first real CAD lead sheet, tracks 1 and 2, consumed by `import_rat_cad_tracks.py` (first N valid +91 mobiles to Prerna) and used as a dedup source by `discover_cad_leads.py`. | Input |
| `leads/CAD_candidates_batch{1,2}_2026-09-28.csv` (179, 174) | Stage-1 discovery output (Apify Google Maps plus Google Places), ICP-qualified, headcount from free Apollo org enrich. | Intermediate |
| `leads/CAD_Callable_Founders_2026-09-28.csv` (121), `CAD_RAT_Unassigned_Ready42.csv` (42), `CAD_recovered_00prefix.csv` (1), `CAD_held_pool_icp_pass.csv` (144), `COBOL_batch3.csv` (31), `COBOL_high_top12.csv` (12) | Stage-2 enriched "callable founder" files (company, person, title, email, phone, linkedin, gate). These go to the 50/50 or one-owner importers. | Input to HubSpot |
| `leads/CAD_SUPPLY_List{A,B}_candidates_2026-09-30.csv` (450, 159) | CAD-data-supply account targets from `TAMbuilds/CADTAMbuild.md` (boutique architecture studios, CAD outsourcers). Discovery only, **not enriched, not pushed**. | Not yet in HubSpot |
| `TAMbuilds/CADTAMbuild.md` | Build prompt for the CAD-supply TAM (Lists A to F, rules, budget caps). | Spec doc |
| `audit/*.json`, `*.log` (about 55 files, gitignored) | Per-run audit trails: ids created, from/to values, enrichment results (`enrich_cad_founders_*`, `import_cad_100_run_*`, `push_all_to_owner_*`, `reassign_*`, `revert_*`, `remove_*`, `dedup_contacts_*`, `backfill_*`). `cad_enrich_seen.json` holds 350 already-seen candidates. `_poll_cobol_batch3_state.json` and `.txt` hold the poller state, a company to deal/contact id map used to diff the externally edited COBOL file and archive removed rows. | Mirror of writes made, a replay log |
| `snapshots/rat_{2026-09-25,09-28,09-29,09-30,10-01}.json` | Funnel-report daily snapshot chain, one JSON per day. See section 7. | Output of report step 1, input to step 2 |
| `dashboard/index.html`, `dashboard/build_ops_dashboard.py` | Static board. The builder writes `dashboard/data.json` (not present locally, gitignored, rebuilt by CI) from a live pull. | Read-only mirror |
| `bhanu_gmail_token.json` | Google OAuth user token, scope `gmail.send` only. Used by `email_transport.py` as the gmail_oauth candidate. It has an `account` field that is empty and an expiry of 2026-10-01. It authenticates as an unverified identity, which `funnel report setup.md` section 11 warns about. | Credential, copied to the monolith `secrets/` area |
| `.env` | Legacy vars: `HUBSPOT_API_KEY`, `apollo_api_key`, `signal_hire`, `GOOGLE_MAPS_API_KEY`, `APIFY_TOKEN`. Map to the monolith names `HUBSPOT_KEY_RAT`, `APOLLO_API_KEY`, `SIGNALHIRE_API_KEY`, ... | Credentials |
| `.gitignore` | Excludes `.env`, `audit/`, `leads/`, `snapshots/`, `*.csv`, `*.xlsx`, `context.md`, token files. `context.md` (the living portal/owner notes) is **not** in the copied tree. | n/a |

- **Dedup keys for pushes** are normalized company name for deals, plus LinkedIn URL and email for contacts. This is done live against HubSpot, not against the local CSVs. Because of the hand-made CSV and xlsx copies, local files may contain rows already in HubSpot.
- Row counts: the 11 `leads/` files hold 1,516 rows in total (non-unique across files). The 4 root CSVs hold 228 rows.

## 7. Daily funnel report(s)

### A. Funnel report (the live one): two scripts, one JSON per day

Design source: `funnel report setup.md` (the porting guide), applied to this pipeline.

**Step 1: `rapidactionteam_full_funnel_report.py`** (HubSpot access, writes `snapshots/rat_<YYYY-MM-DD>.json`)

- Date and window: today in IST. Candidates are the deals in pipeline 2575252183 where `hs_lastmodifieddate >= IST midnight`, narrowed to `hs_v2_date_entered_current_stage` falling on that IST day.
- Per candidate deal it reads full stage history with `GET /crm/v3/objects/deals/{id}?propertiesWithHistory=dealstage`. Timestamps are converted to IST dates.
- Two separate signals:
  - **A, engaged**: any history event today with `sourceType == "CRM_UI"` (a human moved it). The list of deal ids goes to `engaged_deal_ids`.
  - **B, dashboard_flow**: row counts per `DASHBOARD_ROWS`. Only the entry row Cold Called Assigned also counts non-human events (bulk pushes). Every other row needs CRM_UI.
- `flow` is the raw per-stage-label count under the same rule. `current_state` is a true count of deals now on each of the 30 stages.
- `cumulative` is incremental: yesterday's cumulative plus today's `dashboard_flow`.
  - On a same-day rerun it backs out today's flow to avoid double counting.
  - `--seed` rebuilds the baseline from full history of every deal in the pipeline and sets `"seeded": true`.
  - The script **aborts** if yesterday's snapshot is missing (broken chain) unless `--allow-gap` is passed.
- There is no `--date` argument, so it cannot backfill.

**Step 2: `rapidactionteam_dashboard_mail.py`** (zero HubSpot calls)

- It reads today's snapshot plus the previous 13 days, so rolling 7-day and previous 7-day windows. If today's snapshot is missing it prints "No snapshot" and sends nothing.
- Top strip, three boxes:
  - "SAMPLES RECEIVED TO DATE" (the `cumulative["Sample Received"]` row, chosen because Closed / Won is still 0) plus "+N this week" against 7 days ago.
  - "7-DAY LEADS ENGAGED": set union of `engaged_deal_ids` over the last 7 days, partial windows allowed with a note.
  - "DAILY LEADS ENGAGED": today's count with a percentage chip against yesterday.
- Table: one row per `ROW_LABELS`, 15 rows in total. Columns: Inception-to-date (cumulative), Rolling-7 vs previous-7 sum plus a change chip, Daily vs yesterday plus a change chip.
- The email has a plain-text part and an HTML alternative (inline CSS), plus a mandatory CAVEAT footer: "HubSpot holds no call-log objects ... activity is inferred from stage moves made by people in the HubSpot UI. Bulk imports are counted only as new leads in the entry stage."
- Send needs `--send --to a@x,b@y`. The default run only prints. **No recipient is hardcoded** (grep for addresses found none), so the recipient list lives only in whoever runs it.
- Transport (`email_transport.py`) is tried in order:
  1. `gmail_oauth`, using `.gmail_token.json` or `bhanu_gmail_token.json` (`gmail.send` scope).
  2. `gmail_sa`, a service account from `google_service_account_file` and `gmail_send_as`.
  3. `smtp`, using `smtp_host`, `smtp_user`, `smtp_password`.
  4. `outbox`, which writes JSON to `audit/email_outbox/` (this folder does not exist, so no email has ever fallen to it).
  - It never raises.

**Schedule: none for the funnel report.** `.github/workflows/dashboard.yml` only builds the static board (cron `0 */6 * * *`, plus `workflow_dispatch`) and deploys it to GitHub Pages using the repo secret `HUBSPOT_API_KEY`. Nothing automatically runs step 1 or step 2. Per `funnel report setup.md` section 12, the intent is to run step 1 late in the IST day, then step 2. In practice the snapshot mtimes are about 19:10 to 20:10 local, i.e. run by hand in the evening.

### B. Legacy daily activity report: `rapidactionteam_daily_report.py` (plus `rapidactionteam_report_core.py`)

- Layer 1 `collect()` supports a window ending today only. It reads stage history per candidate deal and notes by `hs_timestamp`.
- Human stage moves are attributed to the person via `updatedByUserId` mapped through `owners.userId`. They feed the per-owner KPI table from the `METRICS` sets above.
- Notes are fetched through `/crm/v3/objects/notes/search`, associated to deals through `/crm/v4/associations/notes/deals/batch/read`, and classified by `rapidactionteam_note_rules.py` (ordered first-match-wins regexes, then `BUCKET_MAP`).
  - Bad number: `invalid (lead|number)|wrong number|...`.
  - Not decision maker.
  - Not interested.
  - No pickup.
  - No reply.
  - Discovery call and Sample sent: suppressed from the note table because they duplicate stage KPIs.
  - Interested.
  - Other: listed in an appendix.
  - The rules are **starter rules only** (the file says so). They were not built from real RAT note text, so many notes will land in "Other".
- Sections: (1) per-owner KPIs, (2) note-only activity, (3) leads engaged per owner (engaged, via stage-move, net new, with a footnote counting deals excluded for a "bad number" note).
- Flags: `--date` (only today allowed), `--week` (week to date), `--send --to`.
- The same `rapidactionteam_note_rules.py` is imported by the dashboard builder (the one module owning the rules).
- Not scheduled, and it is secondary to report A.

### C. Dashboard: `dashboard/build_ops_dashboard.py`

- `data.json` holds: `generated_at_utc`, `pipeline_label`, `total_deals`, `stage_order`, `stage_counts` (live count per stage), `recent_note_buckets` (last 7 days), `recent_note_other_count`, `recent_notes_scanned`, `caveat`.
- `dashboard/index.html` renders live stage bars versus dead, and note buckets.
- The dashboard is deployed by GitHub Actions.

### Snapshot chain state (the xlsx/leads/snapshots question)

| Snapshot | Notes |
|---|---|
| rat_2026-09-25 | First snapshot. It has `cumulative` (260 on the entry row versus 55 of flow that day, so it carried a baseline) but no `seeded` flag, so it was probably written by an earlier version of the script before the flag existed. 396 deals in the current_state total. |
| rat_2026-09-28 | `seeded: true`, rebuilt from full history. This implies the 09-26 and 09-27 weekend gap broke the chain and it was reseeded. 536 deals. |
| rat_2026-09-29 | 702 deals |
| rat_2026-09-30 | 1,197 deals |
| rat_2026-10-01 | 1,236 deals, engaged 89, cumulative as below. |

- Missing: **10-02 (Fri), 10-03 (Sat), 10-04 (Sun)**. The chain has stopped, and running step 1 today would abort unless it uses `--seed` or `--allow-gap`.
- **The live per-stage counts equal the 10-01 snapshot's `current_state` exactly** (verified for all 30 stages, total 1,236). The last modified timestamp is 2026-10-02T00:08Z, which is 05:38 IST on Oct 2. So there was no real funnel movement after the Oct 1 snapshot. The 10-01 snapshot is still a faithful current state, and no data has been lost by the gap.
- 10-01 `cumulative` (inception to date):

| Row | Cumulative |
|---|---|
| Cold Called Assigned | 1117 |
| No Pickup/Callback | 433 |
| Replied | 134 |
| 1st Interest Sent | 140 |
| Discovery Call Scheduled | 13 |
| Call Rescheduled | 2 |
| Sample Requested | 16 |
| Sample Follow Up | 6 |
| Sample Received | 19 |
| Negotiation | 8 |
| Contract Signed | 0 |
| Ops Data Handover Done | 0 |
| Payment Initiation | 0 |
| Closed / Won | 0 |
| Dead | 466 |

- Cumulative caveat: the cumulative figures are event counts, so one deal that moves No Pickup then Callback counts twice in the combined row. The 1,117 Cold Called Assigned figure is below the 1,237 deals because it only counts entry events in stage history, so deals created straight into later stages or Dead (CRM_UI-created, or historical imports) are not counted there.
- Note also that `cold_call_assigned_date` is populated on 1,133 deals, which does not equal the snapshot cumulative either. These are separate measures.

## 8. Findings, gotchas and recommendations for the unified design

1. **Reports compute stage timing from HubSpot stage history, not from the date properties.** The 7 stage-date deal properties are empty. The unified model should store stage-entry events (deal_id, stage_id, ts, sourceType, updatedByUserId) in its own database. These can be bulk-fetched with `deals/batch/read` and `propertiesWithHistory` (50 per call; 1,237 deals took 25 calls). That also lets a past date be backfilled, which the legacy code cannot do.
2. **Human versus integration separation is essential.** About 49% (1,227 of 2,516) of stage events are INTEGRATION (bulk pushes). Keep the `CRM_UI`-only engagement rule and the entry-row-only leniency.
3. **Snapshot chain fragility.** An incremental `cumulative` plus an abort-on-gap rule is fragile: the chain is already broken for 10-02 to 10-04, and the 09-28 reseed suggests an earlier break. In the monolith, compute daily and cumulative values from the stored event table instead, so a missed day is just a recompute. The weekend gap is expected because the team does not work on weekends.
4. **Timezone.** Portal tz is US/Eastern, the code uses IST. Define `report_tz` per portal in config. For RAT keep IST, since the team calls India.
5. **Stage semantics for a merged cross-account funnel.** RAT's entry stage is "Cold Called Assigned" and all labels share the `Dead: <where>/<why>` shape. If the other portals use different stage names, add a per-portal stage-to-canonical-row mapping rather than keying by label.
6. **Hardcoded display names.** Owners are matched by display name ("Prerna Jain", "Harsha A"). Use owner id or email in the monolith. Owner id equals user id in this portal, but never assume that elsewhere.
7. **`lead_category` drift.** The live enum has 4 options but the sync script knows 1. It also has 104 blank values (100 from 09-29). Treat it as the source tag. Fix the sync script to cover Cobol, COBOL_high and Publishers, and backfill the blanks as read-only analysis (no writes were made).
8. **Dedup across repos.** The same company may appear in the other two portals. The repo itself deduped the held-pool CSV against 5,824 deal names from the other portal. Use normalized domain plus LinkedIn URL plus company name as the unified key.
9. **Mail caution.** Only the daily funnel email is sanctioned. `bhanu_gmail_token.json` is `gmail.send` only (no read), it has an empty `account` field and an expiry of 2026-10-01 (it needs a refresh). The unified report should not send until a recipient list is given. There is no hardcoded recipient.
10. **Untracked context.** `context.md` is not in the legacy tree (gitignored in the original). It holds the portal and owner ids and the incident log. If it exists at `/Users/bhanu/Desktop/RapidActionTeam/context.md`, it is worth reading separately. I only read `legacy/`, per the rules.
11. **Sibling-repo cross-references.** The notes files point at `~/Desktop/hubspot` (TAMBuildSpecs/India/India_COBOL_IP, `temporary/` held CAD batches) and an external "AshishCluster" process that edits the COBOL batch3 CSV in place. `poll_push_cobol.py` has an absolute path (`/Users/bhanu/Desktop/RapidActionTeam/leads/COBOL_batch3.csv`, visible in two audit files). The monolith must repoint these paths.
12. **Local-only data.** `leads/`, `audit/` and `snapshots/` hold PII and ids and are gitignored in the original. In the monolith keep them out of git, or move them into the database with the PII fields flagged.

## 9. Blocked / not done

- Nothing was denied. The `emails` search returned 403 for missing scope (not worked around). The Claude Docs and Drive MCP tools were not needed.
- Chat transcripts (`~/.claude/projects`) were not read, as instructed.
- I did not read `/Users/bhanu/Desktop/RapidActionTeam/context.md` or any original path.
