"""Assemble the report model for an IST day and persist the facts to ``rpt_*`` (docs/REPORTS.md).

``build_model(engine, day)`` is a pure function of the event store (as of that day) and the deal table; it returns plain JSON-able data with
NO volatile fields (no wall-clock time, no run ids), so re-running for the same day over the same database gives a byte-identical JSON.
``persist_days`` rewrites the ``rpt_funnel_daily`` / ``rpt_owner_daily`` / ``rpt_engaged_daily`` rows of the given IST days in short
transactions (delete + insert per day), so any past day can be rebuilt and a re-run never duplicates.
"""
import collections
import json
import sqlite3
from typing import Any, Dict, List, Optional, Sequence, Tuple

from leadgen import db as ldb
from leadgen.reports import REPORT_TOOL_VERSION
from leadgen.reports import metrics as M

TOP_LEVEL_ORDER = ("meta", "headline", "combined", "funnels", "cold_lead_country", "integrity", "crosscheck", "trends")


# ------------------------------------------------------------------------------------------------------------ rows
def _block(eng: M.Engine, fk: str, lvl: str, key: str, D: str, basis: str, covered_full: bool, scope: str = "all") -> Dict[str, Any]:
    blk = M._stat_block(eng, fk, lvl, key, D, basis, scope)
    pm = eng.cfg["window"]["pct_min_prev"]
    blk["change_today"] = M.change(blk["yesterday"]["headline"], blk["today"]["headline"], True, pm)
    blk["change_roll7"] = M.change(blk["prev7"]["headline"], blk["roll7"]["headline"], covered_full, pm)
    blk["basis"] = basis
    return blk


def _canonical_rows(eng: M.Engine, fk: str, D: str, occ: Dict[str, Any], full: bool) -> List[Dict[str, Any]]:
    rows = []
    in_scope = fk in eng.cohort_slugs
    for code in eng.canon_order:
        s = eng.canon[code]
        derived = bool(s.get("is_derived"))
        basis = eng.basis(code)
        row = {"level": "rung", "code": code, "rank": s["rank"], "label": s["label"], "kind": "dead" if s["is_dead"] else ("won" if s["is_won"] else ("derived" if derived else ("entry" if s.get("is_entry") else "live"))),
               "now": None if derived else occ["by_code"].get(code, 0)}
        row.update(_block(eng, fk, "c", code, D, basis, full))
        if in_scope and not derived and code != "DEAD":
            row["inception_in_scope"] = eng.inception(fk, "c", code, D, "in_scope")
        rows.append(row)
    if eng.cells.get((fk, "c", M.UNMAPPED)) or occ["by_code"].get(M.UNMAPPED):
        row = {"level": "rung", "code": M.UNMAPPED, "rank": "?", "label": "UNMAPPED stages (no stage_map row - fix the map)", "kind": "unmapped", "now": occ["by_code"].get(M.UNMAPPED, 0)}
        row.update(_block(eng, fk, "c", M.UNMAPPED, D, "human", full))
        rows.append(row)
    return rows


def _dead_rows(eng: M.Engine, fk: str, D: str, occ: Dict[str, Any], full: bool) -> List[Dict[str, Any]]:
    rows = []
    for rc in eng.reasons:
        row = {"level": "dead_reason", "code": rc, "label": eng.reason_label[rc], "now": occ["by_reason"].get(rc, 0)}
        row.update(_block(eng, fk, "r", rc, D, "human", full))
        if fk in eng.cohort_slugs:
            row["inception_in_scope"] = eng.inception(fk, "r", rc, D, "in_scope")
        rows.append(row)
    return rows


