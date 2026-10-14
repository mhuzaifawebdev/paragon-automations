/**
 * Log-out watcher for the AutoDialer-Alerts Google Sheet.
 *
 * Every minute it asks Zadarma whether each operator's extension is online. When one goes from online to
 * offline during the shift it adds a row to the "Logouts" tab; when it comes back online the same row gets
 * the time and the minutes it was logged out. The "Daily summary" tab (scripts/build_alert_report.py) then
 * shows log-outs next to idle alerts and call gaps.
 *
 * Install (once):
 *   1. Open the AutoDialer-Alerts sheet > Extensions > Apps Script. Add a file, paste this, save.
 *   2. Project Settings (gear) > Script properties: add ZADARMA_API_KEY and ZADARMA_API_SECRET.
 *   3. Back in the editor choose the function  setup  and press Run. Approve the access Google asks for.
 *      That creates the Logouts tab and the one-minute timer. Running setup again does no harm.
 * To stop it: run  stopWatching.
 *
 * Keep EXTENSIONS and the shift hours the same as config.yaml (vapi.zadarma.monitor).
 */

var SHIFT_START_UTC = '07:00';
var SHIFT_END_UTC = '16:00';
var EXTENSIONS = {
  '100': 'Nadia Theuma', '101': 'Felice', '102': 'Giuseppe Papa', '103': 'Ali', '104': 'Hidayat Ullah',
  '105': 'Zahra Batool', '106': 'Meerab', '107': 'Gulelala Yousafzai', '108': 'Ivan', '109': 'Shahid Khan Usmani',
  '110': 'Muhammad Huzaifa', '111': 'Tanisha Imam', '112': 'Nazish Shabbir', '113': 'Saima Abbas', '117': 'Hamza Ali',
  '124': 'Daniya (AI dialer line)'
};
var LOGOUT_TAB = 'Logouts';
var LOGOUT_HEADER = ['Date (UTC)', 'Logged out at (UTC)', 'Extension', 'Operator', 'Logged back in at (UTC)', 'Minutes logged out'];
var MD5_OF_EMPTY = 'd41d8cd98f00b204e9800998ecf8427e';   // Zadarma signs with md5 of the query; these calls have none

function setup() {
  logoutTab_();
  stopWatching();
  ScriptApp.newTrigger('watchLogouts').timeBased().everyMinutes(1).create();
}

function stopWatching() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'watchLogouts') ScriptApp.deleteTrigger(t);
  });
}

/** Runs every minute. Outside the shift it only follows log-outs that are still open. */
function watchLogouts() {
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(5000)) return;                  // the previous minute is still running
  try {
    var now = new Date();
    var props = PropertiesService.getScriptProperties();
    var shift = inShift(now, SHIFT_START_UTC, SHIFT_END_UTC);
    var exts = Object.keys(EXTENSIONS).filter(function (ext) {
      if (shift) return true;
      var s = loadState_(props, ext);
      return s && s.row;
    });
    if (!exts.length) return;
    var online = zadarmaOnline_(exts, props);
    var tab = null;
    exts.forEach(function (ext) {
      var prev = loadState_(props, ext);
      var d = decide(prev, online[ext], now, shift);
      if (!d.next) return;
      if (d.action === 'logout') {
        tab = tab || logoutTab_();
        tab.appendRow([iso_(now).slice(0, 10), iso_(now).slice(11, 16), ext, EXTENSIONS[ext], '', '']);
        d.next.row = tab.getLastRow();
      } else if (d.action === 'login') {
        tab = tab || logoutTab_();
        tab.getRange(prev.row, 5, 1, 2).setValues([[iso_(now).slice(11, 16), minutesBetween(prev.since, now)]]);
      }
      props.setProperty('presence_' + ext, JSON.stringify(d.next));
    });
  } finally {
    lock.releaseLock();
  }
}

// ---- decision rules (pure; tested offline by tests/test_logout_watch.mjs) ----

function inShift(now, startUtc, endUtc) {
  var hm = iso_(now).slice(11, 16);
  return startUtc <= hm && hm <= endUtc;
}

/**
 * prev: saved state {online, day, since, row} or null. online: true / false / null (Zadarma did not say).
 * Returns {action: 'none' | 'logout' | 'login', next: state to save, or null when nothing changed}.
 *  - No answer from Zadarma: nothing happens, so a hiccup is never recorded as a log-out.
 *  - First check of the day: the status is only remembered. Someone already offline when the shift starts
 *    did not log out during it (the pause monitor reports that case).
 *  - online -> offline during the shift: a log-out starts.
 *  - offline -> online with an open log-out: it ends, whatever the time.
 */
function decide(prev, online, now, shift) {
  if (online !== true && online !== false) return { action: 'none', next: null };
  var day = iso_(now).slice(0, 10), at = iso_(now);
  if (!prev || prev.day !== day) {
    if (prev && prev.row) {
      if (online === true) return { action: 'login', next: { online: online, day: day, since: at, row: null } };
      return { action: 'none', next: { online: prev.online, day: day, since: prev.since, row: prev.row } };
    }
    return { action: 'none', next: { online: online, day: day, since: at, row: null } };
  }
  if (prev.online === online) return { action: 'none', next: null };
  if (prev.online === true && online === false) {
    return { action: shift ? 'logout' : 'none', next: { online: online, day: day, since: at, row: null } };
  }
  return { action: prev.row ? 'login' : 'none', next: { online: online, day: day, since: at, row: null } };
}

function minutesBetween(fromIso, toDate) {
  return Math.max(0, Math.round((toDate.getTime() - Date.parse(fromIso)) / 60000));
}

// ---- Zadarma, sheet and storage ----

function iso_(d) { return d.toISOString(); }

function loadState_(props, ext) {
  try { return JSON.parse(props.getProperty('presence_' + ext) || 'null'); } catch (e) { return null; }
}

/** {ext: true|false|null}. All extensions are asked at once. Same signing as agent/zadarma_client.py. */
function zadarmaOnline_(exts, props) {
  var key = props.getProperty('ZADARMA_API_KEY'), secret = props.getProperty('ZADARMA_API_SECRET');
  if (!key || !secret) throw new Error('Add ZADARMA_API_KEY and ZADARMA_API_SECRET under Project Settings > Script properties.');
  var requests = exts.map(function (ext) {
    var method = '/v1/pbx/internal/' + ext + '/status/';
    var mac = Utilities.computeHmacSignature(Utilities.MacAlgorithm.HMAC_SHA_1, method + MD5_OF_EMPTY, secret);
    var hex = mac.map(function (b) { return ('0' + (b & 0xff).toString(16)).slice(-2); }).join('');
    return { url: 'https://api.zadarma.com' + method, headers: { Authorization: key + ':' + Utilities.base64Encode(hex) },
             muteHttpExceptions: true };
  });
  var out = {};
  UrlFetchApp.fetchAll(requests).forEach(function (resp, i) {
    var v = null;
    try {
      var data = JSON.parse(resp.getContentText());
      if (data.status === 'success') v = { 'true': true, 'false': false }[String(data.is_online)];
    } catch (e) { /* unreadable reply: treated as no answer */ }
    out[exts[i]] = v === undefined ? null : v;
  });
  return out;
}

function logoutTab_() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var tab = ss.getSheetByName(LOGOUT_TAB) || ss.insertSheet(LOGOUT_TAB);
  if (tab.getLastRow() === 0) {
    tab.appendRow(LOGOUT_HEADER);
    tab.setFrozenRows(1);
    tab.getRange(1, 1, 1, LOGOUT_HEADER.length).setFontWeight('bold');
  }
  return tab;
}
