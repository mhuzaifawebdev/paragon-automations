"""Rescrape integration: scraper ↔ Nazish's auto-dialer.

When the dialer marks leads unreachable (5× no-answer) or wrong_number, this script:
  1. Fetches those leads from /api/failed-leads on the dialer.
  2. For each institute, looks it up in the scraper's reuse index.
     - Found with a usable alternative contact → queued into a mini-batch CSV for the dialer.
     - Not found (never scraped) → added to a "rescrape queue" Google Sheet dropped into
       the configured Drive folder; the auto-scanner picks it up next tick.
  3. Uploads the mini-batch CSV to the dialer (admin_login → batch_check → batch_create).
  4. Marks processed lead IDs as rescrape_queued on the dialer, so they are not re-fetched.

Usage:
    python scripts/rescrape_leads.py [--dry-run]

Environment variables (set in GitHub Actions secrets):
    DIALER_URL          base URL of the dialer, e.g. https://pgi-backend.vercel.app
    DIALER_ADMIN_PASS   the dialer admin panel password
    RESCRAPE_SECRET     shared secret for /api/failed-leads (same value in pgi-backend env)
    DRIVE_FOLDER_ID     the scraper's Drive folder (for the rescrape-queue sheet)
    GOOGLE_SERVICE_ACCOUNT_JSON  (already set; used for Drive upload)
"""
import argparse
import csv
import io
import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data_hei"
sys.path.insert(0, str(ROOT / "scraper2"))
import reuse  # noqa: E402