def _native_rows(eng: M.Engine, slug: str, D: str, full: bool) -> List[Dict[str, Any]]:
    acct, pipe = eng.pipe_of[slug]
    occ = eng.occupancy(D)
    now_by_group = collections.Counter()
    for (a, p, s), n in occ.items():
        if (a, p) == (acct, pipe):
            now_by_group[eng.stages[(a, p, s)].group_stage] += n
    heads = []
    for (a, p, s), st in eng.stages.items():
        if (a, p) != (acct, pipe) or st.group_stage != s:
            continue
        has = bool(eng.cells.get((slug, "s", s))) or now_by_group.get(s, 0)
        if st.is_deleted and not has:
            continue
        heads.append(st)
    heads.sort(key=lambda st: (st.is_deleted, st.order if st.order is not None else 10 ** 6, st.stage))
    rows = []
    for st in heads:
        basis = eng.basis(st.canonical)
        label = st.label
        if st.is_deleted:
            label = "%s (deleted stage%s)" % (label, "" if st.is_mapped else ", UNMAPPED")
        row = {"level": "native", "stage_id": st.stage, "label": label, "canonical": st.canonical or M.UNMAPPED, "dead_reason": st.dead_reason,
               "sub_row": st.sub_row, "is_dead": st.is_dead, "is_won": st.is_won, "is_deleted": st.is_deleted, "is_mapped": st.is_mapped,
               "now": now_by_group.get(st.stage, 0)}
        row.update(_block(eng, slug, "s", st.stage, D, basis, full))
        if slug in eng.cohort_slugs:
            row["inception_in_scope"] = eng.inception(slug, "s", st.stage, D, "in_scope")
        rows.append(row)
    return rows


# ----------------------------------------------------------------------------------------------------------- owners
def _owner_rows(eng: M.Engine, slug: str, D: str) -> Dict[str, Any]:
    acct, pipe = eng.pipe_of[slug]
    wb = M._window_bounds(eng.cfg, D)
    reps_cfg = eng.cfg.get("reps", {}).get(slug, [])
    by_email = {}
    for o in eng.owners.values():
        if o["account"] == acct and o["email"]:
            by_email[o["email"]] = o["owner_id"]
    rep_ids = [by_email[e] for e in reps_cfg if e in by_email]
    missing_reps = [e for e in reps_cfg if e not in by_email]
    evs_today = [e for e in eng.events_in(D, D, True, slug)]
    evs_roll = [e for e in eng.events_in(wb["roll7"][0], D, True, slug)]
    state = eng.deal_state(D)
    cur = collections.defaultdict(lambda: collections.Counter())
    for deal, (a, p, s) in state.items():
        if (a, p) != (acct, pipe):
            continue
        d = eng.deals[deal]
        owner = eng.owner_at(deal, D) if (eng.live_day is None or D < eng.live_day) else d.owner
        st = eng.stages[(a, p, s)]
        c = cur[owner]
        c["total"] += 1
        c["dead" if st.is_dead else ("won" if st.is_won else "live")] += 1
        c["code:" + (st.canonical or M.UNMAPPED)] += 1
    nn = eng.net_new(slug, D)
    assigned = collections.Counter()
    for deal, evs in eng.owner_events.items():
        d = eng.deals[deal]
        if (d.account, d.pipeline) != (acct, pipe):
            continue
        for day, _ts, owner in evs:
            if day == D and owner is not None:
                assigned[owner] += 1
    ids = list(rep_ids)
    seen_other = set(e.actor for e in evs_roll if e.actor is not None) | set(o for o in cur if o is not None) | set(assigned)
    others = sorted((o for o in seen_other if o not in rep_ids and eng.owners.get(o, {}).get("account") == acct), key=lambda o: eng.owners[o]["name"])
    rows = []
    for oid in ids + others + ([None] if None in cur else []):
        today = [e for e in evs_today if e.actor == oid]
        roll = [e for e in evs_roll if e.actor == oid]
        by_code = collections.defaultdict(set)
        for e in today:
            by_code[eng.stage(e).canonical or M.UNMAPPED].add((e.deal, eng.stage(e).group_stage))
        c = cur.get(oid, collections.Counter())
        o = eng.owners.get(oid) if oid is not None else None
        rows.append({"owner_id": oid, "name": o["name"] if o else "(unassigned)", "email": o["email"] if o else None, "archived": bool(o and o["archived"]),
                     "listed_rep": oid in rep_ids,
                     "moves_today": len(set((e.deal, eng.stage(e).group_stage) for e in today)),
                     "engaged_today": len(set(e.deal for e in today)),
                     "engaged_roll7": len(set(e.deal for e in roll)),
                     "net_new_today": sum(1 for d_, a_ in nn.items() if a_ == oid),
                     "assigned_today": assigned.get(oid, 0) if oid is not None else 0,
                     "moves_today_by_code": {k: len(v) for k, v in sorted(by_code.items())},
                     "now_total": c.get("total", 0), "now_live": c.get("live", 0), "now_dead": c.get("dead", 0), "now_won": c.get("won", 0),
                     "now_by_code": {k[5:]: v for k, v in sorted(c.items()) if k.startswith("code:")}})
    return {"rows": rows, "missing_reps": missing_reps, "owner_as_of": "owner as of the report day (deal_owner_event)" if (eng.live_day is None or D < eng.live_day) else "current owner (deal table, as of the last sync)"}


