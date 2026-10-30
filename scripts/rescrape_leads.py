"""Failed dialer leads -> new numbers, as a file for a manager to upload (the CRM owner's Option A: docs/02).

A manager takes the failed leads (Unreachable, Wrong Number) from the batch's Excel in Drive / Batches and saves them as
a CSV with institute, country, phone and status columns. For each institute this script looks in the scraper's reuse
index for a number not tried yet:
  - found     -> a row in the CRM upload file (the owner's template), with --out;
  - not found -> with --push, a priority rescrape batch. It scrapes again without reusing the old result, and when it
                 finishes its "<batch> CRM upload.csv" lands in Drive / Results, with the tried numbers left out.
Nothing is sent to the CRM. The manager uploads the file and picks Assign to, so Sarah's leads go back to her.

    python scripts/rescrape_leads.py --file failed.csv [--out upload.csv] [--push]

Numbers already tried are kept only as hashes (data_hei/rescrape/<batch>.json): the repository is public. The upload
file holds phone numbers, so its default folder rescrape_output/ is git-ignored.
"""
import argparse
import csv
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data_hei"
sys.path.insert(0, str(ROOT / "scraper2"))
import dialer_file as df  # noqa: E402
import reuse  # noqa: E402

HEADERS = {"institute": ("institute", "institution", "institution name", "institute name"), "country": ("country",),
           "phone": ("phone",), "phone2": ("phone2", "phone 2", "second phone"), "phone3": ("phone3", "phone 3"),
           "email": ("email",), "status": ("status",)}


def read_failed(path):
    """The failed leads from the manager's CSV: headings in any case; a Status column keeps only the failed ones."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        table = list(csv.DictReader(f))
    leads = []
    for row in table:
        low = {str(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        lead = {field: next((low[n] for n in names if low.get(n)), "") for field, names in HEADERS.items()}
        if lead["status"] and not any(w in lead["status"].lower().replace("_", " ") for w in ("unreachable", "wrong")):
            continue
        leads.append(lead)
    return leads


def tried_hashes(lead):
    return {h for h in (df.phone_hash(lead.get("phone")), df.phone_hash(lead.get("phone2")), df.phone_hash(lead.get("phone3")),
                        df.email_hash(lead.get("email"))) if h}


def group_leads(leads):
    """One entry per institute: {key: {institute, country, tried (hashes), status}}."""
    groups = {}
    for lead in leads:
        inst = lead.get("institute", "").strip()
        if not inst:
            continue
        country = lead.get("country", "").strip()
        g = groups.setdefault(df.inst_key(inst, country), {"institute": inst, "country": country, "tried": set(),
                                                           "status": lead.get("status") or ""})
        g["tried"] |= tried_hashes(lead)
    return groups


def _reuse_key(institute, country):
    n = df._norm(institute)
    return f"n:{n}|{df._norm(country)}" if len(n) >= 4 else None


def _earlier_result(data_root, institute, country):
    rkey = _reuse_key(institute, country)
    hit = reuse.load_index(data_root).get(rkey) if rkey else None
    if not hit:
        return None
    try:
        import batch_runner
        return reuse._batch_results(data_root, hit["b"], fetch_archive=batch_runner.fetch_archive).get(hit["r"])
    except Exception:
        return None


def classify(groups, data_root, lookup=None):
    """(rows for the upload file, groups to scrape again). lookup(institute, country) -> earlier result or None."""
    lookup = lookup or (lambda i, c: _earlier_result(data_root, i, c))
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows, rescrape = [], {}
    for key, g in groups.items():
        row = {"row_id": "x", "institution_name": g["institute"], "country_sheet": g["country"]}
        found, _ = df.file_rows([row], {"x": lookup(g["institute"], g["country"])}, {key: g["tried"]},
                                note=f"New number after {g['status'] or 'a failed call'} ({today})")
        if found:
            rows += found
        else:
            rescrape[key] = g
    return rows, rescrape


def queue_rescrape(groups, data_root, now):
    """Writes the upload CSV, the priority queue request and the tried-numbers map. Returns (batch, paths written)."""
    batch = "rescrape-" + now.strftime("%Y%m%d-%H%M")
    up = data_root / "uploads" / f"{batch}.csv"
    up.parent.mkdir(parents=True, exist_ok=True)
    with open(up, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["institute", "country"])
        w.writerows([g["institute"], g["country"]] for g in groups.values())
    req = data_root / "_queue_requests" / f"{batch}.json"
    req.parent.mkdir(parents=True, exist_ok=True)
    req.write_text(json.dumps({"batch": batch, "source": "dialer rescrape", "added": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                               "reader": "paid", "priority": True, "kind": "rescrape"}, indent=1), encoding="utf-8")
    tried = data_root / "rescrape" / f"{batch}.json"
    tried.parent.mkdir(parents=True, exist_ok=True)
    tried.write_text(json.dumps({"institutes": {k: {"institute": g["institute"], "country": g["country"],
                                                    "tried": sorted(g["tried"]), "status": g["status"]}
                                                for k, g in groups.items()}}, ensure_ascii=False, indent=1), encoding="utf-8")
    return batch, [up, req, tried]


def _git(*args):
    subprocess.run(["git", *args], cwd=ROOT, check=True)


def push(paths, message):
    _git("add", *[str(p) for p in paths])
    _git("commit", "-q", "-m", message)
    _git("pull", "--rebase", "--autostash", "-q")
    _git("push", "-q")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", required=True, help="The failed leads, as CSV")
    ap.add_argument("--out", help="The CRM upload file to write (default rescrape_output/Rescrape <date> CRM upload.csv)")
    ap.add_argument("--push", action="store_true", help="Also queue the institutes with no new number for a rescrape, and push that")
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    groups = group_leads(read_failed(args.file))
    rows, rescrape = classify(groups, DATA)
    print(f"{len(groups)} institute(s): {len(rows)} row(s) with a new number, {len(rescrape)} to scrape again")
    if rows:
        out = Path(args.out) if args.out else ROOT / "rescrape_output" / f"Rescrape {now:%Y-%m-%d %H%M} CRM upload.csv"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(df.csv_text(rows), encoding="utf-8-sig")
        print(f"Upload this in the CRM (admin panel > Leads): {out}")
    if rescrape and args.push:
        batch, written = queue_rescrape(rescrape, DATA, now)
        push(written, f"rescrape: {len(rescrape)} institute(s) queued [automated]")
        print(f"Queued {batch}. Its CRM upload file will land in Drive / Results when it finishes.")
    elif rescrape:
        print("Not queued: run again with --push to scrape them again.")


if __name__ == "__main__":
    main()
