"""Builds a real, properly-styled multi-tab .xlsx from one batch's output CSVs (scraper/run_batch.py's
merge() writes them), for a team's downloaded results (web_scraper's "Download results" button).

This exists because the client-side conversion (SheetJS, in web_scraper/index.html) can write a
working hyperlink *relationship* into a .xlsx, but the free/community build of that library cannot
write custom cell styling at all - confirmed by round-tripping a test file through it: the resulting
styles.xml only ever contains one default black font, no matter what style is requested. So a
SheetJS-built link is technically clickable but always looks like plain text. openpyxl (server-side,
free, no such limitation) produces a real blue/underlined clickable hyperlink, matching what
scraper/build_hei_sheet.py's Google Sheet has always looked like.

    python scraper2/build_xlsx.py --batch teamA   # writes data_hei/teamA/results.xlsx

Only called once a batch is fully done (see .github/workflows/scrape_batch.yml) - not on every
live checkpoint - since openpyxl's file-rewrite cost isn't worth paying on every row the way the
plain-CSV merge() already is.
"""
import argparse
import csv
import sys
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.worksheet.hyperlink import Hyperlink

ROOT = Path(__file__).resolve().parent.parent

SHEETS = {
    "Institutions": "institutions_enriched.csv",
    "Partners": "partners.csv",
    "Campuses": "campuses.csv",
    "Contacts": "contacts.csv",
    "Needs Review": "needs_review.csv",
}
# Institutions column -> (target sheet, its row_id column name) - same pairing as the summary counts
# scraper/build_hei_sheet.py links on its client sheet.
LINK_COLUMNS = {
    "partners_found": "Partners",
    "campuses_found": "Campuses",
    "person_scraped": "Contacts",
    "needs_human_check": "Needs Review",
}
HYPERLINK_FONT = Font(color="0563C1", underline="single")


def read_csv(path):
    if not path.exists():
        return [], []
    with open(path, newline="", encoding="utf-8-sig") as f:
        r = csv.reader(f)
        rows = list(r)
    return (rows[0], rows[1:]) if rows else ([], [])


def build(data_dir):
    data_dir = Path(data_dir)
    sheets = {}
    for name, filename in SHEETS.items():
        header, rows = read_csv(data_dir / filename)
        sheets[name] = {"header": header, "rows": rows}

    wb = Workbook()
    wb.remove(wb.active)
    ws_objs = {}
    for name, data in sheets.items():
        ws = wb.create_sheet(name)
        ws.append(data["header"])
        for row in data["rows"]:
            ws.append(row)
        ws_objs[name] = ws

    inst = sheets["Institutions"]
    if inst["header"] and "row_id" in inst["header"]:
        rid_col = inst["header"].index("row_id")
        ws_inst = ws_objs["Institutions"]
        for link_col_name, target_sheet in LINK_COLUMNS.items():
            if link_col_name not in inst["header"]:
                continue
            link_col_idx = inst["header"].index(link_col_name)
            target_data = sheets[target_sheet]
            if "row_id" not in target_data["header"]:
                continue
            target_rid_col = target_data["header"].index("row_id")
            # Excel is 1-indexed and row 1 is the header, so data row i (0-indexed) is sheet row i+2.
            row_id_to_target_row = {}
            for i, row in enumerate(target_data["rows"]):
                rid = row[target_rid_col] if target_rid_col < len(row) else ""
                if rid and rid not in row_id_to_target_row:
                    row_id_to_target_row[rid] = i + 2

            for i, row in enumerate(inst["rows"]):
                rid = row[rid_col] if rid_col < len(row) else ""
                target_row = row_id_to_target_row.get(rid)
                if not target_row:
                    continue
                cell = ws_inst.cell(row=i + 2, column=link_col_idx + 1)
                if cell.value in (None, ""):
                    continue
                # A plain string (even "#'Sheet'!A1") makes openpyxl write an EXTERNAL relationship
                # (TargetMode="External") - confirmed by inspecting the raw XML it produces - which
                # Excel would treat as a URL, not an in-workbook jump. The Hyperlink object with
                # `location` (no `target`) is what actually produces a spec-correct internal link
                # with no relationship file at all, verified the same way.
                cell.hyperlink = Hyperlink(ref=cell.coordinate, location=f"'{target_sheet}'!A{target_row}")
                cell.font = HYPERLINK_FONT

    out_path = data_dir / "results.xlsx"
    wb.save(out_path)
    return out_path


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--batch", required=True)
    a = ap.parse_args()
    out = build(ROOT / "data_hei" / a.batch)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
