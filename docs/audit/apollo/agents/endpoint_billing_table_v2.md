# Apollo endpoint billing truth table v2 (docs-verbatim)

Built 2026-10-04/05 from raw page snapshots in `docs/audit/apollo/apollo_docs_snapshot/` (curl with browser UA; each file has a header with URL, HTTP status, fetch time and sha256; `_fetch_log.tsv` lists every request). Every quoted string below was machine-verified as an exact substring of the saved raw file (OpenAPI JSON strings are quoted decoded; HTML-page quotes are entity-decoded). No call was made to api.apollo.io or app.apollo.io, no key was used, no live test was run. Companion: `endpoint_billing_table_v2.csv`, `../apollo_docs_snapshot/_credit_sentences.md` (all 1,000+ credit sentences by page).

Source status: docs.apollo.io (all 109 sitemap pages, the OpenAPI spec, llms.txt), apollo.io/pricing, apollo.io/pricing/about-credits and an apollo.io insights article were fetched raw. **knowledge.apollo.io returned HTTP 403 (Cloudflare challenge) to curl and WebFetch for every article tried, including "What Are Credits?" (9527776320781, 3 URL variants), Review Credit Usage, Use Waterfall Enrichment, Use Apollo API and Access a Prospect's Phone Number. Those are UNVERIFIED; only search-snippet text exists (Appendix A of `_credit_sentences.md`, marked summariser-derived).**

## 1. Compact table

| ID | Endpoint | Parameter combination | Verdict | Unit | Credit type |
|---|---|---|---|---|---|
| R01 | `POST /people/match` | no reveal_*, no waterfall (id / email / name+domain / linkedin_url) | **CONDITIONAL** | per person, only if credit-consuming data is found (demographic credit not charged when match_confidence=none) | lead credit (docs wording: "1 credit for demographics or email") |
| R02 | `POST /people/match` | same person looked up again (already enriched / unlocked earlier by this team) | **CONDITIONAL** | per repeat call (docs: "can potentially use more credits") | lead credit (+ mobile if phone re-requested) |
| R03 | `POST /people/match` | reveal_phone_number=true + webhook_url (async phone delivery) | **CONDITIONAL** | per person: +8 on top of R01, only "if a mobile phone is returned"; billing moment (acceptance vs delivery) not stated | mobile credit (direct_dial_credit on split plans; the shared lead_credit pool on unified-credit plans, see P1) |
| R04 | `POST /people/match` | reveal_phone_number=true + poll_only=true (no webhook_url) | **SILENT** | n/a (no billing text specific to poll_only) | unknown (presumably as R03) |
| R05 | `POST /people/match` | reveal_personal_emails=true | **SILENT** | not stated ("potentially consumes credits") | unknown (lead/email credit presumably) |
| R06 | `POST /people/match` | run_waterfall_email=true (needs webhook_url) | **CONDITIONAL** | per record, per vendor lookup; vendor-dependent | other (waterfall data-source credits; shown as "Waterfall" bucket in UI) |
| R07 | `POST /people/match` | run_waterfall_phone=true (needs webhook_url) | **CONDITIONAL** | per record, per vendor lookup; vendor-dependent | other (waterfall) |
| R08 | `POST /people/match and /people/bulk_match` | any waterfall parameter true: synchronous response and callbacks | **CONDITIONAL** | credits reported only in the async webhook payload / poll result | other (waterfall) |
| R09 | `POST /people/bulk_match` | no reveal, no waterfall (up to 10 people per call) | **CONDITIONAL** | per person (same rule as R01); response carries credits_consumed (decimal) and unique_enriched_records | lead credit |
| R10 | `POST /people/bulk_match` | reveal_phone_number=true + webhook_url / poll_only | **CONDITIONAL** | per person +8 if mobile returned; whether the sync credits_consumed includes the async phone credits is not stated | mobile credit (see P1) |
| R11 | `GET /webhook_result/{request_id} (alias /webhook_result/show)` | poll for phone / waterfall result (up to 30 days after trigger) | **FREE** | 0 credits per call | none |
| R12 | `GET /organizations/enrich` | single organization by domain / linkedin / website / name | **CONSUMES** | per organization (found vs not-found not stated) | other (credit key not stated; assume lead pool) |
| R13 | `POST /organizations/bulk_enrich` | up to 10 domains / details[] | **CONSUMES** | per organization; whether per domain submitted, per organization found, or unique is not stated | other (credit key not stated) |
| R14 | `POST /mixed_companies/search` | any filters, page / per_page (<=100) | **CONSUMES** | per page (up to 100 results); empty-page and beyond-last-page billing not stated; docs generically say credits apply "when qualifying data is returned" | other (credit key not stated; measured: lead pool) |
| R15 | `POST /mixed_people/api_search` | any filters (no emails / phones returned) | **FREE** | 0 credits per call and per page | none |
| R16 | `POST /mixed_people/search and POST /organizations/search (legacy / deprecated paths)` | n/a | **SILENT** | n/a | unknown |
| R17 | `GET /organizations/{organization_id}/job_postings` | any | **CONSUMES** | per page (up to 10,000 results) | other (not stated) |
| R18 | `GET /organizations/{id} (Get complete organization info)` | any | **CONSUMES** | per company | other (not stated) |
| R19 | `GET /people/{id} (Get complete person info)` | any | **CONSUMES** | per person | other (not stated) |
| R20 | `POST /news_articles/search` | any | **CONSUMES** | per page (up to 25 results) | other (not stated) |
| R21 | `POST /contacts/search, POST /accounts/search` | saved records only | **FREE** | 0 credits per call | none |
| R22 | `POST /contacts, /contacts/bulk_create, PATCH /contacts/{id}, /contacts/bulk_update, accounts create/update/bulk` | create / update (Create a Contact is the documented way to stop paying twice) | **FREE** | 0 credits per call | none |
| R23 | `Sequences: POST /sequences, PUT /sequences/{id}, POST /emailer_campaigns/search, add_contact_ids, remove_or_stop_contact_ids, approve/abort/archive` | API calls | **FREE** | 0 credits per call | none |
| R24 | `GET /users/api_profile` | include_credit_usage=true/false | **FREE** | 0 credits per call | none |
| R25 | `POST /usage_stats/credit_usage_stats` | master key or scope credit_usage_stats_read | **FREE** | 0 credits per call | none |
| R26 | `POST /usage_stats/api_usage_stats` | n/a | **FREE** | 0 credits per call | none |
| R27 | `GET /auth/health` | n/a | **SILENT** | n/a (not in OpenAPI; only an example call) | none presumed |
| R28 | `any endpoint` | HTTP 429 rate-limited request, then client retry | **SILENT** | n/a | n/a |
| R29 | `webhook delivery of phone / waterfall results (Apollo -> your webhook_url)` | retries by Apollo | **SILENT** | n/a | n/a |
| R30 | `GET /conversations/{id}, POST /conversations/export` | conversations with AI insights | **CONDITIONAL** | per conversation, only if it has AI insights | other (AI / conversation credit; key not stated) |
| R31 | `POST /agents/task (Run an Assistant task)` | n/a | **CONDITIONAL** | variable, depends on actions the Assistant performs; polling 0 | other |
| R32 | `every other listed endpoint (deals, tasks, lists, notes, fields, email accounts, users search, analytics report, calls, outreach emails, email drafts / send, search conversations, etc.)` | n/a | **FREE** | 0 credits | none |

## 2. Per-row evidence

