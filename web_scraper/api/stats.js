/**
 * GET /api/stats -> data_hei/dashboard_stats.json (counts only: institutes scraped per day, totals, batch list).
 * GET /api/stats?view=institutes -> data_hei/institute_counts.json (campuses and partners per institute).
 * GET /api/stats?view=queue -> data_hei/queue.json (the batch queue: waiting, running, done, failed).
 * GET /api/stats?view=hr&token=<access phrase> -> the HR dashboard's figures (hr_cycle/Careers.gs, counts only).
 * Built by scripts/build_dashboard_stats.py after every chunk. Public: it holds no contact details.
 */
const { getRawFile } = require("./_github");
const { getHrStats } = require("./_hr");

module.exports = async (req, res) => {
  try {
    if ((req.query || {}).view === "hr") {
      // The HR dashboard (hr.html): counts and averages from the HR sheet, behind the access phrase.
      const phrase = process.env.ACCESS_PHRASE;
      if (phrase && (req.query || {}).token !== phrase) { res.status(401).json({ error: "Wrong access phrase." }); return; }
      res.setHeader("Cache-Control", "private, max-age=300");
      res.status(200).json(await getHrStats());
      return;
    }
    if ((req.query || {}).view === "queue") {
      const [qText, cfgText] = await Promise.all([
        getRawFile("data_hei/queue.json"),
        getRawFile("config.yaml"),
      ]);
      const q = JSON.parse(qText || '{"items":[]}');
      const num = (key) => {
        const m = (cfgText || "").match(new RegExp(`${key}\\s*:\\s*([\\d.]+)`));
        return m ? parseFloat(m[1]) : 0;
      };
      res.setHeader("Cache-Control", "s-maxage=30, stale-while-revalidate=60");
      res.status(200).json({ updated: q.updated || null, paused: q.paused || null,
        budget_cap: num("max_queue_usd_total"),
        spend_correction: num("spend_correction_usd"),
        credit: num("gemini_credit_usd"),
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
