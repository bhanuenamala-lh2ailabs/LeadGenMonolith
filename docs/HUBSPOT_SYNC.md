# HubSpot sync (read-only) and canonicalizer

Code: `leadgen/hubspot/{client,sync,canonicalize}.py`, data: `config/stage_map.yaml`, tests: `tests/test_hubspot_sync.py`.
Binding rules: `docs/ARCHITECTURE.md` (sections 0, 1, 3.1); schema contract: `docs/review/schema-decisions.md`, `docs/DATA_DICTIONARY.md`.

## Run it

```
.venv/bin/python -m leadgen.hubspot.sync --account main|companyops|rat|all [--full] [--object deals,contacts,...] [--since-hours 24] [--verify]
.venv/bin/python -m leadgen.hubspot.canonicalize [--account main|companyops|rat|all] [--all]
.venv/bin/python -m leadgen.hubspot.sync --account all --object none --verify     # independent count check only (API vs hsraw vs canonical, per stage)
```

* `sync` fills `hsraw_*` (full API JSON) and `ops_*`; `canonicalize` projects `hsraw_*` into the canonical tables. They are separate on purpose:
  the canonical layer can always be rebuilt (`canonicalize --all`) without calling HubSpot.
* `--object` takes `account, owners, pipelines, properties, deals, contacts, companies, notes, tasks, calls, meetings, emails, assoc`
  (`none` = skip the sync, useful with `--verify`).
* First run of an object (no cursor) or `--full` = full read through the list endpoints (no 10,000 cap). Later runs are incremental:
  search `hs_lastmodifieddate >= cursor - 24 h` (contacts: `lastmodifieddate`), windowed by date and split in half whenever a window holds
  more than 9,500 hits (the search API cannot page past 10,000), deals then `batch/read` with history (50 per call). Archived deals,
  contacts and companies are not searchable, so their (small) archived list is re-listed on every run. Cursors: `ops_sync_state`
  (`hubspot`, account, object type, `hs_lastmodifieddate`), value = start time of the last successful run.
* Measured on 2026-10-04 (100 req / 10 s cap, ~0 retries): RAT full 73 s, COMPANYOPS full 298 s, MAIN full 493 s; canonicalize 2 s / 6 s / 21 s;
  an incremental run with nothing new ~30 s (mostly the deal-association refresh).

## Read-only guard (`client.py`)

`assert_read_only(method, path)` runs before anything is sent: GET is allowed; POST only to `/crm/v3/objects/<type>/search`,
`/crm/v3/objects/<type>/batch/read`, `/crm/v4/associations/<from>/<to>/batch/read` (and the v3 twin); PATCH / PUT / DELETE / every other POST,
absolute URLs, `..` and anything not a plain relative API path raise `ReadOnlyViolation`. Tested with 28 allowed / blocked cases, a fake
session that proves nothing is sent, and a source scan for write verbs. The token comes from `leadgen.config.get_secret(<env key>)`, only
into the `Authorization` header; error text goes through `leadgen.config.redact`. Verified after the real runs: no token value appears in any
`hsraw_*` / `ops_*` column. Rate: 100 requests / 10 s (sliding window) plus 4 search calls / s; 429 / 5xx / transport errors are retried with
exponential backoff (1, 2, 4 ... 60 s, up to 6 retries) honouring `Retry-After`.

## What is read, and where it lands

| HubSpot | hsraw (`object_type`, `hs_id`) | Notes |
|---|---|---|
| `GET /account-info/v3/details` | `properties`, `account:details` | portal id must equal config/accounts.yaml or the account aborts (`portal_mismatch`) |
| owners (archived false + true) | `owners`, owner id | archived owners carry no `userId` |
| `GET /crm/v3/pipelines/deals` | `pipelines`, pipeline id | stages inside |
| property definitions deals / contacts / companies | `properties`, `deals` etc. | custom (non-`hubspotDefined`) names drive the property lists |
| deals incl. archived | `deals` | `propertiesWithHistory=dealstage,hubspot_owner_id,pipeline` in `history_json`; `_archived_at` added to `properties_json`; associations in `associations_json` |
| contacts, companies (incl. archived) | `contacts`, `companies` | |
| notes, tasks, calls, meetings, emails | same names | associations to deals / contacts / companies via v4 batch read |
| deal -> contacts / companies | inside `deals.associations_json` | refreshed for ALL live deals on every run (an association change does not always bump `hs_lastmodifieddate`) |

