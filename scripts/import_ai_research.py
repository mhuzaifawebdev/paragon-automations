"""Imports contacts researched outside the scraper (an AI tool or a person filling ai_research_*.csv) into the
re-check results, keeping ONLY what the cited page really shows.

Each row's id is <re-check part>/<row id>, e.g. b-deeper-p3/r051 -> data_hei/website-recheck-contact-b-deeper-p3/
scrape_results/r051.json. For every row, the source page (and any further page named in notes) is downloaded and:
  - the contact name is kept only if every word of it is on that page; the job title only with the name
  - an email only if the address is on the page (obfuscated '(at)' forms count)
  - a phone only if its last 8 digits are on the page
A row whose notes say the match is unsure ('could not confirm', 'possible match', 'may be outdated', 'not confirmed')
gives no contact. Nothing already in the result is overwritten.

    python scripts/import_ai_research.py ai_research_500_contacts.csv            # check and write
    python scripts/import_ai_research.py ai_research_500_contacts.csv --dry-run  # only report
"""
import argparse
import csv
import json
import re
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scraper"))
import fetch_pages  # noqa: E402

DATA = ROOT / "data_hei"
UNSURE = re.compile(r"could not confirm|possible match|may be outdated|not confirmed|not fully confirmed|confirm still|"
                    r"so confirm|unconfirmed|presumably|first name only|first name not given", re.I)
URL = re.compile(r"https?://[^\s,;)\"']+")


def _n(s):
    s = unicodedata.normalize("NFKD", (s or "").lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def page_text(url, cache):
    if url not in cache:
        try:
            rec = fetch_pages.fetch(url)
            cache[url] = _n(rec.get("text", "")).replace("(at)", "@").replace("[at]", "@").replace(" at ", "@")
        except Exception:
            cache[url] = ""
    return cache[url]


def name_ok(name, text):
    words = [w for w in re.findall(r"[a-z]{3,}", _n(name)) if w not in {"prof", "ssa", "dott", "ing", "rer", "nat"}]
    return len(words) >= 2 and all(w in text for w in words)


def phone_ok(phone, text):
    d = re.sub(r"\D", "", phone or "")
    return len(d) >= 8 and d[-8:] in re.sub(r"\D", "", text)


def check(row, cache):
    """{field: value} of what the cited pages confirm."""
    urls = [u.rstrip(".") for u in [row.get("source_url", "")] + URL.findall(row.get("notes", "")) if u]
    texts = [page_text(u, cache) for u in urls[:3]]
    text = " ".join(texts)
    out = {"source_url": urls[0] if urls else ""}
    if not text:
        return out
    name = (row.get("contact_name") or "").strip()
    if name and not UNSURE.search(row.get("notes", "")) and name_ok(name, text):
        out.update(name=name, designation=(row.get("job_title") or "").strip())
    for key in ("contact_email", "general_email"):
        e = (row.get(key) or "").strip().lower()
        if e and "@" in e and e in text:
            out[key] = e
    ph = (row.get("contact_phone") or "").strip()
    if ph and phone_ok(ph, text):
        out["phone"] = ph
    return out


def apply(res, got):
    """Adds what was confirmed to one result. Returns the fields added."""
    c = res.setdefault("contacts", {})
    added = []
    if got.get("name") and not c.get("contact"):
        c["contact"] = {"name": got["name"], "designation": got.get("designation", ""), "designation_local": "",
                        "email": got.get("contact_email", ""), "office_email": "", "phone": got.get("phone", ""),
                        "source_url": got["source_url"], "evidence_quote": f"{got['name']} - {got.get('designation', '')}",
                        "role_class": "other", "why_chosen": "Found by outside research; checked on the cited page."}
        c.update(rung="Outside research (name checked on the cited page)", needs_human_check=not got.get("contact_email"))
        added.append("contact")
    email = got.get("general_email") or ("" if "contact" in added else got.get("contact_email", ""))
    if email and not c.get("generic_email") and not (c.get("contact") or {}).get("email"):
        c.update(generic_email=email, generic_email_url=got["source_url"])
        added.append("email")
    if got.get("phone") and not c.get("phone"):
        c["phone"] = {"value": got["phone"], "source_url": got["source_url"]}
        added.append("phone")
    if added:
        c["outside_research"] = got["source_url"]
    return added


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    with open(a.csv, newline="", encoding="utf-8-sig") as fh:
        rows = [r for r in csv.DictReader(fh) if r.get("source_url") or URL.search(r.get("notes", ""))]
    cache = {}
    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        checked = list(pool.map(lambda r: (r, check(r, cache)), rows))
    totals = {"contact": 0, "email": 0, "phone": 0, "rejected": 0}
    for row, got in checked:
        part, _, rid = row["id"].partition("/")
        path = DATA / f"website-recheck-contact-{part}" / "scrape_results" / f"{rid}.json"
        if not path.exists():
            print(f"  {row['id']}: no such result, skipped")
            continue
        res = json.loads(path.read_text(encoding="utf-8"))
        added = apply(res, got)
        claimed = any(row.get(k) for k in ("contact_name", "contact_email", "general_email", "contact_phone"))
        if claimed and not added:
            totals["rejected"] += 1
        for k in added:
            totals[k] += 1
        print(f"  {row['id']:<22} {row['institute'][:38]:<38} {'+' + ', +'.join(added) if added else '-'}")
        if added and not a.dry_run:
            path.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{len(rows)} rows with a source: +{totals['contact']} contacts, +{totals['email']} emails, "
          f"+{totals['phone']} phones; {totals['rejected']} rows had nothing the cited page confirms")


if __name__ == "__main__":
    main()
