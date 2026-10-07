"""Render the report model as self-contained HTML (inline CSS + inline SVG, no external assets), Markdown, CSV and JSON.

Pure functions of the model (``build.build_model``): no I/O, no wall-clock, so the outputs are byte-identical for identical models.
HTML is written with f-strings and ``html.escape`` (no template engine).  No emojis.  Python 3.9 compatible.
"""
import csv
import html
import io
from typing import Any, Dict, List, Optional, Sequence

from leadgen.reports.build import model_json
from leadgen.reports.metrics import ist_hhmm_of

E = html.escape


# ------------------------------------------------------------------------------------------------------ formatting
def fmt(n: Any) -> str:
    if n is None:
        return "-"
    if isinstance(n, float):
        if n != n:
            return "-"
        return "{:,.1f}".format(n)
    if isinstance(n, int):
        return "{:,}".format(n)
    return str(n)


def sgn(n: Optional[int]) -> str:
    if n is None:
        return "-"
    return ("+" if n > 0 else "") + "{:,}".format(n)


def delta(ch: Optional[Dict[str, Any]]) -> str:
    """'+12 (+8.1%)' / 'new' / 'flat' / '+3 (partial window)'"""
    if not ch:
        return "-"
    f = ch["flag"]
    if f == "flat":
        return "flat"
    if f == "new":
        return "new (%s)" % sgn(ch["abs"])
    if f == "pct":
        return "%s (%s%.1f%%)" % (sgn(ch["abs"]), "+" if ch["pct"] > 0 else "", ch["pct"])
    if f == "partial":
        return "%s (partial window)" % sgn(ch["abs"])
    return sgn(ch["abs"])


# ---------------------------------------------------------------------------------------------------------- CSS
CSS = """
:root{color-scheme:light dark;--bg:#f6f6f4;--surface:#fcfcfb;--surface-2:#f0efec;--ink:#0b0b0b;--ink-2:#52514e;--ink-3:#6b6a66;--line:#dcdbd6;--accent:#2a78d6;--series-1:#2a78d6;--warn:#8a5a00;--warn-bg:#fbf1da;--bad:#a12f2e;--bad-bg:#fbe4e3;--ok:#1d6b3a;--ok-bg:#e3f3ea;--shadow:0 1px 2px rgba(0,0,0,.05)}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#121211;--surface:#1a1a19;--surface-2:#242422;--ink:#ffffff;--ink-2:#c3c2b7;--ink-3:#a2a198;--line:#3a3a37;--accent:#3987e5;--series-1:#3987e5;--warn:#f0c46b;--warn-bg:#3a2e12;--bad:#f09a98;--bad-bg:#3d1b1a;--ok:#8fd4a6;--ok-bg:#16301f;--shadow:none}}
:root[data-theme="dark"]{color-scheme:dark;--bg:#121211;--surface:#1a1a19;--surface-2:#242422;--ink:#ffffff;--ink-2:#c3c2b7;--ink-3:#a2a198;--line:#3a3a37;--accent:#3987e5;--series-1:#3987e5;--warn:#f0c46b;--warn-bg:#3a2e12;--bad:#f09a98;--bad-bg:#3d1b1a;--ok:#8fd4a6;--ok-bg:#16301f;--shadow:none}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:0 16px 48px}
header.top{padding:28px 0 12px}
h1{font-size:24px;line-height:1.25;margin:0 0 4px;font-weight:650;letter-spacing:-.01em}
h2{font-size:18px;margin:32px 0 10px;font-weight:650;border-bottom:1px solid var(--line);padding-bottom:6px}
h3{font-size:15px;margin:22px 0 8px;font-weight:650}
.sub{color:var(--ink-2);margin:0}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0 0}
.chip{display:inline-block;border:1px solid var(--line);background:var(--surface);color:var(--ink-2);border-radius:999px;padding:2px 10px;font-size:12.5px}
.chip.warn{background:var(--warn-bg);color:var(--warn);border-color:transparent}
.chip.bad{background:var(--bad-bg);color:var(--bad);border-color:transparent}
.chip.ok{background:var(--ok-bg);color:var(--ok);border-color:transparent}
nav.toc{display:flex;flex-wrap:wrap;gap:4px 14px;margin:14px 0 0;font-size:13.5px}
nav.toc a{color:var(--accent);text-decoration:none}
nav.toc a:hover{text-decoration:underline}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:10px;margin:14px 0 4px}
.tile{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:12px 14px;box-shadow:var(--shadow);min-width:0}
.tile .k{font-size:12.5px;color:var(--ink-2);margin:0}
.tile .v{font-size:28px;line-height:1.15;font-weight:650;font-variant-numeric:tabular-nums;margin:2px 0}
.tile .s{font-size:12.5px;color:var(--ink-2);font-variant-numeric:tabular-nums;margin:0}
.spark{display:block;margin-top:6px}
.spark .ln{fill:none;stroke:var(--series-1);stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
.spark .dot{fill:var(--series-1);stroke:var(--surface);stroke-width:2}
.spark .base{stroke:var(--line);stroke-width:1}
.note{font-size:13px;color:var(--ink-2);margin:6px 0}
.callout{border-left:3px solid var(--line);background:var(--surface);padding:8px 12px;margin:10px 0;font-size:13.5px;color:var(--ink-2);border-radius:0 6px 6px 0}
.callout.warn{border-color:var(--warn);background:var(--warn-bg);color:var(--ink)}
.callout.bad{border-color:var(--bad);background:var(--bad-bg);color:var(--ink)}
.tw{overflow:auto;max-height:78vh;background:var(--surface);border:1px solid var(--line);border-radius:10px;margin:8px 0 14px;box-shadow:var(--shadow)}
table{border-collapse:separate;border-spacing:0;width:100%;font-size:13.5px}
caption{text-align:left;padding:8px 12px;font-weight:600;color:var(--ink-2);font-size:13px}
th,td{padding:5px 10px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
th:first-child,td:first-child,th.l,td.l{text-align:left}
td.wrapc{white-space:normal;min-width:200px}
thead th{position:sticky;top:0;background:var(--surface-2);z-index:2;font-weight:600;color:var(--ink-2);font-size:12.5px;vertical-align:bottom}
thead th small{display:block;font-weight:400;color:var(--ink-3);font-size:11px}
tbody th{font-weight:500;text-align:left;position:sticky;left:0;background:var(--surface);z-index:1}
tbody tr:last-child td,tbody tr:last-child th{border-bottom:0}
tr.sub td,tr.sub th{color:var(--ink-2);font-size:13px}
tr.sub th{padding-left:26px;font-weight:400}
tr.grp td,tr.grp th{background:var(--surface-2);font-weight:600;color:var(--ink-2)}
tr.hl td,tr.hl th{font-weight:650}
tr.flag td,tr.flag th{color:var(--warn)}
td.muted,.muted{color:var(--ink-3)}
td.num-strong{font-weight:650}
details{margin:8px 0}
summary{cursor:pointer;color:var(--accent);font-size:13.5px}
.cols2{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px}
.sm{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px;margin:8px 0}
dl.gl{display:grid;grid-template-columns:minmax(150px,230px) 1fr;gap:6px 14px;font-size:13.5px}
dl.gl dt{font-weight:600}
dl.gl dd{margin:0;color:var(--ink-2)}
footer{margin-top:36px;color:var(--ink-3);font-size:12.5px;border-top:1px solid var(--line);padding-top:12px}
.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px}
@media (max-width:640px){h1{font-size:21px}.tile .v{font-size:24px}dl.gl{grid-template-columns:1fr}}
@media print{body{background:#fff;color:#000}.tw{max-height:none;overflow:visible}thead th{position:static}}
"""


