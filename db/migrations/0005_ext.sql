-- 0005_ext.sql
-- External pulls: Google Sheets (catalog / tabs / rows) and Gmail (account / labels / threads / messages / attachments),
-- with FTS5 full-text indexes.  These tables are READ-ONLY mirrors of what the pull jobs fetched; nothing is ever written
-- back to Google, and no mail is ever sent from here.
--
-- FTS DESIGN DECISION (documented once, applies to all three indexes):
--   External-content FTS5 tables (content='<table>', content_rowid='<integer pk>') kept in sync by AFTER INSERT /
--   AFTER UPDATE / AFTER DELETE triggers.  Why triggers and not an "explicit rebuild" step:
--     * the index can never be stale or forgotten: any writer (importer, ad-hoc SQL, a future job) keeps it correct;
--     * an incremental Gmail pull touches a few thousand rows a day, so trigger overhead is negligible;
--     * external content means the text is stored ONCE (in the base table), not twice.
--   Bulk-load escape hatch (first full Gmail / Sheets pull, 100k+ rows): the triggers still work, but if load speed matters
--   the loader may insert with the triggers present and then run   INSERT INTO <fts>(<fts>) VALUES('rebuild');   -
--   safe at any time, it rebuilds the index from the base table.  Consistency check (run by leadgen.db.doctor()):
--   INSERT INTO <fts>(<fts>) VALUES('integrity-check');   (raises if the index and the content table disagree).
--   Only the columns listed in each trigger's UPDATE OF clause cause re-indexing.
--
-- PII: Gmail bodies and some sheets hold personal data (the intern-hiring sheet contains applicant data).  ext_gsheet_catalog.pii_class
-- and pull_enabled let the pull job exclude or segregate such sheets; token files are referenced BY NAME only (never stored).
-- Sheets are OPT-IN: pull_enabled defaults to 0 (1,529 of the 1,570 visible sheets belong to other people), a bulk pull reads only
-- allow-listed sheets (config/gsheets_pull.yaml, flipped on by the catalog importer), ext_gsheet_exclusion is a hard deny-list that
-- triggers enforce on every insert/update of the catalog, and no ext_gsheet_row can be inserted for a pull_enabled = 0 spreadsheet.
-- Storage note: every row is stored three times (cells_json, text_flat, the FTS index) and 46.6 M cells are allocated across the
-- catalog (one workbook alone 5.7 M): keep the allow-list small.

-- =============================================================================================
-- Google Sheets
-- =============================================================================================

-- ext_gsheet_exclusion: spreadsheets that must NEVER be pulled (applicant PII ...).  A catalog row for one of these ids is forced to
-- pii_class = 'excluded', pull_enabled = 0 by trg_gsheet_catalog_exclusion_*, whatever the importer wrote.
CREATE TABLE ext_gsheet_exclusion (
  spreadsheet_id  TEXT PRIMARY KEY CHECK (spreadsheet_id <> '' AND spreadsheet_id = trim(spreadsheet_id)),  -- spreadsheet that must never be pulled
  reason          TEXT NOT NULL CHECK (reason <> ''),
  added_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (added_at IS strftime('%Y-%m-%dT%H:%M:%fZ', added_at) AND added_at NOT GLOB '*T24:*')
) STRICT;
INSERT INTO ext_gsheet_exclusion (spreadsheet_id, reason) VALUES
  ('1IPA45kJ6yTsBo8d33DM0Ay_CIfBa6jrj_wrh3A4_uiQ', 'Founder''s Office (Strategy & Ops) automation-intern hiring sheet: applicant personal data (docs/discovery/google-integrations.md section 6)');

