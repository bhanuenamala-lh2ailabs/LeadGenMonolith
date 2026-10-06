#!/usr/bin/env python3
"""ICP check for the corpus companies that had no verdict: 43 in the TAM corpus never judged + 8 fuzzy TAM matches + 31 companies the name-match
could not link to anything.  No Apollo, no web-search budget: uses (1) page text already stored in the TAM database, (2) a robots-aware crawl of the
company's own site for what is missing (reusing edtech_icp_stage12), (3) for the 31, a domain guessed from the name and accepted only if the live page
contains the company name.  Writes data/inbox/edtech/batches_tam/tam_XX.json for the classifier agents (no web access needed)."""
import csv, hashlib, json, sqlite3, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
import edtech_icp_stage12 as S12          # crawl(), guess_domain()
import edtech_icp_stage34_prepare as S34  # sig()
from leadgen import norm

D = ROOT / "data/inbox/edtech"; OUT = D / "batches_tam"; OUT.mkdir(exist_ok=True); (D / "verdicts_tam").mkdir(exist_ok=True)


def main():
    final = list(csv.DictReader(open(ROOT / "out/cluster1_icp_matched_800.csv")))
    scored = {r["company"] for r in csv.DictReader(open(ROOT / "out/edtech_untouched_scored.csv"))}
    match = json.loads((ROOT / "data/audit/the42_match.json").read_text())
    people = {}
    for r in csv.DictReader(open(D / "campaign_source_833_people.csv")): people.setdefault(r["company"], []).append(r)
    c = sqlite3.connect("file:%s?mode=ro" % (ROOT / "db/leadgen.sqlite"), uri=True); c.row_factory = sqlite3.Row
    tam_by_norm = {r["name_norm"]: dict(r) for r in c.execute("select id,name,name_norm,root_domain from legacy_tam_companies")}
    targets = []   # (company, tam_row or None)
    for r in final:
        comp = r["company"]
        if r["source"] == "in TAM corpus" and not r["final_icp"]:
            targets.append((comp, tam_by_norm.get(norm.norm_company_name(comp))))
        elif comp in match["tam_fuzzy"]:
            targets.append((comp, tam_by_norm.get(match["tam_fuzzy"][comp][0])))
        elif comp in match["unknown"]:
            targets.append((comp, None))
    print("targets:", len(targets), "| with a TAM row:", sum(1 for _, t in targets if t))
    items = []
    def stored_text(t):
        if not t: return ""
        rows = c.execute("select url,page_type,text from legacy_tam_pages where company_id=? and coalesce(text,'')!='' order by length(text) desc limit 6", (t["id"],)).fetchall()
        return "\n".join("[%s] %s" % (u, (x or "")[:1500 if i == 0 else 800]) for i, (u, _, x) in enumerate(rows))
    pre = {comp: stored_text(t) for comp, t in targets}            # read the DB in the main thread only
    def work(item):
        comp, t = item
        dom = (t or {}).get("root_domain") or ""; src = "tam_pages"
        text = pre[comp]
        if len(text) < 800:
            if not dom:
                g = S12.guess_domain(comp); dom = (g or {}).get("domain") or ""; src = "domain_guess"
            if dom:
                pages = [p for p in S12.crawl(dom) if p.get("status") == 200 and p.get("text")]
                text = "\n".join("[%s] %s" % (p["url"], p["text"][:1500 if i == 0 else 800]) for i, p in enumerate(pages[:6])); src = (src if src == "domain_guess" else "crawl")
        return comp, dom, text, src
    with ThreadPoolExecutor(max_workers=16) as ex:
        res = list(ex.map(work, targets))
    for comp, dom, text, src in res:
        sg, hc = S34.sig(text) if text else ({}, None)
        items.append({"id": hashlib.md5(comp.encode()).hexdigest()[:10], "company": comp, "domain": dom, "text_source": src,
                      "titles_in_export": "; ".join(sorted({(p.get("title") or "")[:40] for p in people.get(comp, [])}))[:200], "people_in_export": len(people.get(comp, [])),
                      "signal_counts": {k: dict(sorted(v.items(), key=lambda kv: -kv[1])[:8]) for k, v in sg.items()}, "headcount_from_pages": hc, "excerpt": text[:4200]})
    usable = [i for i in items if len(i["excerpt"]) >= 200]
    unusable = [i["company"] for i in items if len(i["excerpt"]) < 200]
    for f in OUT.glob("tam_*.json"): f.unlink()
    for n, i in enumerate(range(0, len(usable), 28)): (OUT / ("tam_%02d.json" % n)).write_text(json.dumps(usable[i:i + 28], indent=1))
    (D / "tam_unusable.json").write_text(json.dumps(unusable, indent=1))
    print("with usable text:", len(usable), "| no usable text (stay unverified):", len(unusable), "| batches:", (len(usable) + 27) // 28)


if __name__ == "__main__":
    main()
