# Apollo credit audit playbook

How the Apollo.io credit audit was done across three repos (`companyOps`, `RapidActionTeam`, `hubspot`) plus their Claude Code chat histories, so it can be repeated on another laptop that has the same three repos. Hand this file to Claude Code on that laptop and say: **"Read AUDIT_PLAYBOOK.md and run the audit. Ask me for the inputs in section 2 first."**

Result of the original run (2026-10-05): of 96,439 credits Apollo reported used in one billing cycle, 41,469 (43%) could be traced to a repo, script and purpose; 54,970 could not. About half of the traced credits were estimates, not measurements. Treat those numbers as an example of the output, not as expectations: the other laptop will differ.

---

## 0. What the audit answers
1. Which credits were spent by which repo, script, Claude agent and purpose, on which day.
2. Which API calls Apollo's own documentation says consume credits, and which of those the repos' ledgers counted, ignored or assumed free.
3. How much of Apollo's usage cannot be explained by anything on the laptop (people in the Apollo web app, other machines, other API keys), and what evidence points where.
4. Whether the API key could have leaked.

## 1. Ground rules (apply to every step and every agent you start)
- **Read-only.** Do not modify the repos, their databases or their `.env`. Work on copies in a separate audit folder.
- **Never print, log, write or paste a secret** (API keys, tokens, `.env` values, OAuth files, private keys). Compare keys only by hash, inside Python, and report only "equal / not equal". Redact transcripts before anyone reads them (script in Appendix B).
- **No Apollo API calls** during the audit, with one exception that the owner must approve: the free `credit_usage_stats` meter (a POST that spends no credits) to read the current balance. Do not call search, enrich, match or reveal endpoints: they spend credits.
- **No HubSpot or Google writes. No mail.**
- **The owner of the laptop must say, in this session, that you may read their Claude Code chat history.** Those transcripts can contain pasted secrets. If a permission classifier blocks a read, do not work around it: tell the owner and let them decide.
- **Separate MEASURED from ESTIMATED everywhere.** A measured credit has a ledger row, a log line or a balance delta behind it. An estimate is reconstructed from code, counts or chat. Never merge the two silently.
- **No double counting.** One spend is one row: the ledger row, the log line and the chat message about the same run are the same spend. A repo sometimes runs another repo's job (same laptop, shared API key): count it once, under the repo whose code made the call.
- **If a claim cannot be verified from evidence, say so.** Earlier assistants in these chats said things like "tracked in the ledger" and "free" that were false. Verify, do not repeat.

## 2. Inputs to get from the owner before starting
| Input | Why | If missing |
|---|---|---|
| Apollo **usage chart** (screenshot of Settings > Billing > Credit usage, daily bars, split by type) for the whole billing cycle | The ground truth you reconcile against | Ask. Without it you can only report traced credits, not a gap |
| Apollo **credit usage history export** filtered by team member and by action, same dates | Shows who and what; settles most "unexplained" credits | Ask; the owner downloads it from the Apollo UI |
| Billing cycle start/end, top-ups (date and size), current balance | Window boundaries and the pool arithmetic | Ask |
| The **Apollo account timezone** | Decides which day each credit lands on (see section 7) | Infer with the boundary test in section 7 |
| Paths of the three repos on this laptop and the usernames/emails of the people who work in them | Inventory, git authorship | Find with `ls ~/Desktop` |
| Permission to read Claude Code history under `~/.claude/projects/` | Chats are the best evidence for runs that wrote no ledger | Without it, the audit is weaker; say so in the report |

## 3. Workspace setup
```bash
mkdir -p ~/ApolloAudit/{repos,chat_context/{raw,transcripts,summaries},docs/audit/apollo/{pre,agents},data/audit,tools}
cd ~/ApolloAudit
# copy the three repos without venvs, caches or .git (keep git history separately as bundles)
for r in companyOps RapidActionTeam hubspot; do
  (cd ~/Desktop/$r && git bundle create ~/ApolloAudit/repos/$r.bundle --all 2>/dev/null; git log --format='%an|%ae|%ad' > ~/ApolloAudit/repos/$r.authors.txt)
  rsync -a --exclude='.venv/' --exclude='venv/' --exclude='__pycache__/' --exclude='*.pyc' --exclude='node_modules/' --exclude='.git/' ~/Desktop/$r/ ~/ApolloAudit/repos/$r/
done
# Claude Code chat history: main session .jsonl + subagents/ + tool-results/ (full tool outputs live there)
for r in companyOps RapidActionTeam hubspot; do
  rsync -a --exclude=memory ~/.claude/projects/-Users-$(whoami)-Desktop-$r/ ~/ApolloAudit/chat_context/raw/$r/
done
# redacted, readable transcripts (script in Appendix B; it redacts every value found in the repos' .env files and common token shapes)
python3 tools/chats_to_markdown.py chat_context/raw/<repo> chat_context/transcripts/<repo>
```
Verify the redaction: grep the transcripts for the first 8 characters of every key in the `.env` files; the count must be 0 before any agent reads them.
SQLite databases (cost ledgers) are opened read-only: `sqlite3 -readonly file` or the URI `file:path?mode=ro&immutable=1`. Never copy a live WAL database while a process writes to it.

