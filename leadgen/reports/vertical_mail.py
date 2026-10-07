"""Supply Funnel Report by Vertical: the five-section daily mail, built read-only from ``deal_stage_event`` / ``stage`` / ``ops_sync_state``.

Layout follows the approved mail "Supply Funnel Report by Vertical" (one section per vertical, three tiles, one stage table).  Row
labels and their stage-label mapping live in ``config/mail_rows.yaml``; nothing here hard-codes a stage.

Counting rules (IST days, ``deal_stage_event.ist_day``):
  * entry row (Leads Assigned / Cold Lead / Cold Called Assigned) and the Closed/Won row: every source counts (report.yaml basis);
    every other row counts human CRM_UI moves only.
  * per day   : distinct deals that qualify on that day (a Dead row is the union of its dead stages, distinct per day).
  * inception : distinct deals that ever qualified for the row, as of the report day (all time).
  * rolling 7 / prev 7 : sum of the daily distinct counts over the window (the flow convention of the approved mail).
  * engaged   : distinct deals with at least one human stage move in the pipeline on the day; 7-day = set union over the window.
  * wins      : inception of the Closed/Won row; "this week" = inception(today) - inception(today - 7).  RAT shows Sample Received instead.

Day status (never a missing day shown as zero):
  * weekend : Saturday or Sunday with no events at all.  Its zeros are real and count as covered days.
  * partial : the report day when the deals sync finished before that IST day ended (today's mail).  Daily chips are withheld.
  * gap     : a weekday whose total events are below 5 % of the median weekday of the 14 days before it.  Gap days are excluded from
              every window and from the chips; the footer names them.  They are never rendered as 0.
  * ok      : everything else.

Dry-run by default: the CLI only writes ``reports/vertical/<date>/index.html`` (and ``index.txt``).  ``--send`` is the ONLY path that mails,
and it goes to ``config/report.yaml mail.default_to`` unless ``--to`` names someone else.  Tokens are never printed.

Usage:
  .venv/bin/python -m leadgen.reports.vertical_mail --date 2026-10-05            # dry-run: writes the HTML, prints the path
  .venv/bin/python -m leadgen.reports.vertical_mail --date 2026-10-05 --send      # mails the report once
"""
import argparse
import collections
import datetime
import html as htmlmod
import json
import os
import sqlite3
import statistics
import sys
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from leadgen import config as lcfg
from leadgen.google import auth
from leadgen.reports import mailer

TITLE = "Supply Funnel Report by Vertical"
TOKEN_FILE = "companyops_gmail_send_token.json"
COVERED = ("ok", "weekend", "partial")
GAP_FRACTION = 0.05
GAP_LOOKBACK = 14
IST_OFFSET = datetime.timedelta(hours=5, minutes=30)
EM = "—"
MUTED, UP, DOWN = "#6b7280", "#1a7f37", "#c0362c"
DEFAULT_DB = os.path.join(lcfg.PROJECT_ROOT, "db", "leadgen.sqlite")
DEFAULT_OUT = os.path.join(lcfg.PROJECT_ROOT, "reports", "vertical")


class MappingError(RuntimeError):
    pass


# ----------------------------------------------------------------------------------------------------------------- dates
def parse_day(s: str) -> datetime.date:
    return datetime.date.fromisoformat(s)


def day_str(d: datetime.date) -> str:
    return d.isoformat()


def add_days(day: str, n: int) -> str:
    return day_str(parse_day(day) + datetime.timedelta(days=n))


def window(end: str, n: int) -> List[str]:
    """The n IST days ending on ``end`` (inclusive), oldest first."""
    return [add_days(end, -i) for i in range(n - 1, -1, -1)]


def is_weekend(day: str) -> bool:
    return parse_day(day).weekday() >= 5


def short(day: str) -> str:
    return parse_day(day).strftime("%d %b")


def long_(day: str) -> str:
    return parse_day(day).strftime("%d %b %Y")


def ist_hhmm(iso_utc: str) -> str:
    t = datetime.datetime.strptime(iso_utc[:19], "%Y-%m-%dT%H:%M:%S") + IST_OFFSET
    return t.strftime("%H:%M")


def utc_day_end(day: str) -> datetime.datetime:
    """The UTC instant at which IST day ``day`` ends (next IST midnight)."""
    return datetime.datetime.combine(parse_day(day) + datetime.timedelta(days=1), datetime.time(0)) - IST_OFFSET


