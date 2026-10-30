/**
 * On-time starter for the GitHub jobs, run by Google (free) instead of Vercel Cron, so the client's Vercel
 * account does no scheduled work at all. GitHub's own schedule fires only every few hours, so this sends the
 * start signal on time; the work itself still runs on GitHub, for free.
 *
 *   every 10 minutes, 07:00-16:59 UTC  ->  .github/workflows/pause_monitor.yml   (auto-dialer: offline/idle, call gaps)
 *   every 15 minutes                   ->  .github/workflows/resume_batches.yml  (scraper: restart paused batches)
 *   every 10 minutes                   ->  .github/workflows/queue.yml           (scraper: Drive inbox + batch queue)
 *
 * Install (once), in the same Apps Script project as LogoutWatch.gs (AutoDialer-Alerts sheet > Extensions > Apps Script):
 *   1. Add a file, paste this, save.
 *   2. Project Settings (gear) > Script properties: add GITHUB_DISPATCH_TOKEN (a GitHub token allowed to start
 *      workflows on mhuzaifawebdev/paragon-automations: classic token with "repo" and "workflow").
 *   3. Choose the function  setupScheduler  and press Run. Approve the access Google asks for.
 * To stop it: run  stopScheduler.
 */

var GITHUB_REPO = 'mhuzaifawebdev/paragon-automations';
var GITHUB_BRANCH = 'main';
var MONITOR_FROM_UTC = 7, MONITOR_TO_UTC = 16;          // hours, inclusive: same window as the old Vercel cron

function setupScheduler() {
  stopScheduler();
  ScriptApp.newTrigger('startPauseMonitor').timeBased().everyMinutes(10).create();
  ScriptApp.newTrigger('startResumeBatches').timeBased().everyMinutes(15).create();
  ScriptApp.newTrigger('startBatchQueue').timeBased().everyMinutes(10).create();
}

function stopScheduler() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    var f = t.getHandlerFunction();
    if (f === 'startPauseMonitor' || f === 'startResumeBatches' || f === 'startBatchQueue') ScriptApp.deleteTrigger(t);
  });
}

function startPauseMonitor() {
  if (!monitorHour(new Date().getUTCHours())) return;
  dispatchWorkflow_('pause_monitor.yml');
}

function startResumeBatches() {
  dispatchWorkflow_('resume_batches.yml');
}

function startBatchQueue() {
  dispatchWorkflow_('queue.yml');
}

function monitorHour(h) {
  return h >= MONITOR_FROM_UTC && h <= MONITOR_TO_UTC;
}

function dispatchWorkflow_(file) {
  var token = PropertiesService.getScriptProperties().getProperty('GITHUB_DISPATCH_TOKEN');
  if (!token) throw new Error('Add GITHUB_DISPATCH_TOKEN in Project Settings > Script properties.');
  var resp = UrlFetchApp.fetch('https://api.github.com/repos/' + GITHUB_REPO + '/actions/workflows/' + file + '/dispatches', {
    method: 'post',
    contentType: 'application/json',
    headers: { Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json' },
    payload: JSON.stringify({ ref: GITHUB_BRANCH }),
    muteHttpExceptions: true
  });
  if (resp.getResponseCode() !== 204) {
    throw new Error('GitHub refused to start ' + file + ': ' + resp.getResponseCode() + ' ' + resp.getContentText().slice(0, 300));
  }
}
