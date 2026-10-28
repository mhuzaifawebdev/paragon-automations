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
    # "name" is listed LAST on purpose: a file very often has a separate "Name" column that means
    # the CONTACT person's first name (as this real sheet does: University + ...+ Name + Surname),
    # not the institution. Specific institutional terms must win that collision; "name" only
    # applies as a last resort when nothing more specific is present.
    "institution_name": ["institution_name", "institution", "university", "school", "college",
                         "hei", "hei_name", "hei name", "organization", "organisation",
                         "partner", "partner_name", "partner name", "institute", "institute_name",
                         "erasmus_institution", "beneficiary", "applicant", "applicant_name",
                         "sending_institution", "receiving_institution",
                         "company_name", "company name", "companyname", "company",
                         "coordinating organisation name", "coordinator organisation name",
                         "retail stores", "retail_stores",
                         "name"],
    "country_sheet": ["country_sheet", "country", "hei_country", "hei_country_name",
                      "partner_country", "country_of_hei"],
    "phone_1": ["phone_1", "phone", "phone1", "generic_phone", "contact_phone", "primary_phone",
                "contact_work_phone_number", "work_phone_number"],
    "phone_2": ["phone_2", "phone2", "work_phone", "secondary_phone", "contact_generic_phone_number"],
    "contact_name": ["contact_name", "contact", "on_file_name", "name_on_file", "person"],
    "designation": ["designation", "role", "title", "position", "erasmus_director"],
    "email": ["email", "contact_email", "email_address", "contact_work_email", "contact_generic_email"],
    "status_raw": ["status_raw", "status", "remarks", "notes"],
    "seed_urls": ["seed_urls", "website", "website_url", "url", "site"],
}
OUT_FIELDS = ["row_id", "institution_name", "country_sheet", "phone_1", "phone_2", "contact_name",
              "designation", "email", "status_raw", "duplicate_group", "flags", "seed_urls"]

# When a file has no single "contact name" column but splits it into a first/last name pair (very
# common: "Name" + "Surname", or "First Name" + "Last Name"), combine them instead of leaving
# contact_name blank. Only used as a fallback, when nothing in ALIASES["contact_name"] matched.
FIRST_NAME_ALIASES = ["first_name", "firstname", "given_name", "name"]
LAST_NAME_ALIASES = ["last_name", "lastname", "surname", "family_name"]


def _find_header(fieldnames, aliases):
    normed = {_norm(h): h for h in fieldnames}
    return next((normed[_norm(a)] for a in aliases if _norm(a) in normed), None)


class ImportError_(Exception):
    pass


def _norm(s):
    s = unicodedata.normalize("NFKD", (s or "")).lower()
    return re.sub(r"[^a-z0-9]", "", s)


NAME_HEADER_WORDS = ("institution", "university", "organisation", "organization", "school", "college", "augstskola")
NOT_NAME_HEADER_WORDS = ("type", "country", "city", "code", "website", "email", "phone", "address", "size", "category")


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
    if "institution_name" not in mapping:
        # a header that merely contains an institution word, e.g. "Augstskola / Institution"
        found = next((h for n, h in normed.items() if any(w in n for w in NAME_HEADER_WORDS)
                      and not any(w in n for w in NOT_NAME_HEADER_WORDS)), None)
        # a place list (Google Maps export): "title" is the place's name when the sheet also has city/street
        if not found and "title" in normed and ("city" in normed or "street" in normed):
            found = normed["title"]
            if mapping.get("designation") == found:
                del mapping["designation"]
        if found:
            mapping["institution_name"] = found
            missing = [m for m in missing if not m.startswith("institution_name")]
    if "seed_urls" not in mapping:
        site = next((h for n, h in normed.items() if "website" in n), None)
        if site:
            mapping["seed_urls"] = site
    if missing:
        raise ImportError_("Missing required column(s) in your file - none of the expected header spellings "
                           "were found: " + "; ".join(missing) + f". Headers seen: {', '.join(fieldnames)}")
    return mapping