# ------------------------------------------------------------------------------------------------------------- config
def load_mail_rows(config_dir: Optional[str] = None) -> Dict[str, Any]:
    cfg = lcfg.load_yaml("mail_rows", config_dir)
    keys = [v["key"] for v in cfg["verticals"]]
    if len(keys) != len(set(keys)):
        raise MappingError("mail_rows.yaml: duplicate vertical key")
    return cfg


class Row(object):
    __slots__ = ("name", "labels", "prefix", "entry", "any_source", "inception_from")

    def __init__(self, name: str, labels: Sequence[str], prefix: Optional[str], entry: bool, any_source: bool = False,
                 inception_from: Optional[str] = None) -> None:
        self.name, self.labels, self.prefix, self.entry, self.any_source = name, frozenset(labels), prefix, entry, any_source
        # inception_from: only deals that ENTERED this row on or after this IST day count toward its inception (used for the Cluster 2
        # cold call row, so the 15 Sep bulk migration is not counted as new leads).  Daily and rolling counts are unaffected.
        self.inception_from = inception_from

    def matches(self, label: str) -> bool:
        if label in self.labels:
            return True
        return bool(self.prefix) and label.startswith(self.prefix)


HIDDEN_WON = "__won_hidden__"


def resolve_rows(vdef: Dict[str, Any], stage_labels: Iterable[str]) -> List[Row]:
    """Turn the config rows into Row objects.  Every named label must exist in the pipeline's stage table; a label that is not there is an error."""
    have = set(stage_labels)
    out, missing, seen = [], [], {}
    for r in vdef["rows"]:
        labels = list(r.get("labels") or [])
        prefix = r.get("label_prefix")
        for lab in labels:
            if lab not in have:
                missing.append("%s: %r" % (r["name"], lab))
            if lab in seen and seen[lab] != r["name"] and not (r.get("overlap") or seen.get("__overlap__%s" % lab)):
                raise MappingError("%s: label %r maps to two rows (%s, %s)" % (vdef["key"], lab, seen[lab], r["name"]))
            seen[lab] = r["name"]
            if r.get("overlap"):
                seen["__overlap__%s" % lab] = True  # a derived row (e.g. Discovery Call Done) may share stages with the rows that follow it
        if prefix and not any(x.startswith(prefix) for x in have):
            missing.append("%s: prefix %r matches no stage" % (r["name"], prefix))
        out.append(Row(r["name"], labels, prefix, bool(r.get("entry")), bool(r.get("any_source")), r.get("inception_from")))
    if missing:
        raise MappingError("%s: stage labels not found in the pipeline: %s" % (vdef["key"], "; ".join(missing)))
    won = vdef.get("won_row")
    if won and vdef.get("tile") == "wins" and won not in [r.name for r in out]:
        # the WINS tile needs Closed/Won even when the mail does not print that row (Cluster 1 layout): a hidden row carries it
        out.append(Row(HIDDEN_WON, [won], None, False, True))
    return out


# --------------------------------------------------------------------------------------------------------------- data
Event = Tuple[int, str, str, bool]          # (deal_id, stage label, ist_day, is_human)


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REASSIGN_FILE = os.path.join(ROOT, "data", "audit", "itsvc_reassign_to_coding.json")
# Coding-sourced leads worked by Harsha (RAT) and Vaishnavi (Cluster 2) are counted in the Coding funnel, not in their own funnel (user, 2026-10-06).
# Their stage labels are mapped onto the Coding rows; a stage with no Coding row is not counted anywhere.
REASSIGN_LABEL = {
    # Coding rows
    "cold called assigned": "Cold Call", "cold call": "Cold Call",
    "no pickup": "No Pickup", "no pickup/callback": "No Pickup",
    "interested": "Interested", "closed/won": "Closed/Won",
    # the stages that have no Coding row, mapped by the nearest meaning (decision recorded 2026-10-06):
    "replied": "Interested", "1st interest sent": "Interested", "1st interest follow up": "Interested",
    "awaiting meeting": "Interested",
    "discovery call": "GMeet Fixed", "discovery call done": "GMeet Fixed", "gmeet1 completed": "GMeet Fixed",
    "call rescheduled": "No Pickup", "callback": "No Pickup", "retired: callback +1 day (use no pickup + task instead)": "No Pickup",
    "awaiting results": "Script Shared",
    "sample requested": "Script Shared", "sample follow up": "Script Shared", "sample received": "Script Results Received",
    "negotiation": "Commercial Negotiation", "contract signed": "Deal Contract Signed",
    "loi signed": "LOI",
}
REASSIGN_HOME = {"companyops": "2464812771", "rat": "2575252183"}


