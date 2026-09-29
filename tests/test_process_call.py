"""Offline tests for agent/process_call.py's row_id mapping (the piece ARCHITECTURE.md flags as
unverified against a live payload). No `claude` CLI call, no network. Run:
    python tests/test_process_call.py
"""
import csv
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(ROOT / "data"))
import process_call as pc  # noqa: E402
import state as st  # noqa: E402

results = []


def check(name, got, want):
    ok = got == want
    results.append(ok)
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f": got {got!r}, want {want!r}"))


with tempfile.TemporaryDirectory() as tmp:
    log = Path(tmp) / "placed.csv"
    with open(log, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["call_id", "row_id", "to_number", "placed_at"])
        w.writerow(["call-abc", "r035", "+35621112222", "2026-09-27T00:00:00+00:00"])
    pc.PLACED_LOG = log

    check("tier 1: placed.csv wins even when metadata also has a (different) row_id",
          pc.extract_row_id({"message": {"call": {"id": "call-abc", "metadata": {"row_id": "r999"}}}}), ("r035", "placed_log"))

    pc.PLACED_LOG = Path(tmp) / "no_such_file.csv"
    check("tier 2: falls back to metadata.row_id when the call isn't in placed.csv",
          pc.extract_row_id({"message": {"call": {"id": "call-xyz", "metadata": {"row_id": "r050"}}}}), ("r050", "metadata"))

    real_load = st.load
    st.load = lambda: {"r010": {"row_id": "r010", "current_contact_phone": "35621119999"}}
    try:
        check("tier 3: falls back to phone-number match against campaign_state.csv",
              pc.extract_row_id({"message": {"call": {"id": "call-none", "customer": {"number": "+356 2111 9999"}}}}),
              ("r010", "phone_match"))
        check("no tier matches -> unresolved, not a crash",
              pc.extract_row_id({"message": {"call": {"id": "call-none", "customer": {"number": "+35699999999"}}}}),
              (None, "unresolved"))
    finally:
        st.load = real_load


print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
