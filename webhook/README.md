# Webhook receiver

`worker.js` is a Cloudflare Worker (free tier) that receives Vapi's
`end-of-call-report` webhook and commits the raw payload to
`data/calls/inbox/<call_id>.json` in this repo, via GitHub's Contents API.

It does no scoring or classification itself - `agent/process_call.py`, run by
`.github/workflows/process-calls.yml`, picks up files from `data/calls/inbox/`
and does that work. This keeps the Worker tiny and stable: it never needs
redeploying when the persona, scoring rubric, or outcome rule changes.

## Deploy

```
npm install -g wrangler          # if not already installed
cd webhook
wrangler login
wrangler secret put GITHUB_TOKEN          # fine-grained PAT: Contents read/write, this repo only
wrangler secret put VAPI_WEBHOOK_SECRET   # any random string; set the same value in Vapi's dashboard
```

Edit `GITHUB_OWNER` and `GITHUB_REPO` at the top of `worker.js` to match this
repository, then:

```
wrangler deploy
```

Take the resulting `https://paragon-vapi-webhook.<subdomain>.workers.dev` URL and
set it as `vapi.server_url` in `config.yaml`, and as the assistant's Server URL
(with the matching secret) in the Vapi dashboard.

## Verify it before going live

1. Send a test POST with a fake `end-of-call-report` body and confirm a file lands
   in `data/calls/inbox/` in GitHub.
2. Send a POST of a different event type (e.g. `status-update`) and confirm it's
   acknowledged (200) but nothing is committed.
3. Send a POST with the wrong `x-vapi-secret` and confirm it's rejected (401).

## Known unverified item

The exact webhook authentication header name (`x-vapi-secret` here) was not
confirmed against a live Vapi payload before this was written - check Vapi's
current Server URL / webhook security documentation, or a real received request,
before relying on it in production.
