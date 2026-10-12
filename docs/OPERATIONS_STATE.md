# Operations state — as of 2026-10-12

This is the handoff doc: what's actually live in HubSpot right now, who's working which subsegment and
why, the rotation rule that drives sourcing, everything changed in HubSpot over the last 2 days, and
what's still pending. Written so that once you have `.env` + `secrets/` filled in, you (or your Claude
Code session, which auto-loads `CLAUDE.md` first) can pick this up and continue exactly where it left
off, without re-deriving any of it from scratch.

Read `CLAUDE.md` → `README.md` → `CONTEXT.md` → this file → `docs/playbooks/icp_sourcing_playbook.md`,
in that order, before touching anything.

## 1. The 5 verticals and who runs them

| Vertical | HubSpot portal(s) | Reps | Daily target |
|---|---|---|---|
| Coding | 3 separate portals: MAIN, CompanyOps, RAT | Lamiya, Yuktha, Shagufta (share MAIN) · Vaishnavi (sole owner, CompanyOps) · Harsha (sole owner, RAT) | 100/rep/day |
| Company Ops Cluster 1 | CompanyOps, pipeline `default` | Manit, Pratyush | 100/rep/day |
| Company Ops Cluster 2 | CompanyOps, pipeline `2464812771` | Tanisha, Amisha | 100/rep/day |
| Company Ops Global | CompanyOps | — (not actively rotated; see Pending) | — |
| RAT / Specialty Datasets | RAT portal | — (not actively rotated; see Pending) | — |

Cluster 1 and Cluster 2 are segment-gated: a deal's `hubspot_owner_id` is set by its `segment` property,
not by whichever rep happened to source it. See the `SEGMENT_TO_OWNER` map in each cluster's Apps Script
(`apps_script_client.get_content(scriptId)` to read it live — script IDs below). Coding is rep-gated
directly (`REP_TO_PORTAL` map), since Coding doesn't use industry segments the same way.

**Cluster 1** (`SEGMENT_TO_OWNER`):
- Manit: Food Tech, Mobility, Healthcare/MedTech
- Pratyush: Gaming & Sports, Ed-Tech, Ecommerce/Marketplace

**Cluster 2** (`SEGMENT_TO_OWNER`):
- Tanisha: Enterprise SaaS/IT, Fintech, Financial Services, Adtech
- Amisha: Market Research, Construction, Real Estate/Proptech, Manufacturing (Amisha also absorbed
  Vaishnavi's old 4 segments after Vaishnavi moved to Coding — see Pending, this routing still needs a
  final decision on whether to reassign Vaishnavi's *existing* deals or just route new ones to Amisha)

**Coding** (`REP_TO_PORTAL`): Lamiya/Yuktha/Shagufta on MAIN (same portal, different owner IDs, compete
for the same Queue pool by rep, not by segment) · Vaishnavi sole owner on CompanyOps · Harsha sole owner
on RAT.

## 2. Headcount bands and the quota split

Every vertical pulls from 3 headcount bands with a fixed per-rep daily split (`BAND_QUOTAS` in each Apps
Script): **34 / 33 / 33** out of the 100 daily target, always in that order of priority — the remainder
(if a band is short) fills from whatever's left in the Queue regardless of band, see `selectRowsForRep_`.

- **Cluster 1 and Cluster 2** use 3 bands: `51-200`, `201-500`, `501-1000`.
- **Coding** uses a *different* 3-band split: `51-200`, `201-350`, `351-500` — no `501-1000` band in
  Coding at all. Don't copy Cluster 1/2's bands onto a Coding sourcing script by habit.
- **Standing exception, every vertical, set 2026-10-12**: a hospital lead needs 200+ headcount to
  qualify, full stop — this floor applies on top of whichever band the vertical would otherwise accept,
  logged as an ICP rejection like any other mismatch. See `docs/playbooks/icp_sourcing_playbook.md`.

## 2a. Full segment and sub-ICP lists (pulled live from HubSpot, 2026-10-12 — this is the real governed
enum, not a plan document; it has historical naming drift in it, documented as-is)

