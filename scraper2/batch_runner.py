"""Chunked orchestrator for a batch: scrapes CHUNK_SIZE rows at a time (not all at once), and
after every chunk rebuilds the CSVs, writes progress.json (for a dashboard/UI to read), and
optionally republishes to the Sheet. Reuses scraper2/pipeline.py and scraper/run_batch.merge()
exactly as scraper2/run.py does - this is not a second scraper, only a different loop shape
around the same one-institute-at-a-time work.

    PARAGON_DATA=data_hei/teamA python scraper2/batch_runner.py --batch teamA --chunk-size 25 --provider gemini --workers 3
    PARAGON_DATA=data_hei/teamA python scraper2/batch_runner.py --batch teamA --once   # do exactly one chunk, then exit

Safe to re-run or interrupt at any time: every scraped institute is a JSON file that already
exists on disk and is skipped next time (scraper2/pipeline.py / scraper2/run.py's existing
skip-if-exists logic), so the only thing a chunk boundary adds is a checkpoint: progress.json and
the merged CSVs are only ever rewritten BETWEEN chunks, not mid-chunk. A .lock file stops two runs
of the same batch racing each other's CSV rebuild.
"""
import argparse
import contextlib
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scraper2"))
sys.path.insert(0, str(ROOT / "scraper"))
import discover  # noqa: E402
import import_batch  # noqa: E402
import pipeline  # noqa: E402
import run_batch  # noqa: E402
from run import load_cfg, load_env  # noqa: E402


class AlreadyRunning(Exception):
    pass


@contextlib.contextmanager
def batch_lock(data_dir):
    """A plain file-existence lock (same idea as deploy/vps/run_pending.sh) - good enough for
    "don't let two processes rebuild this batch's CSVs at once", not a distributed lock."""
    lock = data_dir / ".lock"
    if lock.exists():
        raise AlreadyRunning(f"{lock} exists - another run of this batch looks to be in progress. "
                             f"If that's wrong (a crashed run left it behind), delete the file and retry.")
    lock.write_text(str(time.time()), encoding="utf-8")
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def _progress_path(data_dir):
    return data_dir / "progress.json"


KEEP_KEYS = ("drive_link", "drive_error")      # written by drive_upload.py between chunks; a rewrite must not lose them


def write_progress(data_dir, batch, status, queue, done_ids, chunk_no, total_chunks, timings, costs, started_at, extra=None):
    enriched_path = data_dir / "institutions_enriched.csv"
    enriched = run_batch.read_csv_safe(enriched_path) if hasattr(run_batch, "read_csv_safe") else _read_csv(enriched_path)
    by_id = {r["row_id"]: r for r in enriched}
    confidence_counts = {"high": 0, "medium": 0, "low": 0, "none": 0}
    for rid in done_ids:
        e = by_id.get(rid, {})
        ok = e.get("contact_verified") == "yes"
        role_ok = bool(e.get("person_scraped"))
        if not ok and not role_ok:
            confidence_counts["none"] += 1
        elif ok and e.get("email_scraped"):
            confidence_counts["high"] += 1
        elif ok:
            confidence_counts["medium"] += 1
        else:
            confidence_counts["low"] += 1

    elapsed = time.time() - started_at
    avg = (sum(timings) / len(timings)) if timings else None
    remaining = len(queue) - len(done_ids)
    eta_seconds = (avg * remaining / max(1, STATS["workers"])) if avg else None    # institutes run in parallel
    total_partners = sum(int(r.get("partners_found") or 0) for r in enriched)
    total_campuses = sum(int(r.get("campuses_found") or 0) for r in enriched)

    data = {
        "batch": batch, "status": status, "generated_at": datetime.now(timezone.utc).isoformat(),
        "chunk": chunk_no, "total_chunks": total_chunks,
        "total_rows": len(queue), "done": len(done_ids), "pending": remaining,
        "elapsed_seconds": round(elapsed), "eta_seconds": round(eta_seconds) if eta_seconds else None,
        "cost_usd_so_far": round(sum(costs) + discover.spent_usd(), 4),
        "paid_search_usd": discover.spent_usd(), "paid_search_limit_usd": discover.BUDGET["limit"],
        "paid_search_limit_reached": not discover.budget_left(),
        "paid_searches": discover.SEARCH_USAGE["searches"], "haiku_fallback_calls": discover.SEARCH_USAGE["reader_calls"],
        "cost_usd_projected_total": round(sum(costs) / max(len(done_ids), 1) * len(queue), 2) if done_ids else None,
        "ai_calls": STATS["ai_calls"], "reader_mode": STATS["mode"], "fast_limit_usd": STATS["fast_limit"],
        "provider": STATS["provider"], "chunk_size": STATS["chunk_size"],
        "confidence": confidence_counts, "partners_found": total_partners, "campuses_found": total_campuses,
        "recent": [r["row_id"] for r in enriched if r["row_id"] in done_ids][-20:],
    }
    try:
        old = json.loads(_progress_path(data_dir).read_text(encoding="utf-8"))
        data.update({k: old[k] for k in KEEP_KEYS if k in old})
    except Exception:
        pass
    data.update(extra or {})
    _progress_path(data_dir).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data


