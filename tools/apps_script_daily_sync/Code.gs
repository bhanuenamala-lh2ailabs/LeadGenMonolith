/**
 * LH2 Daily Sync - Scheduled (Apps Script port of tools/daily_sync_pull.py)
 *
 * REWRITTEN 2026-10-13 from scratch against the CURRENT tools/daily_sync_pull.py (the verified-correct
 * reference) - the previous draft of this file (built 2026-10-12, paused same day) predates the
 * rep-day/WTD/MTD/ITD rollup blocks, the LOI/Closed-Won union-dedup, and Coding's 3-portal support
 * entirely; its core pull/write logic is NOT reused. What WAS reused from it: the general shape of
 * hdr_/fetchJson_ auth, stageLabels_/batchHistory_/batchTaskAssociations_/emailTaskIds_ (HubSpot fetch
 * helpers), and the hard-won fix of managing row/column groups via raw Sheets API batchUpdate
 * (delete-all-then-add-fresh) rather than SpreadsheetApp's shiftRowGroupDepth (proven non-idempotent -
 * see rebuildGroups_'s comment).
 *
 * SCOPE: cluster1, cluster2, coding, engineering (RAT) - EXTENDED 2026-10-22 per explicit user ask to
 * also cover Engineering Datasets at the 6pm sync. coops_global is still not written by this script
 * (no SOP/rep model wired up for it, per the original scope decision - "only coops 1 and 2 and coding
 * on three accounts as only those have hubspot changes and sops made to capture details to compute
 * these numbers"). Engineering's constants (REP_MAPS_/rowFormulas_/BUCKET_STAGES_/targets/portal) are
 * ported verbatim from daily_sync_pull.py's "rat" vertical (its internal key - this file's user-facing
 * name is "engineering" to match the Performance Report tab title "Engineering Datasets"). IMPORTANT
 * DIVERGENCE FROM cluster1/cluster2/coding, carried over faithfully from the Python reference: engineering
 * gets NO granular/follow-up-coverage stats (Same day lead coverage, Email/WhatsApp touch %, Follow-up
 * coverage x3, Same day script coverage all render BLANK, not computed) - daily_sync_pull.py is explicit
 * that rat "never got the granular HubSpot schema changes (no Task-coverage wiring, no stage-bucket
 * definitions) that coding/cluster1/cluster2 have" and renders those cells blank by deliberate policy,
 * not as a bug. See HAS_GRANULAR_ below.
 *
 * DELIBERATE DEVIATION FROM THE PYTHON REFERENCE'S *STRUCTURE* (not its output): the Python file does
 * THREE separate passes per vertical - one for itd/mtd/wtd+owner counts (counts_for_ids), one for the
 * rep-day earliest-in-week breakdown (rep_day_counts), one for granular stats (_load_granular_stats) -
 * each re-fetching the same deals' full stage history from HubSpot independently. That tripling is fine
 * for a Python script with no execution-time ceiling; Apps Script time-driven triggers are NOT so
 * forgiving (the original porting brief, docs/DAILY_SYNC_APPS_SCRIPT_HANDOFF.md, flags this exact risk:
 * "Apps Script has a 6-minute execution cap per run"). This file fetches each portal's deal list and
 * full dealstage history ONCE and derives all three outputs (core itd/mtd/wtd + owner breakdown + LOI
 * dedup, the rep-day earliest-in-week day buckets, and the same-day-lead/same-day-script timestamps)
 * from that single per-deal history walk in pullVertical_() - same filter logic, same dedup semantics,
 * same resulting numbers as the three Python passes would produce, just without the redundant HTTP
 * round-trips. This is a network-efficiency change, not a logic change - every per-entry resolution
 * rule (REASSIGN translation, the "any source vs CRM_UI-only" filter, the earliest-in-week-per-(deal,
 * label) dedup) is copied verbatim from the matching Python function. Flagged here for anyone diffing
 * this against the Python file expecting a 1:1 procedural mirror - it is not one, by design.
 *
 * SETUP (one-time, by a human with Apps Script editor access):
 *   1. Run bootstrapApiKeys_() once from the editor and approve the permissions dialog (grants
 *      script.external_request/spreadsheets/script.scriptapp, already declared in appsscript.json).
 *   2. Run testDryRun_('cluster1') (or 'cluster2'/'coding'/'engineering') to sanity-check pulled numbers
 *      via Logger.log WITHOUT writing the sheet - cross-check against a fresh `python3 tools/daily_sync_pull.py`
 *      run's numbers for the same vertical before trusting this.
 *   3. Once verified, run runOneVerticalLive_('cluster1') etc. (or runDailySyncNow_() for all four) to do
 *      a real write, then read the sheet back to confirm.
 *   4. Only after a verified-correct live write: run installTrigger() to install the recurring schedule.
 */

var PERF_SHEET_ID = '1F9CGzd5qUL-kFywvGd4l7w6sJpwspoSMxSfIJIvrrXo';

// ---------------------------------------------------------------------------------------------------
// 0. Auth - HubSpot private-app tokens live in Script Properties, never hardcoded in source. Same 3
//    tokens as .env's HUBSPOT_KEY_MAIN/HUBSPOT_KEY_COMPANYOPS/HUBSPOT_KEY_RAT in the repo this script is
//    ported from - get them from the user, never read them out of .env (gitignored, per-machine, not
//    something a cloud script should depend on anyway).
// ---------------------------------------------------------------------------------------------------
function bootstrapApiKeys_() {
  // One-time: paste the 3 real keys here, run this function once from the editor, then DELETE the
  // hardcoded values below and push a cleaned version - keys never sit in visible source after the
  // first run. If this project's Script Properties already have these set (from the earlier, now
  // superseded draft), this is a no-op to re-run with the same values - harmless either way.
  var props = PropertiesService.getScriptProperties();
  props.setProperty('HUBSPOT_KEY_MAIN', 'REPLACE_ME');
  props.setProperty('HUBSPOT_KEY_COMPANYOPS', 'REPLACE_ME');
  props.setProperty('HUBSPOT_KEY_RAT', 'REPLACE_ME');
  Logger.log('keys stored in Script Properties');
}

function hdr_(keyName) {
  var key = PropertiesService.getScriptProperties().getProperty(keyName);
  if (!key) throw new Error('Script Property ' + keyName + ' is not set - run bootstrapApiKeys_() first');
  return { 'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json' };
}

function fetchJson_(url, options) {
  var opts = options || {};
  opts.muteHttpExceptions = true;
  var resp = UrlFetchApp.fetch(url, opts);
  var text = resp.getContentText();
  return text ? JSON.parse(text) : {};
}

// ---------------------------------------------------------------------------------------------------
// 1. Calendar - ported verbatim from daily_sync_pull.py's HOLIDAYS/is_working_day/working_days_between.
//    Only the TARGET denominator uses working-day counts; the HubSpot data pull always scans every
//    calendar day, holidays included (a rep's real holiday work still shows up in the actuals).
// ---------------------------------------------------------------------------------------------------
var HOLIDAYS_ = { '2026-10-02': true }; // add more IST yyyy-MM-dd dates here as they come up - one-line change
var WEEKDAY_OK_ = { Mon: true, Tue: true, Wed: true, Thu: true, Fri: true };

function istPart_(d, fmt) { return Utilities.formatDate(d, 'Asia/Kolkata', fmt); }
function isoDateIst_(d) { return istPart_(d, 'yyyy-MM-dd'); }

function istMidnightUtcDate_(y, m1based, day) {
  // A JS Date representing y-m1based-day 00:00:00 IST, as the correct UTC instant. IST = UTC+5:30, so
  // IST midnight = the previous UTC day's 18:30. Date.UTC() normalizes month/day overflow natively.
  return new Date(Date.UTC(y, m1based - 1, day, 0, 0, 0) - (5 * 60 + 30) * 60 * 1000);
}

function isWorkingDayIst_(d) {
  if (!WEEKDAY_OK_[istPart_(d, 'EEE')]) return false; // Sat/Sun excluded
  return !HOLIDAYS_[isoDateIst_(d)];
}

function workingDaysBetween_(start, end) {
  // Inclusive of both ends, same as Python's working_days_between.
  var n = 0, d = new Date(start.getTime());
  while (d.getTime() <= end.getTime()) {
    if (isWorkingDayIst_(d)) n++;
    d = new Date(d.getTime() + 24 * 60 * 60 * 1000);
  }
  return n;
}

function mondayOfWeek_(d) {
  var y = Number(istPart_(d, 'yyyy')), mo = Number(istPart_(d, 'M')), da = Number(istPart_(d, 'd'));
  var order = { Mon: 0, Tue: 1, Wed: 2, Thu: 3, Fri: 4, Sat: 5, Sun: 6 };
  var offset = order[istPart_(d, 'EEE')];
  var todayMidnightUtc = istMidnightUtcDate_(y, mo, da);
  return new Date(todayMidnightUtc.getTime() - offset * 24 * 60 * 60 * 1000);
}

function firstOfMonthIst_(d) {
  var y = Number(istPart_(d, 'yyyy')), mo = Number(istPart_(d, 'M'));
  return istMidnightUtcDate_(y, mo, 1);
}

function todayMidnightIst_(d) {
  var y = Number(istPart_(d, 'yyyy')), mo = Number(istPart_(d, 'M')), da = Number(istPart_(d, 'd'));
  return istMidnightUtcDate_(y, mo, da);
}

// ---------------------------------------------------------------------------------------------------
// 2. REASSIGN - ported verbatim from daily_sync_pull.py's REASSIGN dict (Coding deals whose history
//    spans an old CompanyOps/RAT pipeline before they moved into Coding).
// ---------------------------------------------------------------------------------------------------
var REASSIGN_ = {
  'cold called assigned': 'Cold Call', 'cold call': 'Cold Call', 'no pickup': 'No Pickup', 'no pickup/callback': 'No Pickup',
  'interested': 'Interested', 'closed/won': 'Closed/Won', 'replied': 'Interested', '1st interest sent': 'Interested',
  '1st interest follow up': 'Interested', 'discovery call': 'GMeet Fixed', 'gmeet1 completed': 'GMeet Fixed',
  'call rescheduled': 'No Pickup', 'callback': 'No Pickup', 'retired: callback +1 day (use no pickup + task instead)': 'No Pickup',
  'sample requested': 'Script Shared', 'sample follow up': 'Script Shared', 'sample received': 'Script Results Received',
  'negotiation': 'Commercial Negotiation', 'contract signed': 'Deal Contract Signed', 'loi signed': 'LOI',
  'dead: cold call / wrong number': 'Dead/ColdCall/WrongNumber', 'dead: cold call / not interested': 'Dead/ColdCall/Not Interested',
  'dead: cold call / wrong fit': 'Dead/ColdCall/WrongFit', 'dead: cold call / no pickup': 'Dead/ColdCall/NoPickup',
  'dead: replied / not interested': 'Dead/ColdCall/Not Interested',
};

