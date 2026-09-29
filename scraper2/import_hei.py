"""Import one Batch of the client's "Merged Legacy Database - HEI Network" sheet into its own workspace.

    python scraper2/import_hei.py --batch B1            # -> data_hei/B1/  (numbers_clean.csv, websites.csv, hei_map.csv)

Then run everything for that batch with PARAGON_DATA=data_hei/B1 (the old sheet's data is never touched):
    PARAGON_DATA=data_hei/B1 python scraper2/run.py --all --alternatives --provider gemini --workers 3
    PARAGON_DATA=data_hei/B1 python scraper/build_hei_sheet.py

Row ids are r001.. in sheet order within the batch; hei_map.csv links each one back to the sheet's own ID and row.
The client's columns are only read here, never written.
"""
import argparse
import csv
import re
import sys
import time
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build

ROOT = Path(__file__).resolve().parent.parent
SHEET_ID = "1oowF4IFgas5DeeSSZXTeCchVOi-Y7oYAnf461qnyHXs"
TAB = "HEI-NETWORK(Partners+Campus)"
(UNI, ID, BATCH, ROLE, NAME, SURNAME, WMAIL, WPHONE, CONTACTS_URL, GPHONE, GMAIL, SITE, CAMPUS_URL, PARTNER_URL,
 HTYPE, COUNTRY, BUDGET, REMARKS) = range(18)


def rows_of_sheet():
    creds = service_account.Credentials.from_service_account_file(
        str(ROOT / "credentials" / "google_service_account.json"), scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
    got = build("sheets", "v4", credentials=creds).spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range=f"'{TAB}'!A2:R2000").execute().get("values", [])
    return [(i, (r + [""] * 18)[:18]) for i, r in enumerate(got, start=2)]


def urls(cell):
    return [u for u in re.split(r"\s+", cell or "") if u.startswith("http")]


BAD_HOSTS = ("google.", "facebook.", "linkedin.", "instagram.", "youtube.", "twitter.", "wikipedia.")


def site_root(url):
    """The client's Website column is messy (tracking parameters, inner pages, even Google search URLs). Use the site root;
    an inner page is still read first as a seed."""
    from urllib.parse import urlparse
    u = urlparse((url or "").strip())
    if u.scheme not in ("http", "https") or not u.netloc or any(b in u.netloc.lower() for b in BAD_HOSTS):
        return ""
    return f"{u.scheme}://{u.netloc}/"


def clean_phone(p):
    return re.sub(r"[^\d+]", "", p or "")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--batch", required=True, help="the value in the sheet's Batch column, e.g. B1")
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    out = ROOT / "data_hei" / a.batch
    out.mkdir(parents=True, exist_ok=True)

    picked = [(n, r) for n, r in rows_of_sheet() if r[BATCH].strip().upper() == a.batch.upper() and r[UNI].strip()]
    if a.limit:
        picked = picked[:a.limit]
    numbers, mapping, memo = [], [], []
    for k, (sheet_row, r) in enumerate(picked, start=1):
        rid = f"r{k:03d}"
        person = " ".join(x.strip() for x in (r[NAME], r[SURNAME]) if x.strip())
        country = r[COUNTRY].split(",")[-1].strip()
        raw_site = r[SITE].strip()
        site = site_root(raw_site)
        cu, ca, pa = urls(r[CONTACTS_URL]), urls(r[CAMPUS_URL]), urls(r[PARTNER_URL])
        seeds = pa[:1] + cu[:1] + ca[:1] + cu[1:2] + pa[1:2]
        if raw_site.startswith("http") and site and raw_site.rstrip("/") != site.rstrip("/") and "?" not in raw_site:
            seeds.append(raw_site)        # one page of each kind first (the crawler reads up to 3)
        numbers.append({"row_id": rid, "institution_name": r[UNI].strip(), "country_sheet": country,
                        "phone_1": clean_phone(r[GPHONE]), "phone_2": clean_phone(r[WPHONE]), "contact_name": person,
                        "designation": r[ROLE].strip(), "email": r[WMAIL].strip(), "status_raw": r[REMARKS].strip(),
                        "duplicate_group": "", "flags": "", "seed_urls": " ".join(seeds)})
        mapping.append({"row_id": rid, "hei_id": r[ID].strip(), "sheet_row": sheet_row, "university": r[UNI].strip(),
                        "on_file_name": person, "on_file_role": r[ROLE].strip(), "on_file_email": r[WMAIL].strip(),
                        "on_file_phone": r[WPHONE].strip(), "generic_email": r[GMAIL].strip(), "website": raw_site,
                        "hei_type": r[HTYPE].strip(), "country": country})
        if site.startswith("http"):
            memo.append({"row_id": rid, "url": site, "how": "Website URL column of the client's sheet",
                         "saved_at": time.strftime("%Y-%m-%d")})

    def write(name, rows, fields):
        with open(out / name, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)

    write("numbers_clean.csv", numbers, list(numbers[0]))
    write("hei_map.csv", mapping, list(mapping[0]))
    write("websites.csv", memo, ["row_id", "url", "how", "saved_at"])
    print(f"{a.batch}: {len(numbers)} institutes -> {out} | websites from the sheet: {len(memo)} | "
          f"with a contacts/campus/partner URL to read first: {sum(1 for n in numbers if n['seed_urls'])}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