## 4. Phase A: the billing truth table, from Apollo's own documentation
Goal: for every Apollo endpoint the repos call, what the documentation says about credits, quoted verbatim.
1. Fetch **raw** documentation pages, not summaries: `curl -sSL -A 'Mozilla/5.0' https://docs.apollo.io/reference/<page>.md` (the `.md` variant of each docs page worked for all 109 pages; the sitemap and `llms.txt` list them). Also fetch `https://docs.apollo.io/openapi/apollo-rest-api.json`. Save each page with a header: URL, HTTP status, UTC fetch time, sha256. Apollo's help-center articles (knowledge.apollo.io) return 403 to scripts: mark anything from them "unverified".
2. Extract every sentence containing credit/charge/billed/free/consumes. Build a table: endpoint and parameter combination, docs verdict (CONSUMES / FREE / CONDITIONAL / SILENT), unit (per call, per page, per record), credit type, verbatim quote, URL, fetch time.
3. List every call the repos make (`grep -rn "api.apollo.io"` and the endpoint names) and attach the file and line.
4. Rules found on 2026-10-05 (re-verify, they change):
   | Call | Verdict |
   |---|---|
   | `people/match` | 1 credit only if credit-consuming data is found; 0 if nothing found. Same person again "can potentially use more credits" |
   | `people/match` + `reveal_phone_number` | +8 credits "if a mobile phone is returned". Whether billed on acceptance or delivery is not stated; **measured: billed even when no number was returned** |
   | `reveal_personal_emails=true`, retries on 429/timeout, webhook retries | docs silent |
   | `mixed_companies/search` (organisation search) | **consumes 1 credit per page** (measured: per call that returns at least one organisation; empty calls free) |
   | `organizations/enrich`, `organizations/bulk_enrich` | 1 credit per organisation (submitted vs found not stated) |
   | `mixed_people/api_search` (people search) | free |
   | contacts/accounts/sequences, `usage_stats/*`, `users/api_profile` | free. The API pricing page states the table of consuming endpoints is exhaustive |
   | Free per-request meter | phone-reveal results and `bulk_match` responses include `credits_consumed` |
5. The free **meter**: `POST https://api.apollo.io/api/v1/usage_stats/credit_usage_stats` with header `x-api-key` and body `{}` returns `credit_usage_stats.lead_credit.{limit,consumed,left_over}`. Read it before and after any single call to measure its cost. Remember `direct_dial_credit.consumed` is a separate counter and did not move with phone reveals on a unified plan.
6. For each repo's own cost-ledger logic (`cost_ledger` writers, "N credits spent" prints, budget tables) record which endpoints it **counts**, which it **ignores** and what it books for failures. That list is the set of *structurally untracked* spend.

## 5. Phase B: evidence inventory per repo
For each repo gather, without running anything:
1. **Ledgers and databases** with credit columns (look for `cost_ledger`, `budget_ledger`, `usage`, `quota`, call caches). Note the **first row's timestamp**: spend before it is untracked by definition. Check time zones (UTC in the database, IST in logs).
2. **Call sites**: every file calling Apollo, its endpoints, parameters, batch sizes, retries, gates (is there a "vendors disabled" switch? does every script honour it?).
3. **Outputs and logs** with modification times, row counts, and Apollo-derived columns (phones, emails, org ids). Scratch directories (`/tmp`, scratchpads) are usually gone: say so.
4. **Chat history** (the richest evidence): every balance or "credits left" reading with its exact timestamp, every run's printed or stated credit use, `ps` listings showing what else was running, the user's own pasted Apollo screenshots.
5. **Git**: authors, dates, who wrote Apollo-calling code, remotes (whose GitHub account owns each repo), workflows (`.github/workflows`, any with an Apollo secret or a cron).
6. **Key hygiene** (hashes only): is the same Apollo key in all `.env` files; when did each `.env` change (file mtime; VS Code Local History if present); has the key value ever appeared in git history (`git log --all -S<key>` with the key read inside Python, print counts only), in zip/tgz archives, in synced folders, in mailed files. Count files containing the key.

