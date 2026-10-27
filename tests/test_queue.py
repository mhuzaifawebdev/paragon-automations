"""Offline tests for the batch queue: scripts/drive_inbox.py, scripts/queue_tick.py and scraper2/reuse.py.
Drive is faked. Run: python tests/test_queue.py"""
import io
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scraper2"))
import drive_inbox as di  # noqa: E402
import queue_tick as qt  # noqa: E402
import reuse  # noqa: E402

results = []


def check(name, got, want):
    ok = got == want
    results.append(ok)
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f": got {got!r}, want {want!r}"))


# ---- names
check("batch name from the file name", di.slug("Spain Schools – Oct (2).xlsx"), "spain-schools-oct-2")
check("accents and symbols folded", di.slug("Écoles Françaises.csv"), "ecoles-francaises")
check("a very short name still makes a valid batch name", di.slug("A.csv"), "batch-a")
taken = {"spain-schools"}
check("a taken name gets -2, then -3", [di.unique_name("spain-schools", taken), di.unique_name("spain-schools", taken)],
      ["spain-schools-2", "spain-schools-3"])
check("names stay within 40 characters", len(di.unique_name("x" * 40, {"x" * 40})) <= 40, True)

# ---- reading files
CSV = "University,Country,Website\nUniversity of Malta,Malta,https://um.edu.mt\nHERS,Belgium,https://hers.be\n"
check("a CSV with an institute column is read", di.table_from_file("list.csv", "text/csv", CSV.encode())[0], CSV)
for name, mime, data, want in [("old.xls", "application/vnd.ms-excel", b"x", "old Excel format"),
                               ("notes.pdf", "application/pdf", b"x", "Only CSV, Excel"),
                               ("bad.csv", "text/csv", b"Phone,Email\n123,a@b.co\n", "No institute rows")]:
    try:
        di.table_from_file(name, mime, data)
        check(f"{name} is refused", "accepted", want)
    except di.Unusable as e:
        check(f"{name} is refused with a reason the client can act on", want in str(e), True)
import openpyxl  # noqa: E402
wb = openpyxl.Workbook()
wb.active.title = "Notes"
wb.active.append(["just", "notes"])
ws = wb.create_sheet("Institutes")
ws.append(["Institution Name", "Country"])
ws.append(["University of Malta", "Malta"])
buf = io.BytesIO()
wb.save(buf)
text, note = di.table_from_file("list.xlsx", "", buf.getvalue())
check("Excel: the first sheet with institute rows is used, and the note says which were skipped",
      ("University of Malta" in text, "Notes" in note), (True, True))
big = "University\n" + "".join(f"Uni {i}\n" for i in range(2500))
parts = di.split(big)
check("a list over 1,200 rows is split into parts of 1,000, each with the header",
      ([p.count("\n") - 1 for p in parts], all(p.startswith("University") for p in parts)), ([1000, 1000, 500], True))
check("a list within the limit stays one batch", len(di.split(CSV)), 1)


# ---- inbox pickup with a fake Drive
class FakeDrive:
    def __init__(self, files):
        self.files, self.moves = files, []

    def inbox_files(self):
        return list(self.files)

    def download(self, f):
        return f["data"]

    def to_processing(self, f):
        self.moves.append((f["name"], "Processing", ""))

    def to_done(self, f, note=""):
        self.moves.append((f["name"], "Done", note))

    def to_failed(self, f, note):
        self.moves.append((f["name"], "Failed", note))


root = Path(tempfile.mkdtemp())
q = {"items": [], "paused": None}
drive = FakeDrive([{"id": "f1", "name": "Spain schools.csv", "mimeType": "text/csv", "md5Checksum": "A", "data": CSV.encode()},
                   {"id": "f2", "name": "Spain schools.csv", "mimeType": "text/csv", "md5Checksum": "A", "data": CSV.encode()},
                   {"id": "f3", "name": "broken.csv", "mimeType": "text/csv", "md5Checksum": "B", "data": b"Phone,Email\n123,a@b.co\n"},
                   {"id": "f4", "name": "Big list.csv", "mimeType": "text/csv", "md5Checksum": "C", "data": big.encode()}])
