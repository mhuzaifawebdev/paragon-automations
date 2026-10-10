/**
 * POST { batch, token, mode } -> writes data_hei/<batch>/reader.json = {"mode": "free" | "fast"}.
 *
 * "free" (the default for every batch): only the free Gemini reader is used; when its allowance is used up the
 * batch pauses and resumes by itself. "fast": Claude Haiku (paid) reads whatever Gemini refuses, up to the cap in
 * config.yaml (max_fast_usd_per_batch). scraper2/batch_runner.py re-reads this file before every institute, so
 * a switch reaches a running batch within about a minute. A batch that is paused is started again straight away
 * when it is switched to fast - that is the whole point of the switch.
 */
const { getRawFile, upsertFile, dispatch } = require("./_github");

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
  const { batch, token, mode } = body || {};

  const accessPhrase = process.env.ACCESS_PHRASE;
  if (accessPhrase && token !== accessPhrase) {
    res.status(401).json({ error: "Wrong access phrase." });
    return;
  }
  if (!batch || !BATCH_RE.test(batch)) {
    res.status(400).json({ error: "Invalid batch name." });
    return;
  }
  if (mode !== "free" && mode !== "fast") {
    res.status(400).json({ error: "mode must be 'free' or 'fast'." });
    return;
  }

  try {
    const text = await getRawFile(`data_hei/${batch}/progress.json`);
    if (text === null) {
      res.status(404).json({ error: `No batch named "${batch}".` });
      return;
    }
    let progress = {};
    try { progress = JSON.parse(text); } catch {}

    await upsertFile(`data_hei/${batch}/reader.json`,
      JSON.stringify({ mode, set_at: new Date().toISOString() }) + "\n",
      `scrape ${batch}: reader set to ${mode} [automated]`);

    let restarted = false;
    if (mode === "fast" && progress.status === "waiting") {
      await dispatch("scrape-start", {
        batch,
        provider: progress.provider || "gemini",
        chunk_size: String(progress.chunk_size || 25),
      });
      restarted = true;
    }
    res.status(200).json({ ok: true, batch, mode, restarted });
  } catch (e) {
    res.status(500).json({ error: `Could not switch: ${e.message}` });
  }
};
