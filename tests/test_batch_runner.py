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


# ---- free / fast mode, pausing when the free allowance is used up, and never getting stuck ----
HEADER = "row_id,institution_name,country_sheet,phone_1,phone_2,contact_name,designation,email,status_raw,duplicate_group,flags,seed_urls\n"


def new_batch(name, n):
    d = tmp / name
    (d / "scrape_results").mkdir(parents=True)
    (d / "numbers_clean.csv").write_text(
        HEADER + "".join(f"r{i:03d},College {i},Malta,,,,,,,,,https://c{i}.example/\n" for i in range(1, n + 1)), encoding="utf-8")
    br.run_batch.RESULTS, br.run_batch.DATA, br.run_batch.NUMBERS = d / "scrape_results", d, d / "numbers_clean.csv"
    br.STATS.update(mode="free", workers=1)
    return d


def run(d, once=True, workers=1):
    class A:
        batch, chunk_size, publish_every = "t", 25, 0
    A.once, A.workers = once, workers
    br.STATS["workers"] = workers
    br._run(A, d, d / "scrape_results", CFG)
    return json.loads((d / "progress.json").read_text(encoding="utf-8"))


def good(r):
    return {"row_id": r["row_id"], "scraped_at": "2026-10-08T00:00:00Z",
            "contacts": {"institution_name": r["institution_name"], "official_website": "https://x.example/",
                         "contact": None, "alternates": [], "source": "web"},
            "network": None, "timing": {"total_seconds": 60.0, "ai_calls": 1}}


CFG = {"max_paid_usd_per_batch": 3.0, "max_fast_usd_per_batch": 30.0}
d = new_batch("modes", 1)
check("a batch with no reader.json runs in free mode", br.reader_mode(d), "free")
br.apply_mode(d, CFG)
check("free mode: Claude Haiku never reads, and the cap is the small search cap",
      (br.pipeline.llm.FALLBACK["enabled"], br.discover.BUDGET["limit"]), (False, 3.0))
(d / "reader.json").write_text('{"mode": "fast"}', encoding="utf-8")
br.apply_mode(d, CFG)
check("fast mode: Haiku may read what Gemini refuses, inside the larger cap",
      (br.pipeline.llm.FALLBACK["enabled"], br.discover.BUDGET["limit"]), (True, 33.0))
(d / "reader.json").write_text("not json", encoding="utf-8")
check("an unreadable reader.json means free, never paid", br.reader_mode(d), "free")
(d / "SIMULATE_EXHAUSTED").write_text("", encoding="utf-8")
br.apply_mode(d, CFG)
check("the pretend-exhausted test switch is picked up from the batch folder", br.pipeline.llm.SIMULATE_EXHAUSTED, True)
(d / "SIMULATE_EXHAUSTED").unlink()
br.apply_mode(d, CFG)
check("and switched off again when the file is removed", br.pipeline.llm.SIMULATE_EXHAUSTED, False)

# daily allowance gone in free mode: stop asking, say when it is back
d = new_batch("daily", 5)
asked = []
RESET = 4102444800.0        # some fixed time in the future


def exhausted(r, cfg):
    asked.append(r["row_id"])
    if len(asked) == 1:
        return good(r)
    return {"row_id": r["row_id"], "retry": True, "quota": True, "resume_at": RESET, "daily": True,
            "reason": "Gemini free allowance used up for today", "timing": {}}


br.pipeline.scrape = exhausted
prog = run(d)
check("allowance used up: the batch goes to 'waiting' with the day's reason",
      (prog["status"], prog["waiting_kind"], prog["done"], prog["pending"]), ("waiting", "daily", 1, 4))
check("it says when the free allowance is expected back", prog["resume_at"], "2100-01-01T00:00:00+00:00")
check("the rest of the chunk is not attempted once the allowance is known to be gone", asked, ["r001", "r002"])
check("waiting for the allowance is not counted as a failed attempt", (d / "scrape_results" / "attempts.json").exists(), False)
check("the progress file records the mode and how the batch was started",
      (prog["reader_mode"], prog["fast_limit_usd"]), ("free", 30.0))

