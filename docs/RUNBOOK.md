# Runbook

All commands from the repo root. `PY=.venv/bin/python`. Nothing here is scheduled — nothing runs unless you run it.

## Daily routine
```bash
$PY -m leadgen sync hubspot                # pull the 3 portals (incremental, read-only), then canonicalize
$PY -m leadgen report daily                # writes reports/daily/<today>/{funnel.html,md,csv,json}; dry-run
$PY -m leadgen report daily --date 2026-10-03 --days 7    # rebuild/backfill any past days from the event store
$PY -m leadgen report daily --send         # the ONLY mailing path; goes to bhanu.enamala@lh2.ai unless --to NAME
$PY -m leadgen doctor                      # integrity, FK orphans, unmapped stages, phone-gate review, sync freshness
```
The report is rebuilt from `deal_stage_event`, so a missed day is never lost: re-sync, then `--date`/`--days`.

## Database
| Task | Command |
|---|---|
| Apply new migrations (auto-backup via VACUUM INTO to `db/backup/`) | `$PY -m leadgen migrate` |
| Status / drift check | `$PY -m leadgen status` · `doctor` |
| Refresh planner stats after big loads | `$PY -m leadgen analyze` |
| Regenerate the data dictionary | `$PY tools/gen_data_dictionary.py` (`--check` in CI) |
| Add a table/column | new `db/migrations/NNNN_name.sql`; never edit an applied one (sha256-enforced) |
| Back up | `sqlite3 db/leadgen.sqlite "VACUUM INTO 'backup.sqlite'"` (safe while idle) |
| Query by hand | `sqlite3 -readonly db/leadgen.sqlite` — start from the `v_*` views |

## Syncs and imports
- **HubSpot** (`leadgen.hubspot`): GET + read-only search only; a guard blocks every other verb. `--account main|companyops|rat|all`, `--full` for a complete re-pull, `--verify` compares DB to live counts.
  New HubSpot stage seen in history but not in `config/stage_map.yaml` → shows as `unmapped_stage` in `doctor`; add it to the YAML, re-run `python -m leadgen.hubspot.canonicalize`.
- **Legacy imports** (idempotent; re-run is a no-op): `import-legacy tam|repo-dbs|flatfiles`. Order done on 2026-10-04: tam verbatim → tam canonical → repo-dbs verbatim → repo-dbs canonical → tam link → flatfiles whale/snapshots/files/link.
- **Google Sheets** (`pull gsheets`): read-only, incremental by modifiedTime, 422 human sheets pulled; the 1,145 auto-generated purunjay.choudhary sheets are catalogued only (`--include-bulk` to pull, ~25 min). Hard-excluded sheets are blocked by a DB trigger.
- **Gmail** (`pull gmail`): needs a one-time consent: `$PY tools/gmail_auth.py` (browser; sign in as the mailbox; scope gmail.readonly), then `pull gmail --check --online`, `pull gmail --pull --max-messages 200` (trial), `pull gmail --pull` (full; later runs incremental).
  If Google says "access blocked": add the address as a test user on the OAuth consent screen (Cloud console), or use `--client secrets/hubspot_client_secret_lh2pipeline.json`. Testing-mode consent screens expire refresh tokens after 7 days.

## Running the original scripts
`legacy/<repo>/` still contains every original script with its own `.env` copy so they run as before (`cd legacy/companyOps && python opsdata/...`).
They keep their own dry-run/`--apply` discipline — **those are the only code paths that can write to HubSpot**. Known-broken ones are listed in `docs/discovery/funnel-reports.md` (e.g. `daily_fullfunnel_report.py` looks up the dead label "Company Ops Data").
The venvs were not copied; recreate one per script family if you need to run them.

## Credentials
`.env` = namespaced keys (`HUBSPOT_KEY_MAIN|COMPANYOPS|RAT`, `APOLLO_API_KEY`, …), `secrets/` = Google OAuth + service account. Code reads them only through `leadgen.config.get_secret()`; `redact()` for any logging.
Rotate a key: edit `.env`, nothing else. A Google token refreshes in place (file stays 0600).

## Recovery
- Bad import: every legacy row carries `_import_run_id`; `ops_import_run` lists runs. Restore from `db/backup/` (taken before each migration) or re-import from `legacy/`.
- Lost DB: `db/migrations/` + `legacy/` + `$PY -m leadgen migrate`, then the import order above and `sync hubspot --account all --full`, `pull gsheets` rebuild everything.
- Source of truth for deals/contacts is always **HubSpot**; the DB is a read-only mirror plus history.
