"""Re-checks the institutes that have a website but no named contact, with the newer contact rules (head teacher /
principal, then English, language or EU-project teacher). Only those institutes are scraped again; nothing else.

    python scripts/recheck_no_contact.py --prepare     # every batch: set those institutes aside to be scraped again
    python scripts/recheck_no_contact.py --finish      # batches whose re-check is complete: restore their settings
    python scripts/recheck_no_contact.py --list        # batches with institutes still waiting for the re-check

--prepare moves each such result to <batch>/scrape_results_prev/ (scraper2/batch_runner.py then treats the institute
as not done, and keep_better() never lets the new result be worse than the old one), switches the batch to the free
reader for the re-check (the earlier choice is kept in the RECHECK marker and put back by --finish) and gives the
re-check its own spending record (paid_usage_recheck.json), capped like any batch.
"""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data_hei"
# Raised whenever the contact rules improve, so institutes re-checked under older rules are re-checked again.
# Round 2: contacts-only second read, named staff with their own email accepted, leadership pages crawled.
ROUND = 2


def batches(data=DATA):
    return sorted(p for p in Path(data).iterdir() if p.is_dir() and p.name != "uploads" and (p / "numbers_clean.csv").exists())


def needs_recheck(result):
    c = result.get("contacts") or {}
    return (bool(c.get("official_website")) and not c.get("contact")
            and not str(c.get("notes") or "").startswith("Website not found")
            and int(result.get("rechecked") or 0) < ROUND)          # True (round 1) counts as 1


def prepare(bdir):
    """Returns how many institutes of this batch were set aside for the re-check."""
    prev = bdir / "scrape_results_prev"
    moved = []
    for f in sorted((bdir / "scrape_results").glob("r*.json")):
        try:
            res = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if needs_recheck(res):
            prev.mkdir(exist_ok=True)
            res["rechecked"] = ROUND             # a second --prepare in the same round never sets it aside again
            (prev / f.name).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
            f.unlink()
            moved.append(f.stem)
    if not moved:
        return 0
    marker, reader = bdir / "RECHECK", bdir / "reader.json"
    if not marker.exists():
        before = json.loads(reader.read_text(encoding="utf-8")) if reader.exists() else None
        marker.write_text(json.dumps({"reader_before": before}), encoding="utf-8")
    reader.write_text(json.dumps({"mode": "free"}), encoding="utf-8")
    attempts = bdir / "scrape_results" / "attempts.json"
    if attempts.exists():                        # earlier failures must not count against the re-check
        seen = json.loads(attempts.read_text(encoding="utf-8"))
        attempts.write_text(json.dumps({k: v for k, v in seen.items() if k not in moved}), encoding="utf-8")
    progress = bdir / "progress.json"
    if progress.exists():
        p = json.loads(progress.read_text(encoding="utf-8"))
        p.update(status="running", pending=len(moved))
        progress.write_text(json.dumps(p, ensure_ascii=False, indent=1), encoding="utf-8")
    return len(moved)


def pending(bdir):
    prev = bdir / "scrape_results_prev"
    return len(list(prev.glob("r*.json"))) if prev.exists() else 0


def finish(bdir):
    """Once every set-aside institute was scraped again: put the batch's reader choice back. True if finished."""
    marker = bdir / "RECHECK"
    if not marker.exists() or pending(bdir):
        return False
    before = json.loads(marker.read_text(encoding="utf-8")).get("reader_before")
    reader = bdir / "reader.json"
    if before:
        reader.write_text(json.dumps(before), encoding="utf-8")
    elif reader.exists():
        reader.unlink()
    marker.unlink()
    prev = bdir / "scrape_results_prev"
    if prev.exists():
        prev.rmdir()
    return True


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--prepare", action="store_true")
    g.add_argument("--finish", action="store_true")
    g.add_argument("--list", action="store_true")
    a = ap.parse_args()
    for b in batches():
        if a.prepare:
            n = prepare(b)
            if n:
                print(f"{b.name}: {n} institute(s) set aside for the re-check")
        elif a.finish:
            if finish(b):
                print(f"{b.name}: re-check complete")
        elif pending(b):
            print(b.name)


if __name__ == "__main__":
    main()
