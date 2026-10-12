/**
 * GET /api/progress?batch=<name> -> the batch's progress.json, straight from GitHub. This is the
 * only thing web_scraper/index.html polls while a batch is running.
 */
const { getRawFile } = require("./_github");

const BATCH_RE = /^[a-z0-9-]{3,40}$/;

module.exports = async (req, res) => {
  const batch = (req.query && req.query.batch) || "";
  if (!BATCH_RE.test(batch)) {
    res.status(400).json({ status: "invalid", error: "Invalid batch name." });
    return;
  }

  try {
    const text = await getRawFile(`data_hei/${batch}/progress.json`);
    if (text === null) {
      res.status(200).json({ status: "not_found", batch });
      return;
    }
    const data = JSON.parse(text);
    // Which paid readers have a key (written every 15 minutes by resume_batches.yml). It is newer than what a
    // paused batch last recorded, so a key added while the batch waits shows up on the page.
    try {
      const readers = JSON.parse((await getRawFile("data_hei/paid_readers.json")) || "{}").fast_readers;
      if (Array.isArray(readers) && readers.length) {
        data.fast_readers = readers;
        const current = readers.find(r => r.id === data.fast_reader_id) || readers[0];
        if (data.reader_mode !== "fast") {
          data.fast_reader_id = current.id;
          data.fast_reader = current.name;
          data.fast_usd_per_institute = current.usd_per_institute;
        }
      }
    } catch {}
    res.status(200).json(data);
  } catch (e) {
    res.status(200).json({ status: "error", batch, error: `Could not read progress: ${e.message}` });
  }
};
