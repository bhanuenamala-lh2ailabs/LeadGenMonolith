# companyOps - HubSpot funnel discovery

Slug: `companyops-hubspot-funnel`. Evidence: code/docs in `legacy/companyOps` plus LIVE read-only calls on 2026-10-04 using `HUBSPOT_KEY_COMPANYOPS` (GET + search POSTs only; key never printed). A machine-readable copy of the stage map is in `docs/discovery/companyops-pipelines-live.json`.

## 0. Headline gotchas (read first)

1. **The portal is 246897735 (the "BUILD" portal), NOT 246754894.** `legacy/companyOps/context.md` is a copy of the *main* repo's context file (portal 246754894, 2 pipelines "Scraped"/"Campaign", owners Lamiya/Yuktha/Shobit etc., 5-source `lead_source` enum). Almost none of that table applies to this portal. The files that DO describe this portal: `README.md`, `docs/OPSDATA_PIPELINE.md`, `docs/OPS_DASHBOARD_METRIC_SPEC.md`, `docs/Cluster2_Report_Metric_Definitions.md`, `docs/methodology/company_ops_funnel_report_methodology.md`. Generic hard rules from context.md (no non-Indian number, no automated mail, HubSpot = source of truth, dry-run first) DO still apply.
2. **Two pipelines live now, both 33 stages:** `Company Ops Cluster 1 India` (id `default`, 1,135 deals) and `Company Ops Cluster 2 India` (id `2464812771`, 6,440 deals). Stage IDs differ per pipeline for identical labels (same trap as the main portal) - always resolve stage id from the deal's own `pipeline`.
3. **Stale pipeline label in code:** `opsdata/outflo_pull_push.py`, `opsdata/daily_fullfunnel_report.py` (and deals_v*/pipeline_v* scripts) look the pipeline up by label `"Company Ops Data"`. That label no longer exists live (renamed to Cluster 1/2). Those scripts would abort (`ABORT: pipeline ... not found` / `StopIteration`). Only `full_funnel_report.py`, `cluster_cutoff_report.py`, `tam/` and the `*_discovery` scripts use pipeline ids directly. The unified design must key on pipeline id, not label.
4. **Two docs-vs-live drifts:** (a) docs say stage `Callback +1 day`; live label is `Retired: Callback +1 day (use No pickup + task instead)` (still holds 51 C1 + 98 C2 deals and still counted in report row "No Pickup / Callback"). (b) docs say the whole portal has ~68 notes; live there are **1,849 notes**.
5. **Secrets stored under non-monolith names:** legacy `.env` uses `HUBSPOT_API_KEY` (monolith: `HUBSPOT_KEY_COMPANYOPS`), `signal_hire`, `apollo_api_key`, `theStackApi`, `theStackApiKeyTwo`, and the cluster report script also requires `n8n_cloud_url` + `n8n_cloud_api_key` which are NOT present in legacy `.env` or the monolith `.env` (see section 7 - that script cannot run as-is).
6. Live key lacks the `emails` (communications) CRM scope: `POST /crm/v3/objects/emails/search` returns a missing-scopes error. Deals/contacts/companies/notes/tasks/calls/meetings/owners/pipelines/properties all work.

## 1. Portal identity (live)

`GET /account-info/v3/details` -> HTTP 200: portalId **246897735**, accountType STANDARD, timeZone **US/Eastern** (utcOffset -04:00), companyCurrency **USD**, additionalCurrencies none, uiDomain app-na2.hubspot.com, dataHostingLocation na2. Key works. (The portal TZ is US/Eastern; all LH2 reporting is IST with an 18:30 IST day-close cutoff, computed in code.)

Object totals (search `total`): deals **7,575** (+ **498 archived**), contacts **8,165**, companies **2,641**, notes **1,849**, tasks 63, calls 2, meetings 33. Deal createdate range 2026-08-06 .. 2026-10-01. Contacts with `phone` 3,907 / `email` 3,283 / `mobilephone` 0.

GitHub Actions in the repo reference this same portal: both workflows say `HUBSPOT_API_KEY (portal 246897735)`.

## 2. Deal pipelines and stages (live)

Two deal pipelines. Closed flag: `isClosed=true` exactly for `Closed/Won` and all 15 `Dead:` stages. The only "won" stage is `Closed/Won`. Probabilities are HubSpot defaults per stage (shown in table). `displayOrder` in C1 starts at 1 and in C2 at 0 (cosmetic).

Funnel meaning (from `docs/OPSDATA_PIPELINE.md`, `OPS_DASHBOARD_METRIC_SPEC.md`, report code): ENTRY stages = `LinkedIn sent`, `Cold called assigned`. LIVE = everything not Dead/Won. DEAD = label starts `Dead:` (naming `Dead: <branch> / <reason>`; the pre-v4 slash convention `Dead/<where>/<why>` from context.md is NOT used here). `Retired: Callback +1 day ...` is a retired live stage kept because deal history references it - do not write to it. `Dead: Email Campaign / Branch Retired` is not a funnel outcome: it parks the 124 (now 118 in C2) deals from the retired Gmail cold-email branch.

"report row" = which of the 15 rows of the Cluster 1&2 daily report the stage feeds (see section 7); "(not a report row)" stages count toward no row of the Cluster 1&2 report (only `LinkedIn sent` is in that situation).

#### Company Ops Cluster 1 India (pipeline id `default`, 33 stages, 1135 deals)