-- ext_gsheet_catalog: one row per spreadsheet visible to our credentials (metadata only; ~1,570 files).
CREATE TABLE ext_gsheet_catalog (
  spreadsheet_id       TEXT    PRIMARY KEY CHECK (spreadsheet_id <> '' AND spreadsheet_id = trim(spreadsheet_id)),  -- Google spreadsheet id (the key)
  title                TEXT    NOT NULL,
  owner_email          TEXT,                                           -- first owner; the full list is owners_json
  owners_json          TEXT    NOT NULL DEFAULT '[]' CHECK (json_valid(owners_json) AND json_type(owners_json) = 'array'),   -- Drive `owners` array (all 1,570 happen to have exactly one)
  time_zone            TEXT,                                           -- sheet timeZone (Asia/Calcutta, America/Los_Angeles ...): matters for every date cell / formatted value
  created_time         TEXT    CHECK (created_time  IS NULL OR (created_time IS strftime('%Y-%m-%dT%H:%M:%fZ', created_time) AND created_time NOT GLOB '*T24:*')),
  modified_time        TEXT    CHECK (modified_time IS NULL OR (modified_time IS strftime('%Y-%m-%dT%H:%M:%fZ', modified_time) AND modified_time NOT GLOB '*T24:*')),
  last_modifying_user  TEXT,  -- Drive lastModifyingUser (NULL for 37 sheets)
  can_edit             INTEGER CHECK (can_edit IS NULL OR can_edit IN (0, 1)),  -- whether our credentials can edit it (we never write)
  in_shared_drive      INTEGER NOT NULL DEFAULT 0 CHECK (in_shared_drive IN (0, 1)),  -- 1 if it lives in a shared drive
  web_url              TEXT,  -- browser URL
  visible_to_json      TEXT    NOT NULL DEFAULT '[]' CHECK (json_valid(visible_to_json) AND json_type(visible_to_json) = 'array'),   -- credentials that can see it, by name
  tab_count            INTEGER CHECK (tab_count IS NULL OR tab_count >= 0),  -- number of tabs
  pii_class            TEXT    NOT NULL DEFAULT 'normal' CHECK (pii_class IN ('normal', 'personal', 'excluded')),  -- normal / personal / excluded; excluded implies pull_enabled = 0
  pull_enabled         INTEGER NOT NULL DEFAULT 0 CHECK (pull_enabled IN (0, 1)),   -- opt-in allow-list, see header
  catalog_fetched_at   TEXT    NOT NULL CHECK (catalog_fetched_at IS strftime('%Y-%m-%dT%H:%M:%fZ', catalog_fetched_at) AND catalog_fetched_at NOT GLOB '*T24:*'),
  data_pulled_at       TEXT    CHECK (data_pulled_at IS NULL OR (data_pulled_at IS strftime('%Y-%m-%dT%H:%M:%fZ', data_pulled_at) AND data_pulled_at NOT GLOB '*T24:*')),
  import_run_id        INTEGER REFERENCES ops_import_run(import_run_id) ON DELETE SET NULL,
  created_at           TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at) AND created_at NOT GLOB '*T24:*'),
  updated_at           TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (updated_at IS strftime('%Y-%m-%dT%H:%M:%fZ', updated_at) AND updated_at NOT GLOB '*T24:*'),
  CHECK (pii_class <> 'excluded' OR pull_enabled = 0)
) STRICT;
CREATE INDEX ix_gsheet_catalog_owner    ON ext_gsheet_catalog(owner_email);
CREATE INDEX ix_gsheet_catalog_modified ON ext_gsheet_catalog(modified_time);

CREATE TRIGGER trg_gsheet_catalog_exclusion_ins AFTER INSERT ON ext_gsheet_catalog
WHEN EXISTS (SELECT 1 FROM ext_gsheet_exclusion x WHERE x.spreadsheet_id = NEW.spreadsheet_id) AND (NEW.pii_class <> 'excluded' OR NEW.pull_enabled <> 0)
BEGIN
  UPDATE ext_gsheet_catalog SET pii_class = 'excluded', pull_enabled = 0 WHERE spreadsheet_id = NEW.spreadsheet_id;
