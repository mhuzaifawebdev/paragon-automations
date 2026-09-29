"""Publish one HEI batch (data_hei/<batch>/) to the client's HEI sheet as NEW tabs. Columns A-R of the client's tabs are never touched.

    PARAGON_DATA=data_hei/B1 python scraper/build_hei_sheet.py --dry-run
    PARAGON_DATA=data_hei/B1 python scraper/build_hei_sheet.py

Tabs written (created if missing, rewritten each run):
  Paragon Results   one row per institute, in batch order: the client's ID + the person on file (not answering), then OUR
                    ranked alternative contacts, hyperlinked partner/campus counts and a source link for each fact
  Partners / Campuses / Contacts / Needs Review / Legend    exactly as in the first sheet (linked from the counts)
"""
import argparse
import csv
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_sheet as bs  # noqa: E402

HEI_SHEET_ID = "1oowF4IFgas5DeeSSZXTeCchVOi-Y7oYAnf461qnyHXs"
MAIN = "Paragon Results"
LEAD = ["Client ID", "University (client sheet)", "On file (not answering)"]
FIRST_COL = "D"      # our block starts after the 3 echo columns


import re  # noqa: E402

WEAK = re.compile(r"rector|admission|student experience|public relations|administrative|finance|marketing|teacher|secretary|principal|director|head of school|headmaster", re.I)
ERASMUS = re.compile(r"erasmus|international|mobility|relations|outgoing|incoming", re.I)
LEAD_N = 3
QUALITY_HEAD = ["Contact quality (auto)", "Reviewer verdict (Right / Wrong / note)"]


def contact_quality(e, prim, m):
    """Plain-English label so a reviewer knows how much to trust the main contact before opening any link."""
    if e.get("method") == "not read":
        return "Not read - website unreachable or blocked"
    if not prim:
        return "No named contact found on the site"
    toks = re.findall(r"[a-zà-ž]{3,}", (m.get("on_file_name", "") or "").lower())[:2]
    if toks and all(t in prim["name"].lower() for t in toks):
        return "Only the on-file person found (not an alternative)"
    if WEAK.search(prim["designation"]) and not ERASMUS.search(prim["designation"]):
        return "Fallback role - no Erasmus contact found"
    return "Erasmus / international role"


def confidence(quality, prim):
    """High / Medium / Low from four checkable facts. It is a rule of thumb for triage, not a probability."""
    if quality.startswith("Not read"):
        return "n/a (not read)"
    if not prim:
        return "none"
    if prim.get("verified") == "yes" and quality.startswith("Erasmus"):
        return "High" if prim.get("email") else "Medium"
    return "Low"


CONF_LEGEND = ["Confidence", "High = person confirmed on the cited page, has an Erasmus/international role and a page-confirmed email. "
               "Medium = same but no email found. Low = only a fallback role (rector, secretary, principal...), only the on-file person, "
               "or not confirmed. A rule of thumb for deciding which rows to check first; it is not a probability."]


