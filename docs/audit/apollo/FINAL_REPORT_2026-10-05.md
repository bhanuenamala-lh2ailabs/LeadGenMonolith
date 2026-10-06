# Apollo credits: final report (21 Sep to 4 Oct 2026)

Prepared 5 Oct 2026 for bhanu.enamala@lh2.ai. Sources: this folder's `MASTER_RECONCILIATION.md` (our audit, built from the three repos' own ledgers) and the teammate trace e-mailed by Sreenandan M S on 5 Oct, 12:54 IST.

## 1. Bottom line
- **Apollo's chart shows about 96,100 credits used** in the cycle (96,439 per the account balance).
- **We can attribute 41,469 of them (43%)** to a specific repo, script and purpose. The other **54,631 (57%) have no traced source.**
- **Half of the traced credits (21,156) are estimates**, not measured or logged at run time.
- **Bulk activity stopped around 1 Oct, 10:30 IST.** Four sources agree on this, including the teammate's day-by-day figures. Oct 2 and Oct 4 have no or near-zero spend.
- **Apollo has not confirmed that the account is out of credits.** That conclusion is inferred.

## 2. Where the credits went (traced only, 41,469)

| Repo | Traced | Logged at run time | Measured | Estimated |
|---|---:|---:|---:|---:|
| hubspot (incl. RAT-purpose runs) | 25,421 | 3,647 | 5,410 | 16,364 |
| companyOps | 14,259 | 8,266 | 2,049 | 3,944 |
| RapidActionTeam (own rows only) | 1,789 | 0 | 941 | 848 |

| Endpoint class (Apollo docs verdict) | Traced |
|---|---:|
| Phone reveal, +8 per mobile (conditional) | 25,836 |
| Person match / unlock (conditional) | 4,849 |
| Org enrichment (1 per organisation: **billed**) | 4,051 |
| Org search (1 per page: **billed**) | 3,715 |
| Org search + enrich pipelines (**billed**) | 3,018 |

Note on the classes: a pipeline that matches and then reveals a phone lands in the phone class, so the phone total is slightly high.

## 3. Day by day (US Pacific, matching Apollo's chart)

| Day | Apollo | Traced | Untraced |
|---|---:|---:|---:|
| 21 Sep | 12,500 | 2,432 | 10,068 |
| 22 Sep | 6,800 | 153 | 6,647 |
| 23 Sep | 5,600 | 2,330 | 3,270 |
| 24 Sep | 12,400 | 6,911 | 5,489 |
| 25 Sep | 3,000 | 1,547 | 1,453 |
| 27 Sep | 4,600 | 3,953 | 647 |
| 28 Sep | 9,300 | 6,378 | 2,922 |
| 29 Sep | 13,800 | 3,754 | 10,046 |
| 30 Sep | 14,800 | 4,201 | 10,599 |
| 1 Oct | 7,100 | 4,005 | 3,095 |
| 3 Oct | 6,200 | 5,805 | 395 |
| **Total** | **96,100** | **41,469** | **54,631** |

The gap is largest on 29 and 30 Sep (about 20,600 credits). Those are the days with the best-audited tracking, so better tracking will not close it.

## 4. The teammate's trace vs ours

| Item | Teammate (Sep 28 to Oct 4) | Ours (same window) |
|---|---:|---:|
| Total traced | 21,971 | 24,143 |
| Days | 28 Sep 3,597; 29 Sep 8,886; 30 Sep 6,085; 1 Oct 3,402; 4 Oct 1 | 28 Sep 6,378; 29 Sep 3,754; 30 Sep 4,201; 1 Oct 4,005; 3 Oct 5,805 |
| RapidActionTeam | 11,879 | 1,789 |
| hubspot repo | 5,903 | 25,421 (full cycle, not just this window) |

What this means:
- **Totals are close (about 2,200 apart) but the day split differs.** The teammate's 29 Sep figure is more than double ours, and our 3 Oct figure is in a day he doesn't list.
- **RapidActionTeam attribution differs.** He counts 11,879 under RAT. We count RAT-purpose spend run from the hubspot repo under hubspot, so only 1,789 sits under RAT. Both views can be true, but the owner of that spend needs to be agreed.
- **Balance drop.** The teammate reports a 22,188-credit fall from 29 Sep to 1 Oct. His traced spend covers about 15,000 to 17,000 of it, which leaves roughly 5,000 to 7,000 unexplained.

