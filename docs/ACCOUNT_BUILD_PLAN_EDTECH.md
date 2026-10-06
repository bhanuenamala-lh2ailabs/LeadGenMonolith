# EdTech account build: exhaustive list of ICP-matching Indian EdTech companies

Scope (user, 2026-10-06): build the ACCOUNT list first. Leads, dedup against HubSpot, phone/LinkedIn gates and headcount come later.
Headcount is deferred. ICP match is the gate now.

## Stages

| # | Stage | Source / method | Cost | Status |
|---|---|---|---|---|
| 1 | Apollo org-tag corpus | `legacy_tam_companies` edtech category, 5,644 companies | Already spent, historical | Done |
| 2 | Play Store developers | Apify `solidcode/google-play-apps-scraper`, 25 education queries, India | $0.19 | Done: 358 apps, 296 developers, 270 with a website, 228 new companies added |
| 3 | App Store sellers | iTunes Search API (free), country `in`, genre 6017, same keyword set | Free | To do |
| 4 | MCA company master | data.gov.in RoC master, NIC 85491/85492/85499 plus name keywords | Free, needs a data.gov.in API key | Blocked: key not in `.env` |
| 5 | Google Places discovery | Text queries × hub cities, IDs-only first, then details for new IDs | Metered (GOOGLE_MAPS key present, daily quota) | To do after quota resets |
| 6 | Tracxn manual exports | CSV from licence, if provided | Licence | Optional |
| 7 | Account dedup | Root domain, then normalised name + state. Account level only | Free | Runs after each stage |
| 8 | Crawl every account | Playwright, robots-aware, text stored in `ext_crawl_page` (migration 0017) | Free compute | Running: about 17 sites a minute, about 5.5 h for 5,660 |
| 9 | ICP AI judge | Full-text judge per company, verbatim quote required, verdict in `ext_icp_check` | Claude model tokens, about 2,200 per company | After crawl |
| 10 | ICP-matched account list | Fit and Maybe, with funnel counts and reasons | — | Output |
| 11 | Headcount | Deferred | — | Later |

## ICP gate (judge rubric)
- Core business is EdTech (learners served online or hybrid).
- Runs its own software product (not a services or agency firm).
- Operational artefacts visible: Slack, Jira, Confluence, Notion, GitHub, API docs, agile practice, product engineers hiring.
- India-headquartered or India-operating.
- Coaching and training businesses are NOT rejected. They are scored (user, 2026-10-06): a coaching or training institute with substantial data (Google Drive, LMS logs, recordings, Slack, batch records) can be a valuable account even with no product. Its verdict is Maybe with funnel bucket `coaching_training_scored`, and the score reflects data richness, not whether it's a product company. Example: a 500+ headcount coaching institute is worth pursuing for its Drive data.
- Headcount is scored later, not used to reject.
- No learner, parent or tutor personal data is stored. Company-level facts only.

## Judge rules
- Every verdict carries a verbatim quote from the stored text, with its URL.
- The judge reads stored text only. No re-crawl needed when the ICP changes.
- A company with no usable text is recorded as `unverified`, never as Out.

## Open decisions
1. data.gov.in API key for stage 4 (free to register).
2. Places budget for stage 5 (metered; confirm the cap).
3. Classifier model choice for stage 9.
