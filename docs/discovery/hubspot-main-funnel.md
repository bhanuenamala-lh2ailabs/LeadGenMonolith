# Discovery: `hubspot` repo - Main HubSpot portal (HUBSPOT_KEY_MAIN)

Date of live pulls: 2026-10-04 (read-only: GET plus search POSTs). Source code: `legacy/hubspot` (verbatim copy of `/Users/bhanu/Desktop/hubspot`). No secret values were printed or written.

## 0. Headline

- `HUBSPOT_KEY_MAIN` resolves to portal **246754894** (the repo's "Main" portal). It works (HTTP 200 on every call).
- The three keys in the monolith `.env` resolve to **three distinct portals**. No two keys point at the same portal.
- The "Ops-Data / Kartik" portal named in the hubspot README (246897735, `hubspot_kartik`) is now the key `HUBSPOT_KEY_COMPANYOPS` (the companyOps repo's key). It was moved out of the hubspot `.env`, so hubspot's `.env` only has `hubspot_key`.
- Total funnels (deal pipelines) across the 3 accounts: **5 real funnels** plus one empty default pipeline in RAT.
- The README's pipeline names are stale. Live pipelines in Main are now **"Coding"** (id `default`, formerly "Scraped") and **"CoOps ( Global )"** (id `2425754306`, formerly "Campaign"). The CoOps pipeline was re-staged with a completely different stage set.
- The local mirror `crm_mirror/data/*.json` is badly stale (1,197 deals on 2026-07-29 vs 6,201 live). The real daily-report "database" is `crm_mirror/data/snapshots/*.json`, whose last file is 2026-10-01.

## 1. Portal identity (GET /account-info/v3/details)

| Env var | portalId | Account type | Timezone | Currency | UI domain | What it is |
|---|---|---|---|---|---|---|
| `HUBSPOT_KEY_MAIN` | **246754894** | STANDARD | **Asia/Calcutta** | USD | app-na2.hubspot.com (na2) | hubspot repo "Main" - codebase acquisition (Coding) + CoOps Global |
| `HUBSPOT_KEY_COMPANYOPS` | **246897735** | STANDARD | US/Eastern | USD | app-na2 | hubspot README's "Ops-Data (Kartik)" portal = companyOps repo's portal |
| `HUBSPOT_KEY_RAT` | **247485022** | STANDARD | US/Eastern | USD | app-na2 | RapidActionTeam portal (not mentioned in hubspot README) |

All three are different. For quick reference, live pipelines per portal (counts from search API on 2026-10-04):

| Portal | Pipeline id | Pipeline label | Stages | Deals |
|---|---|---|---|---|
| 246754894 MAIN | `default` | Coding | 28 | 4,821 |
| 246754894 MAIN | `2425754306` | CoOps ( Global ) | 30 | 1,380 |
| 246897735 COMPANYOPS | `default` | Company Ops Cluster 1 India | 33 | 1,135 |
| 246897735 COMPANYOPS | `2464812771` | Company Ops Cluster 2 India | 33 | 6,440 |
| 247485022 RAT | `2575252183` | Rapid Action Team | 30 | 1,236 |
| 247485022 RAT | `default` | Deals pipeline (HubSpot stock, 7 stages) | 7 | 1 |

That is the "~5 funnels": Coding, CoOps ( Global ), Company Ops Cluster 1, Company Ops Cluster 2, Rapid Action Team.

Portal-level totals (search API `total`):

| Portal | Deals | Contacts | Companies |
|---|---|---|---|
| MAIN 246754894 | 6,201 | 8,549 | 6,756 |
| COMPANYOPS 246897735 | 7,575 | 8,165 | 2,641 |
| RAT 247485022 | 1,237 | 1,374 | 844 |

(Sum of Main pipelines 4,821 + 1,380 = 6,201, so no deals sit outside the two pipelines.)

Note on a README oddity: the hubspot README says `docs/LH2_HUBSPOT_OPERATING_CONTEXT.md` "same key, same portal". Confirmed - everything in that doc is about 246754894.

## 2. Deal pipelines and stages (GET /crm/v3/pipelines/deals, Main portal)

Counts below are live deal counts per stage from the search API.

### 2a. Pipeline `default` = "Coding" (4,821 deals, 28 stages)

Live (open) funnel stages, in order. Probability and isClosed are HubSpot's metadata.

| Order | Stage id | Label | Prob | Closed? | Deals | Funnel meaning |
|---|---|---|---|---|---|---|
| 0 | 3992480462 | Cold Call | 0.10 | no | 539 | Never contacted; entry stage (only stage where any sourceType counts in reports) |
| 1 | 4104051404 | No Pickup | 0.12 | no | 1,408 | Dialled, rang out; still being retried. Replaced "Call Attempted" (retired 2026-08-05) |
| 2 | 3992480465 | Interested | 0.20 | no | 386 | Said yes on call |
| 3 | 3992480469 | GMeet Fixed | 0.40 | no | 31 | Meeting booked (gate: `gmeet1_link` must be populated to hand to Pod Lead) |
| 4 | 3992480471 | Script Shared | 0.55 | no | 36 | Our script sent, awaiting their run |
| 5 | 3992480473 | Script Results Received | 0.60 | no | 14 | Output back from them |
| 6 | 4030231231 | Commercial Negotiation | 0.60 | no | 6 | Talking price |
| 7 | 4173850324 | LOI | 0.65 | no | 1 | NEW since README (letter of intent) |
| 8 | 4029653710 | Deal Contract Signed | 0.85 | no | 1 | Signed |
| 9 | 3992480475 | Data Migration Done | 0.90 | no | 0 | Assets transferred |
| 10 | 4036632313 | Metadata Matched | 0.92 | no | 0 | Verified against promise |
| 11 | 4036633274 | Payment Initiation | 0.95 | no | 0 | Paying |

Terminal stages (all `isClosed=true`, prob 0 except Won):

| Order | Stage id | Label | Deals | Kind |
|---|---|---|---|---|
| 12 | 4036632309 | Closed/Won | 16 | WON |
| 13 | 4036632310 | Dead/ColdCall/Not Interested | 467 | dead (answered, said no) |
| 14 | 4036687547 | Dead/ColdCall/WrongFit | 1,268 | dead (screened, never dialled) |
| 15 | 4099250912 | Dead/ColdCall/WrongNumber | 515 | dead (bad number; counts as a dial) |
| 16 | 4102985416 | Dead/ColdCall/NoPickup | 1 | dead (gave up after repeated ring-outs) |
| 17 | 4061963984 | Dead/Interested/NoShow | 27 | dead |
| 18 | 4102985417 | Dead/GMeet/NoShow | 6 | dead |
| 19 | 4103772884 | Dead/GMeet/Cancelled | 0 | dead (called off in advance) |
| 20 | 4036632311 | Dead/GMeet/wrong fit | 39 | dead |
| 21 | 4036687548 | Dead/GMeet/Privacy Concerns | 4 | dead |
| 22 | 4104051405 | Dead/ScriptShared/NoShow | 11 | dead |
| 23 | 4036687549 | Dead/ResultsReceived/WrongFit-Rejected | 31 | dead |
| 24 | 4035313388 | Dead/Negotiation/Pricing | 8 | dead |
| 25 | 4036632312 | Dead/Negotiation/Contractual | 1 | dead |
| 26 | 4326788842 | Dropped - Pretraining | 4 | NEW, not in any report stage map (see section 8 gaps) |
| 100 | 3992480464 | Call Attempted (retired) | 1 | retired, do not write; kept for history |

Naming convention: `Dead/<stage it died at>/<reason>`. A dead deal keeps the funnel level it reached (KPI_DEFINITIONS.md 0-11 scale: Cold Call 0, Call Attempted 1, Interested 2, GMeet Fixed 3, Script Shared 4, Script Results 5, Commercial Negotiation 6, Contract Signed 7, Data Migration 8, Metadata Matched 9, Payment Initiation 10, Closed/Won 11).

Design principle from the repo docs: "the outcome IS the stage" - no stage means "we tried"; calls attempted is derived by summing outcome stages.

### 2b. Pipeline `2425754306` = "CoOps ( Global )" (1,380 deals, 30 stages)

This is the old "Campaign" pipeline, renamed and re-staged for the Company Ops (operating-data) ask to non-Indian targets (UK, Singapore, US, Australia). Docs and some scripts still call it "Campaign".

Open stages:

| Order | Stage id | Label | Prob | Deals |
|---|---|---|---|---|
| 0 | 4317507288 | Cold Lead | 0.05 | 725 |
| 1 | 4317507289 | Communicated | 0.11 | 12 |
| 2 | 4317507290 | No Response | 0.17 | 512 |
| 3 | 4018854633 | Interested | 0.23 | 22 |
| 4 | 4317507291 | VC Fixed | 0.30 | 1 |
| 5 | 4317507292 | VC Rescheduled | 0.36 | 0 |
| 6 | 4317507293 | 1 Pager Shared | 0.42 | 4 |
| 7 | 4317507294 | 1 Pager Output Received | 0.48 | 0 |
| 8 | 4018854637 | Commercial Negotiation | 0.54 | 0 |
| 9 | 4317507295 | LOI Signed | 0.60 | 0 |
| 10 | 4317507296 | Sample Extraction | 0.67 | 0 |
| 11 | 4317507297 | Demand Fulfillment | 0.73 | 0 |
| 12 | 4317507298 | Contract Signed | 0.79 | 0 |
| 13 | 4317507299 | Data Extraction Done | 0.85 | 0 |
| 14 | 4018854642 | Closed/Won | 1.0 (closed) | 0 |

Dead stages (all closed, prob 0), `Dead: <where> / <reason>`:

| Order | Stage id | Label | Deals |
|---|---|---|---|
| 15 | 4317507300 | Dead: Communicated / Invalid Contact | 26 |
| 16 | 4317507301 | Dead: Communicated / Not Interested | 43 |
| 17 | 4317507302 | Dead: Communicated / Wrong Fit | 34 |
| 18 | 4317507303 | Dead: Communicated / No Response | 1 |
| 19 | 4317507304 | Dead: Interested / Not Interested | 0 |
| 20 | 4317507305 | Dead: VC / No Show | 0 |
| 21 | 4317507306 | Dead: VC / Rejected by LH2 | 0 |
| 22 | 4317507307 | Dead: VC / Not Interested | 0 |
| 23 | 4317507308 | Dead: 1 Pager / No Response | 0 |
| 24 | 4317507309 | Dead: 1 Pager Output / Low Quality | 0 |
| 25 | 4317507310 | Dead: Negotiation / Pricing Not Agreed | 0 |
| 26 | 4317507311 | Dead: Negotiation / Contractual Not Agreed | 0 |
| 27 | 4317507312 | Dead: Sample Extraction / Failed | 0 |
| 28 | 4317507313 | Dead: Sample Extraction / Rejected by LH2 | 0 |
| 29 | 4317507314 | Dead: Demand Fulfillment / Requirements Not Met | 0 |

Three stage ids are shared residue from the old Campaign pipeline and look like Coding stage ids to a naive reader: `4018854633` (Interested), `4018854637` (Commercial Negotiation), `4018854642` (Closed/Won). Any query must filter on `pipeline == 2425754306` explicitly (the repo's `coopsglobal_full_funnel_report.py` states this).

Historic incident (from `docs/LH2_HUBSPOT_OPERATING_CONTEXT.md`): stage ids differ between pipelines even for same-named stages; a script that wrote Scraped ids to Campaign deals created 13 orphan contacts. Always resolve stage ids from the deal's own `pipeline`.

## 3. Owners (GET /crm/v3/owners, Main portal)

Seven active owners, plus four archived ones. In this portal `userId == ownerId` for every owner, but the code (correctly) builds a userId->ownerId map because the namespaces are distinct (stage-history `updatedByUserId` is a USER id). `/settings/v3/users` returns 403 (key lacks that scope), so owner `userId` is the only path.

| Owner id | Name | Email | Role per repo docs | Coding deals | CoOps Global deals |
|---|---|---|---|---|---|
| 96574824 | Lamiya Saleem | lamiya.saleem@lh2.ai | GTM Analyst (carries deals through whole funnel by documented exception; "everyone works the whole funnel" since Aug 2026) | 2,095 | 0 |
| 96573782 | Yuktha Anand | yuktha.anand@lh2.ai | GTM Analyst (same exception) | 1,579 | 0 |
| 166322228 | Ishpreet Sood | ishpreet.sood@lh2.ai | Pod Lead (aka Lead Manager): runs GMeet attend -> Script Shared -> Results | 162 | 0 |
| 166262056 | Shobit Gupta | shobit.gupta@lh2.ai | Pod Head (aka Lead Closer): negotiation -> close; recipient of 18:30 daily report | 28 | 8 |
| 168572330 | Shagufta Khan | shagufta.khan@lh2.ai | not in repo docs (new; caller on Coding) | 344 | 0 |
| 168572679 | Akarsh G B | akarsh.g@lh2.ai | not in repo docs (new; owns essentially all of CoOps Global) | 0 | 1,372 |
| 95472647 | Bhanu Enamala | bhanu.enamala@lh2.ai | test mailbox / build account | 0 | 0 |
| (archived) 166420402 | Shreyas Boosnoor | shreyas.boosnoor@lh2.ai | former GTM Analyst | 393 | 0 |
| (archived) 166483631 | Yash Wani | yash.wani@lh2.ai | former GTM Analyst (241 person-named legacy Campaign deals) | 93 | 0 |
| (archived) 98906502 | Harsha A | harsha.a@lh2.ai | not in docs | 112 | 0 |
| (archived) 166322218 | Ashish Ranjan | ashish.ranjan@lh2.ai | former (no role) | 3 | 0 |
| (none) | unowned | | | 12 | 0 |

Stage ownership rule (`crm_mirror/enrich/stage_ownership.py`, set 2026-08-18): follow-up ownership is by stage, not deal owner. Yuktha/Lamiya: Cold Call, No Pickup, Interested, fixing GMeet. Ishpreet: GMeet Fixed (only if `gmeet1_link` set), Script Shared, Script Results Received. Shobit: Commercial Negotiation -> Contract -> Migration -> Payment -> close.

Handover model: GTM Analyst (Cold Call -> GMeet Fixed) -> Pod Lead (meeting -> Script Shared -> Results) -> Pod Head (negotiation, close). Post Aug-2026 (`tasksSchedule.md`): all four people work the complete funnel; reports have no role split.

Hard rule: README says owner ids "main portal" only; do NOT assume the same ids exist in the other two portals.

## 4. Custom properties (live GET /crm/v3/properties/{deals,contacts,companies})

Live totals: deals 229 properties (50 custom), contacts 413 (12 custom), companies 270 (25 custom). Raw JSON of all three property lists was fetched but is not stored in the repo (re-pull with the script pattern in section 11).

### Deals (custom, 50), group `dealinformation` unless noted

| Name | Type/field | Notes |
|---|---|---|
| `lead_source` | enumeration/select, **43 options** | The segmentation dimension. See lead source table below |
| `scraped_type` | enumeration/radio (ITservices, Distressed startups) | Segment marker |
| `lh2_domain` | string | LH2 Domain (unique key) - primary dedup key |
| `lh2_distress_key` | string | Unique key on distress track |
| `linkedin_url` | string | Founder LinkedIn; most reliable join key (OutFlo dedup) |
| `pipeline_source`, `source_tab` | string | provenance |
| `poc` | enumeration/select (no options) | person of contact (legacy) |
| `call_outcome` | enum (Connected - Interested, Connected - Rejected, Connected - Busy, Wrong Number, No Pickup) | |
| `call_notes` | textarea | |
| `call_attempt_count` | number | |
| `callback_datetime`, `followup_date` | datetime/date | |
| `needs_number_lookup`, `calendly_link_sent` | boolean checkbox | |
| `email_version_sent` | enum (M1V1, M1V2, None) | |
| `gmeet1_date`, `gmeet1_link`, `gmeet1_outcome` | datetime / string / enum (Script Run On Call, Client Will Run Later, Rejected, No Show) | `gmeet1_link` is the GMeet gate |
| `script_status` | enum (Not Started, Sent to Client, Running, Results Received) | |
| `script_link`, `script_output_link` | string | defined, never populated (0/930 at README time) |
| `metadata_link` | string | the live script-results link (Drive folder/Sheet URL) |
| `negotiation_notes` | textarea | |
| `cost` (Deal Cost USD), `deal_value_range` | number, string | `cost` vs standard `amount` disagree on 3 of 5 deals carrying both (unreconciled) |
| `loc`, `num_projects`, `pr_count`, `num_repos` | number | codebase sizing (`num_repos` never populated) |
| `number_of_datasets`, `dataset_customization_required`, `data_delivery_timeline`, `ai_lifecycle_stage` | number / bool / datetime / enum (Training, Evaluation, Deployment, Maintenance), group `computer_software_information` | ops-data sizing; essentially greenfield |
| `distress_score`, `distress_tier`, `distress_flags`, `distress_reasons`, `funding_status` | number/string/textarea | Tracxn distress rank |
| `flag_deadpooled`, `flag_website_dead`, `flag_layoff_or_shutdown`, `flag_no_revenue`, `flag_tiny_headcount`, `flag_funding_stale`, `flag_funding_aging`, `flag_negative_profit` | string | individual distress signals |
| `outflo_request_sent`, `outflo_connected_at`, `outflo_replied_at` | datetime | OutFlo LinkedIn outreach timestamps |

Lead source universe (43 options, historical drift). Live deal counts by pipeline (Coding / CoOps):

- Coding: Scraping Algo ( IT services ) 919; LinkedIn Sales Nav ( IT Services ) 893; Linkedin Campaign ( IT Services ) 520; Founder Search ( IT Services ) 424; IT Services ( Pune ) 242; NASSCOM ( IT Services ) 224; Outflo Outreach ( Startups ) 177; Tracxn Sheet ( Startups ) 175; IT Services ( Chennai ) 139; CAD_salesNav 116; distressed_live 112; Boutique Consulting ( Cat 1 ) 107; IT Services ( Hyderabad ) 100; IT Services ( Bangalore ) 95; IT Services ( Mumbai ) 93; Apollo Search ( IT Services ) 70; IT Services ( Delhi ) 60; Scraping Algo ( Startups ) 50; Relevant IT Services 50; COBOL 46; Linkedin Campaign ( Decks ) 41; Outflo Outreach ( IT Services ) 30; Scraped ( IT Services ) 23; Private Codebase Tracker sheet ( IT services ) 22; TechExpoGuj 22; Linkedin Campaign ( Distressed Startups ) 18; LinkedinAdsLead 16; Outflo ( Decks ) 9. Zero: Romania, Upwork, maxheadcount200.
- CoOps ( Global ): UK_Calling 375; Fintech_US 242; Australia_salesNav 200; Cold Call ( Proptech US ) 178; UK_Proptech 148; Singapore_Ecommerce 89; UK_fintech 76; US_EST_Adtech 36; Singapore_Mobility 12; Outflo_Singapore 11; Outflo_Georgia 7; Outflo_Australia 3.

Source universe note: the Operating-Context doc says only 5 sources are "live" (Linkedin Campaign (IT Services), Outflo Outreach (Startups), Scraping Algo (IT services), Scraping Algo (Startups), Tracxn Sheet (Startups)). The portal has grown well past that (43 options) - the doc is stale on this point. Writing a `lead_source` option that does not already exist fails with `INVALID_OPTION`; PATCH the property first.

### Contacts (custom, 12)

`linkedin_url` (label "LinkedIn URL (LH2)"), `contact_role`, `spoc_type` (Primary/Secondary), `call_outcome` (Not Called, Connected, No Answer, Left Voicemail, Interested, Not Interested, Callback Requested, Wrong Contact, Do Not Contact), `call_notes`, `call_date`, `next_step`, `pipeline_source`, `role` (checkbox: Administrator, Decision maker, End user), `preferred_communication_channel` (select, 3 opts), `technical_expertise_area` (checkbox, 4 opts), `ai_project_focus` (checkbox, 4 opts).

### Companies (custom, 25)

`lh2_domain` (unique key), `size_bucket` (1-100 / 100-500 / 500-1000), `headcount_source`, `incorp_year`, `segment`, `pipeline_source`, `pipeline_notes`, `pipeline_synced_at`, `source_tab`, `funding_status`, `eval_results`, `eval_results_received_at`, `distress_score`, `distress_rank`, `distress_tier`, `distress_flags`, `distress_reasons`, and the same eight `flag_*` signals as deals.

Where properties are defined in code ("properties_sync" equivalents): `legacy/hubspot/lh2-pipeline/src/lh2_pipeline/export/hubspot_setup.py` (generated reference: `lh2-pipeline/HUBSPOT_SCHEMA_REFERENCE.md`, which still documents a defunct "Codebase Acquisition" pipeline with 18 stages and says "founded_year" where live has `incorp_year`), `hubspot_sync.py`, `hubspot_client.py`; `crm_mirror/enrich/setup_opsdata_account.py` (creates the Company Ops Data pipeline and properties in the COMPANYOPS portal, writes `opsdata_pipeline_ids.json` -> pipeline `2464812771` = Company Ops Cluster 2 India); `crm_mirror/enrich/lead_source_to_dropdown.py` (lead_source enumeration migration).

## 5. Object counts (live, 2026-10-04)

- Deals 6,201 (Coding 4,821; CoOps Global 1,380). Created between 2026-07-15 and 2026-10-01; latest modification 2026-10-04T12:32Z.
- Contacts 8,549 (first created 2026-07-14, last 2026-10-01).
- Companies 6,756 (first created 2026-07-14, last 2026-10-01; last modified 2026-10-03).
- Stage counts per pipeline are in section 2.

Coding pipeline stage summary: live pool = 539 Cold Call + 1,408 No Pickup + 386 Interested + 31 GMeet Fixed + 36 Script Shared + 14 Results + 6 Negotiation + 1 LOI + 1 Contract = 2,422 live deals; Closed/Won 16; dead 2,378 (467+1,268+515+1+27+6+0+39+4+11+31+8+1), plus 4 Dropped - Pretraining and 1 retired Call Attempted. Check: 2,422 + 16 + 2,378 + 4 + 1 = 4,821.

CoOps Global: live 1,276 (725 Cold Lead + 12 Communicated + 512 No Response + 22 Interested + 1 VC Fixed + 4 One-Pager Shared), dead 104 (26+43+34+1), won 0.

## 6. Local files / DBs / sheets that act as databases, and relation to HubSpot

Rule stated in README and Operating Context: **HubSpot is the source of truth; everything local is a mirror or point-in-time artefact.** Total repo size ~2.0 GB (monolith copy excludes venv/.git).

### 6a. HubSpot mirrors / snapshot store (the important ones)

| Path | What | Freshness |
|---|---|---|
| `crm_mirror/data/deals.json` (865 KB, 1,197 deals), `contacts.json` (1,358), `companies.json` (871) | Full dump via `crm_mirror/sync.py hubspot` (deals + associated contacts/companies, ~24 deal props, 8 contact props, 5 company props) | **Stale: last modified deal 2026-07-29; live has 6,201 deals.** Only 867 Coding (`default`) + 330 CoOps (then "Campaign") deals; lead_source values are old pre-cleanup ("LH2 Distress", "GoodFirms", "LH2 Pipeline"...). Do not trust for counts |
| `crm_mirror/data/index/{by_domain,by_linkedin,by_name,deal_names,hubspot_all_names,sheet_worked_exclude}.json` | Dedup keys (a deal is the dedup unit, not company/contact). Note the repo rule: dedup against deals only | `by_*` from 2026-07-30; `deal_names.json` 2026-08-04 |
| `crm_mirror/data/snapshots/` (188 files) | **The daily-report store.** `full_funnel_<date>.json` (27 files, 2026-09-03..2026-10-01), `full_funnel_coopsglobal_<date>.json` (7 files, 2026-09-23..2026-10-01), `coding_funnel_<date>.json` (16, 2026-09-03..09-17 plus 10-01), `full_funnel_owner_<ownerId>_<date>.json` (9 owner variants x 15 days, 09-03..09-17), `hubspot_2026-07-29.json` | Last snapshot **2026-10-01**; none for 2026-10-02..04 (reports not running; weekend gaps on 09-26/27). Several early dates were backfilled (09-03..09-17 written 09-17/18; 09-19..22 written 09-23) |
| `crm_mirror/holding/*.json,csv` | Frozen migration/audit artefacts (pre/post migration snapshot 2026-08-05, no-Indian-number list, LinkedIn 1000 untouched, tracxn pool, OutFlo parked) | Historic |
| `analysis/weekly/_data/{all_deals,hubspot_index,kpi_history,source_sets,...}.json` | Weekly analysis raw pulls and caches; `kpi_history.json` appends every run | Historic (W31 = Aug 2026) |
| `dashboard_data.json` (361 KB, 965 rows) + `index.html` (root) | Output of `build_dashboard.py`; deployed to GitHub Pages | `generated: 2026-08-06 00:45 IST` - stale locally |
| `crm_mirror/outflo/{leads,leads_ALL_campaigns,pending_distribution}.json` + `snapshots/` | OutFlo (LinkedIn outreach) pull; separate source system | Historic |
| `crm_mirror/enrich/official_mail_state/state.json` (24 KB) | state of the "official email" recheck (SignalHire) for sheet rows | Aug 19 |
| `crm_mirror/enrich/{pushed_*,*_pushed.json,enriched_push_log.json,...}.json` | Per-push audit logs (what was pushed to HubSpot, reversible) | Historic |

### 6b. SQLite databases (local, not HubSpot-derived; upstream of HubSpot pushes)

| Path | Tables (row counts) | Purpose |
|---|---|---|
| `lh2-pipeline/data/pipeline.sqlite` (10 MB) | raw_listings 5,455; companies 5,369; people 1,480; cache 1,391; quota 6; crm_feedback 0; no_domain 0 | GoodFirms-style IT-services directory crawl -> companies -> people; feeds Scraped/Coding pushes |
| `lh2-pipeline/data/gmaps_cache.sqlite` | tiles 44; usage 1 | Google Maps scrape cache |
| `itsvc-tam/itsvc.db` (138 MB) | raw_records/candidates/entities/classifications/captures 61,799 each; site_text 7,824; others 0 | Indian IT-services TAM build (work in progress, Oct 4) |
| `TAMBuildSpecs/_corpus/data/tam_corpus.sqlite` (326 MB) | company 302,963; company_identifier 311,027; company_source 303,087; indicator 652,128; icp_score 301,345; icp_score_component 640,833; source_attempt 6,142; contact 0 | Large TAM corpus (2 verticals, 28 sources, 11 ICP segments) |
| `godown/shutdown-radar/data/radar.sqlite` (14 MB) | candidate 10,957; evidence 11,018; entity 10,816; scored 10,957; prequal 400; run_stat 11 | Shutdown radar (India dead startups / "Deadpool") |
| `godown/founder_id/resolver.sqlite`, `search_ledger.db` | companies 420; calls 2,038 | Founder-lookup resolver |
| `godown/itdirs/state/scrape_state.sqlite` | pages 34 | directory scrape state |

### 6c. CSV / XLSX / Google Sheets

- Hundreds of CSVs/XLSX in `temporary/` (inbox), `exports/` (point-in-time; lead batches, LinkedIn audiences, opsdata exports), `reserve/` (curated untouched pools, 7 MB; see `reserve/README.md`), `_archive/already_in_hubspot/` (DEALS_MASTER.csv, DEALS_NOTES.csv, DEALS_TRANSITIONS.csv, DEALS_ENGAGEMENTS.csv - HubSpot exports), `godown/` (863 MB scratch: gazette PDFs, seed_shutdowns.csv, enrich logs), plus root-level `*_enriched*.csv`, `RAT_CAD_Tracks1_2_Unassigned.xlsx`, `anthropic_poc_*.csv`, `ceo_leads_enriched_*.csv`, `poc_correction_*.csv`. Per README: none of these is a source of truth.
- Google Sheets (accessed via `crm_mirror/enrich/gsheets.py` using the human OAuth token `sheets_token.json`, now `secrets/hubspot_sheets_token.json`). Embedded sheet file ids in scripts: Private Codebase Tracker `1B9in9qK1V3IyjoyjYSqwn0gGRyoP9qVlMBGKKheoEhM` (tabs Pipeline Tracker gid 411280464, IT Services Firms, DB); `1xuoBloYzQOg3j27UQC0t6l9AXjFw9IxAGknlbYNsgOo` (used 4x, enrich_*_sheet.py scripts); also `1bz5sZmG_7mfRefUZld2wErxRfV9AcPNBSS6eXmyxbLE`, `1URrL2rrKo7ECS9UxMI9KRLIf3_1cpQiMahJ4RDoL8xE`, `1SI4GGdrAEvd-pfun2iULhz3yh46h8LYGkGTZCqy9VIE`, `17IkH_Ej1kAPk5q3EAZuiL5QTWg_hwDPfW-td4WKcMD0`, `12BLV3nv1d9Is4UHN113YVhiTBNilHe-9phIMMCEKS4A` (enrich_ceo_leads/amazon/nvidia/openai/telehealth/xai sheet scripts, anthropic_poc.py, source_audit_pull.py - per-company "POC" lead sheets). Sheet contents were not read in this task (read-only scope; no Google calls made).
- Tracxn sheet (startup export, ~6,654 rows ranked by distress score) lives in `crm_mirror/sources/tracxn/`; Deadpool waves in `crm_mirror/sources/deadpool_waves/`.

Relationship summary: Google Sheets / CSVs / SQLite are upstream sourcing and enrichment workspaces that get pushed INTO HubSpot (with dedup against the local index). HubSpot is system of record afterwards; the JSON mirror + snapshot store are derived read-copies (mirror used for dedup, snapshots used for time-series that HubSpot cannot answer).

## 7. Daily funnel report(s) this repo produces

There is **no cron/CI for the reports** in the repo. Only GitHub Actions workflow: `.github/workflows/deploy-dashboard.yml` ("Deploy Sales Dashboard"): `workflow_dispatch` + hourly cron (`0 * * * *` UTC), runs `python build_dashboard.py` (env `HUBSPOT_API_KEY` from GitHub secret), assembles `index.html` + `dashboard_data.json` into GitHub Pages. Reports are run by hand (originally a Windows `schtasks` box; the only schtasks task referenced is the disabled "LH2 VCF lead notifier"). The documented schedule is **18:30 IST** (6:30 pm) daily. Day boundary = IST.

Report scripts (all in `crm_mirror/enrich/`):

1. `full_funnel_report.py` (whole Main portal, both pipelines) - writes snapshot `crm_mirror/data/snapshots/full_funnel_<date>.json` with keys `date, flow, current_state, dashboard_flow, cumulative, engaged_deal_ids`. Run FIRST each day.
2. `full_funnel_dashboard_mail.py` - reads snapshots only (no API) and emits `full_funnel_dashboard.html` + plain text; "Full HubSpot Funnel - <date>". Top strip: Wins to Date / 7-Day Leads Engaged / Daily Leads Engaged. Table of 11 rows (Leads Assigned, No Pickup, Interested, GMeet Fixed, Script Shared, Script Results Received, Commercial Negotiation, LOI, Deal Contract Signed, Closed/Won, Dead combined) with columns Inception-to-Date / Rolling 7 Days / Daily plus vs-previous-7 trend chips. Default `--to` bhanu.enamala@lh2.ai; `--send` required to send.
3. `coopsglobal_full_funnel_report.py` + `coopsglobal_dashboard_mail.py` (+ `coops_fullfunnel_mail.py`, `coopsglobal_backfill_history.py`, `coopsglobal_seed_backfill.py`) - same design scoped to pipeline `2425754306`; snapshots `full_funnel_coopsglobal_<date>.json` (rows: Cold Lead, Communicated, No Response, Interested, VC Fixed, 1 Pager Shared, 1 Pager Output Received, Commercial Negotiation, LOI Signed, Contract Signed, Closed/Won, Dead).
4. `coding_funnel_report.py` - same, filtered to `CODING_SOURCES` (11 coding lead_source values: Apollo Search, Founder Search, LinkedIn Sales Nav, Linkedin Campaign, Outflo Outreach (IT Services), Relevant IT Services, Scraping Algo (IT services), Scraped (IT Services), Private Codebase Tracker, NASSCOM, CAD_salesNav). Spec: "Coding_Funnel_Report_KPIs_and_Stages.pdf" (17 Sep 2026). Snapshots `coding_funnel_<date>.json`. Table columns Today / Yesterday / Roll7 / Prev7.
5. `daily_fullfunnel_mail.py` (per-person daily activity, 6:30 pm): three sections (full-funnel KPIs per person for the four people Ishpreet, Shobit, Lamiya, Yuktha; note-only activity buckets; leads engaged). KPIs: Calls attempted, Calls connected, GMeets fixed, VCs done, Scripts shared, Script results received, Evaluations done, Commercial negotiation, Negotiation calls, Contracts signed, Closed/Won. Supports `--week`, `--date`, `--pipeline`. Needs `--send --to <addr>`; default is print only.
6. `daily_activity_report.py` - library + CLI for the same per-person KPIs/notes; `METRICS` sets are the canonical KPI definitions (explicit stage-set membership, not cascading). Imports note classifiers from `lh2-pipeline/dashboard/note_rules.py`.
7. `daily_report.py` (older; also `lh2-pipeline/dashboard/daily_report.py`) - "Daily 18:30 IST report to the Supply Head" Shobit (`shobit.gupta@lh2.ai`), role tracker tables (per person: GTM Analyst Yuktha/Lamiya, Pod Lead Ishpreet, Pod Head Shobit) built from `dashboard_data.json`; test mailbox `bhanu.enamala@lh2.ai`. Targets include e.g. 40 connected calls/day for callers.
8. `build_dashboard.py` + `index.html` - the web dashboard (both pipelines combined; stage history, CRM_UI only). Spec: `docs/sop/DASHBOARD_METRIC_SPEC.md`.
9. `daily_handover_mail.py` (to Lamiya, Yuktha, Ishpreet), `weekly_report_thisweek.py`, `weekly_segment_report.py` (to ishpreet.sood@lh2.ai), `monthly_wow_report.py` (to shobit.gupta@lh2.ai), `analysis/weekly/weekly_report.py` (KPI_DEFINITIONS tier 1-3).

Mail transport: `crm_mirror/enrich/gmail_sender.py` - chain gmail_oauth (gmail.send scope, sends as `bhanu.enamala@lh2.ai`) -> service account domain-wide delegation (returned `unauthorized_client` on 2026-08-04) -> SMTP -> disk outbox `crm_mirror/vcf_outbox/`. Tokens now at `secrets/hubspot_gmail_send_token_crm_mirror.json` and `secrets/hubspot_gmail_send_token_dashboard.json`. Nothing was sent in this task.

### How stage dates / notes are interpreted (the load-bearing rules)

- HubSpot has no call objects; every number is inferred from (a) **deal stage history** `GET /crm/v3/objects/deals/{id}?propertiesWithHistory=dealstage` and (b) **notes**.
- A metric = a **set of stages** entered, not a flag. Only history entries with `sourceType == CRM_UI` (a human clicked) count as activity (about 42% of entries are INTEGRATION noise). Exception: entry stage (Cold Call / Cold Lead) counts any source because a script push IS the "entered funnel" event.
- Activity is credited to the **actor** (`updatedByUserId`, a USER id mapped to owner id via `/crm/v3/owners` `userId`), not to the current deal owner. Assignment metrics follow owner.
- Candidate narrowing: search `hs_lastmodifieddate >= window start`, then keep deals where `hs_v2_date_entered_current_stage` falls on the IST day; then fetch history only for those (parallel, 12 workers). This only works for windows ending today (the property holds only the latest stage entry); scripts hard-exit for past windows. Portal has no per-stage `hs_date_entered_<id>` properties.
- Two counting rules in snapshots: **flow** stages (12 active) = deals moved in today; **current-state** stages (14 terminal incl. Won) = count sitting in stage now; Roll7/Prev7 for current-state = value as recorded 7/14 days ago. Snapshots exist because HubSpot cannot answer "how many were in stage X seven days ago".
- `cumulative` in snapshot = prior day's cumulative + today's dashboard_flow (incremental; seeded by a one-time full-history backfill). `engaged_deal_ids` stored so rolling-7 "Leads Engaged" is a true set union.
- Notes (Plan B): free-text classified by ordered first-match-wins regex (negations before positives) from `lh2-pipeline/dashboard/note_rules.py` into buckets: No pickup, Callback booked, Bad number, Lead vetted, Not interested, Disqualified, Chase sent, Other. A note whose meaning is already a KPI (e.g. "gmeet fixed" on a deal that moved) is NOT counted again as note-only but still counts toward Leads Engaged. "Net new engaged" excludes WRONG_SPOC regex matches.
- KPI metric sets (`daily_activity_report.METRICS` / `build_dashboard.METRICS`): att = {No Pickup, Dead/ColdCall/WrongNumber, Dead/ColdCall/Not Interested, Dead/ColdCall/NoPickup, Interested} (WrongFit excluded: never dialled); conn = {Dead/ColdCall/Not Interested, Interested}; gm = {GMeet Fixed}; vc = {Dead/GMeet/NoShow, Dead/GMeet/wrong fit, Dead/GMeet/Privacy Concerns, Script Shared} (Cancelled excluded); ss; rr; ev = {Dead/ResultsReceived/WrongFit-Rejected, Commercial Negotiation}; cn; neg = {Dead/Negotiation/Pricing, Dead/Negotiation/Contractual, Deal Contract Signed}; dcs; won. `Call Attempted` is aliased to No Pickup.
- Lead-quality KPIs (analysis/KPI_DEFINITIONS.md, weekly): **ULR** (Usable Lead Rate) = qualified AND contactable / pushed, target >= 80%; Tier 1: QR (1 - WrongFit/pushed, >=85%), CR (1 - contact_unreachable/pushed, >=85%), FAR (verified-correct/verified, >=90%); Tier 2: ER (Interested+/contacted, >=25%), MR (GMeet Fixed+/pushed, >=8%), AFD (mean level 0-11); Tier 3: WR (Closed/Won/pushed, trend only); WNR (wrong-number rate = WrongNumber / dialled, excluding WrongFit; <5% healthy, 5-15% drift, >15% stop pushing from source). Funnel KPIs only for leads pushed >= 14 days ago; report denominators; n<25 directional; segment by source always.
- Output paths: snapshots in `crm_mirror/data/snapshots/`; HTML in the working directory (`full_funnel_dashboard.html`, `daily_fullfunnel.html`, `weekly_fullfunnel.html`, `coopsglobal_dashboard.html`, `daily_report.html`, `full_funnel_dashboard.html`); weekly analysis in `analysis/weekly/<YYYY-Www>/README.md`; mail fallback outbox `crm_mirror/vcf_outbox/`.
- Recipients hard-coded: Shobit (daily_report, monthly_wow), Ishpreet (weekly_segment), Lamiya/Yuktha/Ishpreet (handover), Bhanu test mailbox default. Operating-context rule 2: the ONLY sanctioned recurring mail is the 18:30 IST report to the Pod Head; bulk-assignment mailer incident (9 mails / 1,040 deals) led to disabling the VCF lead notifier. Hard rules also: no non-Indian-mobile push to HubSpot for the Coding track (`crm_mirror/enrich/indian_number.py`, plus `bangladesh_number.py`, `romania_number.py` for expansions).

## 8. Gaps / bugs found while mapping (for the unified design)

1. **Stale stage-id maps in whole-portal reports.** `full_funnel_report.py` / `coding_funnel_report.py` map each label to `[Coding id, old Campaign id]` (e.g. Cold Call `["3992480462","4002503379"]`). The old Campaign stage ids (`4002503379`, `4018854634`...) no longer exist after the pipeline was re-staged to CoOps ( Global ); only the 3 reused ids (Interested, Commercial Negotiation, Closed/Won) still match. So the "whole portal" snapshot effectively counts only Coding plus a few CoOps stages; CoOps is covered properly only by `coopsglobal_full_funnel_report.py`.
2. **`Dropped - Pretraining`** (id 4326788842, 4 deals, closed) is not in any report's `TERMINAL_STAGE_IDS`, so those deals vanish from Dead / current-state counts. (`LOI` was added to FLOW_STAGE_IDS, so it is covered.)
3. **Docs vs live drift:** README pipeline names (Scraped/Campaign) and lead-source universe (5) are stale; README says Kartik 29-stage pipeline, live Company Ops Cluster pipelines have 33 stages and there are TWO clusters; schema reference doc describes an obsolete "Codebase Acquisition" pipeline.
4. Local mirror JSON (`deals.json`) is a Jul-29 artifact - regenerate from API when building the unified DB.
5. Reports depend on `lh2-pipeline/dashboard/note_rules.py` (import path breaks when files move).
6. `daily_fullfunnel_mail.py` etc. read `.env` with lowercase keys (`hubspot_key`); the monolith `.env` uses `HUBSPOT_KEY_MAIN`. Scripts must be re-pointed.
7. Reports are manual / scheduler-less on this Mac; snapshot store has no entries after 2026-10-01.
8. 12 Coding deals have no owner; `amount` vs `cost` price ambiguity unresolved; `script_link` / `script_output_link` / `num_repos` / `data_delivery_timeline` never populated.

## 9. Key gotchas to carry into the monolith

- Always resolve stage id by pipeline; never hard-code. Reordering stages needs an atomic PUT of the whole pipeline.
- Search `limit` max 200 (100 in practice used); always loop on `paging.next.after`. HubSpot search results cap at 10,000 per query - not an issue in Main (6.2k deals) but COMPANYOPS has 7.5k deals and growing.
- Batch associations (`/crm/v4/associations/{from}/{to}/batch/read`, 100 per call); batch reads/writes accept 200/201/207.
- Retry on 429/502/503/504 with backoff.
- Archived deals still hold history; mirror with `archived=true` if counting "never touched".
- Day boundaries IST; reporting cutoff 18:30 IST; Main portal timezone is Asia/Calcutta while the other two are US/Eastern (affects "today" semantics in HubSpot UI and date-typed properties).
- Deal naming: company name, never the person's (about 241 legacy person-named Campaign deals on Yash Wani).
- Key lacks `/settings/v3/users` scope (403).

## 10. Mapping of the three keys and repos (final)

| Monolith env | Portal | Repo originally using it | Pipelines |
|---|---|---|---|
| HUBSPOT_KEY_MAIN | 246754894 (Asia/Calcutta) | hubspot (`hubspot_key`) | Coding, CoOps ( Global ) |
| HUBSPOT_KEY_COMPANYOPS | 246897735 (US/Eastern) | companyOps; hubspot README's `hubspot_kartik` | Company Ops Cluster 1 India, Cluster 2 India |
| HUBSPOT_KEY_RAT | 247485022 (US/Eastern) | RapidActionTeam | Rapid Action Team (+ 1 stray deal in default) |

Whether two keys collide: no, all three portal ids are different.

## 11. Reproducing the live pulls (no secrets)

The pulls used a 30-line stdlib helper reading `HUBSPOT_KEY_*` from `/Users/bhanu/Desktop/LeadGenMonolith/.env` (not echoed): `GET /account-info/v3/details`, `GET /crm/v3/pipelines/deals`, `GET /crm/v3/owners?archived=false|true`, `GET /crm/v3/properties/{deals,contacts,companies}`, and `POST /crm/v3/objects/{type}/search` with empty `filterGroups` and `limit=1` for totals (and `pipeline`/`dealstage`/`lead_source`/`hubspot_owner_id` EQ filters for breakdowns). Intermediate JSON lives only in the session scratchpad.