NAME_WORDS = ("univers", "colleg", "kolegij", "school", "schule", "scuola", "escuela", "escola", "lyc", "gymnas",
              "akadem", "academ", "instit", "istitut", "hochschule", "conservator", "polytech", "politec", "facult",
              "fakult", "agrupamento", "centro", "centre", "center", "bildung", "liceo", "szkol", "uniwersytet",
              "egyetem", "koulu", "skola", "skole", "hogeschool", "hogskola", "consorzi", "stichting", "verein",
              "association", "foundation", "fundac", "ministry", "committee", "board")
ERASMUS_SITE = "erasmus-plus.ec.europa.eu"


def _cell_kind(v):
    v = (v or "").strip()
    if not v:
        return ""
    low = v.lower()
    if low.startswith(("http", "www.")) or "@" in v:
        return "link"
    if re.fullmatch(r"[\d\s.,+()#/:-]+", v) or v.startswith("#"):
        return "number"
    return "text"


def _name_like(v):
    v = (v or "").strip()
    return (_cell_kind(v) == "text" and len(v) >= 8 and v.lower() != "erasmus+"
            and sum(c.isalpha() for c in v) >= len(v) / 2)


def _looks_like_data(first_row):
    """A header row holds labels; a data row holds a number, a link or an email somewhere."""
    return any(_cell_kind(c) in ("link", "number") for c in first_row)


def _rows_headerless(text):
    """A sheet with no header row: row 1 is already an institute. The name column is picked from the data
    (most name-like values, institution words count extra). Erasmus+ project exports are read by position:
    a project row starts 'Erasmus+' with the coordinator in col 12 and its country in col 13, and is followed by
    a summary row naming the same institute in col 1, so names are de-duplicated."""
    table = [r for r in csv.reader(io.StringIO(text)) if any(c.strip() for c in r)]
    erasmus = lambda r: (r[0] or "").strip().lower() == "erasmus+" and len(r) >= 13   # noqa: E731
    plain = [r for r in table if not erasmus(r)]
    width = max((len(r) for r in plain), default=0)
    best, best_score = None, 0.0
    for j in range(width):
        vals = [(r[j] if j < len(r) else "").strip() for r in plain]
        filled = [v for v in vals if v]
        if not filled:
            continue
        like = sum(_name_like(v) for v in vals) / len(plain)
        words = sum(any(w in v.lower() for w in NAME_WORDS) for v in filled) / len(plain)
        distinct = len(set(v.lower() for v in filled)) / len(filled)
        score = like * 0.5 + words + distinct * 0.5 if like >= 0.4 else 0.0
        if score > best_score:
            best, best_score = j, score
    if best is None and len(plain) > len(table) / 2:
        raise ImportError_("No column of institute names was found (the sheet has no header row and no column "
                           "where most values look like institution names).")
    out, seen, pairs = [], set(), len(plain) < len(table)
    for r in table:
        country = ""
        if erasmus(r):
            name, country = r[11].strip(), r[12].strip()
        else:
            name = (r[best] if best is not None and best < len(r) else "").strip()
            if _cell_kind(name) != "text":
                name = next((c.strip() for c in r if _name_like(c) and any(w in c.lower() for w in NAME_WORDS)), "")
        key = _norm(name)
        if not name or (pairs and key in seen):     # only an Erasmus+ export names each institute twice
            continue
        seen.add(key)
        site = next((c.strip() for c in r if c.strip().lower().startswith(("http", "www."))
                     and ERASMUS_SITE not in c), "")
        if site.lower().startswith("www."):
            site = "https://" + site
        out.append({"institution_name": name, "country_sheet": country, "seed_urls": site})
    return out


def rows_from_csv_text(text):
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ImportError_("File has no header row / is empty.")
    known = {_norm(a) for a in ALIASES["institution_name"]}
    if _looks_like_data(reader.fieldnames) and not any(_norm(h) in known for h in reader.fieldnames):
        return _rows_headerless(text)
    mapping = _map_headers(reader.fieldnames)
    first_col = None if "contact_name" in mapping else _find_header(reader.fieldnames, FIRST_NAME_ALIASES)
    last_col = None if "contact_name" in mapping else _find_header(reader.fieldnames, LAST_NAME_ALIASES)
    out = []
    for r in reader:
        row = {out_field: (r.get(src) or "").strip() for out_field, src in mapping.items()}
        if "contact_name" not in row and (first_col or last_col):
            row["contact_name"] = " ".join(p for p in (
                (r.get(first_col) or "").strip() if first_col else "",
                (r.get(last_col) or "").strip() if last_col else "",
            ) if p)
        out.append(row)
    return out


