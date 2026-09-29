# System architecture — Paragon AI calling agent

One end-to-end system: a dialer places calls, an AI persona runs the conversation,
and every finished call is automatically scored, classified, and used to decide who
to call next. Everything is either free infrastructure (GitHub Actions, Cloudflare
Workers, a repo-committed CSV) or pay-per-use (Vapi, at the cost worked out in
`Paragon-Voice-Platform-and-Training-Workflow.pdf` — roughly $35/month at the
project's current scale).

## Diagram

```
                     ┌─────────────────────────┐
   daily cron  ───►  │ run_campaign.py          │   picks eligible institutes from
  (GH Actions)       │ (the dialer)              │   data/campaign_state.csv,
                     └────────────┬──────────────┘   respects config.yaml limits
                                  │ places call via Vapi API
                                  │ (persona + tool declarations filled
                                  │  per-institute by fill_persona.py /
                                  │  build_tools() in vapi_call.py)
                                  ▼
                     ┌─────────────────────────┐
                     │   Vapi + claude-sonnet-5 │◄──┐ live call over Paragon's
                     │   (the live conversation)│   │ Zadarma SIP trunk
                     └────────────┬──────────────┘   │
                                  │                   │ mid-call, synchronous:
                                  │                   │ book_meeting, get_institute_context,
                                  │                   │ log_callback_request, escalate_to_human
                                  │                   │
                                  │            ┌──────┴──────────────┐
                                  │            │ webhook/tools_worker.js│  →  agent/tools.py
                                  │            │ (live in-call tools)   │     (handlers, CSV writes)
                                  │            └────────────────────────┘
                                  │ call ends → end-of-call-report webhook
                                  ▼
                     ┌─────────────────────────┐
                     │ webhook/worker.js         │   Cloudflare Worker: verifies,
                     │ (Cloudflare Worker)       │   commits raw payload to
                     └────────────┬──────────────┘   data/calls/inbox/<id>.json
                                  │ git commit
                                  ▼
                     ┌─────────────────────────┐
  scheduled/on-push  │ process_call.py           │   scores (pgi-score-call skill),
  (GH Actions)  ───► │ (the processor)           │   classifies (classify-outcome
                     └────────────┬──────────────┘   skill + classify_outcome.py's
                                  │                    fixed rule), updates state
                                  ▼
              data/results/<id>.json   +   data/campaign_state.csv updated
                                  │
                                  └──── no answer? state goes back to "pending";
                                        exhausted contacts trigger
                                        find-erasmus-contacts (scraper) for
                                        the next person at that institute
```

## Components and their status

| Component | File(s) | Status |
|---|---|---|
| Numbers database | `data/numbers_clean.csv`, `data/institutions_enriched.csv` | Built. 77 institutes cleaned from the operators' sheet; enrichment partial (scraper pilot, see below). |
| Contact/network scraper (v2) | `scraper2/*`, `scraper/{fetch_pages,verify,build_sheet,run_batch}.py`, `tests/test_scraper2.py`, `tests/test_verify.py` | Built and tested: **59 offline checks pass** (43 v2 + 16 verifier). Live-tested on 27 institutes with the Sheet round trip. **Not yet run live:** the GitHub Action, the Apps Script button, the `claude_api` and `gemini` providers (mock-tested only), and anything from a VPS. Old agent-loop scraper (`scraper/run_batch.py` scrape path, `.claude/skills/find-erasmus-contacts`, `map-institution-network`) is superseded. |
| SOP scenario playbook | `data/scenarios.yaml` | Built. |
| Outcome rule | `data/classify_outcome.py` | Built and unit-tested (12 checks pass). |
| Calling persona | `prompts/persona.md` | Built: the actual system prompt (identity, 10 points, style, all 9 scenarios, disclosure line), not a description of one. |
| Persona filler | `agent/fill_persona.py` | Built and tested against both an unscraped row (falls back to raw sheet data) and a scraped row (pulls in a real multiplier-effect hook). |
| Vapi call builder | `agent/vapi_call.py` | Built and tested in `--dry-run` (produces the exact API payload, no credentials needed). Real calls need `VAPI_API_KEY`, `VAPI_PHONE_NUMBER_ID` (from the Zadarma trunk setup below), and `config.yaml`'s voice/transcriber fields filled in. |
| Zadarma-Vapi trunk setup | `agent/setup_zadarma_trunk.py` | Built and tested in `--dry-run` — produces the exact two API calls from Zadarma's own published integration guide. Confirms this is a **one-time setup**, not a Teamsale CRM setting: credentials come from `my.zadarma.com` → My PBX → Extensions. |
| Campaign state | `agent/state.py`, `data/campaign_state.csv` | Built and tested (eligibility limits, no-answer requeueing, escalation, persistence all verified). |
| Dialer loop | `agent/run_campaign.py` | Built and tested in `--dry-run`. |
| Scoring skill | `.claude/skills/pgi-score-call` | Built. |
| Outcome-extraction skill | `.claude/skills/classify-outcome` | Built. |
| Call simulator (for training) | `.claude/skills/sim-receptionist` | Built, not yet run — use it for training step 3 before spending on real calls. |
| Post-call processor | `agent/process_call.py` | Built and **verified end to end** against a synthetic call: transcript saved, scored (8.5/10), classified (`engagement_appointment`), state updated, inbox file consumed. |
| Acceptance tests | `tests/labelled_calls/*.json`, `tests/run_acceptance_tests.py` | Built. **3/3 fixtures pass** (a hot lead, a flat refusal, a no-answer) — run this after any change to the persona, scenarios, or either skill. |
| Webhook receiver (post-call) | `webhook/worker.js` | Written, **not deployed** — needs a Cloudflare account, this repo pushed to GitHub, and a GitHub PAT. |
| Live in-call tools | `agent/tools.py`, `webhook/tools_worker.js`, `config.yaml`'s `vapi.tools` | Built and **tested against synthetic payloads** — all 4 tools (`book_meeting`, `get_institute_context`, `log_callback_request`, `escalate_to_human`) verified locally, including an unknown-tool-name and a missing-row_id case failing safely rather than crashing. `book_meeting` is an honest stub (never claims a slot is confirmed) until Dr. Nadia's calendar access exists. The Worker itself proxies to a small always-on backend that isn't chosen/deployed yet (Workers can't run Python) — see `webhook/tools_worker.js`'s header comment for the two deployment options. Added after reviewing a working reference n8n integration that confirmed Vapi's real live tool-call payload shape (`message.toolCallList` in, `{results:[...]}` out). |
| Automation | `.github/workflows/{process-calls,run-campaign}.yml` | Written, **not active** — needs the repo on GitHub and secrets configured; `run-campaign.yml`'s scheduled trigger is deliberately gated behind manual dispatch until live-call training is signed off. |