def load_reassigned(con: sqlite3.Connection) -> Dict[int, Dict[str, Any]]:
    """{local deal_id: {'home': (account, pipeline)}} for the coding-sourced deals worked in RAT or Cluster 2."""
    if not os.path.exists(REASSIGN_FILE):
        return {}
    rows = json.load(open(REASSIGN_FILE, encoding="utf-8"))
    hs = {str(r["deal_id"]): r["portal"] for r in rows}
    out = {}  # type: Dict[int, Dict[str, Any]]
    for hs_id, portal in hs.items():
        r = con.execute("SELECT deal_id FROM deal WHERE hs_deal_id = ?", (hs_id,)).fetchone()
        if r:
            out[int(r[0])] = {"home": (portal, REASSIGN_HOME[portal])}
    return out


def coding_events_from_reassigned(con: sqlite3.Connection, reassigned: Dict[int, Dict[str, Any]], upto: str) -> List[Event]:
    """Events of the reassigned deals in their own pipeline, relabelled onto the Coding rows; unmapped stages are dropped."""
    out = []  # type: List[Event]
    for deal_id, info in reassigned.items():
        account, pipeline = info["home"]
        for d, label, day, human in load_events(con, account, pipeline, upto):
            if d != deal_id:
                continue
            key = label.strip().lower()
            # every human move counts toward the engaged tile; a stage with no Coding row keeps its own label so it matches no row
            out.append((d, REASSIGN_LABEL.get(key, label), day, human))
    return out


def load_events(con: sqlite3.Connection, account: str, pipeline: str, upto: str) -> List[Event]:
    sql = ("SELECT e.deal_id, s.label, e.ist_day, e.is_human FROM deal_stage_event e "
           "JOIN stage s ON s.account_id = e.account_id AND s.pipeline_id = e.pipeline_id AND s.stage_id = e.to_stage_id "
           "WHERE e.account_id = ? AND e.pipeline_id = ? AND e.ist_day <= ?")
    return [(int(r[0]), r[1], r[2], bool(r[3])) for r in con.execute(sql, (account, pipeline, upto))]


def stage_labels(con: sqlite3.Connection, account: str, pipeline: str) -> List[str]:
    return [r[0] for r in con.execute("SELECT label FROM stage WHERE account_id = ? AND pipeline_id = ?", (account, pipeline))]


def sync_times(con: sqlite3.Connection, accounts: Iterable[str]) -> Dict[str, Optional[str]]:
    out = {}  # type: Dict[str, Optional[str]]
    for acc in accounts:
        r = con.execute("SELECT MAX(last_success_at) FROM ops_sync_state WHERE source_system = 'hubspot' AND account_id = ? "
                        "AND object_type = 'deals' AND last_status = 'ok'", (acc,)).fetchone()
        out[acc] = r[0] if r else None
    return out


class Vertical(object):
    """Counts for one funnel.  ``events`` are the funnel's own stage entries up to the report day."""

    def __init__(self, vdef: Dict[str, Any], rows: List[Row], events: List[Event]) -> None:
        self.vdef, self.rows, self.events = vdef, rows, events
        self.per_day = [collections.defaultdict(set) for _ in rows]   # row -> day -> deals
        self.first = [dict() for _ in rows]                             # row -> deal -> first qualifying day
        self.engaged = collections.defaultdict(set)                     # day -> deals with a human move
        for deal, label, day, human in events:
            if human:
                self.engaged[day].add(deal)
            for i, row in enumerate(rows):
                if not row.matches(label):
                    continue
                if not (row.entry or row.any_source or human):
                    continue
                self.per_day[i][day].add(deal)
                if row.inception_from and day < row.inception_from:
                    continue  # counted in daily figures, not in inception of this row
                prev = self.first[i].get(deal)
                if prev is None or day < prev:
                    self.first[i][deal] = day

    def day_count(self, i: int, day: str) -> int:
        return len(self.per_day[i].get(day, ()))

    def inception(self, i: int, day: str) -> int:
        return sum(1 for d in self.first[i].values() if d <= day)

    def engaged_day(self, day: str) -> int:
        return len(self.engaged.get(day, ()))

    def engaged_union(self, days: Iterable[str]) -> Set[int]:
        out = set()  # type: Set[int]
        for d in days:
            out |= self.engaged.get(d, set())
        return out

    def row_index(self, name: str) -> int:
        for i, r in enumerate(self.rows):
            if r.name == name:
                return i
        raise MappingError("%s: no row %r" % (self.vdef["key"], name))