function resolveLabel_(idToLabel, oldIdToLabel, rawValue) {
  var lbl = idToLabel[rawValue];
  if (lbl) return lbl;
  if (oldIdToLabel) {
    var rawOld = oldIdToLabel[rawValue];
    if (rawOld) return REASSIGN_[rawOld.trim().toLowerCase()] || null;
  }
  return null;
}

function isAnySourceLabel_(canonLower) {
  // Entry rungs (the funnel's first stage) count any source; every other stage counts human (CRM_UI)
  // moves only - ported verbatim from counts_for_ids()'s any_src check.
  return canonLower === 'cold call' || canonLower === 'cold called assigned' ||
         canonLower === 'cold lead' || canonLower === 'cold lead assigned';
}

// ---------------------------------------------------------------------------------------------------
// 3. HubSpot fetch helpers - 50/batch for history, 100/batch for task associations (HubSpot's own caps,
//    confirmed live in the Python build - see daily_sync_pull.py's _batch_history docstring).
// ---------------------------------------------------------------------------------------------------
function stageLabels_(h, pipelineId) {
  var r = fetchJson_('https://api.hubapi.com/crm/v3/pipelines/deals/' + pipelineId, { headers: h, method: 'get' });
  var out = {};
  var stages = r.stages || [];
  for (var i = 0; i < stages.length; i++) out[stages[i].id] = stages[i].label;
  return out;
}

function searchAllDeals_(h, pipelineId, extraProps) {
  // Every deal ever in this pipeline, no date floor - needed for ITD. Returns [{id, properties}, ...].
  var out = [], after = null;
  var props = ['dealname'].concat(extraProps || []);
  while (true) {
    var body = { filterGroups: [{ filters: [{ propertyName: 'pipeline', operator: 'EQ', value: pipelineId }] }],
                 properties: props, limit: 100 };
    if (after) body.after = after;
    var r = fetchJson_('https://api.hubapi.com/crm/v3/objects/deals/search', {
      method: 'post', headers: h, payload: JSON.stringify(body) });
    var results = r.results || [];
    for (var i = 0; i < results.length; i++) out.push(results[i]);
    if (!r.paging) break;
    after = r.paging.next.after;
  }
  return out;
}

function batchHistory_(h, ids) {
  // {dealId: [{value, timestamp, sourceType}, ...]} - 50 ids per batch/read call.
  var out = {};
  for (var i = 0; i < ids.length; i += 50) {
    var chunk = ids.slice(i, i + 50);
    var inputs = [];
    for (var j = 0; j < chunk.length; j++) inputs.push({ id: chunk[j] });
    var r = fetchJson_('https://api.hubapi.com/crm/v3/objects/deals/batch/read', {
      method: 'post', headers: h,
      payload: JSON.stringify({ inputs: inputs, propertiesWithHistory: ['dealstage'] }) });
    var results = r.results || [];
    for (var k = 0; k < results.length; k++) {
      var res = results[k];
      out[res.id] = (res.propertiesWithHistory && res.propertiesWithHistory.dealstage) || [];
    }
  }
  return out;
}

function batchTaskAssociations_(h, ids) {
  // {dealId: [taskId, ...]} - reverse deals->tasks association, 100/batch (v4 associations cap).
  var out = {};
  for (var i = 0; i < ids.length; i += 100) {
    var chunk = ids.slice(i, i + 100);
    var inputs = [];
    for (var j = 0; j < chunk.length; j++) inputs.push({ id: chunk[j] });
    var r = fetchJson_('https://api.hubapi.com/crm/v4/associations/deals/tasks/batch/read', {
      method: 'post', headers: h, payload: JSON.stringify({ inputs: inputs }) });
    var results = r.results || [];
    for (var k = 0; k < results.length; k++) {
      var res = results[k];
      var taskIds = [];
      var to = res.to || [];
      for (var t = 0; t < to.length; t++) taskIds.push(to[t].toObjectId);
      out[res.from.id] = taskIds;
    }
  }
  return out;
}

function emailTaskIds_(h) {
  // NOTE (ported deliberately, not fixed): same as daily_sync_pull.py's _load_granular_stats, this reads
  // only the FIRST 100 EMAIL-type tasks in the portal (limit:100, no pagination) - a real limitation
  // inherited from the verified-correct Python reference, not something to silently "fix" here, since
  // doing so would make this script's numbers diverge from the Python cross-check baseline.
  var r = fetchJson_('https://api.hubapi.com/crm/v3/objects/tasks/search', {
    method: 'post', headers: h,
    payload: JSON.stringify({
      filterGroups: [{ filters: [{ propertyName: 'hs_task_type', operator: 'EQ', value: 'EMAIL' }] }],
      properties: ['hs_task_type'], limit: 100,
    }) });
  var out = {};
  var results = r.results || [];
  for (var i = 0; i < results.length; i++) out[results[i].id] = true;
  return out;
}

function round1_(x) { return Math.round(x * 10) / 10; }

function sameIstDay_(aMs, bMs) {
  return isoDateIst_(new Date(aMs)) === isoDateIst_(new Date(bMs));
}

function addCounts_(target, source) {
  for (var k in source) target[k] = (target[k] || 0) + source[k];
}
function addOwnerCounts_(target, source) {
  // target/source: {oid: {canon: count}}
  for (var oid in source) {
    if (!target[oid]) target[oid] = {};
    addCounts_(target[oid], source[oid]);
  }
}
function addOwnerDay_(target, source) {
  // target/source: {oid: {dayIso: {canon: count}}}
  for (var oid in source) {
    if (!target[oid]) target[oid] = {};
    for (var dayIso in source[oid]) {
      if (!target[oid][dayIso]) target[oid][dayIso] = {};
      addCounts_(target[oid][dayIso], source[oid][dayIso]);
    }
  }
}

// ---------------------------------------------------------------------------------------------------
// 4. Rep maps - owner_id -> display name. Ported verbatim from daily_sync_pull.py's REP_MAPS.
// ---------------------------------------------------------------------------------------------------
// Arrays of [ownerId, name], NOT a plain object - JS objects with integer-looking string keys (e.g.
// '96574824') silently reorder enumeration to ASCENDING NUMERIC key order regardless of insertion order
// (`for...in` on such keys ignores the order they were written in), which would scramble Coding's 5-rep
// left-to-right column order (Lamiya/Yuktha/Shagufta/Vaishnavi/Harsha, matching REP_MAPS in
// daily_sync_pull.py) into an unrelated numeric-id order. An array preserves the exact order written here.
var REP_MAPS_ = {
  cluster1: [['168609107', 'Manit'], ['169098409', 'Pratyush']],
  cluster2: [['168015618', 'Tanisha'], ['168015679', 'Amisha']],
  coding: [['96574824', 'Lamiya'], ['96573782', 'Yuktha'], ['168572330', 'Shagufta'],
           ['168341981', 'Vaishnavi'], ['98906502', 'Harsha']],
  // Engineering Datasets ("rat" vertical in the Python reference)'s own POCs, in the RAT account's own
  // "Rapid Action Team" pipeline (2575252183) - NOT the "Coding" pipeline (2608440047) in that same RAT
  // account, which is Harsha's Coding-overflow queue, a different vertical entirely (see portalsFor_).
  engineering: [['168541136', 'Prerna'], ['168829625', 'Rohan'], ['169456665', 'Hemanth']],
};

// ---------------------------------------------------------------------------------------------------
// 5. Row formulas / METRICS / REP_METRIC_LABELS / LOI_DEDUP_LABELS - ported verbatim from
//    daily_sync_pull.py (row_formulas(), METRICS, REP_METRIC_LABELS_C1C2/CODING, LOI_DEDUP_LABELS).
//    REP_METRIC_LABELS MUST mirror rowFormulas_()'s own sumStages_ argument lists exactly (documented
//    duplication in the Python file too, not a bug - see that file's comment above REP_METRIC_LABELS_C1C2).
// ---------------------------------------------------------------------------------------------------
var METRICS_ = ['Leads assigned', 'Calls attempted', 'Calls picked', 'Interested', 'VC setup', 'VC completed',
                'Script/1-pager shared', 'Script results/1-pager filled', 'Contract/LOI signed'];

function sumStages_(counts, labels) {
  var total = 0;
  for (var i = 0; i < labels.length; i++) total += (counts[labels[i]] || 0);
  return total;
}

function rowFormulas_(vertical, c) {
  if (vertical === 'cluster1' || vertical === 'cluster2') {
    return {
      'Leads assigned': sumStages_(c, ['Cold lead assigned']),
      'Calls attempted': sumStages_(c, ['No Pickup - Attempt 1', 'No Pickup - Attempt 2', 'No Pickup - Attempt 3',
                                         'Dead: Cold Call / Wrong Number', 'Dead: Cold Call / Not Interested', 'Replied']),
      'Calls picked': sumStages_(c, ['Replied', 'Dead: Cold Call / Not Interested']),
      'Interested': sumStages_(c, ['1st interest sent']),
      'VC setup': sumStages_(c, ['Discovery call']),
      'VC completed': sumStages_(c, ['Dead: Discovery Call / Rejected by LH2', 'Dead: Discovery Call / Not Interested', 'One pager requested']),
      'Script/1-pager shared': sumStages_(c, ['One pager requested']),
      'Script results/1-pager filled': sumStages_(c, ['One pager received']),
      'Contract/LOI signed': (c['__loi_dedup__'] !== undefined) ? c['__loi_dedup__'] : sumStages_(c, ['LOI signed']),
    };
  }
  if (vertical === 'coding') {
    return {
      'Leads assigned': sumStages_(c, ['Cold Call']),
      'Calls attempted': sumStages_(c, ['No Pickup - Attempt 1', 'No Pickup - Attempt 2', 'No Pickup - Attempt 3',
                                         'Dead/ColdCall/NoPickup', 'Dead/ColdCall/WrongNumber', 'Dead/ColdCall/Not Interested', 'Interested']),
      'Calls picked': sumStages_(c, ['Interested', 'Dead/ColdCall/Not Interested']),
      'Interested': sumStages_(c, ['Interested']),
      'VC setup': sumStages_(c, ['GMeet Fixed']),
      'VC completed': sumStages_(c, ['Script Shared', 'Dead/GMeet/Privacy Concerns', 'Dead/GMeet/wrong fit']),
      'Script/1-pager shared': sumStages_(c, ['Script Shared']),
      'Script results/1-pager filled': sumStages_(c, ['Script Results Received']),
      'Contract/LOI signed': (c['__loi_dedup__'] !== undefined) ? c['__loi_dedup__'] : sumStages_(c, ['LOI', 'Deal Contract Signed', 'Closed/Won']),
    };
  }
  if (vertical === 'engineering') {
    // Ported verbatim from daily_sync_pull.py's row_formulas() "rat" branch.
    return {
      'Leads assigned': sumStages_(c, ['Cold Called Assigned']),
      'Calls attempted': sumStages_(c, ['No Pickup', 'Callback', 'Dead: ColdCall/WrongNumber', 'Dead: ColdCall/NotInterested', 'Replied']),
      'Calls picked': sumStages_(c, ['Replied', 'Dead: ColdCall/NotInterested']),
      'Interested': sumStages_(c, ['1st Interest Sent']),
      'VC setup': sumStages_(c, ['Discovery Call']),
      'VC completed': sumStages_(c, ['Dead: DiscoveryCall/RejectedByLH2', 'Dead: DiscoveryCall/NotInterested', 'Sample Requested']),
      'Script/1-pager shared': sumStages_(c, ['Sample Requested']),
      'Script results/1-pager filled': sumStages_(c, ['Sample Received']),
      'Contract/LOI signed': (c['__loi_dedup__'] !== undefined) ? c['__loi_dedup__'] : sumStages_(c, ['Contract Signed']),
    };
  }
  throw new Error('unknown vertical ' + vertical);
}

