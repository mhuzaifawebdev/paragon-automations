"""Scrapes again the institutes where no website was found: first in the queue, without reusing the old result.

Finished batches keep only their counts, so the list comes from the public institute_counts.json. Repeated names are
scraped once (the dashboard applies the result to every row of that name), and broken names are skipped. An institute
already sent in an earlier website-recheck batch is not sent again.

    python scripts/recheck_websites.py --education 30 --other 20 --retail 10 --name pilot
    python scripts/recheck_websites.py --education 9999 --other 9999 --name full --chunk 500

Writes data_hei/uploads/website-recheck-<name>[-pN].csv and its queue request. Push them to start.
"""
import argparse
import csv
import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data_hei"
sys.path.insert(0, str(ROOT / "scraper2"))
sys.path.insert(0, str(ROOT / "scripts"))
import dialer_file as df  # noqa: E402
from recheck_no_contact import clean_name  # noqa: E402

PREFIX = "website-recheck-"
EDUCATION = re.compile(r"univ|uniw|hochschul|college|colegi|school|schule|ecole|école|lycée|lycee|liceo|instit|\bies\b|cefp|cifp|"
                       r"escola|akadem|academ|wyzsz|wyższ|fakult|facult|szkol|szkoł|istituto|gimnaz|gymnas|politec|polytech", re.I)


def group_of(row):
    if "retail" in row["batch"]:
        return "retail"
    return "education" if EDUCATION.search(row["name"]) else "other"


def choose(rows, wanted, seed=11):
    """{group: names to send}: no website, a usable name, each name once, none already rechecked."""
    sent = {df._norm(r["name"]) for r in rows if r["batch"].startswith(PREFIX)}
    pools, seen = {"education": [], "other": [], "retail": []}, set()
    for r in rows:
        key = df._norm(r["name"])
        if r.get("website") or r["batch"].startswith(PREFIX) or key in sent or key in seen or not clean_name(r["name"]):
            continue
        seen.add(key)
        pools[group_of(r)].append(r["name"])
    rng = random.Random(seed)
    return {g: rng.sample(sorted(p), min(wanted.get(g, 0), len(p))) for g, p in pools.items()}


def write_batches(names, name, data=DATA, chunk=500, now="2026-11-01T00:00:00Z"):
    parts = [names[i:i + chunk] for i in range(0, len(names), chunk)]
    written = []
    for n, part in enumerate(parts, 1):
        batch = f"{PREFIX}{name}" + (f"-p{n}" if len(parts) > 1 else "")
        up = Path(data) / "uploads" / f"{batch}.csv"
        up.parent.mkdir(parents=True, exist_ok=True)
        with open(up, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["institute", "country"])
            w.writerows([x, ""] for x in part)
        req = Path(data) / "_queue_requests" / f"{batch}.json"
        req.parent.mkdir(parents=True, exist_ok=True)
        req.write_text(json.dumps({"batch": batch, "source": "website re-check", "added": now, "reader": "paid",
                                   "priority": True, "kind": "rescrape"}, indent=1), encoding="utf-8")
        written += [up, req]
    return written


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--education", type=int, default=0)
    ap.add_argument("--other", type=int, default=0)
    ap.add_argument("--retail", type=int, default=0)
    ap.add_argument("--name", required=True)
    ap.add_argument("--chunk", type=int, default=500)
    a = ap.parse_args()
    rows = json.loads((DATA / "institute_counts.json").read_text(encoding="utf-8"))["institutes"]
    picked = choose(rows, {"education": a.education, "other": a.other, "retail": a.retail})
    names = [x for g in picked.values() for x in g]
    print({g: len(v) for g, v in picked.items()}, "total", len(names))
    for p in write_batches(names, a.name, chunk=a.chunk):
        print(p)


if __name__ == "__main__":
    main()