END;
CREATE TRIGGER trg_gsheet_catalog_exclusion_upd AFTER UPDATE OF pii_class, pull_enabled ON ext_gsheet_catalog
WHEN EXISTS (SELECT 1 FROM ext_gsheet_exclusion x WHERE x.spreadsheet_id = NEW.spreadsheet_id) AND (NEW.pii_class <> 'excluded' OR NEW.pull_enabled <> 0)
BEGIN
  UPDATE ext_gsheet_catalog SET pii_class = 'excluded', pull_enabled = 0 WHERE spreadsheet_id = NEW.spreadsheet_id;
END;

-- ext_gsheet_tab: one row per tab (~1,860).  sheet_id is Google's numeric sheetId (unique only inside a spreadsheet).
CREATE TABLE ext_gsheet_tab (
  tab_pk          INTEGER PRIMARY KEY,  -- surrogate key
  spreadsheet_id  TEXT    NOT NULL REFERENCES ext_gsheet_catalog(spreadsheet_id) ON DELETE CASCADE,  -- owning spreadsheet
  sheet_id        INTEGER NOT NULL,  -- Google's numeric sheetId (unique only inside a spreadsheet)
  title           TEXT    NOT NULL,
  tab_index       INTEGER,  -- position of the tab
  is_hidden       INTEGER NOT NULL DEFAULT 0 CHECK (is_hidden IN (0, 1)),
  grid_rows       INTEGER CHECK (grid_rows IS NULL OR grid_rows >= 0),            -- ALLOCATED grid, not populated cells
  grid_cols       INTEGER CHECK (grid_cols IS NULL OR grid_cols >= 0),  -- ALLOCATED grid columns, not populated cells
  header_json     TEXT    CHECK (header_json IS NULL OR (json_valid(header_json) AND json_type(header_json) = 'array')),
  pull_status     TEXT    NOT NULL DEFAULT 'pending' CHECK (pull_status IN ('pending', 'ok', 'empty', 'error', 'skipped')),  -- pending / ok / empty / error / skipped
  pull_error      TEXT,  -- error text of the last failed pull
  pulled_rows     INTEGER CHECK (pulled_rows IS NULL OR pulled_rows >= 0),  -- rows stored by the last pull
  content_sha256  TEXT    CHECK (content_sha256 IS NULL OR length(content_sha256) = 64),
  pulled_at       TEXT    CHECK (pulled_at IS NULL OR (pulled_at IS strftime('%Y-%m-%dT%H:%M:%fZ', pulled_at) AND pulled_at NOT GLOB '*T24:*')),
  import_run_id   INTEGER REFERENCES ops_import_run(import_run_id) ON DELETE SET NULL,
  created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at) AND created_at NOT GLOB '*T24:*'),
  updated_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (updated_at IS strftime('%Y-%m-%dT%H:%M:%fZ', updated_at) AND updated_at NOT GLOB '*T24:*'),
  UNIQUE (spreadsheet_id, sheet_id),
  CHECK (pull_status NOT IN ('ok', 'empty') OR pulled_at IS NOT NULL)
) STRICT;
CREATE INDEX ix_gsheet_tab_status ON ext_gsheet_tab(pull_status);

-- ext_gsheet_row: one row per populated sheet row.  cells_json = JSON array of the row's values (UNFORMATTED_VALUE),
-- text_flat = the same values joined by spaces (this is what FTS indexes).  Row 1 is the header row unless the tab says otherwise.
CREATE TABLE ext_gsheet_row (
  row_pk       INTEGER PRIMARY KEY,  -- surrogate key (FTS rowid)
  tab_pk       INTEGER NOT NULL REFERENCES ext_gsheet_tab(tab_pk) ON DELETE CASCADE,  -- owning tab
  row_number   INTEGER NOT NULL CHECK (row_number >= 1),                     -- 1-based sheet row
  cells_json   TEXT    NOT NULL CHECK (json_valid(cells_json) AND json_type(cells_json) = 'array'),
  text_flat    TEXT    NOT NULL DEFAULT '',  -- the row's values joined by spaces (FTS-indexed)
  row_sha256   TEXT    CHECK (row_sha256 IS NULL OR length(row_sha256) = 64),
  fetched_at   TEXT    NOT NULL CHECK (fetched_at IS strftime('%Y-%m-%dT%H:%M:%fZ', fetched_at) AND fetched_at NOT GLOB '*T24:*'),
  UNIQUE (tab_pk, row_number)
) STRICT;