def read_map():
    """hei_map.csv only exists for a batch imported from the client's own HEI sheet
    (scraper2/import_hei.py) - it holds the "on file, not answering" person to compare against.
    A batch imported from a team's own CSV (scraper2/import_batch.py) has no such column to
    compare against, so an empty map here is correct, not a bug to paper over."""
    p = bs.DATA / "hei_map.csv"
    if not p.exists():
        return {}
    with open(p, encoding="utf-8-sig") as f:
        return {r["row_id"]: r for r in csv.DictReader(f)}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if not os.environ.get("PARAGON_DATA"):
        sys.exit("set PARAGON_DATA=data_hei/<batch> first (this script must never write the old sheet's data here)")

    bs.SHEET_ID, bs.MAIN_TAB, bs.FIRST_COL = HEI_SHEET_ID, MAIN, FIRST_COL
    hmap = read_map()
    enriched = bs.read("institutions_enriched")
    partners, campuses, contacts, review = bs.detail_tabs()
    print(f"institutes: {len(enriched)} | partners shown: {len(partners)} | campuses: {len(campuses)} | "
          f"contacts: {len(contacts)} | needs review: {len(review)}")

    def lead(rid):
        m = hmap.get(rid, {})
        on_file = "; ".join(x for x in (m.get("on_file_name", ""), m.get("on_file_role", ""), m.get("on_file_email", "")) if x)
        return [bs.txt(m.get("hei_id", "")), bs.txt(m.get("university", "")), bs.txt(on_file)]

    if a.dry_run:
        gids = {t: 111 for t in list(bs.TAB_HEADERS) + [MAIN]}
        rows = bs.main_rows(enriched, partners, campuses, contacts, gids)
        for rn in sorted(rows)[:3]:
            print(f"row {rn}:", lead(f"r{rn - 1:03d}") + rows[rn])
        return

    svc = bs.sheets_service()
    meta = svc.spreadsheets().get(spreadsheetId=HEI_SHEET_ID).execute()
    gids = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta["sheets"]}
    missing = [t for t in list(bs.TAB_HEADERS) + [MAIN] if t not in gids]
    if missing:
        svc.spreadsheets().batchUpdate(spreadsheetId=HEI_SHEET_ID, body={"requests": [
            {"addSheet": {"properties": {"title": t, "gridProperties": {"rowCount": 1200, "columnCount": 26}}}} for t in missing]}).execute()
        meta = svc.spreadsheets().get(spreadsheetId=HEI_SHEET_ID).execute()
        gids = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta["sheets"]}
    bs.BACK_GID = gids[MAIN]

    tabs = bs.tab_values(partners, campuses, contacts, review)
    tabs["Legend"].append(CONF_LEGEND)
    v = svc.spreadsheets().values()
    kept = {}                     # reviewer verdicts typed earlier must survive a republish
    try:
        old = v.get(spreadsheetId=HEI_SHEET_ID, range=f"'{MAIN}'").execute().get("values", [])
        if old and old[0] and old[0][-1].startswith("Reviewer verdict"):
            kept = {r[0]: r[-1] for r in old[1:] if len(r) > 3 and r[-1]}
    except Exception:
        pass
    for tab, values in tabs.items():
        v.clear(spreadsheetId=HEI_SHEET_ID, range=f"'{tab}'").execute()
        v.update(spreadsheetId=HEI_SHEET_ID, range=f"'{tab}'!A1", valueInputOption="USER_ENTERED", body={"values": values}).execute()

    rows = bs.main_rows(enriched, partners, campuses, contacts, gids)
    cv = LEAD_N + bs.MAIN_HEADER.index("Contact Verified") + 1     # new column goes right after "Contact Verified"
    header = LEAD + bs.MAIN_HEADER + QUALITY_HEAD
    header.insert(cv, "Confidence")
    prim = {c["row_id"]: c for c in contacts if c["rank"] == "1"}
    by_id = {e["row_id"]: e for e in enriched}
    data = []
    for rn, vals in sorted(rows.items()):
        rid = f"r{rn - 1:03d}"
        ld = lead(rid)
        q = contact_quality(by_id.get(rid, {}), prim.get(rid), hmap.get(rid, {}))
        row = [*ld, *vals, q, kept.get(ld[0].lstrip("'"), "")]
        row.insert(cv, confidence(q, prim.get(rid)))
        data.append(row)
    v.clear(spreadsheetId=HEI_SHEET_ID, range=f"'{MAIN}'").execute()
    v.update(spreadsheetId=HEI_SHEET_ID, range=f"'{MAIN}'!A1", valueInputOption="USER_ENTERED", body={"values": [header] + data}).execute()

    reqs = bs.format_requests(gids, tabs)
    g0 = gids[MAIN]
    header_fmt = {"backgroundColor": {"red": 0.86, "green": 0.93, "blue": 0.96}, "textFormat": {"bold": True}, "wrapStrategy": "WRAP"}
    reqs += [
        {"updateSheetProperties": {"properties": {"sheetId": g0, "gridProperties": {"frozenRowCount": 1, "frozenColumnCount": 2}},
                                   "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount"}},
        {"repeatCell": {"range": {"sheetId": g0, "startRowIndex": 0, "endRowIndex": 1}, "cell": {"userEnteredFormat": header_fmt},
                        "fields": "userEnteredFormat(backgroundColor,textFormat,wrapStrategy)"}},
        {"repeatCell": {"range": {"sheetId": g0, "startRowIndex": 1}, "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP", "verticalAlignment": "TOP"}},
                        "fields": "userEnteredFormat(wrapStrategy,verticalAlignment)"}},
        {"setBasicFilter": {"filter": {"range": {"sheetId": g0, "startRowIndex": 0, "endRowIndex": max(len(data) + 1, 2)}}}},
    ]
    for i, w in enumerate([70, 200, 220, 100, 110, 170, 190, 190, 130, 240, 110, 100, 90, 110, 100, 260, 110, 320, 90, 150, 230, 230]):
        reqs.append({"updateDimensionProperties": {"range": {"sheetId": g0, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1},
                                                   "properties": {"pixelSize": w}, "fields": "pixelSize"}})
    svc.spreadsheets().batchUpdate(spreadsheetId=HEI_SHEET_ID, body={"requests": reqs}).execute()
    print("Published. Tabs:", ", ".join(f"{t} (gid {g})" for t, g in gids.items() if t in list(bs.TAB_HEADERS) + [MAIN]))


if __name__ == "__main__":
    main()
