# Daily Sync: porting to a Google Apps Script trigger

The reference logic is `tools/daily_sync_pull.py` in this repo. Run it once (`--write` flag optional) to see it
work, then read it top to bottom — the comments explain *why*, not just *what*. The goal: the same thing, but as an
Apps Script bound to the Supply Team Mapping sheet, firing on its own time-driven trigger, so nothing has to stay
running on anyone's machine.

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
