/**
 * POST { token } -> starts .github/workflows/rescrape.yml now (event rescrape-now) instead of waiting for the hourly
 * run: the dialer's failed leads go back to callers or are queued to be scraped again (scripts/rescrape_leads.py).
 * Refused while the last run is under 5 minutes old, so repeated clicks don't pile up runs.
 */
const { getRawFile, dispatch } = require("./_github");

const MIN_GAP_MS = 5 * 60 * 1000;

module.exports = async (req, res) => {
  if (req.method !== "POST") {
    res.status(405).json({ error: "expected POST" });
    return;
  }
  let body = req.body;
  if (typeof body === "string") {
    try { body = JSON.parse(body); } catch { body = {}; }
  }
  const accessPhrase = process.env.ACCESS_PHRASE;
  if (accessPhrase && (body || {}).token !== accessPhrase) {
    res.status(401).json({ error: "Wrong access phrase." });
    return;
  }
  try {
    const last = JSON.parse((await getRawFile("data_hei/rescrape/last_run.json")) || "{}");
    const age = last.at ? Date.now() - Date.parse(last.at) : Infinity;
    if (age < MIN_GAP_MS) {
      res.status(429).json({ error: `It last ran ${Math.round(age / 60000)} min ago; try again in a few minutes.` });
      return;
    }
    await dispatch("rescrape-now", {});
    res.status(200).json({ ok: true });
  } catch (e) {
    res.status(500).json({ error: `Could not start the rescrape: ${e.message}` });
  }
};
