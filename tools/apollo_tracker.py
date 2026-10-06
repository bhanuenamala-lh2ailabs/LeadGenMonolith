#!/usr/bin/env python3
"""Apollo credit tracker: every credit, dot to dot, for audit.

Commands
  grant    --credits N --note "..."       record a top-up (e.g. 150000 bought/approved)
  meter                                    read Apollo's meter now (free call) and store it
  balance                                  granted - logged spend, and the meter's left-over, side by side
  report   [--from D] [--to D] [--by company|lead|purpose|caller|segment|outcome]
  dot      [--from D] [--to D] [--csv FILE]   one row per call: when, who, what, what came back, where it went
  unlogged                                 meter consumption since the last reading minus what the ledger logged (should be ~0)

Rule: every Apollo call goes through leadgen/apollo_ledger.call(), which writes one row to ext_apollo_call before it returns.
`scan` lists any file in the repo that talks to Apollo without going through that wrapper.
"""
import argparse, csv, datetime as dt, os, re, sqlite3, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from leadgen import apollo_ledger as L  # noqa: E402

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def db(write=False):
    con = sqlite3.connect(str(ROOT / "db/leadgen.sqlite"))
    con.row_factory = sqlite3.Row
    return con


def now_utc():
    d = dt.datetime.now(dt.timezone.utc)
    return d.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (d.microsecond // 1000)


def cmd_grant(a):
    con = db()
    with con:
        con.execute("INSERT INTO ext_apollo_grant(granted_at,credits,note) VALUES (?,?,?)", (now_utc(), a.credits, a.note))
    print("granted", a.credits, "|", a.note)


def cmd_meter(a):
    key = L._env()["APOLLO_API_KEY"]
    import requests
    r = requests.post(L.BASE + "/usage_stats/credit_usage_stats",
                      headers={"X-Api-Key": key, "Content-Type": "application/json"}, timeout=30)
    lc = (r.json() or {}).get("credit_usage_stats", {}).get("lead_credit", {})
    left, used = lc.get("left_over"), lc.get("consumed")
    con = db()
    with con:
        con.execute("INSERT INTO ext_apollo_meter_read(read_at,lead_left,lead_consumed,note) VALUES (?,?,?,?)",
                    (now_utc(), float(left), float(used), a.note or "manual read"))
    print(f"meter: lead credits left {left}, consumed {used}")


def cmd_balance(a):
    con = db()
    granted = con.execute("SELECT coalesce(sum(credits),0) FROM ext_apollo_grant").fetchone()[0]
    spent = con.execute("SELECT coalesce(sum(credits_charged),0) FROM ext_apollo_call WHERE charge_basis IN ('meter_delta','documented_rule')").fetchone()[0]
    last = con.execute("SELECT read_at, lead_left, lead_consumed FROM ext_apollo_meter_read ORDER BY meter_read_id DESC LIMIT 1").fetchone()
    print(f"granted (all top-ups): {granted:,.0f}")
    print(f"logged spend (this ledger): {spent:,.0f}")
    if last:
        print(f"last meter reading {last['read_at']}: left {last['lead_left']:,.0f}, consumed {last['lead_consumed']:,.0f}")


def window(a):
    lo = a.d_from or "1970-01-01"
    hi = a.d_to or "9999-12-31"
    return "substr(called_at_ist,1,10) BETWEEN ? AND ?", [lo, hi]


def cmd_report(a):
    con = db()
    w, args = window(a)
    col = {"company": "coalesce(request_key,'(none)')", "lead": "coalesce(person_id,request_key,'(none)')", "purpose": "purpose",
           "caller": "caller", "segment": "segment", "outcome": "outcome"}[a.by]
    rows = con.execute(f"""SELECT {col} AS k, count(*) AS calls, round(sum(coalesce(credits_charged,0)),1) AS credits,
                           sum(coalesce(email_returned,0)) AS emails, sum(coalesce(mobile_returned,0)) AS mobiles
                           FROM ext_apollo_call WHERE {w} GROUP BY 1 ORDER BY credits DESC""", args).fetchall()
    print(f"{'by ' + a.by:<50} {'calls':>7} {'credits':>10} {'emails':>7} {'mobiles':>8}")
    for r in rows:
        print(f"{str(r['k'])[:50]:<50} {r['calls']:>7} {r['credits']:>10} {r['emails']:>7} {r['mobiles']:>8}")
    print(f"{'TOTAL':<50} {sum(r['calls'] for r in rows):>7} {sum(r['credits'] or 0 for r in rows):>10.1f}")


def cmd_dot(a):
    con = db()
    w, args = window(a)
    rows = [dict(r) for r in con.execute(f"SELECT * FROM v_apollo_dot_to_dot WHERE {w} ORDER BY apollo_call_id", args)]
    if a.csv:
        with open(a.csv, "w", newline="", encoding="utf-8") as fh:
            wr = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["apollo_call_id"]); wr.writeheader(); wr.writerows(rows)
        print("wrote", len(rows), "rows to", a.csv)
    else:
        for r in rows[:50]:
            print(r["called_at_ist"], r["caller"], r["endpoint"], r["request_key"], r["credits_charged"], r["outcome"])
        print(f"({len(rows)} rows)")


