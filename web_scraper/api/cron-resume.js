/**
 * Vercel Cron -> starts the "Resume paused scraper batches" workflow on GitHub (.github/workflows/resume_batches.yml).
 *
 * GitHub's own schedule fires only every few hours instead of every 15 minutes, so a batch paused for the free AI
 * allowance could wait hours longer than needed. Vercel's scheduler runs on time and sends the start signal; the
 * work itself still runs on GitHub, for free. Schedule: web_scraper/vercel.json ("crons").
 *
 * Env vars (Vercel project settings): CRON_SECRET (any long random text; Vercel sends it with each scheduled call),
 * DISPATCH_TOKEN (GitHub token allowed to start workflows; GITHUB_TOKEN is used if not set), GITHUB_OWNER,
 * GITHUB_REPO, GITHUB_BRANCH (default main).
 */
const WORKFLOW = "resume_batches.yml";

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
