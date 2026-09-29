/**
 * GET /api/download?batch=<name> -> streams data_hei/<batch>/institutions_enriched.csv back with
 * a real filename, so the browser's normal "Save As" flow just works. No separate download page.
 */
const { getRawFile } = require("./_github");

const BATCH_RE = /^[a-z0-9-]{3,40}$/;

module.exports = async (req, res) => {
  const batch = (req.query && req.query.batch) || "";
  if (!BATCH_RE.test(batch)) {
    res.status(400).send("Invalid batch name.");
    return;
  }

  try {
    const csv = await getRawFile(`data_hei/${batch}/institutions_enriched.csv`);
    if (csv === null) {
      res.status(404).send(`No results yet for batch "${batch}".`);
      return;
    }
    res.setHeader("Content-Type", "text/csv; charset=utf-8");
    res.setHeader("Content-Disposition", `attachment; filename="${batch}_enriched.csv"`);
    res.status(200).send(csv);
  } catch (e) {
    res.status(500).send(`Could not fetch results: ${e.message}`);
  }
};
