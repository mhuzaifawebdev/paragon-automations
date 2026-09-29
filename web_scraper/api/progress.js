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
    res.status(200).json(data);
  } catch (e) {
    res.status(200).json({ status: "error", batch, error: `Could not read progress: ${e.message}` });
  }
};
