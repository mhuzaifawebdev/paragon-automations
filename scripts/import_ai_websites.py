"""Imports outside research for institutes that had NO website (ai_research_no_website_education.csv filled by an AI
tool or a person) as a website re-check batch, keeping only what can be checked:

  - found_website is kept only if it answers and is the institute's own site: its homepage shows the name
    (discover.check_site), or the name is in the address, or the details were found on that same site. Directories,
    portals and social media are never taken.
  - the contact, email and phone are kept only if the cited page shows them (import_ai_research.check)

Each kept institute becomes one result in data_hei/website-recheck-<name>/scrape_results/, so the dashboard moves it
out of "Website not found" (build_dashboard_stats.apply_rechecks), with whatever contact and email were confirmed.

    python scripts/import_ai_websites.py filled.csv outside-edu-1 [--dry-run]
"""
import argparse
import csv
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scraper2"))
sys.path.insert(0, str(ROOT / "scraper"))
import discover  # noqa: E402
import import_ai_research as air  # noqa: E402

DATA = ROOT / "data_hei"
NOT_OWN = ("onisep.", "youth.europa.eu", "erasmusdays", "facebook.", "linkedin.", "wikipedia.", "opinieouczelniach",
           "otouczelnie", "formularze.net", "whatschool", "skolkoll", "finstat", "tuttitalia", "agenda500", "wroclaw.pl/",
           "goteborg.se", "education.gouv.fr", "atrium-paca", "studyinbelgium", "miz.org", "odeki.de", "km.bayern.de")


def site_ok(row):
    """(website, why) if found_website is the institute's own site, else ("", why)."""
    url = (row.get("found_website") or "").strip()
    if not url:
        return "", "no website given"
    url = url if url.startswith("http") else "https://" + url
    if any(x in url.lower() for x in NOT_OWN):
        return "", "a directory or portal, not the institute's own site"
    name = row.get("institute", "")
    if discover.name_in_domain(url, name):
        return url, "the name is in the address"
    src = (row.get("source_url") or "").lower()
    if src and discover._domain(src) == discover._domain(url):
        return url, "the details were found on this same site"
    host = discover._domain(url).split(":")[0]
    host = host[4:] if host.startswith("www.") else host
    mails = [(row.get(k) or "").split("@")[-1].lower() for k in ("contact_email", "general_email") if "@" in (row.get(k) or "")]
    if any(d == host or d.endswith("." + host) or host.endswith("." + d) for d in mails):
        # the site blocks robots or words its name differently, but the institute's own address is on this domain
        return url, "the institute's email is on this domain"
    try:
        ok, why, _ = discover.check_site(url, {"name": name, "country": row.get("country", "")})
    except Exception as e:
        return "", f"could not open ({str(e)[:60]})"
    return (url, "its homepage shows the name") if ok else ("", why)


def result(i, row, site, got):
    c = {"row_id": f"r{i:03d}", "institution_name": row["institute"], "institution_type": "unknown",
         "official_website": site, "country": None, "phone": None, "contact": None, "alternates": [], "rung": "",
         "source": "outside research", "confidence": 0.5, "needs_human_check": True, "pages_read": 0,
         "notes": "Website and details from outside research, each checked on the cited page."}
    res = {"row_id": c["row_id"], "scraped_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "contacts": c,
           "network": {"external_collaboration": [], "internal_collaboration": []}}
    air.apply(res, got)
    return res


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("name")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--known-sites", action="store_true",
                    help="the rows already have their website (the scraper found no contact there): keep it as given")
    a = ap.parse_args()
    with open(a.csv, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    cache = {}

    def one(row):
        if a.known_sites:
            site, why = (row.get("website") or "").strip(), "the website we already have"
            if not site:
                why = "no website"
            return row, site, why, (air.check(row, cache) if site else {})
        site, why = site_ok(row)
        return row, site, why, (air.check(row, cache) if site else {})

    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        done = list(pool.map(one, rows))
    out = DATA / f"website-recheck-{a.name}"
    n = {"sites": 0, "contact": 0, "email": 0, "phone": 0}
    results = []
    for i, (row, site, why, got) in enumerate(done, 1):
        if not site:
            print(f"  {row['id']:<16} {row['institute'][:40]:<40} - ({why})")
            continue
        res = result(i, row, site, got)
        c = res["contacts"]
        if a.known_sites and not (c.get("contact") or c.get("generic_email") or c.get("phone")):
            continue                                    # nothing confirmed: the site is ours already, nothing to add
        n["sites"] += 1
        n["contact"] += bool(c.get("contact"))
        n["email"] += bool(c.get("generic_email") or (c.get("contact") or {}).get("email"))
        n["phone"] += bool(c.get("phone"))
        results.append(res)
        print(f"  {row['id']:<16} {row['institute'][:40]:<40} + site" + "".join(
            f", +{k}" for k, v in (("contact", c.get("contact")), ("email", c.get("generic_email")), ("phone", c.get("phone"))) if v))
    print(f"\n{len(rows)} rows: {n['sites']} websites kept, with {n['contact']} contacts, {n['email']} emails, {n['phone']} phones")
    if a.dry_run or not results:
        return
    (out / "scrape_results").mkdir(parents=True, exist_ok=True)
    for res in results:
        (out / "scrape_results" / f"{res['row_id']}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "progress.json").write_text(json.dumps({"batch": out.name, "status": "done", "total_rows": len(results),
                                                    "done": len(results), "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ")},
                                                   indent=1), encoding="utf-8")
    print("written to", out)


if __name__ == "__main__":
    main()
