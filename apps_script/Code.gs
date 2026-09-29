/**
 * Paragon > Enrich menu for the Batch Data Google Sheet.
 *
 * Install: in the Sheet open Extensions > Apps Script, paste this file, save, reload the Sheet.
 * One-time setup: Project Settings (gear) > Script properties, add
 *     GITHUB_TOKEN  a fine-grained token with "Actions: read and write" on the one repo
 *     GITHUB_REPO   owner/repo, e.g.  yourname/paragon-calling
 * Free: Apps Script and the GitHub Actions minutes for a private repo cost nothing.
 *
 * How people use it:
 *   1. type  pending  in the "Run status" column (AI) of the rows to enrich
 *      (optionally paste the institute's website in "Website override", column AH)
 *   2. Paragon > Enrich pending rows
 *   3. a few minutes later the row fills in and its Run status becomes "done <date>"
 *
 * The token is readable by anyone who can edit this script, so keep the Sheet's edit access small.
 */

var STATUS_COL = 35;   // AI: Run status
var WEBSITE_COL = 34;  // AH: Website override

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('Paragon')
    .addItem('Mark selected rows pending', 'markSelectedPending')
    .addItem('Enrich pending rows', 'enrichPending')
    .addToUi();
}

function markSelectedPending() {
  var sheet = SpreadsheetApp.getActiveSheet();
  var range = sheet.getActiveRange();
  var first = Math.max(range.getRow(), 2);
  var last = range.getLastRow();
  if (last < first) {
    SpreadsheetApp.getUi().alert('Select one or more institute rows first (below the header).');
    return;
  }
  sheet.getRange(first, STATUS_COL, last - first + 1, 1).setValue('pending');
  SpreadsheetApp.getActive().toast((last - first + 1) + ' row(s) marked pending. Now choose Paragon > Enrich pending rows.');
}

function enrichPending() {
  var props = PropertiesService.getScriptProperties();
  var token = props.getProperty('GITHUB_TOKEN');
  var repo = props.getProperty('GITHUB_REPO');
  var ui = SpreadsheetApp.getUi();
  if (!token || !repo) {
    ui.alert('Setup needed: add GITHUB_TOKEN and GITHUB_REPO under Project Settings > Script properties.');
    return;
  }
  var sheet = SpreadsheetApp.getActiveSheet();
  var status = sheet.getRange(2, STATUS_COL, Math.max(sheet.getLastRow() - 1, 1), 1).getValues();
  var pending = status.filter(function (r) { return String(r[0]).toLowerCase().trim() === 'pending'; }).length;
  if (!pending) {
    ui.alert('No rows are marked pending. Type "pending" in the Run status column (AI) first.');
    return;
  }
  var resp = UrlFetchApp.fetch('https://api.github.com/repos/' + repo + '/dispatches', {
    method: 'post',
    contentType: 'application/json',
    headers: { Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json' },
    payload: JSON.stringify({ event_type: 'enrich' }),
    muteHttpExceptions: true
  });
  if (resp.getResponseCode() === 204) {
    ui.alert('Started. ' + pending + ' row(s) will be enriched. Refresh this sheet in a few minutes; Run status will show "done".');
  } else {
    ui.alert('GitHub refused the request (' + resp.getResponseCode() + '): ' + resp.getContentText().substring(0, 200));
  }
}
