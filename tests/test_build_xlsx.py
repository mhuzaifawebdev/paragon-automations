"""Offline tests for scraper2/build_xlsx.py's Partners-sheet reorder and international-first sort.
Run: python tests/test_build_xlsx.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scraper2"))
import build_xlsx as bx  # noqa: E402

results = []


def check(name, got, want):
    ok = got == want
    results.append(ok)
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f": got {got!r}, want {want!r}"))


header = ["row_id", "institution", "partner_name", "partner_country", "partner_type",
          "mobility_type", "source_url", "evidence_quote", "confidence", "partner_scope",
          "verified", "verify_note", "scraped_at"]
rows = [
    ["r001", "A College", "National Uni", "Ireland", "university", "study",
     "https://a.edu/p", "quote", "0.9", "National", "yes", "", "2026-10-01"],
    ["r001", "A College", "Foreign Uni", "Portugal", "university", "study",
     "https://a.edu/p", "quote", "0.9", "International", "yes", "", "2026-10-01"],
    ["r001", "A College", "EAEC", "", "network", "unknown",
     "https://a.edu/p", "quote", "0.8", "International", "yes", "", "2026-10-01"],
    ["r002", "B College", "Another National", "Spain", "university", "study",
     "https://b.edu/p", "quote", "0.9", "National", "yes", "", "2026-10-01"],
    ["r002", "B College", "Another Foreign", "France", "university", "study",
     "https://b.edu/p", "quote", "0.9", "International", "yes", "", "2026-10-01"],
]

new_header, new_rows = bx._reorder_partners(header, rows)
check("partner_name comes before source_url after reorder",
      new_header.index("partner_name") < new_header.index("source_url"), True)
check("partner_scope comes before source_url after reorder",
      new_header.index("partner_scope") < new_header.index("source_url"), True)
check("reorder drops no columns", set(new_header), set(header))
check("reorder doesn't change row count", len(new_rows), len(rows))

sorted_rows = bx._sort_partners(new_header, new_rows)
name_i, rid_i, scope_i = new_header.index("partner_name"), new_header.index("row_id"), new_header.index("partner_scope")
r001_names = [r[name_i] for r in sorted_rows if r[rid_i] == "r001"]
r002_names = [r[name_i] for r in sorted_rows if r[rid_i] == "r002"]
check("r001: International partners (Foreign Uni, EAEC) come before National (National Uni)",
      r001_names, ["Foreign Uni", "EAEC", "National Uni"])
check("r002: International partner comes before National",
      r002_names, ["Another Foreign", "Another National"])
check("each institute's block stays contiguous (not interleaved with the other institute's rows)",
      [r[rid_i] for r in sorted_rows], ["r001", "r001", "r001", "r002", "r002"])

# Stability: two International partners for the same institute keep their original relative order.
stable_header = ["row_id", "partner_name", "partner_scope"]
stable_rows = [
    ["r001", "First Found", "International"],
    ["r001", "Second Found", "International"],
]
stable_sorted = bx._sort_partners(stable_header, stable_rows)
check("equal-scope rows keep their original (scraped) relative order",
      [r[1] for r in stable_sorted], ["First Found", "Second Found"])


print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
