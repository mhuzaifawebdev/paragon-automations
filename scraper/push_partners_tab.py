"""Push data/partners.csv into a dedicated "Partners" tab on the client's Google
Sheet, so every partner name found is visible and scrollable, not just a count.
Creates the tab if it doesn't exist yet.

    python scraper/push_partners_tab.py
"""
import csv
import sys
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build

ROOT = Path(__file__).resolve().parent.parent
CREDS_PATH = ROOT / "credentials" / "google_service_account.json"
SHEET_ID = "1OSlFUioX-TUXax_cntSOJB2Ux1fvW_XOZootdMzDZus"
TAB_NAME = "Partners"

HEADER = ["Row ID", "Institution", "Partner Name", "Partner Country", "Partner Type",
          "Mobility Type", "Source URL", "Evidence Quote", "Confidence", "Scraped At"]


def sheets_service():
    creds = service_account.Credentials.from_service_account_file(
        str(CREDS_PATH), scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return build("sheets", "v4", credentials=creds)


def ensure_tab(svc):
    meta = svc.spreadsheets().get(spreadsheetId=SHEET_ID).execute()
    names = [s["properties"]["title"] for s in meta["sheets"]]
    if TAB_NAME in names:
        return
    svc.spreadsheets().batchUpdate(
        spreadsheetId=SHEET_ID,
        body={"requests": [{"addSheet": {"properties": {"title": TAB_NAME}}}]}).execute()
    print(f"Created new tab: {TAB_NAME}")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    rows = list(csv.DictReader(open(ROOT / "data" / "partners.csv", encoding="utf-8-sig")))
    values = [HEADER] + [
        [r["row_id"], r["institution"], r["partner_name"], r["partner_country"],
         r["partner_type"], r["mobility_type"], r["source_url"], r["evidence_quote"],
         r["confidence"], r["scraped_at"]]
        for r in rows
    ]

    svc = sheets_service()
    ensure_tab(svc)
    svc.spreadsheets().values().update(
        spreadsheetId=SHEET_ID, range=f"{TAB_NAME}!A1",
        valueInputOption="RAW", body={"values": values}).execute()
    print(f"Pushed {len(rows)} partner rows into the '{TAB_NAME}' tab.")


if __name__ == "__main__":
    main()
