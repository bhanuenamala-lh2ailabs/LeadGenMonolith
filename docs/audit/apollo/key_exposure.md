# Apollo API key exposure and non-script credit paths (forensic review)

Prepared 4 Oct 2026 (laptop local time, IST). Read-only review; no network calls; the key value is never printed or written here. All key comparisons were done inside Python (exact string equality) and are reported as counts, paths and line/time evidence only. Keys other than the current one are labelled K1..K5 (chronological), never quoted.

Question from the user: the Apollo chart shows 96,439 credits used in the cycle 21 Sep to 4 Oct. The three repos explain only about 19k of the 42.6k used since 29 Sep. Is it a teammate or a key leak?

## 0. Bottom line (confidence in brackets)

1. **Nothing on this laptop shows the current key leaving this laptop.** Exact-value hits: 0 in any git commit of the 3 original repos, their 3 bundles, the nested lh2-pipeline repo and the monolith; 0 in the zip/tgz archives under Desktop (at least 20 opened in memory); 0 in the 4 SQLite DBs (4.5 GB); 0 in n8n/Zapier/Make/Apps Script references; 0 in any GitHub workflow. [high for "not in git/archives", cannot see Gmail, Slack, Drive, Apollo, Downloads]
2. **The key was rotated on 29 Sep at 12:51:15 to 12:51:32, 13 minutes after the ~12:38 top-up, in all three repos' `.env` at once** (proven from VS Code Local History, section 2). The old key (K5) was the live key for the whole 21 to 28 Sep window; the current key (K6) has only existed since 29 Sep 12:51. So "leak of the current key" can only explain the post-29 Sep part, and "leak of K5" can only explain pre-rotation use plus anything after it if K5 was never deleted in Apollo. [high on timeline; unknown whether K5 was revoked]
3. **The Apollo key belongs to a shared identity, "Analytics Labs <analytics@lh2.ai>"** (api_profile response seen in chat on 9 Sep and 11 Sep), and the Apollo account has an administrator who regenerates keys and sets scopes (the key was invalidated server-side on 18 Aug "when your admin went in to check scopes"). A shared login plus a documented process that sends callers into the Apollo UI make **teammate UI usage the most plausible source of the unexplained credits**. [medium]
4. **A teammate with a copy of the key running scripts is the second most plausible**, because team practice is to hand over the whole `.env` (emailed to Purunjay on 14 Aug), and the RapidActionTeam repo (owned by Nandan's GitHub) needs `apollo_api_key` in its `.env`. But the 14 Aug mail predates every Apollo key (first one K1 appears 18 Aug 17:44), and nothing on the laptop shows K5 or K6 being sent to anyone. [medium-low]
5. **An outsider using a leaked key is the least supported** (no public artifact, no committed copy, no synced folder). It cannot be excluded from this laptop alone because the key sat in plaintext in about 25 local files, in Claude Code transcripts that were sent to the model API, and in a `.env` that was emailed (an older version). [medium]

The one check that separates all hypotheses: Apollo web app > Settings > Credit usage, filtered by **Team member**, **Action** and **day**, plus Settings > Integrations > API keys (list of keys, creation date, last used). Section 5 gives per-hypothesis checks.

## 1. Exposure inventory (exact key, first-8 prefix, base64 and URL-encoded variants)

Scan roots: /Users/bhanu/Desktop, Documents, ~/.claude, ~/Library/Application Support (Code, Claude, Slack, Google, Microsoft), ~/.config, ~/.ssh, ~/bin, ~/Claude, ~/AppData, /tmp and the agents' scratch area. 35,802 files under Desktop were read (excluding node_modules, .venv, .git object stores); 55 archive or bundle files under Desktop, of which all zip/tgz (at least 20) were opened in memory. Every hit contained the full key; there were **no prefix-only, base64 or URL-encoded hits**, so no truncated or encoded copies exist.

### 1a. Files containing the current key (K6)

| # | Path | Kind | mtime | Size | Occurrences | Risk |
|---|---|---|---|---|---|---|
| 1 | /Users/bhanu/Desktop/hubspot/.env | .env original (key `apollo_api_key`) | 2026-09-29 12:51:15 | 1,581 B | 1 | Source copy; gitignored (`*.env`). Low |
| 2 | /Users/bhanu/Desktop/RapidActionTeam/.env | .env original | 2026-09-29 12:51:32 | 263 B | 1 | Gitignored. This repo's remote is nandanlh2's GitHub; a clone of it by anyone never contains .env. Low |
| 3 | /Users/bhanu/Desktop/companyOps/.env | .env original | 2026-09-30 16:21:12 | 1,128 B | 1 | Gitignored. Low |
| 4 | .../LeadGenMonolith/.env | monolith .env (`APOLLO_API_KEY`) | 2026-10-04 19:42:59 | 2,878 B | 1 | Gitignored (`.env`, `.env.*`); monolith has 0 commits. Low |
| 5-7 | .../LeadGenMonolith/legacy/{RapidActionTeam,companyOps,hubspot}/.env | legacy .env copies | 29 Sep 12:51:32 / 30 Sep 16:21:12 / 29 Sep 12:51:15 | 263 / 1,128 / 1,581 B | 1 each | Gitignored (`legacy/**/.env`, `legacy/`). Low |
| 8 | .../LeadGenMonolith/chat_context/raw/hubspot/2bf3c003-....jsonl | chat transcript (raw) | 2026-10-04 00:36:14 | 292 MB | 2 (3 Oct 13:37Z) | Contains the key because the agent grepped `.env`. Gitignored (`chat_context/`). Medium: a 292 MB file that someone may zip and share |
| 9 | .../chat_context/raw/hubspot/2bf3c003-.../subagents/agent-a1f42c540ad977d1d.jsonl | chat transcript (subagent) | 2026-10-01 17:06:39 | 374 KB | 1 (1 Oct 11:31Z) | Same. Medium |
| 10 | ~/.claude/projects/-Users-bhanu-Desktop-hubspot/2bf3c003-....jsonl | Claude Code session log | 2026-10-04 21:18:36 | 293 MB | 2 | Outside the repo. Same lines were also sent to the model API as tool output. Medium |
| 11 | ~/.claude/projects/-Users-bhanu-Desktop-hubspot/2bf3c003-.../subagents/agent-a1f42c540ad977d1d.jsonl | session log | 2026-10-01 17:06:39 | 374 KB | 1 | Medium |
| 12-19 | ~/Library/Application Support/Code/User/History/{-6bd148a8, 2fee7578 (x6), 4409e120}/... | VS Code Local History snapshots of the three `.env` files | 29 Sep 12:51:15 to 30 Sep 16:21:12 | 263 to 1,581 B | 1 each | Local only, but an unmanaged archive of every `.env` edit. Low-medium |
| 20-21 | ~/Library/Application Support/Code/User/workspaceStorage/{0c5211c4..., 5cb91fda...}/state.vscdb | VS Code workspace state DB | 2026-10-04 20:47 | 184 KB / 90 KB | 1 / 2 | Local. Low |
| 22-24 | agents' scratch files in /private/tmp/claude-501/.../scratchpad (idx.jsonl, main_idx.jsonl, w/msgs.pkl) | derived from transcripts by other audit agents | 4 Oct 22:45 to 23:44 | 2 to 4 MB | 1 each | Temporary; delete after the audit. Low |

Not synced, shared or committed anywhere found: `git log --all -S` for the full key, the first 8 and 12 characters, the last 8 characters and the base64 prefix returned **0 commits** in each of companyOps (22 commits), RapidActionTeam (1), hubspot (3), lh2-pipeline's nested `.git-standalone-repo-backup` (60), the three bundles in archive/git_bundles (re-cloned, same counts) and the monolith (0 commits). No `.env` is tracked in any repo (only `.env.example` files, empty values). Desktop is not iCloud-synced (no ~/Library/Mobile Documents, `FXICloudDriveDesktop` = 0) and there is no Dropbox/Drive folder.

### 1b. Places that do not contain the key (checked)

companyOps/cf.tgz (19.8 MB), companyOps/claudeContext/*.zip (6, including apoorv_project_export.zip that was emailed to Apoorv), falakgroup...zip, repo-quality-score.zip, claudecodeContextRecover/claude-migration.zip, taskRESEARCH/*.zip, first..fourth/*.zip, BhanuBackup zip; db/leadgen.sqlite (3.1 GB), its pre-0015 backup (785 MB), tam.sqlite3 x2 (640 MB each); all CSVs; all markdown including docs/, README, CONTEXT.md; `.github/workflows` of all repos; shell dotfiles and ~/.claude/settings.json; Slack, Claude desktop, Google/Chrome and Microsoft app-support folders; Pictures/Movies/Music/Public.

### 1c. Not checked (limits)

- ~/Downloads: macOS denied access ("Operation not permitted", folder has about 244 entries). Skipped as instructed. Nested archives inside archives were not unpacked.
- Packed git objects were checked via `git log -S` (decompresses), not by file scan; remote GitHub copies, Gmail sent items, Slack/Drive/Sheets, Apollo's own logs and any other machine cannot be seen.
- Old keys K1 to K5 were seen only in transcripts and VS Code history, never tested (no network).

### 1d. Files that were sent to other people (from chat evidence)

| Date | What | To | Contains an Apollo key? |
|---|---|---|---|
| 14 Aug 2026 11:50 IST | **Full hubspot `.env` as attachment `lh2.env`** via crm_mirror/enrich/gmail_sender.py (message id in chat), at the user's instruction "with the full env attached" | purunjay.choudhary@lh2holdings.com (git author `ChPuru`) | No Apollo key existed yet: the first Apollo key (K1) was written 18 Aug 17:44. The assistant advised rotating the other keys; the summary records "rotation advised, no answer". HubSpot token, SignalHire, Apify, OutFlo, Calendly PAT, GitHub PAT, Anthropic key were in that file |
| 8 Sep 2026 | hubspot repo pushed to github.com/bhanuenamala-lh2ailabs/hubspot_scraping "for an intern"; PAT came from `.env`; `.env` gitignored | intern (collaborator, unnamed) | No (git -S = 0). Intern would still need keys from the user out of band |
| 17 Aug | LH2_CRM_FULL_DATA.zip and LH2_ANALYST_PACK.zip emailed | analysts | Predates any Apollo key |
| Aug to Oct | many CSVs emailed (Kartik, Ishpreet, Lamiya, Nandan's `sreenandan.m@lh2.ai`, Apoorv's Claude export zip) | teammates | Output data only; no key in any CSV on disk |

Mail-sending scripts (gmail_sender.py, email_transport.py, rapidactionteam_dashboard_mail.py, company_ops_cluster_report_send.py, send_csv/cobol mail helpers) attach whatever path they are given; none contains logic that attaches `.env` automatically. The only `.env` attachment found is the 14 Aug one.

## 2. Key history and rotation (hashes compared in Python; VS Code Local History + chat evidence)

Equality: the current key value is **identical in all 7 `.env` copies** (3 originals, 3 legacy, monolith), 22 characters, all of them "same value in every repo".

Chronology of the Apollo key value in `.env` files (local time):

| Key | First seen | Where | Evidence | Notes |
|---|---|---|---|---|
| K1 | 18 Aug 17:44:41 | hubspot/.env | VS Code history | First Apollo key; free tier tests |
| K2 | 18 Aug 18:07 | hubspot | VS Code history; chat 19 Aug 15:34 | Key invalidated server-side at 18 Aug ~18:05 ("is_logged_in flipped to false ... regenerated when your admin went in to check the scopes") |
| K3 | 19 Aug 15:41 | hubspot | history; chat | "Key updated" |
| K4 | 20 Aug 12:07 | hubspot | history; chat | In use until 9 Sep |
| K5 | 9 Sep 11:52 (hubspot); by 15 Sep 15:22 (companyOps); by 25 Sep 16:59 (RapidActionTeam) | all three | history, chat 15 to 21 Sep | **Live key for the whole 21 to 28 Sep window** |
| K6 (current) | **29 Sep 12:51:15 hubspot, 12:51:22 companyOps, 12:51:32 RapidActionTeam** | all three | history | Pasted in three files in 17 seconds. Top-up was at about 12:38; chat shows "apollo must be back check now" at 12:52 |

So: yes, the key was rotated (or at least replaced) on 29 Sep. The reason is not stated in any chat, but the sequence is: 05:03Z user asks "what happened with all credits" (10:33 IST), 10:49 IST the exports analysis says "a human doing bulk exports"; 12:38 top-up; 12:51 new key everywhere. In the companyOps chat the user says "i was scrutinised for those 16k export credits".

What the laptop cannot show: whether K1 to K5 were **deleted** in Apollo. Creating a new key does not delete old ones. If K5 is still active, any holder of an old `.env` (Purunjay 14 Aug copy has none of them, but anyone given a later copy has) can still spend.

Other facts about the key:
- Owner identity: "Analytics Labs", analytics@lh2.ai, one team id. The key is on a "master key" tier per the dashboard (chat 2 Sep), so it has full API scope.
- The same key is used in all repos and by every pipeline; per-script attribution is impossible at the Apollo side (usage is per account/user/key, which is exactly the report the user needs).
- Plan: Professional seat plus add-on; contract "3 seats" was discussed (Organization plan); billing renews on the 20th. Number of seats actually active is not on the laptop.

## 3. Git and author findings

| Repo | Remote | Commits | Authors | Apollo-calling code |
|---|---|---|---|---|
| companyOps | github.com/bhanuenamala-lh2ailabs/lh2-companyOpspipeline (master, plus `outflo-daily-sync`) | 22 | corpDev <corpdev@lh2holdings.com> 7 (6 to 10 Aug), ChPuru <puruchoudhary9b@gmail.com> 5 (17 to 27 Aug), Nandan <sreenandanms04@gmail.com> 2 (8 Sep, 23 Sep), Bhanu (3 hostnames) 8 | `git -S api.apollo.io`: **1 commit, bb4d1b5 (21 Sep, Bhanu): opsdata/phone_enrich_apollo_signalhire.py**. ChPuru and Nandan commits touch no Apollo code |
| RapidActionTeam | github.com/**nandanlh2**/RapidActionTeam (main) | 1 | Nandan <sreenandanms04@gmail.com> (25 Sep 15:00, initial commit of 21 files) | Initial commit includes phone_enrich_apollo_signalhire.py (calls api.apollo.io) and Apollo references in 10 tracked files; `.env.example` tells users to supply `apollo_api_key` |
| hubspot | github.com/bhanuenamala-lh2ailabs/hubspot_scraping (main) | 3 | Bhanu Enamala <bhanu.enamala@lh2.ai> 3 | 19 tracked files call Apollo, all in Bhanu's commits (8 Sep, 18 Sep); 31 more Apollo-calling scripts in godown/, rat_cad_discovery/, TAMBuildSpecs/, itsvc-tam/ are **untracked** (no authorship in git) |
| lh2-pipeline (nested, pre-8 Sep history) | github.com/bhanuenamala-lh2ailabs/lh2-pipeline | 60 | enamalaBhanuSai 23, corpDev 18, "LH2 Dashboard" 13 (bot), Bhanu 4, ChPuru 2 | 5 commits mention Apollo in text; none contains `api.apollo.io` |

Observations:
- **No one other than the repo owner wrote Apollo-calling code in the two repos where the owner is Bhanu/corpDev**; the only third-party-authored Apollo code is Nandan's RapidActionTeam initial commit (a repo whose remote is Nandan's own GitHub, so Nandan holds a full copy of Apollo-capable code and has a documented need for an `apollo_api_key`).
- Nandan (Sreenandan M, sreenandan.m@lh2.ai) and Purunjay (ChPuru, purunjay.choudhary@lh2holdings.com) both push to companyOps from their own machines (commit identities are not Bhanu's hostnames). So at least two other people operate this code base on other laptops. Which `.env` they use is not recorded.
- "corpDev <corpdev@lh2holdings.com>" and the GitHub org `corpdev1` appear as an older identity (6 to 10 Aug, same Aug sessions as Bhanu's chat). Treat as an additional LH2 identity of unknown owner.
- Collaborators and CODEOWNERS: none in any repo (no CODEOWNERS file); GitHub collaborator lists cannot be read offline. The 8 Sep chat says an intern was to be added to hubspot_scraping; the 14 Aug chat says Purunjay was "already shared" on the dashboard repo.
- The hubspot repo publishes a GitHub Pages dashboard (public URL) from Actions; secrets are repository secrets, not in files.

## 4. Automation inventory (anything that can call Apollo without a typed command)

| Item | Who/when | Trigger | Calls Apollo? | Credit relevance |
|---|---|---|---|---|
| companyOps/.github/workflows/deploy_ops_dashboard.yml | built by ChPuru/Bhanu, Aug | cron `9,39 4-13 * * *` and 5 evening slots (UTC) | No (secrets: HUBSPOT_API_KEY only) | None |
| companyOps/.github/workflows/outflo-sync.yml | ChPuru, 17 Aug | cron `3,17,33,49 5 * * *` | No (HUBSPOT, OUTFLO) | None |
| RapidActionTeam/.github/workflows/dashboard.yml | Nandan, 25 Sep | cron `0 */6 * * *` | No (HUBSPOT) | None |
| hubspot/.github/workflows/deploy-dashboard.yml and lh2-pipeline: deploy-dashboard, daily-report (13:00 UTC), monthly-run, nightly-enrich (schedules commented out) | Bhanu/ChPuru | hourly / 30 min / daily | No Apollo secret anywhere (secrets: HUBSPOT, GMAIL, ANTHROPIC, SIGNALHIRE, GOOGLE) | None |
| ~/Library/LaunchAgents/ai.lh2.cadoutreach.plist | 27 Aug | every 1800 s | No (send_one.py has 0 Apollo references; the job currently fails with "Operation not permitted" on the script) | None |
| crontab | n/a | no crontab for user | n/a | None |
| `auto_push.py --threshold 10 --poll 30` (hubspot/godown/founder_id) | started 21 Aug 07:55, still running (pid 90692, 44 days) | 30 s polling loop | **No** (0 Apollo references in it and in searchq_enrich.py); it pushes already-enriched leads to HubSpot | Not a credit source. It is the source of "mystery" Cobol/CAD pushes into HubSpot |
| `serve.py` (pid 97017, since 24 Aug) | local | HTTP server | Not Apollo-related | None |
| `cloudflared tunnel` + `leadgen.sh_receiver` on port 8787 (companyOps, since 29 to 30 Sep) | Bhanu's companyOps session | long-running | SignalHire callback receiver, inbound only (public trycloudflare URL with a secret path) | No Apollo calls; but a public inbound endpoint is running |
| `ScheduleWakeup` autonomous loops in Claude sessions | companyOps 9 calls, hubspot 2, plus 1 cron tool call | self-paced | The companyOps loop waited on "Apollo headcount enrichment" batches | Credits can be spent while the user is away, but only inside the user's own sessions and logged in their ledgers |
| `poll_push_cobol.py` (RAT) | 1 Oct | "safe to run as a loop" | No direct Apollo call (imports to_e164_india from phone_enrich_apollo_signalhire.py) | None |
| `enrich_batch3.py ... enrich_batch9` (hubspot/TAMBuildSpecs/India/India_COBOL_IP) | hubspot session 1 Oct | background workers (up to 4) | **Yes** (people/match + reveal, `--max-credits 900`) | Already counted in docs/audit/apollo/hubspot.md (1 Oct COBOL total 4,160); `tail -f /tmp/cobol_enrich9b_w3.log` is still running |
| "AshishCluster" (HubSpot) | seen 1 Oct in RAT portal as `hs_object_source_detail_1`, source id 54087015 | n/a | **Not a person and not an Apollo caller.** The RAT docs show *all* INTEGRATION events (1,227) carry source id 54087015, i.e. it is simply the **name of the RAT HubSpot private app whose token the scripts use**. The 558 deals "created by auto_push/AshishCluster" are the user's own hubspot-session scripts | Do not treat as a third party. Anyone holding the RAT HubSpot token appears under that name, so it also hides teammates' scripts |
| n8n | companyOps `company_ops_cluster_report_send.py`, `full_funnel_*` (n8n_cloud_url/api key, removed from .env 28 Sep) | report mailer only | No | None |
| Zapier, Clay, Make, Apps Script, Wiza, OutFlo | Wiza used for discovery lists; OutFlo is the LinkedIn tool (9 seats) | n/a | No Apollo wiring found in any code or doc; "Apollo integration: Absent" in docs/sop/SALES_FUNNEL.md | None found |
| Apollo-side: sequences, dialer, enrichment workflows, Chrome extension, mobile app, HubSpot-Apollo sync, saved lists | not visible | n/a | Cannot be verified from this laptop | See hypothesis 4 |

Conclusion for (3): **no unattended automation on this laptop or in GitHub Actions can burn Apollo credits other than the user's own long-running background enrich workers, which the audits already count.** Everything else unattended is HubSpot, OutFlo or SignalHire related.

## 5. Non-API credit burners and who plausibly had access

Evidence that humans use the Apollo UI:
- **Caller SOP** (hubspot/docs/sop/CALLER_SOP.md, SALES_FUNNEL.md, lh2-pipeline/SALES_SOP_HUBSPOT.md): on a wrong number the caller must do an "Apollo lookup" (off-HubSpot, in the Apollo app) and redial. Task name "Apollo lookup - wrong number", priority HIGH, due today. Every phone reveal in the UI costs credits (8 per mobile in the unified model; the mobile bucket held 35,464 on 29 Sep).
- The user pasted on 29 Sep a dashboard screenshot with buckets Exports 16,365 / Phone 35,464 / Waterfall 1,175 / Email 786 and wrote that he was "scrutinised for those 16k export credits".
- **No local script uses Apollo's waterfall parameters** (`run_waterfall_*`: 0 hits in the three repos), so the Waterfall 1,175 bucket (pre-window) is not ours. Phone numbers via scripts use `reveal_phone_number`; exports are not an API action.
- Apollo seats: one Professional seat plus add-on, with a 3-seat Organization plan quoted. An Apollo "admin" exists who can regenerate keys (18 Aug). The key owner is a shared mailbox identity (analytics@lh2.ai), which suggests a **shared login** is how several people sign in, which would explain why a Team-member filter in Apollo might show only "Analytics Labs".
- Human sourcing/enrichment roles seen in chats and docs (not proof of Apollo access): callers Yuktha Anand, Lamiya Saleem, Ishpreet Sood (pod lead, runs the COBOL list), Shobit Gupta (pod head), Shagufta Khan and Akarsh G B (new, 23 Sep), Harsha A (archived seat that still owns cold-call deals), Prerna Jain, Ashish Ranjan (RAT HubSpot owner, 131 API-created meetings in July); Company Ops callers Tanisha Sharma, Amisha Pujari, Vaishnavi Kannan, Manit Rastogi, R Kalyan, Anuj Chahar; Kartik Pillai (ops-data/vetting, archived HubSpot seat); builders Nandan, Purunjay, an unnamed intern (8 Sep) and Apoorv Vashist (recipient of a Claude export). Nobody is documented as having an Apollo seat or a copy of the key.
- No evidence either way for Chrome extension use, the Apollo mobile app, Apollo sequences/dialer, or Apollo-HubSpot sync. The user's own docs say HubSpot integration with Apollo is absent.

## 6. Ranked hypotheses for the unexplained credits

Quantities from the other audits: before 29 Sep, scripts explain about 1,433 of 16,365 Exports (91% unexplained); from 29 Sep 10:34 to 3 Oct 22:34, +39,553 consumed of which about 23,260 (55%) are not attributable to hubspot, companyOps or RAT, split about 11.6k phone-reveal and 11.7k unlock/export/search.

### H1. A teammate (or several) using the Apollo UI under the shared login (including callers' SOP lookups and bulk exports). Likelihood: highest
For:
- The documented SOP sends callers to Apollo on every wrong number; the volume of wrong numbers is high (a Wrong Number stage and tasks exist).
- The key identity is a shared mailbox, so a shared UI login is likely.
- 91% of exports are unexplained before 29 Sep and the unexplained flow **continued after the key was rotated**, which a UI user is unaffected by (rotation only blocks API callers).
- The Waterfall bucket (1,175) cannot come from any local script.
- Exports are charged per row in the UI; a single bulk export of 2,000 rows would explain the 16k in about 8 clicks.
Against:
- Nobody is documented as having a seat; the unexplained mix (reveals and unlocks both) is also consistent with scripts.
- 11.6k phone credits = about 1,450 reveals in 4 days is a lot for manual lookups unless several people do it.
Confirm with: Apollo > Settings > Credit usage (Team members tab, Action filter, daily). If the actor is "Analytics Labs" only, also check Settings > Security > login history or ask the admin who knows the shared password.

### H2. A teammate with a copy of an `.env` (Nandan, Purunjay, the intern, or a contractor) running scripts or an agent on another machine with the same key. Likelihood: second
For:
- Handing over the whole `.env` is established practice (14 Aug mail, explicit rotation warning ignored).
- RAT code (needs `apollo_api_key`) lives in Nandan's own GitHub repo; he and Purunjay push to companyOps from other machines; the cluster-report sender and push scripts are theirs.
- Signature in the API: unexplained credits include both people/match unlocks and reveals, the kind of work these scripts do.
- Same pattern was already noted for SignalHire ("someone else also uses the key" 19 Aug).
Against:
- Key was replaced on 29 Sep; for post-29 Sep use someone would have needed K6 or an undeleted K5. No record of K5 or K6 being mailed.
- The 14 Aug `.env` contains no Apollo key.
- The audits found 0 local call sites for the unexplained part, which fits another machine but also fits H1.
Confirm with: Settings > Integrations > API keys: list of keys, creation date, **last used**. If K5 (or an unknown key) shows recent use, someone else is calling. The usage report grouped by API key (if available) splits K5/K6/unknown. Ask Nandan and Purunjay in writing which `.env` they use.

### H3. A previous key (K1 to K5) that was never deleted, still in someone's old `.env`, in a mail attachment, in a cloned machine or in a Claude/IDE history. Likelihood: third
For: six key values in six weeks, no record of deletion; K5 sat in three repos' `.env` for 20 days and in multiple local logs. A new key does not invalidate an old one.
Against: no evidence anyone outside the laptop got K5; an old key would be used with the same signature as H2.
Confirm with: API keys page; any key other than K6 that is still active should be deleted. Test status of old keys with `GET /api/v1/auth/health` only after the user decides (this review made no network calls).

### H4. Apollo-side automation or product features (sequences, enrichment workflows/AI credits, auto-enrich of saved lists, Chrome extension "reveal on visit", HubSpot/LinkedIn sync, dialer). Likelihood: fourth
For: the Waterfall and Email buckets, and the fact that several credit actions do not map to scripts; a team member may have configured an enrichment workflow or a saved list auto-reveal.
Against: no evidence for any of these in docs or chats; the user's docs say no Apollo integration exists in HubSpot.
Confirm with: Apollo > Settings > Integrations and Workflows/Sequences list; Credit usage > Action filter ("Email enrich", "Waterfall", "Export", "Phone reveal", "AI").

### H5. An outsider using a leaked K5 or K6 by API. Likelihood: lowest among external possibilities
For: the key lived in plaintext in about 25 local files and in model-bound transcripts (2 sessions on 1 and 3 Oct); emailed `.env` practice; shared GitHub Pages repo.
Against: 0 commits, 0 public files, no sync folder, no webhook relay that logs the key, no workflow with an Apollo secret, no leak of K6 before it existed on 29 Sep. A random outsider scraping GitHub finds nothing here. Apollo keys have not been tied to the 429s the scripts saw (rate limiting appeared only on the user's own bursts).
Confirm with: API-key-level usage and "last used" timestamps; unusual hours (nights IST) or request patterns not tied to any known batch.

### H6. The user's own sessions beyond the audited ledgers (orphan background workers, parallel Claude sessions in other repos). Likelihood: low, mostly covered
For: 3 long-running processes (auto_push, serve.py, enrich_batch3) found on 1 Oct that the RAT assistant did not start; enrich_batch workers killed and restarted; ledgers missing for some runs.
Against: none of the other 16 Desktop folders (BuyerIntel, strategyScraping, taskRESEARCH, etc.) contains Apollo code or a key; the audits already count the hubspot sessions.
Confirm with: the three repo audits plus a rerun of ps (no Apollo-capable process runs now: only auto_push, serve.py, cloudflared and sh_receiver).

## 7. Recommendations

1. **Rotate now, and delete rather than add**: in Apollo > Settings > Integrations > API keys delete K1 to K5 and K6, create one new key per consumer (`hubspot`, `companyOps`, `RapidActionTeam`, `monolith`, and one per teammate who legitimately scripts), with the narrowest scope (no master key); record owner and purpose in the key name. Then the usage report by API key will do the attribution this review could not.
2. **Stop the shared UI login**: give each human their own Apollo seat (or remove seats), disable the shared analytics@lh2.ai password, require 2FA, and use Settings > Team to list seats.
3. **Enable what Apollo gives you**: credit usage by Team member and Action (daily), export permission limited to the admin, per-user credit caps/limits if the plan allows, usage alerts at 50/75/90%, and an email to the admin for any export above N rows. Review sequences, workflows, saved-list auto-enrich and connected apps; disable the Chrome extension for non-admin seats.
4. **Make the SOP cheaper**: the caller "Apollo lookup" step should be a request to the data owner or limited to a few seats; log each lookup.
5. **Stop spreading `.env`**: never mail `.env`; give teammates their own keys; keep keys in a password manager or GitHub secrets. Rotate everything that was in the 14 Aug attachment (HubSpot private-app token, SignalHire, Apify, OutFlo, Calendly, GitHub PAT, Anthropic) because it travelled by email and the assistant's warning was not acted on.
6. **Clean the local copies**: remove the key from `chat_context/raw` and from ~/.claude session logs (or treat those directories as secrets), delete VS Code Local History for `.env`, delete the agents' scratch files, keep `chat_context/` and `legacy/` out of any zip that is shared. Stop assistants from printing `.env` (use `grep -c`).
7. **Tag each script's calls**: log the key id and a run id per call into `db/leadgen.sqlite` `cost_ledger` so that unexplained usage = Apollo total minus ledger by key.
8. **Ask humans**: a message to Nandan, Purunjay, the intern and the callers: who has Apollo access, who uses which key, who ran exports on 21 Sep to 4 Oct.

## 8. What this laptop cannot show, and confidence

- Whether K5 and earlier keys are still active, who is in the Apollo team, which seats exist, any export history, login history, API-key last-used, source IPs. These are all one Apollo screen away and decide H1 to H5.
- What was said in email or Slack after the transcripts end (Oct 3 22:53 IST for the companyOps session, Oct 4 00:36 for hubspot, Oct 1 17:06 for RAT).
- Downloads (macOS denied) and nested archives.
- Authorship of 31 untracked hubspot Apollo scripts is by chat only (Bhanu's sessions); I did not find a script written by anyone else, but git cannot prove it.
- Confidence: exposure and git facts high; rotation timeline high (three independent sources: VS Code history, `.env` mtimes, chats); ranking of hypotheses medium to low because it is built on absence of local evidence.
- Note: while reading transcript lines for the key's context I displayed an unrelated Apify token in the tool output of this review (not written to any file). Treat that token as exposed and rotate it with the others.