var REP_METRIC_LABELS_C1C2_ = {
  'Leads assigned': ['Cold lead assigned'],
  'Calls attempted': ['No Pickup - Attempt 1', 'No Pickup - Attempt 2', 'No Pickup - Attempt 3',
                       'Dead: Cold Call / Wrong Number', 'Dead: Cold Call / Not Interested', 'Replied'],
  'Calls picked': ['Replied', 'Dead: Cold Call / Not Interested'],
  'Interested': ['1st interest sent'],
  'VC setup': ['Discovery call'],
  'VC completed': ['Dead: Discovery Call / Rejected by LH2', 'Dead: Discovery Call / Not Interested', 'One pager requested'],
  'Script/1-pager shared': ['One pager requested'],
  'Script results/1-pager filled': ['One pager received'],
  'Contract/LOI signed': ['LOI signed'],
};
var REP_METRIC_LABELS_CODING_ = {
  'Leads assigned': ['Cold Call'],
  'Calls attempted': ['No Pickup - Attempt 1', 'No Pickup - Attempt 2', 'No Pickup - Attempt 3',
                       'Dead/ColdCall/NoPickup', 'Dead/ColdCall/WrongNumber', 'Dead/ColdCall/Not Interested', 'Interested'],
  'Calls picked': ['Interested', 'Dead/ColdCall/Not Interested'],
  'Interested': ['Interested'],
  'VC setup': ['GMeet Fixed'],
  'VC completed': ['Script Shared', 'Dead/GMeet/Privacy Concerns', 'Dead/GMeet/wrong fit'],
  'Script/1-pager shared': ['Script Shared'],
  'Script results/1-pager filled': ['Script Results Received'],
  // Day-column caveat (ported from Python, documented there too): plain stage-visit sum, NOT the
  // union-dedup'd "__loi_dedup__" the WTD/MTD/ITD actual columns use - a deal passing through both LOI
  // and Deal Contract Signed in the same week double-counts across its two days here. Flagged, not fixed.
  'Contract/LOI signed': ['LOI', 'Deal Contract Signed', 'Closed/Won'],
};
var REP_METRIC_LABELS_ENGINEERING_ = {
  'Leads assigned': ['Cold Called Assigned'],
  'Calls attempted': ['No Pickup', 'Callback', 'Dead: ColdCall/WrongNumber', 'Dead: ColdCall/NotInterested', 'Replied'],
  'Calls picked': ['Replied', 'Dead: ColdCall/NotInterested'],
  'Interested': ['1st Interest Sent'],
  'VC setup': ['Discovery Call'],
  'VC completed': ['Dead: DiscoveryCall/RejectedByLH2', 'Dead: DiscoveryCall/NotInterested', 'Sample Requested'],
  'Script/1-pager shared': ['Sample Requested'],
  'Script results/1-pager filled': ['Sample Received'],
  'Contract/LOI signed': ['Contract Signed'],
};
var REP_METRIC_LABELS_ = { cluster1: REP_METRIC_LABELS_C1C2_, cluster2: REP_METRIC_LABELS_C1C2_, coding: REP_METRIC_LABELS_CODING_,
                           engineering: REP_METRIC_LABELS_ENGINEERING_ };

var LOI_DEDUP_LABELS_ = {
  coding: { 'LOI': true, 'Deal Contract Signed': true, 'Closed/Won': true },
  cluster1: { 'LOI signed': true },
  cluster2: { 'LOI signed': true },
  engineering: { 'Contract Signed': true },
};

// ---------------------------------------------------------------------------------------------------
// 6. BUCKET_STAGES - ported verbatim from daily_sync_pull.py's BUCKET_STAGES (the dict near the bottom
//    of that file, used by _load_granular_stats). cluster1/cluster2/coding/engineering entries needed
//    here (coops_global is out of scope - see file header SCOPE).
// ---------------------------------------------------------------------------------------------------
var BUCKET_STAGES_ = {
  cluster1: {
    picked: ['Replied', 'Dead: Cold Call / Not Interested'], interested: ['1st interest sent'],
    vc_noshow: ['Dead: Discovery Call / No Show'],
    leads_assigned: ['Cold lead assigned'],
    calls_attempted: ['No Pickup - Attempt 1', 'No Pickup - Attempt 2', 'No Pickup - Attempt 3', 'Dead: Cold Call / Wrong Number', 'Dead: Cold Call / Not Interested', 'Replied'],
    vc_setup: ['Discovery call'], script_shared: ['One pager requested'],
  },
  cluster2: {
    picked: ['Replied', 'Dead: Cold Call / Not Interested'], interested: ['1st interest sent'],
    vc_noshow: ['Dead: Discovery Call / No Show'],
    leads_assigned: ['Cold lead assigned'],
    calls_attempted: ['No Pickup - Attempt 1', 'No Pickup - Attempt 2', 'No Pickup - Attempt 3', 'Dead: Cold Call / Wrong Number', 'Dead: Cold Call / Not Interested', 'Replied'],
    vc_setup: ['Discovery call'], script_shared: ['One pager requested'],
  },
  coding: {
    picked: ['Interested', 'Dead/ColdCall/Not Interested'], interested: ['Interested'],
    vc_noshow: ['Dead/GMeet/NoShow'],
    leads_assigned: ['Cold Call'],
    calls_attempted: ['No Pickup - Attempt 1', 'No Pickup - Attempt 2', 'No Pickup - Attempt 3', 'Dead/ColdCall/NoPickup', 'Dead/ColdCall/WrongNumber', 'Dead/ColdCall/Not Interested', 'Interested'],
    vc_setup: ['GMeet Fixed'], script_shared: ['Script Shared'],
  },
  // Ported verbatim from daily_sync_pull.py's BUCKET_STAGES["rat"] - note NO vc_setup/script_shared keys
  // (matches the Python dict exactly; harmless here too since every read of those two keys elsewhere in
  // this file is already `buckets.vc_setup && ...` / `buckets.script_shared && ...` guarded). These
  // picked/interested/vc_noshow entries are not actually exercised for 'engineering' at runtime - see
  // HAS_GRANULAR_ below - kept only for shape parity with the Python reference.
  engineering: {
    picked: ['Replied', 'Dead: ColdCall/NotInterested'], interested: ['1st Interest Sent'], vc_noshow: null,
    leads_assigned: ['Cold Called Assigned'],
    calls_attempted: ['No Pickup', 'Callback', 'Dead: ColdCall/WrongNumber', 'Dead: ColdCall/NotInterested', 'Replied'],
  },
};

// ---------------------------------------------------------------------------------------------------
// 6b. Which verticals get the Task-association/email-coverage granular stats (Same day lead coverage,
//     Email/WhatsApp touch %, Follow-up coverage x3, Same day script coverage). Ported verbatim from
//     daily_sync_pull.py's documented policy: "coops_global/rat never got the granular HubSpot schema
//     changes (no Task-coverage wiring, no stage-bucket definitions) that coding/cluster1/cluster2 have
//     - those two verticals now render truly BLANK ('') for every granular cell instead of a literal
//     'n/a' string, since there's nothing partially-computed to report". engineering (rat) renders
//     blank, not computed - see pullVertical_ (skips the Task-fetch entirely, saving HubSpot calls too)
//     and buildFullRows_ (renders '' instead of a percentage/count for the affected rows).
// ---------------------------------------------------------------------------------------------------
var HAS_GRANULAR_ = { cluster1: true, cluster2: true, coding: true, engineering: false };

// ---------------------------------------------------------------------------------------------------
// 7. Targets - ported verbatim from daily_sync_pull.py's DAILY_LEADS/OLD_WEEKLY/FACTOR/target().
// ---------------------------------------------------------------------------------------------------
var DAILY_LEADS_ = { coding: 500, cluster1: 200, cluster2: 200, engineering: 200 };
var OLD_WEEKLY_ = {
  coding:   { 'Leads assigned': 1250, 'Calls attempted': 1250, 'Calls picked': 250, 'Interested': null, 'VC setup': 50, 'VC completed': null, 'Script/1-pager shared': 40, 'Script results/1-pager filled': 24, 'Contract/LOI signed': 5 },
  cluster1: { 'Leads assigned': 1000, 'Calls attempted': 1000, 'Calls picked': 150, 'Interested': null, 'VC setup': 15, 'VC completed': null, 'Script/1-pager shared': 12, 'Script results/1-pager filled': 6, 'Contract/LOI signed': 3 },
  cluster2: { 'Leads assigned': 1000, 'Calls attempted': 1000, 'Calls picked': 150, 'Interested': null, 'VC setup': 15, 'VC completed': null, 'Script/1-pager shared': 12, 'Script results/1-pager filled': 6, 'Contract/LOI signed': 3 },
  // = Targets tab's '3.4 Specialty Datasets' column (owner: Kartik) - RAT has no Targets-tab column of
  // its own, so it borrows this one, same as daily_sync_pull.py's OLD_WEEKLY["rat"] comment explains.
  engineering: { 'Leads assigned': 1000, 'Calls attempted': 1000, 'Calls picked': 200, 'Interested': null, 'VC setup': 40, 'VC completed': null, 'Script/1-pager shared': 30, 'Script results/1-pager filled': 21, 'Contract/LOI signed': 4 },
};
function factorFor_(vertical) { return (DAILY_LEADS_[vertical] * 5) / OLD_WEEKLY_[vertical]['Leads assigned']; }

function targetFor_(vertical, metric, wtdDays, mtdDays) {
  var old = OLD_WEEKLY_[vertical][metric];
  if (old === null) return [null, null];
  var dailyEquiv = (old * factorFor_(vertical)) / 5;
  return [Math.ceil(dailyEquiv * wtdDays), Math.ceil(dailyEquiv * mtdDays)];
}

