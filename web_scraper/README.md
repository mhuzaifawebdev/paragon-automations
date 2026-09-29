# Scraper upload page (Vercel + GitHub Actions)

Hands the scraper to a non-technical team: upload an Excel/CSV list of institutes in a browser,
watch progress, download the enriched results. No terminal, no Python, no GitHub access needed
day-to-day.

- `index.html` — upload form + live progress view, polls `/api/progress` every 10s.
- `api/start-batch.js` — commits the uploaded file to the repo and triggers GitHub Actions.
- `api/progress.js` — reads the running batch's `progress.json` from GitHub.
- `api/download.js` — streams the finished batch's enriched CSV back as a real file download.
- `api/cancel-batch.js` — marks a running batch for cancellation.
- `.github/workflows/scrape_batch.yml` — does the actual scraping, one chunk (job) at a time,
  self-triggering until the batch is done.

**Not included:** publishing results into a team's own Google Sheet — that's a bigger, separate
feature (see the plan's "what this deliberately does not include"). This gives every team a
download button for the enriched CSV instead.

## Guardrails against a wrong-file mistake wasting resources

Uploading the wrong file (or a much bigger one than intended) is a real risk once this is handed to
a non-technical team, so three things catch it at different points:

1. **Before upload**: the page shows a confirmation dialog with the parsed row count, file name,
   and chosen provider before anything is sent — a chance to notice "that's not the right file"
   with zero cost incurred.
2. **At upload**: `api/start-batch.js` rejects anything over ~1,200 rows outright (this project is
   scoped for ~1,000-institute batches; a much bigger file is almost always the wrong file). It also
   refuses to run column-less garbage — `scraper2/import_batch.py` still validates headers before
   any scraping happens, so a genuinely wrong file type fails at zero scraping cost, with a real
   error message shown on the page instead of hanging.
3. **While running**: the progress view's **Cancel this batch** button writes a marker the workflow
   checks before every chunk. It can't kill a chunk already in flight, but it stops the *next* one —
   so a mistake caught mid-run costs at most one more chunk, not the whole batch.

What's still a real limit: a file with plausible-looking headers but genuinely wrong data (e.g. a
list of the right shape but the wrong institutes) will scrape successfully — there's no way to
detect "right structure, wrong content" automatically. The row-count cap and the confirmation dialog
are the two guards against that specific failure mode.

## Prerequisite

The repo must be pushed to GitHub first — every piece here reads/writes through GitHub.

## One-time setup (the admin does this once)

1. **Create a fine-grained GitHub PAT** (github.com → Settings → Developer settings → Fine-grained
   tokens → Generate new token), scoped to this repo only, with:
   - **Contents: Read and write**
   - **Actions: Read and write** (needed to trigger `repository_dispatch` — GitHub's own
     `GITHUB_TOKEN` secret cannot do this, by design, to prevent infinite-loop abuse)

2. **Add it as a GitHub Actions secret** named `DISPATCH_TOKEN` (repo → Settings → Secrets and
   variables → Actions), plus the scraping secrets the workflow already needs:
   - `ANTHROPIC_API_KEY` (only required if a team picks the "Claude (paid)" provider)
   - `GEMINI_API_KEY` (for the default free provider)

3. **Deploy this folder to Vercel**:
   ```
   cd web_scraper
   vercel deploy --prod
   ```
   or import the repo in the Vercel dashboard with **Root Directory** set to `web_scraper`.

4. **Set Vercel environment variables** (same PAT from step 1, reused here):
   - `GITHUB_OWNER`, `GITHUB_REPO`, `GITHUB_BRANCH` (usually `main`)
   - `GITHUB_TOKEN` — the same fine-grained PAT from step 1
   - `ACCESS_PHRASE` — any shared passphrase; this is not real per-user auth, just enough to stop
     a random visitor from burning your API credits if the URL leaks. Rotate it if it does.

   Redeploy after adding env vars.

5. **Give your team**: the Vercel URL, and the access phrase. That's the entire hand-off.

## Verify before handing off

1. Open the URL, upload a small (3-5 row) real CSV, start a batch.
2. Check the GitHub repo's Actions tab — a "Scrape uploaded batch" run should appear within a
   minute.
3. Watch the page's progress bar move without reloading (polls every 10s).
4. Once it says **Done**, click **Download results** and confirm a real CSV comes back with
   enriched contact data.
5. Try starting a batch with the same name again — it should be rejected with a clear "already
   exists" message, not silently overwrite the running one.
6. Try a wrong access phrase — should be rejected with 401, not silently accepted.

## If something looks stuck

- **"not found" forever**: the workflow may have failed before writing `progress.json` at all —
  check the Actions tab for a red run and read its log.
- **"error" status with a message**: `scraper2/import_batch.py` couldn't find a required column in
  the uploaded file. The message names which one; check the file's headers against the aliases in
  `scraper2/import_batch.py`'s `ALIASES` dict.
- **Stopped partway with rows still pending**: check Actions for a failed run — the self-chaining
  re-dispatch only fires after a successful chunk; a crashed chunk needs a manual `workflow_dispatch`
  retrigger (Actions → "Scrape uploaded batch" → Run workflow, with the same batch name) to resume.
