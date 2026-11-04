"""Free boost of contacts and emails for every finished institute (no AI, no paid search): each institute with a
website but no email or no contact has its own home, contact, staff and legal pages (Impressum, Mentions legales ...)
read again, with today's rules:

  - email: pipeline.pick_generic_email, which now keeps any address on the institute's own domain when no shared
    inbox is shown
  - contact: the rules-only reader (rank.extract), checked by pipeline.resolve_contacts exactly as a model's answer
    is, so a business owner, founder or managing director named on the site counts

Batches still on disk are updated in place. Batches whose working files were archived to Google Drive
(scripts/queue_tick.py) are read from the archive, updated, archived again, and their summary.json (the
dashboard's counts) is rebuilt. A result is only ever added to, never made worse.

    python scripts/boost_free.py --limit 20                 # try 20 institutes on disk, print what was found
    python scripts/boost_free.py --archived --workers 16    # also every archived batch (needs the Drive robot)
"""
import argparse
import gzip
import json
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scraper2"))
sys.path.insert(0, str(ROOT / "scraper"))
sys.path.insert(0, str(ROOT / "scripts"))
import crawl  # noqa: E402
import evidence  # noqa: E402
import pipeline  # noqa: E402
import rank  # noqa: E402

DATA = ROOT / "data_hei"
ROUND = 2          # raised when these rules change, so institutes boosted earlier are read again (2: other-domain emails)


def wants(res):
    """'email', 'contact', both, or nothing: what this result is missing that a free re-read could add."""
    c = (res or {}).get("contacts") or {}
    if not c.get("official_website") or c.get("boost_round", 0) >= ROUND:
        return set()
    out = set()
    if not c.get("contact"):
        out.add("contact")
    if not c.get("generic_email") and not (c.get("contact") or {}).get("email"):
        out.add("email")
    return out


def boost(res, max_pages=4):
    """Reads the site again and adds what it finds. Returns (email found, contact found)."""
    c = res["contacts"]
    need = wants(res)
    got_email = got_contact = False
    try:
        cr = crawl.crawl(c["official_website"], max_pages=max_pages, workers=2)
        pages = list(cr["pages"])
        if pages:
            pages += pipeline.free_staff_pages(cr, limit=6, workers=2)
    except Exception as e:                       # an unreachable site is recorded as checked, never crashes the run
        print(f"  {c.get('row_id')}: could not read the site ({str(e)[:80]})", flush=True)
        pages, cr = [], {"site": c["official_website"]}
    if pages and "contact" in need:
        pack = evidence.build_pack(pages)
        found = pipeline.resolve_contacts(rank.extract(pack, {"name": c.get("institution_name", "")}), pack)
        # without a model, a capitalised phrase next to a role word can pass as a name ('Ohne Einschrankungen ...
        # Inhaberin'): only a person whose own address carries their name is taken
        found = [p for p in found if p["email"] and pipeline._matches_name(p["email"], pipeline._name_toks(p["name"]))]
        if found:
            p = found[0]
            c["contact"] = {k: v for k, v in p.items() if k not in ("role_class", "why_chosen", "confidence")}
            c.update(rung=p["why_chosen"], confidence=p["confidence"], needs_human_check=not p["email"])
            got_contact = True
    if pages and not c.get("generic_email") and not (c.get("contact") or {}).get("email"):
        email, url = pipeline.pick_generic_email(pages, cr["site"], c.get("institution_name", ""))
        if email:
            c.update(generic_email=email, generic_email_url=url)
            got_email = True
    c["boost_round"] = ROUND
    c["boost_checked"] = time.strftime("%Y-%m-%d", time.gmtime())
    return got_email, got_contact


def run_all(results, workers, label):
    """Boosts every result in {row_id: result} that needs it. Returns (checked, emails, contacts)."""
    todo = [rid for rid, r in results.items() if wants(r)]
    emails = contacts = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for rid, (e, c) in zip(todo, pool.map(lambda rid: boost(results[rid]), todo)):
            emails += e
            contacts += c
    print(f"{label}: {len(todo)} checked, +{emails} email(s), +{contacts} contact(s)", flush=True)
    return len(todo), emails, contacts


def finished(bdir):
    """A batch the scraper is still working on is left alone: its runner rewrites these same files."""
    try:
        return json.loads((bdir / "progress.json").read_text(encoding="utf-8")).get("status") == "done"
    except Exception:
        return False