## 6. Phase C: forensic reconstruction (one agent per repo and period)
Run these as parallel Claude Code subagents (read-only, no network). Give each the ground rules in section 1 and one of the templates in Appendix A. The slices used:
| Slice | Question |
|---|---|
| Docs snapshot (Phase A) | What does Apollo say each call costs? |
| Per repo, window **after** the last top-up | Credit by credit, what ran, what it cost, what it bought, where its output lives now |
| Per repo, **before** the top-up, day by day | Same, with each day compared against the Apollo chart bar |
| **Agent-wise** per repo (main agent and every subagent, by date ranges) | What did each Claude agent believe a call cost, what did it actually cost, was it tracked? Find false "free" beliefs and un-ledgered runs |
| Key exposure / foreign callers | Could the key have leaked? What else can spend credits (workflows, scheduled jobs, other people)? |

Every agent writes a markdown narrative and a CSV with these columns: `ts_ist, date_ist, date_utc, repo, script_or_agent, run_id_or_file, endpoint_or_action, units, credits_per_unit, credits, evidence_type (ledger|log|output_file|chat_balance_delta|chat_claim|code_estimate), evidence_ref, purpose, confidence (high|medium|low), notes (start with [MEASURED] or [ESTIMATED]), data_location_now`. Agent-wise CSVs add: `session_id, agent_id, agent_task, apollo_endpoints, billable_calls_low/central/high, credits_tracked, credits_untracked_low/central/high, believed_cost_quote, tracked_how`.
Practices that mattered: ask agents to quote the chat line or file path for every row; to extract **every** balance reading with its timestamp; to join ledger rows to agents by time window; to state ranges (low/central/high) when counts are reconstructed; and to list every credit-spending event that has no recorded purpose.

## 7. Phase D: merge and the day-boundary test
1. **One source per (repo, period).** Prefer the ledger-based file for the window after the top-up and the pre-window file for earlier days; use the agent-wise files to sharpen estimates and to find untracked calls, not to add on top. Run `tools/apollo_master_merge.py` (Appendix C; adapt the file names) to produce `master_ledger.csv`. Check each repo's total against the figure its own agent reported before combining.
2. **Day boundary.** Apollo's chart days are in the *account's* timezone, which may be neither IST nor UTC. Bucket every traced event under each candidate boundary (UTC-12 to UTC+14 in half-hours), compare with the chart bars read off the screenshot (±0.3k), and reject a boundary where traced spend exceeds Apollo's bar for a day. In the original run only US Pacific boundaries survived (UTC overshot one day by ~780 credits). The owner can confirm in Apollo's settings. Do not compare per-day numbers before settling this: it moved whole runs between days.
3. Reconcile: Apollo's cycle total vs the traced total; per day gap; share traced; share estimated. Cross-check pool arithmetic with balance readings (remaining = allowance - consumed; a chat saying "96,000 remaining" when 96,000 had been consumed was a recurring error).