**Cluster 1 / Cluster 2 `segment` property (CompanyOps portal, 28 options).** Several of these are
duplicate/renamed variants from different points in this project's history (e.g. `Fintech` vs
`Fintech (Delhi)` vs `Fintech (Hyderabad)`, `Healthcare/MedTech` vs `Healthcare & Medtech` vs
`Healthcare&Medtech_Hyd`/`_Mumbai`, `Enterprise SaaS/IT` vs `Enterprise SaaS / IT`). The live
`SEGMENT_TO_OWNER` maps (section 1) only use the "current" spelling for each — check there before
assuming an older variant is still routed anywhere:
```
Healthcare & Medtech · Fintech · Proptech · Construction & Real Estate · Adtech · Gaming & Sports ·
Enterprise SaaS/IT · Mobility Tech · Manufacturing · E-commerce & Marketplace · FoodTech · EdTech ·
Market Research · Fintech (Delhi) · Healthcare&Medtech_Hyd · Healthcare&Medtech_Mumbai ·
Fintech (Hyderabad) · IT Services · Gaming_Blr · Construction · Enterprise SaaS / IT ·
Financial Services · Real Estate / Proptech · Ecommerce/Marketplace · Ed-Tech · Food Tech ·
Healthcare/MedTech · Mobility
```

**Cluster 1 / Cluster 2 `sub_icp` property (51 options)**, grouped by the segment family they belong to:

- *Manufacturing*: Discrete manufacturing with MES/ERP-integrated shop floors · Process manufacturing
  (chemicals, pharma, FMCG) · Auto component & EMS/contract manufacturers · Industrial IoT /
  predictive-maintenance platforms · Supply chain & procurement platforms serving manufacturers
- *Fintech / Financial Services*: Payment providers · Lending / NBFC & BNPL platforms · Digital wallets
  & UPI-first apps · Neobanks / digital-first banking platforms · InsurTech (claims + underwriting
  workflows) · WealthTech / robo-advisory & broking apps · Credit scoring & underwriting engines ·
  Banks (retail & SME lending ops) · NBFCs / housing finance companies · Insurance companies (life,
  health, general) · Asset management companies / mutual funds · Stock broking & wealth management
  firms · Credit rating agencies
- *Real Estate / Proptech / Construction*: Proptech marketplaces & transaction platforms · Developer
  CRM/sales & construction PM platforms · Coworking / flex-workspace operators · Co-living & managed
  rental platforms · Society/facility management SaaS · Data-driven brokerage & advisory firms ·
  EPC & general contractors · Construction-tech (PM, BIM, scheduling platforms) · Construction
  materials procurement platforms · Architecture & engineering design firms · Site execution platforms
- *Adtech*: Programmatic DSP/SSP & ad exchange platforms · Marketing automation / CDP platforms ·
  Influencer marketing & creator-commerce platforms · Attribution & campaign analytics platforms ·
  Retail media networks
- *Market Research*: Syndicated/panel-based research firms · Survey & consumer-insights platforms ·
  Data/analytics-as-a-service providers · Social listening & sentiment-analysis platforms ·
  CX/feedback management platforms
- *Enterprise SaaS/IT*: Vertical SaaS (HRTech, MarTech, LegalTech, CRM/ERP) · IT services & GCCs ·
  DevOps/observability & ITSM platforms · Customer support / helpdesk SaaS · API/infra &
  developer-tooling platforms