def day_status_map(days: Sequence[str], totals: Dict[str, int], report_day: str, report_partial: bool) -> Dict[str, str]:
    """Status of every day in ``days`` (see the module docstring).  ``totals`` = all events per IST day across the five funnels."""
    prior = [add_days(report_day, -k) for k in range(1, GAP_LOOKBACK + 1)]
    weekday_totals = [totals.get(d, 0) for d in prior if not is_weekend(d) and totals.get(d, 0) > 0]
    median = statistics.median(weekday_totals) if weekday_totals else 0
    out = {}  # type: Dict[str, str]
    for d in days:
        n = totals.get(d, 0)
        if d == report_day and report_partial:
            out[d] = "partial"
        elif is_weekend(d):
            out[d] = "weekend" if n == 0 else "ok"
        elif median and n < GAP_FRACTION * median:
            out[d] = "gap"
        else:
            out[d] = "ok"
    return out


# ------------------------------------------------------------------------------------------------- windows and chips
def wsum(fn, days: Sequence[str], status: Dict[str, str]) -> Tuple[Optional[int], int, int]:
    """(sum over covered days or None, covered days, days in window).  Gap days contribute nothing: they are not zeros."""
    cov = [d for d in days if status[d] in COVERED]
    if not cov:
        return None, 0, len(days)
    return sum(fn(d) for d in cov), len(cov), len(days)


def chip(cur: Optional[int], prev: Optional[int], cov_cur: int = 7, n_cur: int = 7, cov_prev: int = 7, n_prev: int = 7) -> Dict[str, Any]:
    """Percent-change chip.  Equal values read 'flat'; a change from 0 to a positive number reads '↑ 100%'.
    When a window is partial (gap days) the two sides are compared per covered day, and the chip says so with ``per_day``."""
    if cur is None or prev is None or cov_cur == 0 or cov_prev == 0:
        return {"kind": "na", "text": "n/a"}
    partial = cov_cur != n_cur or cov_prev != n_prev
    a = cur / float(cov_cur) if partial else float(cur)
    b = prev / float(cov_prev) if partial else float(prev)
    out = {"per_day": partial}  # type: Dict[str, Any]
    if abs(a - b) < 1e-9:
        return dict(out, kind="flat", text="flat")
    if b == 0:
        return dict(out, kind="up", text="↑ 100%")
    pct = int(round((a - b) * 100.0 / b))
    if a > b:
        return dict(out, kind="up", text="↑ %d%%" % abs(pct))
    return dict(out, kind="down", text="↓ %d%%" % abs(pct))


def daily_chip(cur: Optional[int], prev: Optional[int], today_status: str, yest_status: str) -> Dict[str, Any]:
    if today_status == "partial":
        return {"kind": "na", "text": "—"}  # today is partial: no change chip, shown as a dash
    if yest_status == "weekend":
        return {"kind": "na", "text": "vs weekend"}
    if yest_status == "gap" or prev is None:
        return {"kind": "na", "text": "n/a"}
    return chip(cur, prev)


