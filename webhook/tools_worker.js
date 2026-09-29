/**
 * Cloudflare Worker: Vapi live in-call tools endpoint.
 *
 * Different from worker.js (which just files away the end-of-call-report asynchronously
 * after the call ends). This one is on the live call's critical path: Vapi calls it
 * SYNCHRONOUSLY mid-conversation whenever the assistant invokes one of its declared
 * tools (message.toolCallList), waits for the response, and speaks the returned text
 * back to the caller immediately. It must reply fast (a few seconds at most) and
 * always return the {"results": [...]} envelope, even on failure - a missing response
 * for a toolCallId leaves the agent stuck mid-sentence on a real phone call.
 *
 * Workers can't run Python directly, so this delegates to agent/tools.py's actual
 * logic in one of two ways - pick one when deploying:
 *   (a) simplest to ship first: this Worker calls a small always-on HTTP endpoint
 *       (e.g. a tiny Fly.io/Render free-tier box, or a GitHub Actions-triggered
 *       Cloudflare Queue consumer) that runs agent/tools.py's handle_tool_call_list().
 *   (b) longer term: port agent/tools.py's logic to JS directly in this Worker,
 *       reading/writing the same CSVs via the GitHub Contents API (same pattern as
 *       worker.js), avoiding a second always-on process. Not done yet because it
 *       duplicates logic in two languages - only worth it once the tool set is stable.
 *
 * This file ships as a thin proxy for (a), with the request/response shape already
 * correct, so swapping to (b) later doesn't change anything Vapi sees.
 */

const TOOLS_BACKEND_URL = "REPLACE_ME";  // the small HTTP endpoint running agent/tools.py

export default {
  async fetch(request, env) {
    if (request.method !== "POST") {
      return new Response("expected POST", { status: 405 });
    }

    const providedSecret = request.headers.get("x-vapi-secret") || "";
    if (env.VAPI_WEBHOOK_SECRET && providedSecret !== env.VAPI_WEBHOOK_SECRET) {
      return new Response("unauthorized", { status: 401 });
    }

    let payload;
    try {
      payload = await request.json();
    } catch (e) {
      return emptyResults();   // malformed body - still answer every call gracefully
    }

    const msg = payload.message || payload;
    const toolCalls = msg.toolCallList || [];
    if (toolCalls.length === 0) {
      return emptyResults();
    }

    try {
      const backendResp = await fetch(TOOLS_BACKEND_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        // 8s budget: Vapi expects a fast reply since the caller is waiting live
        signal: AbortSignal.timeout(8000),
        body: JSON.stringify(payload),
      });
      if (!backendResp.ok) throw new Error(`backend ${backendResp.status}`);
      const result = await backendResp.json();
      return new Response(JSON.stringify(result), {
        headers: { "Content-Type": "application/json" },
      });
    } catch (e) {
      console.error("tools backend failed", e);
      // Fail closed but never silent: the agent still gets a spoken line for every
      // toolCallId, so the call doesn't stall - it just tells the caller a human
      // will follow up, same as agent/tools.py's own exception handling.
      return new Response(
        JSON.stringify({
          results: toolCalls.map((tc) => ({
            toolCallId: tc.id,
            result: "I'm unable to complete that right now - I'll have someone follow up.",
          })),
        }),
        { headers: { "Content-Type": "application/json" } }
      );
    }
  },
};

function emptyResults() {
  return new Response(JSON.stringify({ results: [] }), {
    headers: { "Content-Type": "application/json" },
  });
}
