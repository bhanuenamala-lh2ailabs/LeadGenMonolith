#!/usr/bin/env python3
"""EdTech ICP check, stage 5: merge queue + domains + crawl signals + classifier verdicts -> scored, tiered, ranked CSV.
Output: out/edtech_untouched_scored.csv and out/edtech_summary.md (docs/TASK_edtech_icp_check.md section 6).  No Apollo, no HubSpot.

Guards from the brief (each failed in production once):
  * a verdict with a NULL/invalid icp_bucket  -> funnel bucket classify_failed (retryable), never a silent pass;
  * a verdict whose id is not in its own batch -> ignored (foreign-id guard);
  * require_own_tech_product_for_fit: a Fit without an own tech product, or of type services/agency, is demoted to Maybe;
  * unknown headcount stays `unknown_headcount` (retryable), never a terminal 'too small';
  * nothing is deleted: every company gets a row with verdict, score and a named funnel bucket.
Ops-data lens (user, 2026-10-05): we want companies whose own pages show engineering/product operations artefacts (Jira, Confluence,
Slack, Notion, GitHub/GitLab, sprint/agile practice, API docs, release notes, a product team hiring developers), not coaching marketing."""
import csv, glob, hashlib, json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
D = ROOT / "data/inbox/edtech"; OUT = ROOT / "out"; OUT.mkdir(exist_ok=True)
OPS_TOOLS = {"jira", "confluence", "slack", "notion", "github", "gitlab", "bitbucket", "linear", "asana", "trello", "jenkins", "figma", "postman", "swagger"}
OPS_PRACTICE = {"agile", "scrum", "sprint", "kanban", "sdlc", "devops", "ci/cd", "code review", "product roadmap", "product management", "release cycle", "qa automation"}
OPS_ARTIFACT = {"api documentation", "api docs", "developer docs", "documentation", "changelog", "release notes", "roadmap"}


