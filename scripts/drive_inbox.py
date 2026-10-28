"""Picks up the lists the client drops into the Drive inbox and queues one batch per file.

    <DRIVE_FOLDER_ID>/Inbox  ->  checked, converted to CSV, queued  ->  moved to Processing
                             ->  unusable: moved to Failed with a "<file> - why.txt" note
                             ->  the same file again (same Drive checksum): moved to Done with a note

The batch is named after the file ("Spain schools.xlsx" -> spain-schools, -2 if that name is taken). A list longer
than MAX_ROWS is split into parts (-p1, -p2, ...). CSV, Excel (.xlsx) and Google Sheets are read; the first sheet
that has institute rows is used. Run by scripts/queue_tick.py; no Drive call is made in the pure helpers below.

Auto-queue (zero intervention):
    config.yaml  scraper.auto_queue_scan_drive: true
    The queue scans the entire DRIVE_FOLDER_ID tree every tick, finds every Google Sheet, and queues any
    that have not been queued before. Nothing is moved or deleted. Sheets with no institute columns are
    skipped automatically. Each sheet is queued exactly once (file ID remembered in queue.json).
    Optional: scraper.auto_queue_skip_ids — list of Drive file IDs to never queue (e.g. reference sheets).
"""
import csv
import io
import os
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scraper2"))
import import_batch  # noqa: E402

MAX_ROWS = 1200          # the same limit as the upload page (web_scraper/api/start-batch.js)
PART_ROWS = 1000         # a longer list becomes parts of this size
NAME_MAX = 40            # batch names: a-z, 0-9 and "-", 3 to 40 characters (the page's BATCH_RE)


class Unusable(Exception):
    """The file cannot become a batch; the message is written to the client in Failed/<file> - why.txt."""


def slug(file_name):
    base = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", str(file_name or ""))
    base = unicodedata.normalize("NFKD", base.lower())
    base = "".join(c for c in base if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9]+", "-", base).strip("-")[:NAME_MAX - 4].strip("-")
    return s if len(s) >= 3 else ("batch-" + s).strip("-")


def unique_name(base, taken):
    name, i = base, 2
    while name in taken:
        suffix = f"-{i}"
        name = base[:NAME_MAX - len(suffix)].rstrip("-") + suffix
        i += 1
    taken.add(name)
    return name


def _decode(data):
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise Unusable("The file's text could not be read (unknown character encoding). Save it as CSV UTF-8 and drop it again.")


def institute_rows(csv_text):
    """How many institute rows import_batch finds in this CSV text (0 = no usable column)."""
    try:
        return len(import_batch.build_rows(import_batch.rows_from_csv_text(csv_text)))
    except Exception:
        return 0


def table_from_file(name, mime, data):
    """(CSV text, note) from a dropped file. Raises Unusable with a reason the client can act on."""
    low = str(name or "").lower()
    if mime == "application/vnd.google-apps.spreadsheet" or low.endswith((".csv", ".txt")) or mime == "text/csv":
        text = _decode(data)
        if not institute_rows(text):
            raise Unusable("No institute rows found. The list needs a column such as 'University', 'Institution Name' "
                           "or 'Institute'.")
        return text, ""
    if low.endswith(".xls") and not low.endswith(".xlsx"):
        raise Unusable("This is the old Excel format (.xls). Open it in Excel, save it as .xlsx, and drop it again.")
    if low.endswith(".xlsx") or "spreadsheetml" in str(mime or ""):
        import openpyxl
        try:
            wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        except Exception as e:
            raise Unusable(f"The Excel file could not be opened ({type(e).__name__}). Check it opens in Excel and drop it again.")
        skipped = []
        for ws in wb.worksheets:
            out = io.StringIO()
            w = csv.writer(out)
            for row in ws.iter_rows(values_only=True):
                if any(v not in (None, "") for v in row):
                    w.writerow(["" if v is None else v for v in row])
            text = out.getvalue()
            if institute_rows(text):
                note = f"Read the sheet '{ws.title}'." + (f" Sheets without institute rows were skipped: "
                                                         f"{', '.join(skipped)}." if skipped else "")
                return text, note
            skipped.append(ws.title)
        raise Unusable("No sheet in this Excel file has institute rows. The list needs a column such as 'University', "
                       "'Institution Name' or 'Institute'.")
    raise Unusable("Only CSV, Excel (.xlsx) and Google Sheets files can be read.")