## 8. Phase E: who or what spent the untraced credits
Collect evidence, rank hypotheses, and say what single check in Apollo would confirm each (credit history by team member / by surface; API keys page: created date and last used):
- **Timing**: untraced spend that arrives in bursts and goes quiet overnight fits people working; a steady drain fits automation. Compare burst times with the periods when each Claude session was open or idle.
- **Credit type**: the chart splits types (phone vs exports/enrichment, plus rare types such as Waterfall and Email). A type no script can produce (no repo sets waterfall parameters) points to the web app or another key.
- **Records created outside any session**: deals or contacts appearing in HubSpot at times no session was running.
- **Shared identity and keys**: one key in several repos, one shared Apollo login, keys swapped (when), old keys possibly still alive.
- **Other people's code**: git authors, other users' automation, interns, scheduled tasks (`launchd`, cron, GitHub Actions, n8n, long-running pollers). `ps aux` output pasted in chats is a good snapshot of what was running.
- **Exposure**: counts from section 5.6. State plainly what the laptop cannot show (Apollo's member list, export history, key last-used times, whether old keys were deleted).

## 9. Lessons from the original audit (check each on the new laptop)
1. Organisation search (`mixed_companies/search`) was assumed free in many scripts, agents and briefs; it bills 1 credit per page. Only one script recorded it. Grep for "free", "0 credits spent", "$0" next to it.
2. A failed phone reveal is billed. Count reveal attempts, not delivered numbers.
3. Cost ledgers start late (the first row may be days after Apollo use began) and cover only the code paths that call the ledger helper. Older discovery scripts and one-off Python snippets never write to it.
4. "Dry run" modes that still pay for lookups; restarts that re-pay the same lookups (no cache); parallel workers duplicating work; killed processes whose summary never printed.
5. Estimates dominate the pre-ledger period. State the share of estimates in the headline.
6. Agents told the user "tracked in the ledger" or "nothing spent" when spend was un-ledgered; a probe call is spend too.
7. The "vendors disabled" switch in one repo did not cover scripts that bypass it (repair/backfill scripts) and can be switched back on.
8. Subagents (classification, research) almost never call Apollo; the main agent does. Verify by scanning every subagent's tool calls for the host name.
9. Two repos can share one key and one credit pool; each repo's own daily cap cannot see the others' spend.
10. A spend run from repo A for repo B's purpose belongs to A's code (count once).
11. Pool counters (`lead_credit`) and per-type counters (`direct_dial_credit`) move differently; do not reconcile phone credits against the wrong counter.
12. Chart days vs your days: see section 7.2.

## 10. Deliverables (suggested layout under `docs/audit/apollo/`)
`MASTER_RECONCILIATION.md` (day table, docs verdict table, class x tracking table, what it does and does not show) · `master_ledger.csv` · `apollo_docs_snapshot/` (raw pages + `_credit_sentences.md`) · `agents/endpoint_billing_table_v2.{md,csv}` · `pre/` and `agents/` per-repo narratives and CSVs · `key_exposure.md` · an optional one-page dashboard. Finish with a short summary for the owner: traced vs total, biggest untraced days, the three most informative who/what findings, and the list of things only Apollo's own history can settle.

## 11. Verification checklist before you report
- [ ] Every repo's total in the merge equals the figure its agent reported.
- [ ] No row appears twice (same run in a ledger and in a chat summary).
- [ ] Measured and estimated are separate in every table; the estimated share is stated.
- [ ] The day-boundary test was run; the chosen boundary is stated as an inference unless confirmed.
- [ ] Percentages recomputed from the tables (the original run caught two wrong percentages that way).
- [ ] No secret anywhere in the outputs (grep for key prefixes).
- [ ] The report says what could not be checked and why.

---

## Appendix A: agent prompt templates (copy, fill the angle-bracket parts)

**A1. Forensic reconstruction for one repo and one period**
> You are a forensic analyst. Reconstruct, credit by credit, every Apollo.io credit consumed by the `<REPO>` repo from `<START>` to `<END>` (`<TIMEZONE>`), and what each was spent on. Repo copy: `<PATH>` (read-only). Context: the Apollo chart shows `<DAILY BARS>`; balance readings known: `<LIST>`. Mine, exhaustively: (1) the repo's cost ledger/database tables (open read-only); (2) every Apollo call site and its credit semantics (endpoints, parameters, retries, gates); (3) run outputs, logs and CSVs with file mtimes and row counts; (4) the Claude chat for this repo: readable transcript `<PATH>` and the full raw session `<PATH>/*.jsonl` plus `subagents/` and `tool-results/` (parse with Python; extract every balance reading with its timestamp and every run's stated credit use); (5) git authors of Apollo code. NEVER print or write any key or token value. Write `<narrative.md>` day by day and `<ledger.csv>` with the columns in section 6, plus a balance-readings CSV. Separate MEASURED from ESTIMATED; no double counting; list every credit-spending event with no recorded purpose. No network calls. Do not modify anything outside your output files. If a tool call is denied by a permission classifier, skip it and report it.

