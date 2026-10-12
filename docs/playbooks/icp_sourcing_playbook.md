# ICP sourcing playbook — repeatable, cluster-by-cluster

This is the standard end-to-end flow for sourcing a new sub-ICP via Apollo, starting with Cluster 2 /
Manufacturing / Discrete manufacturing (2026-10-10) and meant to repeat identically for every remaining
sub-ICP → vertical → cluster after it (Cluster 2's other 7 verticals, then Cluster 1, and so on).

Every step's input and output is tracked - nothing here is "run it and eyeball the printout."

## Step 1 — Pull the ICP definition from the sheet

Source of truth: the Google Sheet "Company Ops C2 : Vertical persona" (or the Cluster 1 equivalent, once we
get there). For each vertical/sub-ICP, record: industries, keywords, sub_icp names, title/seniority
preferences, headcount bands. Cluster 2's mapping is saved at
`cluster2_icp_apollo_mapping.json` (scratchpad) - promote this to a tracked repo file once Cluster 2 is fully
worked through, rather than leaving it in a session scratchpad.

## Step 2 — Agree titles/seniority with the user, per sub-ICP

Don't reuse the generic cluster-level template blindly - the actual decision-maker varies by sub-ICP (e.g.
discrete manufacturing: C-suite only, since ops/IT roles can't approve a data-sharing relationship). Lock:
- `person_titles[]`, in **explicit preference order** (used later for per-company dedup - see Step 5)
- `person_seniorities[]`
- `person_locations[]` / `organization_locations[]` (India, fixed, for every cluster)

**Standing rule (2026-10-11): tech-stack is not guaranteed to exist for every sub-ICP.** Confirmed on Cloud
Kitchens (Cluster 1, Food Tech): Apollo's entire "Point of Sale and Retail Management" category is almost
exclusively US/Western vendors - the real India market leaders (Petpooja, POSist/Restroworks, UrbanPiper,
LimeTray, Torqus, Gofrugal, DineDesk) are absent entirely. This differs from Manufacturing, where the relevant
software (SAP, Siemens, AVEVA) is genuinely global and Apollo tracks it regardless of the customer's country.
**When tech-stack research comes back empty or near-empty for a sub-ICP, don't force in a nominal/low-signal
match - fall back to keyword + industry + headcount + title only**, same as every sub-ICP that never had a
tech-stack leg in the first place. Check coverage per sub-ICP as each one comes up in rotation; don't assume
tech-stack will be available just because it worked for a previous sub-ICP in the same cluster.

**Standing rule (2026-10-12): a hospital lead needs 200+ headcount to qualify, in every vertical.** This is a
floor on top of whatever headcount band a vertical's own sub-ICP mapping otherwise allows - a vertical whose
general band floor is lower does not override it. A hospital under 200 headcount is an ICP rejection, logged
the same as any other ICP mismatch (Step 6/7), not pushed, not queued.

## Step 3 — Resolve `technology_uid` slugs, if the sub-ICP has a tech-stack signal

Apollo's `supported_technologies_csv` (`/v1/auth/supported_technologies_csv`, free) gives display names only -
no UID column. The UID slug convention (lowercase; spaces/periods → `_`; `/`,`(`,`)` stripped) is a guess that
needs live confirmation per name, not assumed:
1. Control test first: fire a totally bogus slug with zero other filters - must return `0` against the true
   unfiltered baseline (~248M). If it doesn't, the filter isn't being enforced server-side and nothing below
   is trustworthy.
2. For each real technology name, test 2-4 slug guesses with **zero other filters** (global, not scoped to
   India/band) - a slug that resolves to a non-zero global count is confirmed; one that returns 0 across every
   guess is unresolved (acceptable to drop if a broader confirmed UID already covers the same signal, e.g.
   `siemens_simatic_s7` standing in for an unresolved `Siemens SIMATIC S7-1500`).
3. Every call here is a 0-credit `/mixed_people/api_search` call - cheap to brute-force, still logged through
   `apollo_ledger.call()` like any other call.

## Step 4 — Build and run the real search, one call per headcount band

`POST /v1/mixed_people/api_search`, always through `leadgen/apollo_ledger.py`'s `call()` (never a raw
`requests` call - this is what gets every call logged with credits + timestamp automatically). One call per
`organization_num_employees_ranges` value - never bundle bands into one request, since the funnel-performance
comparison downstream is band-by-band.

## Step 5 — Dedup by company, by title-priority order

