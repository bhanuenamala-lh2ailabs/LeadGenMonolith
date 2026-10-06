# The unified daily funnel report

One report replaces ~15 generators across 3 repos (5 funnels, 3 HubSpot portals). It is rebuilt from the event store
(`deal_stage_event` + `deal`) for any IST day. It never reads labels, legacy snapshots (except to cross-check) or HubSpot.

Files:
- `config/report.yaml`: the single metric dictionary (12 metrics)
- `leadgen/reports/metrics.py`: one function per dictionary entry
- `build.py`: model assembly and `rpt_*` persistence
- `render.py`: HTML / MD / CSV / JSON
- `mailer.py`: Gmail transport (never invoked without `--send`)
- `daily.py`: CLI
- `tests/test_reports.py`

Outputs: `reports/daily/<date>/funnel.{html,md,csv,json}`. The JSON has no volatile fields, so re-runs are byte-identical.

## Running
- `python -m leadgen report daily`: today (IST), dry-run. Writes files, persists `rpt_*`, mails nothing.
- `--date D`: any past day, as of that day.
- `--days N`: backfill `rpt_*` for N days; files for the last `--files-days` (default 14).
- `--no-persist`: database opened read-only.
- `--send`: the ONLY mailing path. Default recipient is bhanu.enamala@lh2.ai; `--to` names others explicitly. Transport is the Gmail API
  `users.messages.send` (scope `gmail.send`), tokens from `secrets/` in the order of `mail.token_files` in `config/report.yaml`.
No HubSpot call, no scheduler.

## Semantics
- IST day = `deal_stage_event.ist_day`.
- Engaged = CRM_UI (human) stage moves only. Entry rungs (Sourced/Assigned, LinkedIn sent) count any source.
- Entries are de-duplicated on (deal, stage or rung, actor, IST day, human/auto). Entries vs distinct deals are labelled in every header.
- 7-day engaged = set union of distinct deals; Roll7 = sum of 7 daily values; "n of 7 days" when the store/sync does not cover the window;
  percent change only when both windows are 7 of 7 and the earlier value >= 10.
- Inception = distinct deals ever (all time, any source). It only grows and is never re-seeded from a cutoff. The Cluster 2
  "since 15 Sep, excluding the bulk migration" figure appears only as a labelled secondary column (the user rejected it as the primary on 30 Sep).
- Native stages map to canonical rungs via `(account, pipeline_id, stage_id)` (`v_stage_effective`). The retired "Callback +1 day" stays in the No-answer rung.
- Occupancy = deals sitting in the stage, archived excluded (`deal.stage_id` on the sync day, event reconstruction for earlier days; verified equal).
- The RAT stock "Deals pipeline" is excluded. Dead reasons come from `stage_map`.
- Derived rungs: Meeting held = booking, then an asset-rung entry or post-meeting dead stage, with no no-show/cancel on or before that evidence day.
  Evaluated = ASSET_RECEIVED, then a quality dead stage (depth >= 8) or a live/won rung of depth >= 9.

## Persisted facts
- `rpt_funnel_daily`: native stage grain, cohort_scope all/in_scope, sparse, `occupancy_eod` for scope all.
- `rpt_owner_daily`: stage_entry / engaged / net_new / assigned.
- `rpt_engaged_daily`: owner x deal x day.
- `rpt_report_run`: one row per invocation (`mail_mode` = dry_run unless `--send`).
- `rpt_legacy_crosscheck`: replaced on each run. Each day is rewritten in one short transaction (delete + insert).

## Cross-check against the legacy snapshots
For each legacy series the last 5 snapshot days are compared (`dashboard_flow`, `flow`, `current_state`, `cumulative`, engaged deal-id sets).
Nothing is adjusted on either side. Each difference gets the first hypothesis that reproduces the legacy value (raw entries; archived deals;
UTC-midnight pre-filter; snapshot written before 18:30/19:30/20:30 IST; deal-owner attribution; stale MAIN id list; bootstrap; Coding scoped by
lead_source), otherwise "unexplained".
Run of 2026-10-04: 2,297 of 2,779 compared numbers match exactly; 6 are unexplained (Cluster 1 on 09-29: Leads Assigned 132 vs 129,
1st Interest 13 vs 12, Dead 56 vs 54; RAT on 09-30: 1st Interest Sent 15 vs 17). 126 per-owner rows (`main_owner`, series ended 09-17) are not reproduced.

## Deviations from docs/discovery/funnel-reports.md
1. Headline basis: entry and derived rungs count any source; every other rung counts human CRM_UI moves only (the legacy and user rule, rather than
   section 4.4's "Total by default"). Human / Automated / Total are still carried in the JSON, CSV and "Today auto" column.
2. Engaged is stage moves only. The notes half of M16, the note-only split and the note-based reject buckets are not built (`via` is always `stage`).
3. Inception is all-time distinct deals, any source (see Semantics).
4. Meeting held is credited to the booking day, because no `gmeet1_date` / `discovery_call_date` exists in `deal.props_json`.
5. Archived deals keep their historical moves in flows and Inception; occupancy excludes them.
6. `rpt_funnel_daily` is sparse (only stage-days with entries or occupancy).
7. Not built (outside the requested scope): M21 compliance, M22 value, M23 target attainment, M24 weekly quality KPIs.

## Known limits
- The event store starts 2026-07-15, so "all time" means all time in the store.
- Six Cluster 2 stage ids exist only in history (4080987862, 4080987867, 4080987868, 4080987879, 4111134400, 4111134403): 130 deals passed
  through them. They show on a red UNMAPPED row, are never dropped, and a human has to name them in `config/stage_map.yaml`.
- CoOps "Communicated" is mapped to REPLIED (open question 1 in funnel-reports.md); "Call Attempted (retired)" to RETIRED_ADMIN (one-line YAML change).
- The sync day is not "closed" in the data; a day is flagged until the next sync after 18:30 IST.
- Which mailbox each `gmail.send` token sends from is unverified (the token files carry no account field).
