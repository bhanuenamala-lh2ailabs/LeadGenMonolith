-- 0016_campaign_icp.sql
-- Lead-campaign intake and ICP-check results.  Written by tools/load_campaign.py (campaign files such as a LinkedIn lead-gen form or
-- Sales Navigator export) and tools/load_icp_results.py (the EdTech 800-company ICP check of 2026-10-05).
--
-- Rules every writer and reader must follow:
--   * Intake only.  A campaign lead is NOT a HubSpot record.  Before any push, dedup_status must be set by leadgen.dedup against ALL THREE
--     portals (main, companyops, rat); a lead with dedup_status <> 'new' is never pushed.  Rejects are kept (rule 8): nothing is deleted.
--   * PII: rows hold names, LinkedIn URLs, e-mails and phones as received.  Tools print counts only.
--   * pushed_* columns record what was actually created in HubSpot (deal id, owner, lead-source label) so the same lead is never pushed twice.
--   * icp_check keeps every verdict with its evidence; 'Unverified' is a result too (retryable), never silently dropped.

-- ---------------------------------------------------------------------------------------------
-- ext_campaign: one row per received campaign file / export.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE ext_campaign (
  campaign_id    INTEGER PRIMARY KEY,  -- surrogate key
  slug           TEXT    NOT NULL UNIQUE CHECK (slug <> '' AND slug = lower(slug) AND slug NOT GLOB '*[^a-z0-9_-]*'),  -- stable short name, e.g. linkedin_coding_databases_2026_10
  name           TEXT    NOT NULL CHECK (name <> ''),  -- human name of the campaign
  channel        TEXT    NOT NULL CHECK (channel IN ('linkedin_lead_form', 'linkedin_ads', 'sales_navigator', 'outflo', 'manual_csv', 'other')),  -- where the leads came from
  vertical       TEXT,  -- what is being sourced (coding_databases, edtech, ...)
  hubspot_lead_source_label TEXT,  -- the HubSpot lead_source dropdown label leads from this campaign are pushed with (e.g. LinkedinAdsLead)
  source_file    TEXT,  -- repo-relative path of the file as received
  source_sha256  TEXT    CHECK (source_sha256 IS NULL OR (length(source_sha256) = 64 AND source_sha256 NOT GLOB '*[^0-9a-f]*')),  -- sha256 of the file: idempotency key (same file = skipped)
  notes          TEXT,  -- free remarks (who supplied it, what it contains)
  received_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (received_at IS strftime('%Y-%m-%dT%H:%M:%fZ', received_at) AND received_at NOT GLOB '*T24:*')  -- when the file was loaded
) STRICT;

