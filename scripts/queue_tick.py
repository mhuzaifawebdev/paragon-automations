"""The batch queue: runs the client's batches one after another, by itself.

Every tick (.github/workflows/queue.yml, started every 10 minutes by apps_script/Scheduler.gs and at once when a
batch finishes):
  1. new uploads from the page (data_hei/_queue_requests/) and new files in the Drive inbox join the queue;
  2. the running batch is checked: finished -> its Excel is in Drive/Results, its full data is archived to
     Drive/_archive, its institutes go into the reuse index, its working files leave GitHub (summary.json stays for
     the dashboard) and its source file moves to Drive/Done; cancelled or broken -> Failed with the reason;
     no progress for STALE_MIN minutes -> started again (at most MAX_RESTARTS times);
  3. when nothing is running, the next queued batch starts (paid Gemini reader when the free allowance runs out),
     unless this month's spending reached the cap (config.yaml scraper.max_queue_usd_per_month).
     A total cap over all months (scraper.max_queue_usd_total) is never exceeded: each batch may only spend what is
     left of it (data_hei/<batch>/BUDGET), and once it is used up the remaining batches run on the free reader only.
Only this script writes data_hei/queue.json, so two jobs never edit it at once.

    python scripts/queue_tick.py --dispatch-file /tmp/dispatch.txt     # one tick; batches to start are listed
    python scripts/queue_tick.py --send-dispatches /tmp/dispatch.txt   # after the push: start them
"""
import argparse
import csv
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data_hei"
sys.path.insert(0, str(ROOT / "scraper2"))
sys.path.insert(0, str(ROOT / "scripts"))
import reuse  # noqa: E402

STALE_MIN = 60
MAX_RESTARTS = 3
KEEP = {"progress.json", "summary.json"}


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def _write(path, obj):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def load_queue(data_root):
    q = _read(Path(data_root) / "queue.json", {})
    q.setdefault("items", [])
    q.setdefault("paused", None)
    return q


def minutes_between(a, b):
    try:
        ta = datetime.fromisoformat(str(a).replace("Z", "+00:00"))
        tb = datetime.fromisoformat(str(b).replace("Z", "+00:00"))
        return (tb - ta).total_seconds() / 60
    except Exception:
        return 0


def take_requests(q, data_root, now):
    """Batches uploaded on the page (web_scraper/api/start-batch.js) join the end of the queue."""
    folder = Path(data_root) / "_queue_requests"
    have = {it["batch"] for it in q["items"]}
    for f in sorted(folder.glob("*.json")) if folder.exists() else []:
        req = _read(f, {})
        b = req.get("batch")
        if b and b not in have:
            q["items"].append({"batch": b, "source": req.get("source") or "upload page", "added": req.get("added") or now,
                               "state": "queued", "reader": req.get("reader") or "paid",
                               "provider": req.get("provider") or "gemini", "chunk_size": req.get("chunk_size") or 25})
            have.add(b)
        f.unlink()


SAFETY_USD = 1.0      # held back from the total cap: calls already under way when a batch reaches its limit


def month_spent(data_root, month):
    return float(_read(Path(data_root) / "_spend.json", {}).get(month, 0))


def total_spent(data_root):
    return round(sum(float(v) for v in _read(Path(data_root) / "_spend.json", {}).values()), 4)


def record_spend(data_root, it, now):
    """Adds what this batch spent to _spend.json, once, whatever way it ended (done, failed or cancelled)."""
    if it.get("spend_recorded"):
        return float(it.get("cost_usd") or 0)
    spent = float(_read(Path(data_root) / it["batch"] / "paid_usage.json", {}).get("spent_usd") or 0)
    spend = _read(Path(data_root) / "_spend.json", {})
    spend[now[:7]] = round(float(spend.get(now[:7], 0)) + spent, 4)
    _write(Path(data_root) / "_spend.json", spend)
    it.update(spend_recorded=True, cost_usd=round(spent, 4))
    return spent


def _source_settled(q, it):
    """True when every part made from the same dropped file is finished, so the file can leave Processing."""
    fid = it.get("file_id")
    return not fid or all(o["state"] in ("done", "failed", "cancelled") for o in q["items"] if o.get("file_id") == fid)


