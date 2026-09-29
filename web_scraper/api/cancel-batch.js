/**
 * POST { batch, token } -> writes data_hei/<batch>/CANCELLED, a marker
 * .github/workflows/scrape_batch.yml checks before running (and before re-dispatching) each
 * chunk. Doesn't stop a chunk already in progress (there's no way to kill a running GitHub Actions
 * job from here without more infra than this project needs) - it stops the NEXT one, so at most
 * one more chunk's worth of scraping happens after cancelling.
 */
const { putFile } = require("./_github");

const BATCH_RE = /^[a-z0-9-]{3,40}$/;

module.exports = async (req, res) => {
  if (req.method !== "POST") {
    res.status(405).json({ error: "expected POST" });
    return;
  }

  let body = req.body;
  if (typeof body === "string") {
    try { body = JSON.parse(body); } catch { body = {}; }
  }
  const { batch, token } = body || {};

  const accessPhrase = process.env.ACCESS_PHRASE;
  if (accessPhrase && token !== accessPhrase) {
    res.status(401).json({ error: "Wrong access phrase." });
    return;
  }
  if (!batch || !BATCH_RE.test(batch)) {
    res.status(400).json({ error: "Invalid batch name." });
    return;
  }

  try {
    await putFile(`data_hei/${batch}/CANCELLED`, `cancelled via web_scraper at ${new Date().toISOString()}\n`,
      `scrape ${batch}: cancelled [automated]`);
    res.status(200).json({ ok: true, batch });
  } catch (e) {
    res.status(500).json({ error: `Could not cancel: ${e.message}` });
  }
};