# --------------------------------------------------------------------------------------------------------- model
def _trend(eng: M.Engine, fk: str, D: str) -> Dict[str, Any]:
    n = int(eng.cfg["window"]["trend_days"])
    days = M.day_range(M.day_add(D, -(n - 1)), D)
    la = []; en = []; hm = []
    for d in days:
        w = eng.window(fk, "c", "SOURCED", d, d, D)
        la.append(w["total"])
        en.append(len(eng.engaged_deals(fk, d, d, D)))
        hm.append(sum(1 for e in eng.events_in(d, d, True, fk)))
    return {"days": days, "leads_assigned": la, "engaged": en, "human_moves": hm}


def build_model(eng: M.Engine, D: str, with_crosscheck: bool = True) -> Dict[str, Any]:
    cfg = eng.cfg
    wb = M._window_bounds(cfg, D)
    n = wb["n"]
    cov_all = eng.coverage(wb["roll7"][0], wb["roll7"][1], D)
    covp_all = eng.coverage(wb["prev7"][0], wb["prev7"][1], D)
    full_all = cov_all["covered"] == n and covp_all["covered"] == n
    occ_all = M.occupancy(eng, D, M.ALL)
    meta = {"report_day": D, "weekday": M.parse_day(D).strftime("%A"), "timezone": cfg["timezone"],
            "windows": {"today": D, "yesterday": wb["yesterday"], "roll7": wb["roll7"], "prev7": wb["prev7"]},
            "coverage": {"roll7": cov_all, "prev7": covp_all, "roll7_label": "%d of %d days" % (cov_all["covered"], n), "prev7_label": "%d of %d days" % (covp_all["covered"], n)},
            "metrics_version": cfg["metrics_version"], "stage_map_sha256": eng.sha, "tool_version": REPORT_TOOL_VERSION,
            "events_on_day": len(eng.by_day.get(D, ())), "last_event_day_le_report_day": max((d for d in eng.by_day if d <= D), default=None),
            "store": {"first_event_day": eng.first_day, "last_event_day": eng.last_day, "events": len(eng.events)},
            "occupancy_basis": "deal table (live, as of the last sync)" if (eng.live_day is not None and D >= eng.live_day) else "reconstructed from the event store at the end of the day",
            "headline_basis": "Entry rungs (Sourced / Assigned, LinkedIn sent) and derived rungs count any source; every other rung counts CRM_UI (human) moves only. Human, Automated and Total are carried beside it.",
            "labels": {"entries": "entries = de-duplicated (deal, stage, actor, IST day) events", "deals": "deals = distinct deals", "now": "now = deals sitting in the stage (live board count)"},
            "funnels": [{"slug": f["slug"], "name": f["name"], "portal": f["portal"], "account": f["account"], "pipeline_id": f["pipeline"]} for f in eng.funnels]}
    # ---- combined
    combined_rows = _canonical_rows(eng, M.ALL, D, occ_all, full_all)
    combined_dead = _dead_rows(eng, M.ALL, D, occ_all, full_all)
    matrix = []
    funnels = []
    per_funnel_head = []
    for f in eng.funnels:
        slug = f["slug"]
        acc = [f["account"]]
        cov = eng.coverage(wb["roll7"][0], wb["roll7"][1], D, acc)
        covp = eng.coverage(wb["prev7"][0], wb["prev7"][1], D, acc)
        full = cov["covered"] == n and covp["covered"] == n
        occ = M.occupancy(eng, D, slug)
        crow = _canonical_rows(eng, slug, D, occ, full)
        drow = _dead_rows(eng, slug, D, occ, full)
        eg = M.engaged(eng, slug, D)
        la = M.leads_assigned(eng, slug, D)
        won = eng.inception(slug, "c", "WON", D)
        fo = {"slug": slug, "name": f["name"], "portal": f["portal"], "account": f["account"], "pipeline_id": f["pipeline"], "cohort_note": f.get("cohort_note", ""),
              "cohort": eng.pipeline_cfg[slug] if slug in eng.cohort_slugs else None,
              "coverage": {"roll7": cov, "prev7": covp, "roll7_label": "%d of %d days" % (cov["covered"], n)},
              "occupancy": {k: v for k, v in occ.items() if k not in ("by_code", "by_reason")},
              "engaged": eg, "leads_assigned": la, "won_to_date": won,
              "rungs": crow, "dead_reasons": drow, "native": _native_rows(eng, slug, D, full), "owners": _owner_rows(eng, slug, D), "trend": _trend(eng, slug, D)}
        funnels.append(fo)
        per_funnel_head.append({"slug": slug, "name": f["name"], "leads_today": la["today"]["headline"], "leads_roll7": la["roll7"]["headline"], "engaged_today": eg["today"],
                                "engaged_roll7": eg["roll7_union"], "live": occ["live"], "dead": occ["dead"], "won_now": occ["won"], "won_to_date": won["deals_all"], "total_now": occ["total"]})
    # matrix: canonical rung x funnel (today headline, now)
    for code in eng.canon_order:
        r = {"code": code, "label": eng.canon[code]["label"], "cells": {}}
        for f in funnels:
            row = next((x for x in f["rungs"] if x["code"] == code), None)
            r["cells"][f["slug"]] = {"today": row["today"]["headline"], "now": row["now"], "roll7": row["roll7"]["headline"]} if row else None
        matrix.append(r)
    eg_all = M.engaged(eng, M.ALL, D)
    la_all = M.leads_assigned(eng, M.ALL, D)
    contract = next(r for r in combined_rows if r["code"] == "CONTRACT")
    won_row = next(r for r in combined_rows if r["code"] == "WON")
    integ = M.integrity(eng, D)
    ok_accounts = [f["account"] for f in integ["freshness"] if f["lag_days"] is not None and f["lag_days"] <= 0]
    n_report = sum(1 for f in eng.funnels if f["account"] in ok_accounts)
    headline = {
        "funnels_reporting": {"n": n_report, "of": len(eng.funnels)},
        "leads_assigned": {"today": la_all["today"]["headline"], "yesterday": la_all["yesterday"]["headline"], "roll7": la_all["roll7"]["headline"], "prev7": la_all["prev7"]["headline"],
                           "today_human": la_all["today"]["human"], "today_auto": la_all["today"]["auto"], "new_deals": la_all["new_deals"],
                           "change_today": M.change(la_all["yesterday"]["headline"], la_all["today"]["headline"], True, cfg["window"]["pct_min_prev"]),
                           "change_roll7": M.change(la_all["prev7"]["headline"], la_all["roll7"]["headline"], full_all, cfg["window"]["pct_min_prev"])},
        "leads_engaged": dict(eg_all, roll7_label="%d of %d days" % (cov_all["covered"], n)),
        "won": {"to_date": won_row["inception"]["deals_all"], "now": occ_all["won"], "this_week_entries": won_row["roll7"]["headline"]},
        "contracts_signed_7d": contract["roll7"]["headline"],
        "occupancy": {k: v for k, v in occ_all.items() if k not in ("by_code", "by_reason")},
        "per_funnel": per_funnel_head,
    }
    cl = M.cold_lead_country(eng, D)
    out = {"meta": meta, "headline": headline,
           "combined": {"rungs": combined_rows, "dead_reasons": combined_dead, "matrix": matrix},
           "funnels": funnels, "cold_lead_country": cl, "integrity": integ,
           "crosscheck": M.crosscheck(eng, D) if with_crosscheck else {"available": False, "series": [], "reason_counts": {}, "notes": ["cross-check not computed"]},
           "trends": {"ALL": _trend(eng, M.ALL, D)}, "definitions": cfg["metrics"]}
    out["trends"].update({f["slug"]: f["trend"] for f in funnels})
    return out


