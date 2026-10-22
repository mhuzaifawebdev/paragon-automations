// Offline tests for apps_script/Scheduler.gs (no network). Run: node tests/test_scheduler.mjs
import fs from "node:fs";
import vm from "node:vm";

const src = fs.readFileSync(new URL("../apps_script/Scheduler.gs", import.meta.url), "utf8");
const sent = [];
const g = {
  PropertiesService: { getScriptProperties: () => ({ getProperty: k => (k === "GITHUB_DISPATCH_TOKEN" ? "tok" : null) }) },
  UrlFetchApp: { fetch: (url, opt) => { sent.push({ url, opt }); return { getResponseCode: () => 204, getContentText: () => "" }; } },
};
vm.createContext(g);
vm.runInContext(src, g);

let pass = 0, fail = 0;
const check = (name, got, want) => {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  ok ? pass++ : fail++;
  console.log((ok ? "ok   " : "FAIL ") + name + (ok ? "" : `: got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`));
};

check("monitor runs 07:00-16:59 UTC only", [6, 7, 12, 16, 17].map(h => g.monitorHour(h)), [false, true, true, true, false]);
g.startResumeBatches();
check("resume job: starts resume_batches.yml on main with the token",
  [sent[0].url.endsWith("/actions/workflows/resume_batches.yml/dispatches"), JSON.parse(sent[0].opt.payload).ref,
   sent[0].opt.headers.Authorization], [true, "main", "Bearer tok"]);
g.UrlFetchApp.fetch = () => ({ getResponseCode: () => 401, getContentText: () => "Bad credentials" });
let err = "";
try { g.startResumeBatches(); } catch (e) { err = e.message; }
check("a refused start is reported, not silently ignored", err.includes("401"), true);

console.log(`\n${pass}/${pass + fail} passed`);
process.exit(fail ? 1 : 0);
