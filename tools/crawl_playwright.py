#!/usr/bin/env python3
"""Free Playwright crawl of the edtech corpus's own public websites -> ext_crawl_site / ext_crawl_page (migration 0017).

Public company pages only: robots.txt is checked before every fetch, no logins, no forms, no learner/parent/tutor personal data.
One progress line per site goes to stdout and to data/audit/crawl_progress.log, so the run can be followed live.

    --limit N        crawl at most N pending sites (default: all)
    --concurrency K  browsers in parallel (default 6)
    --dry-run        list what would be crawled, fetch nothing
"""
import argparse, asyncio, hashlib, re, sqlite3, sys, time, urllib.request, urllib.robotparser
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from leadgen import db as ldb  # noqa: E402

LOG = ROOT / "data/audit/crawl_progress.log"
MAX_PAGES = 8
SITE_TIMEOUT_S = 180
MAX_CHARS = 20000
THIN = 800
KINDS = [("about", r"/about|/company|/team|/who-we-are"), ("product", r"/product|/features|/platform|/solutions|/how-it-works"),
         ("careers", r"/career|/jobs|/join-us|/hiring|/openings"), ("docs", r"/docs|/developer|/documentation|/resources"),
         ("api", r"/api"), ("help", r"/help|/faq|/support"), ("integrations", r"/integrat"),
         ("tutors", r"/tutor|/teach|/become-a|/expert|/mentor"), ("blog", r"/blog|/insights")]