# --------------------------------------------------------------------------------------------------------- pieces
def spark(values: Sequence[int], label: str, w: int = 150, h: int = 34) -> str:
    vals = [int(v) for v in values]
    if not vals:
        return ""
    mx = max(vals) or 1
    n = len(vals)
    pad = 4
    xs = [pad + i * (w - 2 * pad) / max(n - 1, 1) for i in range(n)]
    ys = [h - pad - (v * (h - 2 * pad) / mx) for v in vals]
    pts = " ".join("%.1f,%.1f" % (x, y) for x, y in zip(xs, ys))
    desc = "%s, last %d days: %s" % (label, n, ", ".join(str(v) for v in vals))
    return ('<svg class="spark" viewBox="0 0 %d %d" width="%d" height="%d" role="img" aria-label="%s"><title>%s</title>'
            '<line class="base" x1="%d" y1="%d" x2="%d" y2="%d"/><polyline class="ln" points="%s"/><circle class="dot" cx="%.1f" cy="%.1f" r="3.2"/></svg>'
            % (w, h, w, h, E(desc), E(desc), pad, h - pad, w - pad, h - pad, pts, xs[-1], ys[-1]))


def tile(k: str, v: Any, s: str = "", sp: str = "") -> str:
    return '<div class="tile"><p class="k">%s</p><p class="v">%s</p><p class="s">%s</p>%s</div>' % (E(k), E(fmt(v)), E(s), sp)


def _th(label: str, unit: str = "") -> str:
    return '<th scope="col">%s%s</th>' % (E(label), ("<small>%s</small>" % E(unit)) if unit else "")


def _ladder_header(first: str, with_scope: bool, scope_label: str = "") -> str:
    h = ['<th scope="col" class="l">%s</th>' % E(first), _th("Now", "deals"), _th("Today", "entries"), _th("Yest.", "entries"), _th("Change", "day"), _th("Roll7", "entries"),
         _th("Prev7", "entries"), _th("Change", "7d vs 7d"), _th("Inception", "deals, all time"), _th("Today auto", "not in headline" )]
    if with_scope:
        h.append(_th("Since cohort start", scope_label or "secondary"))
    return "<thead><tr>%s</tr></thead>" % "".join(h)


def _ladder_row(r: Dict[str, Any], with_scope: bool, cls: str = "", label: Optional[str] = None, th_note: str = "") -> str:
    basis_note = ""
    t = r["today"]
    cells = [
        '<th scope="row">%s%s</th>' % (E(label if label is not None else r["label"]), th_note),
        "<td>%s</td>" % fmt(r.get("now")),
        '<td class="num-strong">%s</td>' % fmt(t["headline"]),
        "<td>%s</td>" % fmt(r["yesterday"]["headline"]),
        '<td class="muted">%s</td>' % E(delta(r.get("change_today"))),
        '<td class="num-strong">%s</td>' % fmt(r["roll7"]["headline"]),
        "<td>%s</td>" % fmt(r["prev7"]["headline"]),
        '<td class="muted">%s</td>' % E(delta(r.get("change_roll7"))),
        "<td>%s</td>" % fmt(r["inception"]["deals_all"]),
        '<td class="muted">%s</td>' % (fmt(t["auto"]) if r.get("basis") == "human" else "-"),
    ]
    if with_scope:
        sc = r.get("inception_in_scope")
        cells.append('<td class="muted">%s</td>' % (fmt(sc["deals_all"]) if sc else "-"))
    return '<tr%s>%s</tr>' % (' class="%s"' % cls if cls else "", "".join(cells)) + basis_note


def _kind_cls(kind: str) -> str:
    return {"dead": "grp", "unmapped": "flag", "derived": "", "won": "hl"}.get(kind, "")


def ladder_table(rungs: List[Dict[str, Any]], dead: List[Dict[str, Any]], caption: str, with_scope: bool = False, scope_label: str = "") -> str:
    out = ['<div class="tw" tabindex="0"><table><caption>%s</caption>%s<tbody>' % (E(caption), _ladder_header("Canonical stage", with_scope, scope_label))]
    for r in rungs:
        tag = ""
        if r["kind"] == "derived":
            tag = ' <span class="muted">(derived)</span>'
        elif r["kind"] == "entry":
            tag = ' <span class="muted">(any source)</span>'
        label = "%s  %s" % (r["rank"], r["label"]) if r["rank"] not in ("?",) else r["label"]
        out.append(_ladder_row(r, with_scope, _kind_cls(r["kind"]), label, tag))
        if r["code"] == "DEAD":
            for d in dead:
                out.append(_ladder_row(d, with_scope, "sub"))
    out.append("</tbody></table></div>")
    return "".join(out)


def native_table(rows: List[Dict[str, Any]], caption: str, with_scope: bool, scope_label: str) -> str:
    out = ['<div class="tw" tabindex="0"><table><caption>%s</caption>%s<tbody>' % (E(caption), _ladder_header("Native stage", with_scope, scope_label))]
    last = None
    for r in rows:
        grp = "Dead" if r["is_dead"] else ("Won" if r["is_won"] else "Live")
        if not r["is_mapped"]:
            grp = "Unmapped"
        if grp != last:
            out.append('<tr class="grp"><td colspan="%d" class="l">%s stages</td></tr>' % (10 + (1 if with_scope else 0), grp))
            last = grp
        tag = ""
        if r["sub_row"]:
            tag = ' <span class="muted">(sub-row of Negotiation / LOI)</span>'
        cls = "flag" if not r["is_mapped"] else ""
        rr = dict(r)
        rr["basis"] = r["basis"]
        out.append(_ladder_row(rr, with_scope, cls, r["label"], tag + (' <span class="muted">[%s]</span>' % E(r["canonical"]) if r["is_mapped"] else "")))
    out.append("</tbody></table></div>")
    return "".join(out)


