"""Builds a local, static HTML dashboard from the CSVs the calling-agent scripts already write:
data/extension_state.csv + data/alerts.csv (agent/pause_monitor.py) and, once real calls have
been placed, data/campaign_state.csv (agent/run_campaign.py / agent/state.py).

This is a SNAPSHOT, not a live page: it embeds the current CSV contents into one HTML file at
build time. Re-run it after pause_monitor.py / run_campaign.py to refresh what it shows. Kept
local (not published anywhere) on purpose - extension numbers and manager names are internal
operational data, and the dashboard needs no server, account, or internet connection to view.

    python scripts/build_dashboard.py            # writes dashboard.html, opens it
    python scripts/build_dashboard.py --no-open   # just write the file
"""
import argparse
import csv
import json
import subprocess
import sys
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dashboard.html"


def read_csv(path):
    p = ROOT / path
    if not p.exists():
        return []
    with open(p, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def build():
    extensions = read_csv("data/extension_state.csv")
    alerts = sorted(read_csv("data/alerts.csv"), key=lambda r: r.get("timestamp", ""), reverse=True)
    campaign = read_csv("data/campaign_state.csv")
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "extensions": extensions,
        "alerts": alerts,
        "campaign": campaign,
    }


HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Paragon calling-agent dashboard</title>
<style>
  :root { --bg:#0f1720; --panel:#17212b; --line:#243040; --ink:#e6edf3; --mut:#8b98a5;
          --ok:#2ea043; --off:#8b98a5; --alert:#d1242f; --accent:#4493f8; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--ink); font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif; }
  header { padding:20px 28px; border-bottom:1px solid var(--line); display:flex; justify-content:space-between; align-items:baseline; flex-wrap:wrap; gap:8px; }
  header h1 { font-size:18px; margin:0; }
  header .meta { color:var(--mut); font-size:12.5px; }
  main { padding:24px 28px; display:grid; gap:22px; max-width:1100px; margin:0 auto; }
  .cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; }
  .card { background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:16px; }
  .card .n { font-size:26px; font-weight:600; }
  .card .l { color:var(--mut); font-size:12.5px; margin-top:2px; }
  section { background:var(--panel); border:1px solid var(--line); border-radius:10px; overflow:hidden; }
  section > h2 { font-size:13px; text-transform:uppercase; letter-spacing:.04em; color:var(--mut);
                 margin:0; padding:14px 16px; border-bottom:1px solid var(--line); }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th, td { text-align:left; padding:9px 16px; border-bottom:1px solid var(--line); white-space:nowrap; }
  th { color:var(--mut); font-weight:500; font-size:12px; }
  tr:last-child td { border-bottom:none; }
  .pill { display:inline-flex; align-items:center; gap:6px; padding:2px 9px; border-radius:999px; font-size:12px; }
  .pill.online { background:rgba(46,160,67,.15); color:var(--ok); }
  .pill.offline { background:rgba(139,152,165,.15); color:var(--mut); }
  .pill.alert { background:rgba(209,36,47,.15); color:var(--alert); }
  .dot { width:7px; height:7px; border-radius:50%; background:currentColor; }
  .empty { color:var(--mut); padding:18px 16px; font-style:italic; }
  footer { color:var(--mut); font-size:12px; text-align:center; padding:20px; }
  code { background:rgba(255,255,255,.06); padding:1px 5px; border-radius:4px; }
</style></head>
<body>
<header>
  <h1>Paragon calling-agent dashboard</h1>
  <div class="meta">Snapshot generated <span id="gen"></span> UTC · re-run <code>python scripts/build_dashboard.py</code> to refresh</div>
</header>
<main>
  <div class="cards" id="cards"></div>

  <section>
    <h2>Extensions (Zadarma PBX — human operators and the AI dialer, same view)</h2>
    <div id="ext-wrap"></div>
  </section>

  <section>
    <h2>Recent supervisor alerts</h2>
    <div id="alerts-wrap"></div>
  </section>

  <section>
    <h2>AI dialer campaign</h2>
    <div id="campaign-wrap"></div>
  </section>