# ---------------------------------------------------------------------------------------------------- section data
def build_section(v: Vertical, report_day: str, status: Dict[str, str], days7: Sequence[str], prev7: Sequence[str]) -> Dict[str, Any]:
    yest = add_days(report_day, -1)
    won_or_sample = v.vdef["won_row"] if v.vdef["tile"] == "wins" else v.vdef["samples_row"]
    tile_idx = v.row_index(HIDDEN_WON if won_or_sample not in [r.name for r in v.rows] else won_or_sample)
    to_date = v.inception(tile_idx, report_day)
    week_delta = to_date - v.inception(tile_idx, add_days(report_day, -7))
    eng7 = v.engaged_union(d for d in days7 if status[d] in COVERED)
    eng7_prev = v.engaged_union(d for d in prev7 if status[d] in COVERED)
    cov7 = sum(1 for d in days7 if status[d] in COVERED)
    covp = sum(1 for d in prev7 if status[d] in COVERED)
    eng_today = v.engaged_day(report_day)
    eng_yest = v.engaged_day(yest)
    rows = []
    for i, row in enumerate(v.rows):
        if row.name == HIDDEN_WON:
            continue  # carried for the WINS tile only; not printed
        inc = v.inception(i, report_day)
        r7 = wsum(lambda d, i=i: v.day_count(i, d), days7, status)
        p7 = wsum(lambda d, i=i: v.day_count(i, d), prev7, status)
        t = v.day_count(i, report_day) if status[report_day] != "gap" else None
        y = v.day_count(i, yest) if status[yest] != "gap" else None
        rows.append({
            "name": row.name, "inception": inc,
            "roll7": r7[0], "roll7_cov": r7[1], "prev7": p7[0], "prev7_cov": p7[1],
            "chip7": chip(r7[0], p7[0], r7[1], len(days7), p7[1], len(prev7)),
            "today": t, "yest": y, "today_status": status[report_day], "yest_status": status[yest],
            "chip_day": daily_chip(t, y, status[report_day], status[yest]),
        })
    return {
        "key": v.vdef["key"], "title": v.vdef["title"], "tile": v.vdef["tile"],
        "tile_label": "WINS TO DATE" if v.vdef["tile"] == "wins" else "SAMPLES RECEIVED TO DATE",
        "to_date": to_date, "week_delta": week_delta,
        "eng7": len(eng7), "eng7_cov": cov7, "eng7_prev": len(eng7_prev), "eng7_prev_cov": covp,
        "chip_eng7": chip(len(eng7), len(eng7_prev), cov7, len(days7), covp, len(prev7)),
        "eng_today": eng_today, "eng_yest": eng_yest, "yest_status": status[yest],
        "today_status": status[report_day],
        "chip_eng_day": daily_chip(eng_today, eng_yest, status[report_day], status[yest]),
        "rows": rows,
    }


def build_report(con: sqlite3.Connection, report_day: str, cfg: Optional[Dict[str, Any]] = None, assume_complete: bool = False) -> Dict[str, Any]:
    """Everything the mail needs for ``report_day``, read from ``con`` (read-only use)."""
    cfg = cfg or load_mail_rows()
    days7 = window(report_day, 7)
    prev7 = window(add_days(report_day, -7), 7)
    lookback = window(report_day, GAP_LOOKBACK + 1)[0]
    accounts = sorted({v["account"] for v in cfg["verticals"]})
    syncs = sync_times(con, accounts)
    partial = any(s is not None and s[:19] < utc_day_end(report_day).strftime("%Y-%m-%dT%H:%M:%S") for s in syncs.values())
    if assume_complete:  # the owner has confirmed the day is over; numbers are as of the last sync
        partial = False
    verticals = []  # type: List[Vertical]
    totals = collections.Counter()  # type: collections.Counter
    reassigned = load_reassigned(con)
    for vdef in cfg["verticals"]:
        labels = stage_labels(con, vdef["account"], vdef["pipeline_id"])
        rows = resolve_rows(vdef, labels)
        events = load_events(con, vdef["account"], vdef["pipeline_id"], report_day)
        if vdef["key"] == "coding":
            events += coding_events_from_reassigned(con, reassigned, report_day)
        elif reassigned and vdef["pipeline_id"] in REASSIGN_HOME.values():
            events = [e for e in events if e[0] not in reassigned]
        for _, _, day, _ in events:
            if day >= lookback:
                totals[day] += 1
        verticals.append(Vertical(vdef, rows, events))
    status = day_status_map(sorted(set(days7 + prev7 + [add_days(report_day, -1)])), totals, report_day, partial)
    sections = [build_section(v, report_day, status, days7, prev7) for v in verticals]
    gaps = sorted(d for d, s in status.items() if s == "gap" and d in set(days7 + prev7))
    weekends = sorted(d for d, s in status.items() if s == "weekend" and d in set(days7 + prev7))
    return {
        "report_day": report_day, "subject": "%s - %s" % (TITLE, long_(report_day)),
        "sections": sections, "status": status, "gaps": gaps, "weekends": weekends,
        "partial": partial, "syncs": {a: (ist_hhmm(s) if s else None) for a, s in syncs.items()},
        "days7": days7, "prev7": prev7,
        "accounts": {v["key"]: v["account"] for v in cfg["verticals"]},
    }


# ------------------------------------------------------------------------------------------------------- rendering
def fmt(n: Optional[int]) -> str:
    return "n/a" if n is None else "{:,}".format(n)


def chip_html(c: Dict[str, Any]) -> str:
    color = {"up": UP, "down": DOWN}.get(c["kind"], MUTED)
    return "<span style=\"color:%s;font-weight:700;\">%s</span>" % (color, htmlmod.escape(c["text"]))


