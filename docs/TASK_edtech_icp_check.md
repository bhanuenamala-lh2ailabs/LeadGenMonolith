# TASK — ICP check on 594 untouched EdTech companies

**Owner of this task:** Claude Code, in this repo (`LeadGenMonolith`).
**Written:** 2026-10-05, handed over from the companyOps work.
**Read `CONTEXT.md` first.** Standing rules 2, 4, 6, 7, 8, 9 and 12 all bind here. This file
adds only what CONTEXT.md does not: the EdTech ICP, the specific way it has already been got
wrong in production, and what "run the ICP check" means step by step.

---

## 1. Inputs — already in this repo

| File | What it is |
|---|---|
| `data/inbox/edtech/edtech_campaign_untouched_594.csv` | **Your work queue.** 594 companies, one row each, pre-sorted |
| `data/inbox/edtech/campaign_source_833_people.csv` | The original export: 833 people, 798 companies. Columns: `firstName, lastName, headline, location, company, title, listLinkedinUrl` |

**The pipeline you need is already here too**, in `legacy/companyOps/tam/`:

| Path | What it gives you |
|---|---|
| `legacy/companyOps/tam/leadgen/` | `crawl.py`, `classify.py`, `score.py`, `signals.py`, `resolve.py`, `dedupe.py`, `funnel.py`, `db.py`, `http.py`, `discover.py`, `cli.py` — a working implementation of every stage below |
| `legacy/companyOps/tam/categories/edtech.yaml` | The EdTech ICP config, **including the two fixes in §3** |
| `legacy/companyOps/tam/config.yaml` | Budgets, unit costs, `vendors_disabled` |
| `legacy/companyOps/tam/data/tam.sqlite3` | 41,363 companies / 16,905 classifications / 224,846 funnel events. Verified identical to the live companyOps DB as of 2026-10-03 22:53 |

⚠️ **That sqlite file is a point-in-time copy, not a live link** (different inode from the
companyOps one). Decide up front whether you work in it or in `db/leadgen.sqlite`, and say so
in your output. Do not write to both and expect them to agree.

---

## 2. Where these 594 came from, and what has already been checked

The 833-person campaign export covers 798 distinct Indian EdTech-adjacent companies. All 798
were cross-checked against the companyOps corpus and the live `companyops` HubSpot portal
(246897735, ~7,100 deals):

| | Companies |
|---|---:|
| Already in the companyOps TAM corpus | 152 |
| Already have a HubSpot deal (any owner) | 111 |
| Already pushed by the old pipeline | 11 |
| **Untouched — in neither. Your queue.** | **594** |

**68 of the 833 people are already HubSpot contacts** (matched on LinkedIn slug). Dedupe on
LinkedIn URL *as well as* domain — standing rule 7, never name alone.

### What the queue file's columns mean

| Column | Meaning |
|---|---|
| `company` | as it appeared in the export — **not** a resolved legal name, and **no domain** |
| `likely_academic_or_coaching` | **182 rows.** A keyword match on the name (university/college/institute/academy/coaching/foundation…). **A hint to check first, NOT a verdict.** Do not auto-reject on it |
| `has_decision_maker` | **343 rows.** At least one campaign contact passes the CEO/Founder/MD/COO/CTO gate (rule 4) |
| `poc_name` / `poc_title` / `poc_linkedin` | the best-ranked such contact, taken straight from the export |
| `all_titles_seen` | every title the export held for that company — good evidence of what kind of org it is |

Rows are sorted so **277 clean-and-have-a-decision-maker** sit at the top. Start there.

### These contacts cost nothing and are not callable

The POCs are **from the user's own export**, not from Apollo or SignalHire — zero credits were
spent producing this file. All 277 have a LinkedIn URL and **none has a phone number**. Under
rule 2 (+91 mobile AND LinkedIn, both hard gates) they cannot reach a rep as they are. That is
deliberate: qualify first, then spend on phones only for companies that passed.

---

## 3. The EdTech ICP — and the exact failure to avoid

We buy **company operations data**: SOPs, playbooks, process docs, decision logs, internal
tooling exhaust. The test, in order:

