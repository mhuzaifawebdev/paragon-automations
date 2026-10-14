# Log-out watcher (Cloudflare Worker, free)

Checks every watched Zadarma extension once a minute. When one goes from online to offline during the
shift, it adds a row to the **Logouts** tab of the AutoDialer-Alerts sheet. When it comes back online,
the same row gets the time and the minutes it was logged out. The "Daily summary" tab then shows
log-outs next to idle alerts and call gaps for each operator.

It exists because the other monitors run on GitHub, which starts them only about twice a day, so an
operator could log out and back in between two checks without being seen.

## Set up (once)

```
npm install -g wrangler
cd presence
wrangler login
wrangler kv namespace create PRESENCE      # copy the id it prints into wrangler.toml
wrangler secret put ZADARMA_API_KEY
wrangler secret put ZADARMA_API_SECRET
wrangler secret put GOOGLE_SERVICE_ACCOUNT_JSON   # paste the whole contents of credentials/google_service_account.json
wrangler deploy
```

The watched extensions, shift hours and sheet are in `wrangler.toml` under `[vars]`, copied from
`config.yaml`. If you add an operator there, add them here too and run `wrangler deploy` again.

## Check it works

1. Open the worker's address that `wrangler deploy` prints. It shows what the watcher last saw for
   each extension.
2. During the shift, log one extension out for 3 minutes. A row appears in "Logouts" within about a
   minute, and gets "about 3 minutes" when you log back in.

## Limits

- A log-out shorter than about a minute can fall between two checks.
- The first check of each day only records who is online; someone already offline at the start of the
  shift is reported by the pause monitor, not here.
- If Zadarma does not answer, that minute is skipped; nothing false is written.

Tests: `node presence/test_logic.mjs`