def owners_table(o: Dict[str, Any], caption: str) -> str:
    rows = o["rows"]
    codes = sorted({c for r in rows for c in r["now_by_code"]})
    head = ('<thead><tr><th scope="col" class="l">Owner</th>%s</tr></thead>' % "".join([
        _th("Human moves today", "deal+stage"), _th("Engaged today", "deals"), _th("Engaged Roll7", "deals, union"), _th("Net new today", "deals"), _th("Assigned today", "owner changes"),
        _th("Deals now", "live+dead+won"), _th("Live", "deals"), _th("Dead", "deals"), _th("Won", "deals")]))
    out = ['<div class="tw" tabindex="0"><table><caption>%s</caption>%s<tbody>' % (E(caption), head)]
    for r in rows:
        nm = r["name"] + (" (archived)" if r["archived"] else "")
        cls = "" if r["listed_rep"] else "sub"
        out.append('<tr%s><th scope="row">%s</th><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
            ' class="%s"' % cls if cls else "", E(nm), fmt(r["moves_today"]), fmt(r["engaged_today"]), fmt(r["engaged_roll7"]), fmt(r["net_new_today"]), fmt(r["assigned_today"]),
            fmt(r["now_total"]), fmt(r["now_live"]), fmt(r["now_dead"]), fmt(r["now_won"])))
    out.append("</tbody></table></div>")
    # stage mix of current deals
    if codes:
        head2 = '<thead><tr><th scope="col" class="l">Current deals by canonical stage</th>%s</tr></thead>' % "".join(_th(c) for c in codes)
        body = "".join('<tr><th scope="row">%s</th>%s</tr>' % (E(r["name"]), "".join("<td>%s</td>" % fmt(r["now_by_code"].get(c, 0)) for c in codes)) for r in rows if r["now_total"])
        out.append('<details><summary>Current deals by canonical stage, per owner</summary><div class="tw" tabindex="0"><table>%s<tbody>%s</tbody></table></div></details>' % (head2, body))
    notes = []
    if o.get("missing_reps"):
        notes.append("Configured reps not found in the owner table: %s." % ", ".join(o["missing_reps"]))
    notes.append("Deals-now owner basis: %s. Rows in lighter text are owners not on the configured rep list (shown because they moved a deal in the last 7 days or own live deals)." % o["owner_as_of"])
    out.append('<p class="note">%s</p>' % E(" ".join(notes)))
    return "".join(out)


# ------------------------------------------------------------------------------------------------------ html page
def _anchor(s: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in s.lower())


