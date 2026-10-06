#!/usr/bin/env python3
"""One entry point for the daily Supply Funnel Report, for the n8n flow.

Steps, in order:
  1. HubSpot read-only sync (all three accounts)          skip with --skip-sync
  2. owner x stage snapshot (tools/owner_stage_snapshot.py)
  3. five-vertical report: HTML, text and snapshot.json saved to reports/vertical/<day>/
  4. mail, only with --send (to --to, default bhanu.enamala@lh2.ai)

Dry run by default: nothing is mailed unless --send is passed.  Nothing here schedules itself; the 9 PM timing belongs to the caller.

    .venv/bin/python tools/daily_report_run.py                       # sync, snapshot, build, no mail
    .venv/bin/python tools/daily_report_run.py --send                # ... and mail to bhanu
    .venv/bin/python tools/daily_report_run.py --skip-sync --send    # use the last sync
Exit code 0 on success; non-zero if any step fails, so the caller can alert.
"""
import argparse, datetime as dt, sqlite3, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = str(ROOT / ".venv/bin/python")
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def run(cmd):
    print("$ " + " ".join(cmd), flush=True)
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode != 0:
        sys.exit("step failed: " + " ".join(cmd))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", help="IST day YYYY-MM-DD (default: today IST)")
    ap.add_argument("--skip-sync", action="store_true")
    ap.add_argument("--send", action="store_true", help="mail the report (the only path that mails)")
    ap.add_argument("--to", default="bhanu.enamala@lh2.ai")
    a = ap.parse_args()
    day = a.day or dt.datetime.now(IST).date().isoformat()
    if not a.skip_sync:
        run([PY, "-m", "leadgen", "sync", "hubspot", "--account", "all"])
    run([PY, str(ROOT / "tools/owner_stage_snapshot.py")])
    cmd = [PY, "-m", "leadgen.reports.vertical_mail", "--date", day]
    if a.send:
        cmd += ["--send", "--to", a.to]
    run(cmd)
    print("done", day, "| mailed" if a.send else "| dry run, nothing mailed", flush=True)


if __name__ == "__main__":
    main()
