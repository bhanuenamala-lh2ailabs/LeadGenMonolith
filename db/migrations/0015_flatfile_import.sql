-- 0015_flatfile_import.sql
-- Generic staging layer for the CSV / XLSX / JSON "excel-sheet databases" of the three legacy repos
-- (companyOps, RapidActionTeam, hubspot): lead batches, reserve / held / holding pools, campaign-leads exports, enriched CSVs,
-- inbound xlsx, stale JSON mirrors of HubSpot.  Those files have heterogeneous columns, so nothing is forced into the canonical
-- tables here: every file is catalogued (ext_file_catalog) and every data row is kept verbatim as JSON (ext_file_row) with a
-- handful of extracted lookup keys.  Written by leadgen/legacy_import/flatfiles.py; see docs/IMPORT_FLATFILES.md.
--
-- Rules the importer and every reader must follow:
--   * Staging only.  Nothing here is canonical: ext_file_row never overwrites company / contact / deal data, and a stale mirror
--     (ext_file_catalog.is_stale_mirror = 1, e.g. hubspot/crm_mirror/data/deals.json, frozen 2026-07-29) must never be preferred over
--     a live HubSpot pull.
--   * NEVER PUSH.  Files flagged is_never_push = 1 (the whale lists) are staged for local reference only.  The authoritative block is
--     the suppression table (kind 'never_push'); any outbound path must still call leadgen.suppression.assert_pushable().  Do not
--     build push batches from ext_file_row without that gate.
--   * Idempotent: a file is identified by its repo-relative path; the same sha256 is skipped, a changed sha256 replaces the file's
--     rows (DELETE + INSERT inside one transaction).  Byte-identical copies of another file are catalogued with dup_of_file_id and
--     store no rows (the RAT_CAD workbook exists five times across the three repos).
--   * PII: rows hold personal data (names, e-mails, phones, LinkedIn URLs) exactly as the legacy files did.  Importers and reports
--     print counts only.
--   * Linking (company_id / contact_id) is a separate, re-runnable pass (flatfiles.link_files) that uses STRONG keys only
--     (company_identifier root_domain / linkedin_company; a unique contact e-mail / person LinkedIn).  Shared or ambiguous keys stay unlinked.