def local_batches(data=DATA):
    return sorted(p for p in Path(data).iterdir() if p.is_dir() and (p / "scrape_results").exists() and finished(p))


def archived_batches(data=DATA):
    return sorted(p for p in Path(data).iterdir()
                  if p.is_dir() and (p / "summary.json").exists() and not (p / "scrape_results").exists())


def boost_local(bdir, workers, limit=0):
    files = sorted((bdir / "scrape_results").glob("r*.json"))
    results = {}
    for f in files:
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if wants(r):
            results[f.stem] = r
    if limit:
        results = dict(list(results.items())[:limit])
    if not results:
        return 0, 0, 0
    out = run_all(results, workers, bdir.name)
    for rid, r in results.items():
        (bdir / "scrape_results" / f"{rid}.json").write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def boost_archived(bdir, workers, svc, folder):
    import build_dashboard_stats as bds
    import drive_upload
    name = f"{bdir.name} data.json.gz"
    raw = drive_upload.get_bytes(svc, name, folder)
    if not raw:
        print(f"{bdir.name}: no archive on Drive, skipped", flush=True)
        return 0, 0, 0
    results = json.loads(gzip.decompress(raw).decode("utf-8"))
    sp = bdir / "summary.json"
    s = json.loads(sp.read_text(encoding="utf-8"))
    out = run_all(results, workers, bdir.name)
    if out[0]:
        drive_upload.put_bytes(svc, gzip.compress(json.dumps(results, ensure_ascii=False).encode("utf-8")), name, folder,
                               mime="application/gzip")
    elif s.get("boosted", 0) >= ROUND:
        return out
    # rebuilt also when nothing was left to check: an earlier run may have archived the boosted results and then
    # stopped (time limit) before its summary.json was committed
    with tempfile.TemporaryDirectory() as tmp:          # the dashboard's counts, rebuilt from the updated results
        t = Path(tmp) / bdir.name
        (t / "scrape_results").mkdir(parents=True)
        for rid, r in results.items():
            (t / "scrape_results" / f"{rid}.json").write_text(json.dumps(r, ensure_ascii=False), encoding="utf-8")
        totals, days, lines = bds.batch_figures(t)
    s.update(totals=totals, days=days, institutes=lines, boosted=ROUND)
    sp.write_text(json.dumps(s, ensure_ascii=False), encoding="utf-8")
    return out


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--batch", help="only this batch")
    ap.add_argument("--archived", action="store_true", help="also the batches archived to Google Drive")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--limit", type=int, default=0, help="at most this many institutes per batch on disk (a trial)")
    ap.add_argument("--commit-every", type=int, default=0, help="git commit + push after every N archived batches")
    a = ap.parse_args()
    from run import load_env
    load_env()
    total = [0, 0, 0]

    def add(out):
        for i, v in enumerate(out):
            total[i] += v

    for b in local_batches():
        if not a.batch or b.name == a.batch:
            add(boost_local(b, a.workers, a.limit))
    if a.archived:
        import drive_upload
        svc = drive_upload.robot_service()
        folder = drive_upload.subfolder(svc, "_archive")
        done = 0
        for b in archived_batches():
            if a.batch and b.name != a.batch:
                continue
            try:
                add(boost_archived(b, a.workers, svc, folder))
            except Exception as e:                      # one bad archive never stops the rest
                print(f"{b.name}: skipped ({str(e)[:150]})", flush=True)
                continue
            done += 1
            if a.commit_every and done % a.commit_every == 0:
                commit(f"free boost: contacts and emails added ({done} archived batches) [automated]")
    print(f"TOTAL: {total[0]} institutes checked, +{total[1]} email(s), +{total[2]} contact(s)", flush=True)
    subprocess.run([sys.executable, str(ROOT / "scripts" / "build_dashboard_stats.py")], cwd=ROOT, check=False)


def commit(msg):
    subprocess.run(["git", "add", "data_hei"], cwd=ROOT, check=False)
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode:
        subprocess.run(["git", "commit", "-q", "-m", msg], cwd=ROOT, check=False)
        subprocess.run(["git", "pull", "--rebase", "--autostash", "-q"], cwd=ROOT, check=False)
        subprocess.run(["git", "push", "-q"], cwd=ROOT, check=False)


if __name__ == "__main__":
    main()
