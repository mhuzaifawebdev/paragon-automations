"""Import a TEAM'S OWN batch (a plain CSV, or any Google Sheet + tab) into its own workspace, the
same shape scraper2/run.py and scraper/run_batch.merge() already read. This generalizes
scraper2/import_hei.py, which stays as-is for the one hardcoded client sheet it was built for.

    python scraper2/import_batch.py --csv my_institutes.csv --batch teamA
    python scraper2/import_batch.py --sheet-url https://docs.google.com/spreadsheets/d/XXX/edit --tab Sheet1 --batch teamA

Then, same as any other batch:
    PARAGON_DATA=data_hei/teamA python scraper2/batch_runner.py --batch teamA --chunk-size 25 --provider gemini

Column names in your CSV/Sheet don't have to match exactly - common variants are recognized (see
ALIASES below). Anything required that isn't found fails loudly, naming exactly which column is
missing, rather than silently importing a blank column.
"""
import argparse
import csv
import io
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

REQUIRED = ["institution_name"]
# Each output field maps to whichever of these header spellings appears first, case/spacing-insensitive.
ALIASES = {
    "institution_name": ["institution_name", "institution", "name", "university", "school", "college"],
    "country_sheet": ["country_sheet", "country"],
    "phone_1": ["phone_1", "phone", "phone1", "generic_phone", "contact_phone", "primary_phone"],
    "phone_2": ["phone_2", "phone2", "work_phone", "secondary_phone"],
    "contact_name": ["contact_name", "contact", "on_file_name", "name_on_file", "person"],
    "designation": ["designation", "role", "title", "position"],
    "email": ["email", "contact_email", "email_address"],
    "status_raw": ["status_raw", "status", "remarks", "notes"],
    "seed_urls": ["seed_urls", "website", "website_url", "url", "site"],
}
OUT_FIELDS = ["row_id", "institution_name", "country_sheet", "phone_1", "phone_2", "contact_name",
              "designation", "email", "status_raw", "duplicate_group", "flags", "seed_urls"]


class ImportError_(Exception):
    pass


def _norm(s):
    s = unicodedata.normalize("NFKD", (s or "")).lower()
    return re.sub(r"[^a-z0-9]", "", s)


def _map_headers(fieldnames):
    """Returns {out_field: source_header_name}. Raises ImportError_ naming any missing required field."""
    normed = {_norm(h): h for h in fieldnames}
    mapping, missing = {}, []
    for out_field, aliases in ALIASES.items():
        found = next((normed[_norm(a)] for a in aliases if _norm(a) in normed), None)
        if found:
            mapping[out_field] = found
        elif out_field in REQUIRED:
            missing.append(f"{out_field} (tried: {', '.join(aliases)})")
    if missing:
        raise ImportError_("Missing required column(s) in your file - none of the expected header spellings "
                           "were found: " + "; ".join(missing) + f". Headers seen: {', '.join(fieldnames)}")
    return mapping


def rows_from_csv_text(text):
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ImportError_("File has no header row / is empty.")
    mapping = _map_headers(reader.fieldnames)
    return [{out: (r.get(src) or "").strip() for out, src in mapping.items()} for r in reader]


def rows_from_sheet(sheet_url, tab):
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    m = re.search(r"/d/([a-zA-Z0-9_-]+)", sheet_url)
    if not m:
        raise ImportError_(f"Could not find a spreadsheet id in: {sheet_url}")
    sheet_id = m.group(1)
    creds = service_account.Credentials.from_service_account_file(
        str(ROOT / "credentials" / "google_service_account.json"),
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
    svc = build("sheets", "v4", credentials=creds).spreadsheets().values()
    values = svc.get(spreadsheetId=sheet_id, range=f"'{tab}'!A1:Z2000").execute().get("values", [])
    if not values:
        raise ImportError_(f"Tab '{tab}' is empty or not found. Share the sheet with the service account first.")
    header, body = values[0], values[1:]
    mapping = _map_headers(header)
    idx = {h: i for i, h in enumerate(header)}
    out = []
    for r in body:
        r = r + [""] * (len(header) - len(r))
        out.append({out_field: r[idx[src]].strip() for out_field, src in mapping.items()})
    return out


def build_rows(raw_rows):
    numbers = []
    for k, r in enumerate(raw_rows, start=1):
        if not r.get("institution_name"):
            continue
        rid = f"r{k:03d}"
        seeds = " ".join(u for u in re.split(r"[\s,;]+", r.get("seed_urls") or "") if u.startswith("http"))
        row = {f: r.get(f, "") for f in OUT_FIELDS}
        row.update(row_id=rid, seed_urls=seeds, duplicate_group="", flags="")
        numbers.append(row)
    return numbers


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", help="path to a CSV file")
    ap.add_argument("--sheet-url", help="any Google Sheet URL (must be shared with the service account)")
    ap.add_argument("--tab", default="Sheet1", help="tab name, only used with --sheet-url")
    ap.add_argument("--batch", required=True, help="a short name for this batch, e.g. teamA")
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()

    if bool(a.csv) == bool(a.sheet_url):
        ap.error("give exactly one of --csv or --sheet-url")

    try:
        raw = rows_from_csv_text(Path(a.csv).read_text(encoding="utf-8-sig")) if a.csv \
            else rows_from_sheet(a.sheet_url, a.tab)
    except ImportError_ as e:
        raise SystemExit(f"Import failed: {e}")

    if a.limit:
        raw = raw[:a.limit]
    numbers = build_rows(raw)
    if not numbers:
        raise SystemExit("No usable rows found (every row was missing an institution name).")

    out = ROOT / "data_hei" / a.batch
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "numbers_clean.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=OUT_FIELDS)
        w.writeheader()
        w.writerows(numbers)
    # No hei_map.csv for a generic import (there is no "on file" contact to compare against and no
    # second-sheet row number to remember) - scraper2/run.py works fine without one, and
    # build_hei_sheet.py's read_map() now tolerates a missing hei_map.csv (fixed alongside this)
    # for exactly this case.

    print(f"{a.batch}: {len(numbers)} institutes -> {out}/numbers_clean.csv | "
          f"with a seed URL to read first: {sum(1 for n in numbers if n['seed_urls'])}")


if __name__ == "__main__":
    main()
