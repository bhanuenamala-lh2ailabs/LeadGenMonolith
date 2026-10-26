# Daily Sync: porting to a Google Apps Script trigger

The reference logic is `tools/daily_sync_pull.py` in this repo. Run it once (`--write` flag optional) to see it
work, then read it top to bottom — the comments explain *why*, not just *what*. The goal: the same thing, but as an
Apps Script bound to the Supply Team Mapping sheet, firing on its own time-driven trigger, so nothing has to stay
running on anyone's machine.

## Current state — handoff 2026-10-26 (read this section first)

The port exists and runs: `tools/apps_script_daily_sync/Code.gs`, bound to the **Performance Report** sheet
(`PERF_SHEET_ID` in the script = `1F9CGzd5qUL-kFywvGd4l7w6sJpwspoSMxSfIJIvrrXo`). Script project (clasp):
`scriptId` `1tq70LkebmVRvRvyPXEgI0IrFHOqd2v3nrxOyCcKQ2nVWxkxWC3WJ20Pu`, editor at
`https://script.google.com/d/1tq70LkebmVRvRvyPXEgI0IrFHOqd2v3nrxOyCcKQ2nVWxkxWC3WJ20Pu/edit`. `clasp push` works
from `tools/apps_script_daily_sync/` once `clasp login` is done; `clasp run <fn>` has NOT worked this whole build
(`NOT_FOUND reading from storage` — a known Apps Script API gap, never root-caused) — run functions from the
editor's own Run dropdown instead. Only plain-named functions show in that dropdown (Apps Script hides anything
ending in `_` as "private by convention") — `testEngineeringDryRun`, `runEngineeringLiveOnce`,
`testSendDailyEmailNow`, `installTrigger`, `pauseAutomation` are the ones meant to be run by hand.

**Scope**: covers Coding, Cluster-1, Cluster-2, and Engineering Datasets (added 2026-10-22/23). Company Ops Global
is NOT covered — no rep model / SOP for it, same original scope decision, never revisited.