# resumed too early: nothing done again -> look again in 30 minutes, not tomorrow
br.PAUSE.update(until=RESET, daily=True)
status, extra = br.waiting_state(4, False, {"waiting_kind": "daily"}, now=1000.0)
check("still no allowance after a resume: try again in 30 minutes",
      (status, extra["resume_at"]), ("waiting", "1970-01-01T00:46:40+00:00"))
br.PAUSE.update(until=1060.0, daily=False)
status, extra = br.waiting_state(4, True, {}, now=1000.0)
check("a short refusal that outlasts the job waits at least 15 minutes",
      (status, extra["waiting_kind"], extra["resume_at"]), ("waiting", "busy", "1970-01-01T00:31:40+00:00"))
br.PAUSE.update(until=None, daily=False)
check("a job that got nothing done waits instead of starting the next job straight away",
      br.waiting_state(4, False, {}, now=1000.0)[1]["waiting_kind"], "retry")
check("a normal job with rows left just carries on", br.waiting_state(4, True, {}), ("running", {}))
check("nothing left is 'done' whatever happened before", br.waiting_state(0, False, {}), ("done", {}))

# switched to fast while paused/running: the very next institute is read
d = new_batch("switch", 3)
seen = []


def switching(r, cfg):
    seen.append((r["row_id"], br.pipeline.llm.FALLBACK["enabled"]))
    if not br.pipeline.llm.FALLBACK["enabled"]:
        (d / "reader.json").write_text('{"mode": "fast"}', encoding="utf-8")      # the operator presses the button
        return {"row_id": r["row_id"], "retry": True, "quota": True, "resume_at": RESET, "daily": True, "reason": "x", "timing": {}}
    return good(r)


br.pipeline.scrape = switching
prog = run(d)
check("a switch to fast made during a chunk applies from the next institute",
      seen, [("r001", False), ("r002", True), ("r003", True)])
check("the paused institute is picked up by the next job, and the page shows fast mode",
      (prog["status"], prog["done"], prog["pending"], prog["reader_mode"]), ("running", 2, 1, "fast"))
prog = run(d)
check("which finishes the batch", (prog["status"], prog["pending"]), ("done", 0))

# a row that can never be read must not hold the batch open for ever
d = new_batch("stuck", 2)
tries = []


def broken(r, cfg):
    tries.append(r["row_id"])
    if r["row_id"] == "r001":
        return good(r)
    if len(tries) % 2:
        raise ValueError("page exploded")
    return {"row_id": r["row_id"], "retry": True, "reason": "AI gave no usable answer", "timing": {}}


br.pipeline.scrape = broken
first, second, third = run(d), run(d), run(d)
check("a failing row is retried by later jobs", (first["status"], first["pending"]), ("running", 1))
check("a job that achieved nothing waits 15 minutes rather than looping", (second["status"], second["waiting_kind"]), ("waiting", "retry"))
check("after 3 failed attempts it is saved as unreadable and the batch finishes", (third["status"], third["pending"]), ("done", 0))
saved = json.loads((d / "scrape_results" / "r002.json").read_text(encoding="utf-8"))
check("the saved row says why", saved["contacts"]["notes"].startswith("Could not be read after 3 attempts"), True)
check("it was tried exactly 3 times", tries.count("r002"), 3)

# progress.json: Drive link survives, old waiting details do not, ETA allows for parallel work
d = new_batch("keep", 4)
br.pipeline.scrape = lambda r, cfg: good(r)
(d / "progress.json").write_text(json.dumps({"status": "waiting", "waiting_kind": "daily", "resume_at": "x", "chunk": 1,
                                             "drive_link": "https://drive.google.com/f"}), encoding="utf-8")
prog = run(d)
check("the Drive link written between chunks is kept", prog.get("drive_link"), "https://drive.google.com/f")
check("a finished batch no longer carries the waiting details", ("resume_at" in prog, "waiting_kind" in prog), (False, False))
rows4 = [{"row_id": f"r{i:03d}"} for i in range(1, 5)]
br.STATS["workers"] = 2
eta = br.write_progress(d, "t", "running", rows4 + [{"row_id": f"x{i}"} for i in range(4)], {"r001", "r002", "r003", "r004"},
                        1, 1, [60.0] * 4, [0] * 4, br.time.time())["eta_seconds"]
check("time remaining allows for institutes being read in parallel (4 left, 60s each, 2 at once)", eta, 120)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
