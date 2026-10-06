# Apollo credit audit: master reconciliation (21 Sep to 4 Oct 2026)

Built by `tools/apollo_master_merge.py` from the per-repo audit ledgers in this folder. One source per repo and period, so nothing is counted twice:
companyOps = `pre/companyops_ledger.csv` (to 28 Sep) + `companyops_ledger.csv` (from 29 Sep); hubspot repo = `pre/hubspot_ledger.csv` (to 28 Sep) + `agents/hubspot_0927_1004.csv` (from 29 Sep, revised); RapidActionTeam = its own rows only (RAT-purpose spend run from the hubspot repo is counted once, under hubspot). Output: `master_ledger.csv` (392 events), `master_summary.json`.
Docs verdicts come from the verbatim snapshot in `apollo_docs_snapshot/` and `agents/endpoint_billing_table_v2.md`.

## 1. Which day boundary does Apollo's chart use?
Each event was bucketed under every day boundary from UTC-12 to UTC+14 and compared with the chart (readings off the screenshot, +-0.3k). A boundary is rejected when traced spend exceeds Apollo's own bar for a day.

| Boundary | Days where traced > chart + 0.3k | Overshoot |
|---|---|---|
| US Pacific (UTC-7, also UTC-6.5 to -7.5) | none | 0 |
| UTC | 25 Sep | 782 |
| US Eastern (UTC-4) | 25 Sep | 782 |
| IST | several | 1,893 |

