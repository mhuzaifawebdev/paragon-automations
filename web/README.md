# Pause-monitor live dashboard (Vercel + GitHub Actions)

A hosted, auto-refreshing view of `data/extension_state.csv` / `data/alerts.csv` — the same data
`agent/pause_monitor.py` already produces, now reachable at a URL instead of a local file someone
has to regenerate by hand.

- `index.html` — static page, polls `/api/status` every 25 seconds and re-renders.
- `api/status.js` — Vercel serverless function, reads the two CSVs from GitHub's Contents API.
- The data itself is kept fresh by `.github/workflows/pause_monitor.yml`, which runs
  `agent/pause_monitor.py` on a schedule and commits the updated CSVs back to the repo.

**Not real-time.** The backend only updates roughly every 10 minutes (during the shift window in
`config.yaml`); the page polls every 25s for whatever was last committed. For a system whose own
alert threshold is "15+ minutes idle," that's live enough — it isn't a websocket push.

## Prerequisite

This repo must be pushed to GitHub first. `api/status.js` reads straight from GitHub's Contents
API, so nothing here works until the repo — and the CSVs it reads — actually exist there.

## Deploy, step by step

1. **GitHub Actions secrets** (repo → Settings → Secrets and variables → Actions → New repository
   secret), same values as your local `.env`:
   - `ZADARMA_API_KEY`, `ZADARMA_API_SECRET`
   - `ALERT_WEBHOOK_URL` (optional — only if you use a Slack/Teams webhook)
   - `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` (for email alerts)

   Once these exist, `.github/workflows/pause_monitor.yml` will run on its own schedule. You can
   trigger it once manually right away: repo → Actions → "Pause-monitor check" → Run workflow.

2. **Deploy this folder to Vercel** (from the repo root):
   ```
   cd web
   vercel deploy --prod
   ```
   or, in the Vercel dashboard: New Project → import this repo → set **Root Directory** to `web`.

3. **Vercel environment variables** (Vercel dashboard → your project → Settings → Environment
   Variables):
   - `GITHUB_OWNER` — e.g. `osamahashmidev`
   - `GITHUB_REPO` — this repo's name
   - `GITHUB_BRANCH` — usually `main`
   - `GITHUB_TOKEN` — a fine-grained GitHub PAT with **Contents: read-only** access to this repo
     only. Only required if the repo is private; skip it for a public repo (GitHub's unauthenticated
     API rate limit — 60 requests/hour — is plenty for one page polling every 25s from a handful of
     viewers, but authenticated raises that a lot if needed).

   Redeploy after adding env vars (Vercel doesn't pick them up on an already-running deployment).

## Verify

1. Open the deployed URL before the workflow has ever run: it should show "No extension data yet"
   cleanly, not an error.
2. Run the GitHub Actions workflow once (manually, via `workflow_dispatch`).
3. Refresh the page (or wait up to 25s): the 14 configured extensions should now appear with real
   status.
4. Check the warning banner never silently swallows a real problem — temporarily break
   `GITHUB_TOKEN` (if the repo is private) and confirm the page shows a visible warning instead of
   a blank table.

## Locking it down later

This page has no login and is reachable by anyone with the URL. It only shows extension numbers,
manager names, and delay reasons — not call recordings or dialed numbers — so that's an acceptable
starting point, not a finished security posture. If this needs to be private, add Vercel's
password-protection (Pro plan) or put it behind an IP allowlist.
