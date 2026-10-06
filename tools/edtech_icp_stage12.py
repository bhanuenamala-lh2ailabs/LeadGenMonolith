#!/usr/bin/env python3
"""EdTech ICP check, stages 1-2 (docs/TASK_edtech_icp_check.md): resolve a website domain for each of the 594 companies, then crawl
the company's OWN public site.  No Apollo, no SignalHire, no HubSpot writes, no LinkedIn.

Stage 1  Google Places Text Search ("{company} {city}") -> websiteUri; match on the site domain, accept only if the Places name resembles the
         company; social/app-store/marketplace URLs are never accepted.  No domain => bucket `domain_unresolved` (retryable, never Out).
Stage 2  robots.txt per host honoured; UA LH2-Research-Bot/1.0 (+bhanu.enamala@lh2.ai); 1 request/sec per host (hosts run in parallel);
         pages: / /about /product(s) /platform /solutions /pricing /careers /customers.  Thin crawl => `crawled_thin` (retryable).
         Every page is stored with source_url, fetched_at, sha256 so a verdict can be traced to its text.
Outputs: data/inbox/edtech/domains.json, data/inbox/edtech/crawl/<n>.json, data/inbox/edtech/stage12_summary.json"""
import csv, difflib, hashlib, html, json, re, sys, time, urllib.robotparser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from leadgen import norm  # noqa: E402

D = ROOT / "data/inbox/edtech"; CR = D / "crawl"; CR.mkdir(parents=True, exist_ok=True)
env = dict(l.strip().split("=", 1) for l in (ROOT / ".env").read_text().splitlines() if "=" in l and not l.startswith("#"))
UA = "LH2-Research-Bot/1.0 (+bhanu.enamala@lh2.ai)"
SOCIAL = ("linkedin.", "facebook.", "instagram.", "twitter.", "x.com", "youtube.", "play.google.", "apps.apple.", "justdial.", "indiamart.", "wikipedia.", "crunchbase.", "glassdoor.", "naukri.", "ambitionbox.", "zaubacorp.", "tofler.", "linktr.ee", "sulekha.")
PATHS = ["/", "/about", "/about-us", "/product", "/products", "/platform", "/solutions", "/pricing", "/careers", "/customers"]


def city(loc):
    return (loc or "").split(",")[0].strip()


PLACES_DEAD = False
STOP = re.compile(r"\b(pvt|private|limited|ltd|llp|inc|corp|corporation|opc|india|technologies|technology|solutions|services|systems|software|infotech|labs|the)\b", re.I)


def guess_domain(company):
    """free fallback when Places is unavailable: try likely domains, accept one only if its live page contains the company's name"""
    core = re.sub(r"[^a-z0-9]", "", STOP.sub(" ", re.sub(r"\(.*?\)", " ", company)).lower())
    first = re.sub(r"[^a-z0-9]", "", (STOP.sub(" ", company).split() or [""])[0].lower())
    if len(core) < 4: return None
    for tld in (".com", ".in", ".co.in", ".io", ".ai", ".org", ".net", ".co"):
        dom = core + tld
        try:
            r = requests.get("https://" + dom, headers={"User-Agent": UA}, timeout=6, allow_redirects=True)
        except Exception:
            continue
        if r.status_code != 200 or "html" not in r.headers.get("Content-Type", "html"): continue
        t = re.sub(r"[^a-z0-9]", "", text_of(r.text[:200000]).lower())
        if core in t or (len(first) >= 5 and first in t[:3000]):
            return {"domain": norm.norm_domain(r.url) or dom, "website": r.url, "places_name": None, "score": 0.6, "query": "guess:" + dom, "resolved_by": "domain_guess_verified_on_page"}
    return None


def resolve(row):
    global PLACES_DEAD
    if PLACES_DEAD:
        g = guess_domain(row["company"])
        return g or {"domain": None, "website": None, "places_name": None, "score": 0, "query": "guess_failed", "resolved_by": None}
    q = "%s %s" % (row["company"], city(row["location"]))
    places = []
    for attempt in range(3):
        try:
            r = requests.post("https://places.googleapis.com/v1/places:searchText", timeout=20,
                              headers={"Content-Type": "application/json", "X-Goog-Api-Key": env["GOOGLE_MAPS_API_KEY"], "X-Goog-FieldMask": "places.displayName,places.websiteUri,places.formattedAddress"},
                              json={"textQuery": q, "regionCode": "IN", "maxResultCount": 3})
            if r.status_code == 429:
                if "RESOURCE_EXHAUSTED" in r.text: PLACES_DEAD = True; break
                time.sleep(3); continue
            places = r.json().get("places", []) if r.status_code == 200 else []
            break
        except Exception:
            places = []; time.sleep(1)
    if PLACES_DEAD and not places:
        return guess_domain(row["company"]) or {"domain": None, "website": None, "places_name": None, "score": 0, "query": q, "resolved_by": None}
    best, bs = None, 0
    cn = norm.norm_company_name(row["company"]) or row["company"].lower()
    for p in places:
        u = p.get("websiteUri") or ""
        if not u or any(s in u.lower() for s in SOCIAL): continue
        pn = norm.norm_company_name((p.get("displayName") or {}).get("text", "")) or ""
        s = difflib.SequenceMatcher(None, cn, pn).ratio()
        if cn and pn and (cn in pn or pn in cn): s = max(s, 0.85)
        if s > bs: best, bs = p, s
    if best and bs >= 0.55:
        return {"domain": norm.norm_domain(best["websiteUri"]), "website": best["websiteUri"], "places_name": best["displayName"]["text"], "score": round(bs, 2), "query": q, "resolved_by": "places"}
    return {"domain": None, "website": None, "places_name": (places[0].get("displayName", {}).get("text") if places else None), "score": round(bs, 2), "query": q}


