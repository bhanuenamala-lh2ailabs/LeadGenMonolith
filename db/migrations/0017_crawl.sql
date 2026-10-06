-- 0017_crawl.sql
-- Free Playwright crawl of TAM companies' own public websites (tools/crawl_playwright.py), feeding the AI ICP judgement.
-- Company-level public pages only.  robots.txt respected; no login, no forms, no learner/parent/tutor personal data is stored
-- (CONTEXT.md rule 9, DPDP).  Nothing here is written to HubSpot.
--
-- Rules:
--   * one row per (company, domain) attempt; a site is never silently dropped - status says why it has no text.
--   * page text is kept as fetched (truncated), with its sha256, so a later judge run can be reproduced exactly.

CREATE TABLE ext_crawl_site (
  crawl_site_id  INTEGER PRIMARY KEY,
  company_id     INTEGER NOT NULL REFERENCES legacy_tam_companies(id) ON DELETE RESTRICT,  -- the TAM company being crawled
  domain         TEXT    NOT NULL CHECK (domain <> '' AND domain = lower(domain)),  -- root domain crawled
  status         TEXT    NOT NULL CHECK (status IN ('pending', 'crawled', 'crawled_thin', 'site_unreachable', 'site_blocked', 'no_website', 'parked_domain', 'robots_disallowed', 'error')),
  pages_ok       INTEGER NOT NULL DEFAULT 0 CHECK (pages_ok >= 0),  -- pages with text
  chars          INTEGER NOT NULL DEFAULT 0 CHECK (chars >= 0),  -- total characters of text kept
  error          TEXT,  -- short reason when status is an error or block (no URLs with secrets)
  started_at     TEXT    CHECK (started_at IS NULL OR started_at IS strftime('%Y-%m-%dT%H:%M:%fZ', started_at)),
  finished_at    TEXT    CHECK (finished_at IS NULL OR finished_at IS strftime('%Y-%m-%dT%H:%M:%fZ', finished_at)),
  UNIQUE (company_id, domain)
) STRICT;
CREATE INDEX ix_ext_crawl_site_status ON ext_crawl_site(status);

CREATE TABLE ext_crawl_page (
  crawl_page_id  INTEGER PRIMARY KEY,
  crawl_site_id  INTEGER NOT NULL REFERENCES ext_crawl_site(crawl_site_id) ON DELETE RESTRICT,
  url            TEXT    NOT NULL CHECK (url LIKE 'http%'),  -- full URL fetched
  page_kind      TEXT    NOT NULL CHECK (page_kind IN ('home', 'about', 'product', 'careers', 'docs', 'api', 'help', 'integrations', 'tutors', 'blog', 'other')),
  http_status    INTEGER CHECK (http_status IS NULL OR http_status BETWEEN 100 AND 599),
  text           TEXT    CHECK (text IS NULL OR length(text) <= 20000),  -- visible text, truncated at 20,000 characters
  text_sha256    TEXT    CHECK (text_sha256 IS NULL OR (length(text_sha256) = 64 AND text_sha256 NOT GLOB '*[^0-9a-f]*')),
  fetched_at     TEXT    NOT NULL CHECK (fetched_at IS strftime('%Y-%m-%dT%H:%M:%fZ', fetched_at)),
  UNIQUE (crawl_site_id, url)
) STRICT;
CREATE INDEX ix_ext_crawl_page_site ON ext_crawl_page(crawl_site_id);

CREATE VIEW v_crawl_summary AS
SELECT status, count(*) AS sites, sum(pages_ok) AS pages_with_text, sum(chars) AS chars
  FROM ext_crawl_site GROUP BY status;