STATS = {"ai_calls": 0, "workers": 1, "mode": "free", "fast_limit": None, "provider": None, "chunk_size": None}
MAX_ATTEMPTS = 3             # a row that fails this often is saved as unreadable, so a batch can always finish
PAUSE = {"until": None, "daily": False}     # set when the free reader's allowance is used up and nothing paid may stand in
_attempt_lock = threading.Lock()


def reader_mode(data_dir):
    """'free' (default: Gemini only) or 'fast' (Claude Haiku reads what Gemini refuses). Set from the upload page,
    which writes reader.json into the batch folder."""
    try:
        mode = json.loads((Path(data_dir) / "reader.json").read_text(encoding="utf-8")).get("mode")
    except Exception:
        mode = None
    return "fast" if mode == "fast" else "free"


def apply_mode(data_dir, cfg):
    """Called before every institute, so a switch made on the page takes effect within about a minute (the
    workflow pulls the repository every 60 seconds while a chunk runs)."""
    mode = reader_mode(data_dir)
    fast_cap = float(cfg.get("max_fast_usd_per_batch", 10.0))
    discover.BUDGET["limit"] = float(cfg.get("max_paid_usd_per_batch", 3.0)) + (fast_cap if mode == "fast" else 0.0)
    pipeline.llm.FALLBACK["enabled"] = mode == "fast"
    pipeline.llm.SIMULATE_EXHAUSTED = (Path(data_dir) / "SIMULATE_EXHAUSTED").exists()     # zero-cost test switch
    if mode == "fast" and STATS["mode"] != "fast":
        PAUSE.update(until=None, daily=False)       # a paused batch switched to fast carries straight on
    STATS.update(mode=mode, fast_limit=fast_cap)
    return mode


def count_failure(results_dir, row, reason):
    """Remember that this row failed. On the MAX_ATTEMPTS-th failure return an 'unreadable' result to save in its
    place; before that return None (the row is left for a later job). Allowance waits are never counted here."""
    path = Path(results_dir) / "attempts.json"
    with _attempt_lock:
        try:
            seen = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            seen = {}
        seen[row["row_id"]] = n = seen.get(row["row_id"], 0) + 1
        path.write_text(json.dumps(seen), encoding="utf-8")
    if n < MAX_ATTEMPTS:
        return None
    return pipeline.unresolved(row, f"Could not be read after {n} attempts ({reason[:160]})", {"total_seconds": 0})


def _site_host(row):
    """The website the sheet gives for this row (a URL column, else the email's domain), or ''."""
    for u in (row.get("seed_urls") or "").split():
        host = urlparse(u).netloc.lower()
        if host:
            return host[4:] if host.startswith("www.") else host
    email = (row.get("email") or "").strip().lower()
    return email.split("@", 1)[1].split()[0].strip(";,") if "@" in email else ""


