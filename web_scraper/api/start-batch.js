/**
 * POST { batch, csv, provider, chunk_size, token } -> commits the uploaded CSV to
 * data_hei/uploads/<batch>.csv and fires the "scrape-start" repository_dispatch event that
 * .github/workflows/scrape_batch.yml listens for. See web_scraper/README.md for setup.
 */
const { getRawFile, putFile, dispatch } = require("./_github");

const BATCH_RE = /^[a-z0-9-]{3,40}$/;
const MAX_CSV_BYTES = 3 * 1024 * 1024; // generous for a 1000-row contact list; Vercel's own body limit applies first
const MAX_ROWS = 1200; // this project is scoped for ~1000-institute batches; a much bigger file is
                        // almost always the wrong file, not a bigger legitimate job - reject it loudly
                        // here instead of letting hundreds of chunks silently burn Actions minutes and
                        // AI cost before anyone notices.

function countDataRows(csv) {
  // Rough on purpose: a full quote-aware parse isn't needed just to bound "how many rows is this
  // roughly" - undercounting a little on a huge accidental file only makes this check more, not
  // less, likely to catch it.
  return Math.max(0, csv.split(/\r\n|\r|\n/).filter(l => l.trim() !== "").length - 1);
}

module.exports = async (req, res) => {
  if (req.method !== "POST") {
    res.status(405).json({ error: "expected POST" });
    return;
  }

  let body = req.body;
  if (typeof body === "string") {
    try { body = JSON.parse(body); } catch { body = {}; }
  }
  const { batch, csv, provider, chunk_size, token } = body || {};

  const accessPhrase = process.env.ACCESS_PHRASE;
  if (accessPhrase && token !== accessPhrase) {
    res.status(401).json({ error: "Wrong access phrase." });
    return;
  }

  if (!batch || !BATCH_RE.test(batch)) {
    res.status(400).json({ error: "Batch name must be 3-40 characters: lowercase letters, numbers, hyphens only." });
    return;
  }
  if (!csv || typeof csv !== "string" || !csv.trim()) {
    res.status(400).json({ error: "No file content received." });
    return;
  }
  if (Buffer.byteLength(csv, "utf8") > MAX_CSV_BYTES) {
    res.status(400).json({ error: "File too large (over 3MB as CSV) - split it into smaller batches." });
    return;
  }
  const rowCount = countDataRows(csv);
  if (rowCount > MAX_ROWS) {
    res.status(400).json({
      error: `This file looks like it has about ${rowCount} rows - over the ${MAX_ROWS} limit for one batch. ` +
             `If that's really intended, split it into smaller files and upload each as its own batch. ` +
             `If that number looks way too high, you may have the wrong file.`,
    });
    return;
  }
  const allowedProviders = ["gemini", "claude_api", "rules"];
  const p = allowedProviders.includes(provider) ? provider : "gemini";
  const chunkSize = Math.max(5, Math.min(100, parseInt(chunk_size, 10) || 25));

  try {
    const already = await getRawFile(`data_hei/${batch}/numbers_clean.csv`);
    if (already !== null) {
      res.status(409).json({ error: `Batch "${batch}" already exists. Pick a different name, or use "Check a batch" to see its progress.` });
      return;
    }

    await putFile(`data_hei/uploads/${batch}.csv`, csv, `scrape ${batch}: uploaded [automated]`);
    await dispatch("scrape-start", { batch, provider: p, chunk_size: String(chunkSize) });

    res.status(200).json({ ok: true, batch, provider: p, chunk_size: chunkSize });
  } catch (e) {
    res.status(500).json({ error: `Could not start the batch: ${e.message}` });
  }
};
