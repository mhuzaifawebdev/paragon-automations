/**
 * GET /api/stats -> data_hei/dashboard_stats.json (counts only: institutes scraped per day, totals, batch list).
 * GET /api/stats?view=institutes -> data_hei/institute_counts.json (campuses and partners per institute).
 * Built by scripts/build_dashboard_stats.py after every chunk. Public: it holds no contact details.
 */
const { getRawFile } = require("./_github");

module.exports = async (req, res) => {
  try {
    const institutes = (req.query || {}).view === "institutes";
    const text = await getRawFile(institutes ? "data_hei/institute_counts.json" : "data_hei/dashboard_stats.json");
    res.setHeader("Cache-Control", "s-maxage=60, stale-while-revalidate=300");
    if (text === null) {
      res.status(200).json(institutes ? { institutes: [] } : { totals: {}, days: [], batches: [] });
      return;
    }
    res.status(200).json(JSON.parse(text));
  } catch (e) {
    res.status(200).json({ error: `Could not read the statistics: ${e.message}`, totals: {}, days: [], batches: [] });
  }
};