def _expand_merged_rows(rows):
    """A merged/stacked Excel cell (multiple institutions Alt+Enter'd into one cell) survives CSV
    export as a single field with embedded newlines - csv.DictReader correctly reads that as ONE
    row's ONE value, not several rows, so without this it would silently become one garbled row.
    Splits such a row into one row per line, pairing line i of each field positionally. A field
    with only one non-blank line (e.g. a status shared by the whole stacked block) is reused
    verbatim on every split row - but since reusing one value across several different institutes
    is a guess, not a fact, every row born from a split is marked for human review, even a cleanly
    aligned one. No-op for ordinary single-line rows.

    A row is only split when the INSTITUTION NAME itself holds several lines. One institute whose
    cells merely list two emails, two phones or two links is still one institute: those values are
    joined with "; " and the row stays whole (splitting it scraped the same institute twice)."""
    out = []
    for row in rows:
        lines = {k: ([ln for ln in v.splitlines() if ln.strip()] if v else []) for k, v in row.items()}
        max_n = max((len(v) for v in lines.values()), default=1) or 1
        if max_n <= 1:
            out.append(row)
            continue
        if len(lines.get("institution_name", [])) <= 1:
            out.append({k: "; ".join(ln.strip() for ln in lines[k]) for k in row})
            continue
        for i in range(max_n):
            new_row = {}
            for k in row:
                ls = lines[k]
                if len(ls) <= 1:
                    new_row[k] = (ls[0] if ls else "").strip()
                else:
                    new_row[k] = ls[i].strip() if i < len(ls) else ""
            new_row["flags"] = "split from merged cell - verify pairing"
            out.append(new_row)
    return out


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
    first_col = None if "contact_name" in mapping else _find_header(header, FIRST_NAME_ALIASES)
    last_col = None if "contact_name" in mapping else _find_header(header, LAST_NAME_ALIASES)
    out = []
    for r in body:
        r = r + [""] * (len(header) - len(r))
        row = {out_field: r[idx[src]].strip() for out_field, src in mapping.items()}
        if "contact_name" not in row and (first_col or last_col):
            row["contact_name"] = " ".join(p for p in (
                r[idx[first_col]].strip() if first_col else "",
                r[idx[last_col]].strip() if last_col else "",
            ) if p)
        out.append(row)
    return out


def build_rows(raw_rows):
    numbers = []
    for k, r in enumerate(raw_rows, start=1):
        if _cell_kind(r.get("institution_name")) in ("", "number"):
            continue
        rid = f"r{k:03d}"
        seeds = " ".join(u for u in re.split(r"[\s,;]+", r.get("seed_urls") or "") if u.startswith("http"))
        row = {f: r.get(f, "") for f in OUT_FIELDS}
        row.update(row_id=rid, seed_urls=seeds, duplicate_group="")
        row["flags"] = r.get("flags", "") or ""   # preserve a marker _expand_merged_rows set
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
    ap.add_argument("--start", type=int, default=1,
                    help="1-based row number (in the source file, not counting the header) to start "
                         "from - e.g. to carve out institutes 200-300 of a 1000-row file, use "
                         "--start 200 --limit 100")
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()

    if bool(a.csv) == bool(a.sheet_url):
        ap.error("give exactly one of --csv or --sheet-url")
    if a.start < 1:
        ap.error("--start is 1-based (the first data row is 1, not 0)")

    try:
        raw = rows_from_csv_text(Path(a.csv).read_text(encoding="utf-8-sig")) if a.csv \
            else rows_from_sheet(a.sheet_url, a.tab)
    except ImportError_ as e:
        raise SystemExit(f"Import failed: {e}")

    # --start/--limit are applied BEFORE merged-cell expansion, against the file's real row
    # count, so "--start 200" always means row 200 as you'd count it by opening the file -
    # applying it after expansion would shift that number by however many extra rows any
    # earlier merged/stacked cell split into, which has nothing to do with what the person
    # asking for "row 200" actually meant.
    if a.start > 1:
        raw = raw[a.start - 1:]
    if a.limit:
        raw = raw[:a.limit]
    if a.csv:
        raw = _expand_merged_rows(raw)
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