def split(csv_text, size=PART_ROWS):
    """Parts of at most `size` data rows, each with the header row. One part when the list is short enough."""
    rows = list(csv.reader(io.StringIO(csv_text)))
    if not rows:
        return [csv_text]
    header, body = rows[0], [r for r in rows[1:] if any(c.strip() for c in r)]
    if len(body) <= MAX_ROWS:
        return [csv_text]
    parts = []
    for i in range(0, len(body), size):
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(header)
        w.writerows(body[i:i + size])
        parts.append(out.getvalue())
    return parts


def pickup(drive, data_root, queue, now):
    """drive: an object with inbox_files(), download(f), to_processing(f), to_done(f, note), to_failed(f, note).
    Adds a queue item per batch, writes data_hei/uploads/<batch>.csv. Returns one line per file handled."""
    data_root = Path(data_root)
    taken = {p.name for p in data_root.iterdir() if p.is_dir()} | {it["batch"] for it in queue["items"]}
    taken |= {p.stem for p in (data_root / "uploads").glob("*.csv")} if (data_root / "uploads").exists() else set()
    by_md5 = {it["md5"]: it["batch"] for it in queue["items"] if it.get("md5")}
    log = []
    for f in drive.inbox_files():
        name = f.get("name") or "file"
        if f.get("md5Checksum") and f["md5Checksum"] in by_md5:
            drive.to_done(f, f"This file was already queued as batch '{by_md5[f['md5Checksum']]}', so it was not "
                             f"scraped again.")
            log.append(f"{name}: same file as batch {by_md5[f['md5Checksum']]} - skipped")
            continue
        try:
            text, note = table_from_file(name, f.get("mimeType"), drive.download(f))
        except Unusable as e:
            drive.to_failed(f, str(e))
            log.append(f"{name}: failed - {e}")
            continue
        parts = split(text)
        base = unique_name(slug(name), taken)
        names = [base] if len(parts) == 1 else [unique_name(f"{base[:NAME_MAX - 4].rstrip('-')}-p{i}", taken)
                                                for i in range(1, len(parts) + 1)]
        (data_root / "uploads").mkdir(parents=True, exist_ok=True)
        for i, (batch, part) in enumerate(zip(names, parts), 1):
            (data_root / "uploads" / f"{batch}.csv").write_text(part, encoding="utf-8")
            queue["items"].append({"batch": batch, "source": name, "file_id": f.get("id"), "md5": f.get("md5Checksum"),
                                   "added": now, "state": "queued", "rows": institute_rows(part),
                                   **({"part": f"{i}/{len(parts)}"} if len(parts) > 1 else {}),
                                   **({"note": note} if note else {})})
        if f.get("md5Checksum"):
            by_md5[f["md5Checksum"]] = names[0]
        drive.to_processing(f)
        log.append(f"{name}: queued as {', '.join(names)}")
    return log


