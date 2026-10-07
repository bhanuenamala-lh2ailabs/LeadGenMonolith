"""CLI: ``python -m leadgen.reports.daily [--date YYYY-MM-DD] [--days N] [--send] [--to ADDR] [--no-persist]``.

* default (dry-run): builds the report for the IST day from the event store, writes ``reports/daily/<date>/funnel.{html,md,csv,json}``,
  persists the facts to ``rpt_*`` (unless ``--no-persist``) and PRINTS the path.  It never mails.
* ``--days N``: backfill - rebuild the rpt_* facts and the report files of the last N IST days ending on --date (any past day can be produced,
  because everything is recomputed from ``deal_stage_event``).
* ``--send``: the ONLY path that mails (Gmail API).  Recipient: bhanu.enamala@lh2.ai unless ``--to`` names others explicitly.
No HubSpot or Google call is made except the Gmail send of ``--send``.  No scheduler, cron or launchd is installed by this program.
"""
import argparse
import json
import os
import sys
from typing import Any, Callable, Dict, List, Optional, Sequence

from leadgen import db as ldb
from leadgen.reports import REPORT_TOOL_VERSION, build, mailer, metrics as M, render

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_OUT = os.path.join(ROOT, "reports", "daily")
FILES = ("funnel.html", "funnel.md", "funnel.csv", "funnel.json")


def write_atomic(path: str, text: str) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(tmp, path)


def write_outputs(model: Dict[str, Any], out_root: str) -> Dict[str, str]:
    day = model["meta"]["report_day"]
    d = os.path.join(out_root, day)
    os.makedirs(d, exist_ok=True)
    texts = {"funnel.html": render.render_html(model), "funnel.md": render.render_md(model), "funnel.csv": render.render_csv(model), "funnel.json": render.render_json(model)}
    for name, text in texts.items():
        write_atomic(os.path.join(d, name), text)
    return {name: os.path.join(d, name) for name in texts}


def _run_row(con, kind: str, day: str, lo: str, hi: str, params: Dict[str, Any]) -> int:
    with ldb.transaction(con):
        cur = con.execute("INSERT INTO rpt_report_run (report_kind, report_day, window_start, window_end, status, metrics_version, params_json, tool_version) VALUES (?,?,?,?, 'running', ?,?,?)",
                          (kind, day, lo, hi, params.get("metrics_version"), json.dumps(params, sort_keys=True), REPORT_TOOL_VERSION))
        return cur.lastrowid


def _finish_run(con, run_id: int, model: Dict[str, Any], out_dir: str, outputs: Dict[str, str], status: str, error: Optional[str] = None) -> None:
    integ = model["integrity"]
    syncs = [f["last_success_at"] for f in integ["freshness"] if f["last_success_at"]]
    newest = max(integ["newest_event"].values()) if integ["newest_event"] else None
    asof = max(syncs + ([newest] if newest else [])) if (syncs or newest) else None
    with ldb.transaction(con):
        con.execute("UPDATE rpt_report_run SET status = ?, finished_at = ?, data_asof = ?, sync_freshness_json = ?, unmapped_stages_json = ?, history_failures = ?, output_dir = ?, "
                    "outputs_json = ?, stage_map_sha256 = ?, error = ? WHERE report_run_id = ?",
                    (status, ldb.utc_now_iso(), asof, json.dumps(integ["freshness"], sort_keys=True), json.dumps(integ["unmapped_stages"], sort_keys=True), integ["history_failures"],
                     out_dir, json.dumps({k: os.path.relpath(v, ROOT) if v.startswith(ROOT) else v for k, v in outputs.items()}, sort_keys=True), model["meta"]["stage_map_sha256"], error, run_id))


def _mail_row(con, run_id: int, mode: str, to: List[str], subject: str, sent_at: Optional[str] = None) -> None:
    with ldb.transaction(con):
        con.execute("UPDATE rpt_report_run SET mail_mode = ?, mail_to = ?, mail_subject = ?, mail_sent_at = ? WHERE report_run_id = ?", (mode, ", ".join(to), subject, sent_at, run_id))


