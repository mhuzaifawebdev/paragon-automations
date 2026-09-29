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

seed_rows = ib.build_rows([{"institution_name": "X", "seed_urls": "https://a.org junk https://b.org"}])
check("seed_urls keeps only things that look like URLs", seed_rows[0]["seed_urls"], "https://a.org https://b.org")

try:
    ib.rows_from_csv_text("")
    raised = False
except ib.ImportError_:
    raised = True
check("an empty file fails loudly instead of crashing", raised, True)


print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
