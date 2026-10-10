"""Starts paused scraper batches again when their time has come. Run by .github/workflows/resume_batches.yml
every 15 minutes; needs nothing from a person.

A batch pauses itself (status "waiting" in data_hei/<batch>/progress.json, written by scraper2/batch_runner.py)
when the free AI allowance is used up. This script fires the same "scrape-start" event the upload page uses for
every waiting batch whose resume_at has passed. If the allowance is still not back, the batch simply pauses again.

    DISPATCH_TOKEN=... GITHUB_REPOSITORY=owner/repo python scripts/resume_due_batches.py
"""
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RETRY_MINUTES = 30      # if a started job never gets going, the batch is offered again after this long


def due(progress, now, cancelled=False):
    """True when this batch is paused and should be started now. A cancelled batch is started straight away:
    the scrape workflow's first step is what marks it cancelled, and it then stops for good."""
    if progress.get("status") != "waiting":
        return False
    if cancelled:
        return True
    try:
        return datetime.fromisoformat(progress["resume_at"]).timestamp() <= now
    except Exception:
        return True         # no readable time: better to try (and pause again) than to wait for ever


def dispatch(batch, progress, token, repo):
    body = {"event_type": "scrape-start",
            "client_payload": {"batch": batch, "provider": progress.get("provider") or "gemini",
                               "chunk_size": str(progress.get("chunk_size") or 25)}}
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/dispatches", data=json.dumps(body).encode(),
                                 method="POST", headers={"Authorization": f"Bearer {token}",
                                                         "Accept": "application/vnd.github+json",
                                                         "User-Agent": "paragon-scraper-resume"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status


def run(data_root, now, start):
    """Starts every due batch with start(batch, progress). Returns the names started. The batch's resume_at is
    pushed RETRY_MINUTES ahead so the next 15-minute run does not start the same batch a second time."""
    started = []
    for path in sorted(Path(data_root).glob("*/progress.json")):
        try:
            progress = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not due(progress, now, (path.parent / "CANCELLED").exists()):
            continue
        batch = path.parent.name
        try:
            start(batch, progress)
        except Exception as e:
            print(f"{batch}: could not be started: {e}", flush=True)
            continue
        progress["resume_at"] = (datetime.fromtimestamp(now, timezone.utc) + timedelta(minutes=RETRY_MINUTES)).isoformat()
        path.write_text(json.dumps(progress, ensure_ascii=False, indent=1), encoding="utf-8")
        started.append(batch)
        print(f"{batch}: started again", flush=True)
    return started


def main():
    token, repo = os.environ.get("DISPATCH_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        sys.exit("DISPATCH_TOKEN and GITHUB_REPOSITORY must be set.")
    started = run(ROOT / "data_hei", time.time(), lambda batch, progress: dispatch(batch, progress, token, repo))
    print(f"{len(started)} batch(es) started." if started else "No paused batch is due.")


if __name__ == "__main__":
    main()