def render_html(m: Dict[str, Any]) -> str:
    meta, hd = m["meta"], m["headline"]
    D = meta["report_day"]
    parts = []  # type: List[str]
    a = parts.append
    cov7 = meta["coverage"]["roll7"]
    partial = cov7["covered"] < cov7["days"] or meta["coverage"]["prev7"]["covered"] < meta["coverage"]["prev7"]["days"]
    integ = m["integrity"]
    n_unm = len(integ["unmapped_stages"])
    a("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">")
    a("<meta name=\"color-scheme\" content=\"light dark\"><title>LH2 Funnel Daily Report %s</title><style>%s</style></head><body><div class=\"wrap\">" % (E(D), CSS))
    a("<header class=\"top\"><h1>LH2 Funnel Daily Report</h1><p class=\"sub\">%s, %s (IST day). All five funnels, three HubSpot portals, rebuilt from the stage-event store.</p>" % (E(meta["weekday"]), E(D)))
    chips = ['<span class="chip %s">%d of %d funnels reporting</span>' % ("ok" if hd["funnels_reporting"]["n"] == hd["funnels_reporting"]["of"] else "warn", hd["funnels_reporting"]["n"], hd["funnels_reporting"]["of"]),
             '<span class="chip %s">Roll7 window: %s</span>' % ("warn" if partial else "ok", E(meta["coverage"]["roll7_label"])),
             '<span class="chip">Prev7 window: %s</span>' % E(meta["coverage"]["prev7_label"]),
             '<span class="chip">Event store: %s to %s, %s events</span>' % (E(meta["store"]["first_event_day"] or "-"), E(meta["store"]["last_event_day"] or "-"), fmt(meta["store"]["events"])),
             '<span class="chip">Metrics v%s</span>' % E(meta["metrics_version"])]
    if n_unm:
        chips.append('<span class="chip bad">%d unmapped stage ids</span>' % n_unm)
    a('<div class="chips">%s</div>' % "".join(chips))
    a('<nav class="toc" aria-label="Sections"><a href="#headline">Headline</a><a href="#combined">Combined ladder</a>%s<a href="#coldlead">CoOps Cold Lead by country</a><a href="#integrity">Data freshness and integrity</a><a href="#crosscheck">Cross-check vs legacy</a><a href="#glossary">Definitions</a></nav>' %
      "".join('<a href="#f-%s">%s</a>' % (E(f["slug"]), E(f["name"])) for f in m["funnels"]))
    a("</header>")

    # ---- headline tiles
    tr = m["trends"]["ALL"]
    la, en = hd["leads_assigned"], hd["leads_engaged"]
    a('<section id="headline"><h2>Headline, all five funnels</h2><div class="tiles">')
    a(tile("Leads assigned today (entry stage, any source)", la["today"], "Yest. %s | Roll7 %s | Prev7 %s | %s" % (fmt(la["yesterday"]), fmt(la["roll7"]), fmt(la["prev7"]), delta(la["change_roll7"])),
           spark(tr["leads_assigned"], "Leads assigned per day")))
    a(tile("Leads engaged today (distinct deals, human moves only)", en["today"], "Yest. %s | 7-day union %s (%s) | Prev7 %s | %s" % (fmt(en["yesterday"]), fmt(en["roll7_union"]), en["roll7_label"], fmt(en["prev7_union"]), delta(en["change_roll7"])),
           spark(tr["engaged"], "Leads engaged per day")))
    a(tile("Won to date (distinct deals ever Closed/Won)", hd["won"]["to_date"], "Currently in Closed/Won: %s | entered in last 7 days: %s" % (fmt(hd["won"]["now"]), fmt(hd["won"]["this_week_entries"]))))
    a(tile("Contracts signed, last 7 days", hd["contracts_signed_7d"], "Human entries into Contract signed (Roll7)"))
    o = hd["occupancy"]
    a(tile("Deals live in the funnels now", o["live"], "Dead %s | Won %s | Total %s | Unmapped %s" % (fmt(o["dead"]), fmt(o["won"]), fmt(o["total"]), fmt(o["unmapped"]))))
    a(tile("New deals in the funnels today", la["new_deals"]["today"], "Distinct deals whose first-ever stage entry is today | Yest. %s | Roll7 %s" % (fmt(la["new_deals"]["yesterday"]), fmt(la["new_deals"]["roll7"]))))
    a("</div>")
    if meta["events_on_day"] == 0:
        a('<div class="callout warn">No stage events at all on %s (%s). Today / Yesterday columns are therefore zero; the most recent day with events is %s. Roll7 / Prev7 and Inception are unaffected.</div>' % (E(D), E(meta["weekday"]), E(meta["last_event_day_le_report_day"] or "-")))
    a('<p class="note">Today = %s (IST). Engaged = a CRM_UI (human) stage move; API, integration and bulk pushes never count. Leads assigned counts any source. Roll7 = sum of the 7 daily values; the 7-day engaged figure is the union of distinct deals.</p>' % E(D))
    # per funnel strip
    head = "<thead><tr><th scope=\"col\" class=\"l\">Funnel</th>%s</tr></thead>" % "".join([_th("Leads assigned today", "entries"), _th("Leads assigned Roll7", "entries"), _th("Engaged today", "deals"), _th("Engaged 7d", "deals, union"),
                                                                                         _th("Live now", "deals"), _th("Dead now", "deals"), _th("Won now", "deals"), _th("Won to date", "deals"), _th("All deals now", "deals")])
    rows = "".join('<tr><th scope="row"><a href="#f-%s">%s</a></th>%s</tr>' % (E(f["slug"]), E(f["name"]), "".join("<td>%s</td>" % fmt(f[k]) for k in ("leads_today", "leads_roll7", "engaged_today", "engaged_roll7", "live", "dead", "won_now", "won_to_date", "total_now")))
                   for f in hd["per_funnel"])
    a('<div class="tw" tabindex="0"><table><caption>Headline per funnel</caption>%s<tbody>%s</tbody></table></div></section>' % (head, rows))

    # ---- combined ladder
    a('<section id="combined"><h2>Combined funnel on the canonical ladder</h2>')
    a('<p class="note">%s &quot;Inception&quot; is distinct deals that ever entered the stage, all time (never re-seeded from a cutoff); entries and deals are different units and are labelled in every header.</p>' % E(meta["headline_basis"]))
    a(ladder_table(m["combined"]["rungs"], m["combined"]["dead_reasons"], "All five funnels combined: entries by canonical stage", False))
    funnel_names = [(f["slug"], f["name"]) for f in m["funnels"]]
    head = '<thead><tr><th scope="col" class="l">Canonical stage</th>%s</tr></thead>' % "".join('<th scope="col">%s<small>today / now</small></th>' % E(n) for _, n in funnel_names)
    body = []
    for r in m["combined"]["matrix"]:
        if r["code"] in ("DEAD",):
            pass
        cells = []
        for slug, _ in funnel_names:
            c = r["cells"].get(slug)
            cells.append("<td>%s / %s</td>" % (fmt(c["today"]), fmt(c["now"])) if c else "<td>-</td>")
        body.append('<tr><th scope="row">%s</th>%s</tr>' % (E(r["label"]), "".join(cells)))
    a('<h3>The same ladder by funnel</h3><div class="tw" tabindex="0"><table><caption>Today (headline entries) / now (deals sitting in the stage), per funnel</caption>%s<tbody>%s</tbody></table></div>' % (head, "".join(body)))
    a("</section>")

    # ---- per funnel
    for f in m["funnels"]:
        eg = f["engaged"]
        la_f = f["leads_assigned"]
        oc = f["occupancy"]
        has_scope = bool(f["cohort"])
        a('<section id="f-%s"><h2>%s <span class="muted" style="font-weight:400;font-size:14px">portal %s, pipeline %s</span></h2>' % (E(f["slug"]), E(f["name"]), E(f["portal"]), E(f["pipeline_id"])))
        a('<div class="tiles">')
        tf = f["trend"]
        a(tile("Deals now", oc["total"], "Live %s | Dead %s | Won %s%s" % (fmt(oc["live"]), fmt(oc["dead"]), fmt(oc["won"]), (" | Unmapped %s" % fmt(oc["unmapped"])) if oc["unmapped"] else "")))
        a(tile("Leads assigned today", la_f["today"]["headline"], "Roll7 %s (%s) | Prev7 %s" % (fmt(la_f["roll7"]["headline"]), f["coverage"]["roll7_label"], fmt(la_f["prev7"]["headline"])), spark(tf["leads_assigned"], f["name"] + " leads assigned per day")))
        a(tile("Leads engaged today", eg["today"], "7-day union %s | net new today %s" % (fmt(eg["roll7_union"]), fmt(eg["net_new_today"])), spark(tf["engaged"], f["name"] + " leads engaged per day")))
        a(tile("Won to date", f["won_to_date"]["deals_all"], "Distinct deals ever Closed/Won"))
        a("</div>")
        if f["cohort"]:
            a('<div class="callout">Cohort rule on this pipeline: it was bulk-populated on %s. The headline Inception above is ALL-TIME; the last column is a clearly labelled secondary figure (deals that joined on/after %s, excluding deals created before %s that joined on it). It is not a replacement for Inception.</div>' % (
                E(f["cohort"]["cohort_exclude_migration_on"] or f["cohort"]["cohort_start"]), E(f["cohort"]["cohort_start"]), E(f["cohort"]["cohort_exclude_migration_on"] or f["cohort"]["cohort_start"])))
        a(ladder_table(f["rungs"], f["dead_reasons"], "%s: canonical stages" % f["name"], has_scope, "since %s, excl. bulk migration" % (f["cohort"] or {}).get("cohort_start", "")))
        a(native_table(f["native"], "%s: native stages (HubSpot stage names)" % f["name"], has_scope, "since %s, excl. bulk migration" % (f["cohort"] or {}).get("cohort_start", "")))
        a(owners_table(f["owners"], "%s: per-owner activity (today's human moves; current deals)" % f["name"]))
        a("</section>")

    # ---- cold lead
    cl = m["cold_lead_country"]
    a('<section id="coldlead"><h2>CoOps (Global): Cold Lead by country</h2>')
    a('<p class="note">Live deals sitting in the entry stage (Cold Lead), %s. Country is derived from the lead_source label prefix (CoOps has no Country property): UK_*, *_US / US_EST* / Proptech US, Australia*, Singapore*, Georgia. Total %s.</p>' % (E(cl["as_of"]), fmt(cl["total"])))
    head = '<thead><tr><th scope="col" class="l">Country</th>%s</tr></thead>' % "".join([_th("Cold Lead deals", "deals"), _th("Share", "%")])
    tot = cl["total"] or 1
    body = "".join('<tr><th scope="row">%s</th><td>%s</td><td>%.1f%%</td></tr>' % (E(c), fmt(n), n * 100.0 / tot) for c, n in cl["by_country"].items())
    body += '<tr class="hl"><th scope="row">Total</th><td>%s</td><td>100.0%%</td></tr>' % fmt(cl["total"])
    a('<div class="cols2"><div class="tw" tabindex="0"><table><caption>By country</caption>%s<tbody>%s</tbody></table></div>' % (head, body))
    head2 = '<thead><tr><th scope="col" class="l">Lead source</th><th scope="col" class="l">Country</th>%s</tr></thead>' % _th("Deals", "deals")
    body2 = "".join('<tr><th scope="row">%s</th><td class="l">%s</td><td>%s</td></tr>' % (E(r["lead_source"]), E(r["country"]), fmt(r["deals"])) for r in cl["by_lead_source"])
    a('<div class="tw" tabindex="0"><table><caption>By lead source</caption>%s<tbody>%s</tbody></table></div></div>' % (head2, body2))
    a("</section>")

    # ---- integrity
    a('<section id="integrity"><h2>Data freshness and integrity</h2>')
    head = '<thead><tr><th scope="col" class="l">Account</th>%s</tr></thead>' % "".join([_th("Last deals sync", "IST"), _th("Status"), _th("Rows in last sync"), _th("Newest event in store", "IST"), _th("Report day complete in data?")])
    body = "".join('<tr><th scope="row">%s</th><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
        E(f["account"]), E(f["last_success_ist"] or "never"), E(f["status"] or "-"), fmt(f["rows_synced"]), E(ist_hhmm_of(integ["newest_event"][f["account"]]) if integ["newest_event"].get(f["account"]) else "-"),
        "yes" if f["day_complete"] else "no (data through %s IST; the day is not closed)" % (f["last_success_ist"] or "-")[11:]) for f in integ["freshness"])
    a('<div class="tw" tabindex="0"><table><caption>Last successful HubSpot sync per account</caption>%s<tbody>%s</tbody></table></div>' % (head, body))
    store = integ["store"]
    nde = integ["days_without_events"]
    a('<p class="note">Event store covers %s to %s (%s events). Days without any stage event in the last %d days: %s. Weekend days are expected to be quiet.</p>' % (
        E(store["first_event_day"] or "-"), E(store["last_event_day"] or "-"), fmt(store["events"]), integ["lookback_days"], E(", ".join("%s %s%s" % (d["weekday"], d["day"], " (weekend)" if d["weekend"] else "") for d in nde) or "none")))
    if integ["unmapped_stages"]:
        head = '<thead><tr><th scope="col" class="l">Stage id</th>%s</tr></thead>' % "".join([_th("Account", ""), _th("Pipeline"), _th("Label held"), _th("Deals now"), _th("Events")])
        body = "".join('<tr class="flag"><th scope="row">%s</th><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (E(u["stage_id"]), E(u["account_id"]), E(u["pipeline_id"]), E(u["stage_label"]), fmt(u["deals_now"]), fmt(u["events"])) for u in integ["unmapped_stages"])
        a('<div class="callout bad">%d unmapped stage ids (no stage_map row). Their events are NOT dropped: they show in the combined table under UNMAPPED. A human has to name them (docs/review/schema-decisions.md, L4).</div><div class="tw" tabindex="0"><table><caption>Unmapped stages (v_unmapped_stage)</caption>%s<tbody>%s</tbody></table></div>' % (len(integ["unmapped_stages"]), head, body))
    else:
        a('<p class="note">No unmapped stages.</p>')
    head = '<thead><tr><th scope="col" class="l">Funnel</th>%s</tr></thead>' % "".join([_th("Deal rows in DB"), _th("Archived"), _th("Live (not archived)")])
    body = "".join('<tr><th scope="row">%s</th><td>%s</td><td>%s</td><td>%s</td></tr>' % (E(x["name"]), fmt(x["total_rows"]), fmt(x["archived"]), fmt(x["live"])) for x in integ["archived"])
    a('<div class="tw" tabindex="0"><table><caption>Archived deals (excluded from every occupancy count; their historical moves stay in the flow numbers)</caption>%s<tbody>%s</tbody></table></div>' % (head, body))
    bullets = []
    for ip in integ["ignored_pipelines"]:
        bullets.append("Ignored pipeline %s / %s (%s): %s deal(s), %s event(s); excluded from every report (HubSpot stock pipeline)." % (ip["account_id"], ip["pipeline_id"], ip["label"], fmt(ip["deals"]), fmt(ip["events"])))
    if integ["dq_warn_error"]:
        bullets.append("Open data-quality findings (warn/error): " + "; ".join("%s [%s] x%s" % (d["rule_code"], d["severity"], fmt(d["occurrences"])) for d in integ["dq_warn_error"]) + ".")
    if integ["dq_info"]:
        bullets.append("Other open findings (info): " + "; ".join("%s x%s" % (d["rule_code"], fmt(d["occurrences"])) for d in integ["dq_info"]) + ".")
    bullets.append("History read failures / dropped events: %s. Deal rows whose current stage disagrees with their newest event (v_deal_state_drift): %s. Near-duplicate events (v_dse_near_duplicate): %s." % (fmt(integ["history_failures"]), fmt(integ["deal_state_drift"]), fmt(integ["near_duplicate_events"])))
    if integ["pipeline_guess_events"]:
        bullets.append("Events whose pipeline was a guess (v_event_pipeline_guess): " + "; ".join("%s/%s %s x%s" % (g["account_id"], g["pipeline_id"], g["pipeline_basis"], fmt(g["events"])) for g in integ["pipeline_guess_events"]) + ".")
    else:
        bullets.append("Every event's pipeline was resolved from hard evidence (no deal_current / manual guesses).")
    if integ["amount_without_cost"]:
        bullets.append("Deals with `amount` set but no `cost` (cost is the money field; amount is never reported): " + ", ".join("%s %s" % (x["account_id"], fmt(x["deals"])) for x in integ["amount_without_cost"]) + ".")
    if integ["events_outside_reporting_pipelines"] or integ["events_unknown_stage"]:
        bullets.append("Events outside the reporting pipelines: %s; events at unknown stages: %s." % (fmt(integ["events_outside_reporting_pipelines"]), fmt(integ["events_unknown_stage"])))
    if integ["stage_map_changed_since_last_run"]:
        bullets.append("The stage map changed since the last persisted run (rpt_funnel_daily.canonical_code is denormalised): rebuild history with --days N before trusting persisted trend lines.")
    bullets.append("HubSpot holds no call objects in these funnels: dial activity is inferred from stage moves only.")
    a("<ul>%s</ul>" % "".join("<li>%s</li>" % E(b) for b in bullets))
    for an in integ["anchors"]:
        a('<div class="callout %s">Regression anchor: %s. Expected %s; actual %s. %s</div>' % ("" if an["ok"] else "bad", E(an["description"]), E(", ".join("%s %s" % (k, v) for k, v in an["expected"].items())),
                                                                                           E(", ".join("%s %s" % (k, v) for k, v in an["actual"].items())), "PASS" if an["ok"] else "DIFFERENCE"))
    a("</section>")

    # ---- cross-check
    cc = m["crosscheck"]
    a('<section id="crosscheck"><h2>Cross-check against the legacy snapshots</h2>')
    if not cc["available"]:
        a('<p class="note">%s</p>' % E(" ".join(cc["notes"]) or "No legacy snapshots available."))
    else:
        a('<p class="note">Recomputed values (event store) against the %d most recent legacy snapshot days of each series up to the report day. The legacy numbers are never used in the report and neither side is adjusted. Each difference carries the first tested legacy mechanic that reproduces the legacy value, or says it is unexplained.</p>' % 5)
        head = '<thead><tr><th scope="col" class="l">Series</th>%s</tr></thead>' % "".join([_th("Snapshot day"), _th("Rows compared"), _th("Match"), _th("Differ"), _th("Engaged: legacy / recomputed / both")])
        body = []
        agg = {}
        for s in cc["series"]:
            if s["family"] == "main_owner":
                for d in s["days"]:
                    x = agg.setdefault(d["day"], [0, 0, 0, 0])
                    x[0] += d["summary"]["rows"]; x[1] += d["summary"]["match"]; x[2] += d["summary"]["mismatch"]; x[3] += 1
                continue
            name = "%s %s%s" % (s["family"], s["pipeline_id"] or s["account"], (" owner " + s["owner_hs_id"]) if s["owner_hs_id"] else "")
            for d in s["days"]:
                e = d.get("engaged")
                flag = " (bootstrap)" if d["bootstrap"] else (" (seeded)" if d["seeded"] else "")
                body.append('<tr><th scope="row">%s</th><td>%s%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
                    E(name), E(d["day"]), E(flag), fmt(d["summary"]["rows"]), fmt(d["summary"]["match"]), fmt(d["summary"]["mismatch"]),
                    ("%s / %s / %s" % (fmt(e["legacy"]), fmt(e["mine"]), fmt(e["both"]))) if e else "-"))
        for day in sorted(agg):
            x = agg[day]
            body.append('<tr><th scope="row">main_owner (%d owner series, summed)</th><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>-</td></tr>' % (x[3], E(day), fmt(x[0]), fmt(x[1]), fmt(x[2])))
        a('<details open><summary>Per series and day (%d series; per-owner series are summed)</summary><div class="tw" tabindex="0"><table><caption>Legacy snapshot vs recomputed</caption>%s<tbody>%s</tbody></table></div></details>' % (len(cc["series"]), head, "".join(body)))
        head = '<thead><tr><th scope="col" class="l">Delta class (reason)</th>%s</tr></thead>' % _th("Rows")
        body = "".join('<tr><th scope="row" class="wrapc">%s</th><td>%s</td></tr>' % (E(k), fmt(v)) for k, v in cc["reason_counts"].items())
        a('<div class="tw" tabindex="0"><table><caption>Every difference, by class</caption>%s<tbody>%s</tbody></table></div>' % (head, body))
        # engaged only classes
        eng_rows = []
        for s in cc["series"]:
            for d in s["days"]:
                e = d.get("engaged")
                if e and (e["only_mine"] or e["only_legacy"]):
                    why = "; ".join("%s: %s" % (k, v) for k, v in list(e["only_mine_classes"].items()) + list(e["only_legacy_classes"].items()))
                    eng_rows.append('<tr><th scope="row">%s %s</th><td>%s</td><td>%s</td><td>%s</td><td class="wrapc l">%s</td></tr>' % (E(s["family"]), E(s["pipeline_id"] or s["account"]), E(d["day"]), fmt(e["only_mine"]), fmt(e["only_legacy"]), E(why)))
        if eng_rows:
            head = '<thead><tr><th scope="col" class="l">Series</th>%s<th scope="col" class="l">Why</th></tr></thead>' % "".join([_th("Day"), _th("Only recomputed", "deals"), _th("Only legacy", "deals")])
            a('<div class="tw" tabindex="0"><table><caption>Leads engaged: deal ids that differ</caption>%s<tbody>%s</tbody></table></div>' % (head, "".join(eng_rows)))
        # largest unexplained / listing of differing rows for the most recent day per series
        det = []
        for s in cc["series"]:
            if s["family"] == "main_owner":
                continue
            d = s["days"][-1]
            for r in d["rows"]:
                if r["reason"] != "match" and r["mine"] is not None and r["metric"] != "cumulative":
                    det.append('<tr><th scope="row">%s %s</th><td>%s</td><td class="l">%s</td><td class="l">%s</td><td>%s</td><td>%s</td><td>%s</td><td class="wrapc l">%s</td></tr>' % (
                        E(s["family"]), E(s["pipeline_id"] or s["account"]), E(d["day"]), E(r["metric"]), E(r["row"]), fmt(r["legacy"]), fmt(r["mine"]), sgn(int(r["delta"])), E(r["reason"])))
        if det:
            head = '<thead><tr><th scope="col" class="l">Series</th><th scope="col">Day</th><th scope="col" class="l">Metric</th><th scope="col" class="l">Row</th>%s<th scope="col" class="l">Reason</th></tr></thead>' % "".join([_th("Legacy"), _th("Recomputed"), _th("Delta")])
            a('<details><summary>Differing flow / current-state rows on the latest snapshot day of each series (%d)</summary><div class="tw" tabindex="0"><table><caption>Rows that differ</caption>%s<tbody>%s</tbody></table></div></details>' % (len(det), head, "".join(det)))
        a('<p class="note">Cumulative rows are listed in the JSON / CSV only: the legacy &quot;cumulative&quot; is a chained event count re-seeded by bootstrap days, the recomputed Inception is distinct deals ever (all time), so they differ by definition.</p>')
    a("</section>")

    # ---- glossary
    a('<section id="glossary"><h2>Definitions (generated from config/report.yaml)</h2><dl class="gl">')
    for k, v in m["definitions"].items():
        a("<dt>%s</dt><dd>%s<br><span class=\"muted\">Source: %s. Unit: %s. Window: %s. Scope: %s.</span></dd>" % (E(v["name"]), E(" ".join(str(v["definition"]).split())), E(str(v["source"])), E(str(v["unit"])), E(str(v["window"])), E(str(v["scope"]))))
    a("</dl></section>")
    a("<footer>Generated by leadgen.reports v%s from db/leadgen.sqlite. Stage map sha256 %s. IST day boundaries (UTC+05:30). Dry-run by default: this file is only mailed with --send. No data was read from HubSpot or any other external service to build it.</footer>" % (E(meta["tool_version"]), E(meta["stage_map_sha256"][:16])))
    a("</div></body></html>")
    return "".join(parts) + "\n"