PARKED = re.compile(r"domain (is )?(for sale|parked)|buy this domain|this domain is registered|godaddy", re.I)


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (datetime.now(timezone.utc).microsecond // 1000)


def say(line):
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


_ROBOTS = {}


def robots_ok(domain, url):
    """robots.txt is read once per domain and cached; a missing or unreadable file counts as allowed."""
    if domain not in _ROBOTS:
        rp = urllib.robotparser.RobotFileParser()
        try:
            with urllib.request.urlopen("https://%s/robots.txt" % domain, timeout=6) as r:   # bounded: a silent host must not stall the crawl
                rp.parse(r.read().decode("utf-8", "replace").splitlines())
        except Exception:
            rp = None
        _ROBOTS[domain] = rp
    rp = _ROBOTS[domain]
    return True if rp is None else rp.can_fetch("*", url)


def kind_of(url):
    path = urlparse(url).path.lower()
    for k, rx in KINDS:
        if re.search(rx, path):
            return k
    return "other"


def pick_links(base, html_links, domain):
    out, seen = [], set()
    for href in html_links:
        u = urljoin(base, href).split("#")[0]
        p = urlparse(u)
        if p.netloc.lower().removeprefix("www.") != domain or u in seen or p.path in ("", "/"):
            continue
        if kind_of(u) == "other" or re.search(r"\.(pdf|png|jpe?g|gif|svg|zip|mp4)$", p.path, re.I):
            continue
        seen.add(u); out.append(u)
    out.sort(key=lambda u: [k for k, _ in KINDS].index(kind_of(u)) if kind_of(u) != "other" else 99)
    return out[:MAX_PAGES]


async def ensure_browser(holder):
    """Relaunch Chromium if it has died (the 5 Oct run lost its browser after ~140 sites and timed out everything after it)."""
    async with holder["lock"]:
        if not holder["b"].is_connected():
            say("browser closed: relaunching Chromium")
            holder["b"] = await holder["pw"].chromium.launch(headless=True)
    return holder["b"]


async def crawl_site(holder, sem, con, site, idx, total):
    """One site, isolated: any failure is recorded on that site's row and the rest of the crawl carries on."""
    try:
        browser = await ensure_browser(holder)
        await asyncio.wait_for(_crawl_site(browser, sem, con, site, idx, total), timeout=SITE_TIMEOUT_S)
    except asyncio.TimeoutError:
        cid, domain = site["company_id"], site["domain"]
        con.execute("INSERT OR IGNORE INTO ext_crawl_site(company_id,domain,status,started_at) VALUES (?,?,'pending',?)", (cid, domain, now()))
        con.execute("UPDATE ext_crawl_site SET status='error', error='site timeout', finished_at=? WHERE company_id=? AND domain=?", (now(), cid, domain))
        con.commit()
        say("[%d/%d] %-34s %-18s (site timeout %ds)" % (idx, total, domain[:34], "error", SITE_TIMEOUT_S))
    except Exception as e:
        cid, domain = site["company_id"], site["domain"]
        con.execute("INSERT OR IGNORE INTO ext_crawl_site(company_id,domain,status,started_at) VALUES (?,?,'pending',?)", (cid, domain, now()))
        con.execute("UPDATE ext_crawl_site SET status='error', error=?, finished_at=? WHERE company_id=? AND domain=?", (type(e).__name__, now(), cid, domain))
        con.commit()
        say("[%d/%d] %-34s %-18s (%s)" % (idx, total, domain[:34], "error", type(e).__name__))


async def _crawl_site(browser, sem, con, site, idx, total):
    cid, domain = site["company_id"], site["domain"]
    t0 = time.time()
    async with sem:
        con.execute("INSERT OR IGNORE INTO ext_crawl_site(company_id,domain,status,started_at) VALUES (?,?,'pending',?)", (cid, domain, now()))
        sid = con.execute("SELECT crawl_site_id FROM ext_crawl_site WHERE company_id=? AND domain=?", (cid, domain)).fetchone()[0]
        status, pages, chars, err = "crawled", 0, 0, None
        if not robots_ok(domain, "https://%s/" % domain):
            status = "robots_disallowed"
        else:
            page = await browser.new_page(user_agent="Mozilla/5.0 (Macintosh) LeadGenResearch/1.0 (public company pages; contact bhanu.enamala@lh2.ai)")
            try:
                try:
                    resp = await page.goto("https://%s/" % domain, wait_until="domcontentloaded", timeout=25000)
                except Exception:
                    try:
                        resp = await page.goto("http://%s/" % domain, wait_until="domcontentloaded", timeout=25000)
                    except Exception as e:
                        resp = None; err = type(e).__name__
                if resp is None:
                    status = "site_unreachable"
                elif resp.status in (401, 403, 429) or resp.status >= 500:
                    status = "site_blocked" if resp.status in (401, 403, 429) else "site_unreachable"; err = "http %d" % resp.status
                else:
                    home = page.url
                    await page.wait_for_timeout(800)
                    try:
                        html = await page.content()
                    except Exception:
                        html = ""
                    if PARKED.search(html[:6000]):
                        status = "parked_domain"
                    links = await page.eval_on_selector_all("a[href]", "els => els.map(e => e.getAttribute('href'))")
                    targets = [(home, await page.evaluate("document.body ? document.body.innerText : ''"))]
                    for u in pick_links(home, links, domain):
                        if not robots_ok(domain, u):
                            continue
                        try:
                            r2 = await page.goto(u, wait_until="domcontentloaded", timeout=20000)
                            await page.wait_for_timeout(400)
                            targets.append((u, await page.evaluate("document.body ? document.body.innerText : ''")))
                        except Exception:
                            continue
                    for u, txt in targets:
                        txt = re.sub(r"\n{3,}", "\n\n", (txt or "").strip())[:MAX_CHARS]
                        if not txt:
                            continue
                        con.execute("INSERT OR REPLACE INTO ext_crawl_page(crawl_site_id,url,page_kind,http_status,text,text_sha256,fetched_at) VALUES (?,?,?,?,?,?,?)",
                                    (sid, u, kind_of(u) if u != home else "home", 200, txt, hashlib.sha256(txt.encode()).hexdigest(), now()))
                        pages += 1; chars += len(txt)
                    if status == "crawled" and chars < THIN:
                        status = "crawled_thin"
            finally:
                await page.close()
        con.execute("UPDATE ext_crawl_site SET status=?, pages_ok=?, chars=?, error=?, finished_at=? WHERE crawl_site_id=?", (status, pages, chars, err, now(), sid))
        con.commit()
    secs = time.time() - t0
    say("[%d/%d] %-34s %-18s pages=%d chars=%d %.0fs%s" % (idx, total, domain[:34], status, pages, chars, secs, (" (%s)" % err) if err else ""))


async def main_async(a):
    from playwright.async_api import async_playwright
    con = ldb.connect(); con.row_factory = sqlite3.Row
    cat = con.execute("SELECT id FROM legacy_tam_categories WHERE key='edtech'").fetchone()[0]
    rows = con.execute("""SELECT DISTINCT t.id AS company_id, lower(t.root_domain) AS domain FROM legacy_tam_companies t
                          JOIN legacy_tam_company_categories cc ON cc.company_id=t.id AND cc.category_id=?
                          WHERE t.root_domain IS NOT NULL AND t.root_domain<>''
                            AND NOT EXISTS (SELECT 1 FROM ext_crawl_site s WHERE s.company_id=t.id AND s.domain=lower(t.root_domain) AND s.status<>'pending')
                          ORDER BY t.id""", (cat,)).fetchall()
    todo = [dict(r) for r in rows][: a.limit or None]
    say("crawl start: %d sites | concurrency %d | %s" % (len(todo), a.concurrency, datetime.now().strftime("%H:%M:%S")))
    if a.dry_run:
        for s in todo[:20]: print(s)
        return
    sem = asyncio.Semaphore(a.concurrency)
    async with async_playwright() as p:
        holder = {"pw": p, "b": await p.chromium.launch(headless=True), "lock": asyncio.Lock()}
        tasks = [crawl_site(holder, sem, con, s, i, len(todo)) for i, s in enumerate(todo, 1)]
        await asyncio.gather(*tasks, return_exceptions=True)
        await holder["b"].close()
    summary = con.execute("SELECT status, count(*) FROM ext_crawl_site GROUP BY 1 ORDER BY 2 DESC").fetchall()
    say("crawl done: " + ", ".join("%s=%d" % (r[0], r[1]) for r in summary))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--limit", type=int, default=0); ap.add_argument("--concurrency", type=int, default=6); ap.add_argument("--dry-run", action="store_true")
    asyncio.run(main_async(ap.parse_args()))
