# Putting the Scraper and the Auto-Dialer on Paragon's Vercel

Two Vercel projects, both from this repository, both on Paragon's Vercel Pro account:

| Project folder | Address | What it is |
|---|---|---|
| `web_scraper` | leads.paragoninnovation.net | Scraper: upload a batch, follow it, dashboard |
| `web` | dialer.paragoninnovation.net | Auto-Dialer Monitor: live status, daily report, alerts |

The heavy work (scraping, the monitor checks) keeps running on GitHub Actions, which is free for this public
repository. Vercel only serves the pages: no scheduled jobs, and a project is rebuilt only when its own folder
changes (`ignoreCommand` in each `vercel.json`), so data commits cost no build minutes. The GitHub jobs are started
on time by a free Google Apps Script timer (`apps_script/Scheduler.gs`), because GitHub's own schedule fires only
every few hours. Rabhost is used only for the two DNS records.

## 1. Create the two projects in Paragon's Vercel

From a computer logged in to Paragon's Vercel account (`vercel login`, pick the Paragon team):

```
cd web_scraper
vercel link          # create a new project, e.g. "paragon-leads", in the Paragon team
vercel deploy --prod
cd ../web
vercel link          # new project, e.g. "paragon-dialer"
vercel deploy --prod
```

Or in the Vercel dashboard: Add New > Project > import this GitHub repository twice, with Root Directory
`web_scraper` for one and `web` for the other. Importing from GitHub also redeploys automatically on every push.

## 2. Environment variables (Project > Settings > Environment Variables)

Both projects:

| Name | Value |
|---|---|
| `GITHUB_OWNER` | `mhuzaifawebdev` |
| `GITHUB_REPO` | `paragon-automations` |
| `GITHUB_BRANCH` | `main` |
| `GITHUB_TOKEN` | GitHub fine-grained token for this repository. Scraper needs Contents: read and write; Auto-Dialer only read |

Scraper project only:

| Name | Value |
|---|---|
| `ACCESS_PHRASE` | the phrase people type to start a batch |

After adding or changing variables, redeploy (Deployments > ... > Redeploy).

## 3. The two addresses (Project > Settings > Domains)

1. In the scraper project add `leads.paragoninnovation.net`; in the auto-dialer project add
   `dialer.paragoninnovation.net`.
2. Vercel shows the DNS record each one needs, normally a **CNAME**:
   - Name `leads`, value as shown by Vercel (usually `cname.vercel-dns.com`)
   - Name `dialer`, same kind of value
3. Add those two records in Rabhost: cPanel > Zone Editor > paragoninnovation.net > + CNAME Record.
   (If the domain's DNS is managed elsewhere, for example Cloudflare, add them there instead.)
4. Within minutes to a few hours Vercel shows the domains as valid and issues HTTPS certificates itself.

Only these two names get new records. The main website and email of paragoninnovation.net are not touched.

## 4. Scheduled jobs (Google Apps Script, free)

| Schedule (UTC) | Starts on GitHub |
|---|---|
| every 10 minutes, 07:00-16:59 | `pause_monitor.yml` (offline/idle check and call gaps) |
| every 15 minutes | `resume_batches.yml` (restarts paused batches when due) |

Install once: AutoDialer-Alerts sheet > Extensions > Apps Script > add a file with `apps_script/Scheduler.gs`;
Project Settings > Script properties: `GITHUB_DISPATCH_TOKEN` (classic GitHub token with "repo" and "workflow");
run `setupScheduler`. The GitHub schedules stay as a backup; GitHub's concurrency settings stop two runs overlapping.

## 5. Check it works

1. Open both addresses: the pages load, the scraper dashboard shows the figures, the dialer shows extensions.
2. Apps Script editor: run `startResumeBatches` once by hand: within a minute a new run of "Resume paused
   scraper batches" appears in GitHub > Actions.
3. Next working day: GitHub > Actions > Pause-monitor check shows a run about every 10 minutes during the shift.
4. After a week: Vercel > Usage: Build CPU Minutes and Functions should barely move.

## 6. Turn off the old projects

Once the new addresses work, remove the old Vercel projects on the developer's account (`paragon-scrapper`,
`web`) so there is only one copy of each page.