di.pickup(drive, root, q, "2026-10-26T10:00:00Z")
check("one batch per file; a big file becomes parts; the same file twice is queued once",
      [(i["batch"], i.get("part")) for i in q["items"]],
      [("spain-schools", None), ("big-list-p1", "1/3"), ("big-list-p2", "2/3"), ("big-list-p3", "3/3")])
check("the uploads are written for the batch job", sorted(p.name for p in (root / "uploads").iterdir()),
      ["big-list-p1.csv", "big-list-p2.csv", "big-list-p3.csv", "spain-schools.csv"])
check("files are moved: queued -> Processing, repeat -> Done, broken -> Failed",
      [(m[0], m[1]) for m in drive.moves],
      [("Spain schools.csv", "Processing"), ("Spain schools.csv", "Done"), ("broken.csv", "Failed"), ("Big list.csv", "Processing")])
check("the repeat's note names the earlier batch", "spain-schools" in drive.moves[1][2], True)


# ---- the queue
class FakeOps:
    def __init__(self):
        self.started, self.finished, self.moved = [], [], []

    def inbox_pickup(self, q):
        return []

    def start(self, it):
        self.started.append(it["batch"])

    def finish(self, q, it, now):
        self.finished.append(it["batch"])
        return qt.settle(q, it, "done", "ok", self, now)

    def source_to(self, it, folder, note):
        self.moved.append((it["batch"], folder))


qt._write(root / "queue.json", q)
ops = FakeOps()
qt.tick(root, "2026-10-26T10:05:00Z", {}, ops)
check("nothing running -> the first queued batch starts, only one", ops.started, ["spain-schools"])
qt.tick(root, "2026-10-26T10:15:00Z", {}, ops)
check("while it runs, nothing else starts", ops.started, ["spain-schools"])
qt._write(root / "spain-schools" / "progress.json", {"status": "done", "total_rows": 2, "done": 2, "pending": 0})
qt.tick(root, "2026-10-26T10:25:00Z", {}, ops)
check("finished -> finalised, and the next batch starts in the same tick",
      (ops.finished, ops.started), (["spain-schools"], ["spain-schools", "big-list-p1"]))
check("its source file goes to Done once its only part is finished", ops.moved, [("spain-schools", "Done")])
qt._write(root / "big-list-p1" / "progress.json", {"status": "running", "done": 3, "total_rows": 1000,
                                                     "generated_at": "2026-10-26T10:30:00Z"})
qt.tick(root, "2026-10-26T12:00:00Z", {}, ops)
check("no progress for over an hour -> started again", ops.started.count("big-list-p1"), 2)
for t in ("13:10", "14:20", "15:30"):
    qt.tick(root, f"2026-10-26T{t}:00Z", {}, ops)
item = [i for i in qt.load_queue(root)["items"] if i["batch"] == "big-list-p1"][0]
check("after 3 restarts without progress -> failed, and the next part starts",
      (item["state"], ops.started[-1]), ("failed", "big-list-p2"))
check("the dropped file stays in Processing while its other parts are still to run",
      [m for m in ops.moved if m[0].startswith("big-list")], [])
(root / "big-list-p3").mkdir(exist_ok=True)
(root / "big-list-p3" / "CANCELLED").write_text("x")
qt._write(root / "big-list-p2" / "progress.json", {"status": "done", "total_rows": 1, "done": 1, "pending": 0})
qt.tick(root, "2026-10-26T16:00:00Z", {}, ops)
states = {i["batch"]: i["state"] for i in qt.load_queue(root)["items"]}
check("a batch cancelled before it started is skipped", states["big-list-p3"], "cancelled")
check("when every part is settled, the file leaves Processing (Failed, because one part failed)",
      [m for m in ops.moved if m[0].startswith("big-list")][-1][1], "Failed")

root2 = Path(tempfile.mkdtemp())
qt._write(root2 / "queue.json", {"items": [{"batch": "b1", "state": "queued"}]})
qt._write(root2 / "_spend.json", {"2026-10": 50.5})
ops2 = FakeOps()
qt.tick(root2, "2026-10-26T10:00:00Z", {"max_queue_usd_per_month": 50}, ops2)
check("monthly cap reached -> nothing starts and the queue says why",
      (ops2.started, "cap" in (qt.load_queue(root2)["paused"] or "")), ([], True))
