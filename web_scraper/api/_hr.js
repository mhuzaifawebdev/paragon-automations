/**
 * The HR tool's Apps Script web app (hr_cycle/Careers.gs): open roles for the careers page, and applications.
 * Env vars (Vercel project settings): HR_APPS_SCRIPT_URL (the web app's /exec URL) and HR_FORM_SECRET (the same long
 * random text as the Script Property HR_FORM_SECRET). Without them the careers page says it is not set up yet.
 */
function hrCfg() {
  const url = process.env.HR_APPS_SCRIPT_URL, key = process.env.HR_FORM_SECRET;
  if (!url || !key) throw new Error("The careers page is not connected yet (HR_APPS_SCRIPT_URL / HR_FORM_SECRET).");
  return { url, key };
}

async function getJobs() {
  const { url, key } = hrCfg();
  const resp = await fetch(`${url}?action=jobs&key=${encodeURIComponent(key)}`, { redirect: "follow" });
  const data = await resp.json();
  if (data.error) throw new Error(`The HR sheet refused the request: ${data.error}`);
  return data;
}

async function sendApplication(application) {
  const { url, key } = hrCfg();
  const resp = await fetch(url, {
    method: "POST", redirect: "follow", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...application, key }),
  });
  return resp.json();
}

/** One visit through a tracked ad link (Careers.gs logClick_): the variant, its platform, and an anonymous visitor hash. */
async function logClick(click) {
  const { url, key } = hrCfg();
  await fetch(url, {
    method: "POST", redirect: "follow", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...click, action: "click", key }),
  });
}

/** The HR figures for the dashboard (Careers.gs hrStatsCached_): counts and averages only, no personal data. */
async function getHrStats() {
  const { url, key } = hrCfg();
  const resp = await fetch(`${url}?action=hr_stats&key=${encodeURIComponent(key)}`, { redirect: "follow" });
  const data = await resp.json();
  if (data.error) throw new Error(`The HR sheet refused the request: ${data.error}`);
  return data;
}

const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/** Careers on its own subdomain (careers.<domain>) lives at "/"; on any other host under "/careers". */
function basePath(req) {
  const host = String(req.headers["x-forwarded-host"] || req.headers.host || "").toLowerCase();
  return host.startsWith("careers.") ? "" : "/careers";
}

function origin(req) {
  const host = req.headers["x-forwarded-host"] || req.headers.host || "localhost";
  return `https://${host}`;
}

module.exports = { hrCfg, getJobs, sendApplication, logClick, getHrStats, esc, basePath, origin };