### R01 POST /people/match | no reveal_*, no waterfall (id / email / name+domain / linkedin_url)
- Verdict: **CONDITIONAL**; unit: per person, only if credit-consuming data is found (demographic credit not charged when match_confidence=none); credit type: lead credit (docs wording: "1 credit for demographics or email")
- Quote (`reference__people-enrichment.md`): "Credits are charged only if credit-consuming data is found: 1 credit for demographics or email, plus 8 credits if a mobile phone is returned."
  - URL: https://docs.apollo.io/reference/people-enrichment.md | fetched 2026-10-04T18:31:28Z | sha256 ce2bd1b49dc9f8758383ef39120a3de9ca3263042d3710f918c58b812d096ec9
- Quote (`reference__people-enrichment.md`): "If no credit-consuming data is found, the request consumes 0 credits."
  - URL: https://docs.apollo.io/reference/people-enrichment.md | fetched 2026-10-04T18:31:28Z | sha256 ce2bd1b49dc9f8758383ef39120a3de9ca3263042d3710f918c58b812d096ec9
- Quote (`reference__people-enrichment.md`): "Apollo doesn't charge a demographic credit when `match_confidence` is `none`."
  - URL: https://docs.apollo.io/reference/people-enrichment.md | fetched 2026-10-04T18:31:28Z | sha256 ce2bd1b49dc9f8758383ef39120a3de9ca3263042d3710f918c58b812d096ec9
- Quote (`reference__people-enrichment.md`): "Email and mobile phone credit usage isn't determined by `match_confidence`."
  - URL: https://docs.apollo.io/reference/people-enrichment.md | fetched 2026-10-04T18:31:28Z | sha256 ce2bd1b49dc9f8758383ef39120a3de9ca3263042d3710f918c58b812d096ec9
- Earlier-table claim: CONFIRMS earlier row 1 (1 credit if found, 0 if nothing found). REFINES: the demographic charge is keyed on match_confidence; email and mobile are not. Earlier quote of "Apollo doesn't charge a demographic credit..." is now verified verbatim.
- Your measured data: HS: each matched unlock-only call = 1 lead credit (probe pair 479 -> 469 = 1 + 9). RAT 28 Sep waves with 0 direct-dial still billed 1 each. A no-match call was NOT isolated; CO ledger books 1 per any HTTP<400 (possible over-count, never reconciled).

### R02 POST /people/match | same person looked up again (already enriched / unlocked earlier by this team)
- Verdict: **CONDITIONAL**; unit: per repeat call (docs: "can potentially use more credits"); credit type: lead credit (+ mobile if phone re-requested)
- Quote (`docs__convert-enriched-people-to-contacts.md`): "If you call Apollo API to enrich data for the same people in the future, you can potentially use more credits to access the same data you already accessed."
  - URL: https://docs.apollo.io/docs/convert-enriched-people-to-contacts.md | fetched 2026-10-04T18:31:19Z | sha256 a9455d086cf7afcd0ab6f9aae92d6c40c42027fef8c3fe0be78d06512cd0d0a2
- Quote (`docs__capabilities.md`): "If you call Apollo API to enrich data for the same people again in the future, you potentially consume more credits to access the same data you previously accessed."
  - URL: https://docs.apollo.io/docs/capabilities.md | fetched 2026-10-04T18:31:19Z | sha256 f93dbc7854a80d7bd30a7ba46ef1cc8f0bc1b53226472781c88991dfa50ec8db
- Quote (`docs__convert-enriched-people-to-contacts.md`): "This means you don't need to use credits on Apollo to reveal the information again."
  - URL: https://docs.apollo.io/docs/convert-enriched-people-to-contacts.md | fetched 2026-10-04T18:31:19Z | sha256 a9455d086cf7afcd0ab6f9aae92d6c40c42027fef8c3fe0be78d06512cd0d0a2