# --------------------------------------------------------------------------------------------------------- markdown
def _md_cell(s: Any) -> str:
    return str(s).replace("|", "/").replace("\n", " ")


def _md_table(head: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    out = ["| " + " | ".join(head) + " |", "|" + "|".join("---" if i == 0 else "---:" for i in range(len(head))) + "|"]
    for r in rows:
        out.append("| " + " | ".join(_md_cell(c) for c in r) + " |")
    return "\n".join(out)


def _ladder_md(rungs, dead, with_scope=False) -> str:
    head = ["Stage", "Now", "Today", "Yest.", "Chg", "Roll7", "Prev7", "Chg 7d", "Inception (deals)", "Today auto"] + (["Since cohort start"] if with_scope else [])
    rows = []

    def one(r, label):
        row = [label, fmt(r.get("now")), fmt(r["today"]["headline"]), fmt(r["yesterday"]["headline"]), delta(r.get("change_today")), fmt(r["roll7"]["headline"]), fmt(r["prev7"]["headline"]),
               delta(r.get("change_roll7")), fmt(r["inception"]["deals_all"]), fmt(r["today"]["auto"]) if r.get("basis") == "human" else "-"]
        if with_scope:
            sc = r.get("inception_in_scope")
            row.append(fmt(sc["deals_all"]) if sc else "-")
        return row
    for r in rungs:
        rows.append(one(r, "%s %s" % (r["rank"], r["label"]) if "rank" in r and r.get("rank") != "?" else r["label"]))
        if r.get("code") == "DEAD":
            for d in dead:
                rows.append(one(d, "  - " + d["label"]))
    return _md_table(head, rows)


def render_md(m: Dict[str, Any]) -> str:
    meta, hd, integ = m["meta"], m["headline"], m["integrity"]
    D = meta["report_day"]
    L = []
    L.append("# LH2 Funnel Daily Report - %s (IST)" % D)
    L.append("")
    L.append("%s. Roll7 window %s, Prev7 window %s. Event store %s to %s (%s events). %d of %d funnels reporting." % (
        meta["weekday"], meta["coverage"]["roll7_label"], meta["coverage"]["prev7_label"], meta["store"]["first_event_day"], meta["store"]["last_event_day"], fmt(meta["store"]["events"]),
        hd["funnels_reporting"]["n"], hd["funnels_reporting"]["of"]))
    L.append("")
    L.append("## Headline, all five funnels")
    L.append("")
    la, en, o = hd["leads_assigned"], hd["leads_engaged"], hd["occupancy"]
    L.append(_md_table(["Metric", "Today", "Yest.", "Roll7", "Prev7", "Change (7d)"], [
        ["Leads assigned (entry stage, any source; entries)", fmt(la["today"]), fmt(la["yesterday"]), fmt(la["roll7"]), fmt(la["prev7"]), delta(la["change_roll7"])],
        ["Leads engaged (distinct deals, human moves; 7d = union, %s)" % en["roll7_label"], fmt(en["today"]), fmt(en["yesterday"]), fmt(en["roll7_union"]), fmt(en["prev7_union"]), delta(en["change_roll7"])],
        ["New deals (first-ever stage entry)", fmt(la["new_deals"]["today"]), fmt(la["new_deals"]["yesterday"]), fmt(la["new_deals"]["roll7"]), fmt(la["new_deals"]["prev7"]), "-"],
    ]))
    L.append("")
    L.append("Won to date: **%s** distinct deals (currently in Closed/Won: %s). Contracts signed, last 7 days: %s. Deals now: live %s, dead %s, won %s, unmapped %s, total %s." % (
        fmt(hd["won"]["to_date"]), fmt(hd["won"]["now"]), fmt(hd["contracts_signed_7d"]), fmt(o["live"]), fmt(o["dead"]), fmt(o["won"]), fmt(o["unmapped"]), fmt(o["total"])))
    L.append("")
    L.append(_md_table(["Funnel", "Leads today", "Leads Roll7", "Engaged today", "Engaged 7d", "Live", "Dead", "Won now", "Won to date", "All deals"],
                       [[f["name"], fmt(f["leads_today"]), fmt(f["leads_roll7"]), fmt(f["engaged_today"]), fmt(f["engaged_roll7"]), fmt(f["live"]), fmt(f["dead"]), fmt(f["won_now"]), fmt(f["won_to_date"]), fmt(f["total_now"])] for f in hd["per_funnel"]]))
    L.append("")
    L.append("## Combined funnel on the canonical ladder")
    L.append("")
    L.append(meta["headline_basis"] + " Inception = distinct deals ever (all time). Entries and deals are different units.")
    L.append("")
    L.append(_ladder_md(m["combined"]["rungs"], m["combined"]["dead_reasons"]))
    for f in m["funnels"]:
        L.append("")
        L.append("## %s (portal %s, pipeline %s)" % (f["name"], f["portal"], f["pipeline_id"]))
        L.append("")
        oc, eg = f["occupancy"], f["engaged"]
        L.append("Deals now %s (live %s, dead %s, won %s). Leads assigned today %s. Engaged today %s, 7-day union %s (%s), net new today %s. Won to date %s." % (
            fmt(oc["total"]), fmt(oc["live"]), fmt(oc["dead"]), fmt(oc["won"]), fmt(f["leads_assigned"]["today"]["headline"]), fmt(eg["today"]), fmt(eg["roll7_union"]), f["coverage"]["roll7_label"], fmt(eg["net_new_today"]), fmt(f["won_to_date"]["deals_all"])))
        L.append("")
        has_scope = bool(f["cohort"])
        if has_scope:
            L.append("Secondary column: deals that joined on/after %s excluding the %s bulk migration. Headline Inception is all-time." % (f["cohort"]["cohort_start"], f["cohort"]["cohort_exclude_migration_on"]))
            L.append("")
        L.append("### Native stages")
        L.append("")
        rows = []
        for r in f["native"]:
            row = [r["label"] + ("" if r["is_mapped"] else " [UNMAPPED]"), fmt(r["now"]), fmt(r["today"]["headline"]), fmt(r["yesterday"]["headline"]), delta(r["change_today"]), fmt(r["roll7"]["headline"]),
                   fmt(r["prev7"]["headline"]), delta(r["change_roll7"]), fmt(r["inception"]["deals_all"]), fmt(r["today"]["auto"]) if r["basis"] == "human" else "-"]
            if has_scope:
                sc = r.get("inception_in_scope"); row.append(fmt(sc["deals_all"]) if sc else "-")
            rows.append(row)
        L.append(_md_table(["Stage", "Now", "Today", "Yest.", "Chg", "Roll7", "Prev7", "Chg 7d", "Inception (deals)", "Today auto"] + (["Since cohort start"] if has_scope else []), rows))
        L.append("")
        L.append("### Dead reasons")
        L.append("")
        L.append(_md_table(["Reason", "Now", "Today", "Yest.", "Roll7", "Prev7", "Inception (deals)"],
                           [[d["label"], fmt(d["now"]), fmt(d["today"]["headline"]), fmt(d["yesterday"]["headline"]), fmt(d["roll7"]["headline"]), fmt(d["prev7"]["headline"]), fmt(d["inception"]["deals_all"])] for d in f["dead_reasons"]]))
        L.append("")
        L.append("### Owners")
        L.append("")
        L.append(_md_table(["Owner", "Moves today", "Engaged today", "Engaged Roll7", "Net new today", "Assigned today", "Deals now", "Live", "Dead", "Won"],
                           [[r["name"] + (" (archived)" if r["archived"] else ""), fmt(r["moves_today"]), fmt(r["engaged_today"]), fmt(r["engaged_roll7"]), fmt(r["net_new_today"]), fmt(r["assigned_today"]),
                             fmt(r["now_total"]), fmt(r["now_live"]), fmt(r["now_dead"]), fmt(r["now_won"])] for r in f["owners"]["rows"]]))
    cl = m["cold_lead_country"]
    L.append("")
    L.append("## CoOps (Global): Cold Lead by country")
    L.append("")
    L.append("Live deals in the entry stage (%s): %s." % (cl["as_of"], fmt(cl["total"])))
    L.append("")
    L.append(_md_table(["Country", "Cold Lead deals"], [[c, fmt(n)] for c, n in cl["by_country"].items()] + [["Total", fmt(cl["total"])]]))
    L.append("")
    L.append(_md_table(["Lead source", "Country", "Deals"], [[r["lead_source"], r["country"], fmt(r["deals"])] for r in cl["by_lead_source"]]))
    L.append("")
    L.append("## Data freshness and integrity")
    L.append("")
    L.append(_md_table(["Account", "Last deals sync (IST)", "Status", "Rows in last sync", "Report day complete in data"],
                       [[f["account"], f["last_success_ist"] or "never", f["status"] or "-", fmt(f["rows_synced"]), "yes" if f["day_complete"] else "no"] for f in integ["freshness"]]))
    L.append("")
    L.append("- Days without events (last 14): %s" % (", ".join("%s %s%s" % (d["weekday"], d["day"], " weekend" if d["weekend"] else "") for d in integ["days_without_events"]) or "none"))
    L.append("- Unmapped stages: %s" % ("; ".join("%s/%s %s (%s deals now, %s events)" % (u["account_id"], u["pipeline_id"], u["stage_id"], u["deals_now"], u["events"]) for u in integ["unmapped_stages"]) or "none"))
    L.append("- Archived deals (excluded from occupancy): %s" % "; ".join("%s %s" % (a["name"], fmt(a["archived"])) for a in integ["archived"]))
    for ip in integ["ignored_pipelines"]:
        L.append("- Ignored pipeline %s/%s (%s): %s deal(s), %s event(s)" % (ip["account_id"], ip["pipeline_id"], ip["label"], fmt(ip["deals"]), fmt(ip["events"])))
    if integ["dq_warn_error"]:
        L.append("- Open DQ findings (warn/error): " + "; ".join("%s [%s] x%s" % (d["rule_code"], d["severity"], fmt(d["occurrences"])) for d in integ["dq_warn_error"]))
    L.append("- History read failures / dropped events: %s; state drift: %s; near-duplicate events: %s" % (fmt(integ["history_failures"]), fmt(integ["deal_state_drift"]), fmt(integ["near_duplicate_events"])))
    for an in integ["anchors"]:
        L.append("- Regression anchor %s: %s" % (an["id"], "PASS" if an["ok"] else "DIFFERENCE (expected %s, actual %s)" % (an["expected"], an["actual"])))
    cc = m["crosscheck"]
    L.append("")
    L.append("## Cross-check against the legacy snapshots")
    L.append("")
    if not cc["available"]:
        L.append(" ".join(cc["notes"]))
    else:
        rows = []
        for s in cc["series"]:
            name = "%s %s%s" % (s["family"], s["pipeline_id"] or s["account"], (" owner " + s["owner_hs_id"]) if s["owner_hs_id"] else "")
            for d in s["days"]:
                e = d.get("engaged")
                rows.append([name, d["day"], fmt(d["summary"]["rows"]), fmt(d["summary"]["match"]), fmt(d["summary"]["mismatch"]), ("%s/%s/%s" % (e["legacy"], e["mine"], e["both"])) if e else "-"])
        L.append(_md_table(["Series", "Snapshot day", "Rows", "Match", "Differ", "Engaged legacy/recomputed/both"], rows))
        L.append("")
        L.append(_md_table(["Delta class", "Rows"], [[k, fmt(v)] for k, v in cc["reason_counts"].items()]))
    L.append("")
    L.append("---")
    L.append("Generated by leadgen.reports v%s. Stage map sha256 %s. IST days. Metric definitions: config/report.yaml." % (meta["tool_version"], meta["stage_map_sha256"][:16]))
    return "\n".join(L) + "\n"


# -------------------------------------------------------------------------------------------------------------- csv
def render_csv(m: Dict[str, Any]) -> str:
    """Tidy long format: section, scope, key, label, metric, value.  One line per number, diff-friendly."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["report_day", "section", "scope", "key", "label", "metric", "value"])
    D = m["meta"]["report_day"]

    def put(section, scope, key, label, metric, value):
        w.writerow([D, section, scope, key, label, metric, "" if value is None else value])

    def block(section, scope, r, key):
        for name in ("today", "yesterday", "roll7", "prev7"):
            b = r[name]
            put(section, scope, key, r["label"], name + "_headline", b["headline"])
            put(section, scope, key, r["label"], name + "_human", b["human"])
            put(section, scope, key, r["label"], name + "_auto", b["auto"])
            put(section, scope, key, r["label"], name + "_total", b["total"])
            put(section, scope, key, r["label"], name + "_deals_human", b["deals_human"])
            put(section, scope, key, r["label"], name + "_deals_all", b["deals_all"])
        put(section, scope, key, r["label"], "now", r.get("now"))
        put(section, scope, key, r["label"], "inception_deals_all", r["inception"]["deals_all"])
        put(section, scope, key, r["label"], "inception_deals_human", r["inception"]["deals_human"])
        if r.get("inception_in_scope"):
            put(section, scope, key, r["label"], "inception_in_scope_deals_all", r["inception_in_scope"]["deals_all"])
        for cn, ch in (("change_today", r["change_today"]), ("change_roll7", r["change_roll7"])):
            put(section, scope, key, r["label"], cn + "_abs", ch["abs"])
            put(section, scope, key, r["label"], cn + "_pct", ch["pct"])
            put(section, scope, key, r["label"], cn + "_flag", ch["flag"])
    for r in m["combined"]["rungs"]:
        block("canonical", "ALL", r, r["code"])
    for r in m["combined"]["dead_reasons"]:
        block("dead_reason", "ALL", r, r["code"])
    for f in m["funnels"]:
        for r in f["rungs"]:
            block("canonical", f["slug"], r, r["code"])
        for r in f["dead_reasons"]:
            block("dead_reason", f["slug"], r, r["code"])
        for r in f["native"]:
            block("native", f["slug"], r, r["stage_id"])
        eg = f["engaged"]
        for k in ("today", "yesterday", "roll7_union", "prev7_union", "net_new_today", "roll7_days_covered", "prev7_days_covered"):
            put("engaged", f["slug"], k, "Leads engaged", k, eg[k])
        for o in f["owners"]["rows"]:
            key = str(o["owner_id"]) if o["owner_id"] is not None else "unassigned"
            for k in ("moves_today", "engaged_today", "engaged_roll7", "net_new_today", "assigned_today", "now_total", "now_live", "now_dead", "now_won"):
                put("owner", f["slug"], key, o["name"], k, o[k])
    h = m["headline"]
    for k in ("today", "yesterday", "roll7", "prev7"):
        put("headline", "ALL", "leads_assigned", "Leads assigned", k, h["leads_assigned"][k])
    for k in ("today", "yesterday", "roll7_union", "prev7_union", "net_new_today"):
        put("headline", "ALL", "leads_engaged", "Leads engaged", k, h["leads_engaged"][k])
    put("headline", "ALL", "won", "Won to date", "to_date", h["won"]["to_date"])
    put("headline", "ALL", "won", "Won now", "now", h["won"]["now"])
    cl = m["cold_lead_country"]
    for c, n in cl["by_country"].items():
        put("cold_lead_country", cl["funnel"], c, c, "deals", n)
    put("cold_lead_country", cl["funnel"], "TOTAL", "Total", "deals", cl["total"])
    for r in cl["by_lead_source"]:
        put("cold_lead_lead_source", cl["funnel"], r["lead_source"], r["country"], "deals", r["deals"])
    cc = m["crosscheck"]
    for s in cc["series"]:
        sc = "%s:%s" % (s["family"], s["pipeline_id"] or s["account"] + (":" + s["owner_hs_id"] if s["owner_hs_id"] else ""))
        for d in s["days"]:
            put("crosscheck_summary", sc, d["day"], "rows compared / matching / differing", "rows|match|differ", "%s|%s|%s" % (d["summary"]["rows"], d["summary"]["match"], d["summary"]["mismatch"]))
            for r in d["rows"]:
                if r["reason"] != "match":
                    put("crosscheck_difference", sc, "%s|%s|%s" % (d["day"], r["metric"], r["row"]), r["reason"], "legacy|recomputed|delta", "%s|%s|%s" % (r["legacy"], r["mine"], r["delta"]))
    return buf.getvalue()


def render_json(m: Dict[str, Any]) -> str:
    return model_json(m)


def email_subject(m: Dict[str, Any], template: str) -> str:
    import datetime
    d = datetime.date(int(m["meta"]["report_day"][:4]), int(m["meta"]["report_day"][5:7]), int(m["meta"]["report_day"][8:10]))
    return template.replace("{date}", d.strftime("%b %d, %Y"))
