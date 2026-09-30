/**
 * GET /api/download?batch=<name> -> streams data_hei/<batch>/institutions_enriched.csv back with
 * a real filename (plain CSV, unchanged - kept as the simple/fallback path).
 *
 * GET /api/download?batch=<name>&format=json -> all five CSVs scraper/run_batch.py's merge()
 * writes (institutions_enriched, partners, campuses, contacts, needs_review), as one JSON object.
 * web_scraper/index.html's downloadAsExcel() uses this to build a real multi-tab workbook with
 * hyperlinks between tabs - the single-CSV download only ever had the hard counts
 * (partners_found: 5), never the actual partner names, because those live in partners.csv, which
 * nothing was serving before this.
 */
const { getRawFile } = require("./_github");

const BATCH_RE = /^[a-z0-9-]{3,40}$/;
const SHEETS = {
  institutions: "institutions_enriched.csv",
  partners: "partners.csv",
  campuses: "campuses.csv",
  contacts: "contacts.csv",
  needs_review: "needs_review.csv",
};

module.exports = async (req, res) => {
  const batch = (req.query && req.query.batch) || "";
  const format = (req.query && req.query.format) || "csv";
  if (!BATCH_RE.test(batch)) {
    res.status(400).send("Invalid batch name.");
    return;
  }

  try {
    if (format === "json") {
      const entries = await Promise.all(
        Object.entries(SHEETS).map(async ([key, file]) => [key, await getRawFile(`data_hei/${batch}/${file}`)])
      );
      const data = Object.fromEntries(entries);
      if (data.institutions === null) {
        res.status(404).json({ error: `No results yet for batch "${batch}".` });
        return;
      }
      res.status(200).json(data);
      return;
    }

    const csv = await getRawFile(`data_hei/${batch}/${SHEETS.institutions}`);
    if (csv === null) {
      res.status(404).send(`No results yet for batch "${batch}".`);
      return;
    }
    res.setHeader("Content-Type", "text/csv; charset=utf-8");
    res.setHeader("Content-Disposition", `attachment; filename="${batch}_enriched.csv"`);
    res.status(200).send(csv);
  } catch (e) {
    const msg = `Could not fetch results: ${e.message}`;
    if (format === "json") res.status(500).json({ error: msg });
    else res.status(500).send(msg);
  }
};
