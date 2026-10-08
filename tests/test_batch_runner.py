"""Offline tests for scraper2/batch_runner.py: repeated institutes are scraped once, and the full merge is
spaced out as it gets slower. No network, no AI calls. Run: python tests/test_batch_runner.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp())
os.environ["PARAGON_DATA"] = str(tmp)
sys.path.insert(0, str(ROOT / "scraper2"))
sys.path.insert(0, str(ROOT / "scraper"))
import batch_runner as br  # noqa: E402

results = []


def check(name, got, want):
    ok = got == want
    results.append(ok)
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f": got {got!r}, want {want!r}"))


def row(rid, name, country="", site="", email=""):
    return {"row_id": rid, "institution_name": name, "country_sheet": country, "seed_urls": site, "email": email}


res = tmp / "scrape_results"
res.mkdir()
rows = [
    row("r001", "Constructor University Bremen", "Germany", "https://www.constructor.university/"),
    row("r002", "CONSTRUCTOR UNIVERSITY BREMEN", "Germany", "https://constructor.university/study"),   # same, other spelling
    row("r003", "Université de Lyon", "France", "", "info@univ-lyon.fr"),
    row("r004", "Universite de Lyon", "France", "", "erasmus@univ-lyon.fr"),                           # same, accents differ
    row("r005", "IES Pablo Picasso", "Spain", "https://iespicasso-malaga.es/"),
    row("r006", "IES Pablo Picasso", "Spain", "https://iespicasso-madrid.es/"),                        # same name, other school
    row("r007", "Faculty of Arts", "Italy", "https://uni.example.it/"),
    row("r008", "Faculty of Law", "Italy", "https://uni.example.it/"),                                 # same website, other institute
    row("r009", "Lycée Jean Moulin", "France"),
    row("r010", "Lycée Jean Moulin", "Belgium"),                                                       # same name, other country
    row("r011", "", "France"),
]
check("repeats are recognised across spelling, case, accents and www",
      br.mark_repeats(rows, res), {"r002", "r004"})
check("each repeat points at the first row for that institute",
      ((res / "r002.duplicate").read_text(), (res / "r004.duplicate").read_text()), ("r001", "r003"))
check("same name but a different website, or a different country, is NOT a repeat",
      [(res / f"{r}.duplicate").exists() for r in ("r006", "r010")], [False, False])
check("two institutes sharing one website are NOT merged", (res / "r008.duplicate").exists(), False)
check("running it again finds the same repeats and changes nothing", br.mark_repeats(rows, res), {"r002", "r004"})

res2 = tmp / "already"
res2.mkdir()
(res2 / "r002.json").write_text("{}", encoding="utf-8")
check("a row that already has its own result is never turned into a repeat",
      (br.mark_repeats(rows[:2], res2), (res2 / "r002.duplicate").exists()), (set(), False))

check("small batch: the merge keeps its 8-second rhythm", br.merge_gap(0.5, 8), 8)
check("large batch: a 20-second merge is spaced 80 seconds apart", br.merge_gap(20, 8), 80)

# a repeat counts as done, so the batch can finish without scraping it
(tmp / "numbers_clean.csv").write_text(
    "row_id,institution_name,country_sheet,phone_1,phone_2,contact_name,designation,email,status_raw,duplicate_group,flags,seed_urls\n"
    "r001,Alpha College,Malta,,,,,,,,,https://alpha.example/\n"
    "r002,ALPHA COLLEGE,Malta,,,,,,,,,https://alpha.example/\n", encoding="utf-8")
res3 = tmp / "scrape_results3"
res3.mkdir()
scraped = []


def fake_scrape(r, cfg):
    scraped.append(r["row_id"])
    return {"row_id": r["row_id"], "scraped_at": "2026-10-08T00:00:00Z",
            "contacts": {"institution_name": "Alpha College", "official_website": "https://alpha.example/",
                         "contact": None, "alternates": [], "source": "web"},
            "network": None, "timing": {"total_seconds": 1.0, "ai_calls": 3}}


br.pipeline.scrape = fake_scrape
br.run_batch.RESULTS, br.run_batch.DATA, br.run_batch.NUMBERS = res3, tmp, tmp / "numbers_clean.csv"


class Args:
    batch, chunk_size, workers, once, publish_every = "t", 25, 2, False, 0


br._run(Args, tmp, res3, {})
prog = json.loads((tmp / "progress.json").read_text(encoding="utf-8"))
check("the repeated institute is scraped once", scraped, ["r001"])
check("the batch still finishes with nothing pending", (prog["status"], prog["done"], prog["pending"]), ("done", 2, 0))
check("AI calls are counted in the progress file", prog["ai_calls"], 3)
import csv  # noqa: E402
enriched = list(csv.DictReader(open(tmp / "institutions_enriched.csv", encoding="utf-8-sig")))
check("both rows appear in the results, the repeat marked as such",
      [(e["row_id"], e["duplicate_of"], e["official_website"]) for e in enriched],
      [("r001", "", "https://alpha.example/"), ("r002", "r001", "https://alpha.example/")])

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
