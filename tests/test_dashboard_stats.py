"""Offline test for scripts/build_dashboard_stats.py. Run: python tests/test_dashboard_stats.py"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import build_dashboard_stats as bds  # noqa: E402

results = []


def check(name, got, want):
    ok = got == want
    results.append(ok)
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f": got {got!r}, want {want!r}"))


d = Path(tempfile.mkdtemp())
(d / "b1" / "scrape_results").mkdir(parents=True)
(d / "uploads").mkdir()
(d / "b1" / "progress.json").write_text(json.dumps({"status": "running", "total_rows": 3, "done": 2, "generated_at": "2026-10-20T05:00:00Z"}))
r = lambda day, contact, partners, notes="": {"scraped_at": day + "T10:00:00Z", "contacts": {"contact": contact, "notes": notes},
                                              "network": {"external_collaboration": [{}] * partners, "internal_collaboration": []}}
(d / "b1" / "scrape_results" / "r001.json").write_text(json.dumps(r("2026-10-19", {"name": "A", "email": "a@x.edu"}, 4)))
(d / "b1" / "scrape_results" / "r002.json").write_text(json.dumps(r("2026-10-20", None, 0, "Website not found (x)")))
s = bds.build(d)
check("totals count institutes, contacts, emails, partners and missing websites",
      {k: s["totals"][k] for k in ("institutes", "with_contact", "with_email", "partners", "no_website", "batches")},
      {"institutes": 2, "with_contact": 1, "with_email": 1, "partners": 4, "no_website": 1, "batches": 1})
check("one row per day", [(x["day"], x["institutes"]) for x in s["days"]], [("2026-10-19", 1), ("2026-10-20", 1)])
check("the batch list uses progress.json for status and progress", (s["batches"][0]["status"], s["batches"][0]["done"], s["batches"][0]["total_rows"]), ("running", 2, 3))
check("no names or emails are copied into the figures", "a@x.edu" in json.dumps(s) or '"A"' in json.dumps(s), False)

# a generic inbox counts as an email too, so 'With an email' = work email OR generic inbox
g = r("2026-10-20", None, 2)
g["contacts"].update(generic_email="info@y.edu", institution_name=' "Y  College " ', country={"value": "Malta"})
g["network"]["internal_collaboration"] = [{}] * 3
(d / "b1" / "scrape_results" / "r003.json").write_text(json.dumps(g))
rows = []
s = bds.build(d, rows=rows)
check("email = work or generic, with the split kept",
      {k: s["totals"][k] for k in ("with_email", "personal_email", "generic_email", "with_website")},
      {"with_email": 2, "personal_email": 1, "generic_email": 1, "with_website": 2})
check("one Institutes line per result, name cleaned, counts only",
      sorted((x["name"], x["country"], x["campuses"], x["partners"], x["website"]) for x in rows),
      [("", "", 0, 0, 0), ("", "", 0, 4, 1), ("Y College", "Malta", 3, 2, 1)])
check("the Institutes lines carry no email", "info@y.edu" in json.dumps(rows), False)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