## Data contracts (unchanged from the developer handover, now implemented)

- **Numbers DB row** → `data/numbers_clean.csv` / `institutions_enriched.csv` columns, as built.
- **Per-call result** → exactly the shape in `data/results/<call_id>.json`, produced by `process_call.py`: `call_id, row_id, answered, ended_reason, score {points, total, evidence}, outcome_facts {interested, student_arrival_months, meeting_booked}, outcome`.
- **Campaign state row** → `data/campaign_state.csv`, one row per institute: current contact, attempt counts, status, escalation level.

## What "production ready" means here, precisely

Every piece that can be built and tested **without live external accounts** is built
and has been tested — the persona, the filling logic, the dialer's eligibility and
state logic, the entire scoring/classification pipeline (proven against real `claude
-p` runs on 3 labelled fixtures), and the webhook receiver's logic (code-complete,
tested by inspection, not yet deployed since it needs a live Cloudflare + GitHub
setup). What remains is **account setup and one supervised trial**, not further
software development:

## Go-live checklist

1. **Push this repo to GitHub** (it is not a git repository yet). `run-campaign.yml` / `process-calls.yml` need a real remote to commit to.
2. **Get a Vapi account and API key.** Set `VAPI_API_KEY`, `VAPI_PHONE_NUMBER_ID` (or the BYO-SIP credential) as GitHub Actions secrets and in a local `.env` for manual testing.
3. **Connect Zadarma to Vapi** — confirmed against Zadarma's own published guide (`zadarma.com/en/support/instructions/vapiai/`), not guessed:
   a. `my.zadarma.com` → My PBX → Extensions: note your extension (e.g. `1234-100`), generate its SIP password, note your Zadarma number.
   b. Fill `config.yaml`'s `vapi.zadarma` block; put `ZADARMA_SIP_PASSWORD` and `VAPI_API_KEY` in `.env`.
   c. Run `python agent/setup_zadarma_trunk.py` — registers the trunk and number with Vapi, prints the `phoneNumberId` to put in `.env` as `VAPI_PHONE_NUMBER_ID`.
   d. Back in Zadarma Extensions: enable Call forwarding and voicemail, forward to External server (SIP URI): `+<your-number>@sip.vapi.ai`.
   This is NOT part of the Teamsale CRM — it's in the main Zadarma account, a separate panel.
