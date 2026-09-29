"""Measure the FREE Gemini provider against Claude's answers on the same pages. Saves nothing.

Setup (2 minutes, free, no card):
    1. https://aistudio.google.com/apikey  ->  Create API key
    2. put   GEMINI_API_KEY=...   in .env
Run:
    python scraper2/try_gemini.py            # lists your available models, then tests 6 institutes
    python scraper2/try_gemini.py --model gemini-2.5-flash-lite --rows r018,r020,r021

For each institute already answered by Claude it rebuilds the same evidence pack from the page cache (no new
crawling), asks Gemini, resolves the answer the same way, and compares:
  same primary contact?   number of partners found   seconds
so you can decide, with numbers, whether the free model is good enough to switch to.
"""
import argparse
import csv
import json
import os
import re
import sys
import time
import unicodedata
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scraper2"))
sys.path.insert(0, str(ROOT / "scraper"))
import crawl  # noqa: E402
import discover  # noqa: E402
import evidence  # noqa: E402
import llm  # noqa: E402
import pipeline  # noqa: E402


def _n(s):
    s = unicodedata.normalize("NFKD", (s or "").lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def same_person(a, b):
    ta, tb = set(re.findall(r"[a-z]{3,}", _n(a))), set(re.findall(r"[a-z]{3,}", _n(b)))
    return bool(ta and tb and len(ta & tb) >= min(2, len(ta), len(tb)))


def load_env():
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def list_models(key):
    req = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models?key={key}&pageSize=200")
    with urllib.request.urlopen(req, timeout=20) as r:
        return [m["name"].split("/")[-1] for m in json.loads(r.read().decode())["models"]
                if "generateContent" in m.get("supportedGenerationMethods", [])]


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    load_env()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default=llm.DEFAULT_MODELS["gemini"])
    ap.add_argument("--rows", default="r018,r020,r021,r003,r016,r040")
    a = ap.parse_args()
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise SystemExit("GEMINI_API_KEY is not set. Get a free key at https://aistudio.google.com/apikey and add it to .env")

    names = list_models(key)
    flash = [n for n in names if "flash" in n]
    print("models your key can use (flash family):", ", ".join(flash[:12]))
    if a.model not in names:
        raise SystemExit(f"model '{a.model}' is not available to this key. Re-run with --model <one of the names above>.")

    rows = {r["row_id"]: r for r in csv.DictReader(open(ROOT / "data" / "numbers_clean.csv", encoding="utf-8-sig"))}
    memo = discover.load_memo()
    print(f"\n{'row':5} {'claude contact':26} {'gemini contact':26} {'same':5} {'partners C/G':13} {'secs':5}")
    agree = tot = 0
    for rid in a.rows.split(","):
        f = ROOT / "data" / "scrape_results" / f"{rid}.json"
        if not f.exists() or rid not in memo:
            print(f"{rid:5} (no Claude result / website to compare with)")
            continue
        d = json.loads(f.read_text(encoding="utf-8"))
        cc = ((d["contacts"] or {}).get("contact") or {}).get("name") or ""
        cp = len((d.get("network") or {}).get("external_collaboration") or [])
        inst = {"name": rows[rid]["institution_name"], "country": rows[rid]["country_sheet"], "phone": rows[rid]["phone_1"],
                "contact": rows[rid]["contact_name"]}
        pack = evidence.build_pack(crawl.crawl(memo[rid], max_pages=10)["pages"])
        t = time.time()
        try:
            data, _ = llm.extract(inst, pack, "gemini", a.model)
        except llm.LLMError as e:
            print(f"{rid:5} Gemini failed: {str(e)[:120]}")
            continue
        secs = time.time() - t
        contacts = pipeline.boost_on_file(pipeline.resolve_contacts(data, pack), inst["contact"])
        gc = contacts[0]["name"] if contacts else ""
        partners = pipeline.resolve_network(data, pack, inst["name"])[0]
        ok = same_person(cc, gc)
        tot += 1 if cc else 0
        agree += 1 if (cc and ok) else 0
        print(f"{rid:5} {cc[:25]:26} {gc[:25]:26} {('YES' if ok else ('-' if not cc and not gc else 'NO')):5} {cp:>5}/{len(partners):<7} {secs:5.1f}")
    print(f"\nGemini agreed with Claude on {agree} of {tot} contacts Claude found. Free tier: cost $0.")


if __name__ == "__main__":
    main()
