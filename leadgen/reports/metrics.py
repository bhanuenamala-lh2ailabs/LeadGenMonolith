"""Every number of the unified daily funnel report, computed from ``deal_stage_event`` / ``deal`` (docs/REPORTS.md).

Nothing here reads a pipeline or stage LABEL to find data (keys are ``(account_id, pipeline_id, stage_id)``), nothing reads the legacy
snapshots except :func:`crosscheck` (which only compares), and nothing writes.  One function per entry of ``config/report.yaml`` ``metrics``
(``fn`` key); tests fail if the dictionary and this module drift apart.

The :class:`Engine` loads the event store once (a few 10k rows) and answers "as of IST day D" questions; as-of means events with
``ist_day <= D`` only, so any past day can be re-produced.  Python 3.9 compatible.
"""
import bisect
import collections
import datetime
import hashlib
import json
import os
import re
import sqlite3
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from leadgen import config as lcfg

IST_MINUTES = 330
ALL = "ALL"                      # pseudo funnel = all five funnels combined
UNMAPPED = "UNMAPPED"

# --------------------------------------------------------------------------------------------------------- small helpers


def parse_day(s: str) -> datetime.date:
    return datetime.date(int(s[0:4]), int(s[5:7]), int(s[8:10]))


def day_add(day: str, n: int) -> str:
    return (parse_day(day) + datetime.timedelta(days=n)).isoformat()


def day_range(lo: str, hi: str) -> List[str]:
    out = []
    d = parse_day(lo)
    end = parse_day(hi)
    while d <= end:
        out.append(d.isoformat())
        d += datetime.timedelta(days=1)
    return out


def ist_day_of(ts: str) -> str:
    """IST calendar day of an ISO UTC timestamp ('2026-10-04T16:53:20.901Z')."""
    t = datetime.datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S") + datetime.timedelta(minutes=IST_MINUTES)
    return t.date().isoformat()


def ist_hhmm_of(ts: str) -> str:
    t = datetime.datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S") + datetime.timedelta(minutes=IST_MINUTES)
    return t.strftime("%Y-%m-%d %H:%M")


