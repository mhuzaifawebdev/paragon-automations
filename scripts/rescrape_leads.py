"""Rescrape loop: scraper <-> Nazish's auto-dialer.

Every hour in calling hours (apps_script/Scheduler.gs, the "Rescrape failed leads now" button, or GitHub's cron as
a backup) this:
  1. Fetches the leads the dialer closed as unreachable (5x no answer) or wrong_number (/api/failed-leads).
  2. For each institute, looks in the scraper's reuse index for a phone not tried yet:
     - found  -> sent straight back to the dialer as a "Rescrape-<date> <HH>h" batch;
     - not found -> queued as a priority rescrape batch (data_hei/uploads + data_hei/_queue_requests). It runs next,
       scrapes again without reusing the old result, and scripts/queue_tick.py sends what it finds to the dialer.
  3. Marks the processed leads rescrape_queued in the dialer, so they are never fetched twice.

Numbers already tried are kept only as hashes (data_hei/rescrape/<batch>.json): the repository is public.

    python scripts/rescrape_leads.py [--dry-run]

Env: DIALER_URL, DIALER_ADMIN_PASS, RESCRAPE_SECRET, DISPATCH_TOKEN, GITHUB_REPOSITORY (set by GitHub Actions).
"""
import argparse
import csv
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data_hei"
RESCRAPE_DIR = DATA / "rescrape"
sys.path.insert(0, str(ROOT / "scraper2"))
import reuse  # noqa: E402

DIALER_QUEUE = "SALES"
MAX_BATCH_ROWS = 1000
UPLOAD_COLS = ["Institute", "Phone", "Second Phone", "Name", "Designation", "Email", "Country", "Notes"]


# ---------------------------------------------------------------------------------------------------------------
# Matching and contacts
# ---------------------------------------------------------------------------------------------------------------