def pickup_scan_drive(svc, data_root, queue, cfg, now):
    """Scan the whole Drive folder tree, find every Google Sheet not yet queued, queue it.
    Called when config.yaml scraper.auto_queue_scan_drive: true.
    Skip list: scraper.auto_queue_skip_ids (file IDs of reference sheets to never queue).
    """
    import drive_upload
    root = os.environ.get("DRIVE_FOLDER_ID", "")
    if not root:
        return ["auto-scan: DRIVE_FOLDER_ID not set"]
    skip = set(cfg.get("auto_queue_skip_ids") or [])
    seen_ids = {it.get("file_id") for it in queue["items"] if it.get("file_id")}
    seen_ids |= set(queue.get("auto_queued_ids", []))
    data_root = Path(data_root)
    taken = {p.name for p in data_root.iterdir() if p.is_dir()} | {it["batch"] for it in queue["items"]}
    taken |= {p.stem for p in (data_root / "uploads").glob("*.csv")} if (data_root / "uploads").exists() else set()
    log = []
    try:
        sheets = drive_upload.list_sheets_recursive(svc, root)
    except Exception as e:
        return [f"auto-scan: Drive listing failed — {str(e)[:200]}"]
    for f in sheets:
        fid, name = f["id"], f.get("name") or f["id"]
        if fid in seen_ids or fid in skip:
            continue
        try:
            # Export as XLSX so all tabs are available (table_from_file tries each tab for xlsx).
            # Falls back gracefully if the sheet has only one tab.
            data = drive_upload.download_xlsx(svc, f)
            text, note = table_from_file(name + ".xlsx", drive_upload.XLSX, data)
        except Unusable as e:
            log.append(f"auto-scan '{name}' ({fid}): skipped — {e}")
            queue.setdefault("auto_queued_ids", []).append(fid)   # no institute columns — skip forever
            seen_ids.add(fid)
            continue
        except Exception as e:
            log.append(f"auto-scan '{name}': error reading — {str(e)[:120]}")
            continue
        parts = split(text)
        base  = unique_name(slug(name), taken)
        names = [base] if len(parts) == 1 else [
            unique_name(f"{base[:NAME_MAX - 4].rstrip('-')}-p{i}", taken) for i in range(1, len(parts) + 1)]
        (data_root / "uploads").mkdir(parents=True, exist_ok=True)
        for i, (batch, part) in enumerate(zip(names, parts), 1):
            (data_root / "uploads" / f"{batch}.csv").write_text(part, encoding="utf-8")
            queue["items"].append({"batch": batch, "source": name, "file_id": fid, "added": now,
                                   "state": "queued", "rows": institute_rows(part),
                                   **({"part": f"{i}/{len(parts)}"} if len(parts) > 1 else {}),
                                   **({"note": note} if note else {})})
        queue.setdefault("auto_queued_ids", []).append(fid)
        seen_ids.add(fid)
        log.append(f"auto-scan '{name}': queued as {', '.join(names)}" +
                   (f" ({len(parts)} parts)" if len(parts) > 1 else ""))
    return log