CREATE TRIGGER trg_gsheet_row_pull_guard BEFORE INSERT ON ext_gsheet_row
WHEN (SELECT c.pull_enabled FROM ext_gsheet_tab t JOIN ext_gsheet_catalog c ON c.spreadsheet_id = t.spreadsheet_id WHERE t.tab_pk = NEW.tab_pk) = 0
BEGIN
  SELECT RAISE(ABORT, 'ext_gsheet_row: the spreadsheet has pull_enabled = 0 (opt-in allow-list / ext_gsheet_exclusion)');
END;

CREATE VIRTUAL TABLE ext_gsheet_row_fts USING fts5(
  text_flat,
  content = 'ext_gsheet_row', content_rowid = 'row_pk',
  tokenize = 'unicode61 remove_diacritics 2'
);
CREATE TRIGGER trg_gsheet_row_fts_ai AFTER INSERT ON ext_gsheet_row
BEGIN
  INSERT INTO ext_gsheet_row_fts (rowid, text_flat) VALUES (NEW.row_pk, NEW.text_flat);
END;
CREATE TRIGGER trg_gsheet_row_fts_ad AFTER DELETE ON ext_gsheet_row
BEGIN
  INSERT INTO ext_gsheet_row_fts (ext_gsheet_row_fts, rowid, text_flat) VALUES ('delete', OLD.row_pk, OLD.text_flat);
END;
CREATE TRIGGER trg_gsheet_row_fts_au AFTER UPDATE OF text_flat ON ext_gsheet_row
BEGIN
  INSERT INTO ext_gsheet_row_fts (ext_gsheet_row_fts, rowid, text_flat) VALUES ('delete', OLD.row_pk, OLD.text_flat);
  INSERT INTO ext_gsheet_row_fts (rowid, text_flat) VALUES (NEW.row_pk, NEW.text_flat);
END;

-- =============================================================================================
-- Gmail
-- =============================================================================================

-- ext_gmail_account: one mailbox we pull.  token_ref is the NAME of the credential file under secrets/ (never its content).
-- history_id is the incremental-sync cursor from users.getProfile (users.history.list afterwards).
CREATE TABLE ext_gmail_account (
  gmail_account_id  INTEGER PRIMARY KEY,  -- surrogate key of the mailbox
  email_address     TEXT    NOT NULL UNIQUE CHECK (email_address = lower(trim(email_address)) AND instr(email_address, '@') > 1),
  display_name      TEXT,  -- display name of the mailbox
  token_ref         TEXT,  -- NAME of the credential file under secrets/ (never its content)
  scopes_json       TEXT    NOT NULL DEFAULT '[]' CHECK (json_valid(scopes_json) AND json_type(scopes_json) = 'array'),
  history_id        TEXT,
  messages_total    INTEGER CHECK (messages_total IS NULL OR messages_total >= 0),  -- messagesTotal from users.getProfile
  threads_total     INTEGER CHECK (threads_total  IS NULL OR threads_total  >= 0),  -- threadsTotal from users.getProfile
  profile_fetched_at TEXT   CHECK (profile_fetched_at IS NULL OR (profile_fetched_at IS strftime('%Y-%m-%dT%H:%M:%fZ', profile_fetched_at) AND profile_fetched_at NOT GLOB '*T24:*')),
  pull_enabled      INTEGER NOT NULL DEFAULT 1 CHECK (pull_enabled IN (0, 1)),  -- 0 = skip this mailbox in pulls
  created_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at) AND created_at NOT GLOB '*T24:*'),
  updated_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')) CHECK (updated_at IS strftime('%Y-%m-%dT%H:%M:%fZ', updated_at) AND updated_at NOT GLOB '*T24:*')
) STRICT;