- Earlier-table claim: CONFIRMS earlier section 2.1 "Re-enrichment" and disagreement 7. REFINES: docs now say it twice (convert-enriched page and Capabilities) and the "no credits again" sentence applies to data you already hold in a Create-a-Contact response, not to re-calling people/match. Docs do NOT say "not charged for already enriched"; they say the opposite.
- Your measured data: Not isolated. Repos have no cache of paid lookups; restarts re-pay (HS batch3, batch6, CEO run #1 then #2, CO repair.py). Test T7.

### R03 POST /people/match | reveal_phone_number=true + webhook_url (async phone delivery)
- Verdict: **CONDITIONAL**; unit: per person: +8 on top of R01, only "if a mobile phone is returned"; billing moment (acceptance vs delivery) not stated; credit type: mobile credit (direct_dial_credit on split plans; the shared lead_credit pool on unified-credit plans, see P1)
- Quote (`reference__people-enrichment.md`): "Credits are charged only if credit-consuming data is found: 1 credit for demographics or email, plus 8 credits if a mobile phone is returned."
  - URL: https://docs.apollo.io/reference/people-enrichment.md | fetched 2026-10-04T18:31:28Z | sha256 ce2bd1b49dc9f8758383ef39120a3de9ca3263042d3710f918c58b812d096ec9
- Quote (`openapi__apollo-rest-api.json`): "Set to `true` if you want to enrich the person's data with all available phone numbers, including mobile phone numbers. This potentially consumes credits as part of your <a href="https://docs.apollo.io/docs/api-pricing" target="_blank">Apollo pricing plan</a>."
  - URL: None | fetched None | sha256 None
- Quote (`openapi__apollo-rest-api.json`): "It can take several minutes for the phone numbers to be delivered."
  - URL: None | fetched None | sha256 None
- Quote (`docs__retrieve-mobile-phone-numbers-for-contacts.md`): "Retrieving phone numbers may use credits based on the data returned, your Apollo plan, and your enrichment configuration."
  - URL: https://docs.apollo.io/docs/retrieve-mobile-phone-numbers-for-contacts.md | fetched 2026-10-04T18:31:20Z | sha256 166cb332f21b7279b17e909f8be470fc76178010c097622f8ec017a5ecb368e4
- Earlier-table claim: CONFIRMS earlier row 2 docs rule (+8 if a mobile is returned). CONTRADICTS (apparently) the earlier "measured: billed on acceptance": docs literally tie the 8 to a mobile being returned. REFINES the bucket question (earlier disagreement 3): docs now name the meters, see P1.
- Your measured data: HS 3 Oct 22:55: one reveal probe = 9 lead credits, 0 direct_dial (93,374 -> 93,383). RAT 28 Sep 12:52: 16 reveals = 128 dial credits but 2 phones. HS 29 Sep: reveals left result_pending were billed (479 -> 469). All suggest billing on acceptance or billing on non-mobile numbers ("all available phone numbers" are requested), not isolated.

### R04 POST /people/match | reveal_phone_number=true + poll_only=true (no webhook_url)
- Verdict: **SILENT**; unit: n/a (no billing text specific to poll_only); credit type: unknown (presumably as R03)
- Quote (`openapi__apollo-rest-api.json`): "Set to `true` to receive phone or waterfall enrichment results by polling instead of a webhook."
  - URL: None | fetched None | sha256 None
- Earlier-table claim: NEW: not in the earlier table (earlier table said webhook_url is required; docs now offer poll_only).
- Your measured data: Not used by repos (they pass a dummy webhook_url and poll /webhook_result).

### R05 POST /people/match | reveal_personal_emails=true
- Verdict: **SILENT**; unit: not stated ("potentially consumes credits"); credit type: unknown (lead/email credit presumably)
- Quote (`openapi__apollo-rest-api.json`): "Set to `true` if you want to enrich the person's data with personal emails. This potentially consumes credits as part of your <a href="https://docs.apollo.io/docs/api-pricing" target="_blank">Apollo pricing plan</a>."
  - URL: None | fetched None | sha256 None
- Earlier-table claim: CONFIRMS earlier row 3 (UNDOCUMENTED). Note: api-pricing says "1 credit for demographics or email" (wording leaves open whether one credit covers both, and whether personal email is covered).
- Your measured data: Never isolated. CO sends it on every match; HS on reveal calls. UI Email bucket was 786 pre-window.

### R06 POST /people/match | run_waterfall_email=true (needs webhook_url)
- Verdict: **CONDITIONAL**; unit: per record, per vendor lookup; vendor-dependent; credit type: other (waterfall data-source credits; shown as "Waterfall" bucket in UI)
- Quote (`docs__api-pricing.md`): "Email waterfall enrichment typically uses 1–4 credits, but some vendor configurations or successful higher-cost matches may result in 20+ credits."
  - URL: https://docs.apollo.io/docs/api-pricing.md | fetched 2026-10-04T18:31:19Z | sha256 f7b40354e05f9989aed09a7cfba2315b9de2dc3965ff5f51af9976564b268d0b
- Quote (`reference__people-enrichment.md`): "some vendors consume credits per lookup even when no data is found."
  - URL: https://docs.apollo.io/reference/people-enrichment.md | fetched 2026-10-04T18:31:28Z | sha256 ce2bd1b49dc9f8758383ef39120a3de9ca3263042d3710f918c58b812d096ec9
- Quote (`docs__enrich-phone-and-email-using-data-waterfall.md`): "Some data sources charge only when they return data. Other data sources may consume credits for a lookup even when they don't find an email address or phone number."
  - URL: https://docs.apollo.io/docs/enrich-phone-and-email-using-data-waterfall.md | fetched 2026-10-04T18:31:19Z | sha256 21d08d715021fb27a15bde03cdc85193d0598ff80433deab08fa36e2dc8eeb68
- Earlier-table claim: CONFIRMS earlier row 4 (vendor-dependent, may bill with no data). REFINES with typical ranges (1–4 email; 20+ worst case).
- Your measured data: Not used by repo code (grep none). UI showed a Waterfall bucket of 1,175 before the window (source not identified).

### R07 POST /people/match | run_waterfall_phone=true (needs webhook_url)
- Verdict: **CONDITIONAL**; unit: per record, per vendor lookup; vendor-dependent; credit type: other (waterfall)
- Quote (`docs__api-pricing.md`): "Phone waterfall enrichment typically uses 8–25 credits, but some configurations may result in 45+ credits."
  - URL: https://docs.apollo.io/docs/api-pricing.md | fetched 2026-10-04T18:31:19Z | sha256 f7b40354e05f9989aed09a7cfba2315b9de2dc3965ff5f51af9976564b268d0b
- Earlier-table claim: CONFIRMS earlier row 4; REFINES with 8–25 typical and 45+ worst case per person.
- Your measured data: Not used by repo code.

### R08 POST /people/match and /people/bulk_match | any waterfall parameter true: synchronous response and callbacks
- Verdict: **CONDITIONAL**; unit: credits reported only in the async webhook payload / poll result; credit type: other (waterfall)
- Quote (`openapi__apollo-rest-api.json`): "Waterfall credit usage is reported separately, since enrichment completes asynchronously."
  - URL: None | fetched None | sha256 None
- Quote (`openapi__apollo-rest-api.json`): "When this object is present, `unique_enriched_records`, `missing_records`, and `credits_consumed` are omitted from the response."
  - URL: None | fetched None | sha256 None
- Quote (`openapi__apollo-rest-api.json`): "A `200` response with a non-accepted status means no webhook will be delivered"
  - URL: None | fetched None | sha256 None
- Earlier-table claim: NEW (metering detail not in earlier table).
- Your measured data: n/a

### R09 POST /people/bulk_match | no reveal, no waterfall (up to 10 people per call)
- Verdict: **CONDITIONAL**; unit: per person (same rule as R01); response carries credits_consumed (decimal) and unique_enriched_records; credit type: lead credit
- Quote (`reference__bulk-people-enrichment.md`): "Credits are charged only if credit-consuming data is found: 1 credit for demographics or email, plus 8 credits if a mobile phone is returned."
  - URL: https://docs.apollo.io/reference/bulk-people-enrichment.md | fetched 2026-10-04T18:31:27Z | sha256 40133c4484bc4bf0d629114a94eb493f9f3400cf0f2572898beb6f3ac2354ffa
- Quote (`reference__bulk-people-enrichment.md`): "Apollo doesn't charge a demographic credit when it can't match a person."
  - URL: https://docs.apollo.io/reference/bulk-people-enrichment.md | fetched 2026-10-04T18:31:27Z | sha256 40133c4484bc4bf0d629114a94eb493f9f3400cf0f2572898beb6f3ac2354ffa
- Quote (`openapi__apollo-rest-api.json`): "A person who was matched but returned no credit-consuming data isn't included, so this can be lower than the number of entries in `matches` — or `0` while `matches` is non-empty. Duplicate entries in `details[]` that resolve to the same person are counted once."
  - URL: None | fetched None | sha256 None
- Quote (`openapi__apollo-rest-api.json`): "The total credits this request consumed."
  - URL: None | fetched None | sha256 None
- Earlier-table claim: CONFIRMS earlier row 5. REFINES: duplicates inside details[] are billed once; bulk response is a free per-call meter (credits_consumed). Single /people/match has no such field in its response.
- Your measured data: No run in the window (only HS godown/apollo_phone_reveal.py calls it).

### R10 POST /people/bulk_match | reveal_phone_number=true + webhook_url / poll_only
- Verdict: **CONDITIONAL**; unit: per person +8 if mobile returned; whether the sync credits_consumed includes the async phone credits is not stated; credit type: mobile credit (see P1)
- Quote (`openapi__apollo-rest-api.json`): "Set to `true` if you want to enrich the data of all matched people with all available phone numbers, including mobile phone numbers. This potentially consumes credits as part of your <a href="https://docs.apollo.io/docs/api-pricing" target="_blank">Apollo pricing plan</a>."
  - URL: None | fetched None | sha256 None
- Earlier-table claim: CONFIRMS earlier row 5/2 by extension; sync-vs-async accounting is NEW and SILENT.
- Your measured data: No data.

### R11 GET /webhook_result/{request_id} (alias /webhook_result/show) | poll for phone / waterfall result (up to 30 days after trigger)
- Verdict: **FREE**; unit: 0 credits per call; credit type: none
- Quote (`reference__poll-webhook-result.md`): "<td><code>0 credits</code><br><a href="https://docs.apollo.io/docs/api-pricing">Learn more about API pricing and credits</a>.</td>"
  - URL: https://docs.apollo.io/reference/poll-webhook-result.md | fetched 2026-10-04T18:31:28Z | sha256 aa682049fe4ed68a42e4ea327db0f098631d87ea071ba96d4e2386c50e56f8dc
- Quote (`reference__poll-webhook-result.md`): "This endpoint returns webhook results for your team from up to thirty days after the webhook was triggered."
  - URL: https://docs.apollo.io/reference/poll-webhook-result.md | fetched 2026-10-04T18:31:28Z | sha256 aa682049fe4ed68a42e4ea327db0f098631d87ea071ba96d4e2386c50e56f8dc
- Earlier-table claim: CONFIRMS earlier row 6.
- Your measured data: Consistent with 0 (HS reconciles within ~1 percent). Polling up to 8 times per reveal does not appear to bill.

### R12 GET /organizations/enrich | single organization by domain / linkedin / website / name
- Verdict: **CONSUMES**; unit: per organization (found vs not-found not stated); credit type: other (credit key not stated; assume lead pool)
- Quote (`reference__organization-enrichment.md`): "<td><code>1 credit per organization</code><br><a href="https://docs.apollo.io/docs/api-pricing">Learn more about API pricing and credits</a>.</td>"
  - URL: https://docs.apollo.io/reference/organization-enrichment.md | fetched 2026-10-04T18:31:27Z | sha256 f1c453991618ea75235f3a53a74d10f082ef0af7e5b49a25ea242704bbfc536c
- Quote (`docs__api-pricing.md`): "**1 credit** per organization."
  - URL: https://docs.apollo.io/docs/api-pricing.md | fetched 2026-10-04T18:31:19Z | sha256 f7b40354e05f9989aed09a7cfba2315b9de2dc3965ff5f51af9976564b268d0b
- Quote (`changelog__api-errors-now-include-structured-details.md`): "Organization Enrichment requests with a missing or unreadable company identifier; these now return `422` instead of `200` with a null organization"
  - URL: https://docs.apollo.io/changelog/api-errors-now-include-structured-details.md | fetched 2026-10-04T18:31:28Z | sha256 4cdf04e6bfa76d9bc856ffe8bf7b48ac30d73bced8448221758227f3b3b6750b
- Earlier-table claim: CONFIRMS earlier row 10 (BILLS). CONTRADICTS the RAT code comment "free lookup, 0-credit". Rate limit text changed: no hourly cap on paid plans (earlier table said 600 per hour).
- Your measured data: NOT measured against a balance. rat.md hypothesises it explains hundreds of unattributed credits on 28 Sep. CO ledger books 1.

### R13 POST /organizations/bulk_enrich | up to 10 domains / details[]
- Verdict: **CONSUMES**; unit: per organization; whether per domain submitted, per organization found, or unique is not stated; credit type: other (credit key not stated)
- Quote (`reference__bulk-organization-enrichment.md`): "<td><code>1 credit per organization</code><br><a href="https://docs.apollo.io/docs/api-pricing">Learn more about API pricing and credits</a>.</td>"
  - URL: https://docs.apollo.io/reference/bulk-organization-enrichment.md | fetched 2026-10-04T18:31:27Z | sha256 aa17912c0c455c10aad0aae8fd65a1bf08e577d5da4815d3ce5aa92042904f3d
- Earlier-table claim: CONFIRMS earlier row 9 (unit ambiguous remains). Docs silent on duplicate / not-found domains.
- Your measured data: NOT measured by Apollo; CO books per domain submitted, verify_proptech.py per organisation returned.

### R14 POST /mixed_companies/search | any filters, page / per_page (<=100)
- Verdict: **CONSUMES**; unit: per page (up to 100 results); empty-page and beyond-last-page billing not stated; docs generically say credits apply "when qualifying data is returned"; credit type: other (credit key not stated; measured: lead pool)
- Quote (`reference__organization-search.md`): "This endpoint consumes 1 Apollo credit per page, with up to 100 results per page."
  - URL: https://docs.apollo.io/reference/organization-search.md | fetched 2026-10-04T18:31:27Z | sha256 b624258d77028b37ad5c1bd184416d36e9dd220da170da93c52ca542ecb78e04
- Quote (`docs__api-pricing.md`): "Enrichment, organization search, news search, and AI insight endpoints may consume credits when qualifying data is returned."
  - URL: https://docs.apollo.io/docs/api-pricing.md | fetched 2026-10-04T18:31:19Z | sha256 f7b40354e05f9989aed09a7cfba2315b9de2dc3965ff5f51af9976564b268d0b
- Quote (`docs__api-pricing.md`): "If you paginate through results or run repeated searches, your total credit usage can increase."
  - URL: https://docs.apollo.io/docs/api-pricing.md | fetched 2026-10-04T18:31:19Z | sha256 f7b40354e05f9989aed09a7cfba2315b9de2dc3965ff5f51af9976564b268d0b
- Earlier-table claim: CONFIRMS earlier row 8 (1 per page, up to 100). REFINES: api-pricing wording "when qualifying data is returned" is consistent with measured 0 for empty pages but does not state it for this endpoint. Docs say "per page ... up to 100", consistent with per_page being irrelevant to price.
- Your measured data: 1 per call returning >=1 org, 0 if empty, per_page irrelevant (HS 3 Oct: ~3,200 billed calls, 10 org calls = 6 credits at 22:34). Repos widely mislabelled it free.

### R15 POST /mixed_people/api_search | any filters (no emails / phones returned)
- Verdict: **FREE**; unit: 0 credits per call and per page; credit type: none
- Quote (`reference__people-api-search.md`): "<td><code>0 credits</code><br><a href="https://docs.apollo.io/docs/api-pricing">Learn more about API pricing and credits</a>.</td>"
  - URL: https://docs.apollo.io/reference/people-api-search.md | fetched 2026-10-04T18:31:27Z | sha256 c07b841db261ab22592020ba8887c0885dda8462f07a347a5e726ed1c63826f7
- Quote (`reference__people-api-search.md`): "This endpoint doesn't return email addresses or phone numbers."
  - URL: https://docs.apollo.io/reference/people-api-search.md | fetched 2026-10-04T18:31:27Z | sha256 c07b841db261ab22592020ba8887c0885dda8462f07a347a5e726ed1c63826f7
- Quote (`docs__retrieve-linkedin-profiles-for-prospects.md`): "While people API search doesn't consume credits, bulk people enrichment may use credits based on the data returned and the enrichment options enabled."
  - URL: https://docs.apollo.io/docs/retrieve-linkedin-profiles-for-prospects.md | fetched 2026-10-04T18:31:19Z | sha256 83c9f630b7637486af66549f0b3b0e2284bd3809e1ac99d5a8ad125c39d86156
- Earlier-table claim: CONFIRMS earlier row 7.
- Your measured data: Measured 0 (HS 3 Oct, per_page 25 and 1).

### R16 POST /mixed_people/search and POST /organizations/search (legacy / deprecated paths) | n/a
- Verdict: **SILENT**; unit: n/a; credit type: unknown
- Quote (`docs__api-pricing.md`): "The endpoints listed in the table below consume credits. All other Apollo API endpoints do not consume credits."
  - URL: https://docs.apollo.io/docs/api-pricing.md | fetched 2026-10-04T18:31:19Z | sha256 f7b40354e05f9989aed09a7cfba2315b9de2dc3965ff5f51af9976564b268d0b
- Earlier-table claim: CONFIRMS earlier row 11 (UNDOCUMENTED). The paths appear nowhere in the 110 docs pages or in the OpenAPI spec (grep = 0), so they are neither listed as credit-consuming nor documented. The api-pricing sentence implies 0 for unlisted endpoints but cannot be relied on for undocumented aliases.
- Your measured data: Not measured.

### R17 GET /organizations/{organization_id}/job_postings | any
- Verdict: **CONSUMES**; unit: per page (up to 10,000 results); credit type: other (not stated)
- Quote (`reference__organization-jobs-postings.md`): "This endpoint consumes 1 Apollo credit per page, with up to 10,000 results per page."
  - URL: https://docs.apollo.io/reference/organization-jobs-postings.md | fetched 2026-10-04T18:31:27Z | sha256 ce02754dd5a2ecc97a2a89ec6d08b6c8c557b553a766caf290d01b41891e36d8
- Earlier-table claim: CONFIRMS earlier row 16; adds the 10,000 results-per-page detail.
- Your measured data: Not used.

### R18 GET /organizations/{id} (Get complete organization info) | any
- Verdict: **CONSUMES**; unit: per company; credit type: other (not stated)
- Quote (`reference__get-complete-organization-info.md`): "<td><code>1 credit per company</code><br><a href="https://docs.apollo.io/docs/api-pricing">Learn more about API pricing and credits</a>.</td>"
  - URL: https://docs.apollo.io/reference/get-complete-organization-info.md | fetched 2026-10-04T18:31:27Z | sha256 bd6bc60aaf6dfafe5ad8310fc76c1e66b8f39495247fd80f564ae59658392fd1
- Earlier-table claim: CONFIRMS earlier row 16.
- Your measured data: Not used.

### R19 GET /people/{id} (Get complete person info) | any
- Verdict: **CONSUMES**; unit: per person; credit type: other (not stated)
- Quote (`reference__get-complete-person-info.md`): "<td><code>1 credit per person</code><br><a href="https://docs.apollo.io/docs/api-pricing">Learn more about API pricing and credits</a>.</td>"
  - URL: https://docs.apollo.io/reference/get-complete-person-info.md | fetched 2026-10-04T18:31:27Z | sha256 605a7f8933a9e30866b0dd6fa04c683de519c184d84926a5949824575a076404
- Earlier-table claim: CONFIRMS earlier row 16.
- Your measured data: Not used.

### R20 POST /news_articles/search | any
- Verdict: **CONSUMES**; unit: per page (up to 25 results); credit type: other (not stated)
- Quote (`reference__news-articles-search.md`): "This endpoint consumes 1 Apollo credit per page, with up to 25 results per page."
  - URL: https://docs.apollo.io/reference/news-articles-search.md | fetched 2026-10-04T18:31:27Z | sha256 4a951538e61ea9925656c275a06b17aac6ea369469f88ed5b11b8f26edb96d6a
- Earlier-table claim: CONFIRMS earlier row 16.
- Your measured data: Not used.

### R21 POST /contacts/search, POST /accounts/search | saved records only
- Verdict: **FREE**; unit: 0 credits per call; credit type: none
- Quote (`reference__search-for-contacts.md`): "<td><code>0 credits</code><br><a href="https://docs.apollo.io/docs/api-pricing">Learn more about API pricing and credits</a>.</td>"
  - URL: https://docs.apollo.io/reference/search-for-contacts.md | fetched 2026-10-04T18:31:25Z | sha256 da8fcab99ced880238afb75d1d5a831832ec76f2b99b568d639193d3f99ef879
- Quote (`reference__search-for-accounts.md`): "<td><code>0 credits</code><br><a href="https://docs.apollo.io/docs/api-pricing">Learn more about API pricing and credits</a>.</td>"
  - URL: https://docs.apollo.io/reference/search-for-accounts.md | fetched 2026-10-04T18:31:24Z | sha256 1566742711a844588c6b773ec07a6fdd1a7d48a992b02b9cbd52caf848cf81db
- Earlier-table claim: CONFIRMS earlier row 17.
- Your measured data: Not used.

### R22 POST /contacts, /contacts/bulk_create, PATCH /contacts/{id}, /contacts/bulk_update, accounts create/update/bulk | create / update (Create a Contact is the documented way to stop paying twice)
- Verdict: **FREE**; unit: 0 credits per call; credit type: none
- Quote (`reference__create-a-contact.md`): "<td><code>0 credits</code><br><a href="https://docs.apollo.io/docs/api-pricing">Learn more about API pricing and credits</a>.</td>"
  - URL: https://docs.apollo.io/reference/create-a-contact.md | fetched 2026-10-04T18:31:25Z | sha256 9bcdcbe04843d0b5e892cfa5c7ebb349b94aa4d85cb66abebed3810cd652dd8d
- Quote (`docs__api-pricing.md`): "Endpoints that create, update, list, or manage records consume `0 credits`."
  - URL: https://docs.apollo.io/docs/api-pricing.md | fetched 2026-10-04T18:31:19Z | sha256 f7b40354e05f9989aed09a7cfba2315b9de2dc3965ff5f51af9976564b268d0b
- Earlier-table claim: NEW as explicit row (earlier table only listed "contacts create" as not called). Docs tell you to use it to avoid re-paying (see R02).
- Your measured data: Not called by any repo; no repo converts enriched people to contacts.

### R23 Sequences: POST /sequences, PUT /sequences/{id}, POST /emailer_campaigns/search, add_contact_ids, remove_or_stop_contact_ids, approve/abort/archive | API calls
- Verdict: **FREE**; unit: 0 credits per call; credit type: none
- Quote (`reference__add-contacts-to-sequence.md`): "<td><code>0 credits</code><br><a href="https://docs.apollo.io/docs/api-pricing">Learn more about API pricing and credits</a>.</td>"
  - URL: https://docs.apollo.io/reference/add-contacts-to-sequence.md | fetched 2026-10-04T18:31:22Z | sha256 7b3bfedb781f6a0a40cd9391dfcf81dc12599c81b4b5646831525066dc7637d4
- Quote (`reference__create-sequence.md`): "<td><code>0 credits</code><br><a href="https://docs.apollo.io/docs/api-pricing">Learn more about API pricing and credits</a>.</td>"
  - URL: https://docs.apollo.io/reference/create-sequence.md | fetched 2026-10-04T18:31:22Z | sha256 f6013edd26389aa6e1347e38c1a6eb62e3769760d53877c2e0ca9b5af57070e5
- Earlier-table claim: NEW as explicit row; earlier table listed sequences as not called.
- Your measured data: Not used.

### R24 GET /users/api_profile | include_credit_usage=true/false
- Verdict: **FREE**; unit: 0 credits per call; credit type: none
- Quote (`reference__get-current-user-profile.md`): "<td><code>0 credits</code><br><a href="https://docs.apollo.io/docs/api-pricing">Learn more about API pricing and credits</a>.</td>"
  - URL: https://docs.apollo.io/reference/get-current-user-profile.md | fetched 2026-10-04T18:31:24Z | sha256 0f82e6f8460e5880ebee9076d02e00b36e3f2965b639abc8af98d33824af6754
- Quote (`openapi__apollo-rest-api.json`): "Lead credits used by the user. Only present when `include_credit_usage=true`."
  - URL: None | fetched None | sha256 None
- Earlier-table claim: CONFIRMS earlier row 12. REFINES: counters are per USER (key owner), not team (see P2).
- Your measured data: Free; dial counters stale since 3 Oct (consistent with P1 unified pool).

### R25 POST /usage_stats/credit_usage_stats | master key or scope credit_usage_stats_read
- Verdict: **FREE**; unit: 0 credits per call; credit type: none
- Quote (`reference__view-credit-usage-stats.md`): "<td><code>0 credits</code><br><a href="https://docs.apollo.io/docs/api-pricing">Learn more about API pricing and credits</a>.</td>"
  - URL: https://docs.apollo.io/reference/view-credit-usage-stats.md | fetched 2026-10-04T18:31:28Z | sha256 7779bf7c21b7305c7f9860cad615b35df64eb08229d95d03d5f4e11ed7aada11
- Quote (`reference__view-credit-usage-stats.md`): "Balances reflect your team's running credit usage and update within seconds of a credit-consuming request."
  - URL: https://docs.apollo.io/reference/view-credit-usage-stats.md | fetched 2026-10-04T18:31:28Z | sha256 7779bf7c21b7305c7f9860cad615b35df64eb08229d95d03d5f4e11ed7aada11
- Earlier-table claim: CONFIRMS earlier row 13. REFINES: team-level, near-real-time per docs.
- Your measured data: Worked 3 Oct; the reliable meter.

### R26 POST /usage_stats/api_usage_stats | n/a
- Verdict: **FREE**; unit: 0 credits per call; credit type: none
- Quote (`reference__view-api-usage-stats.md`): "<td><code>0 credits</code><br><a href="https://docs.apollo.io/docs/api-pricing">Learn more about API pricing and credits</a>.</td>"
  - URL: https://docs.apollo.io/reference/view-api-usage-stats.md | fetched 2026-10-04T18:31:28Z | sha256 338eb7dc25d6581b8c0c2b8ab73f41d4d755f7d5add1d66af1b9e51b8c808b2e
- Earlier-table claim: CONFIRMS earlier row 14.
- Your measured data: 404 on this key per audits (access, not billing).

### R27 GET /auth/health | n/a
- Verdict: **SILENT**; unit: n/a (not in OpenAPI; only an example call); credit type: none presumed
- Quote (`reference__apollo-api.md`): "Call `auth/health` to [test your API key](https://docs.apollo.io/docs/test-api-key) and confirm you're ready."
  - URL: https://docs.apollo.io/reference/apollo-api.md | fetched 2026-10-04T18:31:20Z | sha256 98644dbff41cc669d51953ad0bd8b0a644bffa0376cf224532308a1a8eef3eb9
- Quote (`reference__openapi-specification.md`): "It consumes no credits, and a `200` response with your user profile confirms your key works."
  - URL: https://docs.apollo.io/reference/openapi-specification.md | fetched 2026-10-04T18:31:20Z | sha256 6fb4121ea8b8da57fc34d6dfe6608c5aefed4f86de305869860e32e3fe7cf485
- Earlier-table claim: CONFIRMS earlier row 15 (UNDOCUMENTED, presumed FREE). The only "consumes no credits" sentence is about GET /users/api_profile in the Postman walk-through, not auth/health. Inference only: "All other Apollo API endpoints do not consume credits."
- Your measured data: Reports healthy with zero balance (so it does not check credits).

### R28 any endpoint | HTTP 429 rate-limited request, then client retry
- Verdict: **SILENT**; unit: n/a; credit type: n/a
- Quote (`reference__rate-limits.md`): "Enrichment endpoints consume credits. Their higher per-minute limit isn't a license to send unlimited requests: your [credit balance](https://docs.apollo.io/docs/api-pricing) still governs how much data you can enrich."
  - URL: https://docs.apollo.io/reference/rate-limits.md | fetched 2026-10-04T18:31:20Z | sha256 c92ff22776b178ae1589a2899a21399025aa0884f06109efe8673807bc406468
- Quote (`reference__rate-limits.md`): "Read the `retry-after` header to find out exactly how long to wait, then retry."
  - URL: https://docs.apollo.io/reference/rate-limits.md | fetched 2026-10-04T18:31:20Z | sha256 c92ff22776b178ae1589a2899a21399025aa0884f06109efe8673807bc406468
- Earlier-table claim: NEW. Docs never say a 429-rejected call is free; they say it is blocked "until that window resets". Timeouts after Apollo accepted a request are not discussed (earlier table section 5 item 9).
- Your measured data: RAT / itsvc / godown retry on exceptions and 429; double-billing on timeout unproven.

### R29 webhook delivery of phone / waterfall results (Apollo -> your webhook_url) | retries by Apollo
- Verdict: **SILENT**; unit: n/a; credit type: n/a
- Quote (`reference__people-enrichment.md`): "**Idempotency:** Apollo may retry webhook calls; your endpoint should be idempotent to handle duplicate payloads safely."
  - URL: https://docs.apollo.io/reference/people-enrichment.md | fetched 2026-10-04T18:31:28Z | sha256 ce2bd1b49dc9f8758383ef39120a3de9ca3263042d3710f918c58b812d096ec9
- Quote (`docs__retrieve-mobile-phone-numbers-for-contacts.md`): "The `credits_consumed` value in this example is illustrative. Don't use it to estimate the cost of every phone enrichment request."
  - URL: https://docs.apollo.io/docs/retrieve-mobile-phone-numbers-for-contacts.md | fetched 2026-10-04T18:31:20Z | sha256 166cb332f21b7279b17e909f8be470fc76178010c097622f8ec017a5ecb368e4
- Earlier-table claim: NEW. Webhook payload carries credits_consumed, which is a free per-request meter if your receiver logs it. Docs examples disagree (8 in one page, 1 in poll-webhook-result phone example, 0 in the waterfall example), all flagged illustrative.
- Your measured data: Scripts never received webhooks (dummy URL), so callbacks were never read.

### R30 GET /conversations/{id}, POST /conversations/export | conversations with AI insights
- Verdict: **CONDITIONAL**; unit: per conversation, only if it has AI insights; credit type: other (AI / conversation credit; key not stated)
- Quote (`reference__get-conversations-info.md`): "This endpoint consumes 1 Apollo credit per conversation if the conversation has AI insights. Conversations without AI insights consume 0 credits."
  - URL: https://docs.apollo.io/reference/get-conversations-info.md | fetched 2026-10-04T18:31:21Z | sha256 a5431cb2135123e1eec23067e53c63d4307fe68de40be4491a890bdbb332f6aa
- Earlier-table claim: NEW (not in earlier table).
- Your measured data: Not used.

### R31 POST /agents/task (Run an Assistant task) | n/a
- Verdict: **CONDITIONAL**; unit: variable, depends on actions the Assistant performs; polling 0; credit type: other
- Quote (`reference__run-an-assistant-task.md`): "Submitting a message does not itself charge credits. Actions such as enrichment can consume credits according to your plan, the action, and the data returned. There is no fixed per-task price."
  - URL: https://docs.apollo.io/reference/run-an-assistant-task.md | fetched 2026-10-04T18:31:20Z | sha256 cdcd42d57ed75f9f35d2fb7096c050afa26fd2d35b8c5f51ab67658a43184426
- Earlier-table claim: NEW.
- Your measured data: Not used.

### R32 every other listed endpoint (deals, tasks, lists, notes, fields, email accounts, users search, analytics report, calls, outreach emails, email drafts / send, search conversations, etc.) | n/a
- Verdict: **FREE**; unit: 0 credits; credit type: none
- Quote (`docs__api-pricing.md`): "The endpoints listed in the table below consume credits. All other Apollo API endpoints do not consume credits."
  - URL: https://docs.apollo.io/docs/api-pricing.md | fetched 2026-10-04T18:31:19Z | sha256 f7b40354e05f9989aed09a7cfba2315b9de2dc3965ff5f51af9976564b268d0b
- Earlier-table claim: NEW (earlier table named only a subset).
- Your measured data: Not used (emailer_campaigns, labels, opportunities not called).

## 3. Cross-cutting docs statements (credit pool, repeats, failures, webhooks, refunds)

### P1 Credit meters and the unified pool (relevant to your usage chart)
- Quote (`reference__view-credit-usage-stats.md`): "| `lead_credit`                    | Email address reveals and email enrichment.                  |"
  - URL: https://docs.apollo.io/reference/view-credit-usage-stats.md | fetched 2026-10-04T18:31:28Z
- Quote (`reference__view-credit-usage-stats.md`): "| `direct_dial_credit`             | Mobile and direct dial phone number reveals.                 |"
  - URL: https://docs.apollo.io/reference/view-credit-usage-stats.md | fetched 2026-10-04T18:31:28Z
- Quote (`reference__view-credit-usage-stats.md`): "| `export_credit`                  | CSV exports of contacts and accounts.                        |"
  - URL: https://docs.apollo.io/reference/view-credit-usage-stats.md | fetched 2026-10-04T18:31:28Z
- Quote (`reference__view-credit-usage-stats.md`): "If your team is on a unified credit plan, `lead_credit` is a shared pool: the `left_over` value already accounts for mobile reveals, exports, dialer minutes, and power-up enrichment, so don't add the individual balances together to estimate what's left."
  - URL: https://docs.apollo.io/reference/view-credit-usage-stats.md | fetched 2026-10-04T18:31:28Z
- Quote (`apollo_io__pricing.html`): ""creditTypes":{"lead_credit":"Email credits","direct_dial_credit":"Mobile credits","export_credit":"Export credits"}"
  - URL: https://www.apollo.io/pricing | fetched 2026-10-04T18:32:16Z
- Reading: Docs map API meters to UI labels: lead_credit = "Email credits", direct_dial_credit = "Mobile credits", export_credit = "Export credits". On a unified plan the single lead_credit pool already includes mobile reveals and exports, which explains the 3 Oct observation (a reveal moved 9 lead credits, 0 direct_dial; the direct_dial pool 104,000/104,000 stale). It does not by itself explain why direct_dial_used still rose in multiples of 8 on 28-29 Sep (open; test T-P1).

### P2 API and web UI: same pool? same counters?
- Quote (`docs__api-pricing.md`): "To view your credit usage, go to **Settings** > **Billing and credits** > <a href="https://app.apollo.io/#/settings/credits/current" target="_blank">**Credit usage**</a>. You can check usage by team or individual."
  - URL: https://docs.apollo.io/docs/api-pricing.md | fetched 2026-10-04T18:31:19Z
- Quote (`apollo_io__pricing.html`): "Credits are pooled across your entire team and granted upfront at the start of each billing cycle, so your full allotment is available from day one."
  - URL: https://www.apollo.io/pricing | fetched 2026-10-04T18:32:16Z
- Quote (`apollo_io__insights_credits_left.html`): "You check remaining Apollo credits in Settings > Credits and activity > Credit usage, which shows your available balance plus a filterable breakdown by date, feature, team member, and surface."
  - URL: https://www.apollo.io/insights/how-many-apollo-credits-do-i-have-left-before-i-run-a-large-enrichment-batch | fetched 2026-10-04T18:32:25Z
- Quote (`reference__view-credit-usage-stats.md`): "To retrieve credit usage for a single user instead of the whole team, use <a href="https://docs.apollo.io/reference/get-current-user-profile">get current user profile</a> with the `include_credit_usage` query parameter set to `true`."
  - URL: https://docs.apollo.io/reference/view-credit-usage-stats.md | fetched 2026-10-04T18:31:28Z
- Quote (`openapi__apollo-rest-api.json`): "Export credits used by the user. Only present when `include_credit_usage=true`."
  - URL: None | fetched None
- Reading: No docs sentence says in so many words "API and UI share one pool", but every source treats credits as a team-wide pool, API enrichment is reviewed on the same Credit usage page (with a "surface" filter), and credit_usage_stats is the team total. api_profile counters are per USER (the key owner), so its export_used = 2 versus the UI team "Exports 16,365" (rat.md) is a per-user vs team difference, not proof that API counters ignore UI exports. Whether API people/match lands in the UI "Exports" bucket is SILENT in first-party docs (KB snippet, unverified: Person API enrichment synced outside Apollo consumes export credits).

### P3 Failed / no-match lookups
- Quote (`reference__people-enrichment.md`): "If no credit-consuming data is found, the request consumes 0 credits."
  - URL: https://docs.apollo.io/reference/people-enrichment.md | fetched 2026-10-04T18:31:28Z
- Quote (`reference__bulk-people-enrichment.md`): "Apollo doesn't charge a demographic credit when it can't match a person."
  - URL: https://docs.apollo.io/reference/bulk-people-enrichment.md | fetched 2026-10-04T18:31:27Z
- Quote (`docs__enrich-phone-and-email-using-data-waterfall.md`): "Other data sources may consume credits for a lookup even when they don't find an email address or phone number."
  - URL: https://docs.apollo.io/docs/enrich-phone-and-email-using-data-waterfall.md | fetched 2026-10-04T18:31:19Z
- Reading: Docs: person no-match is free (0 credits); waterfall can bill without data. Phone reveal that returns nothing: docs say +8 only if a mobile is returned, but measured data show billing without a script-visible phone.

### P4 Webhook-delivered phone numbers: billed on acceptance or delivery?
- Quote (`openapi__apollo-rest-api.json`): "It can take several minutes for the phone numbers to be delivered."
  - URL: None | fetched None
- Quote (`docs__retrieve-mobile-phone-numbers-for-contacts.md`): "The synchronous response may include the person's employer phone number, but it doesn't include the asynchronously retrieved mobile or direct-dial numbers."
  - URL: https://docs.apollo.io/docs/retrieve-mobile-phone-numbers-for-contacts.md | fetched 2026-10-04T18:31:20Z
- Reading: SILENT. No sentence states when the 8 credits are debited. Docs imply the charge depends on the data found ("if a mobile phone is returned") and the webhook payload reports credits_consumed at completion. Measured data (3 Oct, 29 Sep) show a debit by the time the sync response returns. Needs a live test.

### P5 Rate-limit retries and 429
- Quote (`reference__rate-limits.md`): "When you exceed an endpoint's per-minute, per-hour, or per-day limit, Apollo returns a [`429` status code](https://docs.apollo.io/reference/status-codes)."
  - URL: https://docs.apollo.io/reference/rate-limits.md | fetched 2026-10-04T18:31:20Z
- Reading: SILENT on billing of 429-rejected or timed-out requests.

### P6 Refunds
- Quote (`apollo_io__pricing.html`): "Downgrades (plan or seat count) also take effect immediately — your access updates right away, but refunds aren't issued for unused time in your current cycle."
  - URL: https://www.apollo.io/pricing | fetched 2026-10-04T18:32:16Z
- Reading: Only a subscription-refund statement exists in first-party pages (not about credits consumed by API calls). No credit refund for failed API calls is documented. KB article (blocked) not read.

### P7 Interpreting the usage chart ("exports" vs "phone numbers")
- Docs labels (apollo.io/pricing JSON): lead_credit = "Email credits", direct_dial_credit = "Mobile credits", export_credit = "Export credits". Docs describe export_credit as "CSV exports of contacts and accounts".
- First-party docs are SILENT on which UI bucket an API `people/match` unlock lands in. The unverified KB snippet says Person API enrichment synced outside Apollo consumes export credits, which would make API unlocks show under "Exports". That fits your audits (UI Exports 16,365 with only ~1,433 attributable to script exports) but is not proven.
- On a unified-credit plan every bucket drains the single `lead_credit` pool (P1), so the pool total (credit_usage_stats lead_credit consumed) is reliable while the split into Exports / Phone / Email is a labelling of what each action consumed, not separate wallets. Use team-level `credit_usage_stats` deltas, not per-user `api_profile`, to reconcile the chart.

## 4. Where v2 differs from the earlier table

- **D1 Verification status.** Earlier table quotes came from a summarising fetcher. v2 verifies every quote as a byte-exact substring of the raw saved response (people-enrichment, bulk, org search, api-pricing, etc.). Result: all earlier credit quotes I re-checked exist verbatim, except the earlier "Credit usage: 0 credits" form for api_search (now a table cell `<code>0 credits</code>`) and the unquoted "You can enrich up to 10 people per request" (present in the page JSON; the visible prose says "enrich data for up to ten people with a single API call").
- **D2 Reveal meter (earlier disagreement 3: "docs do not say which meter takes the 8").** Docs DO say now: credit_usage_stats lists lead_credit = "Email address reveals and email enrichment", direct_dial_credit = "Mobile and direct dial phone number reveals", and on unified plans lead_credit is a shared pool "[that] already accounts for mobile reveals, exports, dialer minutes, and power-up enrichment". This explains the 3 Oct move of 9 lead credits and 0 direct_dial (stale dial pool). It leaves open the 28-29 Sep direct_dial increments in multiples of 8.
- **D3 Earlier "failed/empty reveals billed" vs docs.** Unchanged contradiction: docs say the +8 applies "if a mobile phone is returned". New caveat from docs: the request asks for "all available phone numbers, including mobile phone numbers"; the earlier hypothesis that a non-mobile (direct / office) number may count remains unverifiable because the KB article is blocked. The KB snippet (summariser-derived, unverified) says credits are used when Apollo "finds and verifies at least one of these phone number types".
- **D4 webhook_url required.** Now also: `poll_only=true` (omit webhook_url) is documented; combining the two returns 400 WEBHOOK_URL_WITH_POLL_ONLY. Webhook payload and poll result carry `credits_consumed`; webhook results are retained 30 days.
- **D5 Bulk metering.** bulk_match sync response returns `credits_consumed` (decimal, can be fractional) and `unique_enriched_records` (billing count; duplicates in details[] counted once). Single /people/match returns neither. Not in the earlier table.
- **D6 Coverage of the credit table.** api-pricing page (updated 2026-09-28) says "The endpoints listed in the table below consume credits. All other Apollo API endpoints do not consume credits." and adds Run an Assistant task (variable), Get conversations info and Export conversations (1 credit per conversation with AI insights). Earlier table had no AI/conversation rows. Earlier "UNDOCUMENTED" for auth/health and legacy search paths is unchanged (still not listed anywhere), though the "all other endpoints" sentence now supports FREE by inference for documented-but-unlisted endpoints.
- **D7 Org search empty pages.** api-pricing adds the generic sentence "may consume credits when qualifying data is returned" for enrichment, organization search, news search and AI insight endpoints. This is the only docs support for the measured 0 on empty org-search pages; the endpoint page itself still says only "1 Apollo credit per page".
- **D8 Rate-limit facts in earlier table.** Earlier text cited "600 calls per hour" for org enrichment and "600 times per hour" for api_search. Current Rate Limits page: enrichment endpoints on paid plans 1,000 per minute with no hourly or daily limit (Free: 50/min, 20/min for bulk, 200/hour, 600/day); search endpoints 200/min, 6,000/hour, 50,000/day on paid; limits are per team and per endpoint. The "600 times per hour" strings that remain are 429 example bodies on the reference pages (stale examples).
- **D9 Per-user vs team meters.** api_profile counters are per user ("Lead credits used by the user"), credit_usage_stats is team-level and updates "within seconds". The earlier reading that api_profile export_used = 2 vs UI Exports 16,365 means "API counters ignore UI exports" is better explained by per-user vs team scope.
- **D10 Waterfall.** Earlier row 4 had no figures. v2: email typically 1-4 (20+ worst case), phone typically 8-25 (45+ worst case), credits reported only in the async payload. UI Waterfall bucket of 1,175 before the window is unexplained by repo code (earlier table says waterfall never used).
- **D11 Re-enrichment.** Docs now state it on two pages (convert-enriched-people-to-contacts, Capabilities) and the Apollo marketing insights page notes re-enriching a clean list drains credits. Still no sentence of the form "not charged for already enriched records" in any fetchable first-party page; the only such sentences are in the blocked KB (summariser-derived: job-change data already purchased; charged even if the value is unchanged).

## 5. Docs are SILENT: needs a live test (designs only; NOT run)

All tests: read `POST /usage_stats/credit_usage_stats` (0 credits, team level) before and after each call, one call at a time, log every credit_type key. Budget about 45 credits in total if S1-S10 are all run.

| ID | Item | Rows | Cheap test design |
|---|---|---|---|
| S1 | People match no-match (bogus name+domain, no reveal) | Docs say 0; CO ledger books 1. | Read credit_usage_stats; 1 people/match with a fabricated person; read again; x3. Max 3 credits. |
| S2 | reveal_phone_number billing moment (acceptance vs delivery) and failed-reveal billing | R03 / P4 | Pick one person known to have no mobile (ideally only a work-direct number). Read credit_usage_stats (all types); POST people/match with reveal_phone_number + poll_only=true (also resolves R04); read stats immediately after the sync 200; poll /webhook_result until result; read stats again. Max 9 credits. Compare with webhook/poll credits_consumed. |
| S3 | Which meter / UI bucket takes unlock (1) and reveal (8): lead, direct_dial, export | R01 / R03 / P1 / P2 | Same read-before/after of credit_usage_stats (all credit_type keys) around S1/S2 calls, plus a look at the UI Credit usage page filtered by surface (API) for the same minute. Zero extra credits. |
| S4 | reveal_personal_emails true vs false | R05 | Two fresh, comparable people (each never looked up): one with false, one with true; compare deltas. Max ~2 credits. |
| S5 | organizations/enrich single call | R12 | One call for a known domain, stats before/after. 1 credit expected. |
| S6 | organizations/bulk_enrich unit (submitted vs found vs unique) | R13 | 10 entries: 4 real, 4 nonexistent, 2 duplicates; stats before/after. Max 10 credits. |
| S7 | mixed_companies/search empty page and page beyond last page | R14 | Query with a filter returning ~0 results (1 call), and page=last+1 on a small result set (1 call). Max 2 credits. |
| S8 | Legacy paths /mixed_people/search and /organizations/search | R16 | One call each with per_page=1; expect 404 or alias. Max 2 credits. |
| S9 | Repeat lookup of an already-enriched person (and repeat reveal) | R02 | Re-call people/match for a person unlocked in S1/S2; then re-call with reveal_phone_number. Max 1 + 9 credits. |
| S10 | bulk_match: does the sync credits_consumed include async phone credits? | R10 | 2 people, reveal_phone_number=true, webhook/poll; compare sync credits_consumed, webhook credits_consumed and stats delta. Max 18 credits. |
| S11 | Double billing on 429 / timeout / client retry | R28 / P5 | Not cheaply testable. Observe: log x-24-hour-usage and the 429 count against credit_usage_stats deltas during a real run; or send 2 identical requests in quick succession to a single paid endpoint and compare deltas. |
| S12 | GET /auth/health | R27 | One call between two credit_usage_stats reads. 0 expected. |
| S13 | Webhook retry duplicates | R29 | Receive a real webhook (webhook.site) for one reveal; check delivery count vs credit delta. Part of S2. |
| S14 | Waterfall email / phone (only if the team has waterfall configured) | R06-R08 | Do not test without confirming vendor config; up to 4-20 (email) / 8-45 (phone) credits per person. |

## 6. Pages that could not be fetched raw
- HTTP 403 (Cloudflare "Just a moment"): knowledge.apollo.io articles 9527776320781 (What Are Credits? / Review Credit Usage in Apollo, all URL variants), 34071121664781 (Use Waterfall Enrichment), 4416173158541 (Use Apollo API), 31969477982221 (Access a Prospect's Phone Number). WebFetch also 403. Not worked around.
- HTTP 404 (not in docs any more or never existed): docs.apollo.io/docs/apollo-api-faqs (linked from the OpenAPI info block), /docs/overview-apollo-api-tutorials, and any slug for People Search (`mixed_people/search`) or legacy Organization Search. The reference index (llms.txt + sitemap + OpenAPI paths) contains no deprecated search endpoints.
- HTTP 429 (docs.apollo.io rate limiting after a burst of ~110 requests): a few exploratory guesses only; the 109 sitemap pages all fetched 200.
- apollo.io/pricing/about-credits fetched 200 but its credit amounts are client-rendered ("Email = - credit", "Phone number = - credits"), so the numbers are not in the raw HTML. Pricing page text for per-action credit costs is likewise templated ({creditCost}).

## 7. Files
- `/Users/bhanu/Desktop/LeadGenMonolith/docs/audit/apollo/agents/endpoint_billing_table_v2.md`
- `/Users/bhanu/Desktop/LeadGenMonolith/docs/audit/apollo/agents/endpoint_billing_table_v2.csv`
- `/Users/bhanu/Desktop/LeadGenMonolith/docs/audit/apollo/apollo_docs_snapshot/` (raw pages, `_credit_sentences.md`, `_fetch_log.tsv`, `_index_*`, `openapi__apollo-rest-api.json` + `.meta.txt`)