</main>
<footer>Data is a local snapshot only — nothing on this page is sent anywhere.</footer>
<script>
const DATA = __DATA__;

document.getElementById("gen").textContent = DATA.generated_at.replace("T"," ").slice(0,19);

function pill(cls, text) { return `<span class="pill ${cls}"><span class="dot"></span>${text}</span>`; }
function esc(s){ return (s??"").toString().replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c])); }

const online = DATA.extensions.filter(e => e.last_status === "true").length;
const offline = DATA.extensions.filter(e => e.last_status === "false").length;
const alerts24h = DATA.alerts.filter(a => (Date.now() - new Date(a.timestamp)) < 86400000).length;
const pending = DATA.campaign.filter(c => c.status === "pending").length;

document.getElementById("cards").innerHTML = [
  [DATA.extensions.length, "extensions watched"],
  [online, "online now"],
  [alerts24h, "alerts, last 24h"],
  [DATA.campaign.length ? pending : "—", "institutes pending"],
].map(([n,l]) => `<div class="card"><div class="n">${n}</div><div class="l">${l}</div></div>`).join("");

document.getElementById("ext-wrap").innerHTML = DATA.extensions.length ? `<table>
  <tr><th>Extension</th><th>Manager</th><th>Status</th><th>Last online</th><th>Last alert</th></tr>
  ${DATA.extensions.map(e => `<tr>
    <td>${esc(e.extension)}</td><td>${esc(e.manager) || "—"}</td>
    <td>${e.last_status === "true" ? pill("online","online") : e.last_status === "false" ? pill("offline","offline") : "unknown"}</td>
    <td>${esc(e.last_online_at).replace("T"," ").slice(0,16) || "never"}</td>
    <td>${esc(e.last_alert_at).replace("T"," ").slice(0,16) || "—"}</td>
  </tr>`).join("")}
</table>` : `<div class="empty">No extension data yet — run <code>python agent/pause_monitor.py</code> first.</div>`;

document.getElementById("alerts-wrap").innerHTML = DATA.alerts.length ? `<table>
  <tr><th>When</th><th>Extension</th><th>Manager</th><th>Reason</th><th>Sent</th></tr>
  ${DATA.alerts.slice(0,50).map(a => `<tr>
    <td>${esc(a.timestamp).replace("T"," ").slice(0,16)}</td><td>${esc(a.extension)}</td>
    <td>${esc(a.manager) || "—"}</td><td>${esc(a.reason)}</td>
    <td>${a.sent_ok === "True" ? pill("online","sent") : pill("alert","not sent")}</td>
  </tr>`).join("")}
</table>` : `<div class="empty">No alerts logged yet.</div>`;

document.getElementById("campaign-wrap").innerHTML = DATA.campaign.length ? `<table>
  <tr><th>Institute</th><th>Contact</th><th>Status</th><th>Attempts</th><th>Last outcome</th></tr>
  ${DATA.campaign.map(c => `<tr>
    <td>${esc(c.row_id)}</td><td>${esc(c.current_contact_name) || "—"}</td>
    <td>${esc(c.status)}</td><td>${esc(c.attempts_total)}</td><td>${esc(c.last_outcome) || "—"}</td>
  </tr>`).join("")}
</table>` : `<div class="empty">No campaign data yet — the AI dialer has not placed any calls (data/campaign_state.csv does not exist).</div>`;
</script>
</body></html>
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-open", action="store_true")
    a = ap.parse_args()
    data = build()
    OUT.write_text(HTML.replace("__DATA__", json.dumps(data)), encoding="utf-8")
    print(f"Wrote {OUT} ({len(data['extensions'])} extensions, {len(data['alerts'])} alerts, "
          f"{len(data['campaign'])} campaign rows)")
    if not a.no_open:
        try:
            subprocess.run(["cmd", "/c", "start", "", str(OUT)], check=False)
        except Exception:
            webbrowser.open(OUT.as_uri())


if __name__ == "__main__":
    main()
