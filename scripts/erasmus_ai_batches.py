"""Splits the Erasmus+ coordinators (erasmus_KA1_ongoing_projects.xlsx, sheet Coordinators) into CSV batches for an
outside AI research tool, newest and biggest projects first, plus the prompt to go with them.

    python scripts/erasmus_ai_batches.py [--size 300] [--out erasmus_ai_batches]

Each row id is "eplus/<PIC>", so the filled files can be imported back and matched to the projects.
"""
import argparse
import csv
from pathlib import Path

import openpyxl

OUT_COLS = ["id", "institute", "website", "country", "city", "type", "action_types", "projects",
            "contact_name", "job_title", "contact_email", "contact_phone", "general_email", "source_url", "notes"]

PROMPT = """Do NOT write or run any code. Use your web search and open the pages yourself.

You are a research assistant. The attached CSV ({name}) lists {n} organisations that coordinate an Erasmus+
learning-mobility project (schools, vocational schools, universities and others). Research ALL {n} rows - every single
one, do not skip any and do not stop early. If your answer gets too long, keep going in the next message until the last
row is done.

FOR EACH ROW:

STEP 1 - THE OFFICIAL WEBSITE
- If "website" is given, use it (add https:// if missing). If it does not open or is empty, search the name with the
  city and country and find the organisation's OWN site (its domain usually contains its name). A directory, city
  council, ministry portal, Facebook or news page is NOT the website: then say so in notes and use what you can find.
- If you found a different or new website, put it in notes as "website: <link>".

STEP 2 - ONE PERSON TO CONTACT (the person running Erasmus+ first)
Best first: the Erasmus+ coordinator / project coordinator / international relations officer
("coordinador/a Erasmus", "coordinatrice Erasmus", "Erasmus-Koordinator/in", "koordynator Erasmus+", "responsable
Erasmus", "référent(e) mobilité"). Otherwise the head (principal, director, head teacher, "proviseur", "director/a",
"dyrektor", "dirigente scolastico", "Schulleiter/in", rector). Otherwise the school secretary / office.
Look at: Erasmus+ / Projects / International pages, Contact, Team / Staff, Management, Impressum / Legal notice.

STEP 3 - CONTACT DETAILS
- contact_email / contact_phone: that person's own, only if written on a page.
- general_email / contact_phone: the official general email and phone of the organisation (from its site). Fill this
  even when you found a person.

STRICT RULES
- Only write what you actually saw written on a page. NEVER guess or build an email address.
- source_url = the exact page where the person / email / phone is written (not a search page, never a guessed
  "/contact" link). If details come from two pages, put the second one in notes as a full link.
- If you are not sure it is the same organisation (same name in another town), leave it empty and say why in notes.
- Keep id, institute, country, city exactly as given.

ANSWER: the full CSV with all {n} rows (as a downloadable file if you can), with exactly these columns, same order:
{cols}
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xlsx", default="erasmus_KA1_ongoing_projects.xlsx")
    ap.add_argument("--size", type=int, default=300)
    ap.add_argument("--out", default="erasmus_ai_batches")
    a = ap.parse_args()
    wb = openpyxl.load_workbook(a.xlsx, read_only=True)
    # latest end date first (the most active coordinators), then the largest total grant
    rows = list(wb["Coordinators"].iter_rows(min_row=2, values_only=True))
    rows.sort(key=lambda r: (str(r[9] or ""), float(r[7] or 0)), reverse=True)
    out = Path(a.out)
    out.mkdir(exist_ok=True)
    seen, files = set(), []
    data = []
    for r in rows:
        name, pic, country, city, typ, site, projects, grant, types, end = r[:10]
        key = pic or name
        if not name or key in seen:
            continue
        seen.add(key)
        data.append({"id": "eplus/" + str(pic or len(data)), "institute": name, "website": site or "", "country": country or "",
                     "city": city or "", "type": typ or "", "action_types": types or "", "projects": projects or ""})
    for i in range(0, len(data), a.size):
        part = data[i:i + a.size]
        f = out / f"erasmus_coordinators_{i // a.size + 1:03d}.csv"
        with open(f, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=OUT_COLS)
            w.writeheader()
            for d in part:
                w.writerow({c: d.get(c, "") for c in OUT_COLS})
        files.append((f, len(part)))
    (out / "PROMPT.txt").write_text(PROMPT.format(name="erasmus_coordinators_NNN.csv", n=a.size, cols=",".join(OUT_COLS)),
                                    encoding="utf-8")
    print(f"{len(data)} organisations in {len(files)} files of up to {a.size} rows in {out.resolve()}")


if __name__ == "__main__":
    main()