**A2. Agent-wise audit**
> Doing an AGENT-WISE credit audit of `<REPO>`'s Claude sessions in `<DATE RANGE>`. Enumerate the main agent's turns (cut by timestamp) and every subagent file (id, first prompt = its task, first/last timestamp, tool-use count). Extract each agent's Apollo activity (commands, scripts it wrote or ran, tool results with counts/credits/status codes). For each agent record what it BELIEVED a call cost (quote: "free", "0 credits", "no credits"), what it actually cost per the billing truth table, and whether it was TRACKED (a ledger row in its time window, a printed total, nothing). Estimate untracked credits per agent as low/central/high with the method. Record failed or retried calls that could double-bill. Output narrative + CSV (columns in section 6).

**A3. Key exposure and foreign callers**
> Determine from evidence on this laptop how the Apollo key could have been exposed or used by someone or something other than the owner's own scripts. Count (never print) files containing the key and its first 8 characters across the repos, git history of every repo and bundle, zip/tgz archives, chat transcripts, editor history and synced folders; list git authors and who wrote Apollo-calling code; inventory everything that can call Apollo without a typed command (workflows, cron/launchd, long-running pollers, other people's scripts); list non-API credit burners (web app exports and reveals, Chrome extension, integrations); compare keys across `.env` files by hash only and date each `.env` change. Rank hypotheses (outsider with a leaked key, teammate with a copy of the key, teammate in the web app, old key never deleted, Apollo-side automation) with evidence for and against and the one Apollo check that would confirm each.

**A4. Docs snapshot** — see Phase A, section 4.

## Appendix B: `tools/chats_to_markdown.py` (redacted transcripts)
```python
#!/usr/bin/env python3
"""Convert Claude Code session .jsonl files into readable, secret-redacted markdown.

Keeps: user prompts, assistant text, condensed tool calls (name + short input), AI titles.
Drops: tool result bodies (summarised to a size note), file-history/attachment/queue noise.
Redacts: every value in ./.env and ./secrets/*.json, plus common token shapes.
Usage: chats_to_markdown.py <raw_dir> <out_dir>
"""
import json, re, sys, glob, os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
secrets = set()
for line in (ROOT / ".env").read_text().splitlines():
    if "=" in line and not line.startswith("#"):
        v = line.split("=", 1)[1].strip().strip("\"'")
        if len(v) >= 8: secrets.add(v)
def walk(o):
    if isinstance(o, dict):
        for v in o.values(): yield from walk(v)
    elif isinstance(o, list):
        for v in o: yield from walk(v)
    elif isinstance(o, str) and len(o) >= 12: yield o
for f in glob.glob(str(ROOT / "secrets" / "*.json")):
    try:
        for s in walk(json.load(open(f))):
            if not s.startswith("http") and " " not in s: secrets.add(s)
    except Exception: pass
secrets_sorted = sorted(secrets, key=len, reverse=True)
PATTERNS = [re.compile(p) for p in (
    r"sk-ant-[A-Za-z0-9_\-]{20,}", r"sk-[A-Za-z0-9]{32,}", r"pat-[a-z0-9]+-[0-9a-f\-]{30,}",
    r"ghp_[A-Za-z0-9]{30,}", r"github_pat_[A-Za-z0-9_]{30,}", r"ya29\.[A-Za-z0-9_\-]{20,}",
    r"1//[A-Za-z0-9_\-]{30,}", r"AIza[A-Za-z0-9_\-]{30,}", r"GOCSPX-[A-Za-z0-9_\-]{20,}",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
    r"(?i)bearer\s+[A-Za-z0-9._\-]{25,}",
)]
def redact(t):
    for s in secrets_sorted: t = t.replace(s, "[REDACTED]")
    for p in PATTERNS: t = p.sub("[REDACTED]", t)
    return t

def clip(s, n): s = s if isinstance(s, str) else json.dumps(s, ensure_ascii=False); return s if len(s) <= n else s[:n] + f"… [+{len(s)-n} chars]"

def convert(jsonl, out):
    title = None; n_user = n_asst = 0
    with open(jsonl) as fh, open(out, "w") as o:
        buf = []
        for line in fh:
            try: d = json.loads(line)
            except Exception: continue
            t = d.get("type"); ts = (d.get("timestamp") or "")[:19]
            if t == "ai-title": title = d.get("aiTitle"); buf.append(f"\n## === Session title: {title} ===\n"); continue
            if t not in ("user", "assistant") or d.get("isSidechain"): continue
            msg = d.get("message") or {}; c = msg.get("content")
            if isinstance(c, str): c = [{"type": "text", "text": c}]
            for b in c or []:
                bt = b.get("type")
                if bt == "text" and b.get("text", "").strip():
                    txt = b["text"]
                    if t == "user":
                        if txt.lstrip().startswith(("<ide_", "<system-reminder", "<command-")) and len(txt) < 400: continue
                        n_user += 1; buf.append(f"\n### USER [{ts}]\n{clip(txt, 20000)}\n")
                    else: n_asst += 1; buf.append(f"\n### ASSISTANT [{ts}]\n{clip(txt, 20000)}\n")
                elif bt == "tool_use": buf.append(f"- 🔧 `{b.get('name')}` {clip(b.get('input'), 400)}")
                elif bt == "tool_result":
                    r = b.get("content"); sz = len(r) if isinstance(r, str) else len(json.dumps(r))
                    buf.append(f"  ↳ result ({sz} chars) {clip(r if isinstance(r,str) else json.dumps(r), 200)}")
        o.write(redact(f"# Transcript: {jsonl.name}\nsource: {jsonl}\nuser msgs: {n_user}, assistant msgs: {n_asst}\n" + "\n".join(buf)))
    return n_user, n_asst

if __name__ == "__main__":
    raw, outd = Path(sys.argv[1]), Path(sys.argv[2]); outd.mkdir(parents=True, exist_ok=True)
    for j in sorted(raw.glob("*.jsonl")):
        out = outd / (j.stem + ".md"); u, a = convert(j, out)
        print(j.name, "→", out.name, f"user={u} asst={a} size={out.stat().st_size//1024}KB", flush=True)
```

