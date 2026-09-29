"""Build the client's Google Sheet from the verified CSVs: enrichment columns on Sheet1
(original A-P untouched) plus linked detail tabs.

    python scraper/build_sheet.py --dry-run     # show what would be written, touch nothing
    python scraper/build_sheet.py               # rebuild the tabs and Sheet1 enrichment

Tabs (created if missing, rewritten each run from data/*.csv):
  Partners / Campuses / Contacts   only source-CONFIRMED rows, one contiguous block per institute
  Needs Review                     everything that failed the source check, with the reason
  Legend                           what each column means and how to check any row yourself
Sheet1 counts are formulas that jump to that institute's block, e.g. "46 partners >".

Nothing is shown as verified unless scraper/verify.py confirmed it against the cited page.
"""
import argparse
import csv
import re
import sys
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(__import__("os").environ.get("PARAGON_DATA") or ROOT / "data")
CREDS_PATH = ROOT / "credentials" / "google_service_account.json"
SHEET_ID = "1OSlFUioX-TUXax_cntSOJB2Ux1fvW_XOZootdMzDZus"   # "Batch Data"
MAIN_TAB = "Sheet1"
BACK_GID = 0        # gid of the main tab, used by the "< institute" links
FIRST_COL = "Q"     # A-P is the client's original data and is never written to
LAST_COL = "AF"     # our output block is Q..AF (16 columns); AG is left blank as a separator
WEBSITE_COL, STATUS_COL = "AH", "AI"    # INPUT columns typed by people; the rebuild never clears or overwrites them

TAB_HEADERS = {
    "Partners": ["Row ID", "Institution", "Partner Name", "Partner Country", "Partner Type", "Mobility Type",
                 "Source (click to open)", "Evidence Quote (search this on the page)", "Verified", "Back"],
    "Campuses": ["Row ID", "Institution", "Campus Name", "City", "Country", "Role",
                 "Source (click to open)", "Evidence Quote (search this on the page)", "Verified", "Back"],
    "Contacts": ["Row ID", "Institution", "Rank", "Name", "Designation", "Email", "Phone",
                 "Why this person", "Source (click to open)", "Evidence Quote (search this on the page)",
                 "Verified", "Back"],
    "Needs Review": ["Row ID", "Institution", "Kind", "Item", "Source", "Quote as claimed", "Why it was not confirmed"],
    "Legend": ["Column / tab", "What it means"],
}
MAIN_HEADER = ["Institution Type", "Country (verified)", "Contact Name", "Designation", "Email", "Phone",
               "Why this contact", "Contact Source", "Contact Verified", "Partners", "Campuses", "Coverage",
               "Backup Contacts", "Multiplier Hook", "Needs Human Check", "Method - Scraped"]

LEGEND = [
    ["VERIFIED", "The evidence quote was found, word for word, on the cited page by a program (not by the AI). Emails and phone numbers must literally appear on that page; they are never guessed. It means 'matches its source', not 'the source is up to date'."],
    ["Source (click to open)", "The exact page the fact was read from. Open it and press Ctrl+F, then paste the Evidence Quote: the highlighted text on the real page is the proof."],
    ["Evidence Quote", "The text copied from the source page. For long lists it is the list line itself."],
    ["Partners", "Institutions this one already collaborates with, as listed on its own website. Educational institutions and networks are listed first in each block; use the Partner Type filter for companies and public bodies. Click the number to jump to the full named list. 'X of Y' compares what was captured with the total the page states."],
    ["Campuses", "The institution's own campuses or sites. 'none published' means its website lists none (for example a distance-learning university)."],
    ["Mobility Type", "study / traineeship / staff, only when the page says so. 'unknown' means the page names the partner but not the type of exchange."],
    ["Contacts", "The Erasmus+ people found on the institution's own pages, ranked by relevance. Rank 1 is shown on Sheet1; the others are backups if nobody answers."],
    ["Why this contact", "Plain-English reason this person was ranked first (for example: institutional Erasmus+ coordinator)."],
    ["Needs Review", "Anything the checker could not confirm on the cited page. Kept for a person to look at; never shown in the main tabs."],
    ["Coverage", "Honest count of what was captured versus what the page says exists, e.g. '4 verified of ~100 stated (rest of the list not readable)'."],
]


