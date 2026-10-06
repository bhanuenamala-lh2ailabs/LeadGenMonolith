#!/usr/bin/env python3
"""Plain-HTTP crawl of pending TAM company websites -> ext_crawl_site / ext_crawl_page (migration 0017).

First pass for the crawl: no browser.  Fast and stable.  Sites whose HTML has almost no text (JavaScript-only) are marked
crawled_thin and can be re-tried later with the Playwright crawler (tools/crawl_playwright.py).

Same rules as the browser crawler: robots.txt checked and cached per domain, public pages only, no logins, no forms, company-level text only.
One progress line per site to stdout and to data/audit/crawl_progress.log.  Each site has a hard overall time limit.

    --concurrency N   sites in parallel (default 30)
    --limit N         at most N pending sites
"""
import argparse, asyncio, hashlib, re, sqlite3, sys, time, urllib.robotparser
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from leadgen import db as ldb  # noqa: E402

LOG = ROOT / "data/audit/crawl_progress.log"
MAX_PAGES = 8
MAX_CHARS = 20000
THIN = 800
SITE_TIMEOUT_S = 90
UA = "Mozilla/5.0 (compatible; LeadGenResearch/1.0; public company pages; contact bhanu.enamala@lh2.ai)"
KINDS = [("about", r"/about|/company|/team|/who-we-are"), ("product", r"/product|/features|/platform|/solutions|/how-it-works"),
         ("careers", r"/career|/jobs|/join-us|/hiring|/openings"), ("docs", r"/docs|/developer|/documentation|/resources"),
         ("api", r"/api"), ("help", r"/help|/faq|/support"), ("integrations", r"/integrat"),
         ("tutors", r"/tutor|/teach|/become-a|/expert|/mentor"), ("blog", r"/blog|/insights")]
PARKED = re.compile(r"domain (is )?(for sale|parked)|buy this domain|this domain is registered|godaddy", re.I)
_ROBOTS = {}


def now():
    d = datetime.now(timezone.utc)
    return d.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (d.microsecond // 1000)


def say(line):
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def kind_of(url):
    path = urlparse(url).path.lower()
    for k, rx in KINDS:
        if re.search(rx, path):
            return k
    return "other"


def robots_ok_sync(domain, url):
    """robots.txt read once per domain, with a short timeout; missing or unreadable means allowed."""
    if domain not in _ROBOTS:
        rp = urllib.robotparser.RobotFileParser()
        try:
            r = httpx.get("https://%s/robots.txt" % domain, headers={"User-Agent": UA}, timeout=6, follow_redirects=True)
            rp.parse(r.text.splitlines()) if r.status_code == 200 else None
            _ROBOTS[domain] = rp if r.status_code == 200 else None
        except Exception:
            _ROBOTS[domain] = None
    rp = _ROBOTS[domain]
    return True if rp is None else rp.can_fetch(UA, url)


def visible_text(html):
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript", "svg", "iframe", "header", "footer", "nav"]):
        t.decompose()
    txt = soup.get_text("\n")
    return re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t]+", " ", txt)).strip()


def links_of(base, html, domain):
    out, seen = [], set()
    for a in BeautifulSoup(html, "html.parser").find_all("a", href=True):
        u = urljoin(base, a["href"]).split("#")[0]
        p = urlparse(u)
        if p.netloc.lower().removeprefix("www.") != domain or u in seen or p.path in ("", "/"):
            continue
        if kind_of(u) == "other" or re.search(r"\.(pdf|png|jpe?g|gif|svg|zip|mp4)$", p.path, re.I):
            continue
        seen.add(u); out.append(u)
    order = [k for k, _ in KINDS]
    out.sort(key=lambda u: order.index(kind_of(u)) if kind_of(u) != "other" else 99)
    return out[:MAX_PAGES]


async def get(client, url):
    try:
        r = await client.get(url)
        return r.status_code, r.text if "html" in r.headers.get("content-type", "html") else "", str(r.url)
    except Exception as e:
        return None, "", type(e).__name__