- *Food Tech / Mobility / Healthcare / Gaming / Ed-Tech / Ecommerce* (Cluster 1's segments): Food
  delivery & QSR tech platforms · EV, ride-hailing & logistics tech · Digital health, telemedicine &
  diagnostics platforms · Gaming studios, esports & fantasy sports platforms · Online learning, test
  prep & upskilling platforms · D2C, marketplace & quick-commerce platforms
- *IT Services (Coding's own, also present here)*: IT services, consulting & managed services providers
  (India)

**Coding `segment` / `sub_icp` (MAIN portal, confirmed 1 option each — no sub-segmentation at all)**:
segment = `IT Services`, sub_icp = `IT services, consulting & managed services providers (India)`. The
headcount band is the *only* axis Coding rotates on — see the 3-band split in section 2.

**`headcount_band` property**: 5 total options exist in the CompanyOps portal (`51-200`, `201-500`,
`501-1000`, `201-350`, `351-500`) because Coding's 3-band scheme and Cluster 1/2's 3-band scheme share
the same portal's property definition but use different subsets — don't assume all 5 are valid for a
given vertical's push; check section 2's per-vertical band list before writing a `headcount_band` value.

## 3. The rotation rule — what happens when a rep falls short

**Hard rule**: if a rep's Queue pool runs dry and `dailyPush` can't fill their 100, the response is not
"report the shortfall and wait" — it's "source the next sub-ICP immediately." Pick the next
unworked sub-ICP for that rep's segment set, guess the keywords/tech-stack signal from general
knowledge if the ICP mapping sheet doesn't have one yet, and run a real sourcing pass same-day. This is
what kept Amisha at her 100 on 11-12 Oct (Process Manufacturing + Real Estate/Proptech top-ups) and what
got Cluster 1 Coding reps to their numbers today when individual sub-ICPs ran out.

The actual sourcing workflow (Apollo search → `technology_uid` resolution → dedup → reveal →
qualify → push) is `docs/playbooks/icp_sourcing_playbook.md`, written to be repeatable cluster-by-cluster,
sub-ICP-by-sub-ICP. It was written against Cluster 2/Manufacturing first and is meant to generalize
unchanged to every remaining sub-ICP in every vertical.

## 3a. The sourcing API call structure, and how rotation actually works in practice

Every sourcing script follows the same shape — one real example, from today's Coding sourcing pass
(`scratchpad/source_coding_itservices.py`, the pattern is identical for Cluster 1/2 scripts):

```python
for band in BANDS:                      # loop every headcount band for this sub_icp, one Apollo call each
    body = {
        "person_titles": ["Founder", "Co-Founder", "CEO", "CTO", "COO"],  # strict C-suite only, every vertical
        "include_similar_titles": False,
        "person_seniorities": ["owner", "founder", "c_suite"],
        "person_locations": ["India"], "organization_locations": ["India"],   # India-only, hard rule
        "organization_num_employees_ranges": [band],       # one band per call, e.g. "51,200" (Apollo's comma format)
        "q_organization_keyword_tags": JOB["keywords"],     # the sub_icp's keyword list, agreed with the user per sub-ICP
        "page": 1, "per_page": 100,
    }
    status, data, apollo_call_id = call("POST", "/mixed_people/api_search", body,
        purpose=f"ICP sourcing - {cluster}/{vertical}/{sub_icp}, {band} band",
        caller="source_<script>.py", segment=f"<cluster>_<subicp>_{band}",
        request_key=f"icp-sourcing-<cluster>-<subicp>-{band}", allow=True)
```

Always through `leadgen/apollo_ledger.py`'s `call()` — never `requests` directly against
`api.apollo.io`. `request_key` makes the call idempotent-at-the-ledger-level (same key = ledger can tell
it's a repeat, though Apollo itself still charges full price on a genuine re-reveal, see `CLAUDE.md`).
`allow=True` is the explicit per-job go-ahead required while `APOLLO_PAUSED=1`.

Where `JOB["keywords"]` comes from, and when `technology_uid` gets added: `person_titles`/seniorities
are fixed across every vertical (strict C-suite, confirmed working pattern) and titles are locked with
the user in explicit preference order *before* sourcing starts, per sub-ICP (see
`docs/playbooks/icp_sourcing_playbook.md` Step 2 — the actual decision-maker title varies by sub-ICP,
e.g. discrete manufacturing is C-suite-only since ops/IT roles can't approve a data-sharing deal). A
`technology_uid` filter gets added on top of keywords only where Apollo's tech-stack category actually
has India-relevant coverage — confirmed absent for some categories (Cloud Kitchens POS vendors are
almost entirely US/Western in Apollo's data; the real India leaders — Petpooja, POSist, UrbanPiper,
LimeTray — aren't tracked) and confirmed present for others (Manufacturing's SAP/Siemens/AVEVA signal is
genuinely global). Check Step 3 of the playbook per sub-ICP before assuming a tech-stack leg exists;
when it doesn't, fall back to keyword + industry + headcount + title only.

**The actual rotation sequence, end to end, for one rep running short:**
1. `dailyPush` logs a shortfall (`pushedCount + failedCount < 100`) to `PushLog` and e-mails the alert
   address — this is the *signal*, not the fix.
2. Pick that rep's next unworked sub-ICP within their assigned segment(s) (section 1's `SEGMENT_TO_OWNER`
   / `REP_TO_PORTAL`) — "unworked" meaning no `ext_sourcing_run` row yet for that
   cluster/vertical/sub_icp/band combination (query the DB, don't rely on memory of what's been done).
3. Lock titles/seniorities for that sub_icp with the user if not already agreed (playbook Step 2).
4. Run the search above, one Apollo call per headcount band, same sub_icp.
5. Dedup against everything already sourced for that cluster (playbook Step 5 — per-company dedup using
   the locked title preference order, so a company already captured via a higher-priority title doesn't
   get re-pulled under a lower one).
6. Qualify: India-only, mobile-mandatory (reject email-only/landline-only, don't build a fallback tier),
   hospital-200+ floor where applicable, any other sub-ICP-specific exclusion. Every rejected account
   still gets logged with its verdict/reason — rejections are never silently dropped, see
   `[[feedback_track_every_funnel_outcome]]` / `[[project_tam_local_db]]` in Claude's memory, and
   `v_funnel_leak_summary` in the DB (migration `0026_funnel_leaks.sql`) for the queryable rollup.
7. Reveal (SignalHire first, Apollo `/people/match` reveal as fallback — enrichment order is a hard
   rule, SignalHire-before-Apollo on every lead, not just when Apollo is paused).
8. Push the qualified, revealed batch into that rep's Queue rows (`status: pending`), ready for the next
   `dailyPush` run.

This is exactly the sequence that ran live on 11-12 Oct for Amisha (Process Manufacturing, then Real
Estate/Proptech, as same-day top-ups) and for Coding's reps today — not a theoretical plan, the actual
worked pattern.

## 4. Apps Script IDs (the actual push automation — lives outside this repo)

Each funnel's `dailyPush()` (Queue sheet → batched HubSpot create/associate) is a bound Google Apps
Script project, not code in this repo. To read or change the live push logic:
`apps_script_client.get_content(scriptId)` / `update_content(scriptId, files)` from
`legacy/hubspot/crm_mirror/enrich/apps_script_client.py`.

| Vertical | Script ID |
|---|---|
| Coding | `1Pqs2gV8xRRTtJ2iWm4Yl7fwE_CD_k_y__MbNQ37vjA18S0BNn9kNP5xx` |
| Cluster 1 | `1buYNNoAVmXPHfru_KSrST9oH8sbev3kYKUmaNdv92tNR2mWYbRyx9AoT` |
| Cluster 2 | `1WIrHwO8WSaKutB_daBjdUP7NYcbybTnpSN2_1jpfQbXiRIBRfrZwyz7t` |

**Important**: `update_content()` only writes the editable HEAD version. A bound Web App URL stays pinned
to whatever version was last deployed via `projects().deployments().update()` — pushing new code with
`update_content()` alone does **not** reach a live webhook URL. Running a function manually from the
Apps Script editor (Run button) *does* execute HEAD directly, no deployment needed — that's the fast path
used throughout this session to apply fixes without a deploy step.

## 5. Everything changed in HubSpot, last 2 days (2026-10-11 → 2026-10-12)

**Cluster 1 / Cluster 2** (mostly done before today, confirmed still live):
- Both migrated off a flat "No Pickup" stage onto a 3-attempt chain (`No Pickup - Attempt 1/2/3` →
  `Dead: Cold Call / No Pickup` once exhausted), each with a callback task + `followup_channel_touch`
  on attempt 1.
- Cluster 1's `VC setup` weekly target was wrong (8/WTD instead of 15) — traced to
  `daily_sync_pull.py`'s `DAILY_LEADS["cluster1"]` being 100 instead of 200 (Cluster 1 has 2 reps ×
  100/day = 200/day team-wide, same as Cluster 2 — this was silently halving *every* Cluster 1 target,
  not just VC setup). Fixed in code and in the live Performance Report sheet.
- Cluster 1's OutFlo campaign-name matcher was silently dropping leads under the campaign name
  `Company Ops: C1 | Gaming & Sports` (no `(V2)` suffix) — fixed by adding the bare name as an alias.

**Coding** (all done today, 2026-10-12):
- Built the 3-portal push automation from scratch (`REP_TO_PORTAL`, batched create/associate,
  `dailyPush`), mirroring Cluster 1/2's pattern.
- Found and fixed a real duplicate-deal bug: a chunk's outer catch treated *any* post-create failure
  (e.g. association-step error) as "the whole chunk failed," triggering a sequential redo that recreated
  deals already successfully made. Confirmed live (one rep got exactly double). Fixed by isolating
  association/notes into their own try/catch that never triggers a redo. Ported the same fix to
  Cluster 1/2's scripts proactively.
- Migrated off the flat "No Pickup" stage onto the same 3-attempt chain as Cluster 1/2, across **all 3
  Coding portals**. This included moving **1,621 in-flight deals** (1,469 MAIN + 74 CompanyOps + 78 RAT)
  from the old stage into `No Pickup - Attempt 1`, then archiving the old stage on all 3 portals.
- Added `Dead/Interested/No Response` and `Dead/Interested/Not Interested` as two new dead reasons under
  `Interested` (Coding only had `Dead/Interested/NoShow` before, less granular than Cluster 1/2's
  equivalent `No Response` / `Not Interested` pair) — then **merged them into one**
  `Dead/Interested/No Response or Not Interested` stage per user feedback, and removed the "no show"
  branch from that same decision point in the SOP/diagram (a no-show can only happen after a GMeet is
  actually booked — that's what `Dead/GMeet/NoShow` further down the funnel is for; routing a no-show
  reason off the bare Interested stage was logically wrong). `Dead/Interested/NoShow` itself still
  exists in HubSpot (not deleted) but is unreferenced in the live flow now.
- `Contract/LOI signed`'s formula in `daily_sync_pull.py` now also counts `Closed/Won` deals, not just
  `LOI` + `Deal Contract Signed` (same stage-visit-sum convention as every other row — not deduped
  against the other two labels).
- Today's push: **513/513 Coding deals pushed** across all 5 reps (Lamiya 102, Yuktha 100, Shagufta 102,
  Vaishnavi 107, Harsha 102).
- **Found and fixed a serious, repo-wide data-integrity bug**: nearly every deal pushed today (across
  all 3 funnels, not just Coding) had its contact-association step silently fail — the push script's
  association/note code is deliberately wrapped in a catch-and-log (to avoid redoing the duplicate-deal
  bug above), so a failed association never surfaced as an error; the deal just sat there with no linked
  contact, or a linked contact with no phone saved. Backfilled from the Queue sheet's own source data
  (every phone number already existed on a real, just-unlinked contact — nothing had to be invented).
  Verified against live HubSpot, not the Queue sheet: **949/949 deals clean** (Coding 511, Cluster 1 218,
  Cluster 2 220) with a linked contact and a saved phone number.
- 2 Coding deals were pushed with non-Indian phone numbers (Philippines, Singapore) — legacy re-queued
  leads from before the India-only gate existed, slipped through a "clean slate" re-queue that didn't
  re-check nationality. **Not yet archived** — flagged in the Coding Queue sheet (status column says
  `NEEDS ARCHIVE`), HubSpot delete was blocked by this session's own tooling permissions; archive them
  by hand in HubSpot. No equivalent `+91` gate exists anywhere in the push pipeline today — worth adding
  to `selectRowsForRep_` before the next clean-slate re-queue happens on any funnel.

**All 3 funnels**: ~977 Queue rows were held (`status: held_for_report_pull`) during yesterday's report
rebuild and restored to `pending` once it finished; most have since been pushed via the fixes above.

## 6. Performance Report (built this session, now live)

`tools/daily_sync_pull.py --write --write-performance-report` writes 5 tabs (Coding, Company Ops
Global, Cluster-1, Cluster-2, Engineering Datasets) in the Performance Report sheet
(`PERFORMANCE_REPORT_SHEET_ID` in `.env`). Each tab now has two uniform blocks: a frozen "last week"
table (Mon-Fri, real historical numbers where available) and a live "this week" table, both the same
13-row shape (9 core metrics + `Picked to assigned %` + `VC completed to interested %`), same dark-navy/
teal/light-mint color banding, same column widths and row heights across all 5 tabs. Company Ops Global
and Engineering Datasets never had a real historical last-week pull, so their last-week blocks use
today's real Leads-assigned actuals as a stand-in with everything else blank, clearly labeled as such.

The 8am daily trigger for this (`tools/com.lh2.dailysync.plist`) exists but was **unloaded** during
this session's active rebuild work and has not been reloaded — check `launchctl list | grep dailysync`
before assuming it's running.

## 7. Pending / not done yet

- **The 2 non-Indian-number Coding deals** (section 5) — need manual archive in HubSpot.
- **9 Cluster 1 deals** sitting in `Cold lead assigned` with no `hubspot_owner_id` (their `segment`
  value didn't resolve through `SEGMENT_TO_OWNER`) — not yet investigated.
- **No `+91` validation gate** anywhere in the push pipeline — add one before the next clean-slate
  re-queue on any funnel.
- **"Daily Sync" tab** (separate from the Performance Report sheet, in the Supply Team Mapping sheet)
  has real content corruption from multiple overwrite generations — `write_sheet()` in
  `daily_sync_pull.py` does a blind `values().update()` with no clearing first, same class of bug
  already fixed for `write_performance_report()`. Not yet fixed.
- **Amisha's routing for Vaishnavi's old 4 Cluster-2 segments** — still needs a decision: reassign
  Vaishnavi's *existing* deals to Amisha, or only route new ones to her going forward.
- **Company Ops Global and RAT verticals** have no active rep rotation set up (no `SEGMENT_TO_OWNER` /
  `REP_TO_PORTAL` equivalent, no Queue-driven daily push) — they're tracked in the Performance Report
  but not pushed to daily like Coding/Cluster 1/Cluster 2.
- **The Firestore/cloud-engine migration** — deliberately deferred. Trigger (per the user, when first
  proposed): once every vertical's sourcing pipeline is live and stable. Not started.
- Per-rep follow-up-coverage metrics in the Performance Report are blocked on whether reps actually log
  real HubSpot Tasks — currently shown as `n/a`, unverified either way.
- "Same-day script-shared coverage" is structurally degenerate in the current stage model (VC-completed
  and script-shared collapse into the same transition on the proceed path) — flagged, not fixed, would
  need a stage redesign.

Related reading: `docs/playbooks/icp_sourcing_playbook.md`, `CONTEXT.md`'s "known stale" section (some
of those notes are now genuinely stale themselves — cross-check dates before trusting any of them).