def sheets_service():
    creds = service_account.Credentials.from_service_account_file(
        str(CREDS_PATH), scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return build("sheets", "v4", credentials=creds)


def read(name):
    p = DATA / f"{name}.csv"
    return list(csv.DictReader(open(p, encoding="utf-8-sig"))) if p.exists() else []


def rnum(row_id):
    return int(re.sub(r"\D", "", row_id))


def esc(s):
    return (s or "").replace('"', '""')


def txt(s):
    """USER_ENTERED makes Sheets parse values: '+357 22 411600' or '- item' or '=x' would become
    formulas (#ERROR!). A leading apostrophe stores them as plain text."""
    s = "" if s is None else str(s)
    return "'" + s if s[:1] in ("=", "+", "-", "@") else s


def link(url, text):
    if not url or not url.startswith("http"):
        return ""
    return f'=HYPERLINK("{esc(url)}","{esc(text)}")'


def col_letter(n):   # 1 -> A
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def first_col_index():
    n = 0
    for ch in FIRST_COL:
        n = n * 26 + ord(ch) - 64
    return n


def verified_sorted(rows):
    keep = [r for r in rows if r.get("verified") == "yes"]
    return sorted(keep, key=lambda r: rnum(r["row_id"]))   # stable: keeps the page's own order


def blocks_for(rows):
    """{row_id: (first_sheet_row, last_sheet_row)} for contiguous per-institute blocks
    (data starts on row 2, under the header)."""
    out = {}
    for i, r in enumerate(rows, start=2):
        a, b = out.get(r["row_id"], (i, i))
        out[r["row_id"]] = (a, i)
    return out


# Everything the institution's own site lists as a partner is shown (a national Erasmus agency or a research institute
# is useful even though it is not a school). Inside each institute's block, educational institutions and networks come
# first; the Partner Type column has a filter for the rest.
TYPE_RANK = {"university": 0, "college": 1, "school": 2, "network": 3, "other": 4, "company": 5}
EDUCATIONAL = {"university", "college", "school", "network"}


def detail_tabs(gids_unused=None):
    partners = sorted(verified_sorted(read("partners")),
                      key=lambda r: (rnum(r["row_id"]), TYPE_RANK.get(r.get("partner_type"), 4)))
    campuses = verified_sorted(read("campuses"))
    contacts = sorted([c for c in read("contacts") if c.get("verified") == "yes"],
                      key=lambda r: (rnum(r["row_id"]), int(r["rank"] or 1)))
    review = sorted(read("needs_review"), key=lambda r: rnum(r["row_id"]))
    return partners, campuses, contacts, review


def tab_values(partners, campuses, contacts, review):
    def back(rid):
        return f'=HYPERLINK("#gid={BACK_GID}&range={FIRST_COL}{rnum(rid) + 1}","< institute")'
    tabs = {}
    tabs["Partners"] = [TAB_HEADERS["Partners"]] + [
        [r["row_id"], txt(r["institution"]), txt(r["partner_name"]), txt(r["partner_country"]), r["partner_type"],
         r["mobility_type"], link(r["source_url"], "open source >"), txt(r["evidence_quote"]), "yes", back(r["row_id"])]
        for r in partners]
    tabs["Campuses"] = [TAB_HEADERS["Campuses"]] + [
        [r["row_id"], txt(r["institution"]), txt(r["campus_name"]), txt(r["city"]), txt(r["country"]), r["role"],
         link(r["source_url"], "open source >"), txt(r["evidence_quote"]), "yes", back(r["row_id"])]
        for r in campuses]
    tabs["Contacts"] = [TAB_HEADERS["Contacts"]] + [
        [r["row_id"], txt(r["institution"]), r["rank"], txt(r["name"]), txt(r["designation"]), txt(r["email"]), txt(r["phone"]),
         txt(r["why_chosen"]), link(r["source_url"], "open source >"), txt(r["evidence_quote"]), "yes", back(r["row_id"])]
        for r in contacts]
    tabs["Needs Review"] = [TAB_HEADERS["Needs Review"]] + [
        [r["row_id"], txt(r["institution"]), r["kind"], txt(r["item"]), link(r["source_url"], "open source >") or txt(r["source_url"]),
         txt(r["evidence_quote"]), txt(r["why_flagged"])] for r in review]
    tabs["Legend"] = [TAB_HEADERS["Legend"]] + LEGEND
    return tabs


def multiplier_hook(rid, partners):
    """Built only from CONFIRMED partners, so the sentence a caller reads aloud is always true to a source."""
    mine = [p for p in partners if p["row_id"] == rid and p["partner_name"]]
    if not mine:
        return ""
    pref = [p for p in mine if p["partner_type"] in ("university", "college")] or mine
    best = max(pref, key=lambda p: float(p.get("confidence") or 0))
    where = f" ({best['partner_country']})" if best["partner_country"] not in ("", "unknown") else ""
    return f"Your institution already collaborates with {best['partner_name']}{where}."


def main_rows(enriched, partners, campuses, contacts, gids):
    pb, cb, kb = blocks_for(partners), blocks_for(campuses), blocks_for(contacts)
    out = {}
    for e in enriched:
        rid = e["row_id"]
        base = e["duplicate_of"] or rid                # duplicates point at the original's blocks
        row = rnum(rid) + 1
        ok = e["contact_verified"] == "yes"
        if ok:
            why = next((c["why_chosen"] for c in contacts if c["row_id"] == base and c["rank"] == "1"), "") or e["rung"]
        elif e["contact_verified"] == "no":
            why = "Found but could not be confirmed on the cited page - see Needs Review"
        elif e["notes"].startswith("Website"):
            why = e["notes"][:220]                       # say why nothing was read, not "no contact found"
        else:
            why = "No public Erasmus contact found on the institution's own site"

        def count_cell(found, verified, blocks, gid, label):
            if found == "":
                return "not searched"
            if int(verified or 0) == 0:
                return "none confirmed" if int(found or 0) else "none published"
            a, b = blocks[base]
            n = b - a + 1
            word = label if n != 1 else {"partners": "partner", "campuses": "campus"}[label]
            return f'=HYPERLINK("#gid={gid}&range=A{a}:J{b}","{n} {word} >")'

        n_back = sum(1 for c in contacts if c["row_id"] == base and c["rank"] != "1")
        kbk = kb.get(base)
        edu = sum(1 for p in partners if p["row_id"] == base and p.get("partner_type") in EDUCATIONAL)
        tot = pb[base][1] - pb[base][0] + 1 if base in pb else 0
        cov = txt("; ".join(x for x in (
            f"Partners: {e['partners_coverage']}" + (f" ({edu} educational)" if tot and edu != tot else "") if e["partners_coverage"] else "",
            f"Campuses: {e['campuses_coverage']}" if e["campuses_coverage"] else "") if x))
        out[row] = [
            e["institution_type"],
            txt(e["country_scraped"]) if e["country_verified"] == "yes" else "",
            txt(e["person_scraped"]) if ok else "",
            txt(e["designation_scraped"]) if ok else "",
            txt(e["email_scraped"]) if ok and e["email_scraped"] else "",
            txt(e["phone_scraped"]) if e["phone_verified"] == "yes" else "",
            txt(why),
            link(e["contact_source_url"], "open source >") if ok else "",
            {"yes": "yes", "no": "NO - not confirmed"}.get(e["contact_verified"], ""),
            count_cell(e["partners_found"], e["partners_verified"], pb, gids["Partners"], "partners"),
            count_cell(e["campuses_found"], e["campuses_verified"], cb, gids["Campuses"], "campuses"),
            cov,
            (f'=HYPERLINK("#gid={gids["Contacts"]}&range=A{kbk[0]}:L{kbk[1]}","{n_back} backup >")' if n_back and kbk else "none found"),
            txt(multiplier_hook(base, partners)),
            "yes" if e["needs_human_check"] == "yes" or e["contact_verified"] == "no" else "",
            f"{e.get('method', '')} - {e['scraped_at'][:10]}".strip(" -"),
        ]
    return out


def read_inputs(svc=None):
    """{row_id: {"website": str, "status": str}} from the two input columns. Row 2 = r001."""
    svc = svc or sheets_service()
    try:
        got = svc.spreadsheets().values().get(spreadsheetId=SHEET_ID,
                                              range=f"{MAIN_TAB}!{WEBSITE_COL}2:{STATUS_COL}1000").execute().get("values", [])
    except Exception:            # the input columns do not exist yet (first run): nothing is pending
        return {}
    out = {}
    for i, row in enumerate(got, start=1):
        row = (row + ["", ""])[:2]
        if row[0].strip() or row[1].strip():
            out[f"r{i:03d}"] = {"website": row[0].strip(), "status": row[1].strip()}
    return out


def ensure_grid_width(svc, gids, need_cols=40):
    """The sheet ships with 32 columns (A..AF); the input columns sit at AH/AI, so widen the grid if needed."""
    meta = svc.spreadsheets().get(spreadsheetId=SHEET_ID, fields="sheets(properties(sheetId,gridProperties))").execute()
    for sh in meta["sheets"]:
        pr = sh["properties"]
        if pr["sheetId"] == gids[MAIN_TAB] and pr["gridProperties"]["columnCount"] < need_cols:
            svc.spreadsheets().batchUpdate(spreadsheetId=SHEET_ID, body={"requests": [{"updateSheetProperties": {
                "properties": {"sheetId": pr["sheetId"], "gridProperties": {"columnCount": need_cols}},
                "fields": "gridProperties.columnCount"}}]}).execute()


def ensure_input_columns(svc, gids):
    """Header + a dropdown for the Run status column, written only if the header is still empty."""
    ensure_grid_width(svc, gids)
    v = svc.spreadsheets().values()
    have = v.get(spreadsheetId=SHEET_ID, range=f"{MAIN_TAB}!{WEBSITE_COL}1:{STATUS_COL}1").execute().get("values", [[]])
    if not have or len(have[0]) < 2 or not have[0][0]:
        v.update(spreadsheetId=SHEET_ID, range=f"{MAIN_TAB}!{WEBSITE_COL}1", valueInputOption="RAW",
                 body={"values": [["Website override (optional)", "Run status (type: pending)"]]}).execute()
    g, c0 = gids[MAIN_TAB], col_index(WEBSITE_COL) - 1
    svc.spreadsheets().batchUpdate(spreadsheetId=SHEET_ID, body={"requests": [
        {"repeatCell": {"range": {"sheetId": g, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": c0, "endColumnIndex": c0 + 2},
                        "cell": {"userEnteredFormat": {"backgroundColor": {"red": 1.0, "green": 0.95, "blue": 0.7},
                                                       "textFormat": {"bold": True}, "wrapStrategy": "WRAP"}},
                        "fields": "userEnteredFormat(backgroundColor,textFormat,wrapStrategy)"}},
        {"setDataValidation": {"range": {"sheetId": g, "startRowIndex": 1, "endRowIndex": 1000, "startColumnIndex": c0 + 1, "endColumnIndex": c0 + 2},
                               "rule": {"condition": {"type": "ONE_OF_LIST", "values": [
                                   {"userEnteredValue": x} for x in ("pending", "done", "needs website")]},
                                        "showCustomUi": True, "strict": False}}},
        {"updateDimensionProperties": {"range": {"sheetId": g, "dimension": "COLUMNS", "startIndex": c0, "endIndex": c0 + 1},
                                       "properties": {"pixelSize": 230}, "fields": "pixelSize"}},
    ]}).execute()


def col_index(letters):
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n


def mark_done(svc, enriched):
    """Rows the person marked pending get an outcome, so the next run does not repeat them."""
    inputs, by_id, today = read_inputs(svc), {e["row_id"]: e for e in enriched}, __import__("time").strftime("%Y-%m-%d")
    data = []
    for rid, inp in inputs.items():
        if inp["status"].lower() != "pending" or rid not in by_id:
            continue
        note = by_id[rid].get("notes", "")
        if note.startswith("Website not found"):
            status = "needs website - type it in the column to the left, then set pending"
        elif note.startswith("Website could not be read"):
            status = "site not reachable - retry later, or type another website in the column to the left"
        else:
            status = f"done {today}"
        data.append({"range": f"{MAIN_TAB}!{STATUS_COL}{int(rid[1:]) + 1}", "values": [[status]]})
    if data:
        svc.spreadsheets().values().batchUpdate(spreadsheetId=SHEET_ID,
                                                body={"valueInputOption": "RAW", "data": data}).execute()
    return len(data)


def ensure_tabs(svc):
    meta = svc.spreadsheets().get(spreadsheetId=SHEET_ID).execute()
    gids = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta["sheets"]}
    missing = [t for t in TAB_HEADERS if t not in gids]
    if missing:
        svc.spreadsheets().batchUpdate(spreadsheetId=SHEET_ID, body={"requests": [
            {"addSheet": {"properties": {"title": t}}} for t in missing]}).execute()
        meta = svc.spreadsheets().get(spreadsheetId=SHEET_ID).execute()
        gids = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta["sheets"]}
    return gids