function pctStr_(a, t) {
  // Returns a NUMBER (rounded to 1dp) or the string '—' - matches Python's pct()'s "N/A" case,
  // rendered here as the same em-dash used for missing-target cells elsewhere in this file.
  if (!t) return '—';
  return Math.round((100 * a / t) * 10) / 10;
}

// ---------------------------------------------------------------------------------------------------
// 8. Portal config per vertical.
// ---------------------------------------------------------------------------------------------------
function portalsFor_(vertical) {
  if (vertical === 'cluster1') return [{ key: 'HUBSPOT_KEY_COMPANYOPS', pipeline: 'default', oldPipeline: null }];
  if (vertical === 'cluster2') return [{ key: 'HUBSPOT_KEY_COMPANYOPS', pipeline: '2464812771', oldPipeline: null }];
  if (vertical === 'coding') return [
    { key: 'HUBSPOT_KEY_MAIN', pipeline: 'default', oldPipeline: null },
    { key: 'HUBSPOT_KEY_COMPANYOPS', pipeline: '2608440044', oldPipeline: '2464812771' },
    { key: 'HUBSPOT_KEY_RAT', pipeline: '2608440047', oldPipeline: '2575252183' },
  ];
  // Engineering Datasets: the RAT account's OWN "Rapid Action Team" pipeline (2575252183) - NOT the
  // "2608440047" pipeline above, which is the same RAT account's Coding-overflow queue (Harsha), a
  // different vertical. No oldPipeline/migration - REASSIGN_ is Coding-specific only.
  if (vertical === 'engineering') return [{ key: 'HUBSPOT_KEY_RAT', pipeline: '2575252183', oldPipeline: null }];
  throw new Error('unknown vertical ' + vertical);
}

// ---------------------------------------------------------------------------------------------------
// 9. The core pull - ONE pass per portal over every deal's full dealstage history, deriving:
//      (a) itd/mtd/wtd canonical-label distinct-deal counts, team-level AND per-owner, with the
//          "__loi_dedup__" union-dedup bucket (see LOI_DEDUP_LABELS_) riding the same per-deal dedup;
//      (b) the earliest-in-week-per-(owner, canonical label) day bucket, for the rep day-columns
//          (daily_sync_pull.py's rep_day_counts - "a deal's contribution to one canonical label is
//          attributed to the EARLIEST in-week qualifying timestamp for that (deal, label) pair");
//      (c) the granular same-day-lead / same-day-script-shared timestamps and the current-dealstage
//          bucket membership used for Task/email coverage (daily_sync_pull.py's _load_granular_stats).
//    See the file header for why this is one merged pass instead of Python's three separate ones.
// ---------------------------------------------------------------------------------------------------
function pullVertical_(vertical, weekStart, monthStart, today) {
  var portals = portalsFor_(vertical);
  var buckets = BUCKET_STAGES_[vertical];
  var loiDedupSet = LOI_DEDUP_LABELS_[vertical] || {};
  var hasGranular = HAS_GRANULAR_[vertical]; // false for 'engineering' - see HAS_GRANULAR_'s comment
  var weekStartIso = isoDateIst_(weekStart), todayIso = isoDateIst_(today);

  var teamItd = {}, teamMtd = {}, teamWtd = {};
  var ownerItd = {}, ownerMtd = {}, ownerWtd = {};
  var ownerDay = {};
  var gPickedH = 0, gPickedN = 0, gInterestedH = 0, gInterestedN = 0, gNoshowH = 0, gNoshowN = 0;
  var gNonpickedH = 0, gNonpickedN = 0, gAllH = 0, gAllN = 0;
  var gSameDayHit = 0, gSameDayTotal = 0, gSameDayScriptHit = 0, gSameDayScriptTotal = 0;
  var gWeightedAtt = 0, gTotalN = 0, gAnyNoshowStage = false;

  for (var p = 0; p < portals.length; p++) {
    var portal = portals[p];
    var h = hdr_(portal.key);
    var idToLabel = stageLabels_(h, portal.pipeline);
    var oldIdToLabel = portal.oldPipeline ? stageLabels_(h, portal.oldPipeline) : null;
    var deals = searchAllDeals_(h, portal.pipeline, ['dealstage', 'call_attempts_count', 'hubspot_owner_id']);
    var ids = [];
    for (var i = 0; i < deals.length; i++) ids.push(deals[i].id);
    var historyByDeal = batchHistory_(h, ids);

    // --- bucket matching off CURRENT dealstage, for Task/email coverage below --- skipped entirely for
    // verticals with hasGranular=false (engineering) - saves the Task-association/email-search HubSpot
    // calls too, matching daily_sync_pull.py's "rat never got the Task-coverage wiring" policy.
    if (hasGranular) {
      var pickedIds = [], interestedIds = [], noshowIds = [], nonpickedIds = [];
      for (var di = 0; di < deals.length; di++) {
        var props = deals[di].properties || {};
        var curLabel = idToLabel[props.dealstage];
        if (buckets.picked.indexOf(curLabel) !== -1) pickedIds.push(deals[di].id); else nonpickedIds.push(deals[di].id);
        if (buckets.interested.indexOf(curLabel) !== -1) interestedIds.push(deals[di].id);
        if (buckets.vc_noshow && buckets.vc_noshow.indexOf(curLabel) !== -1) { noshowIds.push(deals[di].id); gAnyNoshowStage = true; }
        gWeightedAtt += Number(props.call_attempts_count || 0);
        gTotalN++;
      }
      var taskIdsByDeal = batchTaskAssociations_(h, ids);
      var emailIds = emailTaskIds_(h);
      var hits_ = function (arr) { var c = 0; for (var x = 0; x < arr.length; x++) if ((taskIdsByDeal[arr[x]] || []).length) c++; return c; };
      var emailHits_ = function (arr) {
        var c = 0;
        for (var x = 0; x < arr.length; x++) {
          var tids = taskIdsByDeal[arr[x]] || [];
          for (var y = 0; y < tids.length; y++) { if (emailIds[tids[y]]) { c++; break; } }
        }
        return c;
      };
      gPickedH += hits_(pickedIds); gPickedN += pickedIds.length;
      gInterestedH += hits_(interestedIds); gInterestedN += interestedIds.length;
      gNoshowH += hits_(noshowIds); gNoshowN += noshowIds.length;
      gAllH += emailHits_(ids); gAllN += ids.length;
      gNonpickedH += emailHits_(nonpickedIds); gNonpickedN += nonpickedIds.length;
    }

    // --- per-deal full-history walk: itd/mtd/wtd (+owner, +LOI-dedup), rep-day buckets, same-day stats ---
    var portalItd = {}, portalMtd = {}, portalWtd = {};
    var portalOwnerItd = {}, portalOwnerMtd = {}, portalOwnerWtd = {}, portalOwnerDay = {};
    for (var k = 0; k < ids.length; k++) {
      var did = ids[k];
      var oid = (deals[k].properties || {}).hubspot_owner_id;
      var hist = historyByDeal[did] || [];
      var seenItd = {}, seenMtd = {}, seenWtd = {};
      var earliestInWeekByCanon = {};
      var assignedMs = null, attemptMs = null, vcSetupMs = null, scriptSharedMs = null;

      for (var e = 0; e < hist.length; e++) {
        var hent = hist[e];
        var canon = resolveLabel_(idToLabel, oldIdToLabel, hent.value);
        if (!canon) continue;
        var anySrc = isAnySourceLabel_(canon.toLowerCase());
        if (!anySrc && hent.sourceType !== 'CRM_UI') continue;
        var tsMs = new Date(hent.timestamp).getTime();

        // ITD - unconditional, deduped per deal per canon (+ loi-dedup bucket).
        if (!seenItd[canon]) {
          seenItd[canon] = true;
          portalItd[canon] = (portalItd[canon] || 0) + 1;
          if (oid) { if (!portalOwnerItd[oid]) portalOwnerItd[oid] = {}; portalOwnerItd[oid][canon] = (portalOwnerItd[oid][canon] || 0) + 1; }
          if (loiDedupSet[canon] && !seenItd['__loi_dedup__']) {
            seenItd['__loi_dedup__'] = true;
            portalItd['__loi_dedup__'] = (portalItd['__loi_dedup__'] || 0) + 1;
            if (oid) portalOwnerItd[oid]['__loi_dedup__'] = (portalOwnerItd[oid]['__loi_dedup__'] || 0) + 1;
          }
        }
        // MTD
        if (tsMs >= monthStart.getTime() && !seenMtd[canon]) {
          seenMtd[canon] = true;
          portalMtd[canon] = (portalMtd[canon] || 0) + 1;
          if (oid) { if (!portalOwnerMtd[oid]) portalOwnerMtd[oid] = {}; portalOwnerMtd[oid][canon] = (portalOwnerMtd[oid][canon] || 0) + 1; }
          if (loiDedupSet[canon] && !seenMtd['__loi_dedup__']) {
            seenMtd['__loi_dedup__'] = true;
            portalMtd['__loi_dedup__'] = (portalMtd['__loi_dedup__'] || 0) + 1;
            if (oid) portalOwnerMtd[oid]['__loi_dedup__'] = (portalOwnerMtd[oid]['__loi_dedup__'] || 0) + 1;
          }
        }
        // WTD + earliest-in-week (for the day columns)
        if (tsMs >= weekStart.getTime()) {
          if (!seenWtd[canon]) {
            seenWtd[canon] = true;
            portalWtd[canon] = (portalWtd[canon] || 0) + 1;
            if (oid) { if (!portalOwnerWtd[oid]) portalOwnerWtd[oid] = {}; portalOwnerWtd[oid][canon] = (portalOwnerWtd[oid][canon] || 0) + 1; }
            if (loiDedupSet[canon] && !seenWtd['__loi_dedup__']) {
              seenWtd['__loi_dedup__'] = true;
              portalWtd['__loi_dedup__'] = (portalWtd['__loi_dedup__'] || 0) + 1;
              if (oid) portalOwnerWtd[oid]['__loi_dedup__'] = (portalOwnerWtd[oid]['__loi_dedup__'] || 0) + 1;
            }
          }
          if (earliestInWeekByCanon[canon] === undefined || tsMs < earliestInWeekByCanon[canon]) earliestInWeekByCanon[canon] = tsMs;
        }

        // Same-day-lead / same-day-script-shared: earliest EVER (no week/month bound), matching
        // _load_granular_stats' assigned_ts/attempt_ts/vc_setup_ts/script_shared_ts exactly. Skipped for
        // hasGranular=false verticals (engineering) - Python renders these blank for rat too (g=None).
        if (hasGranular) {
          if (buckets.leads_assigned.indexOf(canon) !== -1 && (assignedMs === null || tsMs < assignedMs)) assignedMs = tsMs;
          if (buckets.calls_attempted.indexOf(canon) !== -1 && (attemptMs === null || tsMs < attemptMs)) attemptMs = tsMs;
          if (buckets.vc_setup && buckets.vc_setup.indexOf(canon) !== -1 && (vcSetupMs === null || tsMs < vcSetupMs)) vcSetupMs = tsMs;
          if (buckets.script_shared && buckets.script_shared.indexOf(canon) !== -1 && (scriptSharedMs === null || tsMs < scriptSharedMs)) scriptSharedMs = tsMs;
        }
      }

      if (hasGranular) {
        if (assignedMs !== null) {
          gSameDayTotal++;
          if (attemptMs !== null && sameIstDay_(assignedMs, attemptMs)) gSameDayHit++;
        }
        if (vcSetupMs !== null) {
          gSameDayScriptTotal++;
          if (scriptSharedMs !== null && sameIstDay_(vcSetupMs, scriptSharedMs)) gSameDayScriptHit++;
        }
      }
      if (oid) {
        for (var canon2 in earliestInWeekByCanon) {
          var dIso = isoDateIst_(new Date(earliestInWeekByCanon[canon2]));
          if (dIso < weekStartIso || dIso > todayIso) continue; // guards clock-skew/future timestamps
          if (!portalOwnerDay[oid]) portalOwnerDay[oid] = {};
          if (!portalOwnerDay[oid][dIso]) portalOwnerDay[oid][dIso] = {};
          portalOwnerDay[oid][dIso][canon2] = (portalOwnerDay[oid][dIso][canon2] || 0) + 1;
        }
      }
    }

    // Merge this portal's results into the vertical-level totals (object-key-wise addition - same
    // operation daily_sync_pull.py's pull_all() does across Coding's 3 portals via collections.Counter +).
    addCounts_(teamItd, portalItd); addCounts_(teamMtd, portalMtd); addCounts_(teamWtd, portalWtd);
    addOwnerCounts_(ownerItd, portalOwnerItd); addOwnerCounts_(ownerMtd, portalOwnerMtd); addOwnerCounts_(ownerWtd, portalOwnerWtd);
    addOwnerDay_(ownerDay, portalOwnerDay);
  }

  function pctOrNone_(hv, nv) { return nv ? round1_(100 * hv / nv) : null; }
  // null for hasGranular=false verticals (engineering) - matches daily_sync_pull.py's granular_stats.get(v)
  // returning None for rat, which buildFullRows_'s Python equivalent treats as "render blank", not "n/a".
  var granular = hasGranular ? {
    avgAttempts: gTotalN ? Math.round((gWeightedAtt / gTotalN) * 100) / 100 : 0,
    pctTouch: gAllN ? round1_(100 * gAllH / gAllN) : 0,
    pctNonpickedTouch: gNonpickedN ? round1_(100 * gNonpickedH / gNonpickedN) : 0,
    pctFollowupPicked: pctOrNone_(gPickedH, gPickedN),
    pctFollowupInterested: pctOrNone_(gInterestedH, gInterestedN),
    pctFollowupNoshow: (buckets.vc_noshow || gAnyNoshowStage) ? pctOrNone_(gNoshowH, gNoshowN) : 'no_noshow_stage',
    pctSameDayLead: gSameDayTotal ? round1_(100 * gSameDayHit / gSameDayTotal) : null,
    pctSameDayScript: gSameDayScriptTotal ? round1_(100 * gSameDayScriptHit / gSameDayScriptTotal) : null,
  } : null;

  return { itd: teamItd, mtd: teamMtd, wtd: teamWtd, ownerItd: ownerItd, ownerMtd: ownerMtd, ownerWtd: ownerWtd,
           ownerDay: ownerDay, granular: granular };
}