| order | stage id | label | prob | isClosed | meaning | report row | deals now |
|---|---|---|---|---|---|---|---|
| 1 | 4327110345 | LinkedIn sent | 0.03 | false | live - entry | (not a report row) | 0 |
| 2 | 4327110346 | Cold called assigned | 0.05 | false | live - entry | Leads Assigned | 33 |
| 3 | 4326787818 | LinkedIn connected | 0.05 | false | live | LinkedIn Connected | 38 |
| 4 | 4326787819 | No pickup | 0.05 | false | live | No Pickup / Callback | 492 |
| 5 | 4326787820 | Retired: Callback +1 day (use No pickup + task instead) | 0.05 | false | RETIRED stage (do not write) | No Pickup / Callback | 51 |
| 6 | 4326787821 | Replied | 0.12 | false | live | Replied | 26 |
| 7 | 4327110347 | 1st interest sent | 0.2 | false | live | 1st Interest | 93 |
| 8 | 4327110348 | 1st interest follow up | 0.2 | false | live | 1st Interest | 1 |
| 9 | 4327110349 | Discovery call | 0.35 | false | live | Discovery Call Scheduled | 29 |
| 10 | 4326787822 | Call rescheduled | 0.35 | false | live | Discovery Call Scheduled | 7 |
| 11 | 4327110350 | One pager requested | 0.45 | false | live | One Pager Requested | 6 |
| 12 | 4326787823 | One pager follow up | 0.45 | false | live | One Pager Follow Up | 1 |
| 13 | 4327110351 | One pager received | 0.55 | false | live | One Pager Received | 0 |
| 14 | 4327110352 | LOI signed | 0.7 | false | live | LOI Signed | 0 |
| 15 | 4327110353 | Contract signed | 0.85 | false | live | Contract Signed | 0 |
| 16 | 4326787824 | Ops data handover done | 0.9 | false | live | Ops Data Handover | 1 |
| 17 | 4327110354 | Payment initiation | 0.95 | false | live | Payment Initiation | 0 |
| 18 | 4326787825 | Closed/Won | 1.0 | true | WON (terminal) | Closed/Won | 0 |
| 19 | 4326787826 | Dead: Cold Call / Wrong Fit | 0.0 | true | DEAD (terminal) | Dead | 39 |
| 20 | 4326787827 | Dead: Cold Call / Wrong Number | 0.0 | true | DEAD (terminal) | Dead | 175 |
| 21 | 4326787828 | Dead: Cold Call / Not Interested | 0.0 | true | DEAD (terminal) | Dead | 110 |
| 22 | 4327110355 | Dead: Cold Call / No Pickup | 0.0 | true | DEAD (terminal) | Dead | 8 |
| 23 | 4326787829 | Dead: Replied / Not Interested | 0.0 | true | DEAD (terminal) | Dead | 22 |
| 24 | 4326787830 | Dead: 1st Interest / No Response | 0.0 | true | DEAD (terminal) | Dead | 0 |
| 25 | 4327110356 | Dead: 1st Interest / Not Interested | 0.0 | true | DEAD (terminal) | Dead | 2 |
| 26 | 4327110357 | Dead: Discovery Call / No Show | 0.0 | true | DEAD (terminal) | Dead | 0 |
| 27 | 4326787831 | Dead: Discovery Call / Rejected by LH2 | 0.0 | true | DEAD (terminal) | Dead | 0 |
| 28 | 4326787832 | Dead: Discovery Call / Not Interested | 0.0 | true | DEAD (terminal) | Dead | 1 |
| 29 | 4326787833 | Dead: One Pager / Not Received | 0.0 | true | DEAD (terminal) | Dead | 0 |
| 30 | 4327110358 | Dead: One Pager / Low Data Quality | 0.0 | true | DEAD (terminal) | Dead | 0 |
| 31 | 4327110359 | Dead: LOI / Pricing Not Agreed | 0.0 | true | DEAD (terminal) | Dead | 0 |
| 32 | 4326788794 | Dead: LOI / Contractual Not Agreed | 0.0 | true | DEAD (terminal) | Dead | 0 |
| 33 | 4326788795 | Dead: Email Campaign / Branch Retired | 0.0 | true | DEAD (terminal) | Dead | 0 |

#### Company Ops Cluster 2 India (pipeline id `2464812771`, 33 stages, 6440 deals)