4. **Pick a TTS voice and confirm the STT transcriber**, fill those into `config.yaml`.
5. **Deploy both webhooks** (`webhook/README.md`): the post-call receiver (`worker.js`) and the live-tools endpoint (`tools_worker.js`, which also needs its small backend for `agent/tools.py` chosen and deployed). Point Vapi's Server URL and each tool's `server.url` at them; set `config.yaml`'s `server_url` and `tools_server_url` to match.
6. **Get the brand card content and the qualification question list** from the client — both are referenced by the persona but not yet supplied; the persona currently flags this openly on a live call rather than inventing content.
7. **Run the training workflow's steps 3–7** (dry-run via `sim-receptionist`, 5 controlled calls, ~20 shadow calls with a human listening, iterate) before removing the `run-campaign.yml` manual-dispatch guard.
8. **Confirm AI-disclosure and recording-consent wording** with the client's legal advisor; the persona's disclosure line is a placeholder pending that confirmation.

## Enrichment scraper v2 (scraper2/): find, read, verify, publish

```
Sheet row -> find website -> crawl pages (parallel, polite) -> numbered evidence lines -> ONE model call -> verify -> Sheet
             email/Wikidata/    keyword + sitemap +             the model answers with       (line numbers)   scraper/    linked tabs,
             memo/search        per-type page slots             LINE NUMBERS only                          verify.py   Method label
```
- The model never writes a quote, email or phone: it points at numbered lines and code copies the text, so quotes are
  verbatim by construction; `scraper/verify.py` then re-checks every claim against the cited page.
- Provider is a setting (`claude_api`, `claude_cli`, `gemini`, `rules`); a failed model call saves nothing.
- Measured (24 Sep, Claude via CLI): 17 institutes in 119 s with 6 in parallel (7 s each effective), about $0.07 each;
  the first version took ~100 s and $0.56 each. Rules-only agreed with the model on 2 of 9 contacts, so it is not
  used unattended. Sites unreachable from one network (DNS/timeouts) are reported, never guessed.
- Run on a VPS with `deploy/vps/run_pending.sh` (cron), or GitHub Actions. See `docs/DEPLOY.md`.

## Open issues (state plainly, do not hide)

- **Scraper coverage:** about 1 institute in 3 in testing could not be read (DNS/timeouts from this network, robots.txt, or no findable website); rows say why. A VPS in Europe may reach more sites; unverified. Model quality on the free Gemini tier is unmeasured until a key is supplied (`scraper2/try_gemini.py`).
- **Vapi webhook field names** (`message.call.metadata`, the auth header) are taken from Vapi's own documentation, not a live payload — `process_call.py` and `worker.js` are written defensively (fallback phone-number matching, a `needs_review/` folder for anything unmapped) precisely because of this, but confirm against a real test call in step 7 above.
- **No git history yet** — nothing in this project has been committed. That's a decision for the user, not something this build should have done unasked.