## Appendix C: `tools/apollo_master_merge.py` (merge and day-boundary test)
This is the script used in the original run. File names and the `CHART` dictionary are specific to that run: replace them with the new laptop's files and chart readings. Keep the structure: one source per (repo, period), then the boundary loop.
```python
#!/usr/bin/env python3
"""Merge the per-repo Apollo audit ledgers (docs/audit/apollo/**) into one master list of credit events,
choosing exactly one source per (repo, period) so nothing is double counted, then test which day boundary
(timezone) best lines our traced credits up with Apollo's own daily usage chart.
Outputs: docs/audit/apollo/master_ledger.csv, master_summary.json (and prints a report)."""
import csv, json, re, collections
from datetime import datetime, timedelta
from pathlib import Path

A = Path(__file__).resolve().parent.parent / "docs" / "audit" / "apollo"
IST = timedelta(hours=5, minutes=30)

def fl(x):
    try: return float(str(x).replace(",", ""))
    except Exception: return 0.0

def pts(s):
    s = (s or "").strip().replace(" IST", "")
    if not s: return None
    s = s.replace("T", " ")
    s = re.sub(r"\+05:30$", "", s)
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try: return datetime.strptime(s, f)
        except ValueError: pass
    return None

events = []   # dict(repo, source, start, end, credits, est, purpose)

def add(repo, source, start, end, credits, est, purpose, extra=None):
    if not credits or start is None: return
    events.append(dict(repo=repo, source=source, start=start, end=end or start, credits=credits, est=est, purpose=purpose[:120], **(extra or {})))

def is_est(r):
    n = r.get("notes", "")
    if "[ESTIMATED]" in n: return True
    if "[MEASURED]" in n: return False
    return r.get("evidence_type") in ("code_estimate", "chat_claim")

# --- companyOps -------------------------------------------------------------
for r in csv.DictReader(open(A / "pre/companyops_ledger.csv")):
    if r["repo"] != "companyOps": continue
    d = r["date_ist"]
    if d >= "2026-09-29": continue
    add("companyOps", "pre/companyops_ledger.csv", pts(r["ts_ist"]), None, fl(r["credits"]), is_est(r), r["purpose"])
for r in csv.DictReader(open(A / "companyops_ledger.csv")):
    if r["repo"] != "companyOps" or r["date_ist"] < "2026-09-29": continue
    add("companyOps", "companyops_ledger.csv", pts(r["ts_ist"]), None, fl(r["credits"]), is_est(r), r["purpose"])

# --- hubspot repo -----------------------------------------------------------
for r in csv.DictReader(open(A / "pre/hubspot_ledger.csv")):
    if r["repo"] != "hubspot": continue
    add("hubspot", "pre/hubspot_ledger.csv", pts(r["ts_ist"]), None, fl(r["credits"]), is_est(r), r["purpose"])
for r in csv.DictReader(open(A / "agents/hubspot_0927_1004.csv")):
    s, e = pts(r["start_ts_ist"]), pts(r["end_ts_ist"])
    if s is None or s < datetime(2026, 9, 29): continue
    tr = fl(r["credits_tracked"]); un = fl(r["credits_untracked_central"])
    add("hubspot", "agents/hubspot_0927_1004.csv", s, e, tr + un, un > 0 and tr == 0, r["agent_task"])

# --- RapidActionTeam (the RAT repo's own spend only; sibling-repo RAT-purpose rows are already in hubspot) ---
for f in ("pre/rat_ledger.csv", "rat_ledger.csv"):
    for r in csv.DictReader(open(A / f)):
        if r["repo"] != "RapidActionTeam": continue
        if r["date_ist"] >= "2026-09-29" and f.startswith("pre"): continue
        if r["date_ist"] < "2026-09-29" and not f.startswith("pre"): continue
        add("RapidActionTeam", f, pts(r["ts_ist"]), None, fl(r["credits"]), is_est(r), r["purpose"])

# --- write master ledger ---------------------------------------------------------
with open(A / "master_ledger.csv", "w", newline="") as fh:
    w = csv.writer(fh); w.writerow(["repo", "source", "start_ist", "end_ist", "credits", "estimated", "purpose"])
    for e in sorted(events, key=lambda e: e["start"]):
        w.writerow([e["repo"], e["source"], e["start"].isoformat(sep=" "), e["end"].isoformat(sep=" "), e["credits"], int(e["est"]), e["purpose"]])

# --- chart (user's screenshot, +-0.3k, labelled days) -----------------------------
CHART = {"2026-09-21": 12500, "2026-09-22": 6800, "2026-09-23": 5600, "2026-09-24": 12400, "2026-09-25": 3000,
         "2026-09-26": 0, "2026-09-27": 4600, "2026-09-28": 9300, "2026-09-29": 13800, "2026-09-30": 14800,
         "2026-10-01": 7100, "2026-10-02": 0, "2026-10-03": 6200, "2026-10-04": 0}

def bucket(offset_h):
    """day buckets for a day boundary at local midnight of UTC+offset_h; spread interval events by minute"""
    shift = timedelta(hours=offset_h) - IST     # IST clock -> local clock
    out = collections.defaultdict(float)
    for e in events:
        s, t = e["start"] + shift, e["end"] + shift
        mins = max(int((t - s).total_seconds() // 60), 0)
        if mins == 0:
            out[s.strftime("%Y-%m-%d")] += e["credits"]
        else:
            share = e["credits"] / (mins + 1)
            for i in range(mins + 1):
                out[(s + timedelta(minutes=i)).strftime("%Y-%m-%d")] += share
    return out

if __name__ == "__main__":
    tot = collections.Counter()
    for e in events: tot[(e["repo"], e["est"])] += e["credits"]
    print("events:", len(events), " totals (repo, estimated?):", {f"{k[0]}|{'E' if k[1] else 'M'}": round(v) for k, v in tot.items()})
    print("grand total traced:", round(sum(e["credits"] for e in events)))
    results = []
    for h2 in range(-24, 29):      # offsets in half hours
        h = h2 / 2
        b = bucket(h)
        over = sum(max(0.0, b.get(d, 0) - c - 300) for d, c in CHART.items())
        viol = sum(1 for d, c in CHART.items() if b.get(d, 0) - c > 300)
        results.append((over, viol, h, b))
    results.sort(key=lambda x: (x[0], x[1]))
    print("\nbest day boundaries (UTC offset, overshoot credits beyond chart+0.3k, #days overshooting):")
    for over, viol, h, b in results[:8]:
        print(f"  UTC{h:+.1f}  overshoot={over:7.0f}  days_over={viol}")
    print("\nfixed reference offsets:")
    for name, h in (("UTC", 0), ("IST", 5.5), ("US Pacific (PDT)", -7), ("US Eastern (EDT)", -4)):
        b = bucket(h); over = sum(max(0.0, b.get(d, 0) - c - 300) for d, c in CHART.items())
        print(f"  {name:18s} overshoot={over:7.0f}")
    json.dump({"events": len(events), "traced_total": sum(e["credits"] for e in events),
               "best": [(h, over, viol) for over, viol, h, _ in results[:5]]}, open(A / "master_summary.json", "w"), indent=1)
```
