"""Rebuilds data_hei/sheet_contacts.json: institutes with a website whose own results still name nobody, but whose
row in the client's uploaded sheet names a person ("Contact Person (as given)"). The dashboard counts them as contacts.

Each name key -> [rows, with the person's own email, reached only through an office email]:
  own email     the sheet's email for that row carries the person's name (anna.rossi@...)
  office email  the sheet gives another email for the row (info@, erasmus@ ...), or the institute has an office inbox
  the rest      a name and nothing to write to (counted on the dashboard as "Name known, no email found yet")
Rows that already have a contact in their results are left out, so nobody is counted twice.
Counts only, no names or emails are written (the page is public). Reads the local copies in leads_local/<batch>/.

  python scripts/build_sheet_contacts.py
"""
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_dashboard_stats as bds  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "leads_local"
OUT = ROOT / "data_hei" / "sheet_contacts.json"
JUNK = re.compile(r"no data|not found|not specific|\bn/?a\b|none|unknown|^-+$|office|department|general|info|contact|"
                  r"team|secretar|admin|reception|@|\d", re.I)
EMAIL = re.compile(r"[^@\s,;:<>()]+@[^@\s,;:<>()]+\.[a-z]{2,}", re.I)


def fold(s):
    import unicodedata
    return "".join(ch for ch in unicodedata.normalize("NFKD", s.lower()) if not unicodedata.combining(ch))


def kind(person, email_cell, result):
    """'own', 'office' or '' for one sheet contact."""
    m = EMAIL.search((email_cell or "").replace("(a)", "@").replace("[at]", "@"))
    if m:
        local = fold(m.group(0).split("@")[0])
        if any(t in local for t in re.split(r"[^a-z]+", fold(person)) if len(t) > 2):
            return "own"
        return "office"
    c = result.get("contacts") or {}
    return "office" if c.get("generic_email") else ""


def rows_of(batch_dir):
    import openpyxl
    wb = openpyxl.load_workbook(batch_dir / "results.xlsx", read_only=True)
    if "Institutions" not in wb.sheetnames:
        return
    it = wb["Institutions"].iter_rows(values_only=True)
    head = {k: i for i, k in enumerate(next(it)) if k}
    if "Row ID" not in head or "Contact Person (as given)" not in head:
        wb.close()
        return
    get = lambda r, k: (r[head[k]] if k in head and head[k] < len(r) else None)  # noqa: E731
    for r in it:
        if not get(r, "Duplicate Of"):
            yield get(r, "Row ID"), str(get(r, "Contact Person (as given)") or "").strip(), str(get(r, "Email (as given)") or "")
    wb.close()


def main():
    hidden = bds.hidden_batches()
    out = defaultdict(lambda: [0, 0, 0])
    seen = 0
    for bdir in sorted(p for p in WORK.iterdir() if (p / "results.xlsx").exists() and (p / "scrape_results").exists()):
        if bdir.name in hidden or bdir.name.startswith(bds.RECHECK_PREFIX):
            continue
        try:
            rows = list(rows_of(bdir))
        except Exception as e:
            print(f"{bdir.name}: skipped ({str(e)[:80]})")
            continue
        seen += 1
        for rid, person, email in rows:
            if not person or JUNK.search(person) or len(re.sub(r"[^\w]", "", person)) < 3:
                continue
            f = bdir / "scrape_results" / f"{rid}.json"
            if not f.exists():
                continue
            r = json.loads(f.read_text(encoding="utf-8"))
            if bds.broken_row(r):
                continue
            n = bds.institute_counts(r)
            if not n["with_website"] or n["with_contact"]:
                continue
            name = " ".join(str((r.get("contacts") or {}).get("institution_name") or "").strip(" \"'").split())
            k = kind(person, email, r)
            v = out[bds._key(name)]
            v[0] += 1
            v[1] += k == "own"
            v[2] += k == "office"
    OUT.write_text(json.dumps(dict(out), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    t = [sum(v[i] for v in out.values()) for i in range(3)]
    print(f"{seen} batches read: {t[0]} sheet contacts ({t[1]} own email, {t[2]} office email, {t[0]-t[1]-t[2]} no email)")


if __name__ == "__main__":
    main()