def cmd_unlogged(a):
    con = db()
    last = con.execute("SELECT meter_read_id, read_at, lead_consumed FROM ext_apollo_meter_read ORDER BY meter_read_id DESC LIMIT 2").fetchall()
    if len(last) < 2:
        print("need two meter readings to compare (run `meter` now and again later)")
        return
    new, old = last[0], last[1]
    meter_delta = new["lead_consumed"] - old["lead_consumed"]
    logged = con.execute("SELECT coalesce(sum(credits_charged),0) FROM ext_apollo_call WHERE called_at > ? AND called_at <= ?",
                         (old["read_at"], new["read_at"])).fetchone()[0]
    print(f"meter spent between {old['read_at']} and {new['read_at']}: {meter_delta:,.0f}")
    print(f"logged by this ledger in the same window:            {logged:,.0f}")
    print(f"unlogged (meter minus ledger):                         {meter_delta - logged:,.0f}")


SCAN_PAT = re.compile(r"api\.apollo\.io|APOLLO_API_KEY|apollo_key|APOLLO_PAUSED")


def cmd_scan(a):
    hits = []
    for dp, dn, fn in os.walk(ROOT):
        if any(x in dp for x in ("/.venv", "/legacy/", "/db/", "/reports/", "/out/", "/node_modules", "/.git")):
            continue
        for f in fn:
            if not f.endswith(".py"):
                continue
            p = Path(dp) / f
            try:
                txt = p.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            if "api.apollo.io" in txt and "apollo_ledger" not in txt and "apollo_tracker" not in p.name and p.name != "apollo_ledger.py":
                hits.append(str(p.relative_to(ROOT)))
    print("files that talk to Apollo WITHOUT the wrapper (move these to leadgen/apollo_ledger.call):")
    for h in hits:
        print("  ", h)
    print(f"({len(hits)} files)")


def main():
    ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="cmd", required=True)
    g = sp.add_parser("grant"); g.add_argument("--credits", type=float, required=True); g.add_argument("--note", required=True)
    m = sp.add_parser("meter"); m.add_argument("--note")
    sp.add_parser("balance")
    r = sp.add_parser("report"); r.add_argument("--from", dest="d_from"); r.add_argument("--to", dest="d_to")
    r.add_argument("--by", default="company", choices=["company", "lead", "purpose", "caller", "segment", "outcome"])
    d = sp.add_parser("dot"); d.add_argument("--from", dest="d_from"); d.add_argument("--to", dest="d_to"); d.add_argument("--csv")
    sp.add_parser("unlogged"); sp.add_parser("scan")
    a = ap.parse_args()
    {"grant": cmd_grant, "meter": cmd_meter, "balance": cmd_balance, "report": cmd_report, "dot": cmd_dot,
     "unlogged": cmd_unlogged, "scan": cmd_scan}[a.cmd](a)


if __name__ == "__main__":
    main()