def window_note(cov: int, n: int) -> str:
    return "" if cov == n else " [%d of %d days]" % (cov, n)


def week_sub(delta: int) -> str:
    return "flat this week" if delta == 0 else "%+d this week" % delta


def tile_html(label: str, value: int, sub_html: str) -> str:
    return ('<td style="text-align:center;padding:16px 8px;width:33%%;">'
            '<div style="font-size:11px;font-weight:800;color:#6b7280;letter-spacing:.03em;">%s</div>'
            '<div style="font-size:26px;font-weight:800;margin-top:4px;">%s</div>'
            '<div style="font-size:13px;margin-top:4px;">%s</div></td>') % (label, fmt(value), sub_html)


TD = 'style="text-align:right;font-size:13px;padding:7px 8px;border:1px solid #e2e4e8;white-space:nowrap;"'
TH = ('style="background:#f5f6f8;text-align:center;font-size:11px;font-weight:700;padding:6px 4px;border:1px solid #e2e4e8;'
      'white-space:nowrap;"')


def pdf_day(day: str) -> str:
    """Date as the approved mail prints it: 'Sep 30' / 'Oct 5' (no leading zero)."""
    d = parse_day(day)
    return "%s %d" % (d.strftime("%b"), d.day)


def pdf_long(day: str) -> str:
    """'Sep 30, 2026' / 'Oct 5, 2026'."""
    return "%s, %d" % (pdf_day(day), parse_day(day).year)


def section_html(s: Dict[str, Any], report_day: str, days7: Sequence[str], prev7: Sequence[str]) -> str:
    # Layout follows the approved mail 'Supply Funnel Report by Vertical - 30 Sep 2026' exactly: no notes inside the sections.
    title = "%s %s %s" % (s["title"], EM, pdf_long(report_day))
    eng_sub = chip_html(s["chip_eng7"])
    day_sub = chip_html(s["chip_eng_day"])
    wins_sub = htmlmod.escape(week_sub(s["week_delta"]))
    tiles = (tile_html(s["tile_label"], s["to_date"], wins_sub) + tile_html("7-DAY LEADS ENGAGED", s["eng7"], eng_sub)
             + tile_html("DAILY LEADS ENGAGED", s["eng_today"], day_sub))
    body = []
    for r in s["rows"]:
        bold = r["name"] in ("Closed/Won", "Dead")
        bg = "#eef2f7" if bold else "#ffffff"
        weight = "800" if bold else "600"
        yest_cell = fmt(r["yest"])
        body.append(
            '<tr style="background:%s;"><td style="font-size:13px;font-weight:%s;padding:7px 8px;border:1px solid #e2e4e8;">%s</td>'
            '<td %s>%s</td>'
            '<td %s>%s</td><td %s>%s</td><td %s>%s</td>'
            '<td %s>%s</td><td %s>%s</td><td %s>%s</td></tr>' % (
                bg, weight, htmlmod.escape(r["name"]),
                TD.replace("text-align:right;", "text-align:right;font-style:italic;background:#eaf1fb;"), fmt(r["inception"]),
                TD, fmt(r["roll7"]),
                TD, fmt(r["prev7"]),
                TD, chip_html(r["chip7"]),
                TD, fmt(r["today"]), TD, yest_cell, TD, chip_html(r["chip_day"])))
    head1 = ('<tr><th %s rowspan="2">Stage</th><th %s rowspan="2">INCEPTION TO DATE</th>'
             '<th %s colspan="3">ROLLING 7 DAYS</th><th %s colspan="3">DAILY</th></tr>') % (TH, TH, TH, TH)
    head2 = ('<tr><th %s>%s - %s</th><th %s>%s - %s</th><th %s>Change</th>'
             '<th %s>%s</th><th %s>%s</th><th %s>Change</th></tr>') % (
        TH, pdf_day(days7[0]), pdf_day(days7[-1]), TH, pdf_day(prev7[0]), pdf_day(prev7[-1]), TH,
        TH, pdf_long(report_day), TH, pdf_day(add_days(report_day, -1)), TH)
    return (
        '<h2 style="font-size:19px;margin:28px 0 12px;border-top:2px solid #e2e4e8;padding-top:16px;">%s</h2>'
        '<table style="width:100%%;border-collapse:separate;background:#fafbfc;border:1px solid #e2e4e8;border-radius:6px;margin-bottom:14px;">'
        '<tr>%s</tr></table>'
        '<div style="overflow-x:auto;-webkit-overflow-scrolling:touch;">'
        '<table style="border-collapse:collapse;width:100%%;min-width:520px;">%s%s%s</table></div>') % (
            htmlmod.escape(title), tiles, head1, head2, "".join(body))


