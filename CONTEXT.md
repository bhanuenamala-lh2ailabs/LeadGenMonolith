# CONTEXT — read this first in any new session

Condensed from the three source repos' Claude Code chats (Jul–Oct 2026) and live checks on 2026-10-04.
Full detail: `chat_context/summaries/{companyOps,RapidActionTeam,hubspot_part1,hubspot_part2}.md`.
Redacted transcripts: `chat_context/transcripts/`. Raw sessions: `chat_context/raw/`. Architecture: `docs/ARCHITECTURE.md`. Live facts: `docs/discovery/`.

## What this business does
LH2 acquires dormant assets from Indian companies (pre-2024 **codebases**, and internal **company ops data**: SOPs, playbooks, process/tooling) to license to frontier AI labs. Pipeline: source accounts -> find a decision-maker -> enrich a **+91 mobile** + LinkedIn URL -> push a deal to a cold-caller in HubSpot -> funnel (cold call -> interested -> meeting -> sample/one-pager -> LOI -> contract -> data delivery -> won). Daily funnel reporting measures it.

## The three HubSpot accounts / five funnels (verified live 2026-10-04)
| Slug | Portal | Origin repo | Pipelines (id) |
|---|---|---|---|
| `main` | 246754894 | hubspot | **Coding** (`default`, ex-"Scraped") · **CoOps ( Global )** (`2425754306`, ex-"Campaign", UK/US/AU/SG/Georgia ops-data calling; entry stage = *Cold Lead*) |
| `companyops` | 246897735 | companyOps | **Company Ops Cluster 1 India** (`default`) · **Cluster 2 India** (`2464812771`) |
| `rat` | 247485022 | RapidActionTeam | **Rapid Action Team** (`2575252183`; CAD/BIM + COBOL firms) |
`companyOps/context.md` wrongly describes portal 246754894. Always key by ids, never labels (the label "Company Ops Data" no longer exists). Stage ids repeat across pipelines — scope by (account, pipeline, stage).

## Standing rules (the user's words win; later entries supersede earlier ones)
1. **Mail:** every mail goes to **bhanu.enamala@lh2.ai** unless the user names another recipient. No automated mailers (a 15-min notifier once spammed 7 people; all GitHub Actions were disabled 17 Aug). Reports are dry-run by default; `--send` is explicit.
2. **+91 mobile and LinkedIn URL are hard gates** for pushing to HubSpot; mobile only (no landline, no email-only). Documented user overrides: Delhi IT batch, OutFlo accepted-connection pushes, Decks. CoOps (Global) is deliberately non-India with per-country gates (+44 UK, +61 AU, +65 SG).
3. **India-only sourcing** for scrapes (1 Sep) — except CoOps (Global).
4. **POC must be CEO/Founder/MD/COO/CTO tier** ("not hr or sales"). Headcount: >50 (Adtech/Mobility/Singapore), 50–1000 for TAM outreach, Cluster 1 floor 20 with a note.
5. **Whale list / LinkedIn target list: never to HubSpot** (stored as `suppression(kind='never_push')`).
6. **Spend gates:** ask before **any** Apollo credit spend (Apollo is off by default; budget guide 750 credits/day). `mixed_companies/search` is NOT free (1 credit per non-empty call); people `api_search` is free. SignalHire first for phone reveal, Apollo as fallback. Estimate cost and print worst-case before runs. Never run paid vendor calls from the monolith's sync/pull code.
7. **Dedupe against HubSpot first**, by deal/domain/LinkedIn, never name alone. One deal per company (multiple contacts on one deal). Name deals after the **company**, not the person.
8. **Never delete rejects locally** — every discovered account stays in the DB with verdict/score/reason; every funnel outcome (failures included) gets a named bucket.
9. **Deleting off-ICP deals:** only those still at the entry stage; ask before destructive deletes; archived deals are recoverable.
10. **Funnel semantics:** the outcome IS the stage. **"Engaged" = stage changes with `sourceType == CRM_UI` only** (a human click); entry-stage/"Leads Assigned" counts any source. IST day boundary. Report totals are live stage counts, not cumulative push totals. Inception only grows; the 30 Sep "count only deals since 15 Sep" change was **rejected by the user** ("revert to yesterday's way") — do not re-seed Inception from a cutoff.
11. **Routing:** Cluster 1 = Amisha + Manit (+R Kalyan); Cluster 2 = Vaishnavi + Tanisha; segments never cross clusters; each rep needs 100 callable leads/day; partition parallel runs by segment, never by rep.
12. **Behaviour:** verify numbers before reporting; stop and ask before destructive or outward-facing actions; do not route around permission-classifier denials; stick to what is asked.
13. **Dedup every lead against ALL THREE portals** (main, companyops, rat) before any push; a live deal in any portal blocks it. Use `python -m leadgen dedup <csv>` (local index + live confirmation). Added 2026-10-05: the first itsvc list claimed zero collisions but 35 of 249 already had deals.
14. **Only callable numbers** are pushed: +91, or +44 / +61 / +65 for the global pipeline. **LinkedIn URL is always pushed.** No number or no LinkedIn = no push.
15. **Email = the POC's own verified work email**, pushed when it exists, otherwise pushed without; always try to get it first (SignalHire, then Apollo when not paused). Generic company addresses are not pushed.
16. **Apollo is PAUSED** (user, 2026-10-05 night): no Apollo call for anything until they say go. `APOLLO_PAUSED=1` in `.env`. Balance 7,637 lead credits; SignalHire 0.