def main():
    rows = list(csv.DictReader(open(D / "edtech_campaign_untouched_594.csv")))
    doms = json.loads((D / "domains.json").read_text()); sig = json.loads((D / "signals.json").read_text())
    ver, batch_ids = {}, {}
    for f in sorted(glob.glob(str(D / "batches/batch_*.json"))):
        for it in json.load(open(f)): batch_ids.setdefault(Path(f).name, set()).add(it["id"])
    for f in sorted(glob.glob(str(D / "batches_web/web_*.json"))):
        for it in json.load(open(f)): batch_ids.setdefault("W:" + Path(f).name, set()).add(it["id"])
    for f in sorted(glob.glob(str(D / "verdicts_web/web_*.json"))):
        ok_ids = batch_ids.get("W:" + Path(f).name, set())
        try: data = json.load(open(f))
        except Exception: continue
        for v in data:
            if v.get("id") in ok_ids: v["_web"] = True; ver[v["id"]] = v           # foreign-id guard
    for f in sorted(glob.glob(str(D / "verdicts/batch_*.json"))):
        name = Path(f).name; ok_ids = batch_ids.get(name, set())
        try: data = json.load(open(f))
        except Exception: continue
        for v in data:
            if v.get("id") in ok_ids: ver[v["id"]] = v                      # foreign-id guard
    out = []
    for r in rows:
        c = r["company"]; cid = hashlib.md5(c.encode()).hexdigest()[:10]; s = sig.get(c, {}); d = doms.get(c) or {}
        v = ver.get(cid); bucket = s.get("funnel_bucket", "domain_unresolved")
        icp = (v or {}).get("icp_bucket"); conf = float((v or {}).get("confidence") or 0)
        ctype = (v or {}).get("company_type", ""); own = bool((v or {}).get("has_own_tech_product")); reason = (v or {}).get("out_reason", "")
        if v is None: funnel = "classify_pending" if bucket == "crawled" else bucket
        elif icp == "Unresolved": funnel = "domain_unresolved"; icp = ""           # web research found no official site: retryable, never Out
        elif icp not in ("Fit", "Maybe", "Out"): funnel = "classify_failed"; icp = ""
        else: funnel = ""
        if icp == "Fit" and (not own or ctype in ("services", "agency")): icp = "Maybe"; reason = reason or "demoted: no own tech product (require_own_tech_product_for_fit)"
        vs = {str(x).lower() for x in (v or {}).get("ops_signals", [])} if v and v.get("_web") else set()
        tools = {k for k in (s.get("signals", {}).get("tooling") or {}) if k in OPS_TOOLS} | (vs & OPS_TOOLS); prac = {k for k in (s.get("signals", {}).get("practices") or {}) if k in OPS_PRACTICE} | {x for x in vs if x in OPS_PRACTICE}
        art = {k for k in (s.get("signals", {}).get("artifacts") or {}) if k in OPS_ARTIFACT} | {x for x in vs if x in OPS_ARTIFACT}; hiring = sum((s.get("signals", {}).get("hiring") or {}).values()) + (2 if any("hiring" in x for x in vs) else 0)
        acad = sum((s.get("signals", {}).get("academic") or {}).values())
        rich = int((v or {}).get("ops_data_richness") or 0)
        hc = s.get("headcount_page") or s.get("headcount_pages") or (v or {}).get("headcount_hint"); hc = int(hc) if hc else None
        tier = "unknown" if hc is None else ("A" if 20 <= hc <= 500 else "B" if 501 <= hc <= 1000 else "excluded")
        score = 0
        if icp:
            score = {"Fit": 60, "Maybe": 30, "Out": 0}[icp] + int(conf * 10) + 5 * min(rich, 3) + min(2 * len(tools), 10) + min(2 * len(prac), 6) + min(3 * len(art), 6) + (4 if hiring >= 2 else 0)
            score -= 10 if acad >= 12 else 0
            score += 5 if r["has_decision_maker"].lower() == "yes" else 0
            score = max(0, min(100, score))
        if not funnel:
            funnel = {"Out": "out_" + (("academic" if ctype == "academic" else "services" if ctype in ("services", "agency") else "other")), "Fit": "fit", "Maybe": "maybe"}[icp]
            if icp in ("Fit", "Maybe"):
                funnel += {"A": "_tier_A", "B": "_tier_B", "excluded": "_excluded_size", "unknown": "_unknown_headcount"}[tier]
        out.append(dict(company=c, resolved_domain=d.get("domain") or (v or {}).get("resolved_domain") or "", icp_bucket=icp or "", confidence=conf or "", company_type=ctype, has_own_tech_product=own if v else "",
                        headcount=hc or "", headcount_source=("own_pages" if (s.get("headcount_pages") or s.get("headcount_page")) else "web_search_hint") if hc else "", tier=tier, score=score, rank_in_tier="", out_reason=reason,
                        evidence_quote=(v or {}).get("evidence_quote", ""), evidence_url=(v or {}).get("evidence_url", ""), funnel_bucket=funnel,
                        ops_data_richness=rich if v else "", ops_signals="; ".join(sorted(tools | prac | art)) + (("; hiring" if hiring >= 2 else "")),
                        poc_name=r["poc_name"], poc_title=r["poc_title"], poc_linkedin=r["poc_linkedin"], likely_academic_or_coaching=r["likely_academic_or_coaching"]))
    for t in ("A", "B", "unknown", "excluded"):
        grp = sorted([o for o in out if o["tier"] == t and o["icp_bucket"] in ("Fit", "Maybe")], key=lambda o: -o["score"])
        for i, o in enumerate(grp, 1): o["rank_in_tier"] = i
    out.sort(key=lambda o: (o["icp_bucket"] not in ("Fit", "Maybe"), {"A": 0, "B": 1, "unknown": 2, "excluded": 3}[o["tier"]], -o["score"]))
    with open(OUT / "edtech_untouched_scored.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out[0].keys())); w.writeheader(); w.writerows(out)
    n = len(out); ic = Counter(o["icp_bucket"] or "(unclassified)" for o in out); fb = Counter(o["funnel_bucket"] for o in out); tr = Counter(o["tier"] for o in out)
    acad = sum(1 for o in out if o["funnel_bucket"] == "out_academic"); classified = sum(v for k, v in ic.items() if k != "(unclassified)")
    md = ["# EdTech untouched 594: ICP check summary", "", "Database written to: **none** (file-based under `data/inbox/edtech/`; `out/edtech_untouched_scored.csv` is the result). No Apollo, SignalHire spend or HubSpot write.", "",
          "## ICP bucket", *["- %s: %d (%.0f%%)" % (k, v, v * 100 / n) for k, v in ic.most_common()], "",
          "Calibration (brief section 3): classified %d; Fit %d (%.0f%% of classified); academic/coaching Out %d (%.0f%% of classified). The brief expects ~30%% academic and warns 70%%+ Fit means a miscalibrated classifier." % (
              classified, ic.get("Fit", 0), ic.get("Fit", 0) * 100 / max(classified, 1), acad, acad * 100 / max(classified, 1)), "",
          "## Tier (headcount from the companies' own pages only)", *["- %s: %d" % (k, v) for k, v in tr.most_common()], "",
          "## Funnel bucket (every outcome, failures included)", *["- %s: %d" % (k, v) for k, v in fb.most_common()]]
    (OUT / "edtech_summary.md").write_text("\n".join(md) + "\n"); print("\n".join(md))


if __name__ == "__main__":
    main()
