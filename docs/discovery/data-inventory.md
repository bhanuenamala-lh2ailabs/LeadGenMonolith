# Discovery: Data-store inventory and entity overlap map

Slug: `data-inventory`. Scope: every database-like store in `legacy/{companyOps,RapidActionTeam,hubspot}`. All SQLite files were opened read-only (`mode=ro&immutable=1`); nothing in `legacy/` or the original repos was modified. No secret values appear in this document. Counts are as of the legacy copies (latest data timestamps 2026-10-03).

## 0. Headline findings

1. **There is no PostgreSQL anywhere.** `grep -ri "psycopg|asyncpg|DATABASE_URL|postgres(ql)?://|pg_dump|CREATE SCHEMA"` over all code, config, md, json hits only: (a) `hubspot/itsvc-tam/itsvc/codebase_signal.py` + its test, where "postgres" is a regex keyword for detecting tech stacks in scraped website text, and (b) scraped-text JSON blobs under `godown/` (job-description evidence). No docker-compose, no alembic/migrations dir, no `.env` DATABASE_URL. The only `.sql` file is `hubspot/TAMBuildSpecs/_corpus/schema.sql`, which is **SQLite** DDL (`PRAGMA journal_mode=WAL`, `AUTOINCREMENT`-style integer PKs). The user's "postgres schemas" are the SQLite schemas listed below. A real Postgres target would be a new decision for the unified design.
2. **10 live SQLite databases + 1 backup + 1 empty stub**, about 1.5 GB total. Three are large and authoritative-ish: `tam.sqlite3` (640 MB), `itsvc.db` (138 MB), `tam_corpus.sqlite` (326 MB).
3. **Three HubSpot portals, five deal pipelines** (verified read-only via `/account-info/v3/details`, `/crm/v3/pipelines/deals`, and a 1-row search for totals; live as of 2026-10-04):