def pickup_auto_sheets(svc_download, data_root, queue, cfg, now):
    """Auto-queue Google Sheets listed in config.yaml scraper.auto_queue_sheets.
    Each sheet is queued exactly once (tracked by Drive file ID). The sheet stays where it is — nothing is moved.
    svc_download(file_id) -> bytes   (calls drive_upload.download with a fake file dict)
    """
    sheets = cfg.get("auto_queue_sheets") or []
    if not sheets:
        return []
    data_root = Path(data_root)
    # all file IDs already seen in the queue (any state) or uploads folder
    seen_ids = {it.get("file_id") for it in queue["items"] if it.get("file_id")}
    seen_ids |= {it.get("file_id") for it in queue.get("done_file_ids", []) if it.get("file_id")} if False else set()
    seen_ids |= set(queue.get("auto_queued_ids", []))
    taken = {p.name for p in data_root.iterdir() if p.is_dir()} | {it["batch"] for it in queue["items"]}
    taken |= {p.stem for p in (data_root / "uploads").glob("*.csv")} if (data_root / "uploads").exists() else set()
    log = []
    for entry in sheets:
        fid  = entry.get("id") if isinstance(entry, dict) else (entry if isinstance(entry, str) else None)
        name = (entry.get("name") if isinstance(entry, dict) else None) or fid or "auto-sheet"
        if not fid or fid in seen_ids:
            continue
        try:
            data = svc_download(fid)
            text, note = table_from_file(name + ".gsheet", "application/vnd.google-apps.spreadsheet", data)
        except Unusable as e:
            log.append(f"auto-sheet {name} ({fid}): skipped — {e}")
            queue.setdefault("auto_queued_ids", []).append(fid)   # don't retry broken sheets
            continue
        except Exception as e:
            log.append(f"auto-sheet {name} ({fid}): error reading — {str(e)[:150]}")
            continue
        parts = split(text)
        base  = unique_name(slug(name), taken)
        names = [base] if len(parts) == 1 else [
            unique_name(f"{base[:NAME_MAX - 4].rstrip('-')}-p{i}", taken) for i in range(1, len(parts) + 1)]
        (data_root / "uploads").mkdir(parents=True, exist_ok=True)
        for i, (batch, part) in enumerate(zip(names, parts), 1):
            (data_root / "uploads" / f"{batch}.csv").write_text(part, encoding="utf-8")
            queue["items"].append({"batch": batch, "source": name, "file_id": fid, "added": now,
                                   "state": "queued", "rows": institute_rows(part),
                                   **({"part": f"{i}/{len(parts)}"} if len(parts) > 1 else {}),
                                   **({"note": note} if note else {})})
        queue.setdefault("auto_queued_ids", []).append(fid)
        seen_ids.add(fid)
        log.append(f"auto-sheet '{name}': queued as {', '.join(names)}" +
                   (f" ({len(parts)} parts)" if len(parts) > 1 else ""))
    return log


def _names(csv_text):
    try:
        return {import_batch._norm(r["institution_name"]) for r in import_batch.rows_from_csv_text(csv_text)}
    except Exception:
        return set()


