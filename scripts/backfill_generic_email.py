"""Adds the institute's general inbox (info@, office@, international@ ...) to results scraped before the scraper looked
for one. Free: no AI is used, only the institute's own home, contact and staff pages are downloaded again and read
by scraper2/pipeline.pick_generic_email (the same rule new batches use).

    python scripts/backfill_generic_email.py --limit 10          # try 10 institutes, print what was found
    python scripts/backfill_generic_email.py                     # every batch, every institute not yet checked
    python scripts/backfill_generic_email.py --batch batch-hei-network --workers 8

Each checked result gets "generic_checked" (also when nothing was found), so a second run skips it. Afterwards the
touched batches' merged CSVs and the dashboard figures are rebuilt; the Excel files are rebuilt by the workflow.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scraper2"))
sys.path.insert(0, str(ROOT / "scraper"))
import crawl  # noqa: E402
import pipeline  # noqa: E402

DATA = ROOT / "data_hei"


def todo(data=DATA, batch=None):
    """(batch, path) of every result with a website that has not been checked for a generic inbox yet."""
    out = []
    for bdir in sorted(p for p in Path(data).iterdir() if p.is_dir() and p.name != "uploads"):
        if batch and bdir.name != batch:
            continue
        for f in sorted((bdir / "scrape_results").glob("r*.json")):
            try:
                c = json.loads(f.read_text(encoding="utf-8")).get("contacts") or {}
            except Exception:
                continue
            if c.get("official_website") and not c.get("generic_email") and not c.get("generic_checked"):
                out.append((bdir.name, f))
    return out


def check_one(path, max_pages=4):
    """Reads the site, stores the inbox found (or that none was found). Returns the email or ""."""
    res = json.loads(path.read_text(encoding="utf-8"))
    c = res["contacts"]
    try:
        cr = crawl.crawl(c["official_website"], max_pages=max_pages, workers=2)
        email, url = pipeline.pick_generic_email(cr["pages"], cr["site"]) if cr["pages"] else ("", "")
    except Exception as e:                       # an unreachable site is recorded as checked, never crashes the run
        email, url = "", ""
        print(f"  {path.parent.parent.name}/{path.stem}: could not read the site ({str(e)[:80]})", flush=True)
    c.update(generic_email=email, generic_email_url=url, generic_checked=time.strftime("%Y-%m-%d", time.gmtime()))
    path.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return email


def remerge(batch):
    """Rebuilds the batch's institutions_enriched.csv so the Excel gets the new column."""
    code = "import sys; sys.path.insert(0, 'scraper'); import run_batch; run_batch.merge(run_batch.read_numbers())"
    subprocess.run([sys.executable, "-c", code], cwd=ROOT, env={**os.environ, "PARAGON_DATA": str(DATA / batch)}, check=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    items = todo(batch=a.batch)
    if a.limit:
        items = items[:a.limit]
    print(f"{len(items)} institute(s) to check", flush=True)
    found, batches = 0, set()
    with ThreadPoolExecutor(max_workers=max(1, a.workers)) as pool:
        for (batch, path), email in zip(items, pool.map(lambda it: check_one(it[1]), items)):
            batches.add(batch)
            found += bool(email)
            print(f"  {batch}/{path.stem}: {email or '-'}", flush=True)
    print(f"Generic inbox found for {found} of {len(items)}", flush=True)
    for b in sorted(batches):
        remerge(b)
    subprocess.run([sys.executable, str(ROOT / "scripts" / "build_dashboard_stats.py")], cwd=ROOT, check=False)
    Path(os.environ.get("BACKFILL_BATCHES_FILE", os.devnull)).write_text("\n".join(sorted(batches)), encoding="utf-8")


if __name__ == "__main__":
    main()