| Env key | Portal id | Tz | Deals | Contacts | Companies | Pipelines (id, #stages) |
|---|---|---|---|---|---|---|
| `HUBSPOT_KEY_COMPANYOPS` (repo companyOps) | **246897735** ("Kartik" ops-data portal) | US/Eastern | 7,575 | 8,165 | 2,641 | "Company Ops Cluster 1 India" (`default`, 33); "Company Ops Cluster 2 India" (`2464812771`, 33) |
| `HUBSPOT_KEY_RAT` (repo RapidActionTeam) | **247485022** | US/Eastern | 1,237 | 1,374 | 844 | "Rapid Action Team" (`2575252183`, 30); stock "Deals pipeline" (`default`, 7, unused) |
| `HUBSPOT_KEY_MAIN` (repo hubspot) | **246754894** | Asia/Calcutta | 6,201 | 8,549 | 6,756 | "Coding" (`default`, 28; formerly "Scraped"); "CoOps ( Global )" (`2425754306`, 30; formerly "Campaign") |

   Note: `companyOps/context.md` line 5 claims portal `246754894` ("same key, same portal"), which is stale/wrong for the current `.env`: the companyOps key resolves to 246897735. `hubspot/README.md` (lines 102-109) is the accurate mapping (Main = 246754894, Ops-Data/Kartik = 246897735). Both `Coding`/`CoOps (Global)` in MAIN and Cluster 1/2 in COMPANYOPS are ops-data/code acquisition funnels with **different stage vocabularies** (full stage lists in section 5).
4. **Local mirrors of HubSpot are stale or partial**: MAIN has `crm_mirror/data/*.json` frozen at 2026-07-29 (1,197 deals vs 6,201 live) plus a full CSV extract at 2026-08-30 (3,822 deals) in `godown/ceo_reality_check/`; COMPANYOPS has **no** deal/contact/company mirror at all (only daily funnel-count snapshots, per-deal dealstage history for 5,784 deals, and `tam.crm_pushes` 1,126 rows); RAT has only 5 snapshot JSONs + CSV lead batches. The authoritative record for deals/contacts/companies/notes/tasks/stage history is **HubSpot itself**, which must be re-pulled by the consolidation job.
5. **Duplication is moderate, not extreme, and the keys are weak.** Across 10 company-bearing domain sets (sum 100,046 domain rows) there are 85,676 distinct root_domains: **14.4% duplicate rows; 10,994 domains appear in >=2 stores, 2,724 in >=3, 573 in >=4**. The biggest single pair overlaps are small relative to size (e.g. tam x itsvc = 4,017 domains = 19.9% of tam). CIN is almost never populated where companies are actually worked (tam `cin`=0, itsvc `cin`=0), so CIN is only usable for the shutdown-radar/Tracxn/registry side. Phone and email overlaps are tiny (tens to hundreds).
6. **The COBOL / mainframe-IP vertical is worked in all three repos** (RAT pushes `COBOL_batch3`, companyOps has `cobol_poc.py` + `exports/cobol_*.csv`, hubspot has `_corpus` vertical `india_cobol_ip` + `india_cobol_*_enriched.csv`). 437 of the corpus's 1,618 India COBOL companies are also in `tam.sqlite3`, and the same companies appear in 3 CSV families. This is the sharpest example of the same entity being tracked three ways.

---

## 1. Postgres verification (details)

| Check | Result |
|---|---|
| `psycopg`, `psycopg2`, `asyncpg`, `sqlalchemy+postgres`, `DATABASE_URL`, `postgres://` in any .py/.env/.toml/.yml/.md/.sql | None (only keyword regex in `codebase_signal.py` and scraped text in godown JSON) |
| `.sql` files | 1: `hubspot/TAMBuildSpecs/_corpus/schema.sql` (18,192 B, SQLite flavour: WAL pragma, `INSERT OR`, `IF NOT EXISTS`, views `v_accounts`, `v_corpus_summary`, `v_source_health`) |
| Migration frameworks | None. `tam` uses a hand-rolled `PRAGMA user_version` counter (live file = 6, the 2026-09-29 backup = 5) inside `companyOps/tam/leadgen/db.py`; `itsvc/db.py` and `_corpus/lib/corpus.py` use `CREATE TABLE IF NOT EXISTS` |
| Docker / compose / managed DB config | None |

## 2. SQLite inventory (summary)

| # | File (under `legacy/`) | Bytes | Tables / rows (major) | Purpose | Written by | Class |
|---|---|---|---|---|---|---|
| 1 | `companyOps/tam/data/tam.sqlite3` | 640,278,528 | 17 tables; companies 41,363; funnel_events 224,846; pages 43,523; raw_sources 49,457; classifications 16,905; company_categories 28,625; crm_pushes 1,126; cost_ledger 2,634 | companyOps TAM build: multi-category (13 India sector categories) company universe with classification, ICP score, funnel stage per company, CRM push log | `companyOps/tam/leadgen/*.py` (db.py, sources/*, resolve.py, crawl.py, classify.py, score.py, funnel.py, outreach.py, backfill.py, dedupe.py, costs.py, signals.py, sh_receiver.py, repair.py, fix_wrong_titles.py) | **Source of truth** for companyOps sourcing state + cost; partly regenerable (pages, signals) |
| 1b | `companyOps/tam/data/tam.sqlite3.bak-20260929-140234` | 363,757,568 | same schema at user_version 5; companies 40,955; pages 43,002; funnel_events 178,623; raw_sources 48,984; crm_pushes 857 | Pre-migration backup, 2026-09-29 14:02 | manual copy | Redundant (older subset of #1). Archive, do not migrate |
| 1c | `companyOps/tam/data/tam.db` | 0 | empty stub (created 2026-09-28 17:31) | none | n/a | Discard |
| 2 | `hubspot/itsvc-tam/itsvc.db` | 138,166,272 | 12 tables; candidates/entities/classifications/captures/raw_records each 61,799; site_text 7,824 (62.8 MB text) | India IT-services company universe from Apollo org search by city x headcount x keyword; 1 entity per candidate (no ER has been run: `entity_members`, `er_decisions`, `suppression`, `cost_ledger`, `cities`, `partner_badges` are all 0 rows) | `hubspot/itsvc-tam/itsvc/{pipeline,classify,crawl,db,cost_ledger}.py` | **Source of truth** for this run (raw payload files under `itsvc-tam/cache` + `out`); `site_text` regenerable |
| 3 | `hubspot/TAMBuildSpecs/_corpus/data/tam_corpus.sqlite` | 326,254,592 | 19 tables + 3 views; company 302,963; company_identifier 311,027; indicator 652,128; icp_score 301,345; icp_score_component 640,833; source_attempt 6,142 | Generic multi-vertical corpus (EAV indicators, source health, score components). Verticals: `uk_proptech` (301,345 cos., all GB, 305,544 Companies House ids) and `india_cobol_ip` (1,618 cos., 100% with domain) | `hubspot/TAMBuildSpecs/_corpus/lib/corpus.py`, `seed_vertical.py`, `score_icp.py`, `report.py` | **Source of truth**; the UK CH bulk part is regenerable from `data/raw/ch_bulk/*.zip` (7 zips, ~470 MB) |
| 4 | `hubspot/lh2-pipeline/data/pipeline.sqlite` | 10,342,400 | companies 5,369; raw_listings 5,455 (all `goodfirms`); people 1,480; cache 1,391; quota 6; crm_feedback 0; no_domain 0 | July 2026 GoodFirms/IT-services scrape, gate, founder/people enrichment, SignalHire quota | `lh2-pipeline/src/lh2_pipeline/store.py` (+ `gmaps_scraper.py` reads it) | Source of truth for GoodFirms-era leads; superseded in practice by HubSpot + itsvc |
| 5 | `hubspot/lh2-pipeline/data/gmaps_cache.sqlite` | 909,312 | tiles 44; usage 1 (2026-08: 304 calls) | Google Places tile cache + monthly call counter | `lh2-pipeline/gmaps_scraper.py` | Regenerable cache but holds spend counter (cost ledger signal) |
| 6 | `hubspot/godown/shutdown-radar/data/radar.sqlite` | 14,024,704 | candidate 10,957; candidate_entity 10,809; entity 10,816; evidence 11,018; scored 10,957; prequal 400; liveness 83; dpiit_startup 0; backfill_progress 0; run_stat 11 | Indian startup shutdown radar (MCA STK-7 strike-off 8,043 + IBBI liquidation 2,755 + news 114 + manual 45) with CIN resolution and tiering (confirmed 2,734 / probable 8,055 / possible 19 / noise 149) | `godown/shutdown-radar/src/shutdown_radar/{db,cli,sources/*}.py` | **Source of truth** for distressed-company evidence (CIN-keyed); `reserve/shutdown_radar_10829_untouched.csv` is a derived export |
| 7 | `hubspot/godown/founder_id/resolver.sqlite` | 319,488 | companies 420 (PK domain; status full/name_only/pending; last_stage; `data` JSON blob); counters 4; walls 27 | Founder / LinkedIn resolver state (resumable, per-source daily counters, block-wall log) | `godown/founder_id/resolver.py`, `li_*.py`, `apify_*.py`, `enrich_push.py` | Working state; the founder results are valuable (domain, CIN, founder name, DIN, LinkedIn); counters = cost signal |
| 8 | `hubspot/godown/founder_id/search_ledger.db` | 196,608 | calls 2,038 (20 run_ids, 3,561 profiles returned); events 39 | Ledger of LinkedIn-search API calls (quota/cost accounting) | `godown/founder_id/search_runner.py` | Cost/credit ledger |
| 9 | `hubspot/godown/itdirs/state/scrape_state.sqlite` | 24,576 | pages 34; counters 0; redirects 0 | Resume state for IT-directory scraper | `godown/itdirs/scrape_directories.py` | Regenerable; discard |

Stray WAL side files (`*-wal` 0 bytes, `*-shm` 32 KB) exist next to `tam.sqlite3`, `itsvc.db`, `pipeline.sqlite`, `tam_corpus.sqlite`, `radar.sqlite`; they are empty and not to be migrated.

### 2.1 Writer coverage by table (from `INSERT INTO` grep)

- **tam.sqlite3**: `categories`/`category_sources`/`stage_runs`/`company_categories`: db.py; `companies`: backfill, dedupe, outreach, resolve, signals, sources/{base,mrsi,sahamati,sebi}; `raw_sources`: sources/base, backfill; `classifications`: classify, dedupe; `pages`: crawl, backfill; `signals`: signals.py; `funnel_events` + `company_categories.funnel_*`: funnel.py (+ dedupe); `contacts`: funnel.py (0 rows now); `crm_pushes`: outreach, backfill, repair, fix_wrong_titles; `cost_ledger`: costs.py; `signalhire_results`: sh_receiver.py; `async_jobs`/`outreach_batches`/`salesnav_import`: created, unused (0 rows).
- **itsvc.db**: candidates/captures/entities/raw_records/classifications: pipeline.py; classifications also classify.py; site_text: crawl.py; cost_ledger: cost_ledger.py (0 rows, so the Apollo spend of this run is not recorded).
- **tam_corpus.sqlite**: all via `lib/corpus.py`; `icp_rule`/`icp_segment`: seed_vertical.py.
- **radar.sqlite**: candidate/entity/evidence/run_stat: db.py; candidate_entity: sources/{ibbi,mca_stk}.py + resolve; scored/liveness/prequal/dpiit_startup: cli.py.
- **pipeline.sqlite**: all seven tables via store.py.

### 2.2 Key distributions (for design)

- tam.sqlite3 `categories` (13, all `active`): adtech(1) construction(2) ecommerce(3) edtech(4) enterprise_saas_it(5) fintech(6) foodtech(7) gaming_sports(8) healthcare_medtech(9) manufacturing(10) mobility(11) proptech(12) market_research(13). `company_categories` rows by category: adtech 4,078; construction 79; ecommerce 473; edtech 5,644; fintech 11,400; healthcare 4,604; manufacturing 1,107; mobility 969; proptech 92; market_research 179 (no domains). `companies`: 41,363 rows, root_domain 20,195, linkedin_url 12,866, apollo_org_id 79, **cin 0, office_phone 0, google_place_id 0**, 13,640 rows tombstoned via `merged_into`, first_seen 2026-09-28 11:14 -> last_seen 2026-09-29 12:07.
- tam `funnel_events` (224,846 rows, 2026-09-28 -> 2026-09-30): score/scored 114,506; resolve (domain_resolved 18,932, domain_from_email 14,152, domain_not_found 10,289, individual_practitioner 6,734); classify (out_too_small 11,432, fit 8,885, out_not_category 5,497, maybe 5,188 ...); crawl; contact_find; crm_push (pushed 482, not_pushed_no_linkedin 615, not_pushed_no_india_mobile 587).
- tam `crm_pushes` (1,126): all `crm='hubspot'`; pipeline `2464812771` (Cluster 2) 420 rows, pipeline `default` (Cluster 1) 461 rows, blank-pipeline legacy rows 245; statuses incl. statuses pushed / contact_corrected / removed_duplicate / removed_off_icp / removed_too_big; every `deal_id` is unique (unique partial index `ux_crm_deal`).
- tam `cost_ledger`: only Apollo, 4 units (org_enrich 236 -> 1,983 credits; org_search_page 275; people_match 1,568; phone_reveal 555 -> 4,440 credits); `usd_est` = 0 for all rows (credit-based, never costed).
- tam `raw_sources` by source: sebi 25,460; apollo_orgs 13,762; sellersjson 2,012; sahamati 1,252; backfill:adtech 1,173; adstxt 1,147; backfill:manufacturing 1,107; tcf_gvl 1,029; backfill:mobility 987; prebid 655; ondc 473; mrsi 179; backfill:proptech 92; backfill:construction 79; retail_seed 50.
- itsvc `candidates`: 100% `source='apollo_org'`; 58,419 with domain (58,331 distinct), 61,778 with LinkedIn, 31,621 with phone; `cin`/`state`/`llpin`/`gstin` empty; `classifications`: 61,593 rows are `model=pre-classification-prior v0` (placeholder, all builds_software=1) and only 206 are real `rules v1` rows; raw_records fetched 2026-10-03 13:29-13:55 (task_key like `apollo:Pune:51,200:cloud consulting`).
- corpus: `status` 285,126 active / 12,872 proposal-to-strike-off / 2,104 liquidation ...; icp bands P1=1, P2=12, P3=76, P4=301,256 (UK is almost entirely P4); `source` table has 28 registered sources incl. paid ones (apify, google_places, apollo, theirstack, signalhire, millionverifier, claude_haiku_classify) with `auth_env_key` names; `budget_ledger` has 0 rows (no spend recorded); `contact` and `suppression` tables exist but are empty (DNC fields `dnc_checked`, `opted_out`, `legal_basis_ref` are UK GDPR/PECR oriented).
- radar: evidence by kind: stk7 8,044; ibbi 2,755; news 174; manual 45. entity.company_status: Struck Off (STK-7) 7,965; Under Liquidation (IBBI) 2,734. `run_stat` also stores JSON audit blobs (datagovin total 3,674,314 MCA records, ibbi as_of 2025-03-31, stk_audit of 12 gazette PDFs).
- pipeline.sqlite `quota`: SignalHire search (limit 4,000/day, used 178/165/8) and credits for 2026-07-22/23/30. people: 1,480 rows, linkedin 702, phone 540, email 309.


---

## 3. Non-SQLite data stores

Legend: **SoT** = source of truth (cannot be regenerated; migrate), **Derived** = regenerable from an SoT (archive, do not migrate row-by-row), **Cache** = regenerable by re-fetching (skip).

### 3.1 hubspot repo (main portal 246754894 + research)

| Path | Size / count | What it is | Class |
|---|---|---|---|
| `crm_mirror/data/{companies,contacts,deals}.json` | 871 / 1,358 / 1,197 records; frozen 2026-07-29 | Old HubSpot mirror, flattened properties (deals: id, dealname, pipeline, dealstage, owner, poc, scraped_type, lead_source ...). Live portal now has 6,201 deals | Derived + stale. Replace by fresh API pull |
| `crm_mirror/data/index/{by_domain,by_linkedin,by_name,deal_names,hubspot_all_names,sheet_worked_exclude}.json` | 12 KB - 353 KB | Dedup key indexes (domain->deal id: 358; deal_names 353 KB; `sheet_worked_exclude` = names already worked in sheets). README rule: "a deal is the dedup unit" | Derived; but `sheet_worked_exclude` (names already worked in sheets, built 2026-08-04) is not rebuildable from HubSpot: SoT |
| `crm_mirror/data/snapshots/*.json` | 186 files, 2026-09-03 -> 2026-10-01 | **Daily funnel history for the Main portal**: `coding_funnel_<d>` (16, Coding pipeline: flow + current_state), `full_funnel_<d>` (27), `full_funnel_coopsglobal_<d>` (7, CoOps Global pipeline, from 2026-09-23), `full_funnel_owner_<ownerid>_<d>` (8 owners + `unassigned` x 15 days to 2026-09-17), plus `hubspot_2026-07-29.json` (generated 2026-07-29: deals 1,197, contacts 1,358, companies 871). Each daily file has `flow` (stage entries that day), `current_state` (stage counts), `cumulative`, `dashboard_flow`, `engaged_deal_ids` | **SoT** for history (HubSpot cannot reproduce past day-end counts exactly) |
| `crm_mirror/outflo/leads.json` (+ `snapshots/leads_2026-08-05/06/09-28.json`, `leads_ALL_campaigns.json`) | 22,996 lead rows (15,868 distinct LinkedIn URLs); 1,936 in ALL_campaigns | OutFlo (LinkedIn automation) campaign leads: bucket, campaign, linkedin, name, company, title, conn/reply/overall status, sender | Derived from OutFlo API but outflo is a 4th system; **SoT for LinkedIn outreach engagement** |
| `crm_mirror/sources/tracxn/{funded,unfunded}.json` | 3,343 + 3,356 companies (34+ Tracxn columns: Company Name, Domain, CIN list, funding, key people, ...) | JSON mirror of tabs `funded`, `unfunded`, `LH2 Ranked Targets`, `LH2 Ranked Targets Outreach Tracker` of one **Google Sheet** (id `12BLV3nv1d9Is4UHN113YVhiTBNilHe-9phIMMCEKS4A`, read by `crm_mirror/sync.py::sync_tracxn` via the lh2bot service account). Underlying data is a Tracxn paid export | SoT locally unless the Sheet is pulled again (Sheet is the master) |
| `crm_mirror/sources/tracxn/lh2_ranked_targets.json` (+`_outreach_tracker`, `index_by_domain.json`) | 6,654 ranked targets: rank, composite/internal/external score, tier, deadpooled/acquired/ipo flags, founder, email, phone, linkedin | Scored/ranked Tracxn universe | SoT for the scoring; Derived from funded/unfunded |
| `crm_mirror/sources/_audit/{hubspot_deals,contacts,tracxn,private_codebase_tracker,outflo_*,net_new,pct_buckets}.json` | hubspot_deals 4,847 deals (id, state, name, stage, pipeline, lead_source, owner, created, domain, linkedin, loc, cost); contacts 2,021; tracxn.json 32.8 MB; private_codebase_tracker ~1 MB | Audit snapshots taken before big pushes. `private_codebase_tracker.json` is a copy of a Google Sheet (the "IT Services Firms" tab, 442 rows exported as `reserve/tracker_it_services_firms_442.csv`) | Point-in-time SoT (sheet content may exist only here) |
| `crm_mirror/holding/*` | `pre_migration_snapshot_2026-08-05.json`, `tracxn_pool_2026-08-04.{json,csv}`, `lead_source_snapshot_2026-08-04.json`, `call_attempted_migration_2026-08-05.csv` | Pre-migration state of Coding pipeline (retired "Call Attempted" stage) | SoT for migration provenance |
| `crm_mirror/enrich/*` | 11 MB: asset_scores*.json (`codebase_fit`/`opsdata_fit`), ready_queue_200, band_followup_data, aug7_salesnav_*, deadpool_resolved, pushed_*.json, campaign-leads-export CSVs, .vcf lead cards | Enrichment scratch + per-batch "what we pushed" logs | pushed_*.json = SoT of push provenance; rest Derived |
| `crm_mirror/rules/*.md` | 6 docs | Per-source business rules (deadpool waves, IT services scrape, outflo->hubspot, private codebase tracker, tracxn scores, asset fit) | SoT (logic documentation) |
| `godown/ceo_reality_check/` | `COMPANIES_FULL.csv` 4,369; `CONTACTS_FULL.csv` 5,151; `DEALS_FULL.csv` 3,822 x 253 columns; `NOTES_FULL.csv` 3,285; `TASKS_FULL.csv` 357; `ENGAGEMENTS_FULL.csv` 30; `STAGE_TRANSITIONS.csv` **7,918 rows (3,822 deals, 2026-07-15 -> 2026-08-27)**; `PER_DEAL_TIMELINE.csv` 2,141; `EVERYTHING.jsonl` 24,933 lines (43 MB); 3 zips | Complete HubSpot Main-portal extract 2026-08-30 incl. stage-transition history (from/to stage, mover, source_type) and notes. Deals split Scraped 3,210 / Campaign 612; lead_source top: Scraping Algo (IT services) 920, Sales Nav 794, Founder Search 618, LinkedIn Campaign 425, Apollo 389, NASSCOM 224, Outflo (Startups) 177, Tracxn 175 | **SoT for historical stage transitions**, notes, tasks (as of 08-30). Not regenerable if HubSpot property history expires |
| `godown/shutdown-radar/data/{out,manual}` | `out/india_startup_shutdowns_20260804.{csv,json,xlsx}` (10,846 rows x 38 cols; json 15.5 MB), `manual/ibbi/*.xlsx` (2,770 rows, IBBI liquidation list as of 2025-03-31), `manual/mca_stk/*.pdf` (gazette PDFs), `cache` 4,830 json files | Shutdown radar raw + outputs | IBBI/MCA PDFs & xlsx = SoT inputs; out/* Derived from radar.sqlite |
| `godown/shutdown-radar-us/` | 543 files, 66 MB; WARN-act state caches incl. 5 xlsx workbooks (IL 4,889 rows layoffs, NJ 23 yearly sheets, CA, IA, MT) + 64 CSV | US WARN layoff notices pipeline | Cache/Derived (re-download from state sites) |
| `godown/cad_leads_2/`, `gujarat_qualify/`, `india_startups_ops/`, `nasscom/`, `romania/`, `prequal/`, `associations/`, `itdirs/`, `founder_id/` ... | 278 MB, 170 MB, 79 MB, 41 MB ... (7,867 files in godown) | Per-exercise Apify/Apollo/SignalHire raw dumps (`*_raw.json`, `*_profiles.json` up to 77 MB, `prequal_out/d_*.jsonl`), scored CSVs, `pushed_*.json` logs | Raw Apify/API dumps = Cache (credit-expensive to re-buy, so archive cold, do not load to DB); `pushed_*.json` + final scored CSVs = SoT; rest Derived |
| `reserve/` | `prequal_reserve_2655_untouched.csv` (3,558 data rows incl. multiline; scored IT firms), `shutdown_radar_10829_untouched.csv` (10,846), `tracker_it_services_firms_442.csv` (442), **`whale_list_27_NEVER_PUSH_TO_HUBSPOT.csv` (27)**, 5 older batches | Candidate pools not yet in HubSpot (checked 2026-09-08) | Derived, except `whale_list` which is a **standing suppression/embargo list**: must be modelled as `suppression` (reason: never push) |
| `exports/` | 42 files, 5.2 MB: `lead_batches/` (Deadpool_Waves xlsx 3 sheets 93/76/39 rows; leads_*.csv), `linkedin_audiences/` (8 ad contact-match CSVs), `opsdata/` (LH2_OpsData_* founders/active-nondistressed CSVs + json), `source_files/` (inbound xlsx: ITservices_ScrapedLeads 4 sheets, Linkedin_Pilot_ITServices_Leads, deals-without-company, linkedin target list), `gmaps/` | Point-in-time outputs (README: "never a source of truth") | Derived; `source_files/` inbound are SoT |
| `temporary/` | 80 files, 4.5 MB incl. xlsx: `Company Ops scrap100 (3).xlsx` (sheets Base 1,539 rows + Scrap 493), `Company Ops Cold Calling Extract - Sep7-Sep11.xlsx` (961 rows), `lead-found year.xlsx` | Ad-hoc | Derived (Base sheet is a LinkedIn Sales Nav dump) |
| `analysis/weekly/{_data,2026-W31}` | `_data/all_deals.json`, `hubspot_index.json`, `itservices_joined.json` ... | Weekly KPI report data (KPI contract in `analysis/KPI_DEFINITIONS.md`) | Derived |
| `itsvc-tam/{cache,out,inbox,reports,config}` | raw Apollo payloads + exports | Inputs of `itsvc.db` (`raw_records.payload_path`) | Cache/Derived but pair with itsvc.db |
| `TAMBuildSpecs/_corpus/data/` | `raw/ch_bulk/BasicCompanyData-2026-09-01-part1..7_7.zip` (~470 MB, UK Companies House bulk), `uk_proptech_accounts.csv` 52 MB, `uk_proptech_leads.csv` 50 rows (company, domain, stage, credits, name, title, linkedin, mobile, email, deal_id), logs | UK Proptech build + India COBOL specs under `TAMBuildSpecs/{UK,India}` | Zips Cache (public data); csv Derived |
| `lh2-pipeline/` | 574 files, 37 MB: `config.yaml`, `data/delivered_domains.txt` (60 KB; by name a list of domains already delivered to HubSpot, not content-verified), `dedup_names_sheet_hubspot.csv`, `.git-standalone-repo-backup`, Google service-account JSON | Scraper engine | `delivered_domains.txt` = suppression-like dedup memory (SoT, unverified contents) |
| Root CSVs in `hubspot/` | `india_cobol_*_enriched.csv` (top50: 50 rows; batch2: 54; batch3: 45; batch4: 19; batch5: 4), `ceo_leads_enriched_2026-09-30.csv`, `telehealth_india_enriched_2026-09-29.csv`, `anthropic_poc_2026-09-29.csv`, `poc_correction_2026-09-30.csv`, `companies.csv` (25 rows), `Deck Sourcing Campaign ... .csv` | Enriched lead batches, some pushed from Google Sheets | Derived (provenance of pushes) |

Google-Sheets-backed sources (read via OAuth tokens, no local DB): `crm_mirror/sync.py::sync_tracxn` (reads the Tracxn sheet, 4 tabs), `crm_mirror/enrich/enrich_{telehealth,ceo_leads,openai,amazon}_sheet.py` and `anthropic_poc.py` (read+write), `godown/intern_screen/*` and `scientific_sheet_write.py` (write), `lh2-pipeline/.../export/sheets_sync.py` (gspread two-way sync). Spreadsheet ids live in script constants; extraction of those and the sheet contents is another discovery's job.

### 3.2 companyOps repo (Kartik portal 246897735)

| Path | Size / count | What it is | Class |
|---|---|---|---|
| `crm_mirror/data/snapshots/full_funnel_cluster{1,2}_<d>.json` | 25 + 25 files, 2026-09-09 -> 2026-10-03 (cluster 1/2 files for 09-26 and 09-27 were written on 09-30, i.e. back-filled) | **Daily funnel history for Cluster 1 and Cluster 2** (keys: date, dashboard_flow, current_state (33 stage counts), cumulative, engaged_deal_ids). Last: 2026-10-03 cluster 1 e.g. No pickup 492, 1st interest sent 93, Dead: Wrong Number 175 | **SoT** |
| `crm_mirror/data/backfill_checkpoints/cluster{1,2}_history.json` | 274 deals (70 KB) and 5,510 deals (1.9 MB) | Raw HubSpot `dealstage` property history per deal id: `[{value: stageId, timestamp, sourceType (CRM_UI/INTEGRATION...), sourceId}]` used to back-fill daily counts from 2026-09-09 | **SoT for deal stage history** of the Kartik portal (this is the only local stage-event record for it) |
| `tam/data/tam.sqlite3` | see section 2 | | |
| `tam/cache/{adstxt,apollo_orgs,mrsi,prebid,sahamati,sebi,sellersjson,tcf_gvl}` | 29 MB | Raw source downloads | Cache |
| `tam/categories/*.yaml` (13), `tam/config.yaml`, `tam/seeds/*.csv` (publishers_in, retail_media_seed) | 84 KB | Category/ICP definitions (config-as-data) | SoT (logic) |
| `tam/exports/accounts_<category>.csv` + `exports/classify/` | 115 MB | Exports of tam.sqlite3 per category | Derived |
| `tam/logs/run_2026-09-28/29/30.jsonl` | 244 KB | Run logs | Cache |
| `exports/` | 14 files, 344 KB: `cobol_india_200_scored.csv` (200), `cobol_remaining_168*.csv`, `cobol_sub499_poc*.csv` (167 / 57 callable), `theirstack_cobol_india*.csv`, `edtech_campaign_overlap.csv` (54 KB), `cluster2_cutoff_{report.html,snapshot.json}` | COBOL vertical and campaign overlap analyses | Derived |
| `data/imports/` + `data/exports/` | `Company Ops scrap100.xlsx` (Base 2,295 rows + Scrap 101) and `(1)` copy (Base 1,539); `Fintech and Financial Services.csv`; `campaign-leads-export-*-2026-09-16.csv`; `RAT_CAD_Tracks1_2_Unassigned.xlsx` (+ `_duplicates/` copy); `exports/`: salesnav_final_leads, shutdown_companies_100_200 / 200_500 (+ `_tech`), fintech_classification_*.csv, MobilityTech.csv | Inbound Sales-Nav dumps and outbound batches | Derived; imports are SoT inputs |
| `opsdata/*.py` | 26 scripts | Pipeline sync, deals v2..v6 migrations, `daily_fullfunnel_report.py`, `full_funnel_{report,backfill_*,dashboard_mail}.py`, `gmail_pull_push.py`, `outflo_pull_push.py`, cluster report senders | Code only (no data) |
| `audit/` | 28 files, 3.8 MB: `deals_v2/v5_migrate_*.json/csv` etc. | Audit trail of HubSpot bulk writes (before/after) | SoT for migration provenance |
| `pipeline/{raw,processed,final}` | 1.1 MB, scrapers `scrape_aimed.py`, `scrape_mtai.py`, `scrape_nathealth.py` | Early scrape outputs | Derived |
| Root: `campaign-leads-export-37cc2be1-...-2026-09-30.csv`, `coding_coldcall_lamiya50_yuktha48_2026-09-29.csv`, `daily_fullfunnel.html`, `claudeContext/conversations-000.zip` (12.7 MB, out of scope), `cloudflared` (39 MB binary), `cf.tgz` | | Misc | binaries/zip: exclude |

### 3.3 RapidActionTeam repo (portal 247485022)

Tiny (1.5 MB), **no database**. HubSpot is the only datastore; all else is flat files:

| Path | Count | What it is | Class |
|---|---|---|---|
| `snapshots/rat_<d>.json` | 5 files: 2026-09-25, 09-28, 09-29, 09-30, 10-01 | Daily funnel snapshots: `flow`, `current_state` (30 stages), `dashboard_flow` | **SoT** (only history; other days lost) |
| `audit/*.json|*.log` | 53 files, 740 KB (enrich_cad_founders_*, import_cad_100_run_*, dedup_contacts_*, backfill_prerna_no_phone_*, cobol_batch3_removal_catchup, `_poll_cobol_batch3_state`, cad_enrich_seen.json) | Run/write audit trail | SoT for push provenance |
| `leads/*.csv` | 11 files: CAD_candidates_batch1 (179) / batch2 (174), CAD_Callable_Founders (121; 109 domains), CAD_SUPPLY_ListA (450) / ListB (159), CAD_RAT_Unassigned_Ready42 (42), CAD_held_pool_icp_pass (144), COBOL_batch3 (31), COBOL_high_top12 (12), RAT_CAD_Tracks1_2_Combined (159), CAD_recovered_00prefix (1) | Lead batches for CAD (computer-aided design) and COBOL verticals | Derived, but only local copy of candidates that were not pushed |
| Root CSV/xlsx | `RAT_CAD_Tracks1_2_Unassigned.xlsx` (sheet Unassigned, 110 rows x 14 cols; identical md5 to copies in companyOps/data/imports, hubspot root, and `(1)` duplicate), `hubspot_CAD_leads_held_pool_2026-09-30.csv` (158 rows, no domain column), `india_cobol_ip_{batch2,batch3,top12}_enriched_2026-10-01.csv` (16 / 42 / 12 rows) | | Derived; the xlsx is **one file stored 5 times across 3 repos** (same hash) |
| `TAMbuilds/CADTAMbuild.md`, `build.md`, `funnel report setup.md`, `*_NOTES.md` | | Specs | Docs |

The RAT repo has no TAM database for its CAD/COBOL verticals: CAD discovery output lives only in `leads/*.csv` and `hubspot/rat_cad_discovery/` (28 files, 1.2 MB) and `hubspot/godown/cad_leads_2/` (278 MB raw Apify/Maps/LinkedIn dumps) in the hubspot repo, i.e. **the RAT portal's sourcing data is in the hubspot repo, not the RAT repo**.

### 3.4 xlsx workbook inventory (openpyxl 3.1.5; unique by md5)

| Workbook (representative path) | Sheets (rows x cols) | Notes |
|---|---|---|
| `RapidActionTeam/RAT_CAD_Tracks1_2_Unassigned.xlsx` (md5 53adb91b, 5 copies across 3 repos) | Unassigned 110x14 (status, poc_phone_e164, icp_track, source, company, domain, matched_keyword_group, hq_location_city, poc_name/title/email/phone/linkedin, notes) | CAD callable leads; duplicates |
| `companyOps/data/imports/Company Ops scrap100.xlsx` (9669680f) | Base 2,295x8 (First/Last Name, Headline, Location, Company, Title, LinkedIn URL, Flag); Scrap 101x5 | Sales-Nav scrape; Flag e.g. "Correct Data", "Email" |
| `companyOps/data/imports/Company Ops scrap100 (1).xlsx` (db75858a) | Base 1,539x8; Scrap 101x5 | Older version |
| `hubspot/temporary/Company Ops scrap100 (3).xlsx` (b6795a49) | Base 1,539x8; Scrap 493x5 | Third version: three diverged versions of the same sheet |
| `hubspot/temporary/Company Ops Cold Calling Extract - Sep7-Sep11.xlsx` | Sheet1 961x6 | Cold-call activity extract |
| `hubspot/temporary/lead-found year.xlsx` | campaign-leads-export 325x8 | OutFlo campaign export |
| `hubspot/exports/lead_batches/Deadpool_Waves_2026-08-04.xlsx` | 1_Pushed 93x15 (owner, company, wave, founder, phone, email, linkedin, shutdown, funding_raised, confidence, deal_id); 2_Companies 76x14; 3_NotPushed 39x7 | Dead-startup waves with HubSpot deal ids |
| `hubspot/exports/source_files/ITservices_ScrapedLeads.xlsx` | Sheet1, Qualified Leads, Under Review, Pipeline Stats | Inbound (dimensions unreadable in read-only mode) |
| `.../Linkedin_Pilot_ITServices_Leads.xlsx`, `deals-without-company.xlsx`, `linkedin target list filled.xlsx` | Sheet1 / linkedin_leads_50_filled | Inbound |
| `hubspot/godown/2025-07-30 16_45_39-2f9407c2....xlsx` (3 copies) | Sheet2 2,770x13 | IBBI liquidation list (as of 2025-03-31), input of radar |
| `hubspot/godown/shutdown-radar/data/out/india_startup_shutdowns_20260804.xlsx` | shutdowns 10,847x38 | Export of radar.sqlite |
| `hubspot/godown/shutdown-radar-us/pipeline/cache/warn_full/{ca,ia,il,mt,nj}/*.xlsx` | CA 6 sheets (detail 218); IA 2026..2021 + historic; IL Layoffs 4,889x34 + Scheduled 6,143x7; MT 54x7; NJ 23 yearly sheets | US WARN downloads: Cache |

Conclusion: the user's "databases in Excel sheets" are really (a) the CSV/xlsx lead batches above, (b) Google Sheets (not stored locally; only partial mirrors in `_audit/*.json`), (c) HubSpot. There is **no xlsx that is the master database**; the nearest is `private_codebase_tracker` (a Google Sheet) whose snapshot is `crm_mirror/sources/_audit/private_codebase_tracker.json`.

### 3.5 Funnel snapshot history (for the unified daily report)

| Series | Location | Days | Pipeline / stage vocabulary |
|---|---|---|---|
| Main / Coding | `hubspot/crm_mirror/data/snapshots/coding_funnel_*.json` + `full_funnel_*.json` | 16 coding (09-03..10-01), 27 full | Coding (28 stages) |
| Main / CoOps (Global) | `full_funnel_coopsglobal_*.json` | 7 (09-23..10-01) | CoOps (Global) (30 stages) |
| Main per-owner | `full_funnel_owner_<id>_*.json` | 9 series x 15 days (09-03..09-17) | owners 96573782, 96574824, 98906502, 166262056, 166322218, 166322228, 166420402, 166483631, unassigned |
| Kartik Cluster 1 | `companyOps/crm_mirror/data/snapshots/full_funnel_cluster1_*.json` | 25 (09-09..10-03) | Cluster 1 (33 stages) |
| Kartik Cluster 2 | `.../full_funnel_cluster2_*.json` | 25 (09-09..10-03) | Cluster 2 (33 stages) |
| RAT | `RapidActionTeam/snapshots/rat_*.json` | 5 (09-25, 09-28..10-01) | Rapid Action Team (30 stages) |

Gaps: no snapshots for Main after 2026-10-01 and none for RAT before 09-25; Main `full_funnel_*` earliest value is 2026-09-03, so any earlier history must be rebuilt from stage-transition history (e.g. `STAGE_TRANSITIONS.csv` covers 2026-07-15 -> 08-27 for Main).

---

## 4. Entity overlap map

### 4.1 How each entity is represented per store

| Entity | tam.sqlite3 (companyOps) | itsvc.db | tam_corpus.sqlite | pipeline.sqlite | radar.sqlite | resolver / ledger | HubSpot portals (live) | Flat files |
|---|---|---|---|---|---|---|---|---|
| **Company** | `companies` (id; root_domain, linkedin_url, name/name_norm, legal_name, cin, apollo_org_id, google_place_id, headcount_band, funding, `merged_into` tombstone) + `company_categories` (company x category: segment, icp_bucket, score, priority_tier, funnel_*, regulated_status, parked_as) + `classifications` + `signals` + `pages` | `candidates` (raw, per source row) -> `entities` (entity_id TEXT, canonical_name, domain, linkedin_url, city, headcount_band) | `company` (per vertical: name_norm unique per vertical; root_domain, linkedin_url, status, headcount) + `company_identifier` (EAV: companies_house 305,544; apollo_org 2,096; domain 3,387) + `company_source` | `companies` (PK domain) + `raw_listings` | `candidate` (brand) -> `candidate_entity` -> `entity` (PK cin) | `resolver.companies` (PK domain, cin, `data` JSON) | Company object (props incl. `lh2_domain`, domain, city); also **deal** (dealname = company name) acts as the dedup unit per README | Tracxn JSON, reserve CSVs, COMPANIES_FULL.csv |
| **Contact / person** | `contacts` (**0 rows**; schema: apollo_person_id, linkedin, email, phone_mobile, role_rank, seniority, enrich_state, funnel_*); `signalhire_results` 529 (item, status, mobile, email); `salesnav_import` 0 | none (company-only) | `contact` (**0 rows**; has DNC / opt-out / legal-basis fields) | `people` 1,480 (domain+name unique; role, linkedin, phone, email, is_primary, confidence) | none | `resolver.companies.data` (founder_name, DIN, title, linkedin) | Contact objects (8,165 / 1,374 / 8,549) | OutFlo leads 22,996 (LinkedIn-keyed), CONTACTS_FULL 5,151, RAT/companyOps lead CSVs |
| **Deal** | `crm_pushes` 1,126 (company_id, category_id, crm, deal_id, contact_id, owner, pipeline, dealstage, segment, status, pushed_at) | none | none | `crm_feedback` (0 rows: call outcome pull-back, email/domain keyed) | none | none | Deals (7,575 / 1,237 / 6,201) | DEALS_FULL.csv (253 cols), `_audit/hubspot_deals.json`, `Deadpool_Waves` xlsx (deal_id col) |
| **Funnel event / stage history** | `funnel_events` 224,846 (entity_type, entity_id, stage, bucket, reason, source, run_id, at) - the **sourcing funnel**, e.g. resolve->crawl->classify->score->contact_find->crm_push; also `company_categories.funnel_stage/bucket` (current) | none (`raw_records` + `stage_*` absent) | `stage_run` 13 (run level), `source_attempt` 6,142 (fetch outcomes) | `companies.status`, `gate_pass`, `gate_reason` (current only) | `scored.tier`, `run_stat` | `resolver.last_stage` | **Deal stage history** (`dealstage` property history; `hs_date_entered_*`) | STAGE_TRANSITIONS.csv 7,918 (Main); `*_history.json` 5,784 deals (Kartik); daily count snapshots (all portals, section 3.5) |
| **Source / provenance** | `raw_sources` 49,457 (source, source_ref, run_id, payload_json, company_id) + `category_sources` (source saturation / robots) + `stage_runs` 31 | `raw_records` 61,799 (source, source_url, sha256, payload_path, task_key) + `captures` (entity x source x stratum) | `source` 28 registry + `company_source` 303,087 + `source_attempt` + `raw_document` | `raw_listings` 5,455 (goodfirms) + `sources_json` on companies | `evidence` 11,018 (kind, source_url, snippet, dedupe_key) + `candidate.discovery_source` | `walls`, `counters` | `lead_source` deal property (e.g. "Scraping Algo ( IT services )"), `scraped_type`, `lh2_domain` | `pushed_*.json`, `audit/*.json` |
| **Cost ledger** | `cost_ledger` 2,634 (Apollo credits only; `usd_est`=0) | `cost_ledger` 0 rows | `budget_ledger` 0 rows (but `classifications.cost_usd` in itsvc and source cost metadata exist) | `quota` 6 (SignalHire search + credits per day) | none | `search_ledger.calls` 2,038 (LinkedIn search calls) + `resolver.counters`; `gmaps_cache.usage` (Google Places 304 calls Aug 2026) | none | `hubspot/Cost Analysis/` (6 files), `APOLLO_*` md |
| **Suppression / embargo** | none (`company_categories.parked_as`, `regulated_status` are the closest; crm_pushes.status `removed_duplicate/off_icp/too_big`) | `suppression` 0 rows (domain/email/phone/entity) | `suppression` 0 rows (kind/value/reason); `contact.opted_out`, `dnc_*` | `no_domain` 0 | none | none | HubSpot unsubscribe/DNC props (not inspected) | `reserve/whale_list_27_NEVER_PUSH_TO_HUBSPOT.csv` (27, explicit embargo), `lh2-pipeline/data/delivered_domains.txt` (dedup memory), `index/sheet_worked_exclude.json`, "Dead" stages in HubSpot |

### 4.2 Join keys available

| Key | Where populated (counts) | Quality / caveats |
|---|---|---|
| **root_domain** | tam.companies 20,195/41,363 (49%); itsvc.entities 58,327 distinct/61,799; corpus 3,362 distinct (UK has domain for only 1,766 of 301,345; India COBOL 1,618/1,618); pipeline.companies 5,369 (PK); resolver 420 (PK); Tracxn ranked 6,654; Main CRM companies 3,760 (of 4,369 in 08-30 extract); reserve prequal 3,558 | **Primary cross-store key.** Needs one normalizer (lowercase, strip scheme/`www.`, path). Multi-company domains (agencies, gmail-style personal domains) and `alt_domains` exist: tam has `alt_domains`, itsvc 58,419 domain rows for 58,331 distinct domains (88 dups) |
| **linkedin_url (company)** | tam.companies 12,866; itsvc 61,422 (99.9%); corpus 2,090; Main audit deals 816 | Normalize to `company/<slug>` or `in/<slug>`. Company and person pages share the same column name in several stores - a person URL in a company column is possible |
| **linkedin_url (person)** | contacts: CONTACTS_FULL 4,823 distinct; OutFlo 15,868 distinct; pipeline.people 701; resolver (founder) 420 | Best person key. OutFlo x HubSpot contacts overlap 774 persons (16% of HubSpot contacts) |
| **hubspot ids** | deal_id: tam.crm_pushes 1,126, Deadpool xlsx, pushed_*.json, `engaged_deal_ids` in snapshots; contact_id: crm_pushes; company id: CRM extracts | **Portal-scoped**: ids are only unique inside a portal. Keep (portal_id, object, id). Pipeline/stage ids differ per portal (e.g. `default` exists in every portal) |
| **CIN** | radar.entity 10,816; Tracxn (list of legal entities); resolver ~hundreds; corpus none for India (companies_house ids are UK); tam.cin 0; itsvc 0 | Only usable for the distressed/startup side; absent where most work happens |
| **phone (E.164)** | itsvc.candidates 31,621 (company office phones, 30,513 distinct last-10); pipeline.people 540; signalhire_results 348; CONTACTS_FULL 4,659 distinct; RAT leads 1,238 | Phone overlap is tiny: itsvc x HubSpot-main contacts 90; pipeline.people x main contacts 371 (70%); RAT leads x main 5. Mixed provenance (company office vs mobile) |
| **email** | pipeline.people 309, signalhire 444, CONTACTS_FULL 4,359 | pipeline.people x main 143 (46% of its emails) |
| **apollo ids** | tam.companies.apollo_org_id 79 (low, but raw_sources apollo_orgs 13,762 hold payloads); corpus apollo_org 2,096; tam.contacts.apollo_person_id (0 rows) | Could be used to join itsvc raw payloads to tam raw_sources |
| **name_norm** | tam.companies.name_norm; corpus unique (vertical_id, name_norm); radar.candidate.brand_name_norm; HubSpot deal names | Weak, used by dedupe for rows without domain (partial index `ix_co_namenorm_nodom`) |

### 4.3 Measured overlap (distinct normalized root_domains; sampled with full scans, not estimates)

Store sizes (distinct domains): tam 20,195; itsvc 58,327; pipeline 5,369; Tracxn ranked 6,654; Main CRM companies (08-30) 3,760; prequal reserve 3,558; corpus 3,362 (India COBOL 1,618); resolver 420; radar liveness 83.

| Pair | Overlap (domains) | As % of smaller set |
|---|---|---|
| tam x itsvc | **4,017** | 19.9% of tam |
| tam x pipeline | 522 | 9.7% |
| tam x Tracxn ranked | 974 | 14.6% |
| tam x Main CRM (08-30) | 583 | 15.5% |
| tam x corpus (India COBOL) | 437 (of 1,618) | 27% of COBOL set |
| tam.crm_pushed (716 distinct pushed to Kartik) x Main CRM | **37** | cross-portal duplicate companies |
| tam.crm_pushed x itsvc | 211 | |
| itsvc x pipeline | 2,061 | 38.4% of pipeline |
| itsvc x prequal reserve | 1,387 | 39% |
| itsvc x Main CRM | 470 (680 incl. tam-pushed and audit deals) | 0.8% of itsvc |
| itsvc x corpus (India COBOL) | 679 | 42% of COBOL set |
| pipeline x Main CRM (08-30) | 1,281 | 23.9% (pipeline is the origin of many Main deals) |
| pipeline x prequal reserve | 2,131 | 59% |
| pipeline x resolver | 372 of 420 | 89% |
| resolver x Main CRM | 180 | 43% |
| corpus (India COBOL) x prequal reserve | 97 | |
| Main CRM x Tracxn ranked | 722 | 19% |
| radar liveness x reserve shutdown (derived) | 62 of 83 | |

Rollup over 10 domain sets (tam, itsvc, corpus India COBOL, pipeline, Tracxn, Main CRM, resolver, prequal reserve, shutdown reserve, radar liveness): sum 100,046; **union 85,676 (14.4% duplicate rows)**; in >=2 stores 10,994; >=3: 2,724; >=4: 573. Among CRM-known domains (Main CRM + tam-pushed + Main audit deals = 4,468), only 680 are present in itsvc: **57,647 of itsvc's 58,327 domains are not in any CRM** - itsvc is mostly net-new TAM, not yet worked.

LinkedIn company-URL overlaps: tam x itsvc 3,145 (24% of tam); tam x corpus 401; itsvc x corpus 670. Person-URL overlaps: OutFlo x HubSpot contacts 774; OutFlo x Main audit deals 376; pipeline.people x HubSpot contacts 478 (68% of its 701 URLs).

COBOL vertical across repos (CSV domain vs stores): `companyOps/exports/cobol_india_200_scored.csv` 182 distinct domains: 35 in tam, 70 in itsvc, 16 in Main CRM, 22 in corpus; `hubspot/india_cobol_top50_enriched.csv` 50 domains: 50 in corpus, 19 in tam, 22 in itsvc; `RapidActionTeam/leads/COBOL_batch3.csv` 31 domains: 31 corpus, 7 tam, 12 itsvc, 7 Main CRM. RAT CAD batches overlap tam by 3-10 domains per file and itsvc by 2-16, Main CRM by 0-2.

### 4.4 Expected duplication / conflict hot spots (for the unified model)

1. **Same company, many local rows**: a company can have a row in tam.companies, itsvc.candidates/entities, corpus.company, pipeline.companies, a Tracxn row, a Main CRM company+deal and a Kartik/RAT deal. Expect ~14% duplicate domain rows overall, concentrated in IT-services (tam x itsvc x pipeline x prequal) and COBOL (tam x corpus x CSVs).
2. **Cross-portal duplicates are business-legal but must be tracked**: the same company can legitimately be a "codebase" deal in Main and an "ops data" deal in Kartik (37 domains already). The unified design needs `portal_id` + `pipeline` on every deal and a company-level view across portals.
3. **Deal != company**: Main README treats the deal (not the company) as the dedup unit; 6,756 Main companies vs 6,201 deals, and `deals-without-company.xlsx` exists as an inbound fix list. Kartik has 2,641 companies for 7,575 deals (multiple deals per company: contacts-as-deals). Map company 1:N deals 1:N contacts explicitly.
4. **tam.companies tombstones**: 13,640 rows have `merged_into` set (and no domain); queries must follow the chain.
5. **Placeholder classifications**: itsvc's 61,593 `pre-classification-prior` rows are not real classifications; do not import them as facts.
6. **Empty-but-designed tables**: tam.contacts, corpus.contact/suppression, itsvc.suppression/er_decisions/entity_members hold zero rows - the person/suppression/ER layers have never been populated in SQL; people live in HubSpot, OutFlo and CSVs.
7. **Cost data is scattered and mostly unrecorded in USD**: Apollo credit counts (tam.cost_ledger), SignalHire counters (pipeline.quota, tam.signalhire_results), LinkedIn search calls (search_ledger), Google Places calls (gmaps_cache.usage), Apify spend (none), itsvc Apollo credits (none), corpus budget (none). `hubspot/Cost Analysis/` and `APOLLO_*.md` are the only narrative cost records.
8. **Same file, many copies**: `RAT_CAD_Tracks1_2_Unassigned.xlsx` x5, IBBI xlsx x3, three diverged `Company Ops scrap100` workbooks, `cobol_remaining_168_with_poc.csv` / `cobol_remaining_for_poc.csv` (same 156 domains, one with POC columns).

---

## 5. Stage vocabularies (five funnels, three portals; live labels)

- **Main / Coding** (`default`, 28): Cold Call, No Pickup, Interested, GMeet Fixed, Script Shared, Script Results Received, Commercial Negotiation, LOI, Deal Contract Signed, Data Migration Done, Metadata Matched, Payment Initiation, Closed/Won; 13 dead stages (Dead/ColdCall/{Not Interested,WrongFit,WrongNumber,NoPickup}, Dead/Interested/NoShow, Dead/GMeet/{NoShow,Cancelled,wrong fit,Privacy Concerns}, Dead/ScriptShared/NoShow, Dead/ResultsReceived/WrongFit-Rejected, Dead/Negotiation/{Pricing,Contractual}); plus Dropped - Pretraining and Call Attempted (retired).
- **Main / CoOps ( Global )** (`2425754306`, 30): Cold Lead, Communicated, No Response, Interested, VC Fixed, VC Rescheduled, 1 Pager Shared, 1 Pager Output Received, Commercial Negotiation, LOI Signed, Sample Extraction, Demand Fulfillment, Contract Signed, Data Extraction Done, Closed/Won; 15 "Dead: <stage> / <reason>" stages.
- **Kartik / Cluster 1 and Cluster 2** (`default`, `2464812771`; identical 33 stages): LinkedIn sent, Cold called assigned, LinkedIn connected, No pickup, Retired: Callback +1 day, Replied, 1st interest sent, 1st interest follow up, Discovery call, Call rescheduled, One pager requested, One pager follow up, One pager received, LOI signed, Contract signed, Ops data handover done, Payment initiation, Closed/Won; 15 Dead: <stage> / <reason> (incl. "Email Campaign / Branch Retired").
- **RAT / Rapid Action Team** (`2575252183`, 30): Cold Called Assigned, No Pickup, Callback, Replied, 1st Interest Sent, 1st Interest Follow Up, Discovery Call, Call Rescheduled, Sample Requested, Sample Follow Up, Sample Received, Negotiation, Contract Signed, Ops Data Handover Done, Payment Initiation, Closed / Won; 14 Dead (compact "Dead: ColdCall/WrongFit" style spelling).

The three stage families (coding, "CoOps/Cluster" call->interest->discovery->one-pager->LOI, RAT call->interest->discovery->sample->negotiation) map onto a common canonical ladder: *assigned/cold -> no-pickup/callback -> replied/interested -> meeting (GMeet/VC/Discovery) -> artefact (script/one-pager/sample) -> commercial (negotiation/LOI) -> contract -> delivery/handover -> payment -> won*, with dead branches keyed by (last_live_stage, reason). Label spelling differs everywhere ("Dead/ColdCall/WrongFit" vs "Dead: ColdCall/WrongFit" vs "Dead: Cold Call / Wrong Fit"), so the unified model should store `portal_id`, `pipeline_id`, `stage_id`, raw label, plus a `canonical_stage` and `dead_reason`.

---

## 6. Recommendations for the unified design

1. **Pick the engine deliberately**: nothing today is Postgres; everything is SQLite, with 3 big DBs (640/326/138 MB). A single local SQLite file (WAL, FKs on) is enough for 100k-300k companies; consider Postgres only if multi-writer/remote access is wanted. The corpus `schema.sql` is the best existing generic design (verticals as data, EAV indicators, source registry, source_attempt as first-class, score components, DNC fields) and can be the base, extended with the tam funnel/CRM tables.
2. **Entity spine**: `company` (surrogate id) + `company_identifier` (domain, linkedin_company, cin, apollo_org, companies_house, google_place, hubspot(portal,id)) with many-to-one identifiers; `company_vertical`/`company_category` (tam's `company_categories` + corpus `vertical`); keep tam's `merged_into` (survivorship) and corpus's `company_source`. Load order that maximizes dedup: tam (domain+LI) -> itsvc (domain, LI) -> pipeline/resolver (domain) -> corpus (India COBOL by domain; UK separate by CH number) -> Tracxn/radar (CIN, name_norm + domain) -> CRM companies.
3. **Contacts/people**: create real `person` + `contact_method` (email/phone/linkedin with source, verified_at, DNC/opt-out) populated from HubSpot contacts (3 portals), OutFlo leads, pipeline.people, resolver founder data, signalhire_results; key on person LinkedIn URL first, then email, then (company_id, name_norm).
4. **Deals**: table `crm_deal` keyed (portal_id, deal_id) with pipeline_id, stage_id, owner, lead_source, created/closed; `crm_deal_stage_event` from HubSpot property history (backfill via API; also ingest `STAGE_TRANSITIONS.csv` 7,918 rows and `*_history.json` 5,784 deals as seed); `crm_pipeline_stage` dictionary with `canonical_stage` and the live labels listed above. Import `tam.crm_pushes` into the same tables (it already carries portal-scoped ids and pipeline/dealstage strings).
5. **Funnel/sourcing events**: keep tam's `funnel_events` shape as `pipeline_event(entity_type, entity_id, stage, bucket, reason, source, run_id, at)` for the *sourcing* funnel, separate from CRM stage events; keep `stage_run`/`source_attempt` style run logs.
6. **Provenance**: unify `raw_sources` (tam), `raw_records` (itsvc), `raw_listings` (pipeline), `evidence` (radar), `source_attempt`/`raw_document` (corpus) into `source` + `raw_record(source_id, run_id, payload_json or payload_path, sha256, fetched_at)`; keep large raw dumps (Apify, `cad_leads_2` 278 MB, `gujarat_qualify` 170 MB, `ch_bulk`, WARN caches) on disk as cold archive referenced by path/sha, not loaded.
7. **Cost ledger**: one `cost_ledger(vendor, unit, qty, usd, run_id, entity_ref, at)` seeded from tam.cost_ledger (2,634 rows), pipeline.quota (6), search_ledger.calls (2,038), gmaps_cache.usage (304 calls), signalhire_results (529); set USD per vendor via a rate table (all current `usd_est` values are 0). Record Apollo credits for itsvc retroactively from `raw_records` page counts (61,799 records/task_keys).
8. **Suppression**: `suppression(kind, value, reason, source, added_at)` with kinds domain/email/phone/linkedin/company. Seed from `whale_list_27_NEVER_PUSH_TO_HUBSPOT.csv` (domains), `delivered_domains.txt`, `sheet_worked_exclude.json`, all HubSpot Dead-stage and unsubscribed contacts, tam `removed_duplicate/off_icp/too_big` pushes. Enforce before any HubSpot write.
9. **Daily snapshots**: ingest the 6 snapshot series (section 3.5) into `funnel_snapshot(portal_id, pipeline_id, date, stage_id, flow_count, current_count, cumulative_count)` so the unified daily report is a single query; note vocabularies differ so report by canonical stage plus native stage. Snapshot gaps (Main after 10-01, RAT before 09-25, cluster 1 on 09-26/27 were backfilled) can be rebuilt from stage-event history.
10. **Treat HubSpot as the live truth and re-pull it**: local mirrors are 1-2 months stale and companyOps has none. The consolidator should page `/crm/v3/objects/{deals,contacts,companies}` with properties + `propertiesWithHistory` for deals (stage history) for each of the 3 portals (read-only GET/search), instead of trusting the CSV extracts.
11. **Do not migrate**: `tam.db` (0 B), `tam.sqlite3.bak-20260929-*` (older subset, archive only), `scrape_state.sqlite`, WAL/SHM, `itsvc.db.site_text` (can be refetched; keep 62 MB only if offline classification is needed), tam `pages` (43,523 crawled pages, ~227 MB of page text; keep if classification is to be re-run offline), `gmaps_cache.tiles`, US WARN caches, Companies House zips.
12. **Fix before merge**: `companyOps/context.md` portal-id statement is wrong; `itsvc` classification placeholders; `tam.contacts` empty; domain-less rows (tam 51%, corpus UK 99.4%) need CIN/name-based resolution; three divergent `Company Ops scrap100` workbooks should be deduped by LinkedIn URL.

---

## Appendix A. Full SQLite schemas (tables, columns, row counts, indexes)

Format: `TABLE name rows=N cols=M` then `column TYPE [PK]`. Generated with `PRAGMA table_info` against read-only immutable copies.

### A.1 companyOps/tam/data/tam.sqlite3 (user_version 6, journal delete)

```
PATH companyOps/tam/data/tam.sqlite3 SIZE 640278528
user_version (6,) journal ('delete',)

TABLE async_jobs rows=0 cols=8
  id INTEGER PK, vendor TEXT, request_id TEXT, payload TEXT, status TEXT, attempts INT, created_at TEXT, resolved_at TEXT

TABLE categories rows=13 cols=7
  id INTEGER PK, key TEXT, name TEXT, status TEXT, hubspot_segment TEXT, config_path TEXT, created_at TEXT

TABLE category_sources rows=15 cols=16
  category_id INT PK, source TEXT PK, tier TEXT, status TEXT, access_method TEXT, url TEXT, robots_status TEXT, blocked_reason TEXT, rows_total INT, rows_new_total INT, attempts INT, last_attempt_at TEXT, last_success_at TEXT, saturated INT, notes TEXT, updated_at TEXT

TABLE classifications rows=16905 cols=18
  company_id INT PK, category_id INT PK, keyword_score REAL, keyword_hits TEXT, segments TEXT, primary_segment TEXT, icp_bucket TEXT, confidence REAL, has_own_tech_product INT, india_hq TEXT, company_type TEXT, evidence_quote TEXT, evidence_url TEXT, pm_tooling_signals TEXT, notes TEXT, model TEXT, prompt_version TEXT, classified_at TEXT

TABLE companies rows=41363 cols=26
  id INTEGER PK, root_domain TEXT, linkedin_url TEXT, name TEXT, name_norm TEXT, alt_domains TEXT, hq_city TEXT, hq_state TEXT, hq_country TEXT, india_hq TEXT, employee_count INT, headcount_band TEXT, founded_year INT, funding_stage TEXT, total_funding_usd REAL, office_phone TEXT, google_place_id TEXT, apollo_org_id TEXT, site_status TEXT, final_url TEXT, first_seen_at TEXT, last_seen_at TEXT, legal_name TEXT, cin TEXT, domain_confidence REAL, merged_into INT

TABLE company_categories rows=28625 cols=16
  company_id INT PK, category_id INT PK, segment TEXT, icp_bucket TEXT, confidence REAL, score REAL, priority_tier TEXT, first_seen_at TEXT, updated_at TEXT, regulated_status TEXT, regulatory_sensitivity TEXT, parked_as TEXT, funnel_stage TEXT, funnel_bucket TEXT, funnel_reason TEXT, funnel_updated_at TEXT

TABLE contacts rows=0 cols=27
  id INTEGER PK, company_id INT, apollo_person_id TEXT, full_name TEXT, first_name TEXT, last_name TEXT, title TEXT, seniority TEXT, role_rank INT, linkedin_url TEXT, email TEXT, email_status TEXT, email_verified_at TEXT, phone_mobile TEXT, phone_other TEXT, phone_source TEXT, in_salesnav INT, enrich_state TEXT, created_at TEXT, updated_at TEXT, category_id INT, funnel_stage TEXT, funnel_bucket TEXT, funnel_reason TEXT, funnel_updated_at TEXT, email_bucket TEXT, phone_bucket TEXT

TABLE cost_ledger rows=2634 cols=9
  id INTEGER PK, run_id INT, category_id INT, vendor TEXT, unit TEXT, qty REAL, usd_est REAL, note TEXT, at TEXT

TABLE crm_pushes rows=1126 cols=13
  id INTEGER PK, company_id INT, category_id INT, crm TEXT, deal_id TEXT, contact_id TEXT, owner TEXT, pipeline TEXT, dealstage TEXT, segment TEXT, status TEXT, note TEXT, pushed_at TEXT

TABLE funnel_events rows=224846 cols=10
  id INTEGER PK, category_id INT, entity_type TEXT, entity_id INT, stage TEXT, bucket TEXT, reason TEXT, source TEXT, run_id INT, at TEXT

TABLE outreach_batches rows=0 cols=5
  id INTEGER PK, batch_no INT, contact_id INT, channel TEXT, created_at TEXT

TABLE pages rows=43523 cols=10
  id INTEGER PK, company_id INT, url TEXT, page_type TEXT, title TEXT, text TEXT, markdown TEXT, http_status INT, text_hash TEXT, crawled_at TEXT

TABLE raw_sources rows=49457 cols=18
  id INTEGER PK, category_id INT, source TEXT, source_ref TEXT, run_id INT, company_name TEXT, website TEXT, root_domain TEXT, linkedin_url TEXT, city TEXT, state TEXT, country TEXT, phone TEXT, segment_hint TEXT, signal_text TEXT, payload_json TEXT, fetched_at TEXT, company_id INT

TABLE salesnav_import rows=0 cols=10
  id INTEGER PK, file TEXT, company_name TEXT, company_linkedin_url TEXT, website TEXT, root_domain TEXT, person_name TEXT, person_title TEXT, person_linkedin_url TEXT, imported_at TEXT

TABLE signalhire_results rows=529 cols=6
  item TEXT PK, status TEXT, mobile TEXT, email TEXT, raw TEXT, received_at TEXT

TABLE signals rows=17032 cols=12
  company_id INT PK, mx_provider TEXT, mx_records TEXT, techs TEXT, uses_jira INT, uses_slack INT, uses_asana INT, uses_clickup INT, uses_gworkspace INT, jira_scrum_mentions INT, open_jobs INT, updated_at TEXT

TABLE stage_runs rows=31 cols=13
  id INTEGER PK, category_id INT, stage TEXT, source TEXT, args TEXT, started_at TEXT, finished_at TEXT, status TEXT, rows_in INT, rows_new INT, rows_seen INT, saturated INT, error TEXT
  INDEX ix_cc_cat on company_categories: CREATE INDEX ix_cc_cat ON company_categories(category_id, icp_bucket)
  INDEX ix_cc_funnel on company_categories: CREATE INDEX ix_cc_funnel ON company_categories(category_id, funnel_stage, funnel_bucket)
  INDEX ix_co_legal on companies: CREATE INDEX ix_co_legal ON companies(legal_name)
  INDEX ix_co_li on companies: CREATE INDEX ix_co_li ON companies(linkedin_url)
  INDEX ix_co_merged on companies: CREATE INDEX ix_co_merged ON companies(merged_into)
  INDEX ix_co_name on companies: CREATE INDEX ix_co_name ON companies(name_norm)
  INDEX ix_co_namenorm_nodom on companies: CREATE INDEX ix_co_namenorm_nodom ON companies(name_norm) WHERE root_domain IS NULL
  INDEX ix_crm_company on crm_pushes: CREATE INDEX ix_crm_company ON crm_pushes(company_id)
  INDEX ix_ct_funnel on contacts: CREATE INDEX ix_ct_funnel ON contacts(category_id, funnel_stage, funnel_bucket)
  INDEX ix_fe_cat on funnel_events: CREATE INDEX ix_fe_cat ON funnel_events(category_id, stage, bucket)
  INDEX ix_fe_entity on funnel_events: CREATE INDEX ix_fe_entity ON funnel_events(entity_type, entity_id, stage)
  INDEX ix_pages_company on pages: CREATE INDEX ix_pages_company ON pages(company_id)
  INDEX ix_pages_url on pages: CREATE INDEX ix_pages_url ON pages(url)
  INDEX ix_rs_domain on raw_sources: CREATE INDEX ix_rs_domain ON raw_sources(root_domain)
  INDEX ux_crm_deal on crm_pushes: CREATE UNIQUE INDEX ux_crm_deal ON crm_pushes(crm, deal_id) WHERE deal_id IS NOT NULL
  INDEX ux_ct_li on contacts: CREATE UNIQUE INDEX ux_ct_li ON contacts(linkedin_url) WHERE linkedin_url IS NOT NULL
```

### A.2 hubspot/itsvc-tam/itsvc.db

```
PATH hubspot/itsvc-tam/itsvc.db SIZE 138166272
user_version (0,) journal ('delete',)

TABLE candidates rows=61799 cols=33
  id INTEGER PK, raw_id INTEGER, source TEXT, name TEXT, legal_name TEXT, cin TEXT, llpin TEXT, gstin TEXT, pan TEXT, domain TEXT, website TEXT, linkedin_url TEXT, phone_e164 TEXT, email_generic TEXT, address TEXT, city TEXT, state TEXT, pincode TEXT, lat REAL, lng REAL, headcount_raw TEXT, headcount_band TEXT, nic_code TEXT, company_class TEXT, paid_up_capital_inr REAL, incorporation_date TEXT, mca_status TEXT, mca_subcategory TEXT, description TEXT, types_json TEXT, partner_program TEXT, partner_tier TEXT, extra_json TEXT

TABLE captures rows=61799 cols=3
  entity_id TEXT, source TEXT, stratum_key TEXT

TABLE cities rows=0 cols=5
  city TEXT PK, state TEXT, tier INTEGER, bbox_json TEXT, aliases_json TEXT

TABLE classifications rows=61799 cols=16
  entity_id TEXT, model TEXT, prompt_version TEXT, builds_software INTEGER, dev_intensity INTEGER, subsegments_json TEXT, tags_json TEXT, exclusion_flags_json TEXT, tech_stack_json TEXT, erp_platforms_json TEXT, export_focus INTEGER, evidence_json TEXT, confidence REAL, rules_applied_json TEXT, cost_usd REAL, created_at TEXT

TABLE cost_ledger rows=0 cols=6
  id INTEGER PK, provider TEXT, units REAL, usd REAL, ts TEXT, task_key TEXT

TABLE entities rows=61799 cols=13
  entity_id TEXT PK, canonical_name TEXT, legal_name TEXT, cin TEXT, llpin TEXT, domain TEXT, linkedin_url TEXT, city TEXT, state TEXT, city_tier INTEGER, headcount_band TEXT, first_seen TEXT, last_seen TEXT

TABLE entity_members rows=0 cols=4
  entity_id TEXT PK, candidate_id INTEGER PK, rule_id TEXT, score REAL

TABLE er_decisions rows=0 cols=7
  id INTEGER PK, a_candidate INTEGER, b_candidate INTEGER, rule_id TEXT, score REAL, decision TEXT, ts TEXT

TABLE partner_badges rows=0 cols=5
  entity_id TEXT, program TEXT, tier TEXT, url TEXT, fetched_at TEXT

TABLE raw_records rows=61799 cols=7
  id INTEGER PK, source TEXT, source_url TEXT, fetched_at TEXT, raw_sha256 TEXT, payload_path TEXT, task_key TEXT

TABLE site_text rows=7824 cols=8
  entity_id TEXT PK, domain TEXT, status TEXT, http_code INTEGER, text TEXT, cin TEXT, email_generic TEXT, fetched_at TEXT

TABLE suppression rows=0 cols=7
  id INTEGER PK, entity_id TEXT, domain TEXT, email TEXT, phone_e164 TEXT, reason TEXT, ts TEXT
  INDEX idx_candidates_cin on candidates: CREATE INDEX idx_candidates_cin ON candidates(cin)
  INDEX idx_candidates_city on candidates: CREATE INDEX idx_candidates_city ON candidates(city)
  INDEX idx_candidates_domain on candidates: CREATE INDEX idx_candidates_domain ON candidates(domain)
  INDEX idx_candidates_phone on candidates: CREATE INDEX idx_candidates_phone ON candidates(phone_e164)
  INDEX idx_captures_entity on captures: CREATE INDEX idx_captures_entity ON captures(entity_id)
  INDEX idx_entities_domain on entities: CREATE INDEX idx_entities_domain ON entities(domain)
```

### A.3 hubspot/TAMBuildSpecs/_corpus/data/tam_corpus.sqlite (matches _corpus/schema.sql)

```
PATH hubspot/TAMBuildSpecs/_corpus/data/tam_corpus.sqlite SIZE 326254592
user_version (0,) journal ('delete',)
VIEW v_accounts on v_accounts
VIEW v_corpus_summary on v_corpus_summary
VIEW v_source_health on v_source_health

TABLE budget_ledger rows=0 cols=9
  id INTEGER PK, provider TEXT, vertical_id INTEGER, units TEXT, qty REAL, cost_usd REAL, stage TEXT, run_id TEXT, created_at TEXT

TABLE company rows=302963 cols=20
  id INTEGER PK, vertical_id INTEGER, display_name TEXT, legal_name TEXT, name_norm TEXT, root_domain TEXT, website TEXT, linkedin_url TEXT, country_code TEXT, region TEXT, postcode TEXT, headcount_est INTEGER, headcount_band TEXT, headcount_source TEXT, founded_year INTEGER, status TEXT, status_detail TEXT, status_as_of TEXT, first_seen_at TEXT, last_updated_at TEXT

TABLE company_identifier rows=311027 cols=7
  id INTEGER PK, company_id INTEGER, id_type TEXT, id_value TEXT, is_primary INTEGER, source_id INTEGER, confidence REAL

TABLE company_link rows=0 cols=9
  id INTEGER PK, company_id INTEGER, link_type TEXT, target_name TEXT, target_company_id INTEGER, target_ref TEXT, effective_date TEXT, source_id INTEGER, confidence REAL

TABLE company_source rows=303087 cols=5
  company_id INTEGER PK, source_id INTEGER PK, attempt_id INTEGER, first_seen_at TEXT, source_ref TEXT

TABLE contact rows=0 cols=21
  id INTEGER PK, company_id INTEGER, full_name TEXT, title TEXT, title_bucket TEXT, seniority_rank INTEGER, linkedin_url TEXT, work_email TEXT, email_status TEXT, phone_e164 TEXT, phone_type TEXT, phone_source TEXT, subscriber_type TEXT, dnc_checked INTEGER, dnc_registered INTEGER, dnc_checked_at TEXT, dnc_provider_ref TEXT, opted_out INTEGER, legal_basis_ref TEXT, source_id INTEGER, obtained_at TEXT

TABLE icp_rule rows=33 cols=11
  id INTEGER PK, vertical_id INTEGER, slug TEXT, display_name TEXT, rule_kind TEXT, indicator_slug TEXT, expression TEXT, weight REAL, max_points REAL, group_name TEXT, active INTEGER

TABLE icp_score rows=301345 cols=14
  id INTEGER PK, company_id INTEGER, vertical_id INTEGER, score REAL, score_raw REAL, band TEXT, match_label TEXT, confidence REAL, segments TEXT, scored_by TEXT, model_id TEXT, prompt_version TEXT, rationale TEXT, scored_at TEXT

TABLE icp_score_component rows=640833 cols=6
  id INTEGER PK, icp_score_id INTEGER, rule_id INTEGER, rule_slug TEXT, points REAL, detail TEXT

TABLE icp_segment rows=11 cols=5
  id INTEGER PK, vertical_id INTEGER, slug TEXT, display_name TEXT, description TEXT

TABLE indicator rows=652128 cols=12
  id INTEGER PK, company_id INTEGER, indicator_id INTEGER, value_bool INTEGER, value_num REAL, value_text TEXT, confidence REAL, evidence_snippet TEXT, evidence_url TEXT, source_id INTEGER, raw_document_id INTEGER, observed_at TEXT

TABLE indicator_def rows=57 cols=10
  id INTEGER PK, slug TEXT, display_name TEXT, category TEXT, value_type TEXT, enum_values TEXT, polarity TEXT, description TEXT, vertical_id INTEGER, created_at TEXT

TABLE raw_document rows=2 cols=7
  id INTEGER PK, source_id INTEGER, attempt_id INTEGER, url TEXT, content_hash TEXT, payload_path TEXT, fetched_at TEXT

TABLE source rows=28 cols=17
  id INTEGER PK, slug TEXT, display_name TEXT, kind TEXT, base_url TEXT, country_scope TEXT, auth_required INTEGER, auth_env_key TEXT, is_paid INTEGER, cost_model TEXT, licence_note TEXT, robots_policy TEXT, tos_scrapable INTEGER, reliability TEXT, notes TEXT, first_used_at TEXT, last_used_at TEXT

TABLE source_attempt rows=6142 cols=14
  id INTEGER PK, source_id INTEGER, vertical_id INTEGER, url TEXT, method TEXT, attempted_at TEXT, http_status INTEGER, outcome TEXT, blocked_reason TEXT, bytes INTEGER, rows_yielded INTEGER, elapsed_ms INTEGER, run_id TEXT, notes TEXT

TABLE stage_run rows=13 cols=14
  id INTEGER PK, run_id TEXT, vertical_id INTEGER, stage TEXT, mode TEXT, input_hash TEXT, cursor TEXT, status TEXT, rows_in INTEGER, rows_out INTEGER, cost_usd REAL, started_at TEXT, finished_at TEXT, notes TEXT

TABLE suppression rows=0 cols=5
  id INTEGER PK, kind TEXT, value TEXT, reason TEXT, added_at TEXT

TABLE verify_item rows=32 cols=8
  id INTEGER PK, vertical_id INTEGER, ref TEXT, item TEXT, status TEXT, evidence_url TEXT, finding TEXT, checked_at TEXT

TABLE vertical rows=2 cols=9
  id INTEGER PK, slug TEXT, display_name TEXT, region TEXT, country_code TEXT, spec_path TEXT, config_path TEXT, status TEXT, created_at TEXT
  INDEX ix_company_domain on company: CREATE INDEX ix_company_domain ON company(root_domain)
  INDEX ix_company_status on company: CREATE INDEX ix_company_status ON company(status)
  INDEX ix_component_score on icp_score_component: CREATE INDEX ix_component_score ON icp_score_component(icp_score_id)
  INDEX ix_icp_score_band on icp_score: CREATE INDEX ix_icp_score_band ON icp_score(vertical_id, band)
  INDEX ix_icp_score_score on icp_score: CREATE INDEX ix_icp_score_score ON icp_score(vertical_id, score DESC)
  INDEX ix_ident_lookup on company_identifier: CREATE INDEX ix_ident_lookup ON company_identifier(id_type, id_value)
  INDEX ix_indicator_company on indicator: CREATE INDEX ix_indicator_company ON indicator(company_id)
  INDEX ix_indicator_def on indicator: CREATE INDEX ix_indicator_def ON indicator(indicator_id)
  INDEX ux_company_vert_norm on company: CREATE UNIQUE INDEX ux_company_vert_norm ON company(vertical_id, name_norm)
```

### A.4 hubspot/lh2-pipeline/data/pipeline.sqlite

```
PATH hubspot/lh2-pipeline/data/pipeline.sqlite SIZE 10342400
user_version (0,) journal ('delete',)

TABLE cache rows=1391 cols=3
  key TEXT PK, value_json TEXT, created_at TEXT

TABLE companies rows=5369 cols=18
  domain TEXT PK, company_name TEXT, website TEXT, city TEXT, state TEXT, hq_country TEXT, founded_year INTEGER, founded_source TEXT, size_band TEXT, size_source TEXT, size_bucket TEXT, segment TEXT, status TEXT, sources_json TEXT, gate_pass INTEGER, gate_reason TEXT, created_at TEXT, updated_at TEXT

TABLE crm_feedback rows=0 cols=7
  email TEXT PK, domain TEXT, call_outcome TEXT, call_notes TEXT, call_date TEXT, next_step TEXT, pulled_at TEXT

TABLE no_domain rows=0 cols=6
  id INTEGER PK, company_name TEXT, city TEXT, source TEXT, source_url TEXT, created_at TEXT

TABLE people rows=1480 cols=15
  id INTEGER PK, domain TEXT, name TEXT, role TEXT, name_source TEXT, linkedin_url TEXT, linkedin_source TEXT, linkedin_confirmed INTEGER, phone TEXT, phone_source TEXT, email TEXT, email_source TEXT, is_primary INTEGER, confidence TEXT, notes TEXT

TABLE quota rows=6 cols=6
  provider TEXT PK, metric TEXT PK, window_key TEXT PK, used INTEGER, limit_value INTEGER, updated_at TEXT

TABLE raw_listings rows=5455 cols=11
  id INTEGER PK, source TEXT, source_url TEXT, scraped_at TEXT, company_name TEXT, website_raw TEXT, city TEXT, founded_year_raw TEXT, size_raw TEXT, segment_raw TEXT, extra_json TEXT
  INDEX ix_companies_gate on companies: CREATE INDEX ix_companies_gate ON companies(gate_pass)
  INDEX ix_feedback_domain on crm_feedback: CREATE INDEX ix_feedback_domain ON crm_feedback(domain)
  INDEX ix_people_domain on people: CREATE INDEX ix_people_domain ON people(domain)
  INDEX ix_raw_city on raw_listings: CREATE INDEX ix_raw_city        ON raw_listings(city)
  INDEX ix_raw_source on raw_listings: CREATE INDEX ix_raw_source      ON raw_listings(source)
  INDEX ux_people_domain_name on people: CREATE UNIQUE INDEX ux_people_domain_name ON people(domain, name)
  INDEX ux_raw_src_url on raw_listings: CREATE UNIQUE INDEX ux_raw_src_url ON raw_listings(source, source_url, company_name)
```

### A.5 hubspot/lh2-pipeline/data/gmaps_cache.sqlite

```
PATH hubspot/lh2-pipeline/data/gmaps_cache.sqlite SIZE 909312
user_version (0,) journal ('delete',)

TABLE tiles rows=44 cols=6
  k TEXT PK, query TEXT, bbox TEXT, places TEXT, calls INT, at TEXT

TABLE usage rows=1 cols=2
  month TEXT PK, calls INT
```

### A.6 hubspot/godown/shutdown-radar/data/radar.sqlite

```
PATH hubspot/godown/shutdown-radar/data/radar.sqlite SIZE 14024704
user_version (0,) journal ('delete',)

TABLE backfill_progress rows=0 cols=5
  state_code TEXT PK, last_offset INTEGER, total INTEGER, done INTEGER, updated_at TEXT

TABLE candidate rows=10957 cols=5
  id INTEGER PK, brand_name TEXT, brand_name_norm TEXT, first_seen_at TEXT, discovery_source TEXT

TABLE candidate_entity rows=10809 cols=5
  candidate_id INTEGER PK, cin TEXT PK, match_score REAL, match_method TEXT, is_confirmed INTEGER

TABLE dpiit_startup rows=0 cols=6
  id INTEGER PK, name TEXT, name_norm TEXT, state TEXT, sector TEXT, recognition_date TEXT

TABLE entity rows=10816 cols=10
  cin TEXT PK, legal_name TEXT, legal_name_norm TEXT, company_status TEXT, company_class TEXT, date_of_registration TEXT, registered_state TEXT, roc TEXT, source TEXT, fetched_at TEXT

TABLE evidence rows=11018 cols=12
  id INTEGER PK, candidate_id INTEGER, kind TEXT, source_url TEXT, source_name TEXT, published_at TEXT, as_of_date TEXT, snippet TEXT, matched_pattern TEXT, raw_json TEXT, fetched_at TEXT, dedupe_key TEXT

TABLE liveness rows=83 cols=10
  candidate_id INTEGER PK, domain TEXT, dns_resolves INTEGER, http_status INTEGER, http_final_url TEXT, ssl_expired INTEGER, rdap_status TEXT, wayback_last_capture TEXT, liveness_score REAL, checked_at TEXT

TABLE prequal rows=400 cols=16
  candidate_id INTEGER PK, prequal_total INTEGER, prequal_max_possible INTEGER, prequal_pct REAL, prequal_routing TEXT, prequal_note TEXT, prequal_unscored TEXT, pq_funding INTEGER, pq_years INTEGER, pq_eng_headcount INTEGER, pq_sector INTEGER, pq_code_location INTEGER, pq_legal_status INTEGER, github_org TEXT, github_url TEXT, scored_at TEXT

TABLE run_stat rows=11 cols=2
  k TEXT PK, v TEXT

TABLE scored rows=10957 cols=12
  candidate_id INTEGER PK, confidence REAL, tier TEXT, shutdown_date TEXT, shutdown_year INTEGER, reason TEXT, sector TEXT, city TEXT, funding_usd TEXT, investors TEXT, rationale TEXT, scored_at TEXT
  INDEX ix_cand_norm on candidate: CREATE INDEX ix_cand_norm ON candidate(brand_name_norm)
  INDEX ix_dpiit_norm on dpiit_startup: CREATE INDEX ix_dpiit_norm ON dpiit_startup(name_norm)
  INDEX ix_entity_norm on entity: CREATE INDEX ix_entity_norm ON entity(legal_name_norm)
  INDEX ix_ev_cand on evidence: CREATE INDEX ix_ev_cand ON evidence(candidate_id)
```

### A.7 hubspot/godown/founder_id/resolver.sqlite

```
PATH hubspot/godown/founder_id/resolver.sqlite SIZE 319488
user_version (0,) journal ('delete',)

TABLE companies rows=420 cols=5
  domain  PK, status , cin , last_stage , data 

TABLE counters rows=4 cols=3
  source  PK, day  PK, n 

TABLE walls rows=27 cols=3
  source , ts , code
```

### A.8 hubspot/godown/founder_id/search_ledger.db

```
PATH hubspot/godown/founder_id/search_ledger.db SIZE 196608
user_version (0,) journal ('delete',)

TABLE calls rows=2038 cols=8
  id INTEGER PK, ts_utc REAL, company TEXT, http_status INTEGER, profiles_returned INTEGER, size_used INTEGER, total_reported INTEGER, run_id TEXT

TABLE events rows=39 cols=3
  ts_utc REAL, kind TEXT, note TEXT
  INDEX ix_calls_ts on calls: CREATE INDEX ix_calls_ts ON calls(ts_utc)
```

### A.9 hubspot/godown/itdirs/state/scrape_state.sqlite

```
PATH hubspot/godown/itdirs/state/scrape_state.sqlite SIZE 24576
user_version (0,) journal ('delete',)

TABLE counters rows=0 cols=3
  source TEXT, k TEXT, v INT

TABLE pages rows=34 cols=2
  url TEXT PK, ts INT

TABLE redirects rows=0 cols=2
  tracker TEXT PK, domain TEXT
```