Apollo doesn't dedup within an org. After fetching a page, group by `organization.id` (or org name if id
missing) and keep only the single person matching the **highest-priority title** per the order locked in Step
2 (e.g. Founder > Co-Founder > CEO > CTO > COO) - mark that one row `is_priority_pick = 1` in
`ext_sourcing_result`, every other row from the same org stays `0` (kept for the record, not discarded).

**Two real bugs found on the first run (Cluster2/Manufacturing/Discrete, 201-500 band, 2026-10-10) - match
titles this way, not with a naive substring scan in priority order:**
1. `"founder"` is a substring of `"co-founder"` - checking titles in plain priority order (Founder before
   Co-Founder) misclassifies every Co-Founder as a Founder. Check Co-Founder's patterns *before* Founder's.
2. Spelled-out forms exist alongside abbreviations ("Chief Executive Officer", not just "CEO") - match both,
   or a real CEO with a spelled-out title loses to a lower-ranked person whose title happens to abbreviate.

Use a canonical→patterns list, checked specific-before-general:
`[("Co-Founder", ["co-founder","co founder","cofounder"]), ("Founder", ["founder"]), ("CEO", ["ceo","chief executive officer"]), ("CTO", ["cto","chief technology officer","chief technical officer"]), ("COO", ["coo","chief operating officer"])]`

## Step 6 — Store the run (input) and results (output)

Every sourcing call writes to two tables (migration `0024_icp_sourcing_runs.sql`):

- **`ext_sourcing_run`** — one row per API call: `apollo_call_id` (FK into `ext_apollo_call`, so credits and
  timestamp are never duplicated - just referenced), `cluster`, `vertical`, `sub_icp`, `headcount_band`,
  `endpoint`, the full `request_body_json` actually sent, `page`/`per_page`, `total_entries_reported` (Apollo's
  server-side match count), `results_returned` (count in this page).
