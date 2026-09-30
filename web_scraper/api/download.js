/**
 * GET /api/download?batch=<name> -> data_hei/<batch>/results.xlsx if the batch has finished (a real,
 * properly-styled multi-tab workbook with working cross-sheet hyperlinks - built server-side by
 * scraper2/build_xlsx.py using openpyxl, once .github/workflows/scrape_batch.yml sees the batch is
 * done). Falls back to plain institutions_enriched.csv if results.xlsx doesn't exist yet (an older
 * batch from before this existed, or one still running).
 *
 * GET /api/download?batch=<name>&format=json -> all five CSVs scraper/run_batch.py's merge() writes,
 * as one JSON object - kept as a simple fallback/programmatic path, not used by the page's button
 * anymore now that the server builds a real .xlsx directly.
 */
const { getRawFile, getRawBinary } = require("./_github");

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

    const xlsx = await getRawBinary(`data_hei/${batch}/results.xlsx`);
    if (xlsx !== null) {
      res.setHeader("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet");
      res.setHeader("Content-Disposition", `attachment; filename="${batch}_enriched.xlsx"`);
      res.status(200).send(xlsx);
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