**Schedule**: once/day at 18:30 IST, Mon–Fri, holiday-aware (`isScheduledSlot_`/`HOLIDAYS_`/`installTrigger`) — was
originally every 2 hours, changed 2026-10-22 per explicit ask. **The trigger is currently PAUSED**
(`pauseAutomation()` was run 2026-10-22, after an incident below) — do not run `installTrigger()` again until the
gaps below are addressed or knowingly accepted, because the trigger and the laptop's own `daily_sync_pull.py`
(launchd, 1am nightly, plus whatever's run manually) both write the SAME cells on the SAME sheet. Two writers on
the same cells is what caused the incident.

**The incident (2026-10-22), for context on why "finish testing" matters here**: the trigger fired at its scheduled
time right after a manual Python write, and the two writes interleaved — the result was a stunted, wrong-roster
version landing in the live, team-visible sheet (Coding's rep blocks showed two reps who'd been dropped from the
roster days earlier, and the main tables lost their day-columns). Took a live Python re-write plus a manual
`pauseAutomation()` to fix. Lesson: never let this trigger and a manual/launchd Python run be live at the same
time, and don't re-enable the trigger without reconciling the gaps below first — the Apps Script version is a
**point-in-time port of `daily_sync_pull.py` from 2026-10-13** and several real changes have landed in the Python
file since without being back-ported:

1. **No cascade-credit logic at all.** The single biggest gap. `daily_sync_pull.py` now applies cascade-credit
   (synthetic skipped-stage crediting, see that file's `CASCADE_*` block, 2026-10-15 for Cluster-1 then extended to
   *every* vertical 2026-10-22) to all 5 verticals' WTD/MTD/ITD numbers and day columns. The Apps Script has none of
   this — if re-enabled as-is, its Calls-attempted/Calls-picked numbers would read meaningfully LOWER than the
   correct (Python-written) numbers for the same day, for every vertical. This needs porting before the trigger can
   be trusted again, or the discrepancy needs to be explicitly flagged to whoever reads the sheet.
2. **No day-columns on the main (team-level) table.** Python added 7 fixed Mon–Sun columns before WTD Actual on the
   main table for Coding/Cluster-1/Cluster-2/Engineering on 2026-10-14 (`DAY_COLUMN_VERTICALS`,
   `COL_WIDTHS_WITH_DAYS`). The Apps Script's `writeVertical_()` header is still the old 10-column shape. The
   per-rep blocks DO have day columns (always did) — just not the team-level table.
3. **Per-rep blocks are 9 rows, not Python's 18.** Python's rep blocks interleave the 9 core metric rows with 9
   granular/derived rows (Same-day coverage, follow-up coverage, etc.) in `build_rep_block_rows`. The Apps Script's
   `buildRepBlockRows_` only ever built the 9 core rows — a pre-existing simplification, not something that just
   broke, but worth knowing before assuming the two outputs are structurally identical.
4. **Re-verify the rep rosters before trusting them.** Coding's `REP_MAPS_.coding` was found stale during this
   build (had 2 retired reps alongside the 3 current ones) and was NOT individually re-audited against
   `daily_sync_pull.py`'s own `REP_MAPS` after the fix — worth a side-by-side diff of both files' `REP_MAPS`/`REP_MAPS_`
   dicts before this is trusted unattended again, since Python's is the one that gets corrected first when someone's
   roster changes.
5. **Email is built but not wired to the trigger.** `sendDailySummaryEmail_()` exists and works (manually, via
   `testSendDailyEmailNow()`), covering Coding/Cluster-1/Cluster-2 only (Engineering deliberately excluded so far —
   see that function's own comment). `dailySyncTick()` → `startQueuedRun_()` only ever writes the sheet; it never
   calls the email function. `EMAIL_RECIPIENTS_` is hard-pinned to `['bhanu.enamala@lh2.ai']` (test phase) — **this
   is the standing hard rule in `CONTEXT.md`/`CLAUDE.md`: mail defaults to that address and nothing automated may
   widen the recipient list or self-schedule without the owner's explicit, per-change sign-off.** Widening this list
   or wiring it into the trigger needs that sign-off first, not just a code change.

**What's left for this handoff, in rough priority order**: (1) decide whether to port cascade-credit before
re-enabling anything, since its absence is a real, quantifiable wrongness, not cosmetic; (2) finish/verify the
email automation this handoff is actually for — real recipients (with sign-off), decide whether it should cover
Engineering too, wire it into (or alongside) the scheduled trigger, test end to end; (3) only then consider
`installTrigger()` again, and only after a side-by-side diff against a same-day `daily_sync_pull.py` run (see
"What to check it against" below — still the right verification method, now with a longer checklist).

## What it has to do, in order

1. **Work out the date window.** WTD = Monday of this week through today. MTD = the 1st of the month through today.
   Both need a *working-day count* too (Sat/Sun always excluded, plus specific holidays — currently just 2 Oct 2026,
   see `HOLIDAYS` in the script) — that count only feeds the target math, never the data pull. The pull itself always
   scans every calendar day in the window, holidays included; a rep's real holiday work still shows up.
2. **Pull HubSpot stage history** for five verticals (Coding, Company Ops Global, Cluster-1, Cluster-2, RAT/Engineering
   Datasets) across three portals (MAIN, CompanyOps, RAT — three separate private-app tokens). For each, every deal
   touched since the MTD start gets its full `dealstage` history (`propertiesWithHistory=dealstage`), then every
   stage-entry event in the window is bucketed into a WTD set and an MTD set, keyed by canonical stage label.
3. **Compute each Targets-sheet row** as a sum of specific stage labels — see `row_formulas()`. A deal that jumped
   past a stage counts for every earlier row on the same line (a deal at "Script Shared" implies it passed "GMeet
   Fixed", so it counts for VC setup too).
4. **Compute the target** for each row — see `target()`. The Targets tab only has a weekly number, no monthly one, so
   the daily-rate math and the scale-factor logic in that function are the whole of the "how do we get a number to
   compare against" answer. Read the big comment above `DAILY_LEADS` before touching this — it explains the
   Specialty-Datasets-for-RAT mapping and the proportional-scaling rule, both things the user asked for explicitly and
   that are easy to silently drop in a rewrite.
5. **Write the "Daily Sync" tab**: one banner row, then one block per vertical — a title row, a header row, nine
   metric rows, a blank spacer. Columns: Metric | WTD Actual | WTD Target | % Target Achievement | (blank) | MTD
   Actual | MTD Target | % Target Achievement.

## Apps Script specifics

- **Auth**: store each portal's HubSpot private-app token in Script Properties (`PropertiesService.getScriptProperties()`),
  never hard-coded in the script body. Same three tokens as `.env`'s `HUBSPOT_KEY_MAIN` / `HUBSPOT_KEY_COMPANYOPS` /
  `HUBSPOT_KEY_RAT` here — ask Bhanu for them, don't read them out of this repo's `.env` (it's gitignored and not in
  GitHub on purpose).
- **HTTP**: `UrlFetchApp.fetch()` for every HubSpot call. Deal-level `propertiesWithHistory` calls can't be batched —
  it's one call per touched deal, same as the Python. Expect this to be the slow part; the Python version takes
  8–12 minutes across all five verticals on a normal day. Apps Script has a 6-minute execution cap per run, so you'll
  likely need to either checkpoint progress across multiple trigger-driven runs, or fan the pulls out and cache
  intermediate per-deal results, rather than trying to do the whole thing in one invocation.
- **Trigger**: a time-driven trigger (`ScriptApp.newTrigger(...).timeBased().atHour(...).everyDays(1).create()`),
  set from the Apps Script editor's Triggers page once the script is attached to the sheet (Extensions → Apps Script).
- **Writing the sheet**: `SpreadsheetApp` — `getRange(...).setValues([[...], ...])` for the data, matching the exact
  layout `build_rows()` produces in the Python (one array of arrays, written in one call to avoid quota issues from
  cell-by-cell writes).

## What to check it against before trusting it

Run the Python (`tools/daily_sync_pull.py`, no `--write`) and your Apps Script on the same day and diff the two
outputs. They should match exactly — if they don't, the bug is almost always in the stage-label formulas or the
working-day calendar, not the HubSpot calls themselves.