def footer_lines(rep: Dict[str, Any]) -> List[str]:
    lines = []  # type: List[str]
    rd = rep["report_day"]
    if rep["partial"]:
        lines.append("%s is a partial day: deals synced to %s IST. Daily figures are as of the sync; daily chips are withheld." % (
            long_(rd), ", ".join("%s %s" % (k, v or "never") for k, v in sorted(rep["syncs"].items()))))
    if rep["gaps"]:
        lines.append("No data (sync gap, not zero) on %s. These days are left out of every window and chip." % ", ".join(long_(d) for d in rep["gaps"]))
    if rep["weekends"]:
        lines.append("Weekend days with no events, shown as weekend (a real zero): %s." % ", ".join(long_(d) for d in rep["weekends"]))
    lines.append("Inception = distinct deals that ever reached the row, all time to the report day. Rolling 7 / prev 7 = sum of daily "
                 "distinct deals over the window, so a deal that enters a row on two days counts on both.")
    lines.append("Leads engaged = distinct deals with at least one human stage move in the funnel on the day; 7-day = distinct deals over the window.")
    lines.append("Entry row counts every source; all other rows count human (CRM_UI) moves only. Cluster 2 cold call inception counts only leads that entered cold call on or after 2026-09-16, the day after the 2026-09-15 bulk migration; other Cluster 2 rows keep their full history, including deals that moved on from before the cutoff.")
    return lines


def render_html(rep: Dict[str, Any]) -> str:
    rd = rep["report_day"]
    days7, prev7 = rep["days7"], rep["prev7"]
    sections = "".join(section_html(s, rd, days7, prev7) for s in rep["sections"])
    notes = ""  # the approved mail has no footer notes
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>%(title)s</title></head>'
        '<body style="margin:0;background:#ffffff;color:#1c2130;font-family:Arial,Helvetica,sans-serif;">'
        '<div style="max-width:760px;margin:0 auto;padding:16px;">'
        '<h1 style="font-size:22px;margin:0 0 6px;">LH2 Holdings Inc. Mail - %(subject)s</h1>'
        '<p style="color:#6b7280;font-size:13px;margin:0 0 4px;">To: bhanu.enamala@lh2.ai</p>'
        '<p style="color:#6b7280;font-size:13px;margin:0 0 4px;">From: LH2 Holdings Inc. Mail</p>'
        '%(sections)s'
        '<div style="margin-top:28px;border-top:1px solid #e2e4e8;padding-top:12px;color:#6b7280;font-size:12px;">'
        '<ul style="padding-left:18px;margin:0;">%(notes)s</ul></div>'
        '</div></body></html>') % {"title": htmlmod.escape(rep["subject"]), "subject": htmlmod.escape(rep["subject"]),
                                   "sections": sections, "notes": notes}


def render_text(rep: Dict[str, Any]) -> str:
    rd = rep["report_day"]
    out = ["LH2 Holdings Inc. Mail - %s" % rep["subject"], "To: bhanu.enamala@lh2.ai", "From: LH2 Holdings Inc. Mail", ""]
    for s in rep["sections"]:
        out.append("%s %s %s" % (s["title"], EM, long_(rd)))
        out.append("%s: %s (%s)" % (s["tile_label"], fmt(s["to_date"]), week_sub(s["week_delta"])))
        out.append("7-DAY LEADS ENGAGED: %s%s (%s, prev 7: %s)" % (fmt(s["eng7"]), window_note(s["eng7_cov"], 7), s["chip_eng7"]["text"],
                                                                   fmt(s["eng7_prev"])))
        out.append("DAILY LEADS ENGAGED: %s (%s, yesterday: %s)" % (fmt(s["eng_today"]), s["chip_eng_day"]["text"], fmt(s["eng_yest"])))
        out.append("")
        out.append("%-30s %10s %10s %10s %12s %8s %8s %12s" % ("Stage", "Inception", "Roll7", "Prev7", "Change", "Today", "Yest", "Change"))
        for r in s["rows"]:
            out.append("%-30s %10s %10s %10s %12s %8s %8s %12s" % (
                r["name"][:30], fmt(r["inception"]), fmt(r["roll7"]), fmt(r["prev7"]), r["chip7"]["text"], fmt(r["today"]), fmt(r["yest"]),
                r["chip_day"]["text"]))
        out.append("")
    out.append("Notes:")
    out.extend("- " + x for x in footer_lines(rep))
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------------------------------------- CLI / mail
def summary_lines(rep: Dict[str, Any]) -> List[str]:
    out = ["Subject: %s" % rep["subject"]]
    for s in rep["sections"]:
        out.append("%s  |  %s %s  |  7-DAY %s  |  DAILY %s" % (
            s["title"] + " " + EM + " " + long_(rep["report_day"]), s["tile_label"], fmt(s["to_date"]), fmt(s["eng7"]), fmt(s["eng_today"])))
    return out


