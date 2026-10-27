/** GET /careers/sitemap.xml (or /sitemap.xml on careers.<domain>): every open job page, for Google. */
const { getJobs, esc, basePath, origin } = require("./_hr");

module.exports = async (req, res) => {
  try {
    const data = await getJobs();
    const base = `${origin(req)}${basePath(req)}`;
    const urls = [`<url><loc>${esc(base || origin(req))}/</loc></url>`].concat((data.jobs || []).map(j =>
      `<url><loc>${esc(base)}/job/${encodeURIComponent(j.id)}</loc><lastmod>${esc(j.posted)}</lastmod></url>`));
    res.setHeader("Content-Type", "application/xml; charset=utf-8");
    res.setHeader("Cache-Control", "s-maxage=3600");
    res.status(200).send(`<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">${urls.join("")}</urlset>`);
  } catch (e) {
    res.status(503).send("unavailable");
  }
};
