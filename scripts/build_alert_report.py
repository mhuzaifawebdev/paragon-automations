"""Turns the auto-dialer's alert log into two readable tabs of the AutoDialer-Alerts Google Sheet:

  "Daily summary"   one row per day and operator: offline alerts, idle alerts, call gaps, longest gap, time lost
  "Alerts by day"   every alert, newest day first, each day under its own heading, with the gap length as a number

The raw "Alerts" tab that the monitors append to is left exactly as it is. Both tabs are rebuilt from
data/alerts.csv (the complete log; the raw tab misses older rows) every time this runs.

    python scripts/build_alert_report.py            # writes to the sheet in config.yaml
    python scripts/build_alert_report.py --dry-run  # prints what it would write
"""
import csv
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALERTS = ROOT / "data" / "alerts.csv"
GAP = re.compile(r"^(?:(\d+)h)?(\d+)m(\d+)s gap between calls \(previous ended around (\S+ (\S+)), next started (\S+ (\S+))\)")
SUMMARY_TAB, DAY_TAB = "Daily summary", "Alerts by day"


def classify(reason):
    """(type, gap minutes or None, detail) for one alert reason."""
    m = GAP.match(reason or "")
    if m:
        minutes = int(m.group(1) or 0) * 60 + int(m.group(2)) + int(m.group(3)) / 60
        return "Gap between calls", round(minutes, 1), f"no call from {m.group(5)[:5]} to {m.group(7)[:5]}"
    if "offline during shift" in (reason or ""):
        return "Offline in shift", None, "offline while calls were waiting"
    if "no activity change" in (reason or ""):
        return "Idle 15+ min", None, "online but no activity while calls were waiting"
    return "Other", None, reason or ""


def load(path=ALERTS):
    rows = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            if (r.get("extension") or "").strip().lower() == "test":
                continue
            try:
                ts = datetime.fromisoformat(r["timestamp"])
            except Exception:
                continue
            kind, gap, detail = classify(r.get("reason"))
            m = GAP.match(r.get("reason") or "")
            day = m.group(4)[:10] if m else ts.strftime("%Y-%m-%d")      # a gap belongs to the day it happened
            when = m.group(5)[:5] if m else ts.strftime("%H:%M")       # a gap is shown at the time it started
            rows.append({"day": day, "ts": ts, "when": when, "ext": r.get("extension", ""), "who": r.get("manager", ""),
                         "kind": kind, "gap": gap, "detail": detail})
    return rows


def _fmt_minutes(m):
    if not m:
        return ""
    h, mm = divmod(int(round(m)), 60)
    return f"{h} h {mm:02d} min" if h else f"{mm} min"


def summary_values(rows, logouts=()):
    """logouts: rows of the "Logouts" tab (written by presence/worker.js): date, out at, ext, operator, back at, minutes."""
    agg = defaultdict(lambda: {"off": 0, "idle": 0, "gaps": 0, "longest": 0.0, "lost": 0.0, "ext": "",
                               "logouts": 0, "away": 0.0, "open": False})
    for lo in logouts:
        lo = list(lo) + [""] * 6
        if not re.match(r"\d{4}-\d{2}-\d{2}$", lo[0]):
            continue                                   # the header row
        a = agg[(lo[0], lo[3])]
        a["ext"] = lo[2]
        a["logouts"] += 1
        try:
            a["away"] += float(lo[5])
        except ValueError:
            a["open"] = True                           # still logged out
    for r in rows:
        a = agg[(r["day"], r["who"])]
        a["ext"] = r["ext"]
        if r["kind"] == "Offline in shift":
            a["off"] += 1
        elif r["kind"] == "Idle 15+ min":
            a["idle"] += 1
        elif r["kind"] == "Gap between calls":
            a["gaps"] += 1
            a["longest"] = max(a["longest"], r["gap"])
            a["lost"] += r["gap"]
    out = [["Date", "Operator", "Extension", "Offline alerts", "Idle alerts", "Call gaps", "Longest gap",
            "Total gap time", "Log-outs", "Time logged out"]]
    days = sorted({d for d, _ in agg}, reverse=True)
    for day in days:
        people = sorted(((w, a) for (d, w), a in agg.items() if d == day), key=lambda x: (-x[1]["lost"] - x[1]["away"], -x[1]["off"], x[0]))
        for who, a in people:
            out.append([day, who, a["ext"], a["off"] or "", a["idle"] or "", a["gaps"] or "",
                        _fmt_minutes(a["longest"]), _fmt_minutes(a["lost"]), a["logouts"] or "",
                        (_fmt_minutes(a["away"]) + (" + still out" if a["open"] else "")).strip(" +") if a["logouts"] else ""])
    return out


