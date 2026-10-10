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

## Optional: deliver results to Google Drive automatically

When this is set up, every finished batch's Excel file is placed in a Google Drive folder by itself,
named `<batch name> results.xlsx`, and the upload page shows an "Open in Google Drive" link. A batch
that is run again replaces its own file. If Drive is unreachable the batch still finishes and the
download button still works; the page says why delivery failed.

### Company Google Workspace (Drive shows "Shared drives" in the left menu)

1. In Google Drive, open **Shared drives** and create one (for example "Scraper results"), or open
   an existing one.
2. Click the shared drive's name → **Manage members** → add this address as **Content manager**:
   `paragon@skyline-468406.iam.gserviceaccount.com`
   (the project's robot account - the same one that already writes the Google Sheets).
3. Open the shared drive, or a folder inside it, where the files should land. Copy the last part of
   the address in the browser: in `https://drive.google.com/drive/folders/0AAbCdEf...` the folder ID
   is `0AAbCdEf...`.
4. On GitHub: repo → Settings → Secrets and variables → Actions → **New repository secret**, named
   `DRIVE_FOLDER_ID`, with that ID as the value. `GOOGLE_SERVICE_ACCOUNT_JSON` must already exist
   there (it does if the Google Sheets features work).
5. The Google Drive API must be switched on for the robot's Google Cloud project: console.cloud.google.com
   → project `skyline-468406` → APIs & Services → Library → "Google Drive API" → Enable.

It has to be a **shared drive**, not a folder in someone's own "My Drive" shared with the robot:
a robot account has no storage of its own, and Google refuses the upload.

### Personal Gmail (no "Shared drives")

The tool uploads as the Drive's owner instead, using a permission they grant once. It can only see
files it created itself.

1. At console.cloud.google.com create a project, enable the **Google Drive API**, and under
   APIs & Services → OAuth consent screen choose External, fill in the app name, and **publish** it
   ("In production") - an app left in "Testing" loses its permission every 7 days.
2. Credentials → Create credentials → OAuth client ID → **Desktop app**. Note the client ID and secret.
3. On any computer: `pip install google-auth-oauthlib`, then
   `python scripts/drive_authorize.py --client-id ... --client-secret ...`. The Drive's owner signs
   in and clicks Allow.
4. Save the three values it prints as GitHub secrets: `GOOGLE_OAUTH_CLIENT_ID`,
   `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_REFRESH_TOKEN`.

Files go to a folder the tool creates, "Paragon Scraper Results".

## Large batches (1,000 institutes): runs by itself over several days

Nothing extra to set up. Upload the file and leave it.

- The batch is read 25 institutes at a time. Calls to the free AI reader (Gemini) are spaced out
  automatically when it starts refusing.
- When the day's free allowance is used up, the page shows **waiting** with the time the batch
  resumes. `.github/workflows/resume_batches.yml` checks every 15 minutes and starts it again; if
  the allowance is still not back it waits another 30 minutes.
- The Excel file in Google Drive is refreshed every 100 institutes and at every pause, so the
  results so far are always there. The same file is replaced each time.
- An institute that fails 3 times is saved as "Could not be read after 3 attempts" so the batch
  always finishes.

**Free / fast switch (on the batch's progress card).** Every batch starts in free mode: only
Gemini reads, and Claude is used only for the personal-email searches (cap
`max_paid_usd_per_batch`, $3). The button "Continue now with Claude Haiku (paid)" switches that one
batch to fast mode: Haiku reads whatever Gemini refuses, about 6 cents per institute, up to
`max_fast_usd_per_batch` ($30, in `config.yaml`). Gemini is still tried first, so the batch is back
on free reading by itself when Gemini answers again. "Back to free mode" switches it off. The
choice is stored in `data_hei/<batch>/reader.json`.

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
- **"waiting" status**: not stuck. The free AI allowance ran out; the page shows when it resumes.
  If the time has passed by more than 30 minutes, check Actions → "Resume paused scraper batches"
  is running on schedule and that the `DISPATCH_TOKEN` secret has not expired.
- **Stopped partway with rows still pending**: check Actions for a failed run — the self-chaining
  re-dispatch only fires after a successful chunk; a crashed chunk needs a manual `workflow_dispatch`
  retrigger (Actions → "Scrape uploaded batch" → Run workflow, with the same batch name) to resume.
