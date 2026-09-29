"""Builds a local, static HTML progress page for one scraper batch, from
data_hei/<batch>/progress.json (written by scraper2/batch_runner.py after every chunk) and its
institutions_enriched.csv. Same snapshot pattern as scripts/build_dashboard.py (the calling-agent
one) - a plain HTML file, no server, no framework, refreshed by re-running this script.

batch_runner.py already calls this automatically after every chunk, so normally you don't need to
run it by hand - this is here for re-building the page on demand, or for the Vercel API route to
mirror locally when testing.

    python scripts/build_scraper_dashboard.py --batch teamA
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "scraper_dashboard.html"


def build(data_dir):
    data_dir = Path(data_dir)
    progress_path = data_dir / "progress.json"
    progress = json.loads(progress_path.read_text(encoding="utf-8")) if progress_path.exists() else {
        "batch": data_dir.name, "status": "not started", "total_rows": 0, "done": 0, "pending": 0,
        "elapsed_seconds": 0, "eta_seconds": None, "cost_usd_so_far": 0, "cost_usd_projected_total": None,
        "confidence": {"high": 0, "medium": 0, "low": 0, "none": 0}, "partners_found": 0, "campuses_found": 0,
        "recent": [], "chunk": 0, "total_chunks": 0,
    }
    review_path = data_dir / "needs_review.csv"
    needs_review = max(0, len(review_path.read_text(encoding="utf-8-sig").splitlines()) - 1) if review_path.exists() else 0
    progress["needs_review"] = needs_review
    return progress


HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta http-equiv="refresh" content="15">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Scraper batch progress</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@500&display=swap" rel="stylesheet">
<style>
  :root {
    --bg: #0b0e14;
    --bg-grad: radial-gradient(1200px 600px at 15% -10%, rgba(99,102,241,.16), transparent 60%),
               radial-gradient(1000px 500px at 100% 0%, rgba(20,184,166,.10), transparent 55%);
    --panel: #12151f; --panel-2: #161a26; --line: #232838;
    --ink: #eef1f8; --mut: #8790a8; --mut-2: #5c6580;
    --accent: #6366f1; --accent-2: #22d3ee;
    --ok: #22c55e; --warn: #f59e0b; --bad: #f43f5e; --off: #64748b;
    --radius: 14px; --shadow: 0 1px 2px rgba(0,0,0,.4), 0 12px 32px -12px rgba(0,0,0,.55);
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg-grad), var(--bg); color: var(--ink);
    font: 400 14px/1.55 "Inter", -apple-system, Segoe UI, Roboto, sans-serif; -webkit-font-smoothing: antialiased;
  }
  .mono { font-family: "JetBrains Mono", ui-monospace, Menlo, monospace; }
  header {
    padding: 20px 32px; border-bottom: 1px solid var(--line);
    background: rgba(11,14,20,.72); backdrop-filter: blur(10px);
    display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px;
  }
  header h1 { font-size: 16px; margin: 0; font-weight: 700; letter-spacing: -.01em; }
  header h1 .batchname { color: var(--accent-2); }
  header .status-badge {
    display: inline-flex; align-items: center; gap: 6px; font-size: 11.5px; font-weight: 600;
    padding: 3px 10px; border-radius: 999px; background: rgba(99,102,241,.14); color: #a5b4fc; margin-left: 10px;
  }
  header .meta { color: var(--mut); font-size: 12px; }
  main { padding: 28px 32px 48px; display: grid; gap: 22px; max-width: 1080px; margin: 0 auto; }

  .bar-wrap {
    background: linear-gradient(180deg, var(--panel-2), var(--panel)); border: 1px solid var(--line);
    border-radius: var(--radius); padding: 22px 24px; box-shadow: var(--shadow);
  }
  .bar-top { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 12px; }
  .bar-top .count { font-size: 22px; font-weight: 800; letter-spacing: -.02em; }
  .bar-top .count small { font-size: 13px; font-weight: 500; color: var(--mut); }
  .bar-track { height: 10px; background: #0b1219; border-radius: 999px; overflow: hidden; }
  .bar-fill { height: 100%; background: linear-gradient(90deg, var(--accent), var(--accent-2)); border-radius: 999px;
              transition: width .4s ease; }
  .bar-foot { display: flex; gap: 16px; margin-top: 12px; font-size: 12.5px; color: var(--mut); flex-wrap: wrap; }
  .bar-foot b { color: var(--ink); font-weight: 600; }

  .kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 14px; }
  .kpi {
    background: linear-gradient(180deg, var(--panel-2), var(--panel)); border: 1px solid var(--line);
    border-radius: var(--radius); padding: 16px 18px; box-shadow: var(--shadow); position: relative; overflow: hidden;
  }
  .kpi::before { content: ""; position: absolute; top: 0; left: 0; right: 0; height: 2px;
                 background: linear-gradient(90deg, var(--kc, var(--accent)), transparent); }
  .kpi .n { font-size: 24px; font-weight: 800; letter-spacing: -.02em; }
  .kpi .l { color: var(--mut); font-size: 12px; margin-top: 3px; font-weight: 500; }

  section { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius);
            overflow: hidden; box-shadow: var(--shadow); }
  section > h2 { font-size: 13px; font-weight: 700; letter-spacing: .01em; margin: 0;
                 padding: 16px 20px; border-bottom: 1px solid var(--line); }

  .conf-row { display: flex; gap: 10px; padding: 18px 20px; flex-wrap: wrap; }
  .conf { flex: 1; min-width: 110px; text-align: center; padding: 14px 10px; border-radius: 10px; border: 1px solid var(--line); }
  .conf.high { background: rgba(34,197,94,.12); } .conf.medium { background: rgba(245,158,11,.12); }
  .conf.low { background: rgba(244,63,94,.10); } .conf.none { background: rgba(100,116,139,.10); }
  .conf .n { font-size: 20px; font-weight: 800; } .conf .l { color: var(--mut); font-size: 11px; margin-top: 2px;
             text-transform: uppercase; letter-spacing: .04em; }

  ul.recent { list-style: none; margin: 0; padding: 10px 20px 16px; columns: 3; column-gap: 20px; }
  ul.recent li { padding: 6px 0; font-size: 13px; border-bottom: 1px solid var(--line); break-inside: avoid; }
  .empty { color: var(--mut); padding: 28px 20px; font-style: italic; text-align: center; font-size: 13px; }
  footer { color: var(--mut-2); font-size: 12px; text-align: center; padding: 10px 20px 30px; max-width: 1080px; margin: 0 auto; }
  code { background: rgba(255,255,255,.06); border: 1px solid var(--line); padding: 1.5px 6px;
         border-radius: 5px; font-family: "JetBrains Mono", monospace; font-size: 11.5px; }
  @media (max-width: 640px) { header, main { padding-left: 16px; padding-right: 16px; } ul.recent { columns: 1; } }
</style></head>
<body>
<header>
  <h1>Scraper batch <span class="batchname" id="batch"></span><span class="status-badge" id="status"></span></h1>
  <div class="meta mono">Auto-refreshes every 15s &middot; generated <span id="gen"></span></div>
</header>
<main>
  <div class="bar-wrap">
    <div class="bar-top">
      <div class="count"><span id="done">0</span> / <span id="total">0</span> <small>institutes scraped</small></div>
    </div>
    <div class="bar-track"><div class="bar-fill" id="fill" style="width:0%"></div></div>
    <div class="bar-foot">
      <span>Chunk <b id="chunk">0</b> of <b id="total-chunks">0</b></span>
      <span>ETA <b id="eta">&mdash;</b></span>
    </div>
  </div>

  <div class="kpis" id="cards"></div>

  <section>
    <h2>Contact quality found so far</h2>
    <div class="conf-row" id="conf"></div>
  </section>

  <section>
    <h2>Most recently scraped</h2>
    <div id="recent-wrap"></div>
  </section>
</main>
<footer>Local snapshot, rebuilt after every chunk by <code>scraper2/batch_runner.py</code>.</footer>
<script>
const D = __DATA__;
const $ = id => document.getElementById(id);
$("batch").textContent = D.batch;
$("status").textContent = D.status;
$("gen").textContent = (D.generated_at || "").replace("T"," ").slice(0,19);
$("done").textContent = D.done; $("total").textContent = D.total_rows;
$("fill").style.width = D.total_rows ? Math.round(100 * D.done / D.total_rows) + "%" : "0%";
$("chunk").textContent = D.chunk; $("total-chunks").textContent = D.total_chunks;
$("eta").textContent = D.eta_seconds ? Math.round(D.eta_seconds/60) + " min" : (D.status === "done" ? "done" : "—");

const fmtUsd = n => n == null ? "—" : "$" + n.toFixed(2);
const KC = {
  "pending": ["#6366f1"], "needs review": ["#f59e0b"], "partners found": ["#22c55e"],
  "campuses found": ["#22d3ee"], "cost so far": ["#f43f5e"], "projected total": ["#8790a8"],
};
$("cards").innerHTML = [
  [D.pending, "pending"],
  [D.needs_review || 0, "needs review"],
  [D.partners_found || 0, "partners found"],
  [D.campuses_found || 0, "campuses found"],
  [fmtUsd(D.cost_usd_so_far), "cost so far"],
  [fmtUsd(D.cost_usd_projected_total), "projected total"],
].map(([n,l]) => `<div class="kpi" style="--kc:${(KC[l]||["#6366f1"])[0]}"><div class="n">${n}</div><div class="l">${l}</div></div>`).join("");

const c = D.confidence || {};
$("conf").innerHTML = ["high","medium","low","none"].map(k =>
  `<div class="conf ${k}"><div class="n">${c[k]||0}</div><div class="l">${k}</div></div>`).join("");

$("recent-wrap").innerHTML = (D.recent && D.recent.length)
  ? `<ul class="recent">${D.recent.slice().reverse().map(r => `<li>${r}</li>`).join("")}</ul>`
  : `<div class="empty">Nothing scraped yet.</div>`;
</script>
</body></html>
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--batch", required=True)
    ap.add_argument("--no-open", action="store_true")
    a = ap.parse_args()
    data_dir = ROOT / "data_hei" / a.batch
    data = build(data_dir)
    OUT.write_text(HTML.replace("__DATA__", json.dumps(data)), encoding="utf-8")
    print(f"Wrote {OUT} (batch {a.batch}: {data['done']}/{data['total_rows']} done)")
    if not a.no_open:
        try:
            subprocess.run(["cmd", "/c", "start", "", str(OUT)], check=False)
        except Exception:
            import webbrowser
            webbrowser.open(OUT.as_uri())


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