def format_requests(gids, tab_rows):
    reqs = []
    header_fmt = {"backgroundColor": {"red": 0.86, "green": 0.93, "blue": 0.96},
                  "textFormat": {"bold": True}, "wrapStrategy": "WRAP"}
    widths = {
        "Partners": [60, 190, 250, 110, 90, 90, 120, 380, 70, 80],
        "Campuses": [60, 190, 250, 110, 110, 80, 120, 380, 70, 80],
        "Contacts": [60, 190, 50, 170, 200, 200, 130, 220, 120, 380, 70, 80],
        "Needs Review": [60, 190, 80, 250, 120, 330, 330],
        "Legend": [170, 800],
    }
    for tab, ws in widths.items():
        gid = gids[tab]
        reqs.append({"updateSheetProperties": {"properties": {"sheetId": gid, "gridProperties": {"frozenRowCount": 1}},
                                               "fields": "gridProperties.frozenRowCount"}})
        reqs.append({"repeatCell": {"range": {"sheetId": gid, "startRowIndex": 0, "endRowIndex": 1},
                                    "cell": {"userEnteredFormat": header_fmt},
                                    "fields": "userEnteredFormat(backgroundColor,textFormat,wrapStrategy)"}})
        for i, w in enumerate(ws):
            reqs.append({"updateDimensionProperties": {"range": {"sheetId": gid, "dimension": "COLUMNS",
                         "startIndex": i, "endIndex": i + 1}, "properties": {"pixelSize": w}, "fields": "pixelSize"}})
        reqs.append({"repeatCell": {"range": {"sheetId": gid, "startRowIndex": 1},
                                    "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP", "verticalAlignment": "TOP"}},
                                    "fields": "userEnteredFormat(wrapStrategy,verticalAlignment)"}})
        if tab != "Legend":
            reqs.append({"setBasicFilter": {"filter": {"range": {"sheetId": gid, "startRowIndex": 0,
                                                                  "endRowIndex": max(len(tab_rows[tab]), 2)}}}})
    g0, c0 = gids[MAIN_TAB], first_col_index() - 1
    reqs.append({"repeatCell": {"range": {"sheetId": g0, "startRowIndex": 0, "endRowIndex": 1,
                                          "startColumnIndex": c0, "endColumnIndex": c0 + len(MAIN_HEADER)},
                                "cell": {"userEnteredFormat": header_fmt},
                                "fields": "userEnteredFormat(backgroundColor,textFormat,wrapStrategy)"}})
    for i, w in enumerate([100, 110, 170, 190, 190, 130, 240, 110, 100, 110, 100, 260, 110, 320, 90, 150]):
        reqs.append({"updateDimensionProperties": {"range": {"sheetId": g0, "dimension": "COLUMNS",
                     "startIndex": c0 + i, "endIndex": c0 + i + 1}, "properties": {"pixelSize": w}, "fields": "pixelSize"}})
    return reqs


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--mark-done", action="store_true", help="give rows marked 'pending' an outcome in the Run status column")
    a = ap.parse_args()

    enriched = read("institutions_enriched")
    partners, campuses, contacts, review = detail_tabs()
    tabs = tab_values(partners, campuses, contacts, review)
    print(f"institutes: {len(enriched)} | partners shown: {len(partners)} | campuses: {len(campuses)} | "
          f"contacts: {len(contacts)} | needs review: {len(review)}")

    if a.dry_run:
        gids = {t: 111 for t in list(TAB_HEADERS) + [MAIN_TAB]}
        rows = main_rows(enriched, partners, campuses, contacts, gids)
        for rn in sorted(rows)[:4]:
            print(f"Sheet1 row {rn}:", rows[rn])
        return

    svc = sheets_service()
    gids = ensure_tabs(svc)
    v = svc.spreadsheets().values()

    for tab, values in tabs.items():           # rewrite each detail tab from scratch
        v.clear(spreadsheetId=SHEET_ID, range=tab).execute()
        v.update(spreadsheetId=SHEET_ID, range=f"{tab}!A1", valueInputOption="USER_ENTERED",
                 body={"values": values}).execute()

    end_col = col_letter(first_col_index() + len(MAIN_HEADER) - 1)
    v.clear(spreadsheetId=SHEET_ID, range=f"{MAIN_TAB}!{FIRST_COL}1:{LAST_COL}1000").execute()   # only our block, never AH/AI
    rows = main_rows(enriched, partners, campuses, contacts, gids)
    data = [{"range": f"{MAIN_TAB}!{FIRST_COL}1", "values": [MAIN_HEADER]}] + [
        {"range": f"{MAIN_TAB}!{FIRST_COL}{rn}:{end_col}{rn}", "values": [vals]} for rn, vals in sorted(rows.items())]
    v.batchUpdate(spreadsheetId=SHEET_ID, body={"valueInputOption": "USER_ENTERED", "data": data}).execute()

    svc.spreadsheets().batchUpdate(spreadsheetId=SHEET_ID, body={"requests": format_requests(gids, tabs)}).execute()
    ensure_input_columns(svc, gids)
    if a.mark_done:
        print("Run status updated for", mark_done(svc, enriched), "row(s).")
    print("Sheet rebuilt. Tabs:", ", ".join(f"{t} (gid {g})" for t, g in gids.items()))


if __name__ == "__main__":
    main()