| order | stage id | label | prob | isClosed | meaning | report row | deals now |
|---|---|---|---|---|---|---|---|
| 0 | 4307081975 | LinkedIn sent | 0.03 | false | live - entry | (not a report row) | 2015 |
| 1 | 4275264191 | Cold called assigned | 0.05 | false | live - entry | Leads Assigned | 776 |
| 2 | 4307081976 | LinkedIn connected | 0.05 | false | live | LinkedIn Connected | 983 |
| 3 | 4307080899 | No pickup | 0.05 | false | live | No Pickup / Callback | 732 |
| 4 | 4307080900 | Retired: Callback +1 day (use No pickup + task instead) | 0.05 | false | RETIRED stage (do not write) | No Pickup / Callback | 98 |
| 5 | 4080987863 | Replied | 0.12 | false | live | Replied | 104 |
| 6 | 4275264192 | 1st interest sent | 0.2 | false | live | 1st Interest | 85 |
| 7 | 4080987864 | 1st interest follow up | 0.2 | false | live | 1st Interest | 56 |
| 8 | 4111134402 | Discovery call | 0.35 | false | live | Discovery Call Scheduled | 17 |
| 9 | 4275264193 | Call rescheduled | 0.35 | false | live | Discovery Call Scheduled | 1 |
| 10 | 4132224745 | One pager requested | 0.45 | false | live | One Pager Requested | 9 |
| 11 | 4132224746 | One pager follow up | 0.45 | false | live | One Pager Follow Up | 22 |
| 12 | 4080987866 | One pager received | 0.55 | false | live | One Pager Received | 11 |
| 13 | 4275264194 | LOI signed | 0.7 | false | live | LOI Signed | 7 |
| 14 | 4080987873 | Contract signed | 0.85 | false | live | Contract Signed | 0 |
| 15 | 4307080901 | Ops data handover done | 0.9 | false | live | Ops Data Handover | 5 |
| 16 | 4307080902 | Payment initiation | 0.95 | false | live | Payment Initiation | 0 |
| 17 | 4307080903 | Closed/Won | 1.0 | true | WON (terminal) | Closed/Won | 0 |
| 18 | 4307080904 | Dead: Cold Call / Wrong Fit | 0.0 | true | DEAD (terminal) | Dead | 336 |
| 19 | 4307080905 | Dead: Cold Call / Wrong Number | 0.0 | true | DEAD (terminal) | Dead | 291 |
| 20 | 4307080906 | Dead: Cold Call / Not Interested | 0.0 | true | DEAD (terminal) | Dead | 193 |
| 21 | 4307080907 | Dead: Cold Call / No Pickup | 0.0 | true | DEAD (terminal) | Dead | 50 |
| 22 | 4080987878 | Dead: Replied / Not Interested | 0.0 | true | DEAD (terminal) | Dead | 503 |
| 23 | 4275264195 | Dead: 1st Interest / No Response | 0.0 | true | DEAD (terminal) | Dead | 8 |
| 24 | 4307080908 | Dead: 1st Interest / Not Interested | 0.0 | true | DEAD (terminal) | Dead | 0 |
| 25 | 4080987880 | Dead: Discovery Call / No Show | 0.0 | true | DEAD (terminal) | Dead | 9 |
| 26 | 4275264196 | Dead: Discovery Call / Rejected by LH2 | 0.0 | true | DEAD (terminal) | Dead | 5 |
| 27 | 4275264197 | Dead: Discovery Call / Not Interested | 0.0 | true | DEAD (terminal) | Dead | 2 |
| 28 | 4132224747 | Dead: One Pager / Not Received | 0.0 | true | DEAD (terminal) | Dead | 3 |
| 29 | 4275264198 | Dead: One Pager / Low Data Quality | 0.0 | true | DEAD (terminal) | Dead | 1 |
| 30 | 4307080909 | Dead: LOI / Pricing Not Agreed | 0.0 | true | DEAD (terminal) | Dead | 0 |
| 31 | 4275264199 | Dead: LOI / Contractual Not Agreed | 0.0 | true | DEAD (terminal) | Dead | 0 |
| 32 | 4275316410 | Dead: Email Campaign / Branch Retired | 0.0 | true | DEAD (terminal) | Dead | 118 |

Live-vs-doc stage observations: `LinkedIn sent` holds 2,015 C2 deals and 0 C1 deals; the LinkedIn branch was deleted by v5 (2026-09-15) and restored the same day (`opsdata/restore_linkedin_branch.py`, new stage ids; 2,999 of 3,007 moved deals went back). Old pre-restoration ids 4080987861 (LinkedIn sent) and 4132224744 (LinkedIn connected) survive only in deal history (`DELETED_STAGE_LABEL` in `dashboard/build_ops_dashboard.py`). Pipeline version history in `docs/OPSDATA_PIPELINE.md`: v1 -> v2 (2026-08-07) -> v3 (2026-08-13) -> v4 (2026-09-08, email branch retired, `Contract signed` briefly closed-won) -> v5 (2026-09-15, "Ops-Data Supply Funnel", cold-calling = main entry, `Contract signed` live again, closing tail restored) -> LinkedIn restored (33 stages). Cluster 1 was cloned from the single "Company Ops Data" pipeline (C2 pipeline createdAt 2026-07-31 - it was the original single "Company Ops Data" pipeline; C1 is the `default` pipeline repurposed; daily snapshots for both start 2026-09-09; the Cluster 2 bulk migration date is 2026-09-15 per `cluster_cutoff_report.py`).

## 3. Owners (live `GET /crm/v3/owners`)

userId == owner id for all owners here (do not rely on it; build the map via the `userId` field). Role-in-funnel is taken from `tam/leadgen/outreach.py` (ROUTING/CLUSTERS), `opsdata/outflo_pull_push.py`, `opsdata/daily_fullfunnel_report.py` and `docs/Cluster2_Report_Metric_Definitions.md`; this portal has NO role field and the repo has no Pod Lead/Pod Head table for it (those roles belong to the main portal).

| owner id | name | email | active | role in funnel | C1 deals | C2 deals |
|---|---|---|---|---|---|---|
| 168015679 | Amisha Pujari | amisha.pujari@lh2.ai | yes | Caller, Cluster 1 rep (TAM routing C1); also holds 460 C2 deals | 536 | 460 |
| 168609107 | Manit Rastogi | manit.rastogi@lh2.ai | yes | Caller, Cluster 1 rep (TAM routing C1) | 480 | 0 |
| 168341981 | Vaishnavi Kannan | vaishnavi.kannan@lh2.ai | yes | Caller, Cluster 2 rep (TAM routing C2; adtech/manufacturing discovery push owner "OWNER_VAISHNAVI") | 0 | 803 |
| 168015618 | Tanisha Sharma | tanisha.sharma@lh2.ai | yes | Caller, Cluster 2 rep (TAM routing C2) | 0 | 1,259 |
| 168340300 | Anuj Chahar | anuj.chahar@lh2.ai | yes | not referenced in code; holds 13 C1 deals | 13 | 0 |
| 166909452 | Anu Meena | anu.meena@lh2.ai | yes | OutFlo LinkedIn sender account (recorded in `outflo_assigned_account`); in report TRACK_EMAILS; 6 C2 deals | 0 | 6 |
| 96316911 | Kartik Pillai | kartik.pillai@lh2.ai | ARCHIVED | Owner of ALL OutFlo-imported deals/contacts (`OWNER_KARTIK`, "only seat in this portal" at the time) and the Gmail cold-email campaign mailbox; still owns 3,912 C2 deals | 0 | 3,912 |
| 168828917 | R Kalyan | r.kalyan@lh2.ai | ARCHIVED | Joined Cluster 1 on 2026-09-30 per outreach.py comment; owns 106 C1 deals | 106 | 0 |
| 98906502 | Harsha A | harsha.a@lh2.ai | ARCHIVED | not referenced; 0 deals | 0 | 0 |

