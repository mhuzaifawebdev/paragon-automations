/**
 * Vercel serverless function: reads data/extension_state.csv and data/alerts.csv straight from
 * GitHub's Contents API (the same repo the GitHub Actions pause-monitor workflow commits to -
 * .github/workflows/pause_monitor.yml) and returns them as JSON for web/index.html to render.
 *
 * No hosted database, no polling GitHub from the browser (would need exposing a token client-side):
 * this function holds GITHUB_TOKEN server-side and the browser only ever talks to /api/status.
 *
 * Env vars (Vercel project settings -> Environment Variables):
 *   GITHUB_OWNER   - e.g. "osamahashmidev"
 *   GITHUB_REPO    - the repo this project lives in
 *   GITHUB_BRANCH  - defaults to "main"
 *   GITHUB_TOKEN   - fine-grained PAT, Contents: read-only, this repo only. Only required if the
 *                    repo is private; a public repo works without it (lower rate limit, 60/hr).
 */

function parseCsv(text) {
  if (!text) return [];
  const rows = [];
  let row = [], field = "", inQuotes = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (inQuotes) {
      if (c === '"') {
        if (text[i + 1] === '"') { field += '"'; i++; }
        else inQuotes = false;
      } else {
        field += c;
      }
    } else if (c === '"') {
      inQuotes = true;
    } else if (c === ',') {
      row.push(field); field = "";
    } else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(field); field = "";
      if (row.length > 1 || row[0] !== "") rows.push(row);
      row = [];
    } else {
      field += c;
    }
  }
  if (field !== "" || row.length) { row.push(field); rows.push(row); }
  if (!rows.length) return [];
  const header = rows[0];
  return rows.slice(1).map((r) => Object.fromEntries(header.map((h, i) => [h, r[i] ?? ""])));
}

async function fetchCsv(path, owner, repo, branch, token) {
  const url = `https://api.github.com/repos/${owner}/${repo}/contents/${path}?ref=${branch}`;
  const headers = {
    "User-Agent": "paragon-pause-monitor-status",
    "Accept": "application/vnd.github.raw",
  };
  if (token) headers.Authorization = `Bearer ${token}`;
  const resp = await fetch(url, { headers });
  if (!resp.ok) {
    if (resp.status === 404) return { rows: [], warning: null };
    return { rows: [], warning: `GitHub fetch failed for ${path}: ${resp.status}` };
  }
  return { rows: parseCsv(await resp.text()), warning: null };
}

module.exports = async (req, res) => {
  const owner = process.env.GITHUB_OWNER;
  const repo = process.env.GITHUB_REPO;
  const branch = process.env.GITHUB_BRANCH || "main";
  const token = process.env.GITHUB_TOKEN;

  if (!owner || !repo) {
    res.status(200).json({
      generated_at: new Date().toISOString(),
      extensions: [], alerts: [],
      warning: "GITHUB_OWNER / GITHUB_REPO not set in Vercel environment variables.",
    });
    return;
  }

  try {
    const [ext, alerts] = await Promise.all([
      fetchCsv("data/extension_state.csv", owner, repo, branch, token),
      fetchCsv("data/alerts.csv", owner, repo, branch, token),
    ]);
    const alertsSorted = alerts.rows.sort((a, b) => (b.timestamp || "").localeCompare(a.timestamp || ""));
    res.status(200).json({
      generated_at: new Date().toISOString(),
      extensions: ext.rows,
      alerts: alertsSorted.slice(0, 50),
      warning: ext.warning || alerts.warning || null,
    });
  } catch (e) {
    res.status(200).json({
      generated_at: new Date().toISOString(),
      extensions: [], alerts: [],
      warning: `status.js error: ${e.message}`,
    });
  }
};