def send_report(rep: Dict[str, Any], text: str, html_body: str, cfg: Dict[str, Any], to: Optional[str]) -> Dict[str, Any]:
    """The only mailing path.  Token: companyops_gmail_send_token.json, checked offline (scope + refresh token) before use."""
    recipients = mailer.resolve_recipients(cfg, to)
    st = auth.token_status(TOKEN_FILE, cfg["mail"]["scope"])
    if not st.get("ok"):
        raise mailer.MailError("token %s unusable: %s" % (TOKEN_FILE, st.get("problem", "unusable")))
    msg = mailer.build_message(rep["subject"], recipients, text, html_body)
    res = mailer.send_raw(mailer.encode_raw(msg), cfg, token_file=TOKEN_FILE)
    res["to"] = recipients
    return res


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m leadgen.reports.vertical_mail", description=TITLE + " (dry-run unless --send).")
    ap.add_argument("--date", help="IST report day YYYY-MM-DD (default: today in IST)")
    ap.add_argument("--db", default=DEFAULT_DB, help="database path (opened read-only)")
    ap.add_argument("--out-dir", default=DEFAULT_OUT, help="output root (default reports/vertical)")
    ap.add_argument("--send", action="store_true", help="mail the report (the ONLY path that mails). Default is dry-run")
    ap.add_argument("--assume-complete", action="store_true", help="treat the report day as finished (shows daily percentage changes); use only when the owner says the day is over")
    ap.add_argument("--to", help="recipient(s), comma separated. Default: config/report.yaml mail.default_to")
    args = ap.parse_args(argv)
    day = args.date or (datetime.datetime.utcnow() + IST_OFFSET).date().isoformat()
    parse_day(day)
    con = sqlite3.connect("file:%s?mode=ro" % args.db, uri=True)
    con.row_factory = sqlite3.Row
    try:
        rep = build_report(con, day, assume_complete=args.assume_complete)
    finally:
        con.close()
    html_body, text = render_html(rep), render_text(rep)
    out_dir = os.path.join(args.out_dir, day)
    os.makedirs(out_dir, exist_ok=True)
    html_path = os.path.join(out_dir, "index.html")
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(html_body)
    # day snapshot, same role as the other repos' daily snapshot files: every row's numbers for the day, so later reports read the saved day
    snap = {"report_day": day, "taken_at_utc": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"), "partial": rep["partial"],
            "syncs": rep["syncs"], "sections": [{"key": s["key"], "title": s["title"], "to_date": s["to_date"], "week_delta": s["week_delta"],
            "engaged_7": s["eng7"], "engaged_today": s["eng_today"], "engaged_yesterday": s["eng_yest"],
            "rows": [{"name": r["name"], "inception": r["inception"], "roll7": r["roll7"], "prev7": r["prev7"], "today": r["today"], "yesterday": r["yest"]} for r in s["rows"]]}
            for s in rep["sections"]]}
    with open(os.path.join(out_dir, "snapshot.json"), "w", encoding="utf-8") as fh:
        json.dump(snap, fh, indent=1, ensure_ascii=False, default=str)
    with open(os.path.join(out_dir, "index.txt"), "w", encoding="utf-8") as fh:
        fh.write(text)
    print(html_path)
    for line in summary_lines(rep):
        print(line)
    if not args.send:
        print("Dry run: nothing sent. Re-run with --send to mail this report.")
        return 0
    cfg = lcfg.load_yaml("report")
    res = send_report(rep, text, html_body, cfg, args.to)
    print("Sent via %s to %s -> message id %s" % (TOKEN_FILE, ", ".join(res["to"]), res.get("message_id")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
