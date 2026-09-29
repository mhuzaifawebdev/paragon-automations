"""Push scraper results into the client's actual Google Sheet, live.

Writes into NEW columns (Q onward) so the client's original data (A-P) is never
touched or overwritten - the enrichment sits visibly next to it for comparison.

    python scraper/sync_to_sheet.py                  # push every result found so far
    python scraper/sync_to_sheet.py --watch           # push, then keep watching for
                                                        # new results every 5s (for a
                                                        # live demo while the scraper
                                                        # is still running elsewhere)
    python scraper/sync_to_sheet.py --pace 2           # add a delay between row writes
                                                        # so updates are visibly gradual
                                                        # on screen during a live demo

row_id "rNNN" maps directly to sheet row NNN+1 (row 1 is the header), confirmed
against the actual sheet - it was the source clean_sheet.py was built from.
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build

ROOT = Path(__file__).resolve().parent.parent
CREDS_PATH = ROOT / "credentials" / "google_service_account.json"
RESULTS = ROOT / "data" / "scrape_results"
SHEET_ID = "1OSlFUioX-TUXax_cntSOJB2Ux1fvW_XOZootdMzDZus"   # "Batch Data" - confirmed same structure as the original
SHEET_TAB = "Sheet1"

HEADER = ["Institution Type (found)", "Country (verified)", "Contact Name (found)",
          "Designation (found)", "Email (found)", "Phone (verified)", "Source URL",
          "Confidence", "Partners Found", "Campuses Found", "Multiplier Hook",
          "Needs Human Check", "Scraped At"]
FIRST_COL = "Q"   # A-P is the client's original data; enrichment starts here


def sheets_service():
    creds = service_account.Credentials.from_service_account_file(
        str(CREDS_PATH), scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return build("sheets", "v4", credentials=creds)


def row_number(row_id):
    return int(re.sub(r"\D", "", row_id)) + 1   # r002 -> sheet row 3


def load_result(path):
    res = json.loads(path.read_text(encoding="utf-8"))
    c, n = res.get("contacts") or {}, res.get("network") or {}
    contact = c.get("contact") or {}
    country = c.get("country") or {}
    phone = c.get("phone") or {}
    return [
        c.get("institution_type", ""),
        country.get("value", ""),
        contact.get("name", ""),
        contact.get("designation", ""),
        contact.get("email") or contact.get("office_email") or "",
        phone.get("value", ""),
        contact.get("source_url") or country.get("source_url") or "",
        c.get("confidence", ""),
        len(n.get("external_collaboration") or []) if res.get("network") else "",
        len(n.get("internal_collaboration") or []) if res.get("network") else "",
        n.get("multiplier_hook", ""),
        "yes" if c.get("needs_human_check") or n.get("needs_human_check") else "",
        res.get("scraped_at", ""),
    ]


def ensure_header(svc):
    svc.spreadsheets().values().update(
        spreadsheetId=SHEET_ID, range=f"{SHEET_TAB}!{FIRST_COL}1",
        valueInputOption="RAW", body={"values": [HEADER]}).execute()


def push_all(svc, pace, seen):
    files = sorted(RESULTS.glob("r*.json"))
    pushed = 0
    for f in files:
        row_id = f.stem
        if row_id in seen:
            continue
        try:
            values = load_result(f)
        except Exception as e:
            print(f"{row_id}: skipped, unreadable ({e})", file=sys.stderr)
            seen.add(row_id)
            continue
        rn = row_number(row_id)
        svc.spreadsheets().values().update(
            spreadsheetId=SHEET_ID, range=f"{SHEET_TAB}!{FIRST_COL}{rn}",
            valueInputOption="RAW", body={"values": [values]}).execute()
        print(f"{row_id} -> sheet row {rn}: {values[2] or '(no contact found)'} "
              f"[{values[0] or 'type unknown'}]")
        seen.add(row_id)
        pushed += 1
        if pace:
            time.sleep(pace)
    return pushed


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--watch", action="store_true", help="keep polling for new results every 5s")
    ap.add_argument("--pace", type=float, default=1.0, help="seconds to pause between row writes (0 = as fast as possible)")
    a = ap.parse_args()

    if not CREDS_PATH.exists():
        raise SystemExit(f"missing {CREDS_PATH} - save the service account JSON key there first")

    svc = sheets_service()
    print("Writing header row...")
    ensure_header(svc)

    seen = set()
    n = push_all(svc, a.pace, seen)
    print(f"\npushed {n} result(s)")

    if a.watch:
        print("Watching for new results (Ctrl+C to stop)...")
        try:
            while True:
                time.sleep(5)
                n = push_all(svc, a.pace, seen)
                if n:
                    print(f"pushed {n} new result(s)")
        except KeyboardInterrupt:
            print("\nstopped")


if __name__ == "__main__":
    main()