-- ---------------------------------------------------------------------------------------------
-- ext_campaign_lead: one row per person/row of a campaign file.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE ext_campaign_lead (
  lead_id              INTEGER PRIMARY KEY,  -- surrogate key
  campaign_id          INTEGER NOT NULL REFERENCES ext_campaign(campaign_id) ON DELETE RESTRICT,  -- campaign the row came in with
  row_no               INTEGER NOT NULL CHECK (row_no >= 1),  -- 1-based data row number in the source file
  first_name           TEXT,  -- first name as received
  last_name            TEXT,  -- last name as received
  title                TEXT,  -- job title / headline as received
  company              TEXT,  -- company name as received
  company_domain_norm  TEXT,  -- normalised website domain (leadgen.norm.norm_domain), NULL when none
  person_linkedin_norm TEXT,  -- normalised personal LinkedIn slug (leadgen.norm.norm_linkedin_person), NULL when none
  company_linkedin_norm TEXT, -- normalised company LinkedIn slug, NULL when none
  email_norm           TEXT,  -- lower-cased e-mail, NULL when none
  phone_e164           TEXT   CHECK (phone_e164 IS NULL OR phone_e164 GLOB '+[0-9]*'),  -- E.164 phone, NULL when none or unparseable
  city                 TEXT,  -- city/location as received
  country              TEXT,  -- country as received
  raw_json             TEXT    NOT NULL CHECK (json_valid(raw_json) AND json_type(raw_json) = 'object'),  -- the full source row keyed by its column headers
  dedup_status         TEXT    NOT NULL DEFAULT 'pending' CHECK (dedup_status IN ('pending', 'new', 'in_hubspot', 'suppressed', 'invalid', 'duplicate_in_file')),  -- pending = not checked yet; new = clear in all three portals; in_hubspot = a live deal exists; suppressed = never-push list; invalid = unusable row; duplicate_in_file = repeats an earlier row
  dedup_detail         TEXT,  -- which portal / key / deal matched (human-readable)
  dedup_checked_at     TEXT   CHECK (dedup_checked_at IS NULL OR dedup_checked_at IS strftime('%Y-%m-%dT%H:%M:%fZ', dedup_checked_at)),  -- when dedup last ran
  pushed_account       TEXT   CHECK (pushed_account IS NULL OR pushed_account IN ('main', 'companyops', 'rat')),  -- HubSpot account the lead was pushed to
  pushed_hs_deal_id    TEXT   CHECK (pushed_hs_deal_id IS NULL OR (pushed_hs_deal_id <> '' AND pushed_hs_deal_id NOT GLOB '*[^0-9]*')),  -- HubSpot deal id created for it
  pushed_owner         TEXT,  -- caller the deal was assigned to
  pushed_lead_source   TEXT,  -- lead_source label written on the deal
  pushed_at            TEXT   CHECK (pushed_at IS NULL OR pushed_at IS strftime('%Y-%m-%dT%H:%M:%fZ', pushed_at)),  -- when it was pushed
  created_at           TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at)),  -- when the row was loaded
  UNIQUE (campaign_id, row_no),
  CHECK ((pushed_hs_deal_id IS NULL) = (pushed_account IS NULL)),
  CHECK (pushed_hs_deal_id IS NULL OR dedup_status = 'new')
) STRICT;
CREATE INDEX ix_ext_campaign_lead_li     ON ext_campaign_lead(person_linkedin_norm) WHERE person_linkedin_norm IS NOT NULL;
CREATE INDEX ix_ext_campaign_lead_dom    ON ext_campaign_lead(company_domain_norm)  WHERE company_domain_norm IS NOT NULL;
CREATE INDEX ix_ext_campaign_lead_email  ON ext_campaign_lead(email_norm)           WHERE email_norm IS NOT NULL;
CREATE INDEX ix_ext_campaign_lead_status ON ext_campaign_lead(campaign_id, dedup_status);

