#!/usr/bin/env python3
"""Query the local Apollo call ledger (ext_apollo_call, migrations 0018 and 0019).

    python tools/apollo_audit.py --from 2026-10-05 --to 2026-10-05
    python tools/apollo_audit.py --from 2026-10-01 --to 2026-10-31 --segment "EdTech TAM"
    python tools/apollo_audit.py --from 2026-10-01 --to 2026-10-31 --outcome discarded --csv out/apollo_discarded.csv

Read-only. Prints by segment, purpose and outcome: calls, credits, mobiles, e-mails, and what became of the results.
Rows written before migration 0019 show outcome 'unrecorded'; their history lives in docs/audit/apollo/master_ledger.csv.
"""
import argparse, csv, sqlite3, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="d_from", required=True, help="IST day, inclusive, YYYY-MM-DD")
    ap.add_argument("--to", dest="d_to", required=True, help="IST day, inclusive, YYYY-MM-DD")
    ap.add_argument("--segment"); ap.add_argument("--caller"); ap.add_argument("--outcome")
    ap.add_argument("--csv")
    a = ap.parse_args()
    con = sqlite3.connect("file:%s?mode=ro" % (ROOT / "db/leadgen.sqlite"), uri=True); con.row_factory = sqlite3.Row
    where, args = ["substr(called_at_ist,1,10) BETWEEN ? AND ?"], [a.d_from, a.d_to]
    if a.segment: where.append("segment = ?"); args.append(a.segment)
    if a.caller: where.append("caller = ?"); args.append(a.caller)
    if a.outcome: where.append("outcome = ?"); args.append(a.outcome)
    q = """SELECT segment, purpose, caller, endpoint, count(*) AS calls, round(coalesce(sum(credits_charged),0),1) AS credits,
                  coalesce(sum(mobile_returned),0) AS mobiles, coalesce(sum(email_returned),0) AS emails,
                  sum(outcome='pushed_hubspot') AS pushed, sum(outcome='discarded') AS discarded,
                  sum(outcome='held_local') AS held_local, sum(outcome='no_result') AS no_result, sum(outcome='unrecorded') AS unrecorded
           FROM ext_apollo_call WHERE """ + " AND ".join(where) + " GROUP BY 1,2,3,4 ORDER BY credits DESC"
    rows = [dict(r) for r in con.execute(q, args)]
    reasons = [dict(r) for r in con.execute("SELECT outcome, outcome_reason, count(*) AS n FROM ext_apollo_call WHERE " +
               " AND ".join(where) + " AND outcome='discarded' GROUP BY 1,2 ORDER BY n DESC", args)]
    print("%-22s %-40s %-18s %6s %9s %7s %7s %7s %7s %7s" % ("segment", "purpose", "caller", "calls", "credits", "mobile", "email", "pushed", "discard", "held"))
    for r in rows:
        print("%-22s %-40s %-18s %6d %9.1f %7d %7d %7d %7d %7d" % (r["segment"][:22], (r["purpose"] or "")[:40], (r["caller"] or "")[:18],
              r["calls"], r["credits"], r["mobiles"], r["emails"], r["pushed"], r["discarded"], r["held_local"]))
    if not rows: print("(no rows in this range: the ledger started with migration 0018, so earlier calls are not here)")
    if reasons:
        print("\nDiscarded, by reason:")
        for r in reasons: print("  %5d  %s" % (r["n"], r["outcome_reason"]))
    if a.csv:
        with open(a.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["segment"]); w.writeheader(); w.writerows(rows)
        print("\nwrote", a.csv)


if __name__ == "__main__":
    sys.exit(main())