No unowned deals in either pipeline. Handover model (Pod Lead/Pod Head) from context.md does NOT exist in this portal's docs - callers carry deals; Discovery call is described in OPSDATA_PIPELINE.md as the "Handover: analyst -> Pod Lead" stage, but no Pod Lead/Head owners are present in this portal.

## 4. Custom properties the repo relies on (live, `GET /crm/v3/properties/{obj}`)

Counts: deals 257 properties (78 custom), contacts 406 (5 custom), companies 253 (8 custom). Property sets are created by `opsdata/properties_sync.py` (group `opsdata_procurement`) and `tam/leadgen/hubspot_setup.py` (adds `segment` options + `tam_source`). Writing an enum value that is not an option fails with `INVALID_OPTION`; PATCH the property options first.

### Deals - group `opsdata_procurement` (created by properties_sync.py; contract in docs/OPSDATA_PIPELINE.md)
- dates: `li_msg1_date`, `li_msg2_date` ("Msg 2 / Cold Email Sent"), `cold_call_assigned_date`, `interest1_sent_date`, `call_rescheduled_date`, `one_pager_sent_date`, `loi_signed_date`, `sample_requested_date`, `token_paid_date`, `data_delivery_timeline` (all type date)
- datetimes: `gmeet1_date`, `replied_at`, `outflo_last_action_at`, `email_sent_at`
- text: `gmeet1_link`, `one_pager_results`, `systems_of_record`, `migration_volume`, `outflo_campaign`, `outflo_lead_id`, `outflo_assigned_account`, `email_campaign`
- numbers: `number_of_datasets`, `migration_record_count`, `token_amount`, `sample_quality_score`
- enums: `gmeet1_outcome` (Proceeds, Wrong Fit, No Show, Privacy Concerns), `dataset_customization_required` (Yes/No), `outflo_status` (Connected, Replied, Request Sent), `email_status`, `internal_eval_result`, `sample_format`; checkboxes `sample_pii_flags` (8 opts), `ops_data_types` (11 opts)
- v4/v5 retired-tail properties are kept but no longer written: sample_*, token_*, migration_*, number_of_datasets, dataset_customization_required.

### Deals - group `dealinformation` (custom, from earlier builds / TAM / scoring)
- identity/join keys: `lh2_domain` (unique key), `linkedin_url`, `poc`, `metadata_link`
- classification: `segment` (enum, 17 options: Healthcare & Medtech, Fintech, Proptech, Construction & Real Estate, Adtech, Gaming & Sports, Enterprise SaaS/IT, Mobility Tech, Manufacturing, E-commerce & Marketplace, FoodTech, EdTech, Market Research, Fintech (Delhi), Healthcare&Medtech_Hyd, Healthcare&Medtech_Mumbai, Fintech (Hyderabad)), `tam_source` (enum, 14: tam:adtech, tam:construction, tam:ecommerce, tam:edtech, tam:enterprise_saas_it, tam:fintech, tam:foodtech, tam:gaming_sports, tam:healthcare_medtech, tam:manufacturing, tam:market_research, tam:mobility, tam:proptech, manual), `lead_source` (enum, 6: Outflo Outreach ( Startups ), Cold Call ( Company Ops ), Cold Email ( Company Ops ), Whatsapp, External Leads, financialservices_mumbai), `lead_type` (enum, only Ed_tech), `communication_channel` (Cold Call, WhatsApp Outreach), `interest_gauge`, `prescreen_result`
- scoring: `opsdata_fit` (0-100, number; **0 deals populated in either pipeline**), `peak_employees`, `years_operating`, `ops_intensive` (bool), `distress_score`, `distress_tier`
- call/meeting logging: `gmeet_date`, `gmeet_link`, `gmeet_outcome` (older duplicates of the gmeet1_* set), `li_msg1_sent_at`, `li_msg2_sent_at`, `cold_email_sent_at`, `onepager_sent_at`, `onepager_received_at`, `sample_requested_at`, `sample_received_at`, `followup_count`
- commercial/delivery (all effectively unused): `cost` ("Deal Cost (USD)"; **0 deals populated**; the price field for this flow, never `amount`), `deal_value_range`, `data_volume_gb`, `record_count`, `data_format`, `data_date_range_start/end`, `completeness_pct`, `internal_tools_count`, `sop_docs_available`, `pii_present`, `pii_scrub_required`, `compliance_notes`, `data_migration_done_at`, `payment_initiated_at`, `token_paid_at`
- Populated fill rates (live, C1 / C2): `lead_source` 797/5,797; `tam_source` 187/98; `segment` 678/891; `linkedin_url` 0/4,024; `outflo_lead_id` 0/3,774; `replied_at` 0/299; `gmeet1_link`, `cost`, `opsdata_fit` 0/0.

### Contacts (custom): `contact_role`, `linkedin_url` ("LinkedIn URL (LH2)"), `next_step`, `outreach_status` (Not Contacted, LinkedIn Sent, Followed Up, Replied, Meeting Booked, Not Interested), `spoc_type` (Primary, Secondary). Standard `phone`, `email` used; phones must be +91 (hard rule).

### Companies (custom): `incorp_year`, `lh2_domain` (unique key), `ops_intensive`, `opsdata_fit`, `peak_employees`, `pipeline_source`, `segment` (plain text on companies), `years_operating`.