-- ---------------------------------------------------------------------------------------------
-- ext_icp_check: one row per company judged against the ICP (company OPERATIONS data buyer: own software product, engineering team, India, 20-1000 people).
-- ---------------------------------------------------------------------------------------------
CREATE TABLE ext_icp_check (
  icp_check_id       INTEGER PRIMARY KEY,  -- surrogate key
  campaign_id        INTEGER NOT NULL REFERENCES ext_campaign(campaign_id) ON DELETE RESTRICT,  -- campaign / corpus the company belongs to
  company            TEXT    NOT NULL CHECK (company <> ''),  -- company name as it appeared in the source
  resolved_domain    TEXT,  -- official website domain found, NULL when none
  icp_bucket         TEXT    NOT NULL CHECK (icp_bucket IN ('Fit', 'Maybe', 'Out', 'Unverified', 'AlreadyWorked')),  -- Fit/Maybe/Out = verdict; Unverified = could not be checked (retryable); AlreadyWorked = already has a HubSpot deal, not re-judged
  verdict_source     TEXT    NOT NULL CHECK (verdict_source IN ('web_research', 'site_crawl', 'tam_corpus', 'hubspot_deal', 'none')),  -- where the verdict came from
  confidence         REAL    CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),  -- classifier confidence 0..1
  company_type       TEXT    CHECK (company_type IS NULL OR company_type IN ('product', 'services', 'agency', 'marketplace', 'enterprise', 'academic', 'unclear')),  -- kind of organisation
  has_own_tech_product INTEGER CHECK (has_own_tech_product IS NULL OR has_own_tech_product IN (0, 1)),  -- 1 = evidence it runs its own software product (required for Fit)
  india_hq           TEXT    CHECK (india_hq IS NULL OR india_hq IN ('yes', 'no', 'unknown')),  -- India headquartered
  out_reason         TEXT,  -- why Out / Maybe / Unverified
  evidence_quote     TEXT,  -- verbatim quote (<=200 chars) from the page or snippet the verdict rests on
  evidence_url       TEXT,  -- page the quote came from
  ops_data_richness  INTEGER CHECK (ops_data_richness IS NULL OR ops_data_richness BETWEEN 0 AND 3),  -- 0 none .. 3 explicit tooling stack + product docs + engineering roles
  ops_signals_json   TEXT    NOT NULL DEFAULT '[]' CHECK (json_valid(ops_signals_json) AND json_type(ops_signals_json) = 'array'),  -- concrete artefacts seen (jira, slack, api docs, ...)
  headcount          INTEGER CHECK (headcount IS NULL OR headcount >= 0),  -- headcount when known
  headcount_source   TEXT,  -- where the headcount came from (own_pages, web_search_hint, ...)
  tier               TEXT    CHECK (tier IS NULL OR tier IN ('A', 'B', 'excluded', 'unknown')),  -- A 20-500, B 501-1000, excluded otherwise, unknown = not known yet
  score              REAL    CHECK (score IS NULL OR (score >= 0 AND score <= 100)),  -- ranking score 0..100
  funnel_bucket      TEXT    NOT NULL CHECK (funnel_bucket <> ''),  -- named outcome bucket (out_academic, out_services, domain_unresolved, fit_tier_A, ...)
  hubspot_status     TEXT,  -- live HubSpot deal summary at check time (portal | pipeline | stage | owner)
  tam_verdict        TEXT,  -- verdict in the companyOps TAM corpus (category/bucket/stage)
  ready_to_procure   INTEGER NOT NULL DEFAULT 0 CHECK (ready_to_procure IN (0, 1)),  -- 1 = Fit/Maybe and not in HubSpot
  pushed_to          TEXT,  -- caller/portal if already pushed (e.g. Manit, Company Ops Cluster 1)
  poc_name           TEXT,  -- best decision-maker named in the source export
  poc_title          TEXT,  -- that person's title
  poc_linkedin_norm  TEXT,  -- that person's normalised LinkedIn slug
  checked_at         TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (checked_at IS strftime('%Y-%m-%dT%H:%M:%fZ', checked_at)),  -- when the verdict was recorded
  UNIQUE (campaign_id, company),
  CHECK (icp_bucket <> 'Fit' OR has_own_tech_product = 1),
  CHECK (ready_to_procure = 0 OR icp_bucket IN ('Fit', 'Maybe'))
) STRICT;
CREATE INDEX ix_ext_icp_check_bucket ON ext_icp_check(campaign_id, icp_bucket, funnel_bucket);
CREATE INDEX ix_ext_icp_check_ready  ON ext_icp_check(ready_to_procure) WHERE ready_to_procure = 1;

-- v_campaign_funnel: where each campaign's leads stand (counts only).
CREATE VIEW v_campaign_funnel AS
SELECT c.slug, c.channel, c.vertical, l.dedup_status, count(*) AS leads,
       sum(l.pushed_hs_deal_id IS NOT NULL) AS pushed
  FROM ext_campaign c JOIN ext_campaign_lead l ON l.campaign_id = c.campaign_id
 GROUP BY c.campaign_id, l.dedup_status;

-- v_icp_summary: ICP outcome counts per campaign.
CREATE VIEW v_icp_summary AS
SELECT c.slug, i.icp_bucket, i.funnel_bucket, count(*) AS companies, sum(i.ready_to_procure) AS ready_to_procure
  FROM ext_campaign c JOIN ext_icp_check i ON i.campaign_id = c.campaign_id
 GROUP BY c.campaign_id, i.icp_bucket, i.funnel_bucket;