CREATE TABLE ext_gmail_label (
  label_pk          INTEGER PRIMARY KEY,  -- surrogate key
  gmail_account_id  INTEGER NOT NULL REFERENCES ext_gmail_account(gmail_account_id) ON DELETE CASCADE,  -- owning mailbox
  label_id          TEXT    NOT NULL CHECK (label_id <> ''),          -- Gmail label id: INBOX, SENT, Label_123 ...
  name              TEXT    NOT NULL,
  label_type        TEXT    NOT NULL DEFAULT 'user' CHECK (label_type IN ('system', 'user')),  -- system or user
  messages_total    INTEGER CHECK (messages_total IS NULL OR messages_total >= 0),  -- messages carrying the label
  threads_total     INTEGER CHECK (threads_total  IS NULL OR threads_total  >= 0),  -- threads carrying the label
  fetched_at        TEXT    CHECK (fetched_at IS NULL OR (fetched_at IS strftime('%Y-%m-%dT%H:%M:%fZ', fetched_at) AND fetched_at NOT GLOB '*T24:*')),
  UNIQUE (gmail_account_id, label_id)
) STRICT;

CREATE TABLE ext_gmail_thread (
  thread_pk         INTEGER PRIMARY KEY,  -- surrogate key
  gmail_account_id  INTEGER NOT NULL REFERENCES ext_gmail_account(gmail_account_id) ON DELETE CASCADE,  -- owning mailbox
  thread_id         TEXT    NOT NULL CHECK (thread_id <> ''),
  history_id        TEXT,
  subject           TEXT,  -- subject of the thread
  snippet           TEXT,  -- snippet of the latest message
  message_count     INTEGER CHECK (message_count IS NULL OR message_count >= 0),  -- number of messages in the thread
  first_message_at  TEXT    CHECK (first_message_at IS NULL OR (first_message_at IS strftime('%Y-%m-%dT%H:%M:%fZ', first_message_at) AND first_message_at NOT GLOB '*T24:*')),
  last_message_at   TEXT    CHECK (last_message_at  IS NULL OR (last_message_at IS strftime('%Y-%m-%dT%H:%M:%fZ', last_message_at) AND last_message_at NOT GLOB '*T24:*')),
  fetched_at        TEXT    CHECK (fetched_at IS NULL OR (fetched_at IS strftime('%Y-%m-%dT%H:%M:%fZ', fetched_at) AND fetched_at NOT GLOB '*T24:*')),
  UNIQUE (gmail_account_id, thread_id)
) STRICT;
CREATE INDEX ix_gmail_thread_last ON ext_gmail_thread(gmail_account_id, last_message_at);

