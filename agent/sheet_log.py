"""Appends one row per supervisor alert to a Google Sheet, instead of (or alongside) emailing one.
Uses the same service-account credential already used by the scraper's Sheet publishing
(credentials/google_service_account.json) - no new secret, no new auth setup.

Needs the sheet shared with that service account's email (same requirement as any other Sheet this
project writes to) and its spreadsheet ID in config.yaml's vapi.zadarma.monitor.alert_sheet_id.

    python agent/sheet_log.py <sheet_id>   # appends one test row, to confirm access/sharing works
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CREDS_PATH = ROOT / "credentials" / "google_service_account.json"

ROW_FIELDS = ["timestamp", "extension", "manager", "reason", "sent_ok"]

_svc_cache = None


def _service():
    # Cached at module level - rebuilding this (and the OAuth token exchange it does under the
    # hood) on every single append() call is what caused real failures: a first-ever run of
    # agent/call_gap_monitor.py found ~30 historical gaps and called append_alert_row 30 times in a
    # few seconds, each one re-authenticating from scratch, which tripped Google's rate limiting and
    # came back as "Expecting value: line 1 column 1 (char 0)" (an empty/malformed HTTP response)
    # for every single one. One real, authenticated client per process, reused, fixes this at the
    # root - see also append_alert_rows() below, which batches multiple rows into one API call too.
    global _svc_cache
    if _svc_cache is not None:
        return _svc_cache
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    creds = service_account.Credentials.from_service_account_file(
        str(CREDS_PATH), scopes=["https://www.googleapis.com/auth/spreadsheets"])
    _svc_cache = build("sheets", "v4", credentials=creds)
    return _svc_cache


def append_alert_rows(sheet_id, rows, tab="Alerts", _svc=None):
    """Same as append_alert_row, but writes many rows in ONE API call. Prefer this whenever more
    than one row needs appending in the same run (e.g. a monitor that just found several events at
    once) - see _service()'s docstring above for why one-call-per-row is a real problem, not just a
    style preference."""
    if not sheet_id or not rows:
        return False
    try:
        svc = _svc or _service()
        values = [[str(row.get(f, "")) for f in ROW_FIELDS] for row in rows]
        svc.spreadsheets().values().append(
            spreadsheetId=sheet_id, range=f"{tab}!A1",
            valueInputOption="RAW", insertDataOption="INSERT_ROWS",
            body={"values": values},
        ).execute()
        return True
    except Exception as e:
        print(f"(sheet log append failed, non-fatal: {e})", file=sys.stderr)
        return False


def append_alert_row(sheet_id, row, tab="Alerts", _svc=None):
    """row: dict with (at least) the keys in ROW_FIELDS - extras are ignored, missing ones blank.
    Returns True on success, False on any failure (never raises - callers already treat a failed
    notification as non-fatal, same as agent/notify_email.py's send_email). For more than one row in
    the same run, use append_alert_rows() instead - see its docstring for why."""
    return append_alert_rows(sheet_id, [row], tab=tab, _svc=_svc)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) < 2:
        raise SystemExit("usage: python agent/sheet_log.py <sheet_id>")
    from datetime import datetime, timezone
    ok = append_alert_row(sys.argv[1], {
        "timestamp": datetime.now(timezone.utc).isoformat(), "extension": "test",
        "manager": "Test row from agent/sheet_log.py", "reason": "manual test", "sent_ok": "True",
    })
    print("appended" if ok else "FAILED (see error above - check the sheet is shared with the service account's email)")
