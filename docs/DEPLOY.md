# Deploying the enrichment scraper (free hosting)

What you get: type `pending` next to an institute in the Google Sheet, and a few minutes later its contact,
partners and campuses appear with source links and a "Method" label saying how it was read.

Two ways to host it, both free. **If you already have a VPS, use A** (simpler, no GitHub needed, and a server in
Europe may also reach sites this machine's network cannot).

## A. On your VPS (recommended)
```
git clone <your repo> /opt/paragon && cd /opt/paragon
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
mkdir -p credentials && nano credentials/google_service_account.json     # the NEW key (see step 1 below)
cp .env.example .env && nano .env                                        # SCRAPER_PROVIDER and the key it needs
crontab -e        # add:   */5 * * * *  /opt/paragon/deploy/vps/run_pending.sh
```
Every 5 minutes the script asks the Sheet for rows marked `pending`, scrapes them (6 at a time), rebuilds the
tabs and marks the rows `done`. It exits at once when nothing is pending or a run is still going (file lock).
Logs: `data/enrich.log`. In the Sheet, people only type `pending` in **Run status** (column AI) or use
**Paragon > Mark selected rows pending**; no GitHub token is needed.

Choose the model in `.env`:
- `SCRAPER_PROVIDER=gemini` + `GEMINI_API_KEY=...` : **free** (Google's free tier). Run
  `python scraper2/try_gemini.py` first: it compares Gemini with the Claude answers on the same pages so you can
  see, with numbers, if it is good enough. Not yet run live.
- `SCRAPER_PROVIDER=claude_api` + `ANTHROPIC_API_KEY=...` : about $0.03-0.11 per institute.
- `SCRAPER_PROVIDER=rules` : no AI, $0, but agreed with the model on only 2 of 9 contacts in testing; rows are
  labelled "rules-only (lower accuracy)". Not recommended for unattended use.

## B. On GitHub Actions (only if you have no server)
Free for a private repo (2,000 minutes a month, hard stop, cannot bill you). Use the **Paragon > Enrich pending
rows** menu item, which starts the workflow.

**What is tested and what is not.** The scraper, verifier, sheet builder, and the "pending" round trip to the
real Sheet were run and checked. The GitHub Action, the Apps Script button, the `claude_api` provider and the
`gemini` provider have **not** been run live (they need the repo pushed, secrets, and keys); the API providers
are covered by mocked tests only. Do steps 6 and 7 first with one row and watch it.

## 1. Rotate the Google service-account key (5 minutes, do this first)
The current key was pasted into a chat. In Google Cloud Console > IAM & Admin > Service Accounts > `paragon` >
Keys: add a new JSON key, then **delete the old one** (`924133ec...`). Keep the new file out of chat.

## 2. Put the project on GitHub as a PRIVATE repo
Staff names and emails are personal data, so the repo must be private.
```
git init && git add . && git commit -m "Paragon calling + enrichment"
gh repo create paragon-calling --private --source . --push
```
`.gitignore` already excludes `.env`, `credentials/`, and the page cache.

## 3. Add the repository secrets (Settings > Secrets and variables > Actions)
| Secret | Value |
|---|---|
| `GOOGLE_SERVICE_ACCOUNT_JSON` | the whole contents of the NEW key file |
| `ANTHROPIC_API_KEY` | from console.anthropic.com (an API key is separate from a Claude Code plan; pay per use) |
| `GEMINI_API_KEY` | optional, only if you choose the free Gemini provider |

Provider choice: Settings > Secrets and variables > Actions > **Variables** > `SCRAPER_PROVIDER` =
`claude_api` (default; about $0.05-0.10 per institute), `gemini` (free tier), or `rules` (no AI at all, free).

## 4. Share the Sheet with the service account
Share "Batch Data" with `paragon@skyline-468406.iam.gserviceaccount.com` as **Editor** (already done).

## 5. Add the button to the Sheet
Sheet > Extensions > Apps Script > paste `apps_script/Code.gs` > save > reload the Sheet. Then Project Settings >
Script properties: `GITHUB_REPO` = `yourname/paragon-calling`, `GITHUB_TOKEN` = a fine-grained GitHub token
limited to that one repo with **Actions: read and write**. (Anyone who can edit the script can read the token,
so keep edit access to the Sheet small.)

## 6. First live test (one row)
1. In Sheet1 type `pending` in **Run status** (column AI) for one institute; optionally paste its website in
   **Website override** (column AH).
2. Paragon > Enrich pending rows. Watch GitHub > Actions > "Enrich institutes".
3. When it finishes, the row's columns Q onward are filled and Run status says `done <date>`.

## 7. If something fails
- Actions log shows which step. A red "Scrape" step usually means a missing secret.
- `needs website`: the free lookups could not find the site; paste it in column AH and set `pending` again.
- `site not reachable`: the site was down, blocked bots (robots.txt is always respected), or has a DNS problem.
- The whole run is safe to repeat: results are keyed by row and rewritten, never duplicated.

## Costs, honestly
GitHub Actions, the Sheets API and hosting are free. The model is the only paid part and only with
`claude_api`: measured on this machine with Sonnet 5 through the Claude CLI, about **$0.03 to $0.11 per
institute** (up to about $0.31 for a site with 40+ partners); the API alone should be no more, but that is
not measured. `provider: rules` or `gemini` makes a run $0.

## Limits worth knowing
- About one institute in three has no site the free lookups can find; those rows say `needs website`.
- JavaScript-only pages and scanned PDFs are reported as not readable, never guessed.
- "Verified" means the quote, name, email and phone are on the cited page, checked by code. It does not
  mean the page is current.
