#!/usr/bin/env python3
"""EdTech ICP check, stage 3-4 preparation: from the crawl, extract free text signals per company and write classifier batch files.

Signals (regex over the company's OWN pages only, company-level facts, nothing about learners / parents / tutors - DPDP):
  tooling      jira, confluence, slack, notion, github, gitlab, bitbucket, linear, asana, trello, figma, jenkins, docker, kubernetes, aws/azure/gcp ...
  practices    agile, scrum, sprint, kanban, sdlc, devops, ci/cd, qa automation ...
  artifacts    api documentation, developer docs, changelog/release notes, product roadmap, saas/platform/app/lms/erp wording, 'our product'
  hiring       careers/openings for software engineer, developer, qa, product manager, devops ...
  academic     admission, affiliated, ugc, aicte, naac, campus, hostel, batches, coaching, tuition, jee, neet, upsc, placement ...
  headcount    'team of N', 'N+ employees' from the company's own pages
Outputs data/inbox/edtech/signals.json and data/inbox/edtech/batches/batch_XX.json (40 companies each, ~4k chars of excerpts)."""
import csv, hashlib, json, re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
D = ROOT / "data/inbox/edtech"; CR = D / "crawl"; BT = D / "batches"; BT.mkdir(exist_ok=True)

PAT = {
    "tooling": r"\b(jira|confluence|slack|notion|github|gitlab|bitbucket|linear|asana|trello|figma|jenkins|docker|kubernetes|terraform|datadog|sentry|postman|swagger|aws|azure|gcp|google cloud|firebase|mongodb|postgres|react|node\.?js|flutter|kotlin|python|java)\b",
    "practices": r"\b(agile|scrum|sprint|kanban|sdlc|devops|ci/cd|code review|qa automation|product management|product roadmap|release cycle)\b",
    "artifacts": r"\b(api documentation|api docs|developer docs|documentation|changelog|release notes|roadmap|our product|our platform|saas|lms|erp|learning management|mobile app|web app|dashboard|analytics platform|ai[- ]powered|edtech platform|software platform|white[- ]label)\b",
    "hiring": r"\b(software engineer|developer|full[- ]stack|backend|frontend|front[- ]end|devops|qa engineer|data engineer|product manager|ui/ux|android developer|ios developer|we are hiring|join our team|open positions|current openings)\b",
    "academic": r"\b(admissions?|affiliated|ugc|aicte|naac|campus|hostel|batches?|coaching|tuition|jee|neet|upsc|cat preparation|exam preparation|placement|faculty|b\.?tech|mba programme|diploma|certification course|classroom|offline classes|franchise|centres?)\b",
}
HC = [re.compile(p, re.I) for p in (r"team of (\d{2,4})", r"(\d{2,4})\+?\s*(?:employees|team members|people|professionals|engineers|experts)", r"over (\d{2,4})\s*(?:employees|team members)")]


def sig(text):
    out = {}
    for k, p in PAT.items():
        m = re.findall(p, text, re.I); out[k] = {}
        for x in m: out[k][x.lower()] = out[k].get(x.lower(), 0) + 1
    hc = None
    for rx in HC:
        for m in rx.finditer(text):
            n = int(m.group(1))
            if 5 <= n <= 20000: hc = max(hc or 0, n)
    return out, hc


def main():
    rows = {r["company"]: r for r in csv.DictReader(open(D / "edtech_campaign_untouched_594.csv"))}
    doms = json.loads((D / "domains.json").read_text()); crawl = {}
    for f in CR.glob("*.json"):
        c = json.loads(f.read_text()); crawl[c["domain"]] = c
    signals, items = {}, []
    for comp, row in rows.items():
        d = doms.get(comp) or {}; dom = d.get("domain")
        if not dom: signals[comp] = {"funnel_bucket": "domain_unresolved"}; continue
        c = crawl.get(dom)
        if not c: signals[comp] = {"funnel_bucket": "crawl_pending", "domain": dom}; continue
        pages = [p for p in c["pages"] if p.get("status") == 200 and p.get("text")]
        full = " ".join(p["text"] for p in pages); chars = len(full)
        s, hc = sig(full)
        bucket = "crawled" if chars >= 800 else "crawled_thin"
        signals[comp] = {"funnel_bucket": bucket, "domain": dom, "chars": chars, "pages_ok": len(pages), "signals": s, "headcount_pages": hc,
                         "sources": [p["url"] for p in pages]}
        if chars < 200: continue                       # nothing to classify; stays crawled_thin (retryable)
        ex = []
        for p in pages[:6]:
            ex.append("[%s] %s" % (p["url"], p["text"][:1500 if p is pages[0] else 800]))
        items.append({"id": hashlib.md5(comp.encode()).hexdigest()[:10], "company": comp, "location": row["location"], "domain": dom, "places_name": d.get("places_name"),
                      "likely_academic_or_coaching": row["likely_academic_or_coaching"], "all_titles_seen": row["all_titles_seen"][:200], "people_in_campaign": row["people_in_campaign"],
                      "signal_counts": {k: dict(sorted(v.items(), key=lambda kv: -kv[1])[:8]) for k, v in s.items()}, "headcount_from_pages": hc,
                      "excerpt": "\n".join(ex)[:4200]})
    (D / "signals.json").write_text(json.dumps(signals, indent=1))
    for f in BT.glob("batch_*.json"): f.unlink()
    items.sort(key=lambda x: x["company"].lower()); n = 0
    for i in range(0, len(items), 40):
        (BT / ("batch_%02d.json" % n)).write_text(json.dumps(items[i:i + 40], indent=1)); n += 1
    from collections import Counter
    print("buckets:", dict(Counter(v["funnel_bucket"] for v in signals.values())), "| classifiable:", len(items), "| batches:", n)


if __name__ == "__main__":
    main()