// ---------------------------------------------------------------------------------------------------
// 10. Build the 18-row main block - ported verbatim (row order/labels) from build_full_rows() in
//     daily_sync_pull.py. flatRow_/coreRow_ mirror Python's flat_row()/core_row() closures.
// ---------------------------------------------------------------------------------------------------
function flatRow_(label, value) {
  return [label, value, '—', '—', '', value, '—', '—', '', value];
}

function buildFullRows_(vertical, pulled, wtdDays, mtdDays) {
  var waAll = rowFormulas_(vertical, pulled.wtd), maAll = rowFormulas_(vertical, pulled.mtd), iaAll = rowFormulas_(vertical, pulled.itd);
  var g = pulled.granular;

  function coreRow(label, m) {
    var t = targetFor_(vertical, m, wtdDays, mtdDays);
    var wa = waAll[m], ma = maAll[m], ia = iaAll[m];
    return [label, wa, t[0] === null ? '—' : t[0], t[0] ? pctStr_(wa, t[0]) : '—', '',
                   ma, t[1] === null ? '—' : t[1], t[1] ? pctStr_(ma, t[1]) : '—', '', ia];
  }

  var nCallers = (REP_MAPS_[vertical] || []).length;
  function fuVal(key) {
    if (!g) return ''; // no granular schema for this vertical (engineering) - blank, not '0 in bucket'
    var v2 = g[key];
    if (v2 === 'no_noshow_stage') return 'n/a (no no-show stage defined)';
    if (v2 === null || v2 === undefined) return '0 in bucket';
    return v2 + '%';
  }
  // CHANGED (ported from daily_sync_pull.py's documented policy): a vertical with no granular HubSpot
  // schema wired up (engineering/rat - see HAS_GRANULAR_) renders every granular cell truly BLANK ('')
  // instead of a computed value - "there's nothing partially-computed to report, just an intentionally
  // unsupported vertical for this round of work."
  var sameDayLead = !g ? '' : (g.pctSameDayLead === null) ? '0 in bucket' : g.pctSameDayLead + '%';
  var sameDayScript = !g ? '' : (g.pctSameDayScript === null || g.pctSameDayScript === undefined) ? '0 in bucket' : g.pctSameDayScript + '%';
  var avgAttempts = g ? g.avgAttempts : '';
  var pctNonpickedTouch = g ? (g.pctNonpickedTouch + '%') : '';
  var pctTouch = g ? (g.pctTouch + '%') : '';

  return [
    flatRow_('Callers', nCallers),
    coreRow('Leads assigned', 'Leads assigned'),
    flatRow_('Same day Lead coverage (% of same day leads where call is attempted)', sameDayLead),
    flatRow_('Call attempts per lead', avgAttempts),
    coreRow('Calls attempted', 'Calls attempted'),
    flatRow_('Email/ Whatsapp coverage% on non-picked call leads', pctNonpickedTouch),
    flatRow_('Email/ whatsapp coverage% on all leads', pctTouch),
    coreRow('Calls picked', 'Calls picked'),
    flatRow_('Follow-up coverage on follow-up marked call-picked leads', fuVal('pctFollowupPicked')),
    coreRow('Interested', 'Interested'),
    flatRow_('Follow-up coverage on follow-up marked interested leads', fuVal('pctFollowupInterested')),
    coreRow('VC setup', 'VC setup'),
    flatRow_('Follow-up coverage for no-shows on VC', fuVal('pctFollowupNoshow')),
    coreRow('VC completed', 'VC completed'),
    flatRow_('Same day script shared coverage on VC completed', sameDayScript),
    coreRow('Script shared / 1 pager shared / Requirement doc shared', 'Script/1-pager shared'),
    coreRow('Script results/ filled one-pager/ volume & sample data', 'Script results/1-pager filled'),
    coreRow('Contract / LOI signed', 'Contract/LOI signed'),
  ];
}

// Collapsible-group spec, ported verbatim from GROUP_SPECS in daily_sync_pull.py.
var GROUP_SPECS_ = [
  ['Leads assigned', 2], ['Calls attempted', 2], ['Calls picked', 1],
  ['Interested', 1], ['VC setup', 1], ['VC completed', 1],
];

// ---------------------------------------------------------------------------------------------------
// 11. Per-rep block - ported verbatim from build_rep_block_rows() in daily_sync_pull.py: banner + header
//     + 9 METRICS rows, 7 FIXED day-columns (Mon..Sun of the current week, blank for days not yet
//     elapsed - fixed width is deliberate, see that function's docstring on why a growing/shrinking
//     day-column count was a real bug class in the earlier Apps Script draft).
// ---------------------------------------------------------------------------------------------------
var REP_BLOCK_GAP_ = 1;
var COL_WIDTHS_ = [420, 95, 85, 80, 20, 95, 85, 80, 20, 135];
var REP_COL_WIDTHS_ = [230, 55, 55, 55, 55, 55, 55, 55, 70, 65, 60, 15, 70, 65, 60, 15, 85];

function buildRepBlockRows_(vertical, oid, name, pulled, nReps, weekStart, todayDate, wtdDays, mtdDays) {
  var dayDates = [];
  for (var i = 0; i < 7; i++) dayDates.push(new Date(weekStart.getTime() + i * 24 * 60 * 60 * 1000));
  var dayHeaders = [];
  for (var i2 = 0; i2 < 7; i2++) dayHeaders.push(istPart_(dayDates[i2], 'EEE') + ' ' + istPart_(dayDates[i2], 'M') + '/' + istPart_(dayDates[i2], 'd'));
  var banner = name + ' — week starting Mon ' + isoDateIst_(weekStart) + ', refreshed ' + isoDateIst_(todayDate) + '.';
  var header = ['Metric'].concat(dayHeaders).concat(
    ['WTD Actual', 'WTD Target', '% Target Achievement', '', 'MTD Actual', 'MTD Target', '% Target Achievement', '', 'ITD Actual']);

  var oWtd = (pulled.ownerWtd || {})[oid] || {};
  var oMtd = (pulled.ownerMtd || {})[oid] || {};
  var oItd = (pulled.ownerItd || {})[oid] || {};
  var waAll = rowFormulas_(vertical, oWtd), maAll = rowFormulas_(vertical, oMtd), iaAll = rowFormulas_(vertical, oItd);
  var repDay = (pulled.ownerDay || {})[oid] || {};

  var rows = [[banner], header];
  for (var m = 0; m < METRICS_.length; m++) {
    var metric = METRICS_[m];
    var labels = REP_METRIC_LABELS_[vertical][metric];
    var dayVals = [];
    for (var d = 0; d < 7; d++) {
      if (dayDates[d].getTime() > todayDate.getTime()) { dayVals.push(''); continue; }
      var dIso = isoDateIst_(dayDates[d]);
      var dayMap = repDay[dIso] || {};
      var s = 0;
      for (var li = 0; li < labels.length; li++) s += (dayMap[labels[li]] || 0);
      dayVals.push(s);
    }
    var t = targetFor_(vertical, metric, wtdDays, mtdDays);
    var repWt = (t[0] === null) ? null : Math.ceil(t[0] / nReps);
    var repMt = (t[1] === null) ? null : Math.ceil(t[1] / nReps);
    var wa = waAll[metric], ma = maAll[metric], ia = iaAll[metric];
    rows.push([metric].concat(dayVals).concat([
      wa, repWt === null ? '—' : repWt, repWt ? pctStr_(wa, repWt) : '—', '',
      ma, repMt === null ? '—' : repMt, repMt ? pctStr_(ma, repMt) : '—', '', ia]));
  }
  return rows;
}