class T(HTMLParser):
    def __init__(self): super().__init__(); self.t = []; self.skip = 0
    def handle_starttag(self, tag, a):
        if tag in ("script", "style", "noscript", "svg", "head", "nav", "footer"): self.skip += 1
    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg", "head", "nav", "footer") and self.skip: self.skip -= 1
    def handle_data(self, d):
        if not self.skip and d.strip(): self.t.append(d.strip())


def text_of(h):
    p = T()
    try: p.feed(h)
    except Exception: pass
    return re.sub(r"\s+", " ", " ".join(p.t))


def crawl(domain):
    base = "https://" + domain; pages = []; rp = urllib.robotparser.RobotFileParser()
    try:
        r = requests.get(base + "/robots.txt", headers={"User-Agent": UA}, timeout=8); rp.parse(r.text.splitlines() if r.status_code == 200 else [])
    except Exception:
        rp.parse([])
    seen = set()
    for path in PATHS:
        url = base + path
        if not rp.can_fetch(UA, url): pages.append({"url": url, "status": "robots_disallow"}); continue
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=10, allow_redirects=True)
        except Exception as e:
            pages.append({"url": url, "status": "error"}); time.sleep(1); continue
        time.sleep(1.0)
        final = r.url.split("#")[0].rstrip("/")
        if r.status_code != 200 or "html" not in r.headers.get("Content-Type", "html") or final in seen: pages.append({"url": url, "status": r.status_code}); continue
        seen.add(final); t = text_of(r.text[:900000])[:7000]
        pages.append({"url": r.url, "status": 200, "fetched_at": datetime.now(timezone.utc).isoformat(), "sha256": hashlib.sha256(t.encode()).hexdigest()[:16], "chars": len(t), "text": t})
    return pages


def main():
    rows = list(csv.DictReader(open(D / "edtech_campaign_untouched_594.csv")))
    dom_f = D / "domains.json"; doms = json.loads(dom_f.read_text()) if dom_f.exists() else {}
    retry = "--retry-unresolved" in sys.argv        # after the Places daily quota resets: re-try every company that has no domain yet
    todo = [r for r in rows if r["company"] not in doms or (retry and not doms[r["company"]].get("domain"))]
    print("stage 1: resolving %d domains (Places free tier)" % len(todo), flush=True)
    with ThreadPoolExecutor(max_workers=8) as ex:
        for r, res in zip(todo, ex.map(resolve, todo)): doms[r["company"]] = res
    dom_f.write_text(json.dumps(doms, indent=1))
    ok = {c: d for c, d in doms.items() if d["domain"]}
    print("   resolved %d of %d | unresolved (retryable): %d" % (len(ok), len(rows), len(rows) - len(ok)), flush=True)
    # one crawl per distinct domain
    by_dom = {}
    for c, d in ok.items(): by_dom.setdefault(d["domain"], []).append(c)
    print("stage 2: crawling %d distinct sites (1 req/s per host)" % len(by_dom), flush=True)
    idx = {c: i for i, c in enumerate(rows_c["company"] for rows_c in rows)}
    def work(item):
        dom, comps = item; f = CR / (hashlib.md5(dom.encode()).hexdigest()[:12] + ".json")
        if f.exists(): return dom, "cached"
        pages = crawl(dom); f.write_text(json.dumps({"domain": dom, "companies": comps, "pages": pages}))
        return dom, sum(1 for p in pages if p.get("status") == 200)
    done = 0
    with ThreadPoolExecutor(max_workers=24) as ex:
        for dom, n in ex.map(work, by_dom.items()):
            done += 1
            if done % 25 == 0: print("   crawled %d/%d" % (done, len(by_dom)), flush=True)
    summ = {"companies": len(rows), "domains_resolved": len(ok), "domain_unresolved": len(rows) - len(ok), "sites": len(by_dom)}
    (D / "stage12_summary.json").write_text(json.dumps(summ, indent=1)); print("DONE", summ, flush=True)


if __name__ == "__main__":
    main()