qt.tick(root2, "2026-11-01T00:10:00Z", {"max_queue_usd_per_month": 50}, ops2)
check("a new month -> the queue continues", ops2.started, ["b1"])
# ---- the total cap over all months: never exceeded
root3 = Path(tempfile.mkdtemp())
qt._write(root3 / "queue.json", {"items": [{"batch": "t1", "state": "queued"}, {"batch": "t2", "state": "queued"}]})
qt._write(root3 / "_spend.json", {"2026-10": 800.0, "2026-11": 30.0})
ops3 = FakeOps()
qt.tick(root3, "2026-11-02T10:00:00Z", {"max_queue_usd_total": 850}, ops3)
t1 = qt.load_queue(root3)["items"][0]
check("total cap: the batch may spend only what is left of all months together (850 - 830 - 1 held back)",
      (ops3.started, t1["cap_usd"], t1.get("reader")), (["t1"], 19.0, None))
qt._write(root3 / "t1" / "paid_usage.json", {"spent_usd": 18.7})
qt._write(root3 / "t1" / "progress.json", {"status": "error", "error": "broken"})
qt.tick(root3, "2026-11-02T11:00:00Z", {"max_queue_usd_total": 850}, ops3)
t2 = qt.load_queue(root3)["items"][1]
check("a failed batch's spending is counted too, and once the total is used up the next batch runs free with $0",
      (qt.total_spent(root3), ops3.started[-1], t2["cap_usd"], t2["reader"]), (848.7, "t2", 0.0, "free"))
qt._write(root3 / "t2" / "progress.json", {"status": "cancelled"})
qt.tick(root3, "2026-11-02T12:00:00Z", {"max_queue_usd_total": 850}, ops3)
qt.tick(root3, "2026-11-02T12:10:00Z", {"max_queue_usd_total": 850}, ops3)
check("spending is recorded once per batch, however many ticks see it", qt.total_spent(root3), 848.7)
real = qt.Ops(root3)
real.start({"batch": "t3", "reader": "free", "cap_usd": 0.0})
import batch_runner  # noqa: E402
check("the batch runner reads the cap the queue gave it", batch_runner.queue_cap(root3 / "t3"), 0.0)
batch_runner.apply_mode(root3 / "t3", {"max_paid_usd_per_batch": 6, "max_fast_usd_per_batch": 15})
check("so its paid limit is $0, not the usual per-batch $6", batch_runner.discover.BUDGET["limit"], 0.0)

(root2 / "_queue_requests").mkdir()
qt._write(root2 / "_queue_requests" / "web1.json", {"batch": "web1", "reader": "free"})
qt.tick(root2, "2026-11-01T00:20:00Z", {}, ops2)
check("a page upload joins the queue (and its request file is used up)",
      ([i["batch"] for i in qt.load_queue(root2)["items"]], list((root2 / "_queue_requests").iterdir())), (["b1", "web1"], []))

# ---- finishing a batch: archive to Drive, index, spending, small GitHub footprint
import drive_upload  # noqa: E402

root4 = Path(tempfile.mkdtemp())
b = root4 / "done-batch"
(b / "scrape_results").mkdir(parents=True)
qt._write(b / "progress.json", {"status": "done", "total_rows": 1, "done": 1, "pending": 0, "drive_link": "https://drive/x"})
qt._write(b / "scrape_results" / "r001.json", {"row_id": "r001", "scraped_at": "2026-10-26T09:00:00Z",
          "contacts": {"official_website": "https://um.edu.mt", "contact": {"name": "A B", "email": "a@um.edu.mt"}, "notes": ""},
          "network": {"external_collaboration": [{}, {}], "internal_collaboration": []}})
(b / "numbers_clean.csv").write_text("row_id,institution_name,country_sheet\nr001,University of Malta,Malta\n", encoding="utf-8")
qt._write(b / "paid_usage.json", {"spent_usd": 0.75})
(b / "results.xlsx").write_bytes(b"xlsx")
(root4 / "uploads").mkdir()
(root4 / "uploads" / "done-batch.csv").write_text("x")
archived = []
real = (drive_upload.put_bytes, drive_upload.subfolder)
drive_upload.put_bytes = lambda svc, data, name, folder, mime="": archived.append((name, folder)) or "https://drive/a"
drive_upload.subfolder = lambda svc, name, root=None: name