async def crawl_one(client, sem, con, site, idx, total):
    cid, domain = site["company_id"], site["domain"]
    t0 = time.time()
    async with sem:
        con.execute("INSERT OR IGNORE INTO ext_crawl_site(company_id,domain,status,started_at) VALUES (?,?,'pending',?)", (cid, domain, now()))
        sid = con.execute("SELECT crawl_site_id FROM ext_crawl_site WHERE company_id=? AND domain=?", (cid, domain)).fetchone()[0]
        status, pages, chars, err = "crawled", 0, 0, None
        try:
            if not await asyncio.to_thread(robots_ok_sync, domain, "https://%s/" % domain):
                status = "robots_disallowed"
            else:
                st, html, final = await get(client, "https://%s/" % domain)
                if st is None:
                    st, html, final = await get(client, "http://%s/" % domain)
                if st is None:
                    status, err = "site_unreachable", final
                elif st in (401, 403, 429):
                    status, err = "site_blocked", "http %d" % st
                elif st >= 500 or st == 404:
                    status, err = "site_unreachable", "http %d" % st
                elif PARKED.search(html[:6000]):
                    status = "parked_domain"
                else:
                    home = final
                    targets = [(home, html)]
                    for u in links_of(home, html, domain):
                        s2, h2, _ = await get(client, u)
                        if s2 == 200 and h2:
                            targets.append((u, h2))
                    for u, h in targets:
                        txt = visible_text(h)[:MAX_CHARS]
                        if not txt:
                            continue
                        con.execute("INSERT OR REPLACE INTO ext_crawl_page(crawl_site_id,url,page_kind,http_status,text,text_sha256,fetched_at) VALUES (?,?,?,?,?,?,?)",
                                    (sid, u, kind_of(u) if u != home else "home", 200, txt, hashlib.sha256(txt.encode()).hexdigest(), now()))
                        pages += 1; chars += len(txt)
                    if chars < THIN:
                        status = "crawled_thin"
        except Exception as e:
            status, err = "error", type(e).__name__
        con.execute("UPDATE ext_crawl_site SET status=?, pages_ok=?, chars=?, error=?, finished_at=? WHERE crawl_site_id=?", (status, pages, chars, err, now(), sid))
        con.commit()
    say("[%d/%d] %-34s %-18s pages=%d chars=%d %.0fs%s" % (idx, total, domain[:34], status, pages, chars, time.time() - t0, (" (%s)" % err) if err else ""))


async def main_async(a):
    con = ldb.connect(); con.row_factory = sqlite3.Row
    cat = con.execute("SELECT id FROM legacy_tam_categories WHERE key='edtech'").fetchone()[0]
    rows = con.execute("""SELECT DISTINCT t.id AS company_id, lower(t.root_domain) AS domain FROM legacy_tam_companies t
                          JOIN legacy_tam_company_categories cc ON cc.company_id=t.id AND cc.category_id=?
                          WHERE t.root_domain IS NOT NULL AND t.root_domain<>''
                            AND NOT EXISTS (SELECT 1 FROM ext_crawl_site s WHERE s.company_id=t.id AND s.domain=lower(t.root_domain) AND s.status NOT IN ('pending','error'))
                          ORDER BY t.id""", (cat,)).fetchall()
    todo = [dict(r) for r in rows][: a.limit or None]
    say("http crawl start: %d sites | concurrency %d | %s" % (len(todo), a.concurrency, datetime.now().strftime("%H:%M:%S")))
    sem = asyncio.Semaphore(a.concurrency)
    limits = httpx.Limits(max_connections=a.concurrency * 2, max_keepalive_connections=a.concurrency)
    async with httpx.AsyncClient(headers={"User-Agent": UA}, timeout=15, follow_redirects=True, limits=limits, verify=True) as client:
        async def guarded(s, i):
            # the slot is taken first and the time limit starts only once the site is running, so queued sites are not timed out unseen
            async with sem:
                try:
                    await asyncio.wait_for(crawl_one(client, asyncio.Semaphore(1), con, s, i, len(todo)), timeout=SITE_TIMEOUT_S)
                except asyncio.TimeoutError:
                    con.execute("UPDATE ext_crawl_site SET status='error', error='site timeout', finished_at=? WHERE company_id=? AND domain=?", (now(), s["company_id"], s["domain"]))
                    con.commit()
                    say("[%d/%d] %-34s %-18s (site timeout %ds)" % (i, len(todo), s["domain"][:34], "error", SITE_TIMEOUT_S))
        await asyncio.gather(*[guarded(s, i) for i, s in enumerate(todo, 1)])
    summary = con.execute("SELECT status, count(*) FROM ext_crawl_site GROUP BY 1 ORDER BY 2 DESC").fetchall()
    say("http crawl done: " + ", ".join("%s=%d" % (r[0], r[1]) for r in summary))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--concurrency", type=int, default=30); ap.add_argument("--limit", type=int, default=0)
    asyncio.run(main_async(ap.parse_args()))
