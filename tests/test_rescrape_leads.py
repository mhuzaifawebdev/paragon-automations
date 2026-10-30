"""Offline tests for the CRM upload file (scraper2/dialer_file.py), the failed-lead script (scripts/rescrape_leads.py), their
queue_tick.py hooks and the batch runner's NO_REUSE switch. Nothing is sent anywhere. Run: python tests/test_rescrape_leads.py"""
import csv
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scraper2"))
import dialer_file as df  # noqa: E402
import drive_upload  # noqa: E402
import queue_tick as qt  # noqa: E402
import rescrape_leads as rl  # noqa: E402

results = []


def check(name, got, want):
    ok = got == want
    results.append(ok)
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f": got {got!r}, want {want!r}"))


def result(main_phone="", person=None, alternates=(), phone_alt=()):
    return {"contacts": {"phone": {"value": main_phone}, "phone_alt": list(phone_alt), "contact": person or {},
                         "alternates": list(alternates), "official_website": "https://uni.mt", "partners": ["x"]}}


# ---- numbers and hashes
check("the same number written three ways is one number",
      len({df.phone_hash("+356 2123 4567"), df.phone_hash("00356 21234567"), df.phone_hash("21234567")}), 1)
check("no hash for an empty or too-short phone", (df.phone_hash(""), df.phone_hash("12")), (None, None))

# ---- the file the CRM reads: the owner's template, lead columns only
check("the headers are exactly the owner's template", df.csv_text([]).strip(), "Phone,Name,Designation,Institute,Email,Country,Notes")
row = {"row_id": "r1", "institution_name": "University of Malta", "country_sheet": "Malta", "phone_1": "+356 2123 4567",
       "phone_2": "", "contact_name": "From Sheet", "designation": "", "email": "sheet@um.mt", "seed_urls": "https://um.mt"}
res = result("+356 2999 0000", {"name": "Ana", "designation": "Erasmus officer", "phone": "+356 2777 1111", "email": "ana@um.mt"},
             alternates=[{"name": "Ben", "designation": "Head", "phone": "+356 2888 2222", "email": "ben@um.mt"}],
             phone_alt=[{"value": "+356 2555 3333"}])
out, summary = df.file_rows([row], {"r1": res})
check("every number given back plus the ones found, 3 at most in the Phone cell",
      out[0]["Phone"], "+356 2123 4567 / +356 2777 1111 / +356 2999 0000")
check("the contact the scraper found, one row per contact person",
      [(r["Name"], r["Designation"], r["Email"]) for r in out],
      [("Ana", "Erasmus officer", "ana@um.mt"), ("Ben", "Head", "ben@um.mt")])
check("nothing but the template's columns reaches the file", "uni.mt" in df.csv_text(out) or "partners" in df.csv_text(out), False)
out2, _ = df.file_rows([row, {**row, "row_id": "r2", "institution_name": "Other College"}], {"r1": res})
check("a number is used once in a file (the CRM would skip the second as a duplicate)",
      "+356 2123 4567" in "".join(r["Phone"] for r in out2 if r["Institute"] == "Other College"), False)
_, s2 = df.file_rows([{"row_id": "r3", "institution_name": "No Number School"}, {"row_id": "r4", "institution_name": ""}], {})
check("rows with no number or no institution are left out and counted", s2, {"rows": 0, "without_number": 2})
key = df.inst_key("University of Malta", "Malta")
out3, _ = df.file_rows([{**row, "phone_1": ""}], {"r1": res}, {key: {df.phone_hash("+356 2777 1111"), df.phone_hash("+356 2999 0000")}})
check("a rescrape leaves out the numbers already tried", out3[0]["Phone"], "+356 2555 3333")

