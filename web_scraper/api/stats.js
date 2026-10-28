/**
 * GET /api/stats -> data_hei/dashboard_stats.json (counts only: institutes scraped per day, totals, batch list).
 * GET /api/stats?view=institutes -> data_hei/institute_counts.json (campuses and partners per institute).
 * GET /api/stats?view=queue -> data_hei/queue.json (the batch queue: waiting, running, done, failed).
 * Built by scripts/build_dashboard_stats.py after every chunk. Public: it holds no contact details.
 */
const { getRawFile } = require("./_github");

module.exports = async (req, res) => {
  try {
    if ((req.query || {}).view === "queue") {
      const [qText, cfgText] = await Promise.all([
        getRawFile("data_hei/queue.json"),
        getRawFile("config.yaml"),
      ]);
      const q = JSON.parse(qText || '{"items":[]}');
      let budgetCap = 0;
      if (cfgText) {
        const m = cfgText.match(/max_queue_usd_total\s*:\s*([\d.]+)/);
        if (m) budgetCap = parseFloat(m[1]);
      }
      res.setHeader("Cache-Control", "s-maxage=30, stale-while-revalidate=60");
      res.status(200).json({ updated: q.updated || null, paused: q.paused || null,
        budget_cap: budgetCap,
        items: (q.items || []).map(({ file_id, md5, ...rest }) => rest) });
      return;
    }
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
