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

# The fields a human actually needs at a glance - contact person, designation, email, phone,
# confidence - come first on the Institutions sheet instead of making the team scroll past ~18
# audit/original-sheet columns to find them. Anything not listed here keeps its existing relative
# order, appended after these.
PRIORITY_COLUMNS = [
    "institution_scraped", "person_scraped", "designation_scraped", "email_scraped",
    "phone_scraped", "confidence", "contact_verified", "needs_human_check",
    "official_website", "institution_type", "country_scraped",
    "partners_found", "campuses_found", "row_id",
]

# Same idea for the Partners sheet: partner name, country, scope (International/National) and type
# come first, so a client specifically wanting international reach doesn't have to scroll past
# source_url/evidence_quote/verify_note to see what matters.
PARTNER_PRIORITY_COLUMNS = ["partner_name", "partner_country", "partner_scope", "partner_type",
                            "mobility_type", "confidence", "verified", "row_id", "institution"]
# International partners are the client's priority - rank them first within each institute's own
# block of partners, so scanning down the sheet doesn't bury them under domestic ones.
SCOPE_RANK = {"International": 0, "Unknown": 1, "National": 2}

# Raw CSV header -> a label a non-technical team member can read at a glance. Anything not listed
# falls back to a prettified version of its raw name (see _prettify) rather than being left as
# snake_case.
FRIENDLY_LABELS = {
    "row_id": "Row ID", "institution_scraped": "Institution Name", "person_scraped": "Contact Person",
    "designation_scraped": "Designation", "email_scraped": "Email", "phone_scraped": "Phone",
    "confidence": "Confidence", "contact_verified": "Contact Verified?",
    "needs_human_check": "Needs Review?", "official_website": "Website",
    "institution_type": "Institution Type", "country_scraped": "Country",
    "partners_found": "Partners Found", "campuses_found": "Campuses Found",
    "institution_original": "Institution Name (as given)", "country_original": "Country (as given)",
    "phone_original": "Phone (as given)", "person_original": "Contact Person (as given)",
    "designation_original": "Designation (as given)", "email_original": "Email (as given)",
    "duplicate_of": "Duplicate Of", "country_source_url": "Country Source",
    "phone_source_url": "Phone Source", "phone_verified": "Phone Verified?",
    "phone_alt": "Alternate Phone", "designation_local": "Designation (local language)",
    "office_email": "Office Email", "contact_phone": "Contact's Direct Phone",
    "contact_source_url": "Contact Source", "contact_evidence": "Contact Evidence Quote",
    "contact_verify_note": "Contact Verification Note", "rung": "Contact Seniority",
    "country_verified": "Country Verified?", "changed_vs_sheet": "Changed vs Original Sheet",
    "partners_verified": "Partners Verified", "campuses_verified": "Campuses Verified",
    "partners_coverage": "Partners Coverage", "campuses_coverage": "Campuses Coverage",
    "network_truncated": "Network List Truncated?", "multiplier_hook": "Multiplier Hook",
    "network_note": "Network Note", "notes": "Notes", "scraped_at": "Scraped At", "method": "Method",
    "institution": "Institution Name", "rank": "Rank", "name": "Name", "designation": "Designation",
    "email": "Email", "phone": "Phone", "source_url": "Source", "evidence_quote": "Evidence Quote",
    "why_chosen": "Why Chosen", "verified": "Verified?", "verify_note": "Verification Note",
    "partner_name": "Partner Name", "partner_country": "Partner Country", "partner_type": "Partner Type",
    "partner_scope": "International or National?",
    "mobility_type": "Mobility Type", "confidence_": "Confidence", "campus_name": "Campus Name",
    "country": "Country", "city": "City", "role": "Role", "kind": "Kind", "item": "Item",
    "why_flagged": "Why Flagged",
}


def _prettify(field):
    return field.replace("_", " ").strip().title()


def _reorder_institutions(header, rows):
    """Puts PRIORITY_COLUMNS first (in that order); everything else keeps its existing relative
    order, appended after. Returns a new (header, rows) pair - nothing is dropped, only reordered."""
    front = [f for f in PRIORITY_COLUMNS if f in header]
    rest = [f for f in header if f not in front]
    new_header = front + rest
    idx = [header.index(f) for f in new_header]
    new_rows = [[row[i] if i < len(row) else "" for i in idx] for row in rows]
    return new_header, new_rows


def _reorder_partners(header, rows):
    """Same idea as _reorder_institutions, for the Partners sheet."""
    front = [f for f in PARTNER_PRIORITY_COLUMNS if f in header]
    rest = [f for f in header if f not in front]
    new_header = front + rest
    idx = [header.index(f) for f in new_header]
    new_rows = [[row[i] if i < len(row) else "" for i in idx] for row in rows]
    return new_header, new_rows


def _sort_partners(header, rows):
    """Within each institute's own block (grouped by row_id, institute order preserved as scraped),
    International partners before National - the client's ask for prominence. Stable: rows with
    the same scope keep their original (scraped) relative order."""
    if "row_id" not in header or "partner_scope" not in header:
        return rows
    rid_i, scope_i = header.index("row_id"), header.index("partner_scope")
    order, groups = [], {}
    for row in rows:
        rid = row[rid_i] if rid_i < len(row) else ""
        groups.setdefault(rid, []).append(row)
        if rid not in order:
            order.append(rid)
    out = []
    for rid in order:
        out.extend(sorted(groups[rid], key=lambda r: SCOPE_RANK.get(r[scope_i] if scope_i < len(r) else "", 1)))
    return out


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
        if name == "Institutions" and data["header"]:
            # Reorder in place so the hyperlink-column lookup below (which reads sheets["Institutions"])
            # sees the same reordered header/rows, not the original CSV order.
            data["header"], data["rows"] = _reorder_institutions(data["header"], data["rows"])
        elif name == "Partners" and data["header"]:
            data["header"], data["rows"] = _reorder_partners(data["header"], data["rows"])
            data["rows"] = _sort_partners(data["header"], data["rows"])
        ws = wb.create_sheet(name)
        ws.append([FRIENDLY_LABELS.get(h, _prettify(h)) for h in data["header"]])
        for row in data["rows"]:
            ws.append(row)
        ws_objs[name] = ws
        # Any column that IS a real URL (the website itself, or a "source" page used to verify a
        # claim) gets made a real clickable hyperlink here - previously these were only plain text,
        # correct-looking but not clickable, in every sheet.
        url_cols = [i for i, h in enumerate(data["header"]) if h == "official_website" or h.endswith("_url")]
        for col_idx in url_cols:
            for row_idx, row in enumerate(data["rows"], start=2):
                val = row[col_idx] if col_idx < len(row) else ""
                if val and val.startswith("http"):
                    cell = ws.cell(row=row_idx, column=col_idx + 1)
                    cell.hyperlink = val   # a plain string -> openpyxl writes a real external relationship
                    cell.font = HYPERLINK_FONT

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
