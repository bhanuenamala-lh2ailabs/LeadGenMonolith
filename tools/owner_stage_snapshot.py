#!/usr/bin/env python3
"""Write today's owner x stage snapshot of open deals from the synced local data (ext_owner_stage_snapshot, migration 0020).
Run after each HubSpot sync.  Re-running the same IST day replaces that day's rows."""
import sqlite3, datetime as dt
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "db/leadgen.sqlite"
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

def main():
    now = dt.datetime.now(dt.timezone.utc)
    day = now.astimezone(IST).date().isoformat()
    taken = now.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (now.microsecond // 1000)
    con = sqlite3.connect(DB)
    with con:
        con.execute("DELETE FROM ext_owner_stage_snapshot WHERE snapshot_day=?", (day,))
        con.execute("""INSERT INTO ext_owner_stage_snapshot(snapshot_day,account_id,pipeline_id,owner_id,stage_id,deals,taken_at)
                       SELECT ?, d.account_id, d.pipeline_id, COALESCE(d.owner_id,''), d.stage_id, count(*), ?
                       FROM deal d WHERE d.is_archived=0 GROUP BY d.account_id, d.pipeline_id, d.owner_id, d.stage_id""", (day, taken))
        n = con.execute("SELECT count(*) FROM ext_owner_stage_snapshot WHERE snapshot_day=?", (day,)).fetchone()[0]
    print("snapshot", day, "rows", n)

if __name__ == "__main__":
    main()