def _norm(s):
    s = unicodedata.normalize("NFKD", str(s or "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


def inst_key(institute, country):
    return f"{_norm(institute)}|{_norm(country)}"


def _reuse_key(institute, country):
    n = _norm(institute)
    return f"n:{n}|{_norm(country)}" if len(n) >= 4 else None


def _digits(phone):
    # last 8 digits (Malta's whole national number): +356 2123 4567, 0035621234567 and 21234567 compare equal
    return re.sub(r"\D", "", str(phone or ""))[-8:]


def _hash(v):
    return hashlib.sha256(v.encode("utf-8")).hexdigest()[:16]


def phone_hash(phone):
    d = _digits(phone)
    return _hash("p:" + d) if len(d) >= 6 else None


def email_hash(email):
    e = str(email or "").strip().lower()
    return _hash("e:" + e) if "@" in e else None


def tried_hashes(lead):
    return {h for h in (phone_hash(lead.get("phone")), phone_hash(lead.get("phone2")), phone_hash(lead.get("phone3")),
                        email_hash(lead.get("email"))) if h}


def _value(x):
    return str((x.get("value") if isinstance(x, dict) else x) or "").strip()


def _people(result):
    """Every person and number a scrape result holds, best first: dicts with name, designation, phone, email."""
    c = (result or {}).get("contacts") or {}
    out = []
    for p in [c.get("contact") or {}] + list(c.get("alternates") or []):
        if isinstance(p, dict):
            out.append({"name": p.get("name") or "", "designation": p.get("designation") or "",
                        "phone": _value(p.get("phone")), "email": p.get("email") or p.get("office_email") or ""})
    for ph in [c.get("phone")] + list(c.get("phone_alt") or []):
        out.append({"name": "", "designation": "", "phone": _value(ph), "email": ""})
    return out


def alternative_contact(result, tried):
    """A contact to call that the dialer has not tried yet, or None. Only a NEW phone counts: the same number with
    another name on it would ring the same unanswered line."""
    phones, seen = [], set()
    for p in _people(result):
        h = phone_hash(p["phone"])
        if h and h not in tried and h not in seen:
            seen.add(h)
            phones.append(p)
    if not phones:
        return None
    best = next((p for p in phones if p["name"]), phones[0])
    email = next((p["email"] for p in _people(result) if p["email"] and email_hash(p["email"]) not in tried), "")
    second = next((p["phone"] for p in phones if p is not best), "")
    return {"Phone": best["phone"], "Second Phone": second, "Name": best["name"],
            "Designation": best["designation"], "Email": best["email"] or email}


def upload_row(institute, country, alt, note):
    return {"Institute": institute, "Country": country, "Notes": note, **alt}


# ---------------------------------------------------------------------------------------------------------------
# Dialer API
# ---------------------------------------------------------------------------------------------------------------

def fetch_failed_leads(dialer_url, secret):
    r = requests.get(dialer_url.rstrip("/") + "/api/failed-leads", params={"token": secret}, timeout=120)
    r.raise_for_status()
    return list(csv.DictReader(io.StringIO(r.text))) if r.text.strip() else []


def mark_done(dialer_url, secret, lead_ids):
    if not lead_ids:
        return 0
    r = requests.post(dialer_url.rstrip("/") + "/api/failed-leads", json={"token": secret, "ids": lead_ids}, timeout=120)
    r.raise_for_status()
    return r.json().get("marked", 0)


def _proxy(dialer_url, payload, token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = requests.post(dialer_url.rstrip("/") + "/api/proxy", json=payload, headers=headers, timeout=120)
    r.raise_for_status()
    return r.json()


def upload_to_dialer(rows, dialer_url, password, name):
    """Uploads rows as one dialer batch (split into parts of MAX_BATCH_ROWS). Returns the batch names created."""
    if not rows:
        return []
    token = _proxy(dialer_url, {"action": "admin_login", "password": password})["token"]
    parts = [rows[i:i + MAX_BATCH_ROWS] for i in range(0, len(rows), MAX_BATCH_ROWS)]
    names = []
    for n, part in enumerate(parts, 1):
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=UPLOAD_COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(part)
        part_name = name + (f" p{n}" if len(parts) > 1 else "")
        check = _proxy(dialer_url, {"action": "batch_check", "csv": buf.getvalue()}, token)
        _proxy(dialer_url, {**check, "action": "batch_create", "name": part_name, "queue": DIALER_QUEUE,
                            "csv": buf.getvalue()}, token)
        names.append(part_name)
        time.sleep(1)
    return names


def dialer_env():
    url, password = os.environ.get("DIALER_URL", "").rstrip("/"), os.environ.get("DIALER_ADMIN_PASS", "")
    if not (url and password):
        raise RuntimeError("DIALER_URL / DIALER_ADMIN_PASS are not set")
    return url, password


# ---------------------------------------------------------------------------------------------------------------
# Sorting the failed leads
# ---------------------------------------------------------------------------------------------------------------

def group_leads(leads):
    """One entry per institute: {key: {institute, country, lead_ids, tried (hashes), status}}."""
    groups = {}
    for lead in leads:
        inst = (lead.get("institute") or "").strip()
        if not inst or not lead.get("id"):
            continue
        country = (lead.get("country") or "").strip()
        g = groups.setdefault(inst_key(inst, country), {"institute": inst, "country": country, "lead_ids": [],
                                                        "tried": set(), "status": lead.get("status") or ""})
        g["lead_ids"].append(lead["id"])
        g["tried"] |= tried_hashes(lead)
    return groups


def classify(groups, data_root, lookup=None):
    """(to_upload rows, upload lead ids, to_rescrape groups). lookup(institute, country) -> earlier result or None."""
    lookup = lookup or (lambda i, c: _earlier_result(data_root, i, c))
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows, ids, rescrape = [], [], {}
    for key, g in groups.items():
        alt = alternative_contact(lookup(g["institute"], g["country"]), g["tried"])
        if alt:
            rows.append(upload_row(g["institute"], g["country"], alt,
                                   f"Rescrape: previous contact closed {g['status']} ({today})"))
            ids += g["lead_ids"]
        else:
            rescrape[key] = g
    return rows, ids, rescrape


def _earlier_result(data_root, institute, country):
    rkey = _reuse_key(institute, country)
    hit = reuse.load_index(data_root).get(rkey) if rkey else None
    if not hit:
        return None
    try:
        return reuse._batch_results(data_root, hit["b"], fetch_archive=None).get(hit["r"])
    except Exception:
        return None


# ---------------------------------------------------------------------------------------------------------------
# Queueing a rescrape batch
# ---------------------------------------------------------------------------------------------------------------

def queue_rescrape(groups, data_root, now):
    """Writes the upload CSV, the priority queue request and the lead map. Returns (batch, paths written)."""
    batch = "rescrape-" + now.strftime("%Y%m%d-%H%M")
    up = data_root / "uploads" / f"{batch}.csv"
    up.parent.mkdir(parents=True, exist_ok=True)
    with open(up, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["institute", "country"])
        w.writerows([g["institute"], g["country"]] for g in groups.values())
    req = data_root / "_queue_requests" / f"{batch}.json"
    req.parent.mkdir(parents=True, exist_ok=True)
    req.write_text(json.dumps({"batch": batch, "source": "dialer rescrape", "added": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                               "reader": "paid", "priority": True, "kind": "rescrape"}, indent=1), encoding="utf-8")
    lead_map = data_root / "rescrape" / f"{batch}.json"
    lead_map.parent.mkdir(parents=True, exist_ok=True)
    lead_map.write_text(json.dumps({"institutes": {k: {"institute": g["institute"], "country": g["country"],
                                                       "lead_ids": g["lead_ids"], "tried": sorted(g["tried"]),
                                                       "status": g["status"]} for k, g in groups.items()}},
                                   ensure_ascii=False, indent=1), encoding="utf-8")
    return batch, [up, req, lead_map]


def send_rescraped(lead_map, rows, results, send):
    """After a rescrape batch: the new contacts it found, sent with send(rows). Returns (sent, no_new_contact).
    rows: the batch's numbers_clean.csv rows; results: {row_id: scrape result}."""
    row_of = {}
    for r in rows:
        row_of.setdefault(inst_key(r.get("institution_name"), r.get("country_sheet")), r.get("row_id"))
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out, none = [], 0
    for key, e in (lead_map.get("institutes") or {}).items():
        alt = alternative_contact(results.get(row_of.get(key)), set(e.get("tried") or []))
        if alt:
            out.append(upload_row(e["institute"], e["country"], alt,
                                  f"Rescraped: previous contact closed {e.get('status', '')} ({today})"))
        else:
            none += 1
    if out:
        send(out)
    return len(out), none


# ---------------------------------------------------------------------------------------------------------------
# Git and GitHub
# ---------------------------------------------------------------------------------------------------------------

def _git(*args):
    subprocess.run(["git", *args], cwd=ROOT, check=True)


def push(paths, message):
    _git("config", "user.name", "paragon-scraper-bot")
    _git("config", "user.email", "actions@users.noreply.github.com")
    _git("add", *[str(p) for p in paths])
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode == 0:
        return
    _git("commit", "-q", "-m", message)
    for attempt in range(3):
        try:
            _git("pull", "--rebase", "--autostash", "-q")
            _git("push", "-q")
            return
        except subprocess.CalledProcessError:
            if attempt == 2:
                raise
            time.sleep(5)


def dispatch_queue_tick():
    token, repo = os.environ.get("DISPATCH_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not (token and repo):
        print("  (no DISPATCH_TOKEN: the 10-minute queue tick will start the rescrape)")
        return
    r = requests.post(f"https://api.github.com/repos/{repo}/dispatches", json={"event_type": "queue-tick"},
                      headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}, timeout=30)
    print(f"  queue tick sent (HTTP {r.status_code})" if r.ok else f"  queue tick NOT sent: HTTP {r.status_code}")


# ---------------------------------------------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="Print what would happen; change nothing anywhere")
    args = ap.parse_args()
    secret = os.environ.get("RESCRAPE_SECRET", "")
    url = os.environ.get("DIALER_URL", "").rstrip("/")
    if not (url and secret):
        sys.exit("DIALER_URL and RESCRAPE_SECRET must be set")

    now = datetime.now(timezone.utc)
    stats = {"at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "fetched": 0, "institutes": 0, "sent_now": 0,
             "queued_for_rescrape": 0, "rescrape_batch": None, "error": None}
    written = []
    try:
        leads = fetch_failed_leads(url, secret)
        groups = group_leads(leads)
        stats.update(fetched=len(leads), institutes=len(groups))
        print(f"{len(leads)} failed lead(s), {len(groups)} institute(s)")
        rows, ids, rescrape = classify(groups, DATA)
        print(f"  {len(rows)} with a new number already known, {len(rescrape)} to scrape again")
        if args.dry_run:
            for r in rows[:20]:
                print(f"  [dry-run] would send {r['Institute']} ({r['Country']}): {r['Name'] or 'office line'}")
            for g in list(rescrape.values())[:20]:
                print(f"  [dry-run] would rescrape {g['institute']} ({g['country']})")
            return

        if rows:
            # sent and marked straight away: if anything later fails, these are never sent twice
            _, password = dialer_env()
            upload_to_dialer(rows, url, password, f"Rescrape-{now:%Y-%m-%d} {now:%H}h")
            mark_done(url, secret, ids)
            stats["sent_now"] = len(rows)
        if rescrape:
            batch, written = queue_rescrape(rescrape, DATA, now)
            stats.update(queued_for_rescrape=len(rescrape), rescrape_batch=batch)
    except Exception as e:
        stats["error"] = str(e)[:300]
        print(f"ERROR: {e}", file=sys.stderr)
    finally:
        if not args.dry_run:
            last = RESCRAPE_DIR / "last_run.json"
            last.parent.mkdir(parents=True, exist_ok=True)
            last.write_text(json.dumps(stats, indent=1), encoding="utf-8")
            push(written + [last], f"rescrape: {stats['sent_now']} sent, {stats['queued_for_rescrape']} queued [automated]")

    if stats["rescrape_batch"]:
        # only once the batch is safely in the repository: otherwise these leads come back next hour and are retried
        mark_done(url, secret, [i for g in rescrape.values() for i in g["lead_ids"]])
        dispatch_queue_tick()
    if stats["error"]:
        sys.exit(1)
    print("Done.")


if __name__ == "__main__":
    main()