`payload_sha256` covers properties + associations + history, so a revision of the previous payload is archived by the trigger whenever
anything changes. A payload that is unchanged is not rewritten. The pre-association version of a first-time row is pruned (it is not a real
previous version). `ops_import_run` (kind `hubspot_sync`, `hubspot:<account>` for sync, `hubspot:<account>:canonicalize` for projection) carries
per-object counts in `params_json`; reconciliation (API search total vs non-archived hsraw rows) is part of every sync run and a
difference becomes `ops_dq_issue count_mismatch`. Scope problems (the `emails` scope is missing on all three keys) become
`ops_dq_issue hubspot_scope_missing` (warn) and never fail the run.

## Canonical projection rules

* **owner**: from the owners API; ids used by objects but not listed get a stub (`role = 'stub...'`, DQ info). `hs_user_id` only from the API.
  Stage history names the USER id (`updatedByUserId`); deactivated users have no `userId` on their owner record, so for them the owner with the same
  numeric id is used, but only in a portal where `userId == ownerId` holds for every owner exposing both (verified in code), and disclosed in
  `ops_dq_issue actor_owner_inferred`.
* **pipeline / stage**: labels refreshed from the API (old label kept in `previous_labels_json`), stages gone from the API become tombstones.
  `config/stage_map.yaml` `deleted_stages` creates tombstones for known deleted ids before events refer to them; any other id seen only in
  history becomes a tombstone (`label_source='history'`) in the pipeline resolved from the deal's pipeline history (DQ info
  `stage_seen_only_in_history`). `stage_alias`: the two COMPANYOPS LinkedIn ids (deleted by v5, restored with new ids) inherit their successor's
  mapping; label aliases for `Callback +1 day` and `Call Attempted`.
* **stage_map**: `config/stage_map.yaml` (154 live stages of the 5 reporting pipelines + 36 deleted ids). Unmapped stage of a reporting pipeline =
  `ops_dq_issue unmapped_stage` (error), `v_unmapped_stage`, doctor warning. The YAML `kind` is cross-checked against the canonical code.
* **company**: golden record + `company_account_link` per portal company. A portal company links to an existing golden company ONLY via a strong
  key: `root_domain` (`leadgen.norm.norm_domain`; free-mail and LinkedIn / Facebook domains are weak and never link) or `linkedin_company`.
  Identifiers written: `hs_company` (`<account>:<id>`), `root_domain`, `linkedin_company`. If two strong keys point at different goldens, or a
  strong value is already held by another golden, a `merge_candidate` (`strong_key_conflict` / `shared_<type>`) is written and nothing is merged.
  A new golden whose normalised NAME equals another golden's gets `merge_candidate(name_norm)` (max 20 per company) - never a link.
  Golden attributes follow the link that created the golden (`link_method = 'new_golden'`). Company phones go to `company_phone`.
  `lh2_domain` is unique per portal only among live companies in HubSpot; archived duplicates keep `lh2_domain NULL` in the link (raw value in hsraw).
* **contact / contact_phone**: `phone`, `mobilephone` -> `norm_phone_e164`, `country_iso2` for unambiguous prefixes, `phone_type` and
  `is_placeholder` as below. Company from `associatedcompanyid`. `do_not_contact` = `hs_email_optout = true` or `call_outcome = 'Do Not Contact'`.
  Custom contact properties go to `attrs_json`.
* **+91 mobile gate** (stored, fails closed): `phone_type = 'mobile'` only when certain. `phonenumbers` is used when installed (it is not in the
  current venv); otherwise `+91` + 10 digits starting 6-9 is `mobile` EXCEPT the 79 and 80 prefixes (Ahmedabad / Bangalore landlines share the
  mobile shape), which stay `unknown`. 1-5 prefixes are `landline`, `1800...` `tollfree`. Dummies (all-same digits, `1234567890`, `0123456789`,
  `9876543210`) are `is_placeholder = 1`. Result on 2026-10-04: 12,830 mobile-gate numbers open; 718 sit in `v_phone_gate_review`
  (552 `+9180...`, 165 `+9179...`, 1 unparseable). `pip install phonenumbers` and `canonicalize --all` would classify them exactly.
