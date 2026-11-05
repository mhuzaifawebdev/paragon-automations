"""FREE local run for the Erasmus+ coordinators: finds each organisation's contact on its own website, on this
computer, with no paid AI and no paid search ($0). Uses the scraper's rules mode (scraper2/pipeline.py with
provider "rules": role words such as Erasmus coordinator / director / Schulleiter next to a name, the official email
and phone) and the free website discovery (email domain, Wikidata, school registries, free DuckDuckGo search).

    python scripts/erasmus_local.py                      # everyone, organisations with a website first
    python scripts/erasmus_local.py --limit 50           # a short trial
    python scripts/erasmus_local.py --only-with-website  # the fast half first
    python scripts/erasmus_local.py --workers 40         # organisations read at the same time (default 30)

Stop it any time with Ctrl+C; starting it again continues where it stopped (one result file per organisation in
erasmus_local/results/). Every 300 organisations, and at the end, erasmus_contacts_verified.xlsx is rebuilt with
these results plus the AI tool's (scripts/import_erasmus_ai.py).
"""
import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "erasmus_local"
RESULTS = OUT / "results"
# Free only: no key for any paid service is visible to the scraper in this process.
for k in ("GEMINI_PAID_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "BRAVE_API_KEY", "OPENAI_API_KEY"):
    os.environ.pop(k, None)
os.environ["PARAGON_DATA"] = str(OUT)          # the website memory of this run stays in erasmus_local/
sys.path.insert(0, str(ROOT / "scraper2"))
sys.path.insert(0, str(ROOT / "scraper"))
import pipeline  # noqa: E402
from run import load_cfg  # noqa: E402

COUNTRY = {"AT": "Austria", "BE": "Belgium", "BG": "Bulgaria", "HR": "Croatia", "CY": "Cyprus", "CZ": "Czech Republic",
           "DK": "Denmark", "EE": "Estonia", "FI": "Finland", "FR": "France", "DE": "Germany", "EL": "Greece",
           "GR": "Greece", "HU": "Hungary", "IE": "Ireland", "IT": "Italy", "LV": "Latvia", "LT": "Lithuania",
           "LU": "Luxembourg", "MT": "Malta", "NL": "Netherlands", "PL": "Poland", "PT": "Portugal", "RO": "Romania",
           "SK": "Slovakia", "SI": "Slovenia", "ES": "Spain", "SE": "Sweden", "IS": "Iceland", "LI": "Liechtenstein",
           "NO": "Norway", "MK": "North Macedonia", "RS": "Serbia", "TR": "Turkey", "AW": "Aruba", "CW": "Curacao",
           "UK": "United Kingdom", "GB": "United Kingdom", "CH": "Switzerland", "AL": "Albania", "BA": "Bosnia and Herzegovina",
           "ME": "Montenegro", "XK": "Kosovo", "UA": "Ukraine"}


def free_cfg():
    """The scraper's settings with every paid step off."""
    cfg = load_cfg()
    cfg.update(provider="rules", model="", gemini_site_search=False, claude_site_search=False, email_web_search=False,
               staff_search=False, staff_search_when_no_contact=False, partner_slices=False, alternatives=False,
               discover_with_search=True, max_pages=12, page_workers=4, reuse_earlier_results=False)
    return cfg


def organisations(xlsx, only_with_website=False):
    """Rows for the scraper from the Coordinators sheet, organisations with a website first, the most active first."""
    import openpyxl
    wb = openpyxl.load_workbook(xlsx, read_only=True)
    rows = list(wb["Coordinators"].iter_rows(min_row=2, values_only=True))
    rows.sort(key=lambda r: (bool(r[5]), str(r[9] or ""), float(r[7] or 0)), reverse=True)
    out, seen = [], set()
    for name, pic, country, city, typ, site, projects, grant, types, end in (r[:10] for r in rows):
        if not name or not pic or pic in seen:
            continue
        seen.add(pic)
        if only_with_website and not site:
            continue
        site = (site or "").strip()
        if site and not site.startswith("http"):
            site = "https://" + site
        out.append({"row_id": str(pic), "institution_name": str(name).strip(), "country_sheet": COUNTRY.get(str(country), str(country or "")),
                    "phone_1": "", "contact_name": "", "email": "", "seed_urls": site, "city": city or "", "type": typ or ""})
    return out


def done_ids():
    return {p.stem for p in RESULTS.glob("*.json")}


def scrape_one(row, cfg):
    t = time.time()
    try:
        res = pipeline.scrape(row, dict(cfg))
    except Exception as e:                      # one bad site never stops the run
        res = {"row_id": row["row_id"], "error": str(e)[:300], "contacts": {"institution_name": row["institution_name"]}}
    res["erasmus"] = {"city": row.get("city"), "type": row.get("type"), "seconds": round(time.time() - t, 1)}
    tmp = RESULTS / (row["row_id"] + ".json.tmp")
    tmp.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    tmp.replace(RESULTS / (row["row_id"] + ".json"))
    return res


def found(res):
    c = (res.get("contacts") or {})
    person = c.get("contact") or {}
    return bool(person.get("name")), bool(person.get("email") or c.get("generic_email")), bool(c.get("phone") or person.get("phone"))


def rebuild_excel():
    try:
        import import_erasmus_ai as imp
        imp.main(quiet=True)
    except Exception as e:
        print(f"   (Excel not rebuilt this time: {str(e)[:120]})", flush=True)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--xlsx", default=str(ROOT / "erasmus_KA1_ongoing_projects.xlsx"))
    ap.add_argument("--workers", type=int, default=30)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only-with-website", action="store_true")
    a = ap.parse_args()
    RESULTS.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(ROOT / "scripts"))
    cfg = free_cfg()
    pipeline.LLM_SLOTS = threading.Semaphore(1)
    have = done_ids()
    todo = [r for r in organisations(a.xlsx, a.only_with_website) if r["row_id"] not in have]
    if a.limit:
        todo = todo[:a.limit]
    print(f"{len(have)} organisations already done; {len(todo)} to do now with {a.workers} at a time. Free: no paid "
          f"service is used. Ctrl+C stops; running again continues.", flush=True)
    stats = {"done": 0, "name": 0, "email": 0, "phone": 0}
    t0, last = time.time(), time.time()
    lock = threading.Lock()
    pool = ThreadPoolExecutor(a.workers)
    try:
        if True:
            futs = [pool.submit(scrape_one, r, cfg) for r in todo]
            for f in as_completed(futs):
                res = f.result()
                n, e, p = found(res)
                with lock:
                    stats["done"] += 1
                    stats["name"] += n
                    stats["email"] += e
                    stats["phone"] += p
                    d = stats["done"]
                if time.time() - last > 60 or d == len(todo):
                    last = time.time()
                    rate = d / max(1, time.time() - t0)
                    eta = (len(todo) - d) / rate / 60 if rate else 0
                    print(f"{d}/{len(todo)} done | named contact {stats['name']} | email {stats['email']} | phone "
                          f"{stats['phone']} | about {eta:.0f} min left", flush=True)
                if d % 300 == 0:
                    rebuild_excel()
    except KeyboardInterrupt:
        print("\nStopping: finishing the organisations already open, then saving...", flush=True)
        pool.shutdown(wait=True, cancel_futures=True)
        print("Stopped. Run the same command again to continue.", flush=True)
    else:
        pool.shutdown(wait=True)
    rebuild_excel()
    print("Results: erasmus_contacts_verified.xlsx", flush=True)


if __name__ == "__main__":
    main()