def check_running(q, it, data_root, now, ops):
    bdir = Path(data_root) / it["batch"]
    p = _read(bdir / "progress.json", {})
    st = p.get("status") or ""
    if (bdir / "CANCELLED").exists() or st == "cancelled":
        record_spend(data_root, it, now)
        return settle(q, it, "cancelled", "Cancelled on the upload page.", ops, now)
    if st == "error":
        record_spend(data_root, it, now)
        return settle(q, it, "failed", p.get("error") or "The list could not be imported.", ops, now)
    total, pending = p.get("total_rows") or 0, p.get("pending")
    if st == "done" or (total and pending == 0):
        return ops.finish(q, it, now)
    if st == "waiting":
        it["note"] = p.get("waiting_reason") or "Waiting; resumes by itself."
        return f"{it['batch']}: waiting"
    last = p.get("generated_at") or it.get("last_start") or it.get("started")
    if last and minutes_between(last, now) > STALE_MIN:
        it["restarts"] = it.get("restarts", 0) + 1
        if it["restarts"] > MAX_RESTARTS:
            record_spend(data_root, it, now)
            return settle(q, it, "failed", f"Stopped making progress; restarted {MAX_RESTARTS} times without success.", ops, now)
        it["last_start"] = now
        ops.start(it)
        return f"{it['batch']}: no progress for {int(minutes_between(last, now))} min - restarted ({it['restarts']}/{MAX_RESTARTS})"
    return f"{it['batch']}: running ({p.get('done', 0)}/{total or '?'})"


def settle(q, it, state, reason, ops, now):
    it.update(state=state, finished=now, reason=reason)
    if _source_settled(q, it):
        ops.source_to(it, "Failed" if state != "done" else "Done", reason if state != "done" else "")
    return f"{it['batch']}: {state} - {reason}"


def tick(data_root, now, cfg, ops):
    """One pass. ops: inbox_pickup(q), finish(q, it, now), start(it), source_to(it, folder, note). Returns log lines."""
    q = load_queue(data_root)
    log = []
    take_requests(q, data_root, now)
    try:
        log += ops.inbox_pickup(q, cfg)
    except Exception as e:
        log.append(f"Drive inbox not read: {str(e)[:200]}")
    for it in [i for i in q["items"] if i["state"] == "running"]:
        log.append(check_running(q, it, data_root, now, ops))
    if not any(i["state"] == "running" for i in q["items"]):
        cap = float(cfg.get("max_queue_usd_per_month") or 0)
        spent = month_spent(data_root, now[:7])
        if cap and spent >= cap:
            q["paused"] = (f"This month's spending cap of ${cap:.2f} is reached (${spent:.2f}). The queue continues next "
                           f"month, or when the cap in config.yaml is raised.")
            log.append(q["paused"])
        else:
            q["paused"] = None
            for it in [i for i in q["items"] if i["state"] == "queued"]:
                if (Path(data_root) / it["batch"] / "CANCELLED").exists():
                    log.append(settle(q, it, "cancelled", "Cancelled before it started.", ops, now))
                    continue
                it.update(state="running", started=now, last_start=now, restarts=0)
                total = float(cfg.get("max_queue_usd_total") or 0)
                if total:                               # this batch may spend only what is left of the total
                    spent = total_spent(data_root) + float(cfg.get("spend_correction_usd") or 0)
                    left = round(max(0.0, total - SAFETY_USD - spent), 2)
                    if left < 0.5:
                        left = 0.0
                        it["reader"] = "free"
                        it["note"] = f"The ${total:.0f} total is used up: this batch runs on the free reader only, $0."
                    it["cap_usd"] = left
                ops.start(it)
                log.append(f"{it['batch']}: started" + (f" (may spend up to ${it['cap_usd']:.2f})" if "cap_usd" in it else ""))
                break
    q["updated"] = now
    _write(Path(data_root) / "queue.json", q)
    return log