// ---------------------------------------------------------------------------------------------------
// 12. Sheet writing - find-or-append this week's block, wide clear (content+format), write main +
//     per-rep blocks, re-apply row/column groups + formatting. Ported from
//     write_performance_report()/build_week_block()/apply_block_formatting() in daily_sync_pull.py,
//     using native SpreadsheetApp calls wherever they are equivalent (simpler + no extra API scopes
//     needed), and the raw Sheets API only for row/column GROUP management - see rebuildGroups_().
// ---------------------------------------------------------------------------------------------------
var PERF_TAB_ = { cluster1: 'Company Ops (Cluster-1)', cluster2: 'Company Ops (Cluster-2)', coding: 'Coding', engineering: 'Engineering Datasets' };
var WEEK_BANNER_RE_ = /week starting Mon (\d{4}-\d{2}-\d{2})/;
var NAVY_HEX_ = '#1E335B';  // {red:0.1176,green:0.2,blue:0.3569} - same palette as the Daily Sync tab
var TEAL_HEX_ = '#198C7F';  // {red:0.098,green:0.549,blue:0.498}
var MINT_HEX_ = '#EDF7F4';  // {red:0.929,green:0.969,blue:0.957}

function findOrAppendWeekRow_(sheet, weekStartIso) {
  var lastRow = sheet.getLastRow();
  if (lastRow === 0) return { startRow: 1, oldLen: null };
  var colA = sheet.getRange(1, 1, lastRow, 1).getValues();
  var weekStarts = [];
  for (var i = 0; i < colA.length; i++) {
    var v = colA[i][0];
    if (!v) continue;
    var m = String(v).match(WEEK_BANNER_RE_);
    if (m) weekStarts.push([i + 1, m[1]]);
  }
  if (!weekStarts.length) return { startRow: 1, oldLen: null };
  var last = weekStarts[weekStarts.length - 1];
  if (last[1] === weekStartIso) return { startRow: last[0], oldLen: lastRow - last[0] + 1 };
  return { startRow: lastRow + 1, oldLen: null };
}

function padRows_(rows, width) {
  for (var i = 0; i < rows.length; i++) {
    while (rows[i].length < width) rows[i].push('');
  }
}

function applyRegionFormatting_(sheet, startRow1, nRows, startCol0, colWidths) {
  var startCol1 = startCol0 + 1;
  var nCols = colWidths.length;
  for (var i = 0; i < nCols; i++) sheet.setColumnWidth(startCol0 + i + 1, colWidths[i]);
  sheet.getRange(1, startCol1, sheet.getMaxRows(), 1).setWrap(true).setVerticalAlignment('middle');

  var bannerRow1 = startRow1, headerRow1 = bannerRow1 + 1, bodyStart1 = headerRow1 + 1;
  sheet.setRowHeight(bannerRow1, 32);
  sheet.getRange(bannerRow1, startCol1, 1, nCols).setBackground(NAVY_HEX_).setFontColor('#FFFFFF').setFontWeight('bold').setFontSize(11);
  sheet.getRange(headerRow1, startCol1, 1, nCols).setBackground(TEAL_HEX_).setFontColor('#FFFFFF').setFontWeight('bold').setHorizontalAlignment('center');
  sheet.getRange(headerRow1, startCol1, 1, 1).setHorizontalAlignment('left');
  for (var j = 0; j < nRows; j++) {
    var bodyRow1 = bodyStart1 + j;
    sheet.getRange(bodyRow1, startCol1, 1, nCols).setBackground(j % 2 === 0 ? MINT_HEX_ : '#FFFFFF');
  }
}

function sheetsApiBatchUpdate_(requests) {
  if (!requests.length) return;
  var token = ScriptApp.getOAuthToken();
  var resp = fetchJson_('https://sheets.googleapis.com/v4/spreadsheets/' + PERF_SHEET_ID + ':batchUpdate', {
    method: 'post', headers: { 'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json' },
    payload: JSON.stringify({ requests: requests }) });
  if (resp.error) Logger.log('batchUpdate error: ' + JSON.stringify(resp.error));
  return resp;
}

function rebuildGroups_(sheetId, tabName, startRow, fullRowLabels, repBlocks) {
  // FIXED (ported from the earlier Apps Script draft, where this was a real, hard-won fix): re-running
  // SpreadsheetApp's shiftRowGroupDepth()/getRowGroup() on every 2-hourly run is NOT idempotent - it
  // stacks depth every single run instead of replacing the existing group. Read every existing group via
  // the raw Sheets API, delete all of them, then add this run's groups fresh, all in ONE batchUpdate -
  // exactly what daily_sync_pull.py's own write_performance_report() does.
  var token = ScriptApp.getOAuthToken();
  var headers = { 'Authorization': 'Bearer ' + token };
  var detail = fetchJson_('https://sheets.googleapis.com/v4/spreadsheets/' + PERF_SHEET_ID +
    '?ranges=' + encodeURIComponent(tabName) + '&fields=sheets(rowGroups,columnGroups)',
    { headers: headers, method: 'get' });
  var sheetDetail = (detail.sheets || [])[0] || {};
  var requests = [];
  var existingRowGroups = sheetDetail.rowGroups || [];
  for (var rg = 0; rg < existingRowGroups.length; rg++) {
    requests.push({ deleteDimensionGroup: { range: { sheetId: sheetId, dimension: 'ROWS',
      startIndex: existingRowGroups[rg].range.startIndex, endIndex: existingRowGroups[rg].range.endIndex } } });
  }
  var existingColGroups = sheetDetail.columnGroups || [];
  for (var cg = 0; cg < existingColGroups.length; cg++) {
    requests.push({ deleteDimensionGroup: { range: { sheetId: sheetId, dimension: 'COLUMNS',
      startIndex: existingColGroups[cg].range.startIndex, endIndex: existingColGroups[cg].range.endIndex } } });
  }
  // New row groups (main block only) - 0-based: banner at (startRow-1), header at startRow, first body
  // row (fullRowLabels[0]) at startRow+1. A core row's children start right after it.
  for (var g = 0; g < GROUP_SPECS_.length; g++) {
    var coreLabel = GROUP_SPECS_[g][0], nChildren = GROUP_SPECS_[g][1];
    var idx = fullRowLabels.indexOf(coreLabel);
    if (idx === -1) continue;
    var gStart0 = startRow + 1 + idx + 1; // first child row, right after the core row
    requests.push({ addDimensionGroup: { range: { sheetId: sheetId, dimension: 'ROWS', startIndex: gStart0, endIndex: gStart0 + nChildren } } });
  }
  // New column groups - one per rep block, covering its 7 day-slot columns (position 1..7 within the
  // block: 0=label, 1-7=days, 8+=WTD/MTD/ITD).
  for (var b = 0; b < repBlocks.length; b++) {
    var dayStart0 = repBlocks[b].col0 + 1, dayEnd0 = dayStart0 + 7;
    requests.push({ addDimensionGroup: { range: { sheetId: sheetId, dimension: 'COLUMNS', startIndex: dayStart0, endIndex: dayEnd0 } } });
  }
  sheetsApiBatchUpdate_(requests);
}

function writeVertical_(ss, vertical, pulled, weekStart, todayDate, wtdDays, mtdDays) {
  var tabName = PERF_TAB_[vertical];
  var sheet = ss.getSheetByName(tabName);
  if (!sheet) throw new Error('tab not found: ' + tabName);

  // Grow the grid if needed - Coding needs ~101 columns (10 main + 1 gap + 5*(17+1) rep blocks); 160 is
  // a generous margin, same constant daily_sync_pull.py uses.
  var neededCols = 160;
  if (sheet.getMaxColumns() < neededCols) sheet.insertColumnsAfter(sheet.getMaxColumns(), neededCols - sheet.getMaxColumns());

  var weekStartIso = isoDateIst_(weekStart);
  var found = findOrAppendWeekRow_(sheet, weekStartIso);
  var startRow = found.startRow, oldLen = found.oldLen;

  var banner = tabName + ' — week starting Mon ' + weekStartIso + ', refreshed ' + isoDateIst_(todayDate) + '.';
  var header = ['Metric', 'WTD Actual', 'WTD Target', '% Target Achievement', '', 'MTD Actual', 'MTD Target', '% Target Achievement', '', 'ITD Actual (new tracking)'];
  var fullRows = buildFullRows_(vertical, pulled, wtdDays, mtdDays);
  var fullRowLabels = []; for (var fr = 0; fr < fullRows.length; fr++) fullRowLabels.push(fullRows[fr][0]);
  var mainRows = [[banner], header].concat(fullRows).concat([['']]);
  padRows_(mainRows, header.length);

  var repList = REP_MAPS_[vertical] || [];
  var nReps = repList.length;
  var repBlocks = [];
  var col0 = header.length + REP_BLOCK_GAP_;
  for (var ri = 0; ri < repList.length; ri++) {
    var oid = repList[ri][0], repName = repList[ri][1];
    var rows = buildRepBlockRows_(vertical, oid, repName, pulled, nReps, weekStart, todayDate, wtdDays, mtdDays);
    padRows_(rows, REP_COL_WIDTHS_.length);
    repBlocks.push({ name: repName, rows: rows, col0: col0 });
    col0 += REP_COL_WIDTHS_.length + REP_BLOCK_GAP_;
  }

  // Wide clear (content AND formatting) before writing - Range.clear() with no options clears both,
  // same guarantee as daily_sync_pull.py's raw updateCells clear (userEnteredValue + userEnteredFormat).
  var clearRows = Math.max(oldLen || 0, mainRows.length) + 2;
  sheet.getRange(startRow, 1, clearRows, 160).clear();

  sheet.getRange(startRow, 1, mainRows.length, header.length).setValues(mainRows);
  for (var b = 0; b < repBlocks.length; b++) {
    sheet.getRange(startRow, repBlocks[b].col0 + 1, repBlocks[b].rows.length, REP_COL_WIDTHS_.length).setValues(repBlocks[b].rows);
  }

  // Defensive unmerge before freezing column A - ported from apply_block_formatting()'s comment: a
  // leftover merged cell in column A (pre-existing on a tab from before this layout) makes
  // setFrozenColumns() fail outright ("can't freeze columns which contain only part of a merged cell").
  // breakApart() on a range with no merges is a safe no-op.
  sheet.getRange(1, 1, sheet.getMaxRows(), 160).breakApart();
  if (sheet.getFrozenColumns() < 1) sheet.setFrozenColumns(1);
  applyRegionFormatting_(sheet, startRow, fullRows.length, 0, COL_WIDTHS_);
  for (var b2 = 0; b2 < repBlocks.length; b2++) {
    applyRegionFormatting_(sheet, startRow, METRICS_.length, repBlocks[b2].col0, REP_COL_WIDTHS_);
  }

  rebuildGroups_(sheet.getSheetId(), tabName, startRow, fullRowLabels, repBlocks);

  // Verify by reading back a handful of key cells - never trust an unverified write.
  var readBackMain = sheet.getRange(startRow, 1, mainRows.length, header.length).getValues();
  Logger.log(tabName + ': wrote row ' + startRow + (oldLen ? ' (updated in place)' : ' (new week block)') +
    ' - readback banner="' + readBackMain[0][0] + '" Leads-assigned-WTD=' + readBackMain[3][1] +
    ' Leads-assigned-WTD-target=' + readBackMain[3][2]);
  for (var b3 = 0; b3 < repBlocks.length; b3++) {
    var rb = repBlocks[b3];
    var rbRead = sheet.getRange(startRow, rb.col0 + 1, rb.rows.length, REP_COL_WIDTHS_.length).getValues();
    Logger.log('  rep ' + rb.name + ': Leads-assigned-WTD=' + rbRead[2][8] + ' (row2=Leads assigned, col index 8 = WTD Actual)');
  }
}