### Note on lead_source
In THIS portal `lead_source` has 6 options (not the main portal's 26/5-source universe). docs/OPSDATA_PIPELINE.md says it is "plain text" but live it is an **enumeration**. Distribution (C1/C2): Outflo Outreach ( Startups ) 0/3,774; Cold Call ( Company Ops ) 797/1,718; Cold Email ( Company Ops ) 0/126; Whatsapp 0/59; financialservices_mumbai 0/120; External Leads 0/0. Cross-portal reporting needs a mapping (the three portals use different lead_source vocabularies).

## 5. Object counts (live)

Deals per pipeline/stage are in the stage tables in section 2 (sums reconcile: C1 1,135, C2 6,440; total 7,575). Condensed:

| | C1 `default` | C2 `2464812771` |
|---|---|---|
| total deals | 1,135 | 6,440 |
| live (non-dead, non-won) | 778 | 4,921 |
| dead (all `Dead:` stages) | 357 | 1,519 (118 of them the retired email-branch park) |
| Closed/Won | 0 | 0 |
| deepest live stages | Ops data handover done 1, One pager requested 6, Discovery call 29, Call rescheduled 7 | LOI signed 7, Ops data handover done 5, One pager received 11, Discovery call 17 |

Segment mix (C1): EdTech 155, Healthcare&Medtech_Mumbai 131, Healthcare & Medtech 94, E-commerce & Marketplace 76, Mobility Tech 76, FoodTech 67, Healthcare&Medtech_Hyd 41, Gaming & Sports 38 (sum 678). Segment mix (C2): Fintech 189, Manufacturing 177, Enterprise SaaS/IT 117, Proptech 107, Market Research 107, Fintech (Hyderabad) 98, Fintech (Delhi) 54, Adtech 42 (sum 891). Most C2 deals (4,024 with linkedin_url / 3,774 OutFlo) carry no `segment`.

Live-deal count check vs local snapshot `crm_mirror/data/snapshots/full_funnel_cluster{1,2}_2026-10-03.json`: identical stage counts (C1 1,135; C2 6,440) - snapshots are consistent with live.

## 6. Local files / DBs / sheets that act as "databases"

HubSpot is the declared source of truth (README.md, context.md rule 3, OPS_DASHBOARD_METRIC_SPEC). Everything local is a mirror, a working set, or an audit log. Sizes from `legacy/companyOps`.

| Path | What | Relation to HubSpot |
|---|---|---|
| `tam/data/tam.sqlite3` (640,278,528 B; plus `.bak-20260929-140234` 363 MB, `-shm`, empty `-wal`, empty `tam.db`) | THE big DB: multi-category TAM pipeline, schema v5, DDL in `tam/leadgen/db.py` | Upstream lead supply. Mirror/ledger of what was pushed to HubSpot (`crm_pushes`), NOT a mirror of HubSpot. See section 8. |
| `crm_mirror/data/snapshots/full_funnel_cluster{1,2}_YYYY-MM-DD.json` (2026-09-09 .. 2026-10-03, 25 days x 2 clusters) | Daily report snapshots: `dashboard_flow`, `current_state`, `cumulative`, `engaged_deal_ids`, `bootstrap` | Derived from HubSpot; the daily mail renders from these, not live. Missing days silently shrink Roll7/Prev7. |
| `crm_mirror/data/backfill_checkpoints/cluster{1,2}_history.json` (70 KB / 1.9 MB) | Per-deal stage histories cached by `full_funnel_backfill_*.py` | HubSpot-derived cache (keys are deal ids) |
| `audit/` (28 files, gitignored) | JSON/CSV audit dumps of every mutating script: `pipeline_v*`, `deals_v2..v6`, `outflo_pull_push_*`, `gmail_pull_push_*`, `properties_sync_*`, `restore_linkedin_branch_*`, `pre_v4_snapshot` | Undo maps / review trail (contain deal names, lead_source - business data) |
| `exports/` (14 files) | `cluster2_cutoff_report.html`, `cluster2_cutoff_snapshot.json` (generated 2026-09-30), COBOL/TheirStack lead CSVs, `edtech_campaign_overlap.csv` | Outputs/one-offs |
| `data/imports/` + `data/exports/` | Hand-delivered spreadsheets: `Company Ops scrap100*.xlsx` (2), `RAT_CAD_Tracks1_2_Unassigned.xlsx` (+ duplicate in `_duplicates/`), `Fintech and Financial Services.csv`, an OutFlo `campaign-leads-export-*.csv` (Sep 16); exports: `shutdown_companies_*.csv`, `fintech_classification_*.csv`, `MobilityTech.csv`, `salesnav_final_leads.csv`, `Company Ops scrap100_enriched*.csv` | Inputs/outputs of enrichment, not mirrors |
| repo root CSVs: `campaign-leads-export-37cc2be1-...-2026-09-30.csv` (216 KB), `coding_coldcall_lamiya50_yuktha48_2026-09-29.csv` (40 KB; names Lamiya/Yuktha - MAIN-portal owners, so it is a stray from the main repo/"coding" track) | OutFlo export / call list | Inputs |
| `*_discovery/` (adtech, manufacturing, mobility, construction), `apollo_proptech/`, `apollo_test/`, `pipeline/` | Per-category discovery runs: JSON outputs (`output/*_pushed.json`, `*_hubspot_ground_truth.json`, `*_contacts_detail.json`...), `raw_maps/`, `raw_apollo/`, `seen/` dedupe sets; `enrich_and_push.py` pushes straight to HubSpot (C2 for adtech/manufacturing owner Vaishnavi, C1 for mobility owner Amisha) | Pre-TAM lead-gen history; superseded by `tam/`. `*_ground_truth.json` files are local copies of HubSpot state at one moment. |
| `dashboard/ops_dashboard_data.json` (gitignored, absent in copy) / `dashboard/index.html` | Static dashboard over HubSpot, built by CI to GitHub Pages | Read-only derived |
| `daily_fullfunnel.html` (60 KB, 2026-09-16, stale) | Last output of `opsdata/daily_fullfunnel_report.py` (gitignored on purpose) | Derived |
| `claudeContext/` (13 MB: `conversations-000.zip`, `projects-000.zip`, `memories-000.zip`, `apoorv_project_export.zip`, `frames`, `light_metadata`, `result.md`) | Claude.ai account export, NOT a database. **Not read** (out of scope) - flag for the chat-context consolidation task | n/a |
| `cf.tgz`, `cloudflared` (39 MB binary) | Cloudflare tunnel binary for the SignalHire/Apollo webhook receiver (`tam/leadgen/sh_receiver.py`) | tooling |
| Google tokens in repo root | `.gmail_token.json` (Kartik's mailbox, scope gmail.readonly per OPSDATA_PIPELINE.md; keys: authorized_as, client_id, client_secret, refresh_token, scope), `bhanu_gmail_token.json` (gmail.send), `sheets_token.json` (scopes spreadsheets + drive.readonly), `client_secret*.json` (installed-app OAuth client, project 107044297252) | Already copied to monolith `secrets/companyops_*` |

No Google Sheet ID or spreadsheet URL is referenced anywhere in the companyOps code/docs, and no .py file in the repo calls the Sheets API; `sheets_token.json` (write scope `spreadsheets` + `drive.readonly`) is only mentioned in README.md (docs/research/scrapeV2.md mentions Google Sheets only as a public Colorado WARN data source, unrelated) - treat as high-privilege and probably used interactively in earlier sessions. No `.xlsx` is a live DB; the only xlsx files are 3 hand-delivered imports.

## 7. The daily funnel reports this repo produces

There are THREE overlapping report generators plus one dashboard. The authoritative recurring one (per docs and snapshot dates) is #1.

### 7.1 Company Ops Cluster 1 & 2 India - Daily Report (the real daily report)
- Two-step: `opsdata/full_funnel_report.py` (writes `crm_mirror/data/snapshots/full_funnel_<cluster>_<date>.json`, hits HubSpot) then `opsdata/full_funnel_dashboard_mail.py [--send] [--to X]` (zero HubSpot calls; renders HTML from the last 14 days of snapshots; subject `Company Ops Cluster 1 & 2 India - Daily Report - <date>`). Default recipient `--to bhanu.enamala@lh2.ai`. Sends via Gmail API with `bhanu_gmail_token.json` (gmail.send). Do NOT send anything.
- Alternate (older) path: `opsdata/company_ops_cluster_report_send.py [--send]` reads/writes an **n8n Cloud Data Table** (id `cieuZhQTdDCB2sgP`) using env `n8n_cloud_url` / `n8n_cloud_api_key` - those keys are not in any .env we hold, so it cannot run; it also uses a slightly different row set (`Cold Lead Assigned`, `Outreach Sent`, `One Pager` merged). It hardcodes recipient bhanu.enamala@lh2.ai. Treat as legacy backup of an n8n workflow "Company Ops Cluster 1 & 2 India - Daily Report".
- Clusters: `cluster1` = pipeline `default`, `cluster2` = `2464812771`.
- 15 report rows (label <- stages): Leads Assigned <- `Cold called assigned` (ANY sourceType counted); LinkedIn Connected <- `LinkedIn connected`; No Pickup / Callback <- `No pickup` + `Retired: Callback +1 day ...`; Replied <- `Replied`; 1st Interest <- `1st interest sent` + `1st interest follow up`; Discovery Call Scheduled <- `Discovery call` + `Call rescheduled`; One Pager Requested; One Pager Follow Up; One Pager Received; LOI Signed; Contract Signed; Ops Data Handover <- `Ops data handover done`; Payment Initiation; Closed/Won; Dead <- every stage starting `Dead`. `LinkedIn sent` is deliberately not a row.
- Columns: Inception (cumulative ENTRIES, never decreases), Today, Yest, Roll7 (sum of 7 daily snapshot flows), Prev7, Chg%. KPI tiles: WON TO DATE (=cumulative Closed/Won), 7-DAY LEADS ENGAGED (set UNION of engaged deal ids over 7 snapshots), DAILY LEADS ENGAGED. Also a "current_state" table per stage.
- How stage dates are read: candidates = deals in pipeline with `hs_lastmodifieddate >= today 00:00` narrowed to `hs_v2_date_entered_current_stage` in today (IST); then `GET /crm/v3/objects/deals/{id}?propertiesWithHistory=dealstage` for each, counting history entries dated today (IST). Row count rule: entry row counts any source; every other row only `sourceType == CRM_UI`. "Engaged" = deal with >=1 CRM_UI stage change that day. Cumulative = yesterday cumulative + today's flow; day-1 bootstrap uses live counts (entry row = all deals in pipeline). **Window must end today** (no per-stage `hs_date_entered_*` props exist in this portal, only the latest-entry prop), so a missed day can never be recomputed from this script.
- Notes are NOT used in this report (stage history only).
- Schedule: **none in the repo** (no workflow for it). It was run by hand / local scheduler (docs mention an 18:30 IST cutoff; context.md says the only sanctioned recurring mail is the 18:30 IST report). Snapshots exist 2026-09-09 -> 2026-10-03 (daily, incl. weekends; 26-27 Sep were backfilled by `full_funnel_backfill_day.py` / `_range.py`). Snapshot 2026-10-04 is not yet present (today).
- Output path: `crm_mirror/data/snapshots/*.json`; email HTML is built in memory (and printed/sent), not saved.
- Definitions doc: `docs/Cluster2_Report_Metric_Definitions.md` (+ PDF generated by `opsdata/cluster_report_definitions_pdf.py`), methodology `docs/methodology/company_ops_funnel_report_methodology.md`. Known limitation in the doc: Chg% is misleading when Roll7/Prev7 cover different numbers of days.

### 7.2 Cluster cutoff report (correction of 7.1 for Cluster 2)
`opsdata/cluster_cutoff_report.py --cluster cluster2 --since 2026-09-15 --migration-date 2026-09-15 [--cache F] [--out-json F] [--out-html F] [--email X]`. Rebuilds the same 15-row table from FULL per-deal stage history (6,177 histories, ~10 min, 10 threads) so it can exclude (a) deals that joined the cluster before the cutoff and (b) the bulk migration (joined on cutoff day having been created before it). Membership = first time a deal sat in one of the cluster's own stages (not createdate). Output present: `exports/cluster2_cutoff_report.html`, `exports/cluster2_cutoff_snapshot.json` (generated 2026-09-30 18:05 IST: 6,177 deals total, 2,004 counted, 4,173 excluded = 3,006 bulk migration + 1,167 joined before cutoff; engaged today 119, 7d 650). This was Bhanu's explicit instruction of 2026-09-30 ("ignore the bulk migration"). Unified report decision needed: 7.1 inception numbers include the migration; 7.2 do not.

### 7.3 Per-person "leads engaged" report (older, sibling methodology)
`opsdata/daily_fullfunnel_report.py [--week]` -> writes `daily_fullfunnel.html` / `weekly_fullfunnel.html` in repo root (gitignored). Ported from the main portal's `daily_fullfunnel_mail.py`. Three layers: stage KPIs (METRICS: li_sent, li_conn, att "Calls attempted", conn "Calls connected", interest_sent, disc_fixed, disc_done, op_requested, op_received, eval_done, loi, neg_done, contract, handover, payment, won), note-only activity via `dashboard/ops_note_rules.classify()` (BUCKET_MAP suppresses buckets that restate a KPI), and leads-engaged = union. TRACK_EMAILS = kartik.pillai, anu.meena, tanisha.sharma, amisha.pujari; daily TARGET = 100 leads engaged per person. Compliance checks: Discovery-call-or-later deals missing `gmeet1_link`/`gmeet_link`, and One-pager-received-or-later deals missing `one_pager_results`. **Broken now**: looks up pipeline label "Company Ops Data" (gone) and only handles one pipeline. `full_funnel_dashboard_mail.py` is the maintained one.

### 7.4 Ops dashboard (not a mail)
`dashboard/build_ops_dashboard.py` -> `ops_dashboard_data.json` -> `dashboard/index.html`, deployed to GitHub Pages by `.github/workflows/deploy_ops_dashboard.yml`. Reads pipeline `2464812771` only (`OPS_PIPELINE_ID` env override) - so Cluster 1 is not on the dashboard. Counting rule: "ever-reached" distinct deals per stage by IST day of entry, plus muted "N now"; NO sourceType filter (this deliberately differs from 7.1/7.3, which use CRM_UI). Note-derived metrics `interestSent` and `onePagerReceived` via `ops_note_rules.py` (first-match-wins; rule order is load-bearing). Metric spec: `docs/OPS_DASHBOARD_METRIC_SPEC.md`.

### 7.5 Schedules / workflows (`.github/workflows/`)
- `deploy_ops_dashboard.yml`: workflow_dispatch + cron `9,39 4-13 * * *` (every 30 min 09:39-19:09 IST) and `1,11,21,41,51 13 * * *` (18:31-19:21 IST). Needs secret `HUBSPOT_API_KEY` (portal 246897735). Cron density is deliberate (GitHub drops ~85% of scheduled runs). Read-only, `cancel-in-progress`.
- `outflo-sync.yml`: workflow_dispatch(dry_run input) + cron `3,17,33,49 5 * * *` (~10:30 IST). Needs `HUBSPOT_API_KEY`, `OUTFLO_API_KEY`. Runs `python3 opsdata/outflo_pull_push.py --apply` (WRITES to HubSpot; currently would abort on label "Company Ops Data"); uploads `audit/*.json`. The monolith must NOT run this as-is.
- Local alternatives named in docs: cron / Windows `schtasks` for OutFlo (OUTFLO_RUNBOOK.md). A Windows scheduled task `LH2 VCF lead notifier` is referenced (main-portal; disabled) - not applicable on this Mac.

### 7.6 Recipients (not exercised)
bhanu.enamala@lh2.ai only (default of `full_funnel_dashboard_mail.py --to`, hardcoded in `company_ops_cluster_report_send.py`). `send_csv_mail.py`, `send_cobol_list.py`, `full_funnel_backfill_*` also send via Gmail. context.md: the 18:30 IST report to the Pod Head is the only sanctioned recurring mail (main portal rule).

## 8. TAM pipeline (tam/) and how it feeds HubSpot

Package `tam/leadgen` (CLI `python -m leadgen.cli <cmd> --category <key>`), config `tam/config.yaml`, 13 category configs in `tam/categories/*.yaml`, briefs in `TAMbuildScripts/cluster 1/` (EcommerceTAMbuild, EdtechTAMbuild, healthTechTAMbuild) and `cluster 2/` (Adtech, Construction, EnterpriseSAAS, FintechFinal/Fintech, Manufacturing, MarketResearch); `TAMbuildScripts/AS_BUILT.md` is the architecture source of truth (one package, one DB, 12-13 categories). Own venv (`tam/.venv`, excluded from the copy) and own `.gitignore`. Stages: `discover -> resolve -> crawl -> classify (Claude haiku) -> score -> contact_find -> email -> phone -> crm_push`; a closed bucket vocabulary is recorded for every exit via `funnel.mark()` (`funnel_events`, 224,846 rows).

`tam.sqlite3` live row counts (opened read-only): companies 41,363 (india_hq yes 30,927 / unknown 10,436), company_categories 28,625, classifications 16,905, raw_sources 49,457 (sebi 25,460; apollo_orgs 13,762; sellersjson 2,012; sahamati 1,252; adstxt 1,147; tcf_gvl 1,029; prebid 655; ondc 473; mrsi 179; backfill:* ...), pages 43,523, signals 17,032, funnel_events 224,846, crm_pushes 1,126, cost_ledger 2,634 (apollo 8,266 credits, usd_est 0), signalhire_results 529, stage_runs 31, categories 13, category_sources 15; contacts / outreach_batches / salesnav_import / async_jobs are empty (people search is switched off). Company_categories per category: fintech 11,400; edtech 5,644; healthcare_medtech 4,604; adtech 4,078; manufacturing 1,107; mobility 969; ecommerce 473; market_research 179; proptech 92; construction 79. ICP bucket (where classified): Fit 1,940, Maybe 2,072, Out 2,885. Categories table keys: adtech, construction, ecommerce, edtech, enterprise_saas_it, fintech, foodtech, gaming_sports, healthcare_medtech, manufacturing, mobility, proptech, market_research - each with `hubspot_segment` (the HubSpot `segment` option).

Feed into HubSpot (code `tam/leadgen/outreach.py`): `ROUTING`/`CLUSTERS` - a category config's `cluster:` field decides pipeline/stage/owners:
- Cluster 1 -> pipeline `default`, entry stage `4327110346` (Cold called assigned), reps Amisha 168015679, Manit 168609107, R Kalyan 168828917; categories: edtech, healthcare_medtech, ecommerce (+ foodtech, gaming, mobility landed here historically).
- Cluster 2 -> pipeline `2464812771`, entry stage `4275264191` (Cold called assigned), reps Vaishnavi 168341981, Tanisha 168015618; categories: fintech, adtech, manufacturing, market_research, enterprise_saas_it, proptech, construction.
- Each pushed deal is created with `pipeline`, `dealstage` (entry), `hubspot_owner_id` (least-loaded rep in the cluster), `segment` = the category's `hubspot_segment`, `tam_source` = `tam:<category>`; contacts pass the Indian-number gate (`to_e164_india`: +91, first digit 6-9); `hubspot_setup.preflight` adds missing `segment` dropdown options BEFORE spending credits. `crm_dedupe.py` runs first (local `crm_pushes` + a HubSpot mirror matched on root_domain, then normalised name).
- `crm_pushes` ledger: 1,126 rows, pushed_at 2026-09-28 11:14 .. 2026-09-30 09:06: status `pushed` 1,001 (C2: Fintech 179, Enterprise SaaS/IT 117, Proptech 100, Adtech 11; C1: EdTech 150, Healthcare & Medtech 93, E-commerce 74, FoodTech 67, Gaming & Sports 38, Mobility Tech 3; rows with lowercase segment keys and NULL pipeline - manufacturing 128, mobility 73, adtech 38 - appear to be the older `*_discovery` pushes back-filled into the ledger), `contact_corrected` 22, `removed_duplicate` 5, `removed_off_icp` 25, `removed_too_big` 3. Live `tam_source` deals: 187 in C1 (healthcare_medtech 94, edtech 80, ecommerce 13) and 98 in C2 (fintech 98) - the remaining historical pushes likely pre-date `tam_source` or came from the `*_discovery` scripts (not verified per deal).
- Cost gates in config.yaml (budgets per run/day/month). Currently **Apollo and SignalHire are administratively OFF** ("DO NOT use apollo or signalhire, wait for my command" - Bhanu 2026-09-29). Do not spend credits.
- Cluster 1/2 reports tie to TAM only through `segment`/`tam_source` on the deal; the reports themselves are purely stage-based and ignore category.

## 9. Lead sources into the funnel

1. OutFlo LinkedIn campaigns (names containing "company ops", API `live.outflo.in`, key OUTFLO_API_KEY) -> `opsdata/outflo_pull_push.py` (daily 10:30 IST via GH Action): one deal per company, one contact per person, owner Kartik Pillai (archived), `lead_source` "Outflo Outreach ( Startups )", provenance in `outflo_*` props. Stage ladder LinkedIn sent -> LinkedIn connected -> Replied, never demoted. Code (v5) maps Request Sent AND Connected to `Cold called assigned`; live data after the LinkedIn restoration still has 2,015 at `LinkedIn sent` and 983 at `LinkedIn connected` in C2. Needs re-reconciling before anyone re-enables it.
2. Gmail cold-email (Kartik's mailbox, `.gmail_token.json`) - RETIRED in v4 (gated by `--i-know-this-is-retired`); 124 deals closed to the parked Dead stage.
3. TAM pipeline pushes (section 8) and legacy `*_discovery/enrich_and_push.py` pushes (cold call assigned).
4. Hand/CSV loads (scrap100 sheets, Tracxn-like lists, WhatsApp outreach: `lead_source` Whatsapp 59, financialservices_mumbai 120).

## 10. Hard rules to carry into the unified design

- No Indian mobile (+91, 10 digits, first digit 6-9) -> no push to HubSpot (`crm_mirror/enrich/indian_number.py` is referenced in context.md but is **not in this repo copy**; the in-repo equivalents are `to_e164_india` in `tam/leadgen/outreach.py`). Prefer mobile over landline.
- No automated outbound mail except the single sanctioned daily report.
- HubSpot is source of truth; local DBs are mirrors/ledgers.
- Dry-run by default, `--apply` to write; audit JSON for every mutation.
- Never hardcode a stage id without its pipeline; resolve ids live.
- Reporting days are IST, cutoff 18:30 IST; portal TZ is US/Eastern.
- Deal name = company name (never the person).
- Pagination: search limit max 200, loop `paging.next.after`; batch association reads 100/call; retry 429/502/503/504; archived (498 here) != deleted.
- Renames: per-stage PATCH keeps ids, pipeline PUT matches by label and recreates (loses ids). Reorder with one atomic PUT. Remove a stage only after migrating deals out.
- Activity metrics filter `sourceType == CRM_UI`; procurement/outcome metrics do not (dashboard uses no filter).
- Deal merge produces a NEW object id.
- Deleted/replaced stage ids persist in deal history; keep an alias map.