-- ---------------------------------------------------------------------------------------------
-- ext_file_catalog: one row per legacy data file seen (loaded, duplicate, or skipped with a reason).
-- ---------------------------------------------------------------------------------------------
CREATE TABLE ext_file_catalog (
  file_id          INTEGER PRIMARY KEY,  -- surrogate key
  path             TEXT    NOT NULL UNIQUE CHECK (path <> '' AND path = trim(path) AND path NOT GLOB '/*' AND path NOT GLOB '*\*'),  -- path relative to the repo root, '/' separators (e.g. legacy/hubspot/reserve/LEADS_10_untouched.csv)
  source_repo      TEXT    NOT NULL CHECK (source_repo IN ('companyOps', 'RapidActionTeam', 'hubspot')),  -- legacy repo the file lives in
  sha256           TEXT    NOT NULL CHECK (length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'),  -- sha256 of the file bytes: the idempotency key (same = skip, changed = replace rows)
  size_bytes       INTEGER NOT NULL CHECK (size_bytes >= 0),  -- file size in bytes when imported
  mtime_at         TEXT    CHECK (mtime_at IS NULL OR (mtime_at IS strftime('%Y-%m-%dT%H:%M:%fZ', mtime_at) AND mtime_at NOT GLOB '*T24:*')),  -- file modification time (UTC) when imported
  kind             TEXT    NOT NULL CHECK (kind IN ('csv', 'tsv', 'xlsx', 'xls', 'json')),  -- file format (delimited text files keep their detected delimiter in `delimiter`)
  role             TEXT    NOT NULL CHECK (role IN ('source_of_truth', 'mirror', 'export', 'regenerable', 'pool', 'skipped')),  -- source_of_truth (cannot be rebuilt) / mirror (copy of another system, may be stale) / export (point-in-time output) / regenerable (rebuildable from a DB or API) / pool (candidate, reserve or held lead list) / skipped (catalogued only)
  skip_reason      TEXT    CHECK (skip_reason IS NULL OR skip_reason <> ''),  -- why role = 'skipped' (mandatory then)
  rows             INTEGER CHECK (rows IS NULL OR rows >= 0),  -- data rows parsed across all sheets (header rows and fully empty rows excluded); NULL when skipped
  rows_loaded      INTEGER NOT NULL DEFAULT 0 CHECK (rows_loaded >= 0),  -- rows stored in ext_file_row (0 for duplicates and skipped files)
  columns_json     TEXT    NOT NULL DEFAULT '{}' CHECK (json_valid(columns_json) AND json_type(columns_json) = 'object'),  -- {sheet_name: [column headers]}; delimited text and JSON files use the single sheet name 'csv' / 'json'
  encoding         TEXT,  -- text encoding detected for csv / tsv (utf-8, utf-8-sig, cp1252, latin-1)
  delimiter        TEXT,  -- field delimiter detected for csv / tsv
  dup_of_file_id   INTEGER REFERENCES ext_file_catalog(file_id),  -- byte-identical file whose rows are the ones stored (this copy stores none)
  is_never_push    INTEGER NOT NULL DEFAULT 0 CHECK (is_never_push IN (0, 1)),  -- 1 = whale list: staged for reference only, never a push source (see suppression kind 'never_push')
  is_stale_mirror  INTEGER NOT NULL DEFAULT 0 CHECK (is_stale_mirror IN (0, 1)),  -- 1 = frozen copy of a live system (HubSpot crm_mirror, 2026-07-29): never overwrite canonical data with it
  notes            TEXT,  -- importer remarks (JSON meta of an index file, sheet warnings, ragged-row counts)
  import_run_id    INTEGER REFERENCES ops_import_run(import_run_id) ON DELETE SET NULL,
  imported_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (imported_at IS strftime('%Y-%m-%dT%H:%M:%fZ', imported_at) AND imported_at NOT GLOB '*T24:*'),  -- when the current content was loaded
  CHECK ((role = 'skipped') = (skip_reason IS NOT NULL)),
  CHECK (role <> 'skipped' OR (rows IS NULL AND rows_loaded = 0)),
  CHECK (dup_of_file_id IS NULL OR (rows_loaded = 0 AND role <> 'skipped')),
  CHECK (rows_loaded <= COALESCE(rows, 0))
) STRICT;
CREATE INDEX ix_ext_file_catalog_sha  ON ext_file_catalog(sha256);
CREATE INDEX ix_ext_file_catalog_role ON ext_file_catalog(role, source_repo);
CREATE INDEX ix_ext_file_catalog_dup  ON ext_file_catalog(dup_of_file_id) WHERE dup_of_file_id IS NOT NULL;

-- ---------------------------------------------------------------------------------------------
-- ext_file_row: one row per data row of a catalogued file (every sheet of a workbook).  row_json is the row as an object keyed by the
-- file's column headers (duplicate headers get a __2 suffix, blank ones col_N; cells that are empty are omitted; CSV values stay
-- strings, xlsx numbers / booleans stay typed, dates are ISO text).  The extracted columns are lookup keys only, taken from the
-- columns the importer recognises as a domain / website, a LinkedIn URL, an e-mail, a phone or a company name; they use the same
-- normalisers as the canonical tables (leadgen.norm).  A generic host (linkedin.com, gmail.com ...) is never stored as a domain.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE ext_file_row (
  row_id                INTEGER PRIMARY KEY,  -- surrogate key
  file_id               INTEGER NOT NULL REFERENCES ext_file_catalog(file_id) ON DELETE CASCADE,  -- owning file
  sheet_name            TEXT    NOT NULL CHECK (sheet_name <> ''),  -- workbook sheet; 'csv' / 'json' for single-table files
  row_no                INTEGER NOT NULL CHECK (row_no >= 1),  -- 1-based position of the data row inside its sheet (the header row is not counted; blank CSV rows still advance it, so row_no follows the file)
  row_json              TEXT    NOT NULL CHECK (json_valid(row_json) AND json_type(row_json) = 'object'),  -- the row, verbatim (see table comment)
  company_domain_norm   TEXT    CHECK (company_domain_norm IS NULL OR (company_domain_norm = lower(company_domain_norm) AND instr(company_domain_norm, '.') > 1
                                       AND company_domain_norm NOT GLOB '*[^a-z0-9.-]*' AND company_domain_norm NOT GLOB 'www.*' AND company_domain_norm NOT GLOB '*..*')),  -- root domain of the row's company (lower-case, no www): matches company_identifier 'root_domain'
  linkedin_norm         TEXT    CHECK (linkedin_norm IS NULL OR (linkedin_norm = lower(linkedin_norm) AND (linkedin_norm GLOB 'company/*' OR linkedin_norm GLOB 'school/*' OR linkedin_norm GLOB 'showcase/*')
                                       AND linkedin_norm NOT GLOB '*/' AND linkedin_norm NOT GLOB '* *')),  -- company LinkedIn slug 'company/<slug>': matches company_identifier 'linkedin_company'
  person_linkedin_norm  TEXT    CHECK (person_linkedin_norm IS NULL OR (person_linkedin_norm = lower(person_linkedin_norm) AND person_linkedin_norm GLOB 'in/*'
                                       AND person_linkedin_norm NOT GLOB '*/' AND person_linkedin_norm NOT GLOB '* *')),  -- person LinkedIn 'in/<slug>': matches contact.linkedin_person_norm
  email_norm            TEXT    CHECK (email_norm IS NULL OR (email_norm = lower(email_norm) AND instr(email_norm, '@') > 1 AND email_norm NOT GLOB '* *')),  -- first valid e-mail of the row, lower-case: matches contact.email_norm
  phone_e164            TEXT    CHECK (phone_e164 IS NULL OR (phone_e164 GLOB '+[1-9]*' AND phone_e164 NOT GLOB '+*[^0-9]*')),  -- first phone the row carries that normalises to E.164 (Indian 10-digit numbers assumed +91; no mobile classification)
  company_name_norm     TEXT    CHECK (company_name_norm IS NULL OR (company_name_norm = lower(company_name_norm) AND company_name_norm <> '')),  -- lower-case, whitespace-collapsed company name (display / weak matching only, never a link key)
  company_id            INTEGER REFERENCES company(company_id),  -- golden company (merge survivor) linked by link_files() through a strong key; NULL = unlinked or ambiguous
  company_link_method   TEXT    CHECK (company_link_method IS NULL OR company_link_method IN ('root_domain', 'linkedin_company', 'root_domain+linkedin_company')),  -- which strong key(s) justified company_id
  contact_id            INTEGER REFERENCES contact(contact_id),  -- golden contact linked by link_files() through a unique person LinkedIn or e-mail
  contact_link_method   TEXT    CHECK (contact_link_method IS NULL OR contact_link_method IN ('linkedin_person', 'email', 'linkedin_person+email')),  -- which key(s) justified contact_id
  linked_at             TEXT    CHECK (linked_at IS NULL OR (linked_at IS strftime('%Y-%m-%dT%H:%M:%fZ', linked_at) AND linked_at NOT GLOB '*T24:*')),  -- when link_files() last set or cleared a link on this row
  UNIQUE (file_id, sheet_name, row_no),
  CHECK ((company_id IS NULL) = (company_link_method IS NULL)),
  CHECK ((contact_id IS NULL) = (contact_link_method IS NULL))
) STRICT;
CREATE INDEX ix_ext_file_row_domain   ON ext_file_row(company_domain_norm)  WHERE company_domain_norm IS NOT NULL;
CREATE INDEX ix_ext_file_row_linkedin ON ext_file_row(linkedin_norm)        WHERE linkedin_norm IS NOT NULL;
CREATE INDEX ix_ext_file_row_person   ON ext_file_row(person_linkedin_norm) WHERE person_linkedin_norm IS NOT NULL;
CREATE INDEX ix_ext_file_row_email    ON ext_file_row(email_norm)           WHERE email_norm IS NOT NULL;
CREATE INDEX ix_ext_file_row_phone    ON ext_file_row(phone_e164)           WHERE phone_e164 IS NOT NULL;
CREATE INDEX ix_ext_file_row_name     ON ext_file_row(company_name_norm)    WHERE company_name_norm IS NOT NULL;
CREATE INDEX ix_ext_file_row_company  ON ext_file_row(company_id)           WHERE company_id IS NOT NULL;
CREATE INDEX ix_ext_file_row_contact  ON ext_file_row(contact_id)           WHERE contact_id IS NOT NULL;

-- v_ext_file_summary: one line per catalogued file for the operator (counts only, no row content).
CREATE VIEW v_ext_file_summary AS
SELECT c.file_id, c.path, c.source_repo, c.kind, c.role, c.rows, c.rows_loaded, c.size_bytes, c.is_never_push, c.is_stale_mirror,
       c.dup_of_file_id, c.skip_reason, c.imported_at,
       (SELECT COUNT(*) FROM ext_file_row r WHERE r.file_id = c.file_id AND r.company_id IS NOT NULL) AS rows_linked_company,
       (SELECT COUNT(*) FROM ext_file_row r WHERE r.file_id = c.file_id AND r.contact_id IS NOT NULL) AS rows_linked_contact
FROM ext_file_catalog c;
