"""Offline tests for scripts/resume_due_batches.py. Nothing is sent to GitHub. Run: python tests/test_resume_due_batches.py"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import resume_due_batches as rd  # noqa: E402

results = []


def check(name, got, want):
    ok = got == want
    results.append(ok)
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f": got {got!r}, want {want!r}"))


NOW = 1_800_000_000.0                       # 2027-01-15T08:00:00Z
PAST, FUTURE = "2027-01-15T07:00:00+00:00", "2027-01-15T09:00:00+00:00"

check("a paused batch whose time has passed is due", rd.due({"status": "waiting", "resume_at": PAST}, NOW), True)
check("a paused batch whose time has not come is left alone", rd.due({"status": "waiting", "resume_at": FUTURE}, NOW), False)
check("running, finished and cancelled batches are never started",
      [rd.due({"status": s, "resume_at": PAST}, NOW) for s in ("running", "done", "cancelled", "error")], [False] * 4)
check("a paused batch with no readable time is tried rather than left for ever", rd.due({"status": "waiting"}, NOW), True)
check("a paused batch that was cancelled is started at once, so the workflow can mark it cancelled",
      rd.due({"status": "waiting", "resume_at": FUTURE}, NOW, cancelled=True), True)

root = Path(tempfile.mkdtemp())
for name, prog in {"due-one": {"status": "waiting", "resume_at": PAST, "provider": "gemini", "chunk_size": 10},
                   "later": {"status": "waiting", "resume_at": FUTURE},
                   "finished": {"status": "done"},
                   "refused": {"status": "waiting", "resume_at": PAST}}.items():
    (root / name).mkdir()
    (root / name / "progress.json").write_text(json.dumps(prog), encoding="utf-8")
(root / "broken").mkdir()
(root / "broken" / "progress.json").write_text("{not json", encoding="utf-8")
sent = []


def start(batch, progress):
    if batch == "refused":
        raise RuntimeError("HTTP 401")
    sent.append((batch, progress.get("chunk_size")))


check("only the due batch is started, with its own settings", (rd.run(root, NOW, start), sent), (["due-one"], [("due-one", 10)]))
after = json.loads((root / "due-one" / "progress.json").read_text(encoding="utf-8"))
check("its time is moved 30 minutes on, so the next run does not start it twice",
      (after["status"], after["resume_at"]), ("waiting", "2027-01-15T08:30:00+00:00"))
check("a batch GitHub refused to start keeps its time and is tried again next run",
      json.loads((root / "refused" / "progress.json").read_text(encoding="utf-8"))["resume_at"], PAST)
sent.clear()
check("15 minutes later nothing is started twice", (rd.run(root, NOW + 900, start), sent), ([], []))

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
