"""Imports the outside AI tool's results for the Erasmus+ coordinators (folder erasmus_ai_results/, any *.csv with the
columns of erasmus_ai_batches/) and keeps only what the cited page really shows (scripts/import_ai_research.check:
the person's name, the email, the phone must be written on the source page, or on the organisation's own website).

    python scripts/import_erasmus_ai.py            # all result files -> erasmus_contacts_verified.xlsx

Output sheets:
  Contacts  one row per organisation with a confirmed detail: name, role, email, phone, general email, source page,
            whether the source is the organisation's own site or a directory, the AI tool's note
  Projects  every project of erasmus_KA1_ongoing_projects.xlsx with its coordinator's confirmed contact added
  Rejected  details the AI tool gave that the page did not show (with the reason), for a second look
"""
import csv
import glob
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scraper2"))
sys.path.insert(0, str(ROOT / "scraper"))
import import_ai_research as air  # noqa: E402
import openpyxl  # noqa: E402
from openpyxl.styles import Font  # noqa: E402

RESULTS = ROOT / "erasmus_ai_results"
PROJECTS = ROOT / "erasmus_KA1_ongoing_projects.xlsx"
OUT = ROOT / "erasmus_contacts_verified.xlsx"


def host(u):
    u = re.sub(r"^https?://", "", str(u or "").strip().lower())
    u = u.split("/")[0]
    return u[4:] if u.startswith("www.") else u


def rows_from_files():
    rows = {}
    for f in sorted(glob.glob(str(RESULTS / "*.csv"))):
        with open(f, encoding="utf-8-sig", newline="") as fh:
            for r in csv.DictReader(fh):
                rid = (r.get("id") or "").strip()
                if rid.startswith("eplus/"):
                    r["_file"] = Path(f).name
                    rows[rid] = r                    # a later file wins (a batch researched again)
    return list(rows.values())


def has_detail(r):
    return any((r.get(k) or "").strip() for k in ("contact_name", "contact_email", "contact_phone", "general_email"))


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    rows = [r for r in rows_from_files() if has_detail(r)]
    print(f"{len(rows)} organisations with a detail to check", flush=True)
    cache = {}

    def one(r):
        got = air.check(r, cache)
        return r, got

    with ThreadPoolExecutor(12) as pool:
        done = list(pool.map(one, rows))

    contacts, rejected = {}, []
    for r, got in done:
        pic = r["id"].split("/", 1)[1]
        src = got.get("source_url") or r.get("source_url") or ""
        site = r.get("website") or ""
        own = bool(site and host(src) and (host(src) == host(site) or host(src).endswith("." + host(site))))
        keep = {"name": got.get("name", ""), "role": got.get("designation", "") if got.get("name") else "",
                "email": got.get("contact_email", ""), "phone": got.get("phone", ""), "general": got.get("general_email", "")}
        for field, label in (("contact_name", "name"), ("contact_email", "email"), ("contact_phone", "phone"),
                             ("general_email", "general")):
            v = (r.get(field) or "").strip()
            if v and not keep[label]:
                rejected.append([r["id"], r.get("institute"), field, v, src, "not written on the cited page or the website",
                                 r["_file"]])
        if any(keep.values()):
            contacts[pic] = [r["id"], pic, r.get("institute"), r.get("country"), r.get("city"), site, keep["name"], keep["role"],
                             keep["email"], keep["phone"], keep["general"], src, "own website" if own else "directory / other page",
                             (r.get("notes") or "")[:500], r["_file"]]

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Contacts"
    ws.append(["id", "PIC", "Organisation", "Country", "City", "Website", "Contact name", "Role", "Contact email",
               "Phone", "General email", "Source page", "Source type", "AI note", "From file"])
    for row in contacts.values():
        ws.append(row)
    pj = wb.create_sheet("Projects")
    if PROJECTS.exists():
        src_wb = openpyxl.load_workbook(PROJECTS, read_only=True)
        it = src_wb["Projects"].iter_rows(values_only=True)
        head = list(next(it))
        pic_col = head.index("Coordinator PIC")
        pj.append(head + ["Contact name", "Role", "Contact email", "Phone", "General email", "Contact source"])
        for p in it:
            c = contacts.get(str(p[pic_col] or ""))
            pj.append(list(p) + ([c[6], c[7], c[8], c[9], c[10], c[11]] if c else ["", "", "", "", "", ""]))
    rj = wb.create_sheet("Rejected")
    rj.append(["id", "Organisation", "Field", "Value given", "Source page", "Why not kept", "From file"])
    for row in rejected:
        rj.append(row)
    for sh in wb.worksheets:
        for cell in sh[1]:
            cell.font = Font(bold=True)
        sh.freeze_panes = "A2"
    wb.save(OUT)
    n = lambda i: sum(1 for c in contacts.values() if c[i])  # noqa: E731
    print(f"{len(contacts)} organisations kept: {n(6)} named contacts, {n(8)} personal emails, {n(10)} general emails, "
          f"{n(9)} phones; {len(rejected)} details rejected.\nSaved: {OUT}")


if __name__ == "__main__":
    main()