Pacific is the only family of boundaries with no contradiction. It also removes two earlier puzzles: Sunday 27 Sep is 86% traced (the Singapore run on the evening of 27 Sep IST plus RapidActionTeam's waves from the morning of 28 Sep IST fall in the same Pacific day), and 25 Sep no longer overshoots. This is an inference. The account's timezone setting in Apollo would confirm it.

## 2. Day by day (US Pacific days): Apollo chart vs traced
| Apollo day | Apollo total | Traced | companyOps | hubspot repo | RAT | Untraced | % traced |
|---|---:|---:|---:|---:|---:|---:|---:|
| 21 Sep | 12,500 | 2,432 | 2,432 | 0 | 0 | 10,068 | 19% |
| 22 Sep | 6,800 | 153 | 9 | 144 | 0 | 6,647 | 2% |
| 23 Sep | 5,600 | 2,330 | 683 | 1,647 | 0 | 3,270 | 42% |
| 24 Sep | 12,400 | 6,911 | 1,179 | 5,732 | 0 | 5,489 | 56% |
| 25 Sep | 3,000 | 1,547 | 51 | 1,496 | 0 | 1,453 | 52% |
| 26 Sep | 0 | 0 | 0 | 0 | 0 | 0 | |
| 27 Sep | 4,600 | 3,953 | 264 | 2,860 | 829 | 647 | 86% |
| 28 Sep | 9,300 | 6,378 | 4,332 | 1,087 | 959 | 2,922 | 69% |
| 29 Sep | 13,800 | 3,754 | 3,522 | 232 | 0 | 10,046 | 27% |
| 30 Sep | 14,800 | 4,201 | 1,769 | 2,432 | 0 | 10,599 | 28% |
| 1 Oct | 7,100 | 4,005 | 0 | 4,004 | 1 | 3,095 | 56% |
| 2 Oct | 0 | 0 | 0 | 0 | 0 | 0 | |
| 3 Oct | 6,200 | 5,805 | 18 | 5,787 | 0 | 395 | 94% |
| 4 Oct | ~0 | 0 | 0 | 0 | 0 | 0 | |
| **Total** | **96,100** (Apollo says 96,439) | **41,469** | 14,259 | 25,421 | 1,789 | **54,631** | **43%** |

Biggest untraced blocks: 30 Sep (10.6k), 21 Sep (10.1k), 29 Sep (10.0k), 22 Sep (6.6k), 24 Sep (5.5k), 23 Sep (3.3k), 1 Oct (3.1k), 28 Sep (2.9k). The 29 and 30 Sep gaps (about 20.6k) are the most striking, because those are the days with the best-audited tracking (the companyOps ledger is complete for its own code, and the hubspot repo was re-audited twice). The 22 Sep bar also carries the whole-cycle Waterfall (1,175) and Email (786) credits, which no script in the three repos can produce.

## 3. What the docs say about each class of call (verbatim sources in `apollo_docs_snapshot/`)
| Call | Docs verdict | Quote (abridged) | What the repos assumed |
|---|---|---|---|
| `people/match` (no reveal) | CONDITIONAL | "Credits are charged only if credit-consuming data is found: 1 credit for demographics or email..." and "If no credit-consuming data is found, the request consumes 0 credits." | companyOps ledger charged 1 per any 2xx (over-counts no-match) |
| phone reveal (`reveal_phone_number` + `webhook_url`) | CONDITIONAL | "...plus 8 credits if a mobile phone is returned." Billing moment not stated | measured: billed even with no number seen by the script; hubspot scripts add 8 per call regardless |
| repeat lookup of the same person | can be billed again | "you can potentially use more credits to access the same data you already accessed" | nothing cached; restarts, dry-runs and duplicate batches re-paid |
| `mixed_companies/search` (org search) | CONSUMES | "consumes 1 Apollo credit per page" | treated as free in 8+ places (agents, scripts printing "0 credits spent") |
| `organizations/enrich`, `bulk_enrich` | CONSUMES | "1 credit per organization" | RapidActionTeam called enrich "free"; unit (submitted vs found) unstated |
| `mixed_people/api_search` | FREE | "0 credits" | correctly treated as free |
| `contacts/search`, accounts, sequences, usage and profile endpoints | FREE | API pricing page: "All other Apollo API endpoints do not consume credits." | n/a |
| waterfall parameters | CONDITIONAL, typically 1-4 (email) / 8-25 (phone) | per vendor lookup | no repo sets them, yet the cycle shows 1,175 Waterfall credits |
| `reveal_personal_emails=true` | SILENT ("potentially consumes credits") | | sent by companyOps on every match, never costed |
| 429 / timeout retries, webhook retries | SILENT | | possible silent double billing |

## 4. Traced credits by class and by how well they were tracked (keyword classification, approximate)
| Class (docs verdict) | Traced credits | Ledgered at run time | Measured by delta/log/counter | Estimated |
|---|---:|---:|---:|---:|
| Phone reveal (+8, conditional) | 25,836 | 6,685 | 5,567 | 13,584 |
| Person match / unlock (conditional) | 4,849 | 3,267 | 1,173 | 409 |
| Org enrich / headcount (consumes) | 4,051 | 1,643 | 681 | 1,727 |
| Org search (consumes) | 3,715 | 198 | 460 | 3,057 |
| Org search + enrich pipelines (consume) | 3,018 | 120 | 519 | 2,379 |

By repo: companyOps 14,259 (8,266 ledgered, 2,049 measured, 3,944 estimated); hubspot 25,421 (3,647 ledgered, 5,410 measured, 16,364 estimated); RapidActionTeam 1,789 (941 measured, 848 estimated).
Caveats: classes come from keywords in each row's endpoint and purpose text, and pipeline rows that do both a match and a reveal land in the phone class. Only 5% of org-search credits are in any ledger (82% are estimates): this is the structural leak, since only one companyOps script ever recorded it. 51% of all traced credits (21,156 of 41,469) are estimates.

## 5. What this does and does not show
- About 41.5k of the 96.4k credits used this cycle trace to a repo, script and purpose. About 54.6k do not.
- Better tracking would not close the gap: the best-tracked days (29 and 30 Sep) are where the gap is largest. The estimates could also be wrong in either direction. If a failed phone reveal is not billed, traced spend falls and the gap widens.
- Evidence for people or tools working outside these repos: the 22 Sep Waterfall and Email credits, the burst-and-quiet pattern (3 credits in 11 h 43 min overnight on 27 to 28 Sep), the 21 Sep record creation with no session running, and the overnight 66-deal RapidActionTeam push on 29 Sep.
- Nothing here proves who. Apollo's own credit-usage history, filtered by team member and by surface, for each Pacific day above, is the missing evidence.

## 6. Open live tests (designs in `agents/endpoint_billing_table_v2.md`, none run)
Read `credit_usage_stats` (free) before and after one call: no-match `people/match`; phone reveal on a person with no mobile (`poll_only`); `reveal_personal_emails` true vs false; one `organizations/enrich`; `bulk_enrich` with real, fake and duplicate domains; an empty org-search page; a repeat lookup. About 45 credits in total. Requires your go-ahead because Apollo spend is gated.

## 7. Re-running
`python3 tools/apollo_master_merge.py` regenerates `master_ledger.csv` and the boundary test from the per-repo CSVs. Update the `CHART` dict in that file with exact figures once you have Apollo's own daily export.