def run(argv: Optional[Sequence[str]] = None, send_fn: Optional[Callable[..., Dict[str, Any]]] = None, out=None, service_factory=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m leadgen.reports.daily", description="Unified daily funnel report (dry-run unless --send).")
    ap.add_argument("--date", help="IST report day YYYY-MM-DD (default: today in IST)")
    ap.add_argument("--days", type=int, default=1, help="backfill: rebuild rpt_* facts and report files for the last N days ending on --date (default 1)")
    ap.add_argument("--send", action="store_true", help="mail the report (the ONLY path that mails). Default is dry-run")
    ap.add_argument("--to", help="recipient(s), comma separated. Default (and the only recipient allowed without --to): bhanu.enamala@lh2.ai")
    ap.add_argument("--no-persist", action="store_true", help="do not write rpt_* tables (the database is opened read-only)")
    ap.add_argument("--out-dir", default=DEFAULT_OUT, help="output root (default reports/daily)")
    ap.add_argument("--db", help="database path (default db/leadgen.sqlite)")
    ap.add_argument("--files-days", type=int, default=14, help="with --days N: write report files only for the last K days (default 14); rpt_* facts are persisted for all N days")
    ap.add_argument("--no-crosscheck", action="store_true", help="skip the legacy-snapshot cross-check panel")
    args = ap.parse_args(list(argv) if argv is not None else None)
    out = out or sys.stdout
    if args.days < 1:
        print("--days must be >= 1", file=sys.stderr)
        return 2
    D = args.date or M.today_ist()
    try:
        M.parse_day(D)
        if len(D) != 10:
            raise ValueError
    except Exception:
        print("--date must be YYYY-MM-DD", file=sys.stderr)
        return 2
    persist = not args.no_persist
    con = ldb.connect(args.db, readonly=not persist, must_exist=True)
    try:
        cfg = M.load_report_cfg()
        recipients = mailer.resolve_recipients(cfg, args.to) if args.send else []
        eng = M.Engine(con, cfg)
        days = M.day_range(M.day_add(D, -(args.days - 1)), D)
        run_id = None
        if persist:
            run_id = _run_row(con, "daily" if args.days == 1 else "rebuild", D, days[0], D, {"date": D, "days": args.days, "send": bool(args.send), "to": args.to,
                                                                                         "metrics_version": cfg["metrics_version"], "no_crosscheck": bool(args.no_crosscheck)})
        try:
            counts = None
            if persist:
                counts = build.persist_days(con, eng, days, run_id)
            final_model = None
            outputs = {}
            for d in days:
                if d != D and d not in days[-max(args.files_days, 1):]:
                    continue
                model = build.build_model(eng, d, with_crosscheck=not args.no_crosscheck)
                paths = write_outputs(model, args.out_dir)
                if d == D:
                    final_model, outputs = model, paths
            if persist:
                n_cc = build.persist_crosscheck(con, final_model, run_id)
                _finish_run(con, run_id, final_model, os.path.join(args.out_dir, D), outputs, "succeeded")
            subject = render.email_subject(final_model, cfg["mail"]["subject"])
            print("report %s  ->  %s" % (D, os.path.join(args.out_dir, D)), file=out)
            if args.days > 1:
                print("  backfilled %d days (%s .. %s)" % (len(days), days[0], days[-1]), file=out)
            if persist:
                print("  rpt_* rows written: %s; crosscheck rows: %d; report_run_id %d" % (counts, n_cc, run_id), file=out)
            else:
                print("  --no-persist: database opened read-only, nothing written to rpt_*", file=out)
            if args.send:
                msg = mailer.build_message(subject, recipients, render.render_md(final_model), render.render_html(final_model))
                try:
                    sender = send_fn or (lambda raw, c: mailer.send_raw(raw, c, service_factory=service_factory))
                    sender(mailer.encode_raw(msg), cfg)
                except Exception as exc:  # report the failure, never retry blindly
                    if persist:
                        _mail_row(con, run_id, "failed", recipients, subject)
                    print("  MAIL FAILED: %s: %s" % (type(exc).__name__, exc), file=out)
                    return 1
                if persist:
                    _mail_row(con, run_id, "sent", recipients, subject, ldb.utc_now_iso())
                print("  mail SENT to %s (subject: %s)" % (", ".join(recipients), subject), file=out)
            else:
                default_to = cfg["mail"]["default_to"]
                if persist:
                    _mail_row(con, run_id, "dry_run", [args.to or default_to], subject)
                print("  dry-run: not mailed. Would send to %s with subject %r; use --send to mail." % (args.to or default_to, subject), file=out)
            return 0
        except BaseException as exc:
            if persist and run_id is not None:
                try:
                    with ldb.transaction(con):
                        con.execute("UPDATE rpt_report_run SET status = 'failed', finished_at = ?, error = ? WHERE report_run_id = ?", (ldb.utc_now_iso(), "%s: %s" % (type(exc).__name__, exc), run_id))
                except Exception:
                    pass
            raise
    finally:
        con.close()


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