-- ext_gmail_message: parsed headers + bodies.  The raw RFC822 (.eml.gz) lives on disk (raw_path + raw_sha256), not in the
-- database - keep those files: the full MIME part tree and the headers not listed here are recoverable only from them.
-- from_addr / to_addrs / cc_addrs / bcc_addrs / reply_to are normalised lower-case bare addresses; the LISTS are COMMA separated
-- ('a@x.com,b@y.com', no spaces - unambiguous even for quoted local parts) and FTS (unicode61) still tokenises them; display names stay
-- in the raw message.  body_text is the text/plain part; body_html is stored for HTML-only mail (marketing / auto-replies) and is NOT
-- full-text indexed.  headers_json is the complete header list as [[name, value], ...] (List-Unsubscribe, Delivered-To, Sender ...).
CREATE TABLE ext_gmail_message (
  message_pk        INTEGER PRIMARY KEY,  -- surrogate key
  gmail_account_id  INTEGER NOT NULL REFERENCES ext_gmail_account(gmail_account_id) ON DELETE CASCADE,  -- owning mailbox
  thread_pk         INTEGER REFERENCES ext_gmail_thread(thread_pk) ON DELETE SET NULL,  -- owning thread (NULL if the thread row was not fetched)
  message_id        TEXT    NOT NULL CHECK (message_id <> ''),         -- Gmail API message id
  rfc822_message_id TEXT,                                               -- Message-ID header
  in_reply_to       TEXT,  -- In-Reply-To header
  references_hdr    TEXT,                                                 -- References header (conversation threading beyond In-Reply-To)
  history_id        TEXT,
  internal_at       TEXT    CHECK (internal_at IS NULL OR (internal_at IS strftime('%Y-%m-%dT%H:%M:%fZ', internal_at) AND internal_at NOT GLOB '*T24:*')),   -- internalDate
  date_header_at    TEXT    CHECK (date_header_at IS NULL OR (date_header_at IS strftime('%Y-%m-%dT%H:%M:%fZ', date_header_at) AND date_header_at NOT GLOB '*T24:*')),   -- Date header (sender's clock) converted to UTC; internal_at is Gmail's receive time
  from_addr         TEXT    CHECK (from_addr IS NULL OR from_addr = lower(from_addr)),  -- lower-case bare sender address
  to_addrs          TEXT    CHECK (to_addrs   IS NULL OR to_addrs   = lower(to_addrs)),  -- lower-case bare recipient addresses, comma separated
  cc_addrs          TEXT    CHECK (cc_addrs   IS NULL OR cc_addrs   = lower(cc_addrs)),  -- lower-case bare Cc addresses, comma separated
  bcc_addrs         TEXT    CHECK (bcc_addrs  IS NULL OR bcc_addrs  = lower(bcc_addrs)),  -- lower-case bare Bcc addresses, comma separated
  reply_to          TEXT    CHECK (reply_to   IS NULL OR reply_to   = lower(reply_to)),  -- lower-case Reply-To address(es)
  subject           TEXT,  -- Subject header
  snippet           TEXT,  -- Gmail snippet
  body_text         TEXT,  -- text/plain body (FTS-indexed)
  body_html         TEXT,  -- text/html body for HTML-only mail (not FTS-indexed)
  headers_json      TEXT    CHECK (headers_json IS NULL OR (json_valid(headers_json) AND json_type(headers_json) = 'array')),
  size_estimate     INTEGER CHECK (size_estimate IS NULL OR size_estimate >= 0),  -- Gmail sizeEstimate in bytes
  is_sent           INTEGER NOT NULL DEFAULT 0 CHECK (is_sent IN (0, 1)),
  is_draft          INTEGER NOT NULL DEFAULT 0 CHECK (is_draft IN (0, 1)),
  is_spam_trash     INTEGER NOT NULL DEFAULT 0 CHECK (is_spam_trash IN (0, 1)),
  has_attachments   INTEGER NOT NULL DEFAULT 0 CHECK (has_attachments IN (0, 1)),  -- 1 if any part is an attachment
  raw_path          TEXT,  -- path of the raw RFC822 (.eml.gz) on disk
  raw_sha256        TEXT    CHECK (raw_sha256 IS NULL OR length(raw_sha256) = 64),
  fetched_at        TEXT    NOT NULL CHECK (fetched_at IS strftime('%Y-%m-%dT%H:%M:%fZ', fetched_at) AND fetched_at NOT GLOB '*T24:*'),
  import_run_id     INTEGER REFERENCES ops_import_run(import_run_id) ON DELETE SET NULL,
  UNIQUE (gmail_account_id, message_id)
) STRICT;
CREATE INDEX ix_gmail_message_thread ON ext_gmail_message(thread_pk);
CREATE INDEX ix_gmail_message_time   ON ext_gmail_message(gmail_account_id, internal_at);
CREATE INDEX ix_gmail_message_from   ON ext_gmail_message(from_addr);
CREATE INDEX ix_gmail_message_rfc    ON ext_gmail_message(rfc822_message_id) WHERE rfc822_message_id IS NOT NULL;

-- message <-> label (many to many)
CREATE TABLE ext_gmail_message_label (
  message_pk  INTEGER NOT NULL REFERENCES ext_gmail_message(message_pk) ON DELETE CASCADE,  -- the message
  label_pk    INTEGER NOT NULL REFERENCES ext_gmail_label(label_pk)     ON DELETE CASCADE,  -- a label carried by the message
  PRIMARY KEY (message_pk, label_pk)
) STRICT, WITHOUT ROWID;
CREATE INDEX ix_gmail_message_label_label ON ext_gmail_message_label(label_pk, message_pk);

-- ext_gmail_attachment: metadata + (optional) extracted text; the bytes live on disk (stored_path / content_sha256).
CREATE TABLE ext_gmail_attachment (
  attachment_pk    INTEGER PRIMARY KEY,  -- surrogate key
  message_pk       INTEGER NOT NULL REFERENCES ext_gmail_message(message_pk) ON DELETE CASCADE,  -- owning message
  part_id          TEXT    NOT NULL,                                     -- MIME part id inside the message
  attachment_id    TEXT,                                                 -- Gmail attachmentId (changes between fetches)
  filename         TEXT    NOT NULL DEFAULT '',  -- attachment file name ('' for unnamed parts)
  mime_type        TEXT,  -- MIME type of the part
  content_id       TEXT,                                                 -- Content-ID of an inline image (referenced from body_html as cid:...)
  disposition      TEXT    CHECK (disposition IS NULL OR disposition IN ('attachment', 'inline')),  -- attachment or inline (inline images are referenced by content_id)
  size_bytes       INTEGER CHECK (size_bytes IS NULL OR size_bytes >= 0),  -- size of the decoded part
  content_sha256   TEXT    CHECK (content_sha256 IS NULL OR length(content_sha256) = 64),
  stored_path      TEXT,  -- where the bytes live on disk
  extracted_text   TEXT,  -- text extracted from the attachment (FTS-indexed)
  fetched_at       TEXT    CHECK (fetched_at IS NULL OR (fetched_at IS strftime('%Y-%m-%dT%H:%M:%fZ', fetched_at) AND fetched_at NOT GLOB '*T24:*')),
  UNIQUE (message_pk, part_id)
) STRICT;
CREATE INDEX ix_gmail_attachment_sha ON ext_gmail_attachment(content_sha256) WHERE content_sha256 IS NOT NULL;

-- FTS: messages (subject, addresses, snippet, body) and attachments (filename, extracted text)
CREATE VIRTUAL TABLE ext_gmail_message_fts USING fts5(
  subject, from_addr, to_addrs, cc_addrs, snippet, body_text,
  content = 'ext_gmail_message', content_rowid = 'message_pk',
  tokenize = 'unicode61 remove_diacritics 2'
);
CREATE TRIGGER trg_gmail_message_fts_ai AFTER INSERT ON ext_gmail_message
BEGIN
  INSERT INTO ext_gmail_message_fts (rowid, subject, from_addr, to_addrs, cc_addrs, snippet, body_text)
  VALUES (NEW.message_pk, NEW.subject, NEW.from_addr, NEW.to_addrs, NEW.cc_addrs, NEW.snippet, NEW.body_text);
END;
CREATE TRIGGER trg_gmail_message_fts_ad AFTER DELETE ON ext_gmail_message
BEGIN
  INSERT INTO ext_gmail_message_fts (ext_gmail_message_fts, rowid, subject, from_addr, to_addrs, cc_addrs, snippet, body_text)
  VALUES ('delete', OLD.message_pk, OLD.subject, OLD.from_addr, OLD.to_addrs, OLD.cc_addrs, OLD.snippet, OLD.body_text);
END;
CREATE TRIGGER trg_gmail_message_fts_au AFTER UPDATE OF subject, from_addr, to_addrs, cc_addrs, snippet, body_text ON ext_gmail_message
BEGIN
  INSERT INTO ext_gmail_message_fts (ext_gmail_message_fts, rowid, subject, from_addr, to_addrs, cc_addrs, snippet, body_text)
  VALUES ('delete', OLD.message_pk, OLD.subject, OLD.from_addr, OLD.to_addrs, OLD.cc_addrs, OLD.snippet, OLD.body_text);
  INSERT INTO ext_gmail_message_fts (rowid, subject, from_addr, to_addrs, cc_addrs, snippet, body_text)
  VALUES (NEW.message_pk, NEW.subject, NEW.from_addr, NEW.to_addrs, NEW.cc_addrs, NEW.snippet, NEW.body_text);
END;

CREATE VIRTUAL TABLE ext_gmail_attachment_fts USING fts5(
  filename, extracted_text,
  content = 'ext_gmail_attachment', content_rowid = 'attachment_pk',
  tokenize = 'unicode61 remove_diacritics 2'
);
CREATE TRIGGER trg_gmail_attachment_fts_ai AFTER INSERT ON ext_gmail_attachment
BEGIN
  INSERT INTO ext_gmail_attachment_fts (rowid, filename, extracted_text) VALUES (NEW.attachment_pk, NEW.filename, NEW.extracted_text);
END;
CREATE TRIGGER trg_gmail_attachment_fts_ad AFTER DELETE ON ext_gmail_attachment
BEGIN
  INSERT INTO ext_gmail_attachment_fts (ext_gmail_attachment_fts, rowid, filename, extracted_text) VALUES ('delete', OLD.attachment_pk, OLD.filename, OLD.extracted_text);
END;
CREATE TRIGGER trg_gmail_attachment_fts_au AFTER UPDATE OF filename, extracted_text ON ext_gmail_attachment
BEGIN
  INSERT INTO ext_gmail_attachment_fts (ext_gmail_attachment_fts, rowid, filename, extracted_text) VALUES ('delete', OLD.attachment_pk, OLD.filename, OLD.extracted_text);
  INSERT INTO ext_gmail_attachment_fts (rowid, filename, extracted_text) VALUES (NEW.attachment_pk, NEW.filename, NEW.extracted_text);
END;

-- updated_at maintenance for the mutable ext_* tables
CREATE TRIGGER trg_ext_gsheet_catalog_touch AFTER UPDATE ON ext_gsheet_catalog
FOR EACH ROW WHEN NEW.updated_at = OLD.updated_at AND OLD.updated_at <> strftime('%Y-%m-%dT%H:%M:%fZ','now')
BEGIN
  UPDATE ext_gsheet_catalog SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE rowid = NEW.rowid;
END;
CREATE TRIGGER trg_ext_gsheet_tab_touch AFTER UPDATE ON ext_gsheet_tab
FOR EACH ROW WHEN NEW.updated_at = OLD.updated_at AND OLD.updated_at <> strftime('%Y-%m-%dT%H:%M:%fZ','now')
BEGIN
  UPDATE ext_gsheet_tab SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE rowid = NEW.rowid;
END;
CREATE TRIGGER trg_ext_gmail_account_touch AFTER UPDATE ON ext_gmail_account
FOR EACH ROW WHEN NEW.updated_at = OLD.updated_at AND OLD.updated_at <> strftime('%Y-%m-%dT%H:%M:%fZ','now')
BEGIN
  UPDATE ext_gmail_account SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE rowid = NEW.rowid;
END;

-- v_gsheet_excluded_rows: rows stored for a spreadsheet that is (now) pull_enabled = 0 - e.g. pull_enabled flipped to 0 after a pull.
-- Must be empty; doctor() warns otherwise and the rows should be deleted.
CREATE VIEW v_gsheet_excluded_rows AS
SELECT c.spreadsheet_id, c.title, c.pii_class, COUNT(r.row_pk) AS stored_rows
FROM ext_gsheet_catalog c
JOIN ext_gsheet_tab t ON t.spreadsheet_id = c.spreadsheet_id
JOIN ext_gsheet_row r ON r.tab_pk = t.tab_pk
WHERE c.pull_enabled = 0
GROUP BY c.spreadsheet_id;