class FakeQueueDrive(FakeDrive):
    svc = "svc"


reuse._CACHE.update(index=None, archives={})
fq = {"items": [{"batch": "done-batch", "state": "running", "file_id": "fz", "source": "list.csv"}]}
fdrive = FakeQueueDrive([])
qt.Ops(root4, fdrive).finish(fq, fq["items"][0], "2026-10-26T10:00:00Z")
drive_upload.put_bytes, drive_upload.subfolder = real
check("the full data is archived to Drive/_archive", archived, [("done-batch data.json.gz", "_archive")])
check("only progress.json and summary.json stay in GitHub; the upload copy is removed",
      (sorted(p.name for p in b.iterdir()), (root4 / "uploads" / "done-batch.csv").exists()), (["progress.json", "summary.json"], False))
summary = json.loads((b / "summary.json").read_text(encoding="utf-8"))
check("the summary keeps the dashboard figures", (summary["totals"]["institutes"], summary["totals"]["partners"], summary["spent_usd"]),
      (1, 2, 0.75))
check("the batch's institutes are in the reuse index; its cost counts towards the month",
      (sorted(json.loads((root4 / "_index.json").read_text())), json.loads((root4 / "_spend.json").read_text())),
      (["n:university of malta|malta", "w:um.edu.mt"], {"2026-10": 0.75}))
check("the item is done with its Drive link, and the source file moved to Done",
      (fq["items"][0]["state"], fq["items"][0]["drive_link"], fdrive.moves[-1][:2]), ("done", "https://drive/x", ("list.csv", "Done")))
sys.path.insert(0, str(ROOT / "scripts"))
import build_dashboard_stats as bds  # noqa: E402
check("the dashboard still counts the archived batch", bds.build(root4)["totals"]["institutes"], 1)

# ---- reuse across batches
check("institute keys: name+country and website host",
      reuse.keys("Université de Malte", "Malta", "https://www.um.edu.mt/en"), ["n:universite de malte|malta", "w:um.edu.mt"])
old = {"r001": {"row_id": "r001", "scraped_at": "2026-10-01T00:00:00Z",
                "contacts": {"row_id": "r001", "official_website": "https://um.edu.mt", "contact": {"name": "A B"}, "notes": "x"},
                "network": {"external_collaboration": [{}] * 3}, "timing": {"cost_usd_estimate": 0.02}}}
idx = {}
reuse.add_batch(idx, "first", [{"row_id": "r001", "institution_name": "University of Malta", "country_sheet": "Malta"}], old)
check("a finished batch's institutes are indexed by name and by website", sorted(idx), ["n:university of malta|malta", "w:um.edu.mt"])
root3 = Path(tempfile.mkdtemp())
(root3 / "_index.json").write_text(json.dumps(idx))
reuse._CACHE.update(index=None, archives={})
got = reuse.lookup({"row_id": "r007", "institution_name": "UNIVERSITY OF MALTA", "country_sheet": "malta"}, root3, "second",
                   fetch_archive=lambda b: reuse.archive_bytes(old), today="2026-10-26")
check("the same institute in a later batch is copied from the archive at no cost, under its own row id",
      (got["row_id"], got["contacts"]["row_id"], got["reused_from"]["batch"], got["timing"]["cost_usd_estimate"],
       len(got["network"]["external_collaboration"])), ("r007", "r007", "first", 0, 3))
reuse._CACHE.update(index=None, archives={})
check("a result older than the reuse age is scraped again",
      reuse.lookup({"row_id": "r9", "institution_name": "University of Malta", "country_sheet": "Malta"}, root3, "second",
                   max_age_days=10, fetch_archive=lambda b: reuse.archive_bytes(old), today="2026-10-26"), None)
reuse._CACHE.update(index=None, archives={})
check("an unknown institute is scraped normally",
      reuse.lookup({"row_id": "r9", "institution_name": "Other College", "country_sheet": "Spain"}, root3, "second",
                   fetch_archive=lambda b: None), None)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
