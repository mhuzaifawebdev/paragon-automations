/**
 * POST /api/apply (JSON, from the careers page's apply form) -> the HR Google Sheet (hr_cycle/Careers.gs doPost).
 * Checks size and the obvious fields here; the HR script checks everything again, de-duplicates and reads the CV.
 */
const { sendApplication } = require("./_hr");

const MAX_B64 = Math.ceil(3 * 1024 * 1024 * 4 / 3) + 8;          // a 3 MB CV

module.exports = async (req, res) => {
  if (req.method !== "POST") { res.status(405).json({ ok: false, error: "expected POST" }); return; }
  let a = req.body;
  if (typeof a === "string") { try { a = JSON.parse(a); } catch { a = {}; } }
  a = a || {};
  if (a.website) { res.status(200).json({ ok: true }); return; }  // honeypot: a bot filled the hidden field
  if (!a.name || !a.email || !a.role_id) { res.status(400).json({ ok: false, error: "Please fill in your name and email." }); return; }
  if (!a.cv_base64 || String(a.cv_base64).length > MAX_B64) {
    res.status(400).json({ ok: false, error: "Please attach a CV of at most 3 MB." }); return;
  }
  const fields = ["role_id", "name", "email", "phone", "linkedin", "source", "utm_source", "utm_campaign", "utm_content", "variant",
    "cv_name", "cv_base64"];
  const clean = Object.fromEntries(fields.map(k => [k, String(a[k] ?? "").slice(0, k === "cv_base64" ? MAX_B64 : 300)]));
  try {
    const r = await sendApplication({ ...clean, consent: a.consent === true });
    res.status(r && r.ok ? 200 : 400).json(r && typeof r === "object" ? { ok: !!r.ok, error: r.error || "" } : { ok: false, error: "No answer." });
  } catch (e) {
    res.status(502).json({ ok: false, error: "We could not receive your application right now. Please try again in a few minutes." });
  }
};
