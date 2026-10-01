"""Flags when too much time passes between one of an operator's calls ending and their next one
starting, during shift hours - a different signal from agent/pause_monitor.py's presence check
(online/offline), which cannot tell "on a call" from "idle between calls" at all. This is
retrospective by nature: it reads completed call history (Zadarma's PBX call statistics), so a gap
is only ever noticed on the next scheduled run (every ~10 min, same cadence as pause_monitor.py),
not the instant it happens - a true real-time alarm would need an always-on service, not a
scheduled GitHub Actions job.

    python agent/call_gap_monitor.py --dry-run   # print what would be flagged, send nothing
    python agent/call_gap_monitor.py             # check once, send real alerts, save state

State: data/call_gap_seen.csv (one row per call_id already alerted on, so the same gap is never
reported twice - following the same CSV-as-database pattern as the rest of this project).
"""
import argparse
import csv
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))
import zadarma_client as zc  # noqa: E402
import sheet_log  # noqa: E402
from vapi_call import load_env  # noqa: E402
from pause_monitor import in_shift, _append_csv, _default_sheet_log  # noqa: E402

SEEN_PATH = ROOT / "data" / "call_gap_seen.csv"
GAP_ALERTS_PATH = ROOT / "data" / "alerts.csv"   # same file pause_monitor.py writes to - one alert log
ALERT_FIELDS = ["timestamp", "extension", "manager", "reason", "sent_ok"]


def _read_seen():
    if not SEEN_PATH.exists():
        return set()
    with open(SEEN_PATH, newline="", encoding="utf-8-sig") as f:
        return {r["call_id"] for r in csv.DictReader(f)}


def _append_seen(call_id):
    new = not SEEN_PATH.exists()
    SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SEEN_PATH, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["call_id"])
        if new:
            w.writeheader()
        w.writerow({"call_id": call_id})


def _parse(ts):
    return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def find_gaps(calls, max_gap_minutes):
    """calls: list of {"callstart": "...", "seconds": int, "call_id": ...} for ONE extension.
    Returns [(gap_seconds, prev_call, next_call), ...] for every consecutive pair whose gap exceeds
    max_gap_minutes. A 0-duration call (no answer/busy) is treated as ending at its own start time -
    it's a point-in-time attempt, not an occupied span."""
    rows = sorted(calls, key=lambda c: c["callstart"])
    gaps = []
    for prev, nxt in zip(rows, rows[1:]):
        prev_end = _parse(prev["callstart"]) + timedelta(seconds=int(prev.get("seconds") or 0))
        next_start = _parse(nxt["callstart"])
        gap = (next_start - prev_end).total_seconds()
        if gap > max_gap_minutes * 60:
            gaps.append((gap, prev, nxt))
    return gaps


def check(cfg, now=None, get_fn=None, sheet_log_fn=None, dry_run=False):
    now = now or datetime.now(timezone.utc)
    ext_cfg = cfg.get("extensions") or {}
    max_gap_minutes = float(cfg.get("max_call_gap_minutes") or 1)
    alert_sheet_id = cfg.get("alert_sheet_id") or ""
    if alert_sheet_id == "REPLACE_ME":
        alert_sheet_id = ""

    if not in_shift(cfg, now):
        return []

    # Look back far enough to span one full shift, so a gap near shift-start is still visible even
    # if this is the first run after a long idle period.
    start = now.replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
    end = now.strftime("%Y-%m-%d %H:%M:%S")
    all_calls = zc.statistics_pbx(start, end, _get_fn=get_fn)

    by_ext = {}
    for c in all_calls:
        by_ext.setdefault(c.get("sip"), []).append(c)

    seen = _read_seen()
    events = []
    for ext, entry in ext_cfg.items():
        manager = entry.get("manager", "") if isinstance(entry, dict) else ""
        calls = by_ext.get(ext, [])
        if len(calls) < 2:
            continue
        for gap_seconds, prev, nxt in find_gaps(calls, max_gap_minutes):
            call_id = nxt.get("call_id") or f"{ext}:{nxt.get('callstart')}"
            if call_id in seen:
                continue
            minutes, seconds = divmod(int(gap_seconds), 60)
            reason = (f"{minutes}m{seconds:02d}s gap between calls (previous ended around "
                      f"{prev['callstart']}, next started {nxt['callstart']})")
            events.append((ext, manager, reason))
            if not dry_run:
                sheet_ok = None
                if alert_sheet_id:
                    sheet_ok = (sheet_log_fn or _default_sheet_log)(alert_sheet_id, {
                        "timestamp": now.isoformat(), "extension": ext, "manager": manager,
                        "reason": reason, "sent_ok": "",
                    })
                _append_csv(GAP_ALERTS_PATH, {"timestamp": now.isoformat(), "extension": ext,
                                              "manager": manager, "reason": reason,
                                              "sent_ok": bool(sheet_ok)}, ALERT_FIELDS)
                _append_seen(call_id)
    return events


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    load_env()
    full = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    cfg = ((full.get("vapi") or {}).get("zadarma") or {}).get("monitor", {})
    events = check(cfg, dry_run=a.dry_run)
    if not events:
        print("No call-gap alerts." + (" (dry run)" if a.dry_run else ""))
    for ext, manager, reason in events:
        print(f"{'[DRY RUN] ' if a.dry_run else ''}ALERT ext {ext} ({manager or 'no manager'}): {reason}")


if __name__ == "__main__":
    main()
