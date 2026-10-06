# Apollo credits, 28 Sep to 5 Oct 2026: where each credit went, what it bought

Built from this repo's files: `docs/audit/apollo/master_ledger.csv` (300 events from 28 Sep), and the push progress files in `data/audit/`. Credits are from the ledger; what landed in HubSpot is from the push progress files. Where a number is an estimate, it says so.

## 1. Headline
- **Credits spent from 28 Sep:** about **25,371** (ledger, 300 events). Bulk activity stopped around 1 Oct 10:30 IST. Nothing bulk since.
- **Apollo-derived accounts pushed to HubSpot:** **214 deals** from the three Apollo-fed pipelines (IT Services 204, COBOL 8, EdTech 2). Each deal has a contact.
- **Phone reveals:** about **10,485 credits** on 82 phone-related events (ledger). At 8 credits per mobile returned, that's roughly 1,300 mobile numbers, a rough upper bound, not a count.
- **Not attributable here:** fintech, UK PropTech, Australia, CAD-200, Healthcare/Medtech and the CompanyOps verticals each have spend in the ledger, but their push logs are not in this repo. Their HubSpot outcomes are not counted below.

## 2. Credits by purpose and repo (ledger, 28 Sep to 4 Oct)

| Repo | Purpose | Credits | Events |
|---|---|---:|---:|
| hubspot_scraping | IT Services push (`itsvc` reveal of POCs) | 5,706 | 5 |
| hubspot_scraping | COBOL India POC enrichment | 3,805 | 24 |
| companyOps | EdTech TAM (Cluster 1) | 3,586 | 62 |
| companyOps | Other / unclassified | 3,253 | 98 |
| hubspot_scraping | UK PropTech / UK candidates | 2,316 | 6 |
| companyOps | Fintech TAM (Cluster 2) | 2,041 | 31 |
| hubspot_scraping | Other / unclassified | 1,752 | 32 |
| RapidActionTeam | CAD-200 founder / phone enrichment | 1,751 | 32 |
| companyOps | COBOL India POC enrichment | 864 | 4 |
| companyOps | Waterfall cycle (unattributed) | 161 | 2 |
| hubspot_scraping | Waterfall cycle (unattributed) | 98 | 1 |
| RapidActionTeam | Other / unclassified | 38 | 3 |
| **Total** | | **25,371** | **300** |

Note: the `itsvc` line is 5,706 credits for 5 events, but the ledger's single largest event is a 3,018-credit run (`reveal_pocs.py --target 250`, 3-4 Oct). The 2,400-credit `itsvc-tam` run is also in this line.

## 3. Accounts and leads that reached HubSpot, by source

| Source | Enriched | Phone found (mobile, +91) | Pushed to HubSpot (deal + contact) | Held here, not pushed |
|---|---:|---:|---:|---:|
| IT Services push (`itsvc`) | 249 delivered per ledger | not separately recorded | **204** (progress file) | 45 difference, unexplained |
| COBOL India POC enrichment | 128 orgs | 13 leads | **8** | 5 (reason not recorded) |
| EdTech TAM (Cluster 1, Manit) | 11 orgs | 3 leads | **2** (FluxGen, ScaNxt) | 1 (MintCarb: contact is an Assistant Professor, held) |
| Fintech / UK / Australia / CAD-200 / Healthcare / CompanyOps verticals | — | — | not in this repo | — |
| **Apollo-derived total** | | | **214** | |

Outcome of the COBOL enrichment: 61 orgs not found in Apollo, 19 with no domain, 18 out of the 20–1000 headcount band, 12 with no CEO-tier contact, 4 with no mobile, 1 with unknown headcount.

## 4. Credit-to-lead map (cost per outcome)

| Source | Credits (ledger) | Outcome | Credits per HubSpot deal |
|---|---:|---|---:|
| IT Services push | 5,706 (plus part of the 3,018 run) | 204 deals | about 28 (using 5,706) |
| COBOL | 3,805 + 864 = 4,669 | 8 deals | about 584 |
| EdTech TAM | 3,586 | 2 deals | about 1,793 |

The EdTech and COBOL figures are the most expensive per deal. Most of their credits went to organisations that never produced a CEO-tier contact or a +91 mobile. The COBOL result is 13 leads from 128 organisations, roughly 10 per cent.

## 5. Open discrepancies
1. **IT Services: 249 delivered per the ledger, but 204 in the push progress file.** The 45-deal gap is unexplained. Either the progress file is incomplete or 45 contacts were revealed but not pushed.
2. **Phone reveal count is an estimate.** The ledger does not record mobiles found per call. The 8-credit figure is Apollo's rule for a returned mobile.
3. **Other verticals' push logs are not in this repo.** Fintech, UK PropTech, Australia, CAD-200, Healthcare/Medtech and the CompanyOps verticals need their own outcome files from the teammate's repos before a per-deal figure can be given.
4. **HubSpot deal counts since 28 Sep** (for example 1,007 in the RAT pipeline, 859 in CompanyOps Cluster 2) include deals from non-Apollo sources. They can't be attributed to Apollo credits.
5. **Still open from earlier:** Apollo's own per-day chart, the "out of credits" confirmation, and the 55,000 credits with no traced source.

## 6. Current status
- Apollo is paused (`APOLLO_PAUSED=1`), with the one exception today: the Nikita Mahamana lookup, which was unpaused and then re-paused.
- No bulk spend since 1 Oct.
