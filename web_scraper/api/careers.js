/**
 * The public careers page: GET /careers (all open roles) and /careers/job/<role id> (one role with its apply form).
 * On the subdomain careers.<domain> the same pages are at / and /job/<id> (rewrites in vercel.json).
 * Each job page carries JobPosting markup, so Google for Jobs lists it (and Indeed, which reads public career pages).
 * The roles come from the HR Google Sheet (hr_cycle/Careers.gs, roles ticked "Open for Applications").
 */
const crypto = require("crypto");
const { getJobs, logClick, esc, basePath, origin } = require("./_hr");

const VARIANT_RE = /^[A-Z0-9-]{3,40}$/;

function paragraphs(text) {
  return String(text || "").split(/\n{2,}/).map(p => p.trim()).filter(Boolean)
    .map(p => `<p>${esc(p).replace(/\n/g, "<br>")}</p>`).join("");
}

function jobPosting(job, data, url) {
  const ld = {
    "@context": "https://schema.org/", "@type": "JobPosting",
    title: job.title, description: paragraphs(job.description) || esc(job.title),
    identifier: { "@type": "PropertyValue", name: data.company, value: job.id },
    datePosted: job.posted, validThrough: `${job.valid_through}T23:59:59Z`,
    employmentType: data.employment_type || "FULL_TIME",
    hiringOrganization: { "@type": "Organization", name: data.company, ...(data.website ? { sameAs: data.website } : {}) },
    jobLocationType: "TELECOMMUTE",
    applicantLocationRequirements: { "@type": "Country", name: data.country || "Pakistan" },
    directApply: true, url,
  };
  return JSON.stringify(ld).replace(/</g, "\\u003c");          // safe inside <script>
}