// ---------------------------------------------------------------------------------------------------
// 13. Scheduling - CHANGED 2026-10-22 per explicit user ask: once daily at 6pm IST (was every 2 hours,
//     12 slots/day - that design is preserved in git history / the comment below if ever needed again),
//     no polling Sat/Sun or a listed holiday. Apps Script cannot natively express "once a day at a
//     specific minute, weekdays only" as a single trigger object with the precision this needs, so this
//     still installs ONE coarse trigger (every 30 minutes, every day, all hours - the simplest, most
//     robust trigger Apps Script supports) and relies on isScheduledSlot_() - checked as the FIRST
//     statement of the trigger-fired function, before any HubSpot call - to decide whether this
//     particular firing should do real work. Allowed slot: IST hour 18 (6pm) at the half-hour (tolerance
//     window minute 20-39, since Apps Script time triggers don't fire at an exact minute) - this lands on
//     18:30 IST, once/day. Weekday/holiday gating is the HOLIDAYS_/WEEKDAY_OK_ maps above - add a date to
//     HOLIDAYS_ to skip it, one line.
//     CAVEAT (flagged, not hidden): this means the installed trigger technically fires every 30 minutes
//     around the clock, every day including weekends - it is the GUARD, not the trigger's own schedule,
//     that enforces the weekday/hour window. The guard is written as the first, unconditional statement
//     of dailySyncTick() so a bug anywhere else in the file cannot cause an off-schedule run to do real
//     work.
// ---------------------------------------------------------------------------------------------------
function isScheduledSlot_(d) {
  if (!WEEKDAY_OK_[istPart_(d, 'EEE')]) return false;
  if (HOLIDAYS_[isoDateIst_(d)]) return false;
  var hour = Number(istPart_(d, 'H'));
  if (hour !== 18) return false;
  var minute = Number(istPart_(d, 'm'));
  return minute >= 20 && minute <= 39;
}

function installTrigger() {
  var triggers = ScriptApp.getProjectTriggers();
  for (var i = 0; i < triggers.length; i++) {
    var fn = triggers[i].getHandlerFunction();
    if (fn === 'dailySyncTick' || fn === 'continueDailySync_') ScriptApp.deleteTrigger(triggers[i]);
  }
  ScriptApp.newTrigger('dailySyncTick').timeBased().everyMinutes(30).create();
  Logger.log('installed: dailySyncTick every 30 min (guard allows only 18:30 IST, Mon-Fri, non-holiday - once/day)');
}

function pauseAutomation() {
  var triggers = ScriptApp.getProjectTriggers();
  var removed = 0;
  for (var i = 0; i < triggers.length; i++) {
    var fn = triggers[i].getHandlerFunction();
    if (fn === 'dailySyncTick' || fn === 'continueDailySync_') { ScriptApp.deleteTrigger(triggers[i]); removed++; }
  }
  PropertiesService.getScriptProperties().deleteProperty('PENDING_VERTICALS');
  Logger.log('removed ' + removed + ' trigger(s) - automation paused');
}

// --- checkpointed run queue: processes cluster1/cluster2/coding/engineering in sequence, self-continuing via a
//     one-off trigger if a single invocation risks running long (Apps Script's execution cap - see
//     docs/DAILY_SYNC_APPS_SCRIPT_HANDOFF.md's warning on this). The continuation trigger deliberately
//     does NOT re-check isScheduledSlot_() - it exists only to FINISH a run the schedule guard already
//     approved, not to start new unscheduled work.
var RUN_BUDGET_MS_ = 4.5 * 60 * 1000;

function startQueuedRun_(nowDate) {
  var props = PropertiesService.getScriptProperties();
  if (props.getProperty('PENDING_VERTICALS')) { Logger.log('a run is already in progress - not starting another'); return; }
  var weekStart = mondayOfWeek_(nowDate), monthStart = firstOfMonthIst_(nowDate), today = todayMidnightIst_(nowDate);
  var state = { remaining: ['cluster1', 'cluster2', 'coding', 'engineering'],
                weekStartIso: isoDateIst_(weekStart), monthStartIso: isoDateIst_(monthStart), todayIso: isoDateIst_(today) };
  props.setProperty('PENDING_VERTICALS', JSON.stringify(state));
  processQueue_();
}

function processQueue_() {
  var props = PropertiesService.getScriptProperties();
  var pendingJson = props.getProperty('PENDING_VERTICALS');
  if (!pendingJson) { Logger.log('nothing queued'); return; }
  var state = JSON.parse(pendingJson);
  var weekStart = new Date(state.weekStartIso + 'T00:00:00+05:30');
  var monthStart = new Date(state.monthStartIso + 'T00:00:00+05:30');
  var today = new Date(state.todayIso + 'T00:00:00+05:30');
  var wtdDays = workingDaysBetween_(weekStart, today);
  var mtdDays = workingDaysBetween_(monthStart, today);
  var ss = SpreadsheetApp.openById(PERF_SHEET_ID);
  var startTime = Date.now();

  while (state.remaining.length) {
    if (Date.now() - startTime > RUN_BUDGET_MS_) {
      props.setProperty('PENDING_VERTICALS', JSON.stringify(state));
      deleteContinuationTriggers_();
      ScriptApp.newTrigger('continueDailySync_').timeBased().after(60 * 1000).create();
      Logger.log('time budget reached - continuing in ~60s via continueDailySync_ (' + state.remaining.join(',') + ' remaining)');
      return;
    }
    var v = state.remaining[0]; // NOT shifted yet - only removed from the queue on SUCCESS (see catch below)
    try {
      Logger.log('pulling ' + v + ' ...');
      var pulled = pullVertical_(v, weekStart, monthStart, today);
      writeVertical_(ss, v, pulled, weekStart, today, wtdDays, mtdDays);
      Logger.log('wrote ' + v);
      state.remaining.shift();
    } catch (err) {
      // A transient HubSpot/Sheets error here must NOT leave PENDING_VERTICALS stuck forever (which would
      // make every future dailySyncTick() refuse to start a new run, thinking one is still "in progress" -
      // startQueuedRun_() checks for exactly that). Persist state (v stays first, retried next time),
      // cap retries so a genuinely broken key/portal can't loop forever burning API calls/triggers.
      state.errorCount = (state.errorCount || 0) + 1;
      Logger.log('ERROR processing ' + v + ' (attempt ' + state.errorCount + '): ' + err);
      if (state.errorCount > 5) {
        Logger.log('giving up after 5 failed attempts this run - clearing queue; next scheduled slot will retry fresh');
        props.deleteProperty('PENDING_VERTICALS');
        deleteContinuationTriggers_();
        return;
      }
      props.setProperty('PENDING_VERTICALS', JSON.stringify(state));
      deleteContinuationTriggers_();
      ScriptApp.newTrigger('continueDailySync_').timeBased().after(60 * 1000).create();
      return;
    }
  }
  props.deleteProperty('PENDING_VERTICALS');
  deleteContinuationTriggers_();
  Logger.log('daily sync run complete');
}

function deleteContinuationTriggers_() {
  var triggers = ScriptApp.getProjectTriggers();
  for (var i = 0; i < triggers.length; i++) {
    if (triggers[i].getHandlerFunction() === 'continueDailySync_') ScriptApp.deleteTrigger(triggers[i]);
  }
}

function continueDailySync_() { processQueue_(); }

function dailySyncTick() {
  // The installed trigger's handler - guard is the FIRST statement, before any HubSpot call.
  var now = new Date();
  if (!isScheduledSlot_(now)) return;
  var props = PropertiesService.getScriptProperties();
  var slotId = istPart_(now, 'yyyy-MM-dd-HH');
  if (props.getProperty('LAST_SLOT_STARTED') === slotId) { Logger.log('slot already started: ' + slotId); return; }
  props.setProperty('LAST_SLOT_STARTED', slotId);
  startQueuedRun_(now);
}

// ---------------------------------------------------------------------------------------------------
// 14. Manual entry points for setup/testing - see the SETUP section in the file header comment.
// ---------------------------------------------------------------------------------------------------
function testDryRun_(vertical) {
  // Computes and logs the numbers WITHOUT writing the sheet - cross-check against a fresh
  // `python3 tools/daily_sync_pull.py` run's numbers for the same vertical before trusting this.
  var now = new Date();
  var weekStart = mondayOfWeek_(now), monthStart = firstOfMonthIst_(now), today = todayMidnightIst_(now);
  var wtdDays = workingDaysBetween_(weekStart, today), mtdDays = workingDaysBetween_(monthStart, today);
  Logger.log('today=' + isoDateIst_(today) + ' weekStart=' + isoDateIst_(weekStart) + ' monthStart=' + isoDateIst_(monthStart) +
             ' wtdDays=' + wtdDays + ' mtdDays=' + mtdDays);
  var pulled = pullVertical_(vertical, weekStart, monthStart, today);
  var rows = buildFullRows_(vertical, pulled, wtdDays, mtdDays);
  Logger.log('--- ' + vertical + ' team rows (label, WTD actual, WTD target, WTD%, , MTD actual, MTD target, MTD%, , ITD) ---');
  for (var i = 0; i < rows.length; i++) Logger.log(JSON.stringify(rows[i]));
  var repList = REP_MAPS_[vertical] || [];
  for (var ri = 0; ri < repList.length; ri++) {
    var oid = repList[ri][0], repName = repList[ri][1];
    var repRows = buildRepBlockRows_(vertical, oid, repName, pulled, repList.length, weekStart, today, wtdDays, mtdDays);
    Logger.log('--- rep ' + repName + ' (oid ' + oid + ') ---');
    for (var r = 0; r < repRows.length; r++) Logger.log(JSON.stringify(repRows[r]));
  }
  return pulled;
}