* **deal**: current pipeline / stage / owner, `lead_source` (normalised code), `segment` = `segment` or `scraped_type` or `lead_category` (RAT),
  `tam_source` = `tam_source` or `pipeline_source` or `source_tab`, `cost` -> `cost_usd` (`amount` stored, never reported), archived flag and time,
  custom properties in `props_json`. `company_id` = golden company of the PRIMARY company association (typeId 5; a single unlabeled
  association counts as primary), set together with `deal_company` (demote / delete first, then `company_id`, then insert - the consistency
  triggers require this order). `deal_contact` has no primary.
* **deal_stage_event**: one row per `dealstage` history entry (oldest first), `from_*` = previous emitted event, `source_type` = `sourceType`
  upper-cased (`is_human`, `ist_day` are generated), `actor_user_id` = `updatedByUserId`, `event_origin = 'hubspot_history'`. `pipeline_id` is
  resolved per event: id known in exactly one pipeline -> `unique_stage_id`; else the `pipeline` property history in effect at `entered_at`
  -> `pipeline_history`; else the deal's current pipeline -> `deal_current`; else the event is skipped with `ambiguous_stage_pipeline`. A repeat of
  the same (pipeline, stage) is not a transition and is skipped. Result: every event resolved by `unique_stage_id` (the "shared" MAIN stage ids
  are not shared in the live pipeline definitions: Coding and CoOps use different ids today).
  `deal_owner_event` comes from the `hubspot_owner_id` history.
* **engagement / engagement_assoc**: notes (HTML stripped to text), tasks, calls, meetings (emails when the scope exists).
* Every canonical row has an `origin_ref` (`hubspot:<account>`, source table, HubSpot id).
* **Idempotency**: every write is compare-then-write or `ON CONFLICT DO NOTHING`; `import_run_id` is written only on insert. By default only raw
  rows whose payload changed since they were applied are re-projected (`canonical_payload_sha256`); deal associations are re-linked every run;
  `--all` re-projects everything. A second run reports `{}` (tested, and verified on the real database for all three accounts).
  A row that violates a constraint is rejected on its own (`row_rejected`, SAVEPOINT), not applied, retried next run, and the finding is
  auto-resolved once it projects.

## Verified on 2026-10-04 (live API vs database)

Non-archived objects, independent `search` totals: deals 6,201 / 7,575 / 1,237, contacts 8,549 / 8,165 / 1,374, companies 6,756 / 2,641 / 844,
notes 5,113 / 1,849 / 561 (MAIN / COMPANYOPS / RAT) - API, hsraw and canonical all equal. Per-stage deal counts: every stage of all five reporting pipelines
(154 stages, API `pipeline` + `dealstage` search vs `v_funnel_stage_counts`) equal; pipeline totals Coding 4,821, CoOps (Global) 1,380, Cluster 1 1,135,
Cluster 2 6,440, Rapid Action Team 1,236. Archived rows are stored too (deals: MAIN 5,491, COMPANYOPS 498, RAT 70).
`python -m leadgen.db doctor`: `ok: true`; remaining warnings are the 6 unmapped COMPANYOPS history-only stages and the 718 phone-gate reviews.

## Open items

1. Six Cluster 2 stage ids exist only in history and no source names them: `4080987862`, `4111134400` (125 events each, the retired Gmail
   cold-email branch), `4080987867`, `4080987868`, `4111134403` (between "One pager received" and "LOI signed"), `4080987879` (Replied -> "Dead: Replied
   / Not Interested"). They are tombstones without a mapping on purpose (loud `unmapped_stage`, 259 events); add them under `deleted_stages` in
   `config/stage_map.yaml` once someone names them.
2. Labels of the 34 deleted MAIN stage ids declared in the YAML (28 of them occur in history) were recovered from the legacy repo (`export_deals_master.py` RETIRED map, `full_funnel_report.py`,
   `docs/sop/funnel_tabs/03_3_stage_inventory_and_changes.md`); the canonical mapping of the ten old "Scraped" stages is inferred from those labels
   (noted per row in the YAML).
3. `Call Attempted (retired)` -> `RETIRED_ADMIN` (funnel-reports 4.5) versus `NO_ANSWER` (funnel-reports 4.2 / SOP). One line in the YAML.
4. `emails` scope missing on all three keys; `/settings/v3/users` not used.
5. The monolith-level command `python -m leadgen sync hubspot` (ARCHITECTURE section 2) is not wired by this package; call the module CLIs above.