def today_ist() -> str:
    return ist_day_of(datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S"))


def load_report_cfg(config_dir: Optional[str] = None) -> Dict[str, Any]:
    cfg = lcfg.load_yaml("report", config_dir)
    cfg["_canonical"] = lcfg.load_canonical_stages(config_dir)
    return cfg


def metric_dictionary(cfg: Dict[str, Any]) -> Dict[str, Any]:
    return cfg["metrics"]


def _pct(prev: int, cur: int) -> float:
    return round((cur - prev) * 100.0 / prev, 1)


def change(prev: int, cur: int, complete: bool, min_prev: int = 10) -> Dict[str, Any]:
    """Metric ``change``: absolute delta always; a percentage only when both windows are complete and prev >= min_prev; 0 -> >0 is 'new'."""
    d = {"abs": cur - prev, "pct": None, "flag": "abs"}  # type: Dict[str, Any]
    if prev == 0 and cur > 0:
        d["flag"] = "new"
    elif prev == 0 and cur == 0:
        d["flag"] = "flat"
    elif not complete:
        d["flag"] = "partial"
    elif prev >= min_prev:
        d["pct"] = _pct(prev, cur)
        d["flag"] = "pct"
    return d


# ------------------------------------------------------------------------------------------------------- data classes
Stage = collections.namedtuple(
    "Stage", "account pipeline stage label order is_deleted is_closed canonical dead_reason depth_reached sub_row is_mapped group_stage "
             "slug is_entry is_live is_dead is_won flag_attempt flag_connected flag_interested")
Event = collections.namedtuple("Event", "eid deal account pipeline stage ts day human actor")
Deal = collections.namedtuple("Deal", "deal account pipeline stage owner hs_id lead_source archived archived_at created cost amount")


class Cell(object):
    """One (funnel, level, key, IST day) aggregate: de-duplicated human / automated entries and the distinct deals behind them."""
    __slots__ = ("nh", "na", "hd", "alld")

    def __init__(self, nh: int, na: int, hd: frozenset, alld: frozenset) -> None:
        self.nh, self.na, self.hd, self.alld = nh, na, hd, alld


EMPTY = Cell(0, 0, frozenset(), frozenset())


def stage_map_sha256(con: sqlite3.Connection) -> str:
    h = hashlib.sha256()
    for r in con.execute("SELECT account_id, pipeline_id, stage_id, canonical_code, COALESCE(dead_reason_code,''), COALESCE(depth_reached,''), "
                         "COALESCE(sub_row,''), COALESCE(flag_attempt,''), COALESCE(flag_connected,''), COALESCE(flag_interested,'') "
                         "FROM stage_map ORDER BY 1,2,3"):
        h.update(("|".join(str(x) for x in tuple(r)) + "\n").encode("utf-8"))
    h.update(b"--alias--\n")
    for r in con.execute("SELECT account_id, pipeline_id, alias_kind, alias_value, stage_id FROM stage_alias ORDER BY 1,2,3,4"):
        h.update(("|".join(str(x) for x in tuple(r)) + "\n").encode("utf-8"))
    return h.hexdigest()


# ====================================================================================================== THE ENGINE
class Engine(object):
    """The event store in memory plus every aggregate the report needs.  Build once, ask for any IST day."""

    def __init__(self, con: sqlite3.Connection, cfg: Optional[Dict[str, Any]] = None, config_dir: Optional[str] = None) -> None:
        self.con = con
        self.cfg = cfg or load_report_cfg(config_dir)
        canon = self.cfg["_canonical"]
        self.canon_order = [s["code"] for s in canon["stages"]]                       # ladder order incl. DEAD last
        self.canon = {s["code"]: s for s in canon["stages"]}
        self.reasons = [r["code"] for r in canon["dead_reasons"]]
        self.reason_label = {r["code"]: r["label"] for r in canon["dead_reasons"]}
        self.funnels = []  # type: List[Dict[str, Any]]
        self._load_pipelines()
        self._load_stages()
        self._load_owners()
        self._load_deals()
        self._load_owner_events()
        self._load_sync()
        self._load_events()
        self._build_cells()
        self._build_derived()
        self._build_cohort_cells()
        self._build_first_human()
        self.sha = stage_map_sha256(con)

    # ------------------------------------------------------------------------------------------------------ loading
    def _load_pipelines(self) -> None:
        names = {f["slug"]: f for f in self.cfg["funnels"]}
        self.slug_of = {}      # (account, pipeline) -> slug
        self.pipe_of = {}      # slug -> (account, pipeline)
        self.pipeline_cfg = {}  # slug -> dict(cohort_start, cohort_exclude_migration_on, label)
        for r in self.con.execute("SELECT account_id, pipeline_id, label, funnel_slug, is_reporting_funnel, cohort_start, cohort_exclude_migration_on "
                                  "FROM pipeline ORDER BY account_id, display_order"):
            if r["is_reporting_funnel"] and r["funnel_slug"] in names:
                self.slug_of[(r["account_id"], r["pipeline_id"])] = r["funnel_slug"]
                self.pipe_of[r["funnel_slug"]] = (r["account_id"], r["pipeline_id"])
                self.pipeline_cfg[r["funnel_slug"]] = {"cohort_start": r["cohort_start"], "cohort_exclude_migration_on": r["cohort_exclude_migration_on"],
                                                       "label": r["label"]}
        self.ignored_pipelines = [(r["account_id"], r["pipeline_id"], r["label"]) for r in self.con.execute(
            "SELECT account_id, pipeline_id, label FROM pipeline WHERE is_reporting_funnel = 0 ORDER BY 1,2")]
        for f in self.cfg["funnels"]:
            if f["slug"] in self.pipe_of:
                g = dict(f)
                g["account"], g["pipeline"] = self.pipe_of[f["slug"]]
                self.funnels.append(g)
        self.slugs = [f["slug"] for f in self.funnels]
        self.cohort_slugs = [s for s in self.slugs if self.pipeline_cfg[s]["cohort_start"]]

    def _load_stages(self) -> None:
        self.stages = {}  # type: Dict[Tuple[str, str, str], Stage]
        for r in self.con.execute("SELECT e.*, p.funnel_slug FROM v_stage_effective e JOIN pipeline p ON p.account_id = e.account_id AND p.pipeline_id = e.pipeline_id"):
            slug = self.slug_of.get((r["account_id"], r["pipeline_id"]))
            code = r["canonical_code"] if r["is_mapped"] else None
            self.stages[(r["account_id"], r["pipeline_id"], r["stage_id"])] = Stage(
                r["account_id"], r["pipeline_id"], r["stage_id"], r["stage_label"], r["stage_order"], bool(r["is_deleted"]), bool(r["is_closed"]),
                code, r["dead_reason_code"], r["depth_reached"], r["sub_row"], bool(r["is_mapped"]), r["native_group_stage_id"], slug,
                bool(r["is_entry"]) if r["is_mapped"] else False, bool(r["is_live"]) if r["is_mapped"] else False,
                bool(r["is_dead"]) if r["is_mapped"] else False, bool(r["is_won"]) if r["is_mapped"] else False,
                r["flag_attempt"], r["flag_connected"], r["flag_interested"])

    def _load_owners(self) -> None:
        self.owners = {}  # owner_id -> dict
        for r in self.con.execute("SELECT owner_id, account_id, email_norm, display_name, is_archived FROM owner"):
            self.owners[r["owner_id"]] = {"owner_id": r["owner_id"], "account": r["account_id"], "email": r["email_norm"], "name": r["display_name"],
                                          "archived": bool(r["is_archived"])}

    def _load_deals(self) -> None:
        self.deals = {}  # type: Dict[int, Deal]
        for r in self.con.execute("SELECT d.deal_id, d.account_id, d.pipeline_id, d.stage_id, d.owner_id, d.hs_deal_id, ls.label AS lead_source, "
                                  "d.is_archived, d.archived_at, d.hs_created_at, d.cost_usd, d.amount "
                                  "FROM deal d LEFT JOIN lead_source ls ON ls.lead_source_id = d.lead_source_id"):
            self.deals[r["deal_id"]] = Deal(r["deal_id"], r["account_id"], r["pipeline_id"], r["stage_id"], r["owner_id"], r["hs_deal_id"], r["lead_source"],
                                            bool(r["is_archived"]), r["archived_at"], r["hs_created_at"], r["cost_usd"], r["amount"])

    def _load_owner_events(self) -> None:
        """deal_owner_event -> deal -> [(ist_day, owner_id)] sorted; owner_at() gives the owner AS OF an IST day (falls back to the current owner)."""
        self.owner_events = collections.defaultdict(list)  # type: Dict[int, List[Tuple[str, str, Optional[int]]]]
        for r in self.con.execute("SELECT deal_id, assigned_at, ist_day, owner_id FROM deal_owner_event ORDER BY assigned_at"):
            self.owner_events[r["deal_id"]].append((r["ist_day"], r["assigned_at"], r["owner_id"]))

    def owner_at(self, deal: int, D: str) -> Optional[int]:
        evs = self.owner_events.get(deal)
        if not evs:
            return self.deals[deal].owner
        cur = None; found = False
        for day, _ts, owner in evs:
            if day > D:
                break
            cur = owner; found = True
        return cur if found else self.deals[deal].owner

    def _load_sync(self) -> None:
        self.sync = {}  # account -> dict(last_success_at, rows_synced, status)
        for r in self.con.execute("SELECT account_id, last_success_at, last_attempt_at, last_status, rows_synced, last_error FROM ops_sync_state "
                                  "WHERE source_system = 'hubspot' AND object_type = 'deals' AND account_id IS NOT NULL"):
            self.sync[r["account_id"]] = {"last_success_at": r["last_success_at"], "last_attempt_at": r["last_attempt_at"], "status": r["last_status"],
                                          "rows_synced": r["rows_synced"], "error": r["last_error"]}
        succ = [v["last_success_at"] for v in self.sync.values() if v["last_success_at"]]
        self.live_day = ist_day_of(min(succ)) if succ else None   # the day the deal table reflects (oldest account)

    def _load_events(self) -> None:
        self.events = []  # type: List[Event]
        self.nonreporting_events = 0
        self.unknown_stage_events = 0
        for r in self.con.execute("SELECT event_id, deal_id, account_id, pipeline_id, to_stage_id, entered_at, ist_day, is_human, actor_owner_id "
                                  "FROM deal_stage_event ORDER BY entered_at, event_id"):
            if (r["account_id"], r["pipeline_id"]) not in self.slug_of:
                self.nonreporting_events += 1
                continue
            if (r["account_id"], r["pipeline_id"], r["to_stage_id"]) not in self.stages:
                self.unknown_stage_events += 1
                continue
            self.events.append(Event(r["event_id"], r["deal_id"], r["account_id"], r["pipeline_id"], r["to_stage_id"], r["entered_at"], r["ist_day"],
                                     int(r["is_human"]), r["actor_owner_id"]))
        self.by_day = collections.defaultdict(list)  # type: Dict[str, List[Event]]
        for e in self.events:
            self.by_day[e.day].append(e)
        self.first_day = min(self.by_day) if self.by_day else None
        self.last_day = max(self.by_day) if self.by_day else None

    # ------------------------------------------------------------------------------------------------------- cells
    def stage(self, e: Event) -> Stage:
        return self.stages[(e.account, e.pipeline, e.stage)]

    def _build_cells(self) -> None:
        raw = collections.defaultdict(lambda: [set(), set(), set(), set()])  # (fk, level, key, day) -> [human pairs, auto pairs, human deals, auto deals]
        for e in self.events:
            st = self.stage(e)
            slug = self.slug_of[(e.account, e.pipeline)]
            code = st.canonical or UNMAPPED
            keys = [(slug, "c", code), (ALL, "c", code), (slug, "s", st.group_stage)]
            if st.dead_reason:
                keys += [(slug, "r", st.dead_reason), (ALL, "r", st.dead_reason)]
            pair = (e.deal, e.actor)
            for fk, lvl, key in keys:
                c = raw[(fk, lvl, key, e.day)]
                if e.human:
                    c[0].add(pair)
                    c[2].add(e.deal)
                else:
                    c[1].add(pair)
                    c[3].add(e.deal)
        self.cells = collections.defaultdict(dict)  # type: Dict[Tuple[str, str, str], Dict[str, Cell]]
        for (fk, lvl, key, day), c in raw.items():
            self.cells[(fk, lvl, key)][day] = Cell(len(c[0]), len(c[1]), frozenset(c[2]), frozenset(c[2] | c[3]))
        self._incep = {}  # cache: (scope, fk, lvl, key) -> (sorted days, cumulative all, cumulative human)

    def _build_cohort_cells(self) -> None:
        """Cells restricted to the pipeline's report cohort (v_deal_cohort.in_scope), only for pipelines that define a cohort."""
        self.in_scope = {}  # (deal, pipeline) -> bool
        joined = {}
        for e in self.events:
            joined.setdefault((e.deal, e.pipeline), e.ts)
        for (deal, pipeline), ts in joined.items():
            d = self.deals[deal]
            slug = self.slug_of.get((d.account, pipeline))
            if slug not in self.cohort_slugs:
                continue
            pc = self.pipeline_cfg[slug]
            jd = ist_day_of(ts)
            ok = True
            if pc["cohort_start"] and jd < pc["cohort_start"]:
                ok = False
            elif pc["cohort_exclude_migration_on"] and jd == pc["cohort_exclude_migration_on"] and (d.created is None or ist_day_of(d.created) < pc["cohort_exclude_migration_on"]):
                ok = False
            self.in_scope[(deal, pipeline)] = ok
        raw = collections.defaultdict(lambda: [set(), set(), set(), set()])
        for e in self.events:
            slug = self.slug_of[(e.account, e.pipeline)]
            if slug not in self.cohort_slugs or not self.in_scope.get((e.deal, e.pipeline)):
                continue
            st = self.stage(e)
            code = st.canonical or UNMAPPED
            keys = [(slug, "c", code), (slug, "s", st.group_stage)]
            if st.dead_reason:
                keys.append((slug, "r", st.dead_reason))
            pair = (e.deal, e.actor)
            for fk, lvl, key in keys:
                c = raw[(fk, lvl, key, e.day)]
                if e.human:
                    c[0].add(pair); c[2].add(e.deal)
                else:
                    c[1].add(pair); c[3].add(e.deal)
        self.cells_in = collections.defaultdict(dict)
        for (fk, lvl, key, day), c in raw.items():
            self.cells_in[(fk, lvl, key)][day] = Cell(len(c[0]), len(c[1]), frozenset(c[2]), frozenset(c[2] | c[3]))

    def _build_derived(self) -> None:
        """MEETING_HELD and EVALUATED: per-deal derived rungs (no native stage).  See config/report.yaml metrics.meetings_held / evaluated."""
        per_deal = collections.defaultdict(list)
        for e in self.events:
            per_deal[e.deal].append(e)
        held_reasons = ("REJECTED_BY_LH2", "NOT_INTERESTED", "PRIVACY")
        self.derived = collections.defaultdict(list)   # (fk, code) -> [(deal, credit_day, evidence_day, bad_day, human)]
        depth = {c: s["depth"] for c, s in self.canon.items()}
        for deal, evs in per_deal.items():
            infos = [(e, self.stage(e)) for e in evs]
            bad_day = None
            for e, st in infos:
                if st.dead_reason in ("NO_SHOW", "MEETING_CANCELLED"):
                    bad_day = e.day
                    break
            # MEETING_HELD
            fb = next(((i, e) for i, (e, st) in enumerate(infos) if st.canonical == "MEETING_BOOKED"), None)
            if fb is not None:
                for e2, st2 in infos[fb[0] + 1:]:
                    live_asset = st2.canonical and not st2.is_dead and depth.get(st2.canonical, 0) >= 6
                    dead_after = st2.is_dead and st2.dead_reason in held_reasons and (st2.depth_reached or 0) >= 5
                    if live_asset or dead_after:
                        self._add_derived(deal, "MEETING_HELD", fb[1], e2.day, bad_day)
                        break
            # EVALUATED
            ar = next((i for i, (e, st) in enumerate(infos) if st.canonical == "ASSET_RECEIVED"), None)
            if ar is not None:
                for e2, st2 in infos[ar + 1:]:
                    dead_q = st2.is_dead and (st2.depth_reached or 0) >= 8
                    live_next = st2.canonical and not st2.is_dead and depth.get(st2.canonical, 0) >= 9
                    if dead_q or live_next:
                        self._add_derived(deal, "EVALUATED", e2, e2.day, None)
                        break

    def _add_derived(self, deal: int, code: str, credit_ev: Event, evidence_day: str, bad_day: Optional[str]) -> None:
        slug = self.slug_of[(credit_ev.account, credit_ev.pipeline)]
        rec = (deal, credit_ev.day, evidence_day, bad_day, credit_ev.human)
        self.derived[(slug, code)].append(rec)
        self.derived[(ALL, code)].append(rec)

    def _build_first_human(self) -> None:
        self.first_human = {}   # deal -> (day, actor owner, ts)
        self.first_bad = {}     # deal -> first IST day of a BAD_CONTACT dead entry
        for e in self.events:
            if e.human and e.deal not in self.first_human:
                self.first_human[e.deal] = (e.day, e.actor, e.ts)
            if e.deal not in self.first_bad and self.stage(e).dead_reason == "BAD_CONTACT":
                self.first_bad[e.deal] = e.day

    # ------------------------------------------------------------------------------------------------ window queries
    def _derived_cell(self, fk: str, code: str, day: str, D: str) -> Cell:
        nh = na = 0
        hd = set(); alld = set()
        for deal, credit, ev_day, bad, human in self.derived.get((fk, code), ()):
            if credit != day or ev_day > D or (bad is not None and bad <= ev_day):
                continue
            alld.add(deal)
            if human:
                nh += 1; hd.add(deal)
            else:
                na += 1
        return Cell(nh, na, frozenset(hd), frozenset(alld))

    def cell(self, fk: str, lvl: str, key: str, day: str, D: str, scope: str = "all") -> Cell:
        if lvl == "c" and key in ("MEETING_HELD", "EVALUATED"):
            return self._derived_cell(fk, key, day, D)
        store = self.cells_in if scope == "in_scope" else self.cells
        return store.get((fk, lvl, key), {}).get(day, EMPTY)

    def window(self, fk: str, lvl: str, key: str, lo: str, hi: str, D: str, scope: str = "all") -> Dict[str, int]:
        """Entries (summed over days) and distinct deals (union over days) in [lo, hi]; days after D are never read."""
        hi = min(hi, D)
        nh = na = 0
        hd = set(); alld = set()
        if lvl == "c" and key in ("MEETING_HELD", "EVALUATED"):
            for deal, credit, ev_day, bad, human in self.derived.get((fk, key), ()):
                if lo <= credit <= hi and ev_day <= D and not (bad is not None and bad <= ev_day):
                    alld.add(deal)
                    if human:
                        nh += 1; hd.add(deal)
                    else:
                        na += 1
        else:
            store = self.cells_in if scope == "in_scope" else self.cells
            days = store.get((fk, lvl, key), {})
            for d in day_range(lo, hi) if lo <= hi else ():
                c = days.get(d)
                if c:
                    nh += c.nh; na += c.na; hd |= c.hd; alld |= c.alld
        return {"human": nh, "auto": na, "total": nh + na, "deals_human": len(hd), "deals_all": len(alld)}

    def inception(self, fk: str, lvl: str, key: str, D: str, scope: str = "all") -> Dict[str, int]:
        """Distinct deals that EVER entered the rung / stage up to D (all time, never re-seeded), any source, with the human-only subset."""
        if lvl == "c" and key in ("MEETING_HELD", "EVALUATED"):
            alld = set(); hd = set()
            for deal, credit, ev_day, bad, human in self.derived.get((fk, key), ()):
                if credit <= D and ev_day <= D and not (bad is not None and bad <= ev_day):
                    alld.add(deal)
                    if human:
                        hd.add(deal)
            return {"deals_all": len(alld), "deals_human": len(hd)}
        ck = (scope, fk, lvl, key)
        ent = self._incep.get(ck)
        if ent is None:
            store = self.cells_in if scope == "in_scope" else self.cells
            days = sorted(store.get((fk, lvl, key), {}))
            seen_all = set(); seen_h = set(); cum_all = []; cum_h = []
            for d in days:
                c = store[(fk, lvl, key)][d]
                seen_all |= c.alld; seen_h |= c.hd
                cum_all.append(len(seen_all)); cum_h.append(len(seen_h))
            ent = (days, cum_all, cum_h)
            self._incep[ck] = ent
        days, cum_all, cum_h = ent
        i = bisect.bisect_right(days, D)
        return {"deals_all": cum_all[i - 1] if i else 0, "deals_human": cum_h[i - 1] if i else 0}

    # --------------------------------------------------------------------------------------------------- basis, coverage
    def basis(self, code: Optional[str]) -> str:
        """per_rung: entry and derived rungs count any source, everything else human only."""
        s = self.canon.get(code or "")
        if s and (s.get("is_entry") or s.get("is_derived")):
            return "any"
        return "human"

    def headline(self, w: Dict[str, int], basis: str) -> int:
        return w["total"] if basis == "any" else w["human"]

    def coverage(self, lo: str, hi: str, D: str, accounts: Optional[Iterable[str]] = None) -> Dict[str, Any]:
        """Which days of [lo, hi] the store + sync can speak for: store start <= day <= the IST day of the oldest relevant account sync."""
        accs = list(accounts) if accounts else list(self.sync)
        ends = [ist_day_of(self.sync[a]["last_success_at"]) for a in accs if a in self.sync and self.sync[a]["last_success_at"]]
        end = min(ends) if ends else None
        days = day_range(lo, hi)
        cov = [d for d in days if self.first_day and d >= self.first_day and end and d <= end and d <= D]
        return {"days": len(days), "covered": len(cov), "missing": [d for d in days if d not in cov]}

    # ------------------------------------------------------------------------------------------------------ occupancy
    def occupancy(self, D: str) -> Dict[Tuple[str, str, str], int]:
        """Deals sitting in each (account, pipeline, stage) at the END of D, archived deals excluded.  For the day the deal table reflects
        (or later) this is deal.stage_id; for earlier days it is reconstructed from the event store (last event <= D)."""
        return self._occ(D)[0]

    def _occ(self, D: str):
        if getattr(self, "_occ_cache", None) and self._occ_cache[0] == D:
            return self._occ_cache[1]
        counts = collections.Counter()
        state = {}  # deal -> (account, pipeline, stage)
        if self.live_day is not None and D >= self.live_day:
            for d in self.deals.values():
                if not d.archived and (d.account, d.pipeline) in self.slug_of:
                    state[d.deal] = (d.account, d.pipeline, d.stage)
        else:
            for e in self.events:
                if e.day > D:
                    break
                state[e.deal] = (e.account, e.pipeline, e.stage)
            for deal in list(state):
                d = self.deals[deal]
                if d.archived and (d.archived_at is None or ist_day_of(d.archived_at) <= D):
                    del state[deal]
        for st in state.values():
            counts[st] += 1
        self._occ_cache = (D, (dict(counts), state))
        return self._occ_cache[1]

    def deal_state(self, D: str) -> Dict[int, Tuple[str, str, str]]:
        return self._occ(D)[1]

    # ----------------------------------------------------------------------------------------------------- engaged
    def events_in(self, lo: str, hi: str, human_only: bool = True, fk: Optional[str] = None) -> List[Event]:
        out = []
        for d in day_range(lo, hi):
            for e in self.by_day.get(d, ()):
                if human_only and not e.human:
                    continue
                if fk and fk != ALL and self.slug_of[(e.account, e.pipeline)] != fk:
                    continue
                out.append(e)
        return out

    def engaged_deals(self, fk: str, lo: str, hi: str, D: str) -> Set[int]:
        return set(e.deal for e in self.events_in(lo, min(hi, D), True, fk))

    def net_new(self, fk: str, day: str) -> Dict[int, Optional[int]]:
        """Deals whose earliest-ever human event is on `day` (in this funnel), minus the reject set (BAD_CONTACT entered on or before `day`)."""
        out = {}
        for e in self.by_day.get(day, ()):
            if not e.human:
                continue
            if fk != ALL and self.slug_of[(e.account, e.pipeline)] != fk:
                continue
            fh = self.first_human.get(e.deal)
            if fh and fh[0] == day and fh[2] == e.ts:
                bad = self.first_bad.get(e.deal)
                if bad is not None and bad <= day:
                    continue
                out[e.deal] = e.actor
        return out


# ====================================================================================================== METRIC FUNCTIONS
# One per `fn:` in config/report.yaml.  They take the Engine and the report day D and return plain JSON-able structures.

def _window_bounds(cfg: Dict[str, Any], D: str) -> Dict[str, Any]:
    n = int(cfg["window"]["days"])
    return {"today": D, "yesterday": day_add(D, -1), "roll7": [day_add(D, -(n - 1)), D], "prev7": [day_add(D, -(2 * n - 1)), day_add(D, -n)], "n": n}


def _stat_block(eng: Engine, fk: str, lvl: str, key: str, D: str, basis: str, scope: str = "all") -> Dict[str, Any]:
    wb = _window_bounds(eng.cfg, D)
    blocks = {}
    for name, (lo, hi) in (("today", (D, D)), ("yesterday", (wb["yesterday"], wb["yesterday"])), ("roll7", tuple(wb["roll7"])), ("prev7", tuple(wb["prev7"]))):
        w = eng.window(fk, lvl, key, lo, hi, D, scope)
        w["headline"] = eng.headline(w, basis)
        blocks[name] = w
    inc = eng.inception(fk, lvl, key, D, scope)
    blocks["inception"] = inc
    return blocks


def rung_entries(eng: Engine, fk: str, code: str, D: str, scope: str = "all") -> Dict[str, Any]:
    """metric rung_entries: the Today / Yesterday / Roll7 / Prev7 entries of a canonical rung (headline basis per_rung) + Inception."""
    return _stat_block(eng, fk, "c", code, D, eng.basis(code), scope)


def deals_entered(eng: Engine, fk: str, lvl: str, key: str, D: str) -> Dict[str, int]:
    """metric deals_entered: distinct deals that entered in Today / Roll7 (union)."""
    wb = _window_bounds(eng.cfg, D)
    t = eng.window(fk, lvl, key, D, D, D)
    r = eng.window(fk, lvl, key, wb["roll7"][0], wb["roll7"][1], D)
    return {"today_all": t["deals_all"], "today_human": t["deals_human"], "roll7_all": r["deals_all"], "roll7_human": r["deals_human"]}


def inception(eng: Engine, fk: str, lvl: str, key: str, D: str, scope: str = "all") -> Dict[str, int]:
    """metric inception / inception_in_scope / won_to_date: distinct deals ever (all time up to D)."""
    return eng.inception(fk, lvl, key, D, scope)


def leads_assigned(eng: Engine, fk: str, D: str) -> Dict[str, Any]:
    """metric leads_assigned: entries into the entry rung SOURCED (any source) + distinct new deals (first-ever stage entry on that day)."""
    out = rung_entries(eng, fk, "SOURCED", D)
    first = {}
    for e in eng.events:
        if e.day > D:
            break
        if e.deal not in first:
            first[e.deal] = e
    wb = _window_bounds(eng.cfg, D)
    newd = collections.Counter()
    for deal, e in first.items():
        if fk == ALL or eng.slug_of[(e.account, e.pipeline)] == fk:
            newd[e.day] += 1
    out["new_deals"] = {"today": newd.get(D, 0), "yesterday": newd.get(wb["yesterday"], 0),
                        "roll7": sum(newd.get(d, 0) for d in day_range(*wb["roll7"])), "prev7": sum(newd.get(d, 0) for d in day_range(*wb["prev7"]))}
    return out


def engaged(eng: Engine, fk: str, D: str) -> Dict[str, Any]:
    """metrics engaged_daily / engaged_7d / net_new_engaged for a funnel (or ALL): distinct human-touched deals; 7-day = set UNION."""
    wb = _window_bounds(eng.cfg, D)
    t = eng.engaged_deals(fk, D, D, D)
    y = eng.engaged_deals(fk, wb["yesterday"], wb["yesterday"], D)
    r = eng.engaged_deals(fk, wb["roll7"][0], wb["roll7"][1], D)
    p = eng.engaged_deals(fk, wb["prev7"][0], wb["prev7"][1], D)
    nn = eng.net_new(fk, D)
    accounts = None if fk == ALL else [eng.pipe_of[fk][0]]
    cov = eng.coverage(wb["roll7"][0], wb["roll7"][1], D, accounts)
    covp = eng.coverage(wb["prev7"][0], wb["prev7"][1], D, accounts)
    return {"today": len(t), "yesterday": len(y), "roll7_union": len(r), "prev7_union": len(p), "net_new_today": len(nn),
            "roll7_days_covered": cov["covered"], "prev7_days_covered": covp["covered"],
            "change_today": change(len(y), len(t), True, eng.cfg["window"]["pct_min_prev"]),
            "change_roll7": change(len(p), len(r), cov["covered"] == wb["n"] and covp["covered"] == wb["n"], eng.cfg["window"]["pct_min_prev"])}


def occupancy(eng: Engine, D: str, fk: str = ALL) -> Dict[str, Any]:
    """metric occupancy_now: deals sitting in each canonical rung (dead split by reason) at the end of D, archived excluded."""
    occ = eng.occupancy(D)
    by_code = collections.Counter(); by_reason = collections.Counter(); live = dead = won = unmapped = total = 0
    for (a, p, s), n in occ.items():
        slug = eng.slug_of.get((a, p))
        if slug is None or (fk != ALL and slug != fk):
            continue
        st = eng.stages[(a, p, s)]
        total += n
        by_code[st.canonical or UNMAPPED] += n
        if st.dead_reason:
            by_reason[st.dead_reason] += n
        if not st.is_mapped:
            unmapped += n
        elif st.is_dead:
            dead += n
        elif st.is_won:
            won += n
        else:
            live += n
    return {"total": total, "live": live, "dead": dead, "won": won, "unmapped": unmapped, "by_code": dict(by_code), "by_reason": dict(by_reason)}


def dead_breakdown(eng: Engine, fk: str, D: str, occ: Optional[Dict[str, Any]] = None, scope: str = "all") -> List[Dict[str, Any]]:
    """metric dead_by_reason: per canonical dead reason (from stage_map): now, Today/Yest/Roll7/Prev7 human entries, Inception."""
    occ = occ or occupancy(eng, D, fk)
    rows = []
    for rc in eng.reasons:
        blk = _stat_block(eng, fk, "r", rc, D, "human", scope)
        rows.append({"reason": rc, "label": eng.reason_label[rc], "now": occ["by_reason"].get(rc, 0), **blk})
    return rows


def derived_rungs(eng: Engine, fk: str, D: str) -> Dict[str, Any]:
    """metrics meetings_held / evaluated: the two derived rungs (distinct deals, any source)."""
    return {c: rung_entries(eng, fk, c, D) for c in ("MEETING_HELD", "EVALUATED")}


def classify_country(label: Optional[str], rules: Sequence[Dict[str, str]], unmatched: str) -> str:
    if not label:
        return unmatched
    for r in rules:
        if re.search(r["pattern"], label, re.I):
            return r["country"]
    return unmatched


def cold_lead_country(eng: Engine, D: str) -> Dict[str, Any]:
    """metric cold_lead_country: live deals in the CoOps (Global) entry stage, by country derived from the lead_source label prefix."""
    spec = eng.cfg["cold_lead_split"]
    out = {"funnel": spec["funnel"], "total": 0, "by_country": {}, "by_lead_source": [], "as_of": "last sync (deal table)", "stage_ids": []}
    if spec["funnel"] not in eng.pipe_of:
        return out
    acct, pipe = eng.pipe_of[spec["funnel"]]
    sids = sorted(k[2] for k, st in eng.stages.items() if k[0] == acct and k[1] == pipe and st.canonical == spec["rung"] and not st.is_deleted)
    out["stage_ids"] = sids
    state = eng.deal_state(D)
    cnt = collections.Counter(); per_src = collections.Counter()
    for deal, (a, p, s) in state.items():
        if a == acct and p == pipe and s in sids:
            lab = eng.deals[deal].lead_source
            c = classify_country(lab, spec["rules"], spec["unmatched"])
            cnt[c] += 1
            per_src[(c, lab or "(no lead source)")] += 1
    out["total"] = sum(cnt.values())
    order = list(spec["order"])
    out["by_country"] = {c: cnt[c] for c in order if cnt.get(c)}
    for c in cnt:
        if c not in out["by_country"]:
            out["by_country"][c] = cnt[c]
    out["by_lead_source"] = [{"country": c, "lead_source": l, "deals": n} for (c, l), n in sorted(per_src.items(), key=lambda kv: (order.index(kv[0][0]) if kv[0][0] in order else 99, -kv[1], kv[0][1]))]
    return out


# ------------------------------------------------------------------------------------------------------- integrity
def integrity(eng: Engine, D: str) -> Dict[str, Any]:
    con = eng.con
    wb = _window_bounds(eng.cfg, D)
    fresh = []
    for acct in sorted(eng.sync):
        s = eng.sync[acct]
        ls = s["last_success_at"]
        fresh.append({"account": acct, "last_success_at": ls, "last_success_ist": ist_hhmm_of(ls) if ls else None, "status": s["status"],
                      "rows_synced": s["rows_synced"], "day_complete": False,
                      "lag_days": (parse_day(D) - parse_day(ist_day_of(ls))).days if ls else None})
    for f in fresh:   # day_complete: the sync happened after the end of the report day (IST)
        ls = f["last_success_at"]
        f["day_complete"] = bool(ls and ist_day_of(ls) > D)
    newest = collections.defaultdict(lambda: None)
    for e in eng.events:
        if e.day <= D:
            newest[e.account] = e.ts
    lb = int(eng.cfg["window"]["no_event_lookback_days"])
    days = day_range(day_add(D, -(lb - 1)), D)
    no_ev = []
    for d in days:
        if not any(e.day == d for e in eng.by_day.get(d, ())):
            wd = parse_day(d).weekday()
            no_ev.append({"day": d, "weekday": parse_day(d).strftime("%a"), "weekend": wd >= 5})
    unmapped = [dict(r) for r in con.execute("SELECT account_id, pipeline_id, stage_id, stage_label, is_deleted, label_source, deals_now, events FROM v_unmapped_stage ORDER BY 1,2,3")]
    archived = []
    for f in eng.funnels:
        n_arch = sum(1 for d in eng.deals.values() if d.archived and (d.account, d.pipeline) == (f["account"], f["pipeline"]))
        n_all = sum(1 for d in eng.deals.values() if (d.account, d.pipeline) == (f["account"], f["pipeline"]))
        archived.append({"funnel": f["slug"], "name": f["name"], "archived": n_arch, "total_rows": n_all, "live": n_all - n_arch})
    dq = [dict(r) for r in con.execute("SELECT rule_code, severity, issues, occurrences FROM v_dq_open WHERE severity IN ('warn','error') ORDER BY severity, rule_code")]
    dq_info = [dict(r) for r in con.execute("SELECT rule_code, severity, issues, occurrences FROM v_dq_open WHERE severity NOT IN ('warn','error') ORDER BY rule_code")]
    hist_rules = eng.cfg.get("history_failure_rules", [])
    hist = 0
    if hist_rules:
        q = "SELECT COALESCE(SUM(occurrences),0) FROM ops_dq_issue WHERE status IN ('open','acknowledged') AND rule_code IN (%s)" % ",".join("?" * len(hist_rules))
        hist = int(con.execute(q, hist_rules).fetchone()[0])
    amount_no_cost = [dict(r) for r in con.execute("SELECT account_id, COUNT(*) AS deals FROM deal WHERE amount IS NOT NULL AND cost_usd IS NULL AND is_archived = 0 GROUP BY 1 ORDER BY 1")]
    stock = [dict(r) for r in con.execute("SELECT p.account_id, p.pipeline_id, p.label, (SELECT COUNT(*) FROM deal d WHERE d.account_id=p.account_id AND d.pipeline_id=p.pipeline_id) AS deals, "
                                          "(SELECT COUNT(*) FROM deal_stage_event e WHERE e.account_id=p.account_id AND e.pipeline_id=p.pipeline_id) AS events "
                                          "FROM pipeline p WHERE p.is_reporting_funnel = 0 ORDER BY 1,2")]
    guess = [dict(r) for r in con.execute("SELECT account_id, pipeline_id, pipeline_basis, events, deals FROM v_event_pipeline_guess ORDER BY 1,2,3")]
    drift = int(con.execute("SELECT COUNT(*) FROM v_deal_state_drift").fetchone()[0])
    dup = int(con.execute("SELECT COUNT(*) FROM v_dse_near_duplicate").fetchone()[0])
    nostages = [dict(r) for r in con.execute("SELECT account_id, pipeline_id, label FROM v_pipeline_without_stages")]
    last_run = con.execute("SELECT stage_map_sha256 FROM rpt_report_run WHERE status = 'succeeded' AND stage_map_sha256 IS NOT NULL ORDER BY report_run_id DESC LIMIT 1").fetchone()
    sha_changed = bool(last_run and last_run[0] != eng.sha)
    anchors = []
    for a in eng.cfg.get("anchors", []):
        if a["day"] != D:
            continue
        if "split" in a:
            cl = cold_lead_country(eng, D)
            ok = cl["total"] == a["total"] and all(cl["by_country"].get(k, 0) == v for k, v in a["split"].items()) and set(cl["by_country"]) <= set(a["split"])
            anchors.append({"id": a["id"], "description": a["description"], "expected": dict(a["split"], total=a["total"]), "actual": dict(cl["by_country"], total=cl["total"]), "ok": ok})
        elif "funnel_deals" in a:
            actual = {}
            for f in eng.funnels:
                actual[f["slug"]] = sum(1 for d in eng.deals.values() if (d.account, d.pipeline) == (f["account"], f["pipeline"]) and not d.archived)
            anchors.append({"id": a["id"], "description": a["description"], "expected": dict(a["funnel_deals"]), "actual": actual, "ok": actual == a["funnel_deals"]})
    return {"freshness": fresh, "newest_event": dict(sorted((k, v) for k, v in newest.items())),
            "store": {"first_event_day": eng.first_day, "last_event_day": eng.last_day, "events": len(eng.events)},
            "days_without_events": no_ev, "unmapped_stages": unmapped, "archived": archived,
            "dq_warn_error": dq, "dq_info": dq_info, "history_failures": hist, "amount_without_cost": amount_no_cost,
            "ignored_pipelines": stock, "events_outside_reporting_pipelines": eng.nonreporting_events, "events_unknown_stage": eng.unknown_stage_events,
            "pipeline_guess_events": guess, "deal_state_drift": drift, "near_duplicate_events": dup, "pipelines_without_stages": nostages,
            "stage_map_changed_since_last_run": sha_changed, "anchors": anchors, "windows": wb, "lookback_days": lb}


# ===================================================================================================== CROSS-CHECK
# Recomputed values vs the imported legacy snapshots (rpt_legacy_snapshot).  The legacy numbers are never used for anything else, and
# neither side is ever adjusted: every difference is reported with the first hypothesis (a legacy mechanic) that reproduces the legacy
# value from the event store, or "unexplained".  Docs: docs/REPORTS.md "Cross-check".

CUTOFFS = ("18:30", "19:30", "20:30")


def _ist_min(ts: str) -> int:
    return (int(ts[11:13]) * 60 + int(ts[14:16]) + IST_MINUTES) % 1440


def _hhmm_to_min(s: str) -> int:
    return int(s[:2]) * 60 + int(s[3:5])


def _label_groups(eng: Engine, slugs: Sequence[str], label: str, dead_only: bool = False) -> Set[Tuple[str, str, str]]:
    """Native stage groups (account, pipeline, group_stage) whose label equals `label` (case-insensitive) inside the given funnels; used ONLY to
    translate the legacy reports' own vocabulary.  dead_only: every stage whose label starts with 'Dead' (the legacy 'Dead' row)."""
    out = set()
    for (a, p, s), st in eng.stages.items():
        if st.slug not in slugs:
            continue
        if dead_only:
            if st.is_dead and st.label.lower().startswith("dead"):
                out.add((a, p, st.group_stage))
        elif st.label.strip().lower() == label.strip().lower():
            out.add((a, p, st.group_stage))
    return out


def _group_of(eng: Engine, e: Event) -> Tuple[str, str, str]:
    return (e.account, e.pipeline, eng.stage(e).group_stage)


def _day_group_index(eng: Engine, D: str) -> Dict[Tuple[str, str, str], List[Event]]:
    cache = getattr(eng, "_dgi", None)
    if cache is None:
        cache = eng._dgi = {}
    if D not in cache:
        idx = collections.defaultdict(list)
        for e in eng.by_day.get(D, ()):
            idx[_group_of(eng, e)].append(e)
        cache[D] = idx
    return cache[D]


_owner_hs_cache = {}  # type: Dict[Tuple[int, int], Optional[str]]


def _owner_hs(eng: Engine, owner_id: Optional[int]) -> str:
    """HubSpot owner id of an owner row ('unassigned' for none) - the vocabulary of the per-owner legacy series."""
    if owner_id is None:
        return "unassigned"
    key = (id(eng), owner_id)
    if key not in _owner_hs_cache:
        r = eng.con.execute("SELECT hs_owner_id FROM owner WHERE owner_id = ?", (owner_id,)).fetchone()
        _owner_hs_cache[key] = r[0] if r else "unassigned"
    return _owner_hs_cache[key]


def _mine_flow(eng: Engine, D: str, groups: Set[Tuple[str, str, str]], any_source: bool, owner_hs: Optional[str], hyp: Dict[str, Any]) -> int:
    seen = set(); n = 0
    cutoff = hyp.get("cutoff"); utc = hyp.get("utc"); arch = hyp.get("archived_excl"); raw = hyp.get("raw"); by_deal_owner = hyp.get("deal_owner")
    cm = _hhmm_to_min(cutoff) if cutoff else None
    idx = _day_group_index(eng, D)
    for g in groups:
        for e in idx.get(g, ()):
            if not (e.human or any_source):
                continue
            if owner_hs is not None:
                who = _owner_hs(eng, eng.owner_at(e.deal, D)) if by_deal_owner else _owner_hs(eng, e.actor)
                if who != owner_hs:
                    continue
            if arch and eng.deals[e.deal].archived:
                continue
            m = _ist_min(e.ts)
            if utc and m < 330:
                continue
            if cm is not None and m >= cm:
                continue
            if raw:
                n += 1
            else:
                k = (e.deal, g, e.actor, e.human)
                if k not in seen:
                    seen.add(k); n += 1
    return n


def _state_counts(eng: Engine, D: str, cutoff: Optional[str], as_of_owner: bool):
    """(Counter by group, Counter by (group, owner hs id)) of the deals sitting in each native group at the end of D (or at `cutoff` IST on D);
    the owner is the owner as of D (deal_owner_event) when as_of_owner, else the current owner."""
    cache = getattr(eng, "_stc", None)
    if cache is None:
        cache = eng._stc = {}
    k = (D, cutoff, as_of_owner)
    if k not in cache:
        cm = _hhmm_to_min(cutoff) if cutoff else None
        state = {}
        for e in eng.events:
            if e.day > D:
                break
            if e.day == D and cm is not None and _ist_min(e.ts) >= cm:
                continue
            state[e.deal] = e
        by_g = collections.Counter(); by_go = collections.Counter()
        for deal, e in state.items():
            d = eng.deals[deal]
            if d.archived and (d.archived_at is None or ist_day_of(d.archived_at) <= D):
                continue
            g = _group_of(eng, e)
            by_g[g] += 1
            by_go[(g, _owner_hs(eng, eng.owner_at(deal, D) if as_of_owner else d.owner))] += 1
        cache[k] = (by_g, by_go)
    return cache[k]


def _mine_state(eng: Engine, D: str, groups: Set[Tuple[str, str, str]], owner_hs: Optional[str], cutoff: Optional[str], as_of_owner: bool = True) -> int:
    by_g, by_go = _state_counts(eng, D, cutoff, as_of_owner)
    if owner_hs is None:
        return sum(by_g.get(g, 0) for g in groups)
    return sum(by_go.get((g, owner_hs), 0) for g in groups)


_SWITCH_TEXT = collections.OrderedDict([
    ("deal_owner", "legacy attributes a move to the deal's owner, the recomputation credits the actor"),
    ("raw", "legacy counts raw history entries (no de-dup on deal+stage+actor+day)"),
    ("archived_excl", "archived deals are invisible to the legacy HubSpot search; the event store keeps their moves"),
    ("utc", "legacy candidate pre-filter used UTC midnight, so moves 00:00-05:30 IST were never candidates"),
])


def _hyp_list(with_owner: bool) -> List[Tuple[Dict[str, Any], str]]:
    import itertools
    switches = (["deal_owner"] if with_owner else []) + ["raw", "archived_excl", "utc"]
    out = []
    for cut in (None,) + CUTOFFS:
        for r in range(0, len(switches) + 1):
            for combo in itertools.combinations(switches, r):
                if cut is None and r == 0:
                    continue
                h = {k: True for k in combo}
                parts = [_SWITCH_TEXT[k] for k in combo]
                if cut:
                    h["cutoff"] = cut
                    parts.insert(0, "legacy snapshot was written before %s IST; later moves of the day are in the event store" % cut)
                out.append((h, "; ".join(parts)))
    out.sort(key=lambda t: (len(t[0]), 1 if "cutoff" in t[0] else 0))
    return out


_HYPS = {True: _hyp_list(True), False: _hyp_list(False)}
SCOPE_NOTES = {
    "main_coding": "known scope difference: the legacy Coding report selects deals by lead_source IN CODING_SOURCES (11 sources), the unified report by pipeline id (docs/discovery/funnel-reports.md H4); not reproducible from the pipeline-scoped event store",
    "main_owner": "per-owner legacy series: the legacy attribution of owner / actor at snapshot time cannot be reproduced exactly",
}


def _explain_flow(eng: Engine, D: str, groups, any_source: bool, owner_hs, legacy: float, mine: int, fam: str, alt_groups=None) -> str:
    if mine == legacy:
        return "match"
    for hyp, why in _HYPS[owner_hs is not None]:
        if _mine_flow(eng, D, groups, any_source, owner_hs, hyp) == legacy:
            return why
    if alt_groups is not None and _mine_flow(eng, D, alt_groups, any_source, owner_hs, {}) == legacy:
        return "legacy whole-portal report knows only the old Scraped/Campaign stage ids; the new CoOps (Global) dead stages are not in its id list"
    if fam in SCOPE_NOTES:
        return SCOPE_NOTES[fam]
    return "unexplained: no tested legacy mechanic reproduces the legacy value"


def _explain_state(eng: Engine, D: str, groups, owner_hs, legacy: float, mine: int, snap_bootstrap: bool, fam: str) -> str:
    if mine == legacy:
        return "match"
    for c in CUTOFFS:
        if _mine_state(eng, D, groups, owner_hs, c) == legacy:
            return "legacy snapshot was written before %s IST; the recomputed value is the end of the IST day" % c
    if owner_hs is not None and _mine_state(eng, D, groups, owner_hs, None, as_of_owner=False) == legacy:
        return "legacy used the current owner at snapshot time; recomputed uses the owner as of the day (deal_owner_event)"
    if snap_bootstrap:
        return "legacy snapshot is a bootstrap / seeded approximation"
    if fam in SCOPE_NOTES:
        return SCOPE_NOTES[fam]
    return "unexplained: no tested legacy mechanic reproduces the legacy value"


def _alt_groups(eng: Engine, fam: str, row: str, groups):
    """main_full 'Dead': restrict to the Coding pipeline (the legacy id list pre-dates the CoOps (Global) restage)."""
    if fam == "main_full" and row == "Dead" and "coding" in eng.pipe_of:
        a, p = eng.pipe_of["coding"]
        return set(g for g in groups if (g[0], g[1]) == (a, p))
    return None


def crosscheck(eng: Engine, D: str, families: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """metric legacy_crosscheck: compare against rpt_legacy_snapshot for the last `crosscheck_days` snapshot days <= D of every series."""
    con = eng.con
    n_days = int(eng.cfg.get("crosscheck_days", 5))
    out = {"available": False, "report_day": D, "series": [], "reason_counts": {}, "notes": []}  # type: Dict[str, Any]
    try:
        total = con.execute("SELECT COUNT(*) FROM rpt_legacy_snapshot").fetchone()[0]
    except sqlite3.Error:
        total = 0
    if not total:
        out["notes"].append("rpt_legacy_snapshot is empty: nothing to cross-check (load the legacy snapshots with the flat-file importer)")
        return out
    out["available"] = True
    rows_cfg = eng.cfg.get("legacy_rows", {})
    fams = list(families) if families else [r[0] for r in con.execute("SELECT DISTINCT source_family FROM rpt_legacy_snapshot WHERE source_family <> 'main_hubspot_mirror' ORDER BY 1")]
    reason_counts = collections.Counter()
    for fam in fams:
        series = [tuple(r) for r in con.execute("SELECT DISTINCT account_id, pipeline_id, owner_hs_id FROM rpt_legacy_snapshot WHERE source_family = ? ORDER BY 1, 2, 3", (fam,))]
        for acct, pipe, owner_hs in series:
            if pipe is not None:
                slugs = [eng.slug_of[(acct, pipe)]] if (acct, pipe) in eng.slug_of else []
            else:
                slugs = [s for s in eng.slugs if eng.pipe_of[s][0] == acct]
            snaps = con.execute("SELECT * FROM rpt_legacy_snapshot WHERE source_family = ? AND account_id = ? AND COALESCE(pipeline_id,'') = COALESCE(?, '') "
                                "AND COALESCE(owner_hs_id,'') = COALESCE(?, '') AND snapshot_day <= ? ORDER BY snapshot_day DESC LIMIT ?",
                                (fam, acct, pipe, owner_hs, D, n_days)).fetchall()
            s_out = {"family": fam, "account": acct, "pipeline_id": pipe, "owner_hs_id": owner_hs, "funnels": slugs, "days": []}
            for sn in reversed(snaps):
                day = sn["snapshot_day"]
                d_out = {"day": day, "snapshot_id": sn["snapshot_id"], "bootstrap": bool(sn["is_bootstrap"]), "seeded": bool(sn["is_seeded"]), "rows": []}
                fam_rows = rows_cfg.get(fam, {})
                any_rows = set(fam_rows.get("any_source_rows", []))
                # ---- dashboard_flow (rows -> native stage groups)
                if sn["dashboard_flow_json"]:
                    for label, val in json.loads(sn["dashboard_flow_json"]).items():
                        spec = fam_rows.get("rows", {}).get(label)
                        if spec is None:
                            d_out["rows"].append({"metric": "dashboard_flow", "row": label, "legacy": val, "mine": None, "delta": None, "reason": "legacy row not defined in config/report.yaml legacy_rows"})
                            continue
                        groups = set()
                        for lab in spec.get("stages", []):
                            groups |= _label_groups(eng, slugs, lab)
                        if spec.get("dead"):
                            groups |= _label_groups(eng, slugs, "", dead_only=True)
                        mine = _mine_flow(eng, day, groups, label in any_rows, owner_hs, {})
                        d_out["rows"].append({"metric": "dashboard_flow", "row": label, "legacy": val, "mine": mine, "delta": mine - val,
                                              "reason": _explain_flow(eng, day, groups, label in any_rows, owner_hs, val, mine, fam,
                                                                   _alt_groups(eng, fam, label, groups))})
                # ---- flow (native stage labels)
                if sn["flow_json"]:
                    for label, val in json.loads(sn["flow_json"]).items():
                        groups = _label_groups(eng, slugs, label)
                        if not groups:
                            d_out["rows"].append({"metric": "flow", "row": label, "legacy": val, "mine": None, "delta": None, "reason": "stage label not found in the pipelines of this series"})
                            continue
                        entry = any(eng.canon.get(eng.stages[(g[0], g[1], g[2])].canonical or "", {}).get("is_entry") for g in groups)
                        mine = _mine_flow(eng, day, groups, bool(entry), owner_hs, {})
                        d_out["rows"].append({"metric": "flow", "row": label, "legacy": val, "mine": mine, "delta": mine - val,
                                              "reason": _explain_flow(eng, day, groups, bool(entry), owner_hs, val, mine, fam)})
                # ---- current_state (occupancy at the end of the day)
                if sn["current_state_json"]:
                    for label, val in json.loads(sn["current_state_json"]).items():
                        groups = _label_groups(eng, slugs, label)
                        if not groups:
                            d_out["rows"].append({"metric": "current_state", "row": label, "legacy": val, "mine": None, "delta": None, "reason": "stage label not found in the pipelines of this series"})
                            continue
                        mine = _mine_state(eng, day, groups, owner_hs, None)
                        d_out["rows"].append({"metric": "current_state", "row": label, "legacy": val, "mine": mine, "delta": mine - val,
                                              "reason": _explain_state(eng, day, groups, owner_hs, val, mine, bool(sn["is_bootstrap"] or sn["is_seeded"]), fam)})
                # ---- cumulative (legacy chained event count vs recomputed distinct deals ever)
                if sn["cumulative_json"]:
                    for label, val in json.loads(sn["cumulative_json"]).items():
                        spec = fam_rows.get("rows", {}).get(label)
                        if spec is None:
                            continue
                        groups = set()
                        for lab in spec.get("stages", []):
                            groups |= _label_groups(eng, slugs, lab)
                        if spec.get("dead"):
                            groups |= _label_groups(eng, slugs, "", dead_only=True)
                        deals = set()
                        for (a, p, g) in groups:
                            sl = eng.slug_of.get((a, p))
                            for dd, c in eng.cells.get((sl, "s", g), {}).items():
                                if dd <= day:
                                    deals |= c.alld
                        why = ("legacy 'cumulative' is a chained event count re-seeded by bootstrap days (%s); recomputed is the distinct deals that ever entered "
                               "the stage(s), all time, from the event store" % ("this snapshot is flagged bootstrap/seeded" if (sn["is_bootstrap"] or sn["is_seeded"]) else "not flagged, but earlier days in the chain may be"))
                        d_out["rows"].append({"metric": "cumulative", "row": label, "legacy": val, "mine": len(deals), "delta": len(deals) - val,
                                              "reason": "match" if len(deals) == val else why})
                # ---- engaged ids
                if sn["engaged_deal_ids_json"] is not None:
                    legacy_ids = set(str(x) for x in json.loads(sn["engaged_deal_ids_json"]))
                    mine_evs = [e for e in eng.by_day.get(day, ()) if e.human and eng.slug_of[(e.account, e.pipeline)] in slugs]
                    mine_ids = {}
                    for e in mine_evs:
                        mine_ids.setdefault(eng.deals[e.deal].hs_id, []).append(e)
                    only_mine = sorted(set(mine_ids) - legacy_ids)
                    only_leg = sorted(legacy_ids - set(mine_ids))
                    cls_m = collections.Counter(); cls_l = collections.Counter()
                    for hs in only_mine:
                        evs = mine_ids[hs]
                        if all(eng.deals[e.deal].archived for e in evs):
                            cls_m["deal is archived (invisible to legacy search)"] += 1
                        elif all(_ist_min(e.ts) < 330 for e in evs):
                            cls_m["move between 00:00 and 05:30 IST (legacy UTC-midnight pre-filter)"] += 1
                        elif all(_ist_min(e.ts) >= _hhmm_to_min(CUTOFFS[0]) for e in evs):
                            cls_m["move after 18:30 IST (legacy snapshot written earlier)"] += 1
                        else:
                            cls_m["other"] += 1
                    hs_to_deal = None
                    for hs in only_leg:
                        if hs_to_deal is None:
                            hs_to_deal = {d.hs_id: d for d in eng.deals.values() if d.account == acct}
                        d = hs_to_deal.get(hs)
                        if d is None:
                            cls_l["deal not in the database"] += 1
                            continue
                        adj = [e for dd in (day_add(day, -1), day_add(day, 1)) for e in eng.by_day.get(dd, ()) if e.deal == d.deal and e.human]
                        has_auto = any((not e.human) and e.deal == d.deal for e in eng.by_day.get(day, ()))
                        if has_auto:
                            cls_l["only automated moves that day in the event store (legacy counted it as engaged)"] += 1
                        elif adj:
                            cls_l["human move on an adjacent IST day (day-boundary drift)"] += 1
                        else:
                            cls_l["no human move that day in the event store"] += 1
                    d_out["engaged"] = {"legacy": len(legacy_ids), "mine": len(mine_ids), "both": len(legacy_ids & set(mine_ids)), "only_legacy": len(only_leg),
                                        "only_mine": len(only_mine), "only_mine_classes": dict(sorted(cls_m.items())), "only_legacy_classes": dict(sorted(cls_l.items()))}
                n_rows = len(d_out["rows"])
                n_match = sum(1 for r in d_out["rows"] if r["reason"] == "match")
                d_out["summary"] = {"rows": n_rows, "match": n_match, "mismatch": n_rows - n_match}
                for r in d_out["rows"]:
                    if r["reason"] != "match":
                        reason_counts[r["reason"]] += 1
                s_out["days"].append(d_out)
            if s_out["days"]:
                out["series"].append(s_out)
    out["reason_counts"] = dict(sorted(reason_counts.items(), key=lambda kv: (-kv[1], kv[0])))
    return out