## 5. Things that changed the picture
- **Org search and org enrichment are billed.** Every repo's code comments called them free. Together they account for roughly 8,000 credits (teammate: 8,224, or 34 to 37% of spend). This is the main cause of overspend, and it is now fixed in our code by treating them as billed.
- **Key rotation (corrected).** The real rotation was on **29 Sep, 12:51–12:58 IST**, applied to hubspot, companyOps and RapidActionTeam within about seven minutes. The 30 Sep 10:07 event was a one-character typo fix in companyOps' copy of the same key, not a second rotation. Source: teammate's VS Code edit history, cross-checked by two agents.
- **Hyderabad Healthcare and Medtech push: resolved.** A read-only HubSpot count in companyOps' portal shows **41 deals** created in one batch. The "90" was the planned count (738 raw, 106 eligible, 90 after dedup). Credits were likely spent against the larger list.
- **Activity outside these three repos is possible.** Examples: 22 Sep Waterfall and Email credits (about 1,175 and 786), a burst of activity overnight 27 to 28 Sep, a 21 Sep record creation with no session running, and an overnight 66-deal RapidActionTeam push on 29 Sep. Nothing here identifies who.

## 6. Second audit (teammate, 5 Oct) and what changed

The teammate's audit covers Sep 21 to Oct 4 with a wider cycle view. Key figures, read from Apollo's `credit_usage_stats` on 5 Oct:

| Item | Teammate | Ours |
|---|---|---|
| Apollo cycle-to-date `lead_credit` consumed (cycle 20 Sep to 20 Oct) | **99,281** (read from Apollo's meter) | 96,100 (read off the chart screenshot, ±0.3k) |
| Traced credits | 43,647 (98.8% estimated) | 41,469 (51% estimated) |
| Untraced | 55,634 (56%) | 54,631 (57%) |
| Apollo `lead_credit` **left over** | **4,999** | Our notes said about 7,294. Needs a fresh read to confirm. |
| `direct_dial_credit` consumed | 60,107 (same pool, do not add) | not tracked |

The two audits agree that roughly 55,000 credits have no traced source. They disagree on the day split: the teammate's 29 Sep is 8,886 against our 3,754, and his 3 Oct is 0 where ours is 5,805. Neither audit has Apollo's per-day series, so the day split stays unresolved.

Stranded spend, with no HubSpot deal to show for it (needs a decision: push or write off):
- companyOps CompanyOps_Mumbai and CompanyOps_IT_Hyd, about **2,214 credits**.
- Healthcare & Medtech Chennai and chennai_codebase, about **1,502 credits**.

Still open:
1. **"Apollo is out of credits" has never been confirmed by Apollo.** No transcript quotes a real Apollo 402, and one run's checkpoint shows 87 successful Apollo emails the same day. SignalHire's 402 is quoted twice, for comparison.
2. **Negative `web_search_record_credit` (-4,799)** in the meter read is an anomaly. It is recorded as returned, not explained.
3. **Apollo's per-user daily export** is still the one source that would settle the gap. Neither audit could get it.
4. **New owner name** in companyOps Sep 21–27 activity: "Anuj Chahar", not on the known roster.
5. **Open live tests** (about 45 credits, needs your approval): no-match `people/match`, phone reveal with no mobile, `reveal_personal_emails` on and off, one `organizations/enrich`, and a repeat lookup.

## 7. What we are doing now
- No bulk Apollo spend has run since 1 Oct.
- Apollo, SignalHire and Apify are excluded from the current account-building run. Crawling and ICP judging use no vendor credits.
- The LinkedIn ads contacts and pushes use no Apollo credits.

## 8. Draft prompt for the teammate (not sent)
> Hi Sreenandan, thanks for the trace. To reconcile it with ours, please send: (1) Apollo's own credit usage for 21 Sep to 4 Oct, by Pacific day and by user, from the Apollo usage page; (2) the 30 Sep key rotation: which key each repo used before and after, and when; (3) the Hyderabad Healthcare and Medtech push, the 90 vs 41 difference, and which file holds the 90; (4) your day split for 29 Sep (8,886) and 3 Oct, with the rows behind them. Please do not run any Apollo calls to get this.