def day_values(rows):
    """Values plus the row numbers (0-based) that are day headings, for formatting."""
    out = [["Time", "Operator", "Extension", "Alert", "Gap (min)", "Details"]]
    heads = []
    by_day = defaultdict(list)
    for r in rows:
        by_day[r["day"]].append(r)
    for day in sorted(by_day, reverse=True):
        items = sorted(by_day[day], key=lambda r: (r["who"], r["when"]))
        n_gap = sum(1 for r in items if r["kind"] == "Gap between calls")
        n_off = sum(1 for r in items if r["kind"] == "Offline in shift")
        label = datetime.strptime(day, "%Y-%m-%d").strftime("%A %d %B %Y")
        heads.append(len(out))
        out.append([f"{label}   ({n_off} offline, {n_gap} call gaps)", "", "", "", "", ""])
        for r in items:
            out.append([r["when"], r["who"], r["ext"], r["kind"], r["gap"] if r["gap"] is not None else "",
                        r["detail"]])
        out.append(["", "", "", "", "", ""])
    return out, heads


def _service():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    creds = service_account.Credentials.from_service_account_file(
        str(ROOT / "credentials" / "google_service_account.json"), scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def write(sheet_id, summary, days, heads, svc=None):
    svc = svc or _service()
    meta = svc.spreadsheets().get(spreadsheetId=sheet_id, fields="sheets(properties(sheetId,title))").execute()
    ids = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta["sheets"]}
    adds = [{"addSheet": {"properties": {"title": t, "index": i}}} for i, t in enumerate((SUMMARY_TAB, DAY_TAB)) if t not in ids]
    if adds:
        rep = svc.spreadsheets().batchUpdate(spreadsheetId=sheet_id, body={"requests": adds}).execute()
        for r in rep["replies"]:
            p = r["addSheet"]["properties"]
            ids[p["title"]] = p["sheetId"]
    for tab, values in ((SUMMARY_TAB, summary), (DAY_TAB, days)):
        svc.spreadsheets().values().clear(spreadsheetId=sheet_id, range=f"'{tab}'").execute()
        svc.spreadsheets().values().update(spreadsheetId=sheet_id, range=f"'{tab}'!A1", valueInputOption="RAW",
                                           body={"values": values}).execute()
    bold = {"textFormat": {"bold": True}}
    reqs = []
    for tab, widths in ((SUMMARY_TAB, [100, 190, 80, 110, 90, 80, 100, 120, 80, 130]), (DAY_TAB, [80, 190, 80, 140, 80, 260])):
        sid = ids[tab]
        reqs += [{"unmergeCells": {"range": {"sheetId": sid}}},
                 {"repeatCell": {"range": {"sheetId": sid}, "cell": {"userEnteredFormat": {}}, "fields": "userEnteredFormat"}},
                 {"updateSheetProperties": {"properties": {"sheetId": sid, "gridProperties": {"frozenRowCount": 1}},
                                            "fields": "gridProperties.frozenRowCount"}},
                 {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1},
                                 "cell": {"userEnteredFormat": {**bold, "backgroundColor": {"red": .9, "green": .94, "blue": .96}}},
                                 "fields": "userEnteredFormat(textFormat,backgroundColor)"}}]
        reqs += [{"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1},
                                                "properties": {"pixelSize": w}, "fields": "pixelSize"}} for i, w in enumerate(widths)]
    sid = ids[DAY_TAB]
    for h in heads:
        rng = {"sheetId": sid, "startRowIndex": h, "endRowIndex": h + 1, "startColumnIndex": 0, "endColumnIndex": 6}
        reqs += [{"mergeCells": {"range": rng, "mergeType": "MERGE_ALL"}},
                 {"repeatCell": {"range": rng, "cell": {"userEnteredFormat": {**bold, "backgroundColor": {"red": .86, "green": .93, "blue": .91}}},
                                 "fields": "userEnteredFormat(textFormat,backgroundColor)"}}]
    svc.spreadsheets().batchUpdate(spreadsheetId=sheet_id, body={"requests": reqs}).execute()


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    import yaml
    cfg = yaml.safe_load(open(ROOT / "config.yaml", encoding="utf-8"))
    sheet_id = cfg["vapi"]["zadarma"]["monitor"].get("alert_sheet_id")
    rows = load()
    logouts = []
    if sheet_id and "--dry-run" not in sys.argv:
        try:
            logouts = _service().spreadsheets().values().get(spreadsheetId=sheet_id, range="'Logouts'!A1:F").execute().get("values", [])
        except Exception:
            logouts = []                               # tab not created yet: nobody has logged out since the watcher started
    summary = summary_values(rows, logouts)
    days, heads = day_values(rows)
    if "--dry-run" in sys.argv or not sheet_id:
        for line in summary[:12]:
            print(line)
        for line in days[:12]:
            print(line)
        return
    write(sheet_id, summary, days, heads)
    print(f"Wrote {len(summary) - 1} summary rows and {len(rows)} alerts over {len(heads)} days.")


if __name__ == "__main__":
    main()