function runOneVerticalLive_(vertical) {
  // Real write for exactly one vertical - use this to verify one at a time before runDailySyncNow_().
  var now = new Date();
  var weekStart = mondayOfWeek_(now), monthStart = firstOfMonthIst_(now), today = todayMidnightIst_(now);
  var wtdDays = workingDaysBetween_(weekStart, today), mtdDays = workingDaysBetween_(monthStart, today);
  var ss = SpreadsheetApp.openById(PERF_SHEET_ID);
  var pulled = pullVertical_(vertical, weekStart, monthStart, today);
  writeVertical_(ss, vertical, pulled, weekStart, today, wtdDays, mtdDays);
}

function runDailySyncNow_() {
  // Real write for all three verticals, right now, bypassing the schedule guard - for manual testing
  // only. The actual scheduled path is dailySyncTick() -> startQueuedRun_() -> processQueue_().
  startQueuedRun_(new Date());
}

// ---------------------------------------------------------------------------------------------------
// 15. Daily D-1/WTD/MTD summary email - ADDED 2026-10-19, explicit user ask: "this gotta be automated
//     with sync+email being sent through apps script". Same house format/style as the Python-built
//     version sent manually this morning (verified correct by the user before this was built) - reuses
//     this file's existing NAVY_HEX_/TEAL_HEX_/MINT_HEX_ palette, rowFormulas_/targetFor_/pctStr_, and
//     pullVertical_'s already-computed per-owner day buckets (summed across reps for the team-level D-1
//     row, same way teamWtd/teamMtd/teamItd are already team-level sums).
//     TEST-ONLY RECIPIENT until the user explicitly says to widen it - see EMAIL_RECIPIENTS_ below.
//     SCOPE: coding/cluster1/cluster2 only - UNCHANGED 2026-10-22 when engineering was added to the sheet
//     -write queue (see SCOPE in the file header): the user's ask that day was specifically the 6pm sheet
//     sync, not this email. Engineering Datasets IS now pulled by this script (pullVertical_), so adding
//     it here is a small, deliberate follow-up if/when asked - not a limitation left over from before.
// ---------------------------------------------------------------------------------------------------
var EMAIL_RECIPIENTS_ = ['bhanu.enamala@lh2.ai']; // TEST PHASE ONLY - do not widen without explicit sign-off
var EMAIL_METRIC_LABELS_ = {
  'Leads assigned': 'Leads assigned',
  'Calls attempted': 'Calls attempted',
  'Calls picked': 'Calls picked',
  'Interested': 'Interested',
  'VC setup': 'VC setup',
  'VC completed': 'VC completed',
  'Script/1-pager shared': 'Script shared / 1 pager shared / Requirement doc shared',
  'Script results/1-pager filled': 'Script results/ filled one-pager/ volume & sample data',
  'Contract/LOI signed': 'Contract / LOI signed',
};
var EMAIL_VERTICAL_TITLES_ = { coding: 'Coding', cluster1: 'Company Ops (Cluster-1)', cluster2: 'Company Ops (Cluster-2)' };

function lastWorkingDayIst_(fromDate) {
  // Steps back at least one calendar day, then continues until a working day is found - "D-1" always
  // means the last COMPLETED working day before today, never today itself even if today is a workday.
  var d = new Date(fromDate.getTime() - 24 * 60 * 60 * 1000);
  while (!isWorkingDayIst_(d)) d = new Date(d.getTime() - 24 * 60 * 60 * 1000);
  return d;
}

function teamDayCounts_(vertical, pulled, dayIso) {
  // Sums every rep's day-bucket for this vertical on dayIso into one {canon: count} map - the team-level
  // equivalent of pulled.ownerWtd/ownerMtd/ownerItd being per-owner (teamWtd/teamMtd/teamItd are already
  // summed the same way by pullVertical_ itself; there's just no team-level day bucket stored, so this
  // derives one on demand instead of duplicating that summation inside pullVertical_).
  var out = {};
  var ownerDay = pulled.ownerDay || {};
  for (var oid in ownerDay) {
    var dayMap = ownerDay[oid][dayIso];
    if (!dayMap) continue;
    for (var canon in dayMap) out[canon] = (out[canon] || 0) + dayMap[canon];
  }
  return out;
}

function escapeHtml_(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function buildVerticalEmailTableHtml_(vertical, pulled, d1Date, wtdDays, mtdDays) {
  var d1Iso = isoDateIst_(d1Date);
  var d1Counts = teamDayCounts_(vertical, pulled, d1Iso);
  var d1All = rowFormulas_(vertical, d1Counts);
  var waAll = rowFormulas_(vertical, pulled.wtd), maAll = rowFormulas_(vertical, pulled.mtd);
  var d1Label = 'D-1 (' + istPart_(d1Date, 'EEE') + ' ' + istPart_(d1Date, 'M') + '/' + istPart_(d1Date, 'd') + ')';

  var thStyle = 'background:' + TEAL_HEX_ + ';color:#FFFFFF;font-weight:bold;padding:8px 10px;text-align:center;border:1px solid ' + TEAL_HEX_ + ';';
  var thLeftStyle = thStyle + 'text-align:left;';
  var tdStyle = 'padding:7px 10px;border:1px solid #D9E2E8;text-align:center;';
  var tdLeftStyle = tdStyle + 'text-align:left;';

  var html = '<h3 style="color:' + NAVY_HEX_ + ';font-family:Arial,sans-serif;margin:22px 0 8px 0;">' +
             escapeHtml_(EMAIL_VERTICAL_TITLES_[vertical]) + '</h3>';
  html += '<table style="border-collapse:collapse;font-family:Arial,sans-serif;font-size:13px;width:100%;max-width:720px;">';
  html += '<tr><th style="' + thLeftStyle + '">Metric</th><th style="' + thStyle + '">' + escapeHtml_(d1Label) + '</th>' +
          '<th style="' + thStyle + '">WTD</th><th style="' + thStyle + '">MTD</th>' +
          '<th style="' + thStyle + '">Target WTD %</th><th style="' + thStyle + '">Target MTD %</th></tr>';

  for (var i = 0; i < METRICS_.length; i++) {
    var m = METRICS_[i];
    var t = targetFor_(vertical, m, wtdDays, mtdDays);
    var wa = waAll[m], ma = maAll[m], d1v = d1All[m] || 0;
    var wtdPct = t[0] ? pctStr_(wa, t[0]) : '—';
    var mtdPct = t[1] ? pctStr_(ma, t[1]) : '—';
    var rowBg = (i % 2 === 0) ? MINT_HEX_ : '#FFFFFF';
    html += '<tr style="background:' + rowBg + ';"><td style="' + tdLeftStyle + '">' + escapeHtml_(EMAIL_METRIC_LABELS_[m]) + '</td>' +
            '<td style="' + tdStyle + '">' + d1v + '</td><td style="' + tdStyle + '">' + wa + '</td>' +
            '<td style="' + tdStyle + '">' + ma + '</td><td style="' + tdStyle + '">' +
            (wtdPct === '—' ? '—' : wtdPct + '%') + '</td><td style="' + tdStyle + '">' +
            (mtdPct === '—' ? '—' : mtdPct + '%') + '</td></tr>';
  }
  html += '</table>';
  return html;
}

function sendDailySummaryEmail_(allPulled, wtdDays, mtdDays, now) {
  var d1Date = lastWorkingDayIst_(now);
  var subject = 'Supply Team Performance Report - ' + istPart_(now, 'd MMM');
  var bodyHtml = '<div style="font-family:Arial,sans-serif;">';
  var verticals = ['coding', 'cluster1', 'cluster2'];
  for (var i = 0; i < verticals.length; i++) {
    bodyHtml += buildVerticalEmailTableHtml_(verticals[i], allPulled[verticals[i]], d1Date, wtdDays, mtdDays);
  }
  bodyHtml += '</div>';

  MailApp.sendEmail({
    to: EMAIL_RECIPIENTS_.join(','),
    subject: subject,
    htmlBody: bodyHtml,
  });
  Logger.log('sent daily summary email to: ' + EMAIL_RECIPIENTS_.join(',') + ' subject="' + subject + '"');
}

function testSendDailyEmailNow_() {
  // Manual end-to-end test entry point: pulls FRESH data for all 3 verticals right now (does not touch
  // the sheet) and sends the email immediately to EMAIL_RECIPIENTS_ (test phase: self only). Run this via
  // `clasp run testSendDailyEmailNow_` to verify the whole pipeline before wiring it into the daily
  // trigger.
  var now = new Date();
  var weekStart = mondayOfWeek_(now), monthStart = firstOfMonthIst_(now), today = todayMidnightIst_(now);
  var wtdDays = workingDaysBetween_(weekStart, today), mtdDays = workingDaysBetween_(monthStart, today);
  var verticals = ['coding', 'cluster1', 'cluster2'];
  var allPulled = {};
  for (var i = 0; i < verticals.length; i++) {
    Logger.log('pulling ' + verticals[i] + ' for email test...');
    allPulled[verticals[i]] = pullVertical_(verticals[i], weekStart, monthStart, today);
  }
  sendDailySummaryEmail_(allPulled, wtdDays, mtdDays, now);
  return 'sent';
}

function testSendDailyEmailNow() {
  // Plain-named wrapper so this shows up in the Apps Script editor's Run dropdown - that picker hides
  // any function whose name ends in "_" (Google's own "private by convention" filtering), which is why
  // none of this file's underscore-suffixed manual entry points (testDryRun_, runOneVerticalLive_, etc.)
  // appear there either. This wrapper is the one to select and Run from the editor for the first-time
  // OAuth consent click; testSendDailyEmailNow_() itself is still what clasp run/future code should call.
  return testSendDailyEmailNow_();
}

// ---------------------------------------------------------------------------------------------------
// 16. Verification wrappers for the 2026-10-22 'engineering' (RAT) extension - plain names so they show
//     up in the Run dropdown (see testSendDailyEmailNow's comment above for why). Follow the file header
//     SETUP steps: dry-run first (compare its Logger output against a fresh
//     `python3 tools/daily_sync_pull.py` run's Engineering Datasets numbers), only then a single live
//     write, before trusting the 6pm trigger to include it.
// ---------------------------------------------------------------------------------------------------
function testEngineeringDryRun() {
  return testDryRun_('engineering');
}

function runEngineeringLiveOnce() {
  // Real write for ONLY the Engineering Datasets tab - run this after testEngineeringDryRun looks right,
  // to verify the actual sheet write before relying on the 6pm trigger (which now includes 'engineering'
  // in its queue automatically - no separate step needed to "turn it on" once this checks out).
  return runOneVerticalLive_('engineering');
}
