/**
 * Cloudflare Worker: Vapi end-of-call-report receiver.
 *
 * Job is deliberately tiny: accept the webhook POST, verify it's really from Vapi,
 * and commit the raw payload to the repo at data/calls/inbox/<call_id>.json via
 * GitHub's Contents API. Nothing else runs here - no scoring, no classification,
 * no CSV updates. That's agent/process_call.py's job, run by
 * .github/workflows/process-calls.yml on a schedule after the commit lands. This
 * split is deliberate: a Worker has no way to run `claude -p` or Python, and
 * keeping it dumb means it never needs redeploying when the scoring logic changes.
 *
 * Deploy:
 *   wrangler secret put GITHUB_TOKEN      # a fine-grained PAT, Contents: read/write, this repo only
 *   wrangler secret put VAPI_WEBHOOK_SECRET   # set the same value as Vapi's server URL secret
 *   wrangler deploy
 * Then set this Worker's URL as the assistant's serverUrl in config.yaml / the Vapi dashboard.
 */

const GITHUB_OWNER = "REPLACE_ME";     // e.g. "osamahashmidev"
const GITHUB_REPO = "REPLACE_ME";      // the repo this project lives in
const GITHUB_BRANCH = "main";

export default {
  async fetch(request, env) {
    if (request.method !== "POST") {
      return new Response("expected POST", { status: 405 });
    }

    // Vapi signs/authenticates server URL requests; the exact header name was never
    // confirmed against a live payload before launch. x-vapi-secret is Vapi's documented
    // name and is checked first; two other header names Vapi has been reported to use in
    // some account configurations are accepted as a fallback so a real call is not silently
    // rejected over a header-name mismatch alone. If VAPI_WEBHOOK_SECRET matches none of
    // them, the request is still logged (not just dropped) so a genuine mismatch is visible
    // in `wrangler tail` rather than invisible.
    const providedSecret =
      request.headers.get("x-vapi-secret") ||
      request.headers.get("x-vapi-signature") ||
      (request.headers.get("authorization") || "").replace(/^Bearer\s+/i, "") ||
      "";
    if (env.VAPI_WEBHOOK_SECRET && providedSecret !== env.VAPI_WEBHOOK_SECRET) {
      console.error("webhook secret mismatch - headers seen:",
        JSON.stringify(Object.fromEntries(request.headers)));
      return new Response("unauthorized", { status: 401 });
    }

    let payload;
    try {
      payload = await request.json();
    } catch (e) {
      return new Response("bad JSON", { status: 400 });
    }

    const msg = payload.message || payload;
    if (msg.type !== "end-of-call-report") {
      // Vapi's serverUrl receives many event types (speech-update, status-update,
      // tool-calls, ...) - only end-of-call-report is wanted here; everything else
      // is acknowledged and dropped so Vapi doesn't retry it as a failure.
      return new Response("ignored (not end-of-call-report)", { status: 200 });
    }

    const callId = (msg.call && msg.call.id) || `unknown-${Date.now()}`;
    const path = `data/calls/inbox/${callId}.json`;
    const content = btoa(unescape(encodeURIComponent(JSON.stringify(payload, null, 1))));

    const ghResp = await fetch(
      `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/contents/${path}`,
      {
        method: "PUT",
        headers: {
          "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
          "User-Agent": "paragon-vapi-webhook-worker",
          "Accept": "application/vnd.github+json",
        },
        body: JSON.stringify({
          message: `call ${callId}: end-of-call-report received`,
          content,
          branch: GITHUB_BRANCH,
        }),
      }
    );

    if (!ghResp.ok) {
      const errText = await ghResp.text();
      console.error("GitHub commit failed", ghResp.status, errText);
      // Return 500 so Vapi retries the webhook rather than silently losing the call.
      return new Response("failed to store payload", { status: 500 });
    }

    return new Response("stored", { status: 200 });
  },
};