class Ops:
    """The real side effects: Drive, the reuse index, pruning, and starting batches (after the push)."""

    def __init__(self, data_root, drive=None):
        self.data_root = Path(data_root)
        self.drive = drive
        self.to_start = []

    def inbox_pickup(self, q, cfg=None):
        if not self.drive:
            return []
        import drive_inbox
        log = drive_inbox.pickup(self.drive, self.data_root, q, now_iso())
        # auto-scan: walks the whole Drive folder tree, queues every Google Sheet not yet seen
        if cfg and cfg.get("auto_queue_scan_drive"):
            log += drive_inbox.pickup_scan_drive(self.drive.svc, self.data_root, q, cfg, now_iso())
        # fallback: specific sheet IDs listed in config (kept for backwards compat)
        elif cfg and cfg.get("auto_queue_sheets"):
            def _dl(fid):
                return self.drive.du.download(self.drive.svc,
                                              {"id": fid, "mimeType": "application/vnd.google-apps.spreadsheet"})
            log += drive_inbox.pickup_auto_sheets(_dl, self.data_root, q, cfg, now_iso())
        return log

    def start(self, it):
        bdir = self.data_root / it["batch"]
        reader = {"mode": "fast", "paid": "gemini"} if it.get("reader", "paid") == "paid" else {"mode": "free"}
        _write(bdir / "reader.json", {**reader, "set_at": now_iso()})
        if "cap_usd" in it:
            _write(bdir / "BUDGET", {"cap_usd": it["cap_usd"]})
        self.to_start.append({"batch": it["batch"], "provider": it.get("provider") or "gemini",
                              "chunk_size": it.get("chunk_size") or 25})

    def source_to(self, it, folder, note):
        if not (self.drive and it.get("file_id")):
            return
        f = {"id": it["file_id"], "name": it.get("source")}
        try:
            (self.drive.to_done(f, note) if folder == "Done" else self.drive.to_failed(f, note))
        except Exception as e:
            it["note"] = f"Source file not moved in Drive: {str(e)[:150]}"

    def finish(self, q, it, now):
        import drive_upload
        import build_dashboard_stats as bds
        batch, bdir = it["batch"], self.data_root / it["batch"]
        p = _read(bdir / "progress.json", {})
        link = p.get("drive_link")
        if not link:                                    # the batch's own upload failed: try again from here
            link, err = drive_upload.upload(bdir, batch)
            if not link:
                it["note"] = f"Finished, waiting for the Drive upload: {err or 'Drive is not set up'}"
                return f"{batch}: finished, Drive upload pending"
        results = {f.stem: _read(f, {}) for f in (bdir / "scrape_results").glob("r*.json")}
        if self.drive and results:                      # the full data, kept out of GitHub
            try:
                svc = self.drive.svc
                drive_upload.put_bytes(svc, reuse.archive_bytes(results), f"{batch} data.json.gz",
                                       drive_upload.subfolder(svc, "_archive"), mime="application/gzip")
            except Exception as e:
                it["note"] = f"Finished; archive to Drive failed, retried next tick: {str(e)[:150]}"
                return f"{batch}: archive pending"
        rows = []
        if (bdir / "numbers_clean.csv").exists():
            with open(bdir / "numbers_clean.csv", newline="", encoding="utf-8-sig") as fh:
                rows = list(csv.DictReader(fh))
        index = reuse.load_index(self.data_root)
        added = reuse.add_batch(index, batch, rows, results)
        (self.data_root / reuse.INDEX_NAME).write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")),
                                                       encoding="utf-8")
        spent = record_spend(self.data_root, it, now)
        totals, days, lines = bds.batch_figures(bdir)
        if self.drive and results:                      # only when the data is safely archived: leave GitHub small
            _write(bdir / "summary.json", {"totals": totals, "days": days, "institutes": lines, "spent_usd": spent,
                                           "drive_link": link, "finished": now})
            for child in bdir.iterdir():
                if child.name not in KEEP:
                    shutil.rmtree(child) if child.is_dir() else child.unlink()
            up = self.data_root / "uploads" / f"{batch}.csv"
            if up.exists():
                up.unlink()
        it.update(drive_link=link, cost_usd=round(spent, 4), indexed=added)
        return settle(q, it, "done", f"{totals.get('institutes', 0)} institutes, {totals.get('with_contact', 0)} with a contact",
                      self, now)


def send_dispatches(path):
    """Starts the listed batches (scrape_batch.yml) - run after the queue state is pushed, never before."""
    import resume_due_batches as rdb
    token, repo = os.environ.get("DISPATCH_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    items = _read(path, [])
    if items and not (token and repo):
        raise SystemExit("DISPATCH_TOKEN / GITHUB_REPOSITORY not set: cannot start " + ", ".join(i["batch"] for i in items))
    for i in items:
        status = rdb.dispatch(i["batch"], {"provider": i["provider"], "chunk_size": i["chunk_size"]}, token, repo)
        print(f"started {i['batch']} (HTTP {status})")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--dispatch-file")
    ap.add_argument("--send-dispatches")
    a = ap.parse_args()
    if a.send_dispatches:
        return send_dispatches(a.send_dispatches)
    import yaml
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8")).get("scraper", {}) or {}
    drive = None
    try:
        import drive_inbox
        drive = drive_inbox.DriveInbox()
    except Exception as e:
        print(f"(Drive not available: {str(e)[:200]})")
    ops = Ops(DATA, drive)
    for line in tick(DATA, now_iso(), cfg, ops):
        print(line)
    if a.dispatch_file:
        _write(a.dispatch_file, ops.to_start)


if __name__ == "__main__":
    main()