def model_json(model: Dict[str, Any]) -> str:
    """Deterministic JSON text (sorted keys, fixed separators, trailing newline)."""
    return json.dumps(model, sort_keys=True, indent=1, ensure_ascii=False, separators=(",", ": ")) + "\n"


# ------------------------------------------------------------------------------------------------------ persistence
def occupancy_series(eng: M.Engine, days: Sequence[str]) -> Dict[str, Dict[Tuple[str, str, str], int]]:
    """Occupancy at the end of each IST day (deals sitting in each native stage, archived-by-then excluded), reconstructed in one pass."""
    out = {}
    state = {}
    ei = 0
    evs = eng.events
    arch_at = {d.deal: (M.ist_day_of(d.archived_at) if d.archived_at else "0000-00-00") for d in eng.deals.values() if d.archived}
    for day in sorted(days):
        while ei < len(evs) and evs[ei].day <= day:
            e = evs[ei]
            state[e.deal] = (e.account, e.pipeline, e.stage)
            ei += 1
        c = collections.Counter()
        for deal, k in state.items():
            if deal in arch_at and arch_at[deal] <= day:
                continue
            c[k] += 1
        out[day] = c
    return out


def facts_for_day(eng: M.Engine, day: str, occ: Dict[Tuple[str, str, str], int]) -> Dict[str, List[tuple]]:
    """The rows of rpt_funnel_daily / rpt_owner_daily / rpt_engaged_daily for one IST day, as tuples (without report_run_id)."""
    fd = []
    keys = set()
    for (fk, lvl, key), days in eng.cells.items():
        if lvl == "s" and day in days:
            keys.add((fk, key, "all"))
    for (fk, lvl, key), days in eng.cells_in.items():
        if lvl == "s" and day in days:
            keys.add((fk, key, "in_scope"))
    for (a, p, s), n in occ.items():
        if n and (a, p) in eng.slug_of:
            st = eng.stages[(a, p, s)]
            keys.add((eng.slug_of[(a, p)], st.group_stage, "all"))
    occ_group = collections.Counter()
    for (a, p, s), n in occ.items():
        if (a, p) in eng.slug_of:
            occ_group[(a, p, eng.stages[(a, p, s)].group_stage)] += n
    for fk, group, scope in sorted(keys):
        a, p = eng.pipe_of[fk]
        st = eng.stages[(a, p, group)]
        store = eng.cells_in if scope == "in_scope" else eng.cells
        c = store.get((fk, "s", group), {}).get(day, M.EMPTY)
        fd.append((day, a, p, group, scope, st.canonical, st.dead_reason, c.nh, c.na, len(c.hd), len(c.alld),
                   occ_group.get((a, p, group), 0) if scope == "all" else None))
    # owner-day
    od = []
    ev_day = eng.by_day.get(day, [])
    grp = collections.defaultdict(lambda: [set(), set()])   # (account, pipeline, owner, code) -> [human deals, auto deals]
    eng_g = collections.defaultdict(lambda: [set(), set()])  # (account, pipeline, owner) -> [deals, (deal, stage) pairs]
    for e in ev_day:
        st = eng.stage(e)
        k = (e.account, e.pipeline, e.actor, st.canonical)
        grp[k][0 if e.human else 1].add(e.deal)
        if e.human and e.actor is not None:
            g = eng_g[(e.account, e.pipeline, e.actor)]
            g[0].add(e.deal); g[1].add((e.deal, st.group_stage))
    for (a, p, o, code), (h, au) in sorted(grp.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2] or 0, kv[0][3] or "")):
        od.append((day, a, p, o, "stage_entry", code, len(h), len(au), len(h | au), 0, 0))
    for (a, p, o), (deals, pairs) in sorted(eng_g.items()):
        od.append((day, a, p, o, "engaged", None, len(pairs), 0, len(deals), len(deals), 0))
    nn = collections.defaultdict(set)
    for slug in eng.slugs:
        for deal, actor in eng.net_new(slug, day).items():
            a, p = eng.pipe_of[slug]
            if actor is not None:
                nn[(a, p, actor)].add(deal)
    for (a, p, o), deals in sorted(nn.items()):
        od.append((day, a, p, o, "net_new", None, len(deals), 0, len(deals), len(deals), 0))
    asg = collections.defaultdict(lambda: [0, set()])
    for deal, evs in eng.owner_events.items():
        d = eng.deals[deal]
        if (d.account, d.pipeline) not in eng.slug_of:
            continue
        for dday, _ts, owner in evs:
            if dday == day and owner is not None:
                x = asg[(d.account, d.pipeline, owner)]
                x[0] += 1; x[1].add(deal)
    for (a, p, o), (cnt, deals) in sorted(asg.items()):
        od.append((day, a, p, o, "assigned", None, 0, cnt, len(deals), 0, 0))
    # engaged-day
    ed = []
    per = {}
    for e in ev_day:
        if e.human and e.actor is not None:
            per[(e.actor, e.deal)] = e        # last human event of the day by this owner on this deal decides the pipeline
    first = {}
    for slug in eng.slugs:
        first.update({(actor, deal): True for deal, actor in eng.net_new(slug, day).items()})
    for (o, deal), e in sorted(per.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        ed.append((day, o, deal, e.account, e.pipeline, "stage", 1 if first.get((o, deal)) else 0))
    return {"funnel_daily": fd, "owner_daily": od, "engaged_daily": ed}


def persist_days(con: sqlite3.Connection, eng: M.Engine, days: Sequence[str], run_id: int) -> Dict[str, int]:
    """Rewrite the rpt_* fact rows of `days` (delete + insert per day, one short IMMEDIATE transaction per day)."""
    occs = occupancy_series(eng, days)
    counts = {"funnel_daily": 0, "owner_daily": 0, "engaged_daily": 0}
    for day in sorted(days):
        facts = facts_for_day(eng, day, occs[day])
        with ldb.transaction(con):
            for t in ("rpt_funnel_daily", "rpt_owner_daily", "rpt_engaged_daily"):
                con.execute("DELETE FROM %s WHERE ist_day = ?" % t, (day,))
            con.executemany("INSERT INTO rpt_funnel_daily (ist_day, account_id, pipeline_id, stage_id, cohort_scope, canonical_code, dead_reason_code, entries_human, entries_auto, "
                            "deals_entered_human, deals_entered_all, occupancy_eod, report_run_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,%d)" % run_id, facts["funnel_daily"])
            con.executemany("INSERT INTO rpt_owner_daily (ist_day, account_id, pipeline_id, owner_id, metric_code, canonical_code, events_human, events_auto, deals_distinct, "
                            "deals_via_stage, deals_note_only, report_run_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,%d)" % run_id, facts["owner_daily"])
            con.executemany("INSERT INTO rpt_engaged_daily (ist_day, owner_id, deal_id, account_id, pipeline_id, via, is_net_new, report_run_id) VALUES (?,?,?,?,?,?,?,%d)" % run_id,
                            facts["engaged_daily"])
        for k in counts:
            counts[k] += len(facts[k])
    return counts


def persist_crosscheck(con: sqlite3.Connection, model: Dict[str, Any], run_id: int) -> int:
    """Write the cross-check rows of the model to rpt_legacy_crosscheck (replacing earlier rows of the same snapshots, so re-runs do not pile up)."""
    cc = model["crosscheck"]
    rows = []
    for s in cc.get("series", []):
        for d in s["days"]:
            for r in d["rows"]:
                if r["mine"] is None:
                    continue
                rows.append((d["snapshot_id"], run_id, r["metric"], r["row"], float(r["legacy"]), float(r["mine"]), r["reason"]))
    if not rows:
        return 0
    ids = sorted(set(r[0] for r in rows))
    with ldb.transaction(con):
        con.execute("DELETE FROM rpt_legacy_crosscheck WHERE snapshot_id IN (%s)" % ",".join("?" * len(ids)), ids)
        con.executemany("INSERT OR IGNORE INTO rpt_legacy_crosscheck (snapshot_id, report_run_id, metric, row_label, legacy_value, recomputed_value, explanation) VALUES (?,?,?,?,?,?,?)", rows)
    return len(rows)