DIALER_BATCH_NAME = "Rescrape-{date}"
DIALER_QUEUE = "SALES"
MAX_BATCH_ROWS = 1000

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _norm(s):
    s = unicodedata.normalize("NFKD", str(s or "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


def _reuse_key(institute, country):
    n = _norm(institute)
    if len(n) < 4:
        return None
    return f"n:{n}|{_norm(country)}"


def _contacts_from_result(res):
    """Extract phone and email alternatives from a scraper result."""
    c = (res or {}).get("contacts") or {}
    return {
        "work_email":     c.get("work_email") or "",
        "personal_email": c.get("personal_email") or "",
        "phone":          c.get("phone") or "",
        "name":           c.get("name") or "",
        "designation":    c.get("designation") or "",
        "source_url":     c.get("official_website") or "",
    }


# ---------------------------------------------------------------------------
# Step 1: fetch failed leads from the dialer
# ---------------------------------------------------------------------------

def fetch_failed_leads(dialer_url, secret):
    url = dialer_url.rstrip("/") + "/api/failed-leads"
    r = requests.get(url, params={"token": secret}, timeout=60)
    r.raise_for_status()
    text = r.text
    if not text.strip():
        return []
    reader = csv.DictReader(io.StringIO(text))
    return list(reader)


# ---------------------------------------------------------------------------
# Step 2: look up each lead in the reuse index
# ---------------------------------------------------------------------------

def classify_leads(leads, data_root):
    """Returns (to_upload, to_rescrape).

    to_upload: list of dicts ready to go into the dialer mini-batch CSV
    to_rescrape: list of dicts to be added to the Drive rescrape-queue sheet
    """
    index = reuse.load_index(data_root)
    to_upload = []
    to_rescrape = []
    seen_institutes = {}  # institute+country → already classified this run

    for lead in leads:
        inst = lead.get("institute", "").strip()
        country = lead.get("country", "").strip()
        lead_id = lead.get("id", "")

        if not inst:
            continue

        key = (inst.lower(), country.lower())
        if key in seen_institutes:
            category, scraper_contact = seen_institutes[key]
        else:
            rkey = _reuse_key(inst, country)
            hit = index.get(rkey) if rkey else None
            if hit:
                earlier = reuse._batch_results(data_root, hit["b"], fetch_archive=None).get(hit["r"])
                if reuse.reusable(earlier):
                    scraper_contact = _contacts_from_result(earlier)
                    category = "upload"
                else:
                    scraper_contact = {}
                    category = "rescrape"
            else:
                scraper_contact = {}
                category = "rescrape"
            seen_institutes[key] = (category, scraper_contact)

        if category == "upload":
            alt_phone = scraper_contact.get("phone", "")
            alt_email = scraper_contact.get("work_email") or scraper_contact.get("personal_email") or ""
            blocked_phones = {lead.get("phone", ""), lead.get("phone2", ""), lead.get("phone3", "")} - {""}

            # Skip if the scraped phone is already one of the blocked/tried ones.
            if alt_phone and alt_phone in blocked_phones:
                alt_phone = ""
            if not alt_phone and not alt_email:
                category = "rescrape"
                seen_institutes[key] = (category, scraper_contact)
                to_rescrape.append({"institute": inst, "country": country,
                                    "reason": lead.get("status", ""), "dialer_lead_id": lead_id})
                continue

            to_upload.append({
                "Institute":   inst,
                "Phone":       alt_phone,
                "Second Phone": "",
                "Name":        scraper_contact.get("name") or lead.get("name") or "",
                "Designation": scraper_contact.get("designation") or lead.get("designation") or "",
                "Email":       alt_email,
                "Country":     country,
                "Notes":       (f"Rescrape: original lead {lead_id} closed "
                                f"{lead.get('status','')} on {datetime.now(timezone.utc).strftime('%Y-%m-%d')}"),
                "_lead_id":    lead_id,
            })
        else:
            to_rescrape.append({"institute": inst, "country": country,
                                "reason": lead.get("status", ""), "dialer_lead_id": lead_id})

    return to_upload, to_rescrape


# ---------------------------------------------------------------------------
# Step 3: upload mini-batch to the dialer
# ---------------------------------------------------------------------------

def _admin_token(dialer_url, password):
    r = requests.post(dialer_url.rstrip("/") + "/api/proxy",
                      json={"action": "admin_login", "password": password}, timeout=30)
    r.raise_for_status()
    return r.json()["token"]


def _batch_check(dialer_url, token, csv_text):
    r = requests.post(dialer_url.rstrip("/") + "/api/proxy",
                      json={"action": "batch_check", "csv": csv_text},
                      headers={"Authorization": f"Bearer {token}"}, timeout=60)
    r.raise_for_status()
    return r.json()


def _batch_create(dialer_url, token, check_result, name, queue):
    payload = {**check_result, "action": "batch_create", "name": name, "queue": queue}
    r = requests.post(dialer_url.rstrip("/") + "/api/proxy",
                      json=payload,
                      headers={"Authorization": f"Bearer {token}"}, timeout=60)
    r.raise_for_status()
    return r.json()


def upload_to_dialer(to_upload, dialer_url, password, dry_run=False):
    """Uploads rows in chunks of MAX_BATCH_ROWS. Returns list of batch names created."""
    if not to_upload:
        return []

    cols = ["Institute", "Phone", "Second Phone", "Name", "Designation", "Email", "Country", "Notes"]
    batches_created = []

    chunks = [to_upload[i:i + MAX_BATCH_ROWS] for i in range(0, len(to_upload), MAX_BATCH_ROWS)]
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    token = None if dry_run else _admin_token(dialer_url, password)

    for idx, chunk in enumerate(chunks, 1):
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(chunk)
        csv_text = buf.getvalue()

        suffix = f"-p{idx}" if len(chunks) > 1 else ""
        name = DIALER_BATCH_NAME.format(date=date_str) + suffix

        if dry_run:
            print(f"[dry-run] Would upload batch '{name}' with {len(chunk)} rows")
            batches_created.append(name)
            continue

        check = _batch_check(dialer_url, token, csv_text)
        result = _batch_create(dialer_url, token, check, name, DIALER_QUEUE)
        batch_id = result.get("batch", {}).get("id") or "?"
        print(f"  Uploaded batch '{name}' ({len(chunk)} rows) → dialer id {batch_id}")
        batches_created.append(name)
        time.sleep(1)

    return batches_created


# ---------------------------------------------------------------------------
# Step 4: write rescrape-queue sheet to Drive (auto-scanner picks it up)
# ---------------------------------------------------------------------------

def write_rescrape_sheet(to_rescrape, dry_run=False):
    """Writes a Google Sheet to the Drive folder with institutes to rescrape."""
    if not to_rescrape:
        return None

    folder_id = os.environ.get("DRIVE_FOLDER_ID")
    if not folder_id:
        print("  DRIVE_FOLDER_ID not set; skipping rescrape sheet")
        return None

    sys.path.insert(0, str(ROOT / "scraper2"))
    import drive_upload

    cols = ["institute", "country", "reason", "dialer_lead_id"]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols)
    w.writeheader()
    w.writerows(to_rescrape)
    csv_bytes = buf.getvalue().encode("utf-8")

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    sheet_name = f"Rescrape Queue {date_str}.csv"

    if dry_run:
        print(f"[dry-run] Would write rescrape sheet '{sheet_name}' ({len(to_rescrape)} rows) to Drive")
        return None

    svc = drive_upload.robot_service()
    link = drive_upload.put_bytes(svc, csv_bytes, sheet_name, folder_id, mime="text/csv")
    print(f"  Rescrape sheet '{sheet_name}' written to Drive: {link}")
    return link


# ---------------------------------------------------------------------------
# Step 5: mark processed leads as rescrape_queued on the dialer
# ---------------------------------------------------------------------------

def mark_done(dialer_url, secret, lead_ids, dry_run=False):
    if not lead_ids:
        return
    if dry_run:
        print(f"[dry-run] Would mark {len(lead_ids)} leads as rescrape_queued")
        return
    r = requests.post(dialer_url.rstrip("/") + "/api/failed-leads",
                      json={"token": secret, "ids": lead_ids}, timeout=60)
    r.raise_for_status()
    result = r.json()
    print(f"  Marked {result.get('marked', '?')} leads as rescrape_queued")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="Print what would happen without making changes")
    args = ap.parse_args()

    dialer_url   = os.environ.get("DIALER_URL", "").rstrip("/")
    admin_pass   = os.environ.get("DIALER_ADMIN_PASS", "")
    secret       = os.environ.get("RESCRAPE_SECRET", "")

    if not dialer_url:
        sys.exit("DIALER_URL not set")
    if not secret:
        sys.exit("RESCRAPE_SECRET not set")
    if not admin_pass and not args.dry_run:
        sys.exit("DIALER_ADMIN_PASS not set")

    print(f"Fetching failed leads from {dialer_url} ...")
    try:
        leads = fetch_failed_leads(dialer_url, secret)
    except Exception as e:
        sys.exit(f"Failed to fetch leads: {e}")
    print(f"  {len(leads)} failed lead(s) returned")

    if not leads:
        print("Nothing to do.")
        return

    print("Checking reuse index ...")
    to_upload, to_rescrape = classify_leads(leads, DATA)
    print(f"  {len(to_upload)} with alternative contact found, {len(to_rescrape)} need rescraping")

    all_processed_ids = [r["_lead_id"] for r in to_upload] + [r["dialer_lead_id"] for r in to_rescrape]

    if to_upload:
        print("Uploading mini-batch to dialer ...")
        upload_to_dialer(to_upload, dialer_url, admin_pass, dry_run=args.dry_run)

    if to_rescrape:
        print("Writing rescrape-queue sheet to Drive ...")
        write_rescrape_sheet(to_rescrape, dry_run=args.dry_run)

    print("Marking leads as rescrape_queued ...")
    mark_done(dialer_url, secret, [i for i in all_processed_ids if i], dry_run=args.dry_run)

    print("Done.")


if __name__ == "__main__":
    main()
