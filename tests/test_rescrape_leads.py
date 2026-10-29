"""Offline tests for the dialer rescrape loop: scripts/rescrape_leads.py, its queue_tick.py hooks and the
batch runner's NO_REUSE switch. Nothing is sent anywhere. Run: python tests/test_rescrape_leads.py"""
import csv
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scraper2"))
import rescrape_leads as rl  # noqa: E402
import queue_tick as qt  # noqa: E402

results = []


def check(name, got, want):
    ok = got == want
    results.append(ok)
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f": got {got!r}, want {want!r}"))


def result(main_phone="", person=None, alternates=(), phone_alt=()):
    return {"contacts": {"phone": {"value": main_phone}, "phone_alt": list(phone_alt),
                         "contact": person or {}, "alternates": list(alternates)}}


# ---- numbers and hashes
check("the same number written three ways is one number",
      len({rl.phone_hash("+356 2123 4567"), rl.phone_hash("00356 21234567"), rl.phone_hash("21234567")}), 1)
check("no hash for an empty or too-short phone", (rl.phone_hash(""), rl.phone_hash("12")), (None, None))

# ---- picking a contact the dialer has not tried
tried = {rl.phone_hash("+356 2123 4567")}
r1 = result("+356 2123 4567", {"name": "Ana", "designation": "Erasmus officer", "phone": "+356 2999 0000",
                                "email": "ana@uni.mt"})
check("a person with a new phone is chosen, with their name and email",
      rl.alternative_contact(r1, tried),
      {"Phone": "+356 2999 0000", "Second Phone": "", "Name": "Ana", "Designation": "Erasmus officer",
       "Email": "ana@uni.mt"})
r2 = result("+356 2123 4567", {"name": "Ana", "phone": "+356 2123 4567"},
            alternates=[{"name": "Ben", "designation": "Head", "phone": "", "email": "ben@uni.mt"}],
            phone_alt=[{"value": "+356 2777 1111"}])
check("only a NEW number counts: the office line from phone_alt, the tried one is skipped",
      rl.alternative_contact(r2, tried)["Phone"], "+356 2777 1111")
check("nothing new -> None (the lead stays closed)",
      rl.alternative_contact(result("21234567", {"name": "Ana", "phone": "+35621234567"}), tried), None)
check("no result at all -> None", rl.alternative_contact(None, tried), None)

# ---- grouping and sorting failed leads
leads = [{"id": "L1", "institute": "University of Malta", "country": "Malta", "phone": "+356 2123 4567",
          "status": "unreachable"},
         {"id": "L2", "institute": "university of  MALTA", "country": "malta", "phone2": "+356 2555 0000",
          "status": "wrong_number"},
         {"id": "L3", "institute": "Escola X", "country": "Spain", "phone": "+34 600 000 000"},
         {"id": "", "institute": "No id", "country": "Spain"}]
groups = rl.group_leads(leads)
check("two leads of one institute are one group, with both lead ids and both numbers tried",
      (len(groups), groups["university of malta|malta"]["lead_ids"], len(groups["university of malta|malta"]["tried"])),
      (2, ["L1", "L2"], 2))
rows, ids, rescrape = rl.classify(groups, None, lookup=lambda i, c: r1 if "Malta" in i else None)
check("known alternative -> sent now, its lead ids marked; unknown -> rescrape",
      ([r["Institute"] for r in rows], ids, list(rescrape)), (["University of Malta"], ["L1", "L2"], ["escola x|spain"]))

# ---- queueing a rescrape batch
root = Path(tempfile.mkdtemp())
now = datetime(2026, 10, 30, 9, 15, tzinfo=timezone.utc)
batch, written = rl.queue_rescrape(rescrape, root, now)
check("batch name carries date and time", batch, "rescrape-20261030-0915")
with open(root / "uploads" / f"{batch}.csv", encoding="utf-8") as f:
    check("the upload CSV has institute and country", list(csv.reader(f)), [["institute", "country"], ["Escola X", "Spain"]])
req = json.loads((root / "_queue_requests" / f"{batch}.json").read_text(encoding="utf-8"))
check("the queue request is a priority rescrape", (req["priority"], req["kind"], req["reader"]), (True, "rescrape", "paid"))
lead_map_text = (root / "rescrape" / f"{batch}.json").read_text(encoding="utf-8")
check("the lead map keeps hashes only: no raw phone in the public repository", "600" in lead_map_text, False)

# ---- the queue: priority first, NO_REUSE, dialer upload on finish
qt._write(root / "queue.json", {"items": [{"batch": "old-batch", "state": "queued"}]})


class Ops(qt.Ops):
    def __init__(self, data_root, fail=False):
        super().__init__(data_root)
        self.fail, self.sent = fail, []

    def send_to_dialer(self, batch, lead_map, rows, results_):
        if self.fail:
            raise RuntimeError("dialer down")
        return rl.send_rescraped(lead_map, rows, results_, self.sent.extend)


ops = Ops(root)
qt.tick(root, "2026-10-30T09:20:00Z", {}, ops)
items = {i["batch"]: i for i in qt.load_queue(root)["items"]}
check("the rescrape jumps ahead of the batch queued before it",
      ([s["batch"] for s in ops.to_start], items[batch]["state"], items["old-batch"]["state"]), ([batch], "running", "queued"))
check("it is marked to scrape again, not reuse the old result", (root / batch / "NO_REUSE").exists(), True)

b = root / batch
(b / "scrape_results").mkdir(parents=True)
qt._write(b / "progress.json", {"status": "done", "total_rows": 1, "done": 1, "pending": 0, "drive_link": "https://d/x"})
(b / "numbers_clean.csv").write_text("row_id,institution_name,country_sheet\nr001,Escola X,Spain\n", encoding="utf-8")
qt._write(b / "scrape_results" / "r001.json", result("+34 911 222 333", {"name": "Marta", "phone": "+34 600 000 000"}))

q = qt.load_queue(root)
it = next(i for i in q["items"] if i["batch"] == batch)
check("dialer down -> not settled, retried next tick, lead map kept",
      (Ops(root, fail=True).finish(q, it, "2026-10-30T12:00:00Z"), it["state"], (root / "rescrape" / f"{batch}.json").exists()),
      (f"{batch}: dialer upload pending", "running", True))
ok_ops = Ops(root)
ok_ops.finish(q, it, "2026-10-30T12:10:00Z")
check("on finish only the new number goes to the dialer (the tried mobile is skipped)",
      [(r["Institute"], r["Phone"]) for r in ok_ops.sent], [("Escola X", "+34 911 222 333")])
check("the item records what was sent, and the lead map is removed",
      (it["state"], it["dialer_sent"], it["no_new_contact"], (root / "rescrape" / f"{batch}.json").exists()),
      ("done", 1, 0, False))

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