- **`ext_sourcing_result`** — one row per person returned: `apollo_person_id`/`apollo_org_id`, `company`,
  `title`, `last_name_obfuscated`, `has_direct_phone`, `title_priority_rank`, `is_priority_pick`, and the full
  `raw_json` Apollo returned (so nothing is lost even if we didn't think to pull a column out at ingest time).

Query the deduped shortlist for any run via the view `v_sourcing_priority_picks`.

### Rotation rule — CORRECTED 2026-10-11, this supersedes any earlier "stay until exhausted" wording

The earlier version of this playbook said to stay on one sub-ICP, pushing a fresh 100 every day, until its
TAM across all 3 headcount bands was exhausted, only then advancing. **That is wrong and is no longer the
rule.** The real rule, as corrected by the user: within a segment, **cycle to a new sub-ICP every single
day** - today's 100 comes from sub-ICP A, tomorrow's 100 comes from sub-ICP B, and so on through every
sub-ICP in that segment, never staying on one past a single day regardless of how much of its TAM is left.
Only *after* cycling through every sub-ICP once does a performance analysis (by sub-ICP, by segment, by
headcount band) decide what to double down on going forward - exhaustion is not the trigger for advancing,
one calendar day is. `v_tam_coverage` still matters for that later analysis step; it no longer gates when to
move to the next sub-ICP.

### TAM coverage — tracked per exact filter, across every run that ever used it

We will not pull a sub-ICP/band's full match count in one sitting - sourcing happens in diversified bites
across days, spread over bands/sub-ICPs/verticals. So `ext_sourcing_run` carries a `filter_signature`
(sha256 of the request body with `page` removed, sorted keys) - the *same* filter combination, any page, any
day, always hashes identically, so every run against that exact slice rolls up together automatically.

The view **`v_tam_coverage`** answers, per filter signature: Apollo's latest reported `total_entries_reported`
(its TAM size for that filter), `pages_fetched`, `max_page_reached`, distinct `people_fetched_distinct`
(deduped by `apollo_person_id` across every run sharing the signature), `pct_covered`, and
`people_remaining_est`. Check this before starting a new session on a sub-ICP already touched before, to
pick up from the right page rather than re-fetching page 1 again.

This is **pre-reveal** data only - no real name, phone, or email. Apollo's people-search endpoint never returns
those; they only unlock per-person via `/people/match` (1 credit if found, +8 more only if a mobile is actually
revealed).

## Funnel leaks — tracked, not silently dropped

Every point a sourced candidate falls out before push gets logged in `ext_funnel_leak`
(migration `0026_funnel_leaks.sql`), with a `cost_impact` tag and a `detail` field explaining exactly what
happened and where - so these can be revisited later instead of disappearing:

| leak_type | When | cost_impact |
|---|---|---|
| `duplicate_person_same_company` | A second/third person matched at an org already covered by a higher-priority title (Step 5) | `rate_limit_waste` - search is free, but the row still ate a slot in that page's rate-limited budget |
| `low_phone_confidence` | `has_direct_phone` came back `"Maybe"`, not `"Yes"` - skipped before reveal | `rate_limit_waste` - same reasoning, no credit spent |
| `hubspot_dedup_block` | `leadgen.dedup.py` found a live hit in any of the 3 portals (Step "dedup against HubSpot", nuance TBD) | `none` - this one's about pipeline hygiene, not resource cost |

Query `v_funnel_leak_summary` for a rollup by cluster/vertical/sub_icp/headcount_band/leak_type.

## Step 7 — Reveal, on request only

When the user says which of the sourced/priority-picked people to actually reveal (never automatic - this is
where real credits get spent), call `/people/match` through the same `apollo_ledger.call()`. A successful
reveal with a real +91 mobile lands in `ext_apollo_candidate_pool` (migration `0023`), status `available`,
tagged with `icp_segment` = the sub_icp name, until the user names a rep/pipeline to assign it to.

## Step 8 — Assign and push

Only on explicit instruction ("add these to <rep>"): update `ext_apollo_candidate_pool` to `status='assigned'`
with `assigned_account`/`assigned_pipeline`/`assigned_owner_name`, then push to the correct HubSpot portal -
subject to every standing hard rule (mobile-mandatory, India-only, dedup-across-3-portals, non-destructive
reserve pattern).

### Every pushed deal gets a "why this lead" note attached (2026-10-10)

Reps were routinely skeptical of a pushed deal's quality, with no way to check why a lead had been sourced
without asking someone. Every deal push now attaches a HubSpot Note (the engagement object, visible at the
top of the deal's activity feed the moment a rep opens it) built entirely from data already captured earlier
in this pipeline - no new API calls, no extra credits:

**Note template** (populated from `ext_sourcing_run` + the reveal-time fields added to
`ext_apollo_candidate_pool` in migration `0027`):

```
WHY THIS LEAD WAS SOURCED

Target: <cluster> -> <vertical> -> <sub_icp>
Headcount band matched: <headcount_band>  (e.g. "201-500 employees")
Industry/keyword signal: <q_organization_keyword_tags, comma-joined>
Confirmed tech stack: <confirmed_technologies_json - the ACTUAL technology names this org's record shows,
  cross-referenced against our target tech list, not just "matched one of 33 possible">
Sourced: <discovered_at date>, sourcing run #<source_run_id>
```

(Trimmed 2026-10-10: dropped Contact/Phone-confidence/Apollo-match-confidence rows as not needed in the note
itself - `match_confidence` and `has_direct_phone` stay captured in `ext_apollo_candidate_pool` for our own
records, just not surfaced to the rep. Also dropped the "(from Apollo org enrichment)" aside on the tech-stack
line - the rep doesn't need to know the data's internal provenance, just the fact.)

**Why the tech-stack line pulls from the reveal response, not the search filter**: a
`currently_using_any_of_technology_uids[]` search only confirms a company matched AT LEAST ONE of the listed
technologies - the search response itself doesn't say which one. The `/people/match` reveal response's
`organization.current_technologies[]` field *does* name the organization's actual confirmed technologies, and
we've already paid for that reveal call - so capturing this field (now stored as
`confirmed_technologies_json`) turns "might be using one of 33 tools" into "confirmed using SAP S/4HANA" in
the note a rep actually reads. This field needs to be captured at reveal time (Step 7), not invented after
the fact.

**`apollo_filter_signature`** (the same hash already used for TAM-coverage tracking) is also written as a
deal property at push time - not for the rep to read, but so the dead-stage feedback-loop notes described in
the Cluster 2 SOP can trace a Wrong-Fit/Rejected-by-LH2 outcome straight back to the exact search that
produced it.

## What this gives us, cumulatively, across every future sub-ICP/vertical/cluster

- `ext_apollo_call` answers "how many credits has sourcing cost, in total and by segment/caller."
- `ext_sourcing_run` + `ext_sourcing_result` answer "exactly what filter combination did we run, when, and what
  came back" for every sub-ICP - the raw material for the cross-subsegment / cross-vertical / cross-headcount
  performance comparison the user asked for earlier this session, without needing to re-run anything to
  reconstruct it later.
- `ext_apollo_candidate_pool` answers "who's sitting revealed-but-unassigned right now, and for which sector."

Repeat Steps 1-6 for every remaining sub-ICP. Steps 7-8 happen only when the user asks for a specific batch to
be revealed/assigned - sourcing (free) and revealing (paid) are intentionally kept as separate, user-gated
steps throughout.
