# Full deployment, start to finish

Everything built so far — the calling-agent pause-monitor dashboard (`web/`) and the scraper
upload page (`web_scraper/`) — depends on one thing that hasn't happened yet: **this repo needs to
exist on GitHub.** Both Vercel apps read/write through GitHub's API; neither can do anything real
until step 1 below is done. This is the complete path from where things stand right now to both
being live and usable by your team.

Time: roughly 30-40 minutes, one time. After this, day-to-day use needs none of it again.

---

## Part A — Get the repo onto GitHub

1. **Create a new empty repository on GitHub** (github.com → New repository). Don't initialize it
   with a README/license — this project already has files. Decide **public vs. private** now: a
   private repo is safer (this project has real operator names/emails in `config.yaml`, plus test
   data), but caps GitHub Actions at 2,000 free minutes/month — the cron schedules already in this
   project (`pause_monitor.yml` every 10 min, `scrape_batch.yml` on demand) were sized to fit that.
   **Recommendation: private.**

2. **Push this local project to it.** From `D:\thesocialnexus\magento_automation`:
   ```
   git init
   git add .
   git status                     # check nothing unexpected is staged before committing
   git commit -m "Initial commit"
   git branch -M main
   git remote add origin https://github.com/<your-username>/<your-repo>.git
   git push -u origin main
   ```
   Before the `git add .`, double check `.gitignore` covers `.env` and `credentials/` — this repo
   already has one set up for that, but it's worth a glance (`git status` after `add` should NOT
   show `.env` or `credentials/google_service_account.json`).

---

## Part B — One GitHub personal access token, used everywhere

Both Vercel apps and both GitHub Actions workflows need a token to read/write this repo and (for
the scraper) trigger workflow runs. One token, used in three places, is simplest:

1. **github.com → Settings → Developer settings → Fine-grained personal access tokens → Generate
   new token.**
2. **Repository access**: only this one repo.
3. **Permissions**: **Contents: Read and write**, **Actions: Read and write**.
4. Generate it, copy the value once (GitHub won't show it again).

You'll paste this same value into three places below (Vercel's `GITHUB_TOKEN` for both apps, and
GitHub's own `DISPATCH_TOKEN` secret).

---

## Part C — GitHub Actions secrets (repo → Settings → Secrets and variables → Actions)

Add these — only add the ones you actually have; blank ones just mean that feature stays inactive,
nothing breaks:

| Secret | Used by | Required for |
|---|---|---|
| `DISPATCH_TOKEN` | `scrape_batch.yml` | The scraper self-chaining between chunks — **required** for the scraper to work at all |
| `GEMINI_API_KEY` | `scrape_batch.yml` | The scraper's default free provider |
| `ANTHROPIC_API_KEY` | `scrape_batch.yml`, `enrich.yml` | Only if a batch uses the "Claude (paid)" provider |
| `ZADARMA_API_KEY`, `ZADARMA_API_SECRET` | `pause_monitor.yml` | The pause-monitor to check extension status at all |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` | `pause_monitor.yml` | Email alerts (optional — without these, alerts still log to `data/alerts.csv`, just aren't emailed) |
| `ALERT_WEBHOOK_URL` | `pause_monitor.yml` | Optional Slack/Teams webhook alert |

`DISPATCH_TOKEN` = the PAT from Part B. `ZADARMA_API_KEY`/`ZADARMA_API_SECRET` are already in your
local `.env` — same values.

---

## Part D — Deploy the pause-monitor dashboard (`web/`)

1. ```
   cd web
   vercel deploy --prod
   ```
   (Choose "Create a new project" if this is the first deploy, same as you already did locally with
   `vercel dev`.)

2. **Vercel dashboard → this project → Settings → Environment Variables**, add:
   - `GITHUB_OWNER` — your GitHub username/org
   - `GITHUB_REPO` — the repo name from Part A
   - `GITHUB_BRANCH` — `main`
   - `GITHUB_TOKEN` — the PAT from Part B

3. **Redeploy** after adding env vars (`vercel deploy --prod` again, or use the dashboard's redeploy
   button) — they don't apply to an already-running deployment.

4. **Trigger the pause-monitor once manually**: repo → Actions → "Pause-monitor check" → Run
   workflow. Wait ~30s, then open your Vercel URL — the 14 configured extensions should appear with
   real status.

---

## Part E — Deploy the scraper upload page (`web_scraper/`)

1. ```
   cd web_scraper
   vercel deploy --prod
   ```
   ("Create a new project" again — this is a separate app from `web/`.)

2. **Vercel dashboard → this project → Settings → Environment Variables**, add:
   - `GITHUB_OWNER`, `GITHUB_REPO`, `GITHUB_BRANCH` — same three values as Part D
   - `GITHUB_TOKEN` — the same PAT from Part B
   - `ACCESS_PHRASE` — any shared passphrase your team will type in to start a batch

3. **Redeploy** after adding env vars.

---

## Part F — Prove it end to end

1. Open the `web_scraper` URL. Upload `sample_data/test_10_institutes.csv` (already in this repo,
   real university websites + fake contact info — built for exactly this test).
2. Confirm the preview panel shows real mappings (Institution name → real university names).
3. Name a batch, enter your access phrase, click **Start scraping**.
4. Check the repo's **Actions** tab — a "Scrape uploaded batch" run should start within a minute.
5. Watch the page's progress bar move without reloading (polls every 10s).
6. Once it says **Done**, click **Download results** — confirm a real CSV comes back with new
   columns: verified contact, confidence rating, partner/campus counts.
7. Separately, open the `web` (pause-monitor) URL and confirm it's still showing live extension
   status, auto-refreshing every 25s.

---

## What's deliberately still not covered here

- **The actual AI dialer placing real calls** — needs `zadarma_number` (from Zadarma's "My numbers"
  panel, not in any data shared so far) and a funded Zadarma balance. Unrelated to the two web apps
  above; see the earlier discussion of what's still missing there.
- **Handing the `web_scraper` URL + access phrase to your team** — do this only after Part F passes;
  no point handing over something unverified.
- **Rotating any secret that was pasted in a chat at any point this project** (the Zadarma SIP
  password, the Google service-account key) — still recommended, independent of this deployment.