## Where everything is (this repo)
| Need | Go to |
|---|---|
| Unified DB | `db/leadgen.sqlite` (SQLite, 14 versioned migrations). Dictionary: `docs/DATA_DICTIONARY.md`. Design decisions: `docs/review/schema-decisions.md` |
| Run things | `docs/RUNBOOK.md`; CLI `.venv/bin/python -m leadgen …` |
| Original repos, untouched | `legacy/{companyOps,RapidActionTeam,hubspot}` (+ git history: `archive/git_bundles/*.bundle`) |
| Credentials | `.env` (namespaced per portal), `secrets/` (Google). Both gitignored |
| Daily unified report | `reports/daily/<date>/` |

## Known stale / contradictory items (do not trust blindly)
- The hubspot chat repeatedly said "~96,000+ lead credits remaining". Wrong: 96,439 were USED of 104,280; remaining was 7,841 on 2026-10-05 (7,637 after the POC-email run). Memory note `apollo-credit-balances` ("direct_dial exhausted, ~10.9k") is stale.
- Apollo's usage chart days appear to be US Pacific days, not UTC or IST (inferred; see `docs/audit/apollo/MASTER_RECONCILIATION.md`).
- Memory notes `funnel-flow-revised` ("nothing applied") and `dashboard-gtm-metrics` ("not built") are **stale**: the migration ran 5 Aug and the dashboard was built 5–6 Aug.
- Apollo credit figures conflict (allowance 104,280 / ~93.4k consumed / ~10.9k left on 3 Oct vs a "96,000+ remaining" line); "direct-dial exhausted" vs available. Check the Apollo dashboard before planning spend.
- "Cluster 1/2" meant healthcare/real-estate on 18 Sep (flipped twice) and means the rep-pair/segment model from 29 Sep. The latter is current.
- Roles flip-flopped four times in Aug; the 18 Aug "stage-ownership bands" were later relaxed ("all four work the complete funnel").
- MCA / data.gov.in API (`MCA_API`) refused connections on 3–4 Oct; Anthropic API key in the hubspot `.env` was reported invalid (401) in the chats; the Gmail readonly token (kartik.pillai) is revoked.
- Snapshot chains in the old repos have gaps (RAT 10-02→; MAIN 09-26/27, 10-02/03; COMPANYOPS 09-26/27/28, 10-02). The monolith recomputes from stage history instead.

## Open work at the end of the source chats (2026-10-01..04)
- itsvc-tam (Indian IT services, 61.8k companies): paused; needs a second discovery source (MCA down). **249 CEO/Founder/MD/CTO-tier, verified-+91 POC leads: PUSHED on 2026-10-05, 204 of them (45 held out: 10 reviewer-flagged, 35 already in HubSpot). See `docs/PUSH_LOG_2026-10-05.md`; do not push again (the recipe below is kept for reference).**: `legacy/hubspot/itsvc-tam/out/poc_callable_250.csv` (249 rows despite the filename; cols: First Name, Last Name, Title, Company, Domain, City, Headcount Band, Founded, LinkedIn URL, Mobile, Email, Codebase Tier, Evidence). Band A/B only (50–500 headcount + resolved domain), sourced from free Apollo org search (0 Apollo credits were ever spent discovering these — cost was only in the later per-contact reveal). To push: target account `main` (portal 246754894), pipeline `default` (Coding), stage `3992480462` (Cold Call) — **re-check each company against live HubSpot first** (this file is from 4 Oct; standing rule 7 applies), then for each row create a Deal (`dealname`=Company, `pipeline`="default", `dealstage`="3992480462", `hubspot_owner_id`=<whoever's named>, optionally `lead_source`) **and** a Contact (`firstname`/`lastname`/`jobtitle`=Title, `linkedin_url`, `phone`+`mobilephone`=Mobile, `email`) via `POST /crm/v3/objects/contacts`, then associate them with `PUT /crm/v4/objects/deal/{dealId}/associations/default/contact/{contactId}` — **a bare Deal with no associated Contact is useless to the caller** (phone/email/LinkedIn live on the Contact, not the Deal; this exact bug was hit and fixed live on 3 Oct — don't reintroduce it). Flagged for exclusion on review, not yet dropped from the file: ADCC Academy, TechnoKraft Training, Fireblaze AI School, Pesto Tech, AnalytixLabs (training institutes), Weboin, SERP WIZARD (marketing/SEO agencies), Petroleum Engineers Associates, NSUT Incubation Foundation (not software companies), Namo Padmavati Outsourcing (possible BPO, lower confidence).
- CAD (RAT): 542 gap-fill ICP matches + 158 held leads not pushed; tracks 3–6 not enriched; COBOL: ~461 weak-signal firms unpursued.
- UK Proptech: wrong-tier deals being fixed; fintech US/UK inventories not in any local corpus.
- Singapore: 2 of 6 segments covered; Fintech edtech/market-research builds awaiting go-ahead; n8n report rollout pending; Lamiya/Yuktha CC bulk removal never executed.