def mark_repeats(rows, results_dir):
    """An institute listed more than once in the file is scraped once: later rows get the `.duplicate` marker
    that run_batch.merge() already understands (it gives them the first row's result and fills 'Duplicate Of').
    Rows are the same institute only when name AND country AND website all match - two schools that merely share
    a name, or two institutes that share a website, are never merged. Returns the row_ids marked now or earlier."""
    first, repeats = {}, set()
    for r in rows:
        name = import_batch._norm(r.get("institution_name"))
        if not name:
            continue
        key = (name, import_batch._norm(r.get("country_sheet")), _site_host(r))
        rid, marker = r["row_id"], results_dir / f"{r['row_id']}.duplicate"
        if key not in first:
            first[key] = rid
        elif marker.exists():
            repeats.add(rid)
        elif not (results_dir / f"{rid}.json").exists():        # never undo a result that was already scraped
            marker.write_text(first[key], encoding="utf-8")
            repeats.add(rid)
    return repeats


def merge_gap(last_merge_seconds, floor):
    """Seconds to leave between full merges: never less than `floor`, and at least 4x what the last one took."""
    return max(floor, 4 * last_merge_seconds)


def _read_csv(path):
    import csv
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def run_chunk(rows, cfg, workers, results_dir, on_row_done=None, data_dir=None):
    """Scrapes one chunk. Same per-row logic as scraper2/run.py's work(), reused not duplicated.
    on_row_done(timings, costs, done_ids), if given, fires after each row (not just once at the end
    of the whole chunk) so a caller can checkpoint live done/pending counts while a chunk is still
    running - a chunk of real websites + real LLM calls can take minutes, and showing nothing until
    it fully finishes looks indistinguishable from being stuck."""
    lock = threading.Lock()
    timings, costs, done_ids = [], [], []

    def save(rid, res):
        with lock:
            (results_dir / f"{rid}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
            timings.append(res["timing"].get("total_seconds", 0))
            costs.append(res["timing"].get("cost_usd_estimate", 0))
            STATS["ai_calls"] += res["timing"].get("ai_calls", 0)
            done_ids.append(rid)
            if on_row_done:
                on_row_done(timings, costs, done_ids)

    def work(r):
        rid = r["row_id"]
        if (results_dir / f"{rid}.json").exists():
            with lock:
                done_ids.append(rid)
                if on_row_done:
                    on_row_done(timings, costs, done_ids)
            return
        if data_dir is not None:
            apply_mode(data_dir, cfg)
        if PAUSE["until"]:
            return                                  # allowance used up: the rest of the chunk waits for the resume
        try:
            res = pipeline.scrape(r, cfg)
            if res.get("quota"):
                with lock:
                    first = not PAUSE["until"]
                    PAUSE.update(until=max(PAUSE["until"] or 0, res["resume_at"]), daily=bool(res.get("daily")))
                if first:
                    print(f"{rid}: free AI allowance used up - pausing the batch", flush=True)
                return
            if res.get("retry"):
                final = count_failure(results_dir, r, res["reason"])
                if final is None:
                    print(f"{rid}: NOT SAVED, {res['reason']} - will retry next chunk", flush=True)
                    return
                res = final
                print(f"{rid}: saved as unreadable after {MAX_ATTEMPTS} attempts", flush=True)
            save(rid, res)
            print(f"{rid}: done ({res['timing'].get('total_seconds')}s)", flush=True)
        except Exception as e:
            print(f"{rid}: FAILED: {type(e).__name__}: {e}", flush=True)
            with open(results_dir / "errors.log", "a", encoding="utf-8") as log:
                log.write(f"{time.strftime('%F %T')} batch_runner {rid}: {e}\n")
            final = count_failure(results_dir, r, f"{type(e).__name__}: {e}")
            if final is not None:
                save(rid, final)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        list(pool.map(work, rows))
    return timings, costs, done_ids


def waiting_state(pending, progressed, prior, now=None):
    """What a job reports when it stops with rows still to do: (status, extra fields for progress.json)."""
    now = time.time() if now is None else now
    iso = lambda t: datetime.fromtimestamp(t, timezone.utc).isoformat()     # noqa: E731
    if pending == 0:
        return "done", {}
    if PAUSE["until"]:
        if PAUSE["daily"]:
            # If the batch was already waiting for the daily allowance and this job still got nothing done, the
            # expected return time was wrong: look again in 30 minutes rather than waiting another whole day.
            again = prior.get("waiting_kind") == "daily" and not progressed
            return "waiting", {"waiting_kind": "daily", "resume_at": iso(now + 1800 if again else PAUSE["until"]),
                               "waiting_reason": "Today's free AI allowance is used up."}
        return "waiting", {"waiting_kind": "busy", "resume_at": iso(max(PAUSE["until"], now + 900)),
                           "waiting_reason": "The free AI reader is refusing requests for now."}
    if not progressed:
        return "waiting", {"waiting_kind": "retry", "resume_at": iso(now + 900),
                           "waiting_reason": "Nothing could be read in the last run; trying again shortly."}
    return "running", {}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    load_env()
    cfg = load_cfg()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--batch", required=True)
    ap.add_argument("--chunk-size", type=int, default=25)
    ap.add_argument("--workers", type=int, default=cfg["workers"])
    ap.add_argument("--provider", default=cfg["provider"], choices=["claude_api", "claude_cli", "gemini", "rules"])
    ap.add_argument("--model", default=cfg["model"])
    ap.add_argument("--alternatives", action="store_true")
    ap.add_argument("--publish-every", type=int, default=0,
                    help="republish to the Sheet every N chunks (0 = never; use scraper/build_hei_sheet.py "
                         "separately if you do want a Sheet, since a generic team CSV has no client sheet "
                         "to publish into by default)")
    ap.add_argument("--once", action="store_true", help="run exactly one chunk then exit (for GitHub Actions: one job = one chunk)")
    a = ap.parse_args()
    cfg.update(provider=a.provider, model=a.model, alternatives=a.alternatives)
    pipeline.LLM_SLOTS = threading.Semaphore(int(cfg["llm_concurrency"]))

    data_dir = Path(os.environ.get("PARAGON_DATA") or (ROOT / "data_hei" / a.batch))
    results_dir = data_dir / "scrape_results"
    results_dir.mkdir(parents=True, exist_ok=True)
    # Paid web searches are capped per BATCH, not per job: the total is kept in the batch folder so every chunk
    # (a separate GitHub job) continues from what the earlier ones already spent.
    discover.set_budget(float(cfg.get("max_paid_usd_per_batch", 3.0)), data_dir / "paid_usage.json")
    STATS.update(workers=a.workers, provider=a.provider, chunk_size=a.chunk_size)
    apply_mode(data_dir, cfg)          # free mode unless the upload page switched this batch to fast

    try:
        with batch_lock(data_dir):
            _run(a, data_dir, results_dir, cfg)
    except AlreadyRunning as e:
        raise SystemExit(str(e))


def _run(a, data_dir, results_dir, cfg):
    if True:
        rows = run_batch.read_numbers()
        repeats = mark_repeats(rows, results_dir)
        if repeats:
            print(f"{len(repeats)} row(s) repeat an institute already in this file - scraped once, result shared", flush=True)
        already_done = {r["row_id"] for r in rows if (results_dir / f"{r['row_id']}.json").exists()} | repeats
        prior = json.loads(_progress_path(data_dir).read_text(encoding="utf-8")) if _progress_path(data_dir).exists() else {}
        STATS["ai_calls"] = prior.get("ai_calls", 0)
        PAUSE.update(until=None, daily=False)
        started_at = time.time() - prior.get("elapsed_seconds", 0)
        all_timings, all_costs = [], []
        total_chunks = max(1, -(-len(rows) // a.chunk_size))

        chunk_no = prior.get("chunk", 0)
        todo = [r for r in rows if r["row_id"] not in already_done]
        chunks_run = 0
        # merge() re-verifies and rewrites all 5 output CSVs from every row done SO FAR, not just the
        # new one - cheap early in a batch, but O(total done) per call, so calling it on every single
        # row would make a whole batch roughly O(n^2) once it's a few hundred rows deep, and would
        # serialize all worker threads against each other's merge (they share the same lock) right
        # when there's the most concurrency to lose. The workflow only pushes to GitHub every 20s
        # anyway (.github/workflows/scrape_batch.yml), so there's no value in recomputing more often
        # than that - throttling here keeps the "live" feel while keeping the cost bounded by wall
        # time, not row count. The unconditional merge() at chunk end (below) guarantees correctness
        # regardless of what this throttle skipped.
        CHECKPOINT_MIN_INTERVAL = 8
        checkpoint_state = {"last": 0.0, "merged_at": 0.0, "merge_took": 0.0}

        while todo:
            chunk_no += 1
            chunks_run += 1
            chunk = todo[:a.chunk_size]
            todo = todo[a.chunk_size:]
            print(f"--- chunk {chunk_no}/{total_chunks} ({len(chunk)} rows) ---", flush=True)

            def _live_checkpoint(timings, costs, done_ids, _base=set(already_done)):
                # Fires after every row (from run_chunk's lock), but only does real work at most
                # once every CHECKPOINT_MIN_INTERVAL seconds - see note above the loop.
                now = time.time()
                if now - checkpoint_state["last"] < CHECKPOINT_MIN_INTERVAL:
                    return
                checkpoint_state["last"] = now
                # The full merge costs more the bigger the batch gets, and every worker waits while it runs
                # (it is called under run_chunk's lock). Spacing it at 4x its own duration keeps it to a fifth
                # of the time at any batch size; the progress counter below is still refreshed every 8 seconds.
                if now - checkpoint_state["merged_at"] >= merge_gap(checkpoint_state["merge_took"], CHECKPOINT_MIN_INTERVAL):
                    run_batch.merge(rows)
                    checkpoint_state["merge_took"] = time.time() - now
                    checkpoint_state["merged_at"] = time.time()
                write_progress(data_dir, a.batch, "running", rows, _base | set(done_ids), chunk_no,
                               total_chunks, all_timings + timings, all_costs + costs, started_at)

            before = set(already_done)
            timings, costs, done_ids = run_chunk(chunk, cfg, a.workers, results_dir, on_row_done=_live_checkpoint,
                                                 data_dir=data_dir)
            all_timings += timings
            all_costs += costs
            already_done |= set(done_ids)
            todo = [r for r in rows if r["row_id"] not in already_done]      # includes rows this chunk could not finish

            run_batch.merge(rows)
            status, extra = waiting_state(len(todo), bool(already_done - before), prior)
            total_chunks = max(total_chunks, chunk_no)
            write_progress(data_dir, a.batch, status, rows, already_done, chunk_no, total_chunks,
                           all_timings, all_costs, started_at, extra)
            if status == "waiting":
                print(f"Batch {a.batch}: waiting - {extra['waiting_reason']} Resumes by itself at {extra['resume_at']}.", flush=True)

            if a.publish_every and chunk_no % a.publish_every == 0:
                import subprocess
                os.environ["PARAGON_DATA"] = str(data_dir)
                subprocess.run([sys.executable, str(ROOT / "scraper" / "build_hei_sheet.py")], cwd=ROOT)

            try:
                dash = ROOT / "scripts" / "build_scraper_dashboard.py"
                if dash.exists():
                    sys.path.insert(0, str(ROOT / "scripts"))
                    import build_scraper_dashboard as bd
                    bd.OUT.write_text(bd.HTML.replace("__DATA__", json.dumps(bd.build(data_dir))), encoding="utf-8")
            except Exception as e:
                print(f"(scraper dashboard refresh failed, non-fatal: {e})", file=sys.stderr)

            if a.once or status == "waiting":
                break

        if not todo and chunks_run:
            print(f"\nBatch {a.batch}: all {len(rows)} rows done.")
        elif todo:
            print(f"\nBatch {a.batch}: chunk {chunk_no} done, {len(todo)} rows still pending "
                  f"(run again, or with --once for one more chunk).")
        else:
            print(f"Batch {a.batch}: nothing to do, all {len(rows)} rows already scraped.")


if __name__ == "__main__":
    main()