# ---- the manager's file of failed leads
root = Path(tempfile.mkdtemp())
failed = root / "failed.csv"
with open(failed, "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f)
    w.writerow(["Institute", "Country", "Phone", "Phone 2", "Status"])
    w.writerow(["University of Malta", "Malta", "+356 2777 1111", "", "unreachable"])
    w.writerow(["university of  MALTA", "malta", "+356 2999 0000", "", "Wrong Number"])
    w.writerow(["Escola X", "Spain", "+34 600 000 000", "", "wrong_number"])
    w.writerow(["Still Calling Uni", "Spain", "+34 600 000 001", "", "waiting"])
leads = rl.read_failed(failed)
check("headings in any case; only the failed leads are kept", [x["institute"] for x in leads],
      ["University of Malta", "university of  MALTA", "Escola X"])
groups = rl.group_leads(leads)
check("two leads of one institute are one group, with both numbers tried", (len(groups), len(groups[key]["tried"])), (2, 2))
rows, rescrape = rl.classify(groups, None, lookup=lambda i, c: res if "Malta" in i else None)
check("a new number already known -> upload row; nothing known -> rescrape",
      ([r["Institute"] for r in rows][:1], list(rescrape)), (["University of Malta"], ["escola x|spain"]))
check("the tried numbers are not in the upload", "2777 1111" in df.csv_text(rows) or "2999 0000" in df.csv_text(rows), False)

# ---- queueing a rescrape batch
now = datetime(2026, 10, 30, 9, 15, tzinfo=timezone.utc)
batch, written = rl.queue_rescrape(rescrape, root, now)
check("batch name carries date and time", batch, "rescrape-20261030-0915")
with open(root / "uploads" / f"{batch}.csv", encoding="utf-8") as f:
    check("the batch CSV has institute and country", list(csv.reader(f)), [["institute", "country"], ["Escola X", "Spain"]])
req = json.loads((root / "_queue_requests" / f"{batch}.json").read_text(encoding="utf-8"))
check("the queue request is a priority rescrape", (req["priority"], req["kind"], req["reader"]), (True, "rescrape", "paid"))
check("hashes only: no raw phone in the public repository", "600" in (root / "rescrape" / f"{batch}.json").read_text(encoding="utf-8"), False)

# ---- the queue: priority first, beside a normal batch, NO_REUSE, the CRM file on finish
qt._write(root / "queue.json", {"items": [{"batch": "old-batch", "state": "queued"}]})


class FakeDrive:
    svc = "svc"


ops = qt.Ops(root)
qt.tick(root, "2026-10-30T09:20:00Z", {}, ops)
items = {i["batch"]: i for i in qt.load_queue(root)["items"]}
check("the rescrape starts first, and runs beside the normal batch instead of waiting for it",
      ([s["batch"] for s in ops.to_start], items[batch]["state"], items["old-batch"]["state"]), ([batch, "old-batch"], "running", "running"))
qt._write(root / "_queue_requests" / "next-normal.json", {"batch": "next-normal"})
qt._write(root / "_queue_requests" / "rescrape-2.json", {"batch": "rescrape-2", "kind": "rescrape", "priority": True})
ops_b = qt.Ops(root)
qt.tick(root, "2026-10-30T09:25:00Z", {}, ops_b)
check("while both run, neither a second normal batch nor a second rescrape starts", ops_b.to_start, [])
check("it is marked to scrape again, not reuse the old result", (root / batch / "NO_REUSE").exists(), True)

put = []
real = (drive_upload.put_bytes, drive_upload.subfolder)
drive_upload.subfolder = lambda svc, name, root=None: name


def fail_put(*a, **k):
    raise RuntimeError("Drive down")


b = root / batch
(b / "scrape_results").mkdir(parents=True)
qt._write(b / "progress.json", {"status": "done", "total_rows": 1, "done": 1, "pending": 0, "drive_link": "https://d/x"})
(b / "numbers_clean.csv").write_text("row_id,institution_name,country_sheet\nr001,Escola X,Spain\n", encoding="utf-8")
qt._write(b / "scrape_results" / "r001.json", result("+34 911 222 333", {"name": "Marta", "phone": "+34 600 000 000"}))
q = qt.load_queue(root)
it = next(i for i in q["items"] if i["batch"] == batch)
drive_upload.put_bytes = fail_put
check("Drive down -> not settled, retried next tick, tried numbers kept",
      (qt.Ops(root, FakeDrive()).finish(q, it, "2026-10-30T12:00:00Z"), it["state"], (root / "rescrape" / f"{batch}.json").exists()),
      (f"{batch}: CRM file pending", "running", True))
ok_ops = qt.Ops(root, FakeDrive())
drive_upload.put_bytes = lambda svc, data, name, folder, mime="": put.append((name, folder, data.decode("utf-8-sig"))) or "link"
ok_ops.crm_file(it, [{"row_id": "r001", "institution_name": "Escola X", "country_sheet": "Spain"}],
                {"r001": json.loads((b / "scrape_results" / "r001.json").read_text())})
drive_upload.put_bytes, drive_upload.subfolder = real
check("the file lands in Drive/Results, under the batch's name", (put[0][0], put[0][1]), (f"{batch} CRM upload.csv", "Results"))
check("only the new number is in it (the tried mobile is left out)", ("+34 911 222 333" in put[0][2], "600 000 000" in put[0][2]), (True, False))
check("the item records the counts, and the tried numbers are removed", (it["crm_rows"], (root / "rescrape" / f"{batch}.json").exists()), (1, False))

# ---- the batch runner really scrapes again when NO_REUSE is set
import batch_runner as br  # noqa: E402

calls = []
br.reuse.lookup = lambda *a, **k: {"row_id": "r001", "timing": {}, "reused_from": {"batch": "old"}}
br.apply_mode = lambda data_dir, cfg: "free"
br.pipeline.scrape = lambda r, cfg: calls.append(r["row_id"]) or {"row_id": r["row_id"], "timing": {}, "contacts": {}}
for name, marker in (("reuse", False), ("noreuse", True)):
    d = root / f"run-{name}"
    (d / "scrape_results").mkdir(parents=True)
    if marker:
        (d / "NO_REUSE").write_text("{}")
    br.run_chunk([{"row_id": "r001"}], {}, 1, d / "scrape_results", data_dir=d)
check("normal batch reuses the earlier result; a rescrape batch scrapes again", calls, ["r001"])

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