1. Does the core operation **generate data as a byproduct**?
2. Is it **digital, and at volume**?
3. Do they **own** it, or hold it under someone else's contract/MSA?
4. **Size band** (§4, stage 5).
5. **India.**

### What already went wrong — do not repeat it

On 2026-09-30 this pipeline pushed **131 coaching centres and training institutes** to a
caller as EdTech "Fit". The rep escalated; the deals had to be removed. Root cause: the
keyword list rewarded an **activity** ("placement assistance", "1:1 mentorship", "mentor
sessions") instead of an **artefact** — a product that actually emits operational data. A
coaching centre's marketing copy scores identically to a real EdTech product company's.

Two fixes are already in `edtech.yaml` and must stay in force:

- **`require_own_tech_product_for_fit: true`** — a company cannot be `Fit` without evidence
  it operates its **own software product**. `has_own_tech_product` was being captured and then
  never gated on. *That* was the bug, not the keywords.
- `placement assistance`, `1:1 mentorship`, `mentor sessions` demoted strong → medium.

So a university, college, school, coaching centre or training institute is **Out** — not
because of its name, but because it fails tests 1 and 3. It runs classes; its records are
student records.

### Hard data constraints — non-negotiable (DPDP Act 2023)

**Never collect learner records, parent contacts, or tutor/teacher personal data.** Minors'
data is involved. This is *why* academic institutions are off-ICP rather than merely
low-scoring. Company-level facts only: product, size, tooling, leadership.

### Calibration check

From the 798 sample, expect roughly **30% to resolve academic/coaching → Out**, plus a further
slice of agencies and services firms. **If your classifier returns 70%+ `Fit`, it is
miscalibrated** — re-read this section before trusting the output.

---

## 4. The five stages

### Stage 1 — Domain resolution (free, and everything depends on it)
The queue has **no domains**. Resolve via Google Places Text Search (`GOOGLE_MAPS_API_KEY`)
using `"{company} {city}"` — named-entity lookups run >70% precision vs 20–40% for generic
queries. India-billed free caps (≈35,000 Pro/month) cover this volume at **no cost**.
**Match on website domain, never on name alone.** No domain ⇒ bucket `domain_unresolved`
(**retryable**), never `Out`.

### Stage 2 — Website crawl (free)
- `robots.txt` honoured **per host** before any fetch (`urllib.robotparser`).
- UA `LH2-Research-Bot/1.0 (+bhanu.enamala@lh2.ai)`, **1 request/sec per host**.
- Fetch `/`, `/about`, `/product(s)`, `/platform`, `/solutions`, `/pricing`, `/careers`,
  `/customers`. Skip blog/news/privacy/terms/login.
- **Never scrape LinkedIn.** Sales-Nav data enters only as manual CSV. **No CAPTCHA solving.**
- Store page text with provenance (`source_url`, `fetched_at`, hash) — a verdict must be
  traceable to the text it was based on.
- Blocked or JS-only site ⇒ Apify actor, budget-capped. Thin crawl ⇒ `crawled_thin`
  (**retryable**), never a silent `Out`.

> **Schema trap that silently broke all crawling once:** `pages` must be
> `UNIQUE(company_id, url)` **and** the upsert must say `ON CONFLICT(company_id, url)`. A
> migration changed the constraint while the insert still said `ON CONFLICT(url)`, so pages
> from one company overwrote another's. Already fixed in `crawl.py` — don't regress it.

### Stage 3 — Headcount (gates the tiering)
Cheapest first:
1. **Own pages** — "we're a team of 120" (`headcount_from_pages`). Free, ~10–15% coverage.
2. **SignalHire profile-count triage** — `searchByQuery` costs **0 credits**; calibrated on 60
   companies: 1–39 profiles ⇒ below floor; 50–800 ⇒ in band; 0 / 40–49 / >800 ⇒ unresolved.
   Gives a **band, not a number**. There is a daily **search-attempt** quota (HTTP 402) that is
   separate from credits — latch it and stop; do not relabel the remainder "not found". That
   mistake once mislabelled 329 accounts.
3. **Apollo `organizations/bulk_enrich`** — 1 credit/org, the only exact source.
   **Ask Bhanu before any Apollo spend** (rule 6; Apollo off by default).

Unknown ⇒ `unknown_headcount` (**retryable**). Never guess a size, and never let unknown land
in a terminal bucket — 182 Fit accounts were once invisible to every retry because they sat in
`out_too_small` having never had a headcount read.

### Stage 4 — Classify (free)
In the companyOps pipeline this runs through **Claude Code subagents, not an API key**:
`classify-export` writes batch files → subagents write verdict files → `classify-import` loads
them. **No `ANTHROPIC_API_KEY` exists or is needed** (the one in the hubspot `.env` was
reported invalid/401 — see CONTEXT.md).

Verdict per company: `icp_bucket` (Fit | Maybe | Out), `confidence`, `company_type`
(product | services | agency | marketplace | enterprise | unclear), `has_own_tech_product`,
`india_hq`, `out_reason`, `evidence_quote` (≤200 chars, **verbatim** from the crawled text),
`evidence_url`.

Three guards, each of which failed in production:
- **A verdict row with a NULL `icp_bucket` must be recorded `classify_failed` (retryable),
  never a silent pass.** There are **1,917 such rows in fintech and 70 in this very edtech
  sample** — the classifier ran, recorded nothing, and they still look classified.
- **Foreign-ID guard:** a verdict is valid only for a company in its own batch file.
- **`require_own_tech_product_for_fit`:** demote services-type `Fit`s (§3).

### Stage 5 — Score, tier, rank
- **Tier A: 20–500 headcount. Tier B: 501–1,000. Excluded: >1,000 and <20.**
- 20–50 headcount ⇒ keep, but attach a **note stating the headcount and asking the caller to
  judge fit on the call** (rule 4, Cluster 1 floor 20 with a note). Dropping the floor from 50
  to 20 took Cluster 1 usable inventory from 14 → 98, so this band matters.
- **Rank, do not disqualify.** Every rejected account **stays in the DB** with verdict, score
  and reason (rule 8), and every outcome — failures included — gets a named funnel bucket.

---

## 5. Stop here

**This task ends at a ranked CSV.** Do **not** discover contacts, enrich phones, or push to
HubSpot. Nothing in this queue is qualified yet, and Bhanu reviews the verdicts first.

If a push is later authorised: EdTech is a **Cluster 1** segment (Amisha Pujari `168015679`,
Manit Rastogi `168609107`, R Kalyan `168828917`), account `companyops` portal `246897735`,
pipeline `default`, cold-call stage `4327110346`. One deal per company, **named after the
company**; the deal **must** have an associated contact or it is useless to the caller
(phone/LinkedIn live on the contact); +91 mobile and LinkedIn URL are hard gates; POC must be
CEO/Founder/MD/COO/CTO tier.

---

## 6. Output

`out/edtech_untouched_scored.csv`, one row per company:

```
company, resolved_domain, icp_bucket, confidence, company_type, has_own_tech_product,
headcount, headcount_source, tier (A|B|excluded|unknown), score, rank_in_tier,
out_reason, evidence_quote, evidence_url, funnel_bucket,
poc_name, poc_title, poc_linkedin, likely_academic_or_coaching
```

Plus a one-page summary: counts per `icp_bucket`, per tier, per funnel bucket (**including
every failure bucket**), and the academic/coaching share — so the §3 calibration check can
actually be made. State which database you wrote to.

---

## 7. Cost

Free: domain resolution (inside Places free caps), crawling, classification, SignalHire
profile-count triage.

Paid, and both need care:
- **Apify** for blocked sites — the companyOps Apify account is **STARTER, $100/month, with
  ~$47 left as of 3 Oct**. Check the balance before running.
- **Apollo `bulk_enrich`** for exact headcount — **only with Bhanu's explicit approval**.
  ~594 companies ⇒ ~594 credits.

Print a worst-case cost estimate before any run, and support `--dry-run`. Rule 6: ask before
*any* Apollo spend.
