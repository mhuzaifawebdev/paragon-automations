"""Offline tests for agent/call_gap_monitor.py. No real Zadarma calls, no cost.
Run: python tests/test_call_gap_monitor.py
"""
import os
import sys
import tempfile
import shutil
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))
os.environ.setdefault("ZADARMA_API_KEY", "test-key")
os.environ.setdefault("ZADARMA_API_SECRET", "test-secret")
import call_gap_monitor as cgm  # noqa: E402

results = []


def check(name, got, want):
    ok = got == want
    results.append(ok)
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f": got {got!r}, want {want!r}"))


# ---- find_gaps: the core math, real timestamps from a live account test earlier this project ----
calls = [
    {"callstart": "2026-10-01 09:07:08", "seconds": 0, "call_id": "a"},    # busy, 0 duration
    {"callstart": "2026-10-01 09:08:04", "seconds": 241, "call_id": "b"},  # answered, ends 09:12:05
    {"callstart": "2026-10-01 09:15:55", "seconds": 0, "call_id": "c"},    # no answer, 3m50s after
]
gaps = cgm.find_gaps(calls, max_gap_minutes=1)
check("a short 56s gap under the 1-minute threshold is not flagged", len(gaps), 1)
check("a real 3m50s gap over the threshold is flagged", round(gaps[0][0]), 230)

gaps_loose = cgm.find_gaps(calls, max_gap_minutes=10)
check("raising the threshold above both gaps flags nothing", gaps_loose, [])

check("a single call (no pairs possible) never raises", cgm.find_gaps(calls[:1], 1), [])
check("no calls at all never raises", cgm.find_gaps([], 1), [])


# ---- check(): shift-hours gating, dedup via data/call_gap_seen.csv, Sheet logging ----
real_seen_path, real_alerts_path = cgm.SEEN_PATH, cgm.GAP_ALERTS_PATH
tmpdir = Path(tempfile.mkdtemp())
cgm.SEEN_PATH, cgm.GAP_ALERTS_PATH = tmpdir / "call_gap_seen.csv", tmpdir / "alerts.csv"
try:
    cfg = {"shift_start_utc": "00:00", "shift_end_utc": "23:59", "max_call_gap_minutes": 1,
           "extensions": {"105": {"manager": "Zahra Batool"}}}
    noon = datetime(2026, 10, 1, 9, 20, tzinfo=timezone.utc)

    def fake_get(url, auth_header):
        return {"status": "success", "start": "", "end": "", "version": "2", "stats": [
            {**c, "sip": "105"} for c in calls
        ]}

    events = cgm.check(cfg, now=noon, get_fn=fake_get, sheet_log_fn=lambda *a: True)
    check("a real gap during shift hours is flagged once", len(events), 1)
    check("the manager label from config is carried into the event", events[0][1], "Zahra Batool")

    events_again = cgm.check(cfg, now=noon, get_fn=fake_get, sheet_log_fn=lambda *a: True)
    check("the same gap is never flagged twice (deduped via call_gap_seen.csv)", events_again, [])

    cfg_outside = {**cfg, "shift_start_utc": "07:00", "shift_end_utc": "08:00"}
    events_outside = cgm.check(cfg_outside, now=noon, get_fn=fake_get)
    check("outside shift hours, nothing is checked at all", events_outside, [])
finally:
    cgm.SEEN_PATH, cgm.GAP_ALERTS_PATH = real_seen_path, real_alerts_path
    shutil.rmtree(tmpdir, ignore_errors=True)


print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
