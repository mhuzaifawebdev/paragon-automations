// Offline tests for the careers page (web_scraper/api/careers.js, apply.js, careers-sitemap.js). The HR Apps Script
// web app is faked. Run: node tests/test_careers.mjs
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);

let pass = 0, fail = 0;
const check = (name, got, want) => {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  ok ? pass++ : fail++;
  console.log((ok ? "ok   " : "FAIL ") + name + (ok ? "" : `: got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`));
};

process.env.HR_APPS_SCRIPT_URL = "https://script.example/exec";
process.env.HR_FORM_SECRET = "s".repeat(20);
const JOBS = { company: "Paragon", website: "https://paragoninnovation.net", country: "Pakistan", employment_type: "FULL_TIME",
  jobs: [{ id: "R-SAL-1", title: "Sales Manager <b>", department: "Sales", description: "Sell courses.\n\nTarget: 40 calls a day.",
           must_haves: ["CRM", "Cold calling"], salary: "", posted: "2026-10-01", valid_through: "2026-11-30" }] };
const sent = [];
global.fetch = async (url, opt) => {
  sent.push({ url, opt });
  if (opt && opt.method === "POST") return { json: async () => ({ ok: true }) };
  return { json: async () => JOBS };
};
const mkRes = () => { const r = { code: 0, headers: {}, body: "" };
  r.status = c => { r.code = c; return r; }; r.setHeader = (k, v) => { r.headers[k] = v; };
  r.send = b => { r.body = b; return r; }; r.json = b => { r.body = b; return r; }; return r; };

const careers = require("../web_scraper/api/careers.js");
let res = mkRes();
await careers({ headers: { host: "leads.paragoninnovation.net" }, query: { src: "linkedin" } }, res);
check("list page lists the open job with a link that keeps the source", [res.code, res.body.includes('href="/careers/job/R-SAL-1?src=linkedin"')], [200, true]);
check("the secret is sent to the HR sheet, never put in the page", [sent[0].url.includes("key="), res.body.includes("s".repeat(20))], [true, false]);
res = mkRes();
await careers({ headers: { host: "careers.paragoninnovation.net" }, query: { id: "R-SAL-1", src: "indeed" } }, res);
const ld = JSON.parse(res.body.match(/<script type="application\/ld\+json">([\s\S]*?)<\/script>/)[1]);
check("job page: JobPosting markup Google needs (remote, country, dates, company)",
  [ld["@type"], ld.jobLocationType, ld.applicantLocationRequirements.name, ld.datePosted, ld.validThrough, ld.hiringOrganization.name, ld.directApply],
  ["JobPosting", "TELECOMMUTE", "Pakistan", "2026-10-01", "2026-11-30T23:59:59Z", "Paragon", true]);
check("on the careers subdomain the job lives at /job/<id>", ld.url, "https://careers.paragoninnovation.net/job/R-SAL-1");
check("text from the sheet is escaped (no HTML injection)", [res.body.includes("Sales Manager &lt;b&gt;"), res.body.includes("Sales Manager <b>")], [true, false]);
check("the apply form carries the role and the source", [res.body.includes('name="role_id" value="R-SAL-1"'), res.body.includes('name="source" value="indeed"')], [true, true]);
res = mkRes();
await careers({ headers: { host: "careers.paragoninnovation.net" }, query: { id: "R-GONE" } }, res);
check("a closed job answers 404 with a friendly page", [res.code, res.body.includes("no longer open")], [404, true]);

const apply = require("../web_scraper/api/apply.js");
res = mkRes();
await apply({ method: "POST", body: { website: "spam" } }, res);
check("honeypot: a bot gets 'ok' and nothing is sent", [res.body.ok, sent.filter(s => s.opt && s.opt.method === "POST").length], [true, 0]);
res = mkRes();
await apply({ method: "POST", body: { name: "Amna", email: "a@x.com", role_id: "R-SAL-1", cv_base64: "A".repeat(4300000) } }, res);
check("a CV over 3 MB is refused before it is sent", [res.code, res.body.ok], [400, false]);
res = mkRes();
await apply({ method: "POST", body: { name: "Amna", email: "a@x.com", role_id: "R-SAL-1", cv_name: "cv.pdf", cv_base64: "QUJD",
  consent: true, source: "linkedin", extra: "dropped" } }, res);
const posted = JSON.parse(sent[sent.length - 1].opt.body);
check("a good application is passed on with the secret, only known fields", [res.body.ok, posted.key.length, "extra" in posted, posted.consent],
  [true, 20, false, true]);

const sitemap = require("../web_scraper/api/careers-sitemap.js");
res = mkRes();
await sitemap({ headers: { host: "careers.paragoninnovation.net" } }, res);
check("sitemap lists every open job page", res.body.includes("<loc>https://careers.paragoninnovation.net/job/R-SAL-1</loc>"), true);

delete process.env.HR_FORM_SECRET;
res = mkRes();
await careers({ headers: { host: "leads.paragoninnovation.net" }, query: {} }, res);
check("not connected yet: a polite 503, no crash", res.code, 503);

console.log(`\n${pass}/${pass + fail} passed`);
process.exit(fail ? 1 : 0);
