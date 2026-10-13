"""Offline tests for scripts/build_alert_report.py. No Google calls. Run: python tests/test_build_alert_report.py"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import build_alert_report as rep  # noqa: E402

results = []


def check(name, got, want):
    ok = got == want
    results.append(ok)
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f": got {got!r}, want {want!r}"))


csv_text = """timestamp,extension,manager,reason,sent_ok
2026-09-30T19:26:43+00:00,test,Test row,manual test,True
2026-10-09T13:58:00+00:00,105,Zahra Batool,offline during shift hours with work still queued,True
2026-10-09T13:58:00+00:00,105,Zahra Batool,"31m16s gap between calls (previous ended around 2026-10-09 08:43:34, next started 2026-10-09 09:14:50)",True
2026-10-10T13:08:00+00:00,105,Zahra Batool,"1h02m00s gap between calls (previous ended around 2026-10-09 15:00:00, next started 2026-10-09 16:02:00)",True
2026-10-10T13:08:00+00:00,102,Giuseppe Papa,no activity change for 15+ minutes with work still queued,True
"""
path = Path(tempfile.mkdtemp()) / "alerts.csv"
path.write_text(csv_text, encoding="utf-8")
rows = rep.load(path)
check("the test row is left out", len(rows), 4)
check("a gap is filed under the day it happened, not the day it was noticed", [r["day"] for r in rows if r["gap"]], ["2026-10-09", "2026-10-09"])
check("gap lengths are read as minutes, hours included", sorted(r["gap"] for r in rows if r["gap"]), [31.3, 62.0])
summary = rep.summary_values(rows)
check("one summary row per day and operator, newest day first",
      [r[:2] for r in summary[1:]], [["2026-10-10", "Giuseppe Papa"], ["2026-10-09", "Zahra Batool"]])
check("the day's totals: 1 offline, 2 gaps, longest 1 h 02, total 1 h 33",
      summary[2][3:], [1, "", 2, "1 h 02 min", "1 h 33 min"])
days, heads = rep.day_values(rows)
check("each day gets its own heading", [days[h][0].split("   ")[0] for h in heads], ["Saturday 10 October 2026", "Friday 09 October 2026"])
check("a gap is shown at the time it started", [d[0] for d in days if d[3] == "Gap between calls"], ["08:43", "15:00"])

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
