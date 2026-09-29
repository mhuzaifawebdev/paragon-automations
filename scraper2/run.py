"""Scraper v2 runner: many institutes in parallel, results in the same files the rest of the system reads.

    python scraper2/run.py --rows r007,r008 --workers 6
    python scraper2/run.py --all --limit 50 --provider rules          # free, no model at all
    python scraper2/run.py --rows r018 --dry-run                       # crawl + evidence pack only, no model call
    python scraper2/run.py --pending                                   # rows marked 'pending' in the sheet (deployment)

Results go to data/scrape_results/<row>.json (v1's shape), then scraper/run_batch.merge() rebuilds the CSVs
(and runs the deterministic verifier). Use scraper/build_sheet.py to publish to the Google Sheet.
"""
import argparse
import csv
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scraper2"))
sys.path.insert(0, str(ROOT / "scraper"))
import pipeline  # noqa: E402
import run_batch  # noqa: E402

RESULTS = Path(__import__("os").environ.get("PARAGON_DATA") or ROOT / "data") / "scrape_results"


def load_env():
    """Keys live in .env (never committed): ANTHROPIC_API_KEY, GEMINI_API_KEY, SCRAPER_PROVIDER."""
    import os
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def load_cfg():
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8")).get("scraper", {}) or {}
    return {"provider": "claude_cli", "model": "", "workers": 6, "llm_concurrency": 4, "page_workers": 5,
            "max_pages": 10, "evidence_chars": 90000, "discover_with_search": False, **cfg}


def sheet_inputs():
    """Optional per-row inputs typed in the sheet: website override + run status (deployment mode)."""
    sys.path.insert(0, str(ROOT / "scraper"))
    import build_sheet
    return build_sheet.read_inputs()


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    load_env()
    cfg = load_cfg()
    import os
    cfg["provider"] = os.environ.get("SCRAPER_PROVIDER") or cfg["provider"]
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rows")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--pending", action="store_true", help="rows whose 'Run status' cell in the sheet says pending")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--force", action="store_true", help="re-scrape rows that already have a result")
    ap.add_argument("--workers", type=int, default=cfg["workers"])
    ap.add_argument("--provider", default=cfg["provider"], choices=["claude_api", "claude_cli", "gemini", "rules"])
    ap.add_argument("--model", default=cfg["model"])
    ap.add_argument("--alternatives", action="store_true", help="the contact on file does not answer: find OTHER people (up to 3 ranked)")
    ap.add_argument("--dry-run", action="store_true", help="crawl and build the evidence pack, make no model call")
    ap.add_argument("--publish", action="store_true", help="after scraping, rebuild the Google Sheet and mark pending rows done")
    a = ap.parse_args()
    cfg.update(provider=a.provider, model=a.model, alternatives=a.alternatives)
    pipeline.LLM_SLOTS = threading.Semaphore(int(cfg["llm_concurrency"]))

    RESULTS.mkdir(parents=True, exist_ok=True)
    rows = run_batch.read_numbers()
    inputs = {}
    if a.pending or a.all:
        try:
            inputs = sheet_inputs()
        except Exception as e:
            print(f"(sheet inputs not available: {e})", file=sys.stderr)
    for r in rows:
        r["website_override"] = (inputs.get(r["row_id"]) or {}).get("website", "")

    if a.rows:
        want = {x.strip() for x in a.rows.split(",") if x.strip()}
        todo = [r for r in rows if r["row_id"] in want]
    elif a.pending:
        todo = [r for r in rows if (inputs.get(r["row_id"]) or {}).get("status", "").lower() == "pending"]
    elif a.all:
        todo = rows
    else:
        ap.error("give --rows, --pending or --all")

    first = {}
    for r in rows:
        for g in r["duplicate_group"].split():
            first.setdefault(g, r["row_id"])
    queue = []
    for r in todo:
        rid = r["row_id"]
        origin = next((first[g] for g in r["duplicate_group"].split() if first[g] != rid), None)
        if origin:
            (RESULTS / f"{rid}.duplicate").write_text(origin, encoding="utf-8")
            continue
        if (RESULTS / f"{rid}.json").exists() and not a.force and not a.pending:
            continue
        queue.append(r)
        if a.limit and len(queue) >= a.limit:
            break

    lock = threading.Lock()
    summary = []

    def work(r):
        rid = r["row_id"]
        try:
            if a.dry_run:
                import crawl, discover, evidence
                inst = {"row_id": rid, "name": r["institution_name"], "country": r["country_sheet"], "phone": r["phone_1"],
                        "email": r["email"], "website_override": r["website_override"]}
                site, how, note = discover.find_website(inst)
                cr = crawl.crawl(site) if site else {"pages": [], "errors": []}
                pack = evidence.build_pack(cr["pages"]) if cr["pages"] else None
                with lock:
                    print(f"{rid}: site={site} via {how} | pages={len(cr['pages'])} | evidence lines={len(pack.lines) if pack else 0}"
                          f" chars={sum(len(l['text']) for l in pack.lines) if pack else 0}", flush=True)
                return
            res = pipeline.scrape(r, cfg)
            if res.get("retry"):
                with lock:
                    print(f"{rid}: NOT SAVED, {res['reason']}. Any existing result is kept; run again later.", flush=True)
                return
            t = res["timing"]
            c = res["contacts"]
            n = res.get("network") or {}
            with lock:
                (RESULTS / f"{rid}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
                summary.append((rid, t.get("total_seconds", 0), t.get("cost_usd_estimate", 0)))
                print(f"{rid}: {t.get('total_seconds')}s (find {t.get('discover_s')}s, read {t.get('crawl_s')}s, "
                      f"model {t.get('model_s')}s) ${t.get('cost_usd_estimate', 0)} | {c.get('institution_type')} | "
                      f"contact={(c.get('contact') or {}).get('name') or '-'} | partners={len(n.get('external_collaboration') or [])} "
                      f"campuses={len(n.get('internal_collaboration') or [])} | {t.get('mode')}", flush=True)
        except Exception as e:
            with lock:
                print(f"{rid}: FAILED: {type(e).__name__}: {e}", flush=True)
                with open(RESULTS / "errors.log", "a", encoding="utf-8") as log:
                    log.write(f"{time.strftime('%F %T')} v2 {rid}: {e}\n")

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=max(1, a.workers)) as pool:
        list(pool.map(work, queue))
    wall = time.time() - t0
    if not queue and a.pending:
        print("nothing pending")
    if queue and not a.dry_run:
        counts = run_batch.merge(rows)
        each = [s for _, s, _ in summary]
        print(f"\nTIMING: {len(summary)}/{len(queue)} institutes in {wall:.0f}s wall-clock with {a.workers} workers = "
              f"{wall / len(queue):.1f}s per institute effective (each took {sum(each) / max(len(each), 1):.0f}s alone)")
        print(f"COST (estimate): ${sum(c for _, _, c in summary):.3f} total, ${sum(c for _, _, c in summary) / max(len(summary), 1):.3f} per institute")
        print("outputs rebuilt (enriched, partners, campuses, contacts, needs_review):", counts)
        if a.publish:
            import subprocess
            r = subprocess.run([sys.executable, str(ROOT / "scraper" / "build_sheet.py"), "--mark-done"],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            print((r.stdout or "").strip()[-600:], (r.stderr or "").strip()[-400:])


if __name__ == "__main__":
    main()
