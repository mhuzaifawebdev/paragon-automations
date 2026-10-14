/**
 * Cloudflare Worker, runs every minute (cron in wrangler.toml): records Zadarma log-outs during the shift.
 *
 * Operators were logging out of Zadarma and back in, so the idle and call-gap monitors (which GitHub runs only
 * about twice a day) never saw it. This checks every watched extension once a minute. When one goes from online
 * to offline during the shift it adds a row to the "Logouts" tab of the AutoDialer-Alerts sheet; when it comes
 * back online the same row gets the time and the minutes it was away.
 *
 * Secrets (wrangler secret put NAME): ZADARMA_API_KEY, ZADARMA_API_SECRET, GOOGLE_SERVICE_ACCOUNT_JSON.
 * Settings (wrangler.toml [vars]): SHEET_ID, SHIFT_START_UTC, SHIFT_END_UTC, EXTENSIONS (JSON {ext: name}).
 */
import { decide, inShift, minutesBetween, MD5_OF_EMPTY } from "./logic.js";

const TAB = "Logouts";
const HEADER = ["Date (UTC)", "Logged out at (UTC)", "Extension", "Operator", "Logged back in at (UTC)", "Minutes logged out"];

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(run(env, new Date()));
  },
  async fetch(request, env) {                       // GET: what the watcher currently sees (no secrets in it)
    const exts = JSON.parse(env.EXTENSIONS || "{}");
    const out = {};
    for (const ext of Object.keys(exts)) out[ext] = await env.PRESENCE.get(ext, "json");
    return new Response(JSON.stringify({ now: new Date().toISOString(), extensions: out }, null, 1),
                        { headers: { "content-type": "application/json" } });
  },
};

export async function run(env, now) {
  const exts = JSON.parse(env.EXTENSIONS || "{}");
  if (inShift(now, env.SHIFT_START_UTC || "07:00", env.SHIFT_END_UTC || "16:00")) return check(env, now, true, exts);
  // Outside the shift only log-outs still open are followed, so a row is closed even when someone returns late.
  const open = {};
  for (const [ext, name] of Object.entries(exts)) {
    const s = await env.PRESENCE.get(ext, "json");
    if (s && s.row) open[ext] = name;
  }
  if (Object.keys(open).length) return check(env, now, false, open);
}

async function check(env, now, shift, exts) {
  let sheets = null;
  for (const [ext, name] of Object.entries(exts)) {
    let online = null;
    try { online = await zadarmaOnline(env, ext); } catch (e) { console.log(`ext ${ext}: ${e.message}`); }
    const prev = await env.PRESENCE.get(ext, "json");
    const { action, next } = decide(prev, online, now, shift);
    if (!next) continue;
    try {
      if (action === "logout") {
        sheets = sheets || await Sheets.open(env);
        next.row = await sheets.append([now.toISOString().slice(0, 10), now.toISOString().slice(11, 16), ext, name, "", ""]);
      } else if (action === "login") {
        sheets = sheets || await Sheets.open(env);
        await sheets.update(prev.row, [now.toISOString().slice(11, 16), minutesBetween(prev.since, now)]);
      }
    } catch (e) {
      console.log(`ext ${ext}: sheet write failed: ${e.message}`);
      continue;                                      // state not saved, so the change is tried again next minute
    }
    await env.PRESENCE.put(ext, JSON.stringify(next));
  }
}

async function zadarmaOnline(env, ext) {
  // Same signing as agent/zadarma_client.py _get: base64 of the hex HMAC-SHA1 of method + query + md5(query).
  const method = `/v1/pbx/internal/${ext}/status/`;
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(env.ZADARMA_API_SECRET),
                                            { name: "HMAC", hash: "SHA-1" }, false, ["sign"]);
  const mac = new Uint8Array(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(method + MD5_OF_EMPTY)));
  const hex = [...mac].map(b => b.toString(16).padStart(2, "0")).join("");
  const resp = await fetch("https://api.zadarma.com" + method, { headers: { Authorization: `${env.ZADARMA_API_KEY}:${btoa(hex)}` } });
  const data = await resp.json();
  if (data.status !== "success") throw new Error(`Zadarma: ${JSON.stringify(data).slice(0, 200)}`);
  return { true: true, false: false }[String(data.is_online)] ?? null;
}

const b64url = bytes => btoa(String.fromCharCode(...new Uint8Array(bytes))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

export class Sheets {
  static async open(env) {
    const sa = JSON.parse(env.GOOGLE_SERVICE_ACCOUNT_JSON);
    const t = Math.floor(Date.now() / 1000);
    const enc = o => b64url(new TextEncoder().encode(JSON.stringify(o)));
    const claims = { iss: sa.client_email, scope: "https://www.googleapis.com/auth/spreadsheets",
                     aud: "https://oauth2.googleapis.com/token", iat: t, exp: t + 3600 };
    const unsigned = `${enc({ alg: "RS256", typ: "JWT" })}.${enc(claims)}`;
    const der = Uint8Array.from(atob(sa.private_key.replace(/-----[^-]+-----|\s/g, "")), c => c.charCodeAt(0));
    const key = await crypto.subtle.importKey("pkcs8", der, { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["sign"]);
    const jwt = `${unsigned}.${b64url(await crypto.subtle.sign("RSASSA-PKCS1-v1_5", key, new TextEncoder().encode(unsigned)))}`;
    const tok = await (await fetch("https://oauth2.googleapis.com/token", {
      method: "POST", headers: { "content-type": "application/x-www-form-urlencoded" },
      body: `grant_type=urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Ajwt-bearer&assertion=${jwt}` })).json();
    if (!tok.access_token) throw new Error("Google sign-in failed: " + JSON.stringify(tok).slice(0, 200));
    const s = new Sheets(env.SHEET_ID, tok.access_token);
    await s.ensureTab();
    return s;
  }
  constructor(id, token) { this.id = id; this.token = token; }
  async call(path, method = "GET", body) {
    const r = await fetch(`https://sheets.googleapis.com/v4/spreadsheets/${this.id}${path}`, {
      method, headers: { authorization: `Bearer ${this.token}`, "content-type": "application/json" },
      body: body ? JSON.stringify(body) : undefined });
    const data = await r.json();
    if (!r.ok) throw new Error(`Sheets ${r.status}: ${JSON.stringify(data).slice(0, 200)}`);
    return data;
  }
  async ensureTab() {
    const meta = await this.call("?fields=sheets(properties(title))");
    if (meta.sheets.some(s => s.properties.title === TAB)) return;
    await this.call(":batchUpdate", "POST", { requests: [{ addSheet: { properties: { title: TAB, gridProperties: { frozenRowCount: 1 } } } }] });
    await this.call(`/values/${TAB}!A1?valueInputOption=RAW`, "PUT", { values: [HEADER] });
  }
  async append(values) {                             // returns the sheet row number written
    const r = await this.call(`/values/${TAB}!A1:append?valueInputOption=RAW&insertDataOption=INSERT_ROWS`, "POST", { values: [values] });
    return Number((r.updates.updatedRange.match(/![A-Z]+(\d+)/) || [])[1]) || null;
  }
  async update(row, values) {
    if (!row) return;
    await this.call(`/values/${TAB}!E${row}:F${row}?valueInputOption=RAW`, "PUT", { values: [values] });
  }
}
