"""Offline tests for scraper2/import_batch.py's header mapping and row building. No network,
no service account, no cost. Run: python tests/test_import_batch.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scraper2"))
import import_batch as ib  # noqa: E402

results = []


def check(name, got, want):
    ok = got == want
    results.append(ok)
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f": got {got!r}, want {want!r}"))


# ---- header aliasing ----
check("recognizes 'University' as institution_name and 'Email' as email",
      ib._map_headers(["University", "Country", "Email"]),
      {"institution_name": "University", "country_sheet": "Country", "email": "Email"})

check("is case/spacing insensitive ('Institution Name' -> institution_name)",
      "institution_name" in ib._map_headers(["Institution Name"]), True)

try:
    ib._map_headers(["Country", "Email"])   # no institution-name-like column at all
    raised = False
except ib.ImportError_ as e:
    raised = "institution_name" in str(e)
check("missing the one required column raises a loud, specific error", raised, True)


# ---- CSV parsing end to end ----
csv_text = "University,Country,Email\nExample College,Malta,info@example.edu\n,Nowhere,x@x.com\n"
rows = ib.rows_from_csv_text(csv_text)
check("rows_from_csv_text reads both data rows", len(rows), 2)
check("mapped values land under the canonical field name", rows[0]["institution_name"], "Example College")

numbers = ib.build_rows(rows)
check("a row with no institution name is dropped", len(numbers), 1)
check("row_id is assigned r001-style", numbers[0]["row_id"], "r001")
check("every output row has all the fields run.py expects", set(numbers[0]) == set(ib.OUT_FIELDS), True)
check("an ordinary (non-merged) row has no review flag", numbers[0]["flags"], "")

seed_rows = ib.build_rows([{"institution_name": "X", "seed_urls": "https://a.org junk https://b.org"}])
check("seed_urls keeps only things that look like URLs", seed_rows[0]["seed_urls"], "https://a.org https://b.org")

try:
    ib.rows_from_csv_text("")
    raised = False
except ib.ImportError_:
    raised = True
check("an empty file fails loudly instead of crashing", raised, True)


# ---- merged/stacked-cell splitting (_expand_merged_rows) ----
single_line_rows = [{"institution_name": "A", "contact_name": "X"}, {"institution_name": "B", "contact_name": "Y"}]
check("single-line rows pass through _expand_merged_rows unchanged",
      ib._expand_merged_rows(single_line_rows), single_line_rows)

aligned = [{"institution_name": "A\nB\nC", "contact_name": "X\nY\nZ"}]
aligned_out = ib._expand_merged_rows(aligned)
check("a cleanly-aligned 3-line stacked block splits into 3 rows", len(aligned_out), 3)
check("aligned split pairs fields positionally",
      [r["institution_name"] for r in aligned_out], ["A", "B", "C"])
check("aligned split pairs the other field positionally too",
      [r["contact_name"] for r in aligned_out], ["X", "Y", "Z"])
check("every row born from a split is marked for review, even a cleanly-aligned one",
      all(r.get("flags") == "split from merged cell - verify pairing" for r in aligned_out), True)

mismatched = [{"institution_name": "A\nB\nC\nD\nE", "contact_name": "X\nY\nZ"}]
mismatched_out = ib._expand_merged_rows(mismatched)
check("a mismatched stacked block (5 names, 3 contacts) splits into 5 rows", len(mismatched_out), 5)
check("mismatched split blank-pads the shorter field",
      [r["contact_name"] for r in mismatched_out], ["X", "Y", "Z", "", ""])
check("mismatched split flags every resulting row for review",
      all(r.get("flags") == "split from merged cell - verify pairing" for r in mismatched_out), True)

shared = [{"institution_name": "A\nB\nC", "status_raw": "pending"}]
shared_out = ib._expand_merged_rows(shared)
check("a single shared one-line field is reused across every split row",
      [r["status_raw"] for r in shared_out], ["pending", "pending", "pending"])
check("reusing a shared one-line field still marks the split for review",
      all(r.get("flags") == "split from merged cell - verify pairing" for r in shared_out), True)

# ---- end to end: a real merged-cell CSV field through the whole import ----
merged_csv = 'University,Contact\n"Example College\nOther College","123\n456"\n'
merged_rows = ib._expand_merged_rows(ib.rows_from_csv_text(merged_csv))
merged_numbers = ib.build_rows(merged_rows)
check("end-to-end: a quoted multi-line CSV field becomes 2 separate institutes",
      [n["institution_name"] for n in merged_numbers], ["Example College", "Other College"])
check("end-to-end: row_ids are sequential across the expanded rows",
      [n["row_id"] for n in merged_numbers], ["r001", "r002"])
check("end-to-end: build_rows preserves the review flag _expand_merged_rows set, doesn't clobber it",
      all(n["flags"] == "split from merged cell - verify pairing" for n in merged_numbers), True)

mismatched_csv = 'University,Contact\n"Example College\nOther College\nThird College","123"\n'
mismatched_numbers = ib.build_rows(ib._expand_merged_rows(ib.rows_from_csv_text(mismatched_csv)))
check("end-to-end: a mismatched merge's review flag survives build_rows",
      all(n["flags"] == "split from merged cell - verify pairing" for n in mismatched_numbers), True)


# ---- institution_name vs a colliding generic "Name" column (real-world bug: a file with both
# "University" and a separate "Name" column for the contact's first name) ----
collision_headers = ib._map_headers(["University", "Erasmus Director", "Name", "Surname"])
check("'University' wins institution_name over a colliding 'Name' column",
      collision_headers["institution_name"], "University")

name_only_headers = ib._map_headers(["Name", "Email"])
check("'Name' is still used as institution_name when nothing more specific exists",
      name_only_headers["institution_name"], "Name")

# ---- --start/--limit must be measured against the file's real rows, before merged-cell
# expansion - otherwise an earlier stacked cell silently shifts what "row 200" means ----
pre_split_csv = ('University,Contact\n'
                  '"Stacked One\nStacked Two","1\n2"\n'      # row 1: will expand into 2 rows
                  'Row Three,3\nRow Four,4\nRow Five,5\n')
raw = ib.rows_from_csv_text(pre_split_csv)
sliced_before_expand = ib._expand_merged_rows(raw[1:3])   # what main() does: slice first, expand after
check("slicing before expansion keeps row numbers matching the real file, not the split-out count",
      [r["institution_name"] for r in sliced_before_expand], ["Row Three", "Row Four"])


# ---- first+last name combine fallback (real-world: "Name" + "Surname", no single contact column) ----
split_name_csv = "University,Name,Surname\nExample College,Anna,Theis\n"
split_rows = ib.rows_from_csv_text(split_name_csv)
check("Name + Surname combine into contact_name when there's no single contact-name column",
      split_rows[0]["contact_name"], "Anna Theis")

single_contact_csv = "University,Contact Name,Name,Surname\nExample College,Priority Contact,Anna,Theis\n"
single_rows = ib.rows_from_csv_text(single_contact_csv)
check("an existing single contact-name column wins over the first+last combine fallback",
      single_rows[0]["contact_name"], "Priority Contact")

# ---- real-world header spellings seen in an actual client sheet ----
real_world_csv = ("University,Erasmus Director,Name,Surname,Contact Work Email,"
                   "Contact Work Phone Number,Contact Generic Phone Number\n"
                   "Example College,Erasmus Coordinator,Anna,Theis,anna@example.edu,111,222\n")
rw = ib.build_rows(ib.rows_from_csv_text(real_world_csv))[0]
check("'Erasmus Director' column is recognized as designation", rw["designation"], "Erasmus Coordinator")
check("'Contact Work Email' is recognized as email", rw["email"], "anna@example.edu")
check("'Contact Work Phone Number' is recognized as phone_1", rw["phone_1"], "111")
check("'Contact Generic Phone Number' is recognized as phone_2", rw["phone_2"], "222")


# ---- one institute, several values in a cell: still one row ----
multi = ib._expand_merged_rows([{"institution_name": "IPAC", "email": "info@ipac.fr\ncm@ipac.fr",
                                 "seed_urls": "https://ipac.fr/\nhttps://facebook.com/ipac", "phone_1": "1\n\n2"}])
check("an institute with two emails in one cell is NOT split into two rows", len(multi), 1)
check("its values are kept, joined", (multi[0]["email"], multi[0]["phone_1"]), ("info@ipac.fr; cm@ipac.fr", "1; 2"))
check("and it is not flagged as a split", multi[0].get("flags"), None)
check("both links still reach the scraper as seed pages",
      ib.build_rows(multi)[0]["seed_urls"], "https://ipac.fr/ https://facebook.com/ipac")

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
