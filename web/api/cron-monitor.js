/**
 * Vercel Cron -> starts the pause-monitor workflow on GitHub (.github/workflows/pause_monitor.yml).
 *
 * GitHub's own schedule fires only a few times a day instead of every 10 minutes, so Vercel's scheduler (which
 * runs on time) sends the start signal instead. The monitor itself still runs on GitHub, for free; this function
 * only makes one request. Schedule: web/vercel.json ("crons").
 *
 * Env vars (Vercel project settings):
 *   CRON_SECRET     any long random text; Vercel sends it with every scheduled call, so nobody else can trigger this
 *   DISPATCH_TOKEN  GitHub token allowed to start workflows on this repo (Actions: read and write);
 *                   GITHUB_TOKEN is used if this is not set
 *   GITHUB_OWNER, GITHUB_REPO, GITHUB_BRANCH (default main)
 */
const WORKFLOW = "pause_monitor.yml";

module.exports = async (req, res) => {
  const secret = process.env.CRON_SECRET;
  if (!secret || req.headers.authorization !== `Bearer ${secret}`) {
    res.status(401).json({ error: "Not allowed." });
    return;
  }
  const owner = process.env.GITHUB_OWNER, repo = process.env.GITHUB_REPO;
  const token = process.env.DISPATCH_TOKEN || process.env.GITHUB_TOKEN;
  if (!owner || !repo || !token) {
    res.status(500).json({ error: "GITHUB_OWNER, GITHUB_REPO and DISPATCH_TOKEN must be set." });
    return;
  }
  const resp = await fetch(`https://api.github.com/repos/${owner}/${repo}/actions/workflows/${WORKFLOW}/dispatches`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, Accept: "application/vnd.github+json", "User-Agent": "paragon-cron" },
    body: JSON.stringify({ ref: process.env.GITHUB_BRANCH || "main" }),
  });
  if (resp.status !== 204) {
    res.status(502).json({ error: `GitHub refused to start ${WORKFLOW}: ${resp.status} ${(await resp.text()).slice(0, 300)}` });
    return;
  }
  res.status(200).json({ ok: true, started: WORKFLOW, at: new Date().toISOString() });
};
