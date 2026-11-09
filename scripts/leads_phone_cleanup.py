"""Takes back phones written from search results that are certainly wrong (leads_local/gap/phone_round1_wrong.json):
a number of another country than both the sheet's country and the website's, or digits that are no phone at all
(two numbers run together). Clears only a cell that still holds exactly that number; nothing else changes.

  python scripts/leads_phone_cleanup.py [--dry-run]
"""
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import leads_local_fill as lf  # noqa: E402
import leads_phone_reread as rr  # noqa: E402
import erasmus_pass2 as p2  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def wrong_list():
    import csv
    todo = {r["key"]: r for r in csv.DictReader(open(ROOT / "leads_local/gap/todo_phone.csv", encoding="utf-8"))}
    also = {k: r["also"].split() for k, r in todo.items()}
    out = defaultdict(dict)
    for key, ph, why, _name, _cc in json.load(open(ROOT / "leads_local/gap/phone_round1_wrong.json", encoding="utf-8")):
        r = todo.get(key, {})
        if why == "other country" and r.get("site"):
            tcc = rr.country_of({"cc": ""}, r["site"] if "://" in r["site"] else "https://" + r["site"])
            if tcc in rr.DIAL and re.sub(r"\D", "", ph).startswith(rr.DIAL[tcc]):
                continue                      # the sheet's country is wrong; the website's country agrees
        for k in [key] + also.get(key, []):
            b, rid = k.split("|", 1)
            out[b][rid] = ph
    return out


def main(dry):
    import openpyxl
    wrong = wrong_list()
    print(f"{sum(len(v) for v in wrong.values())} rows in {len(wrong)} batches to check")
    total = 0
    for b, rows in sorted(wrong.items()):
        results, xlsx = lf.fetch(b)
        if not results:
            continue
        cleared = set()
        for rid, ph in rows.items():
            c = (results.get(rid) or {}).get("contacts") or {}
            cur = c.get("phone")
            cur = cur.get("value") if isinstance(cur, dict) else cur
            if cur and re.sub(r"\D", "", str(cur)) == re.sub(r"\D", "", ph) or \
                    (cur and rr.norm(ph, "") and re.sub(r"\D", "", str(cur)) == re.sub(r"\D", "", rr.norm(ph, ""))):
                c["phone"], c["phone_source"] = "", ""
                cleared.add(rid)
        if not cleared:
            continue
        wb = openpyxl.load_workbook(xlsx)
        ws = wb["Institutions"]
        head = {cell.value: i for i, cell in enumerate(ws[1], start=1)}
        cells = 0
        for row in ws.iter_rows(min_row=2):
            if row[head["Row ID"] - 1].value in cleared and "Phone" in head:
                cell = row[head["Phone"] - 1]
                if cell.value and re.sub(r"\D", "", str(cell.value)) == re.sub(r"\D", "", rows[row[head["Row ID"] - 1].value]):
                    cell.value = None
                    cells += 1
        wb.save(xlsx)
        lf.write_back(b, results, xlsx, set(), dry)
        print(f"{b}: {len(cleared)} wrong phones taken back ({cells} cells){' [dry run]' if dry else ''}", flush=True)
        total += len(cleared)
    print(f"done: {total} wrong phones taken back")
    if total and not dry:
        lf.publish_dashboard()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    import erasmus_local  # noqa: F401
    main("--dry-run" in sys.argv)
