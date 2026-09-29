# Paragon Global — AI Erasmus+ calling agent

Read `ARCHITECTURE.md` first for the full system diagram and component status.
This file is the quickstart for running and testing things locally.

## Setup

```
pip install pyyaml lxml pypdf
npm install -g @anthropic-ai/claude-code    # needed for agent/process_call.py and the scraper
cp .env.example .env                        # fill in VAPI_API_KEY etc. once you have them
```

## Try it without spending anything or needing any credentials

```bash
# See the exact prompt a given institute's call would use:
python agent/fill_persona.py r035

# See the exact Vapi API payload a call would send (no API key needed):
python agent/vapi_call.py r035 --dry-run

# Exercise the whole dialer loop against the campaign state, placing nothing:
python agent/run_campaign.py --dry-run --limit 5

# Score and classify a call transcript (this one does call `claude -p`, at normal API cost):
python agent/process_call.py data/calls/inbox/<file>.json

# Test a live in-call tool (book_meeting, get_institute_context, log_callback_request,
# escalate_to_human) against a synthetic Vapi toolCallList payload - no live call needed:
python agent/tools.py tests/tool_calls/book_meeting_sample.json

# Run the full acceptance test suite (3 labelled fixtures - a hot lead, a refusal, a no-answer):
python tests/run_acceptance_tests.py

# Clean and re-derive the numbers database from the raw operator sheet:
python data/clean_sheet.py

# Check the outcome-classification rule's own unit tests:
python data/classify_outcome.py

# Find a contact / partner network for one institute (costs a `claude -p` call):
python scraper/run_batch.py --rows r035
```

## The pieces, in the order data flows through them

1. `data/source_calls_sheet.csv` → `data/clean_sheet.py` → `data/numbers_clean.csv`, `data/attempts.csv`
2. `scraper/run_batch.py` (uses the `find-erasmus-contacts` / `map-institution-network` skills) → `data/institutions_enriched.csv`, `data/partners.csv`, `data/campuses.csv`
3. `agent/fill_persona.py` (reads `prompts/persona.md` + the numbers DB) → a filled system prompt
4. `agent/run_campaign.py` (reads `data/campaign_state.csv`, `config.yaml`) → places calls via `agent/vapi_call.py`
5. Vapi places the call, live turns run on `claude-sonnet-5`; mid-call, the agent can invoke a tool (`config.yaml`'s `vapi.tools`) → `webhook/tools_worker.js` → `agent/tools.py`, which answers synchronously so the agent can speak the result
6. `webhook/worker.js` receives the end-of-call-report → `data/calls/inbox/<id>.json`
7. `agent/process_call.py` (uses the `pgi-score-call` / `classify-outcome` skills + `data/classify_outcome.py`'s rule) → `data/results/<id>.json`, updates `data/campaign_state.csv`

## Before going live

See `ARCHITECTURE.md`'s "Go-live checklist" — pushing this to GitHub, Vapi/Zadarma
credentials, the brand card, and the supervised training rounds are all still open.