function page(title, body, extraHead = "") {
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>${esc(title)}</title>${extraHead}
<link rel="preconnect" href="https://fonts.googleapis.com"><link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet">
<style>
:root{--bg:#f5f7fb;--panel:#fff;--ink:#0f172a;--mut:#5b6474;--line:#e3e8f0;--accent:#3b5bdb;--ok:#0f7a4d;--bad:#b42318}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.6 Inter,system-ui,sans-serif}
main{max-width:860px;margin:0 auto;padding:32px 16px 64px}h1{font-size:28px;margin:0 0 6px;letter-spacing:-.02em}
.sub{color:var(--mut);margin:0 0 24px}.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:20px 22px;margin:0 0 14px}
.job a{color:var(--ink);text-decoration:none;font-weight:700;font-size:17px}.job a:hover{color:var(--accent)}
.meta{color:var(--mut);font-size:13px}.pill{display:inline-block;background:#eef2ff;color:#3346a8;border-radius:99px;padding:2px 10px;font-size:12px;font-weight:600;margin:2px 4px 2px 0}
label{display:block;font-weight:600;font-size:13px;margin:14px 0 5px}input[type=text],input[type=email],input[type=tel],input[type=url],select{width:100%;padding:10px 12px;border:1px solid var(--line);border-radius:8px;font:inherit}
button{margin-top:18px;background:var(--accent);color:#fff;border:0;border-radius:8px;padding:11px 20px;font:inherit;font-weight:700;cursor:pointer}button[disabled]{opacity:.6}
.msg{margin-top:14px;padding:10px 12px;border-radius:8px;display:none}.msg.ok{display:block;background:#e7f6ee;color:var(--ok)}.msg.bad{display:block;background:#fdecea;color:var(--bad)}
.hp{position:absolute;left:-5000px}.consent{display:flex;gap:8px;align-items:flex-start;font-weight:400}.consent input{margin-top:4px}
a.back{color:var(--accent);text-decoration:none;font-size:14px}
</style></head><body><main>${body}</main></body></html>`;
}

function listPage(data, base, src) {
  const q = src ? `?src=${encodeURIComponent(src)}` : "";
  const jobs = (data.jobs || []).map(j => `<div class="card job"><a href="${base}/job/${encodeURIComponent(j.id)}${q}">${esc(j.title)}</a>
<div class="meta">${esc(j.department)} · Remote (${esc(data.country)}) · posted ${esc(j.posted)}</div></div>`).join("");
  return page(`Careers at ${data.company}`, `<h1>Careers at ${esc(data.company)}</h1>
<p class="sub">Fully remote roles. Choose a job to read it and apply.</p>${jobs || `<div class="card">No open roles right now. Please check back soon.</div>`}`);
}

function jobPage(job, data, base, url, src, utm, track = {}) {
  const must = (job.must_haves || []).map(m => `<span class="pill">${esc(m)}</span>`).join("");
  const body = `<a class="back" href="${base || "/"}">← All jobs</a>
<h1>${esc(job.title)}</h1><p class="sub">${esc(data.company)} · ${esc(job.department)} · Remote (${esc(data.country)})${job.salary ? " · " + esc(job.salary) : ""}</p>
<div class="card">${paragraphs(job.description)}${must ? `<p><b>Must-haves:</b><br>${must}</p>` : ""}</div>
<div class="card"><h2 style="margin:0 0 4px;font-size:19px">Apply for this job</h2><div class="meta">It takes about two minutes. We reply to every application.</div>
<form id="apply" novalidate>
<input type="hidden" name="role_id" value="${esc(job.id)}"><input type="hidden" name="source" value="${esc(src)}"><input type="hidden" name="utm_source" value="${esc(utm)}">
<input type="hidden" name="variant" value="${esc(track.v || "")}"><input type="hidden" name="utm_campaign" value="${esc(track.campaign || "")}"><input type="hidden" name="utm_content" value="${esc(track.content || "")}">
<div class="hp" aria-hidden="true"><label>Website</label><input type="text" name="website" tabindex="-1" autocomplete="off"></div>
<label for="name">Full name</label><input type="text" id="name" name="name" required autocomplete="name">
<label for="email">Email</label><input type="email" id="email" name="email" required autocomplete="email">
<label for="phone">Phone (with country code)</label><input type="tel" id="phone" name="phone" autocomplete="tel">
<label for="linkedin">LinkedIn profile (optional)</label><input type="url" id="linkedin" name="linkedin" placeholder="https://www.linkedin.com/in/your-name">
<label for="cv">CV (PDF or Word, up to 3 MB)</label><input type="file" id="cv" name="cv" accept=".pdf,.docx" required>
<label class="consent"><input type="checkbox" id="consent" required> I agree that ${esc(data.company)} may use my details and CV for this hiring process. I can ask for them to be deleted at any time.</label>
<button type="submit" id="send">Send application</button><div class="msg" id="msg" role="status"></div>
</form></div>
<script>
const f=document.getElementById("apply"),msg=document.getElementById("msg"),send=document.getElementById("send");
(()=>{const v=f.variant.value;if(!v)return;const k="pgi-click:"+v+":"+new Date().toISOString().slice(0,10);
  try{if(localStorage.getItem(k))return;localStorage.setItem(k,"1");}catch(e){}
  fetch("/api/careers?click=1",{method:"POST",keepalive:true,headers:{"Content-Type":"application/json"},
    body:JSON.stringify({role_id:f.role_id.value,v:v,src:f.source.value})}).catch(()=>{});})();
const show=(t,ok)=>{msg.textContent=t;msg.className="msg "+(ok?"ok":"bad");};
f.addEventListener("submit",async e=>{e.preventDefault();
  const file=document.getElementById("cv").files[0];
  if(!f.name.value.trim()||!f.email.value.trim())return show("Please fill in your name and email.");
  if(!file)return show("Please attach your CV.");
  if(!/\\.(pdf|docx)$/i.test(file.name))return show("The CV must be a PDF or Word (.docx) file.");
  if(file.size>3*1024*1024)return show("The CV is too large (3 MB at most).");
  if(!document.getElementById("consent").checked)return show("Please tick the consent box.");
  send.disabled=true;show("Sending…",true);
  const b64=await new Promise((ok,bad)=>{const r=new FileReader();r.onload=()=>ok(String(r.result).split(",")[1]);r.onerror=bad;r.readAsDataURL(file);});
  const fd=Object.fromEntries(new FormData(f).entries());delete fd.cv;
  try{const resp=await fetch("/api/apply",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({...fd,consent:true,cv_name:file.name,cv_base64:b64})});
    const d=await resp.json();
    if(d.ok){f.reset();show("Thank you — your application has arrived. You will receive an email confirmation shortly.",true);}
    else{show(d.error||"Something went wrong. Please try again.");}
  }catch(err){show("Network error. Please try again.");}finally{send.disabled=false;}
});
</script>`;
  return page(`${job.title} — ${data.company}`, body,
    `<link rel="canonical" href="${esc(url)}"><meta name="description" content="${esc(String(job.description || "").slice(0, 155))}">
<script type="application/ld+json">${jobPosting(job, data, url)}</script>`);
}

/**
 * POST /api/careers?click=1 {role_id, v, src}: one visit to a job page through a tracked ad link (the page itself is
 * cached, so the browser reports it). The visitor is a hash of IP, browser and day: never stored in the clear.
 */
async function handleClick(req, res) {
  let b = req.body;
  if (typeof b === "string") { try { b = JSON.parse(b); } catch { b = {}; } }
  b = b || {};
  const v = String(b.v || "").toUpperCase().slice(0, 40);
  if (!VARIANT_RE.test(v)) { res.status(204).end(); return; }
  const ip = String(req.headers["x-forwarded-for"] || "").split(",")[0].trim();
  const day = new Date().toISOString().slice(0, 10);
  const visitor = crypto.createHash("sha256").update(`${ip}|${req.headers["user-agent"] || ""}|${day}`).digest("hex").slice(0, 32);
  try {
    await logClick({ v, visitor, role_id: String(b.role_id || "").slice(0, 40), src: String(b.src || "").slice(0, 30) });
  } catch (e) { /* a lost click never breaks the page */ }
  res.status(204).end();
}

module.exports = async (req, res) => {
  if (req.method === "POST" && req.query && req.query.click) { await handleClick(req, res); return; }
  const base = basePath(req);
  const id = (req.query && req.query.id) || "";
  const src = String((req.query && req.query.src) || "").slice(0, 30);
  const utm = String((req.query && req.query.utm_source) || "").slice(0, 60);
  res.setHeader("Content-Type", "text/html; charset=utf-8");
  try {
    const data = await getJobs();
    res.setHeader("Cache-Control", "s-maxage=600, stale-while-revalidate=1800");
    if (!id) { res.status(200).send(listPage(data, base, src)); return; }
    const job = (data.jobs || []).find(j => j.id === id);
    if (!job) {
      res.status(404).send(page("Job not found", `<a class="back" href="${base || "/"}">← All jobs</a><h1>This job is no longer open</h1><p class="sub">Have a look at the other open roles.</p>`));
      return;
    }
    const url = `${origin(req)}${base}/job/${encodeURIComponent(job.id)}`;
    const q = req.query || {};
    const v = String(q.v || "").toUpperCase().slice(0, 40);
    const track = { v: VARIANT_RE.test(v) ? v : "", campaign: String(q.utm_campaign || "").slice(0, 60),
                    content: String(q.utm_content || "").slice(0, 60) };
    res.status(200).send(jobPage(job, data, base, url, src, utm, track));
  } catch (e) {
    res.status(503).send(page("Careers", `<h1>Careers</h1><p class="sub">The job list is not available right now. Please try again later.</p><!-- ${esc(e.message)} -->`));
  }
};

module.exports.jobPosting = jobPosting;
module.exports.paragraphs = paragraphs;