def refresh_queue(fetch, data_root, queue, now, drop_ids=(), dry_run=False):
    """Re-reads every sheet already in the queue with today's importer, so a sheet queued under older rules is
    not stuck with the rows those rules found. fetch(file_id) -> CSV text of the sheet (first tab with institutes).
      - queued, not yet imported: the upload is replaced when the sheet now gives more rows;
      - already started or done: the rows it lacks become a new batch '<batch>-extra' (names it already has are
        left out, so nothing is scraped twice);
      - items from drop_ids (directories, not institute lists) are removed, with their uploads.
    Multi-part items are left alone. Returns one line per change."""
    data_root, log = Path(data_root), []
    uploads = data_root / "uploads"
    taken = {it["batch"] for it in queue["items"]}
    for it in list(queue["items"]):
        if it.get("file_id") in drop_ids and it.get("state") == "queued":
            log.append(f"{it['batch']}: removed (not an institute list, {it.get('rows')} rows)")
            if not dry_run:
                queue["items"].remove(it)
                (uploads / f"{it['batch']}.csv").unlink(missing_ok=True)
    for it in list(queue["items"]):
        fid, batch = it.get("file_id"), it["batch"]
        if not fid or it.get("part") or batch.endswith("-extra") or fid in drop_ids:
            continue
        try:
            text = fetch(fid)
        except Exception as e:
            log.append(f"{batch}: could not be read - {str(e)[:100]}")
            continue
        now_rows, had = institute_rows(text), it.get("rows") or 0
        if now_rows <= had:
            continue
        started = it.get("state") != "queued" or (data_root / batch / "numbers_clean.csv").exists()
        if not started:
            if len(split(text)) > 1:
                log.append(f"{batch}: now {now_rows} rows, too long for one batch - left as is")
                continue
            log.append(f"{batch}: {had} -> {now_rows} rows (re-read)")
            if not dry_run:
                (uploads / f"{batch}.csv").write_text(text, encoding="utf-8")
                it["rows"] = now_rows
            continue
        extra_name = f"{batch[:NAME_MAX - 6].rstrip('-')}-extra"
        if extra_name in taken:
            continue
        old = uploads / f"{batch}.csv"
        have = _names(old.read_text(encoding="utf-8")) if old.exists() else set()
        rows = [r for r in import_batch.rows_from_csv_text(text)
                if r.get("institution_name") and import_batch._norm(r["institution_name"]) not in have]
        if not rows:
            continue
        log.append(f"{extra_name}: {len(rows)} institutes {batch} lacks")
        taken.add(extra_name)
        if dry_run:
            continue
        out = io.StringIO()
        fields = sorted({k for r in rows for k in r})
        w = csv.DictWriter(out, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
        (uploads / f"{extra_name}.csv").write_text(out.getvalue(), encoding="utf-8")
        queue["items"].append({"batch": extra_name, "source": it.get("source", ""), "file_id": fid, "added": now,
                               "state": "queued", "rows": len(rows), "note": f"Rows missing from {batch}."})
    return log


def _refresh_main(argv):
    """python scripts/drive_inbox.py --refresh [--dry-run] [--drop ID ...] [--retry ID ...]
    Local, one-off: needs credentials/google_service_account.json."""
    import json
    from datetime import datetime, timezone
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    import drive_upload
    ids = lambda flag: [argv[i + 1] for i, a in enumerate(argv) if a == flag and i + 1 < len(argv)]   # noqa: E731
    svc = build("drive", "v3", credentials=service_account.Credentials.from_service_account_file(
        str(drive_upload.KEY_FILE), scopes=["https://www.googleapis.com/auth/drive.readonly"]))

    def fetch(fid):
        meta = svc.files().get(fileId=fid, fields="id,name,mimeType", supportsAllDrives=True).execute()
        return table_from_file(meta["name"] + ".xlsx", drive_upload.XLSX, drive_upload.download_xlsx(svc, meta))[0]

    qpath = ROOT / "data_hei" / "queue.json"
    queue = json.loads(qpath.read_text(encoding="utf-8"))
    before = sum(it.get("rows") or 0 for it in queue["items"])
    dry = "--dry-run" in argv
    log = refresh_queue(fetch, ROOT / "data_hei", queue, datetime.now(timezone.utc).isoformat(), set(ids("--drop")), dry)
    retry = set(ids("--retry"))
    if retry and not dry:
        queue["auto_queued_ids"] = [f for f in queue.get("auto_queued_ids", []) if f not in retry]
    print("\n".join(log) or "nothing to change")
    print(f"queued institutes: {before} -> {sum(it.get('rows') or 0 for it in queue['items'])}"
          + (" (dry run, nothing written)" if dry else ""))
    if not dry:
        qpath.write_text(json.dumps(queue, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


class DriveInbox:
    """The real Drive behind pickup() and the queue: the shared-drive tree of scraper2/drive_upload.py."""

    def __init__(self, svc=None):
        import drive_upload
        self.du = drive_upload
        self.svc = svc or drive_upload.robot_service()
        self.f = {n: drive_upload.subfolder(self.svc, n) for n in ("Inbox", "Processing", "Done", "Failed")}

    def inbox_files(self):
        return self.du.list_files(self.svc, self.f["Inbox"])

    def download(self, f):
        return self.du.download(self.svc, f)

    def _note(self, f, folder, note):
        self.du.put_bytes(self.svc, (note + "\n").encode("utf-8"), f"{f.get('name')} - why.txt", self.f[folder])

    def to_processing(self, f):
        self.du.move(self.svc, f["id"], self.f["Processing"])

    def to_done(self, f, note=""):
        self.du.move(self.svc, f["id"], self.f["Done"])
        if note:
            self._note(f, "Done", note)

    def to_failed(self, f, note):
        self.du.move(self.svc, f["id"], self.f["Failed"])
        self._note(f, "Failed", note)


if __name__ == "__main__" and "--refresh" in sys.argv:
    _refresh_main(sys.argv)
