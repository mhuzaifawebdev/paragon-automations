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
nosite = r("2026-10-20", None, 0, "Website not found (x)")
nosite["contacts"]["institution_name"] = "Z School"
(d / "b1" / "scrape_results" / "r002.json").write_text(json.dumps(nosite))
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
      [("", "", 0, 4, 1), ("Y College", "Malta", 3, 2, 1), ("Z School", "", 0, 0, 0)])
check("the Institutes lines carry no email", "info@y.edu" in json.dumps(rows), False)

bad = r("2026-10-20", None, 0, "Website not found (no website source available)")
bad["contacts"]["institution_name"] = "34986419899"
(d / "b1" / "scrape_results" / "r004.json").write_text(json.dumps(bad))
check("a broken sheet row (a phone number as the name) is left out of the figures",
      bds.build(d)["totals"]["institutes"], s["totals"]["institutes"])

# ---- a re-check that finds a contact on a site already known counts once
import build_dashboard_stats as bd2  # noqa: E402
t2 = {"with_contact": 0, "personal_email": 0, "generic_email": 0, "with_email": 0, "no_website": 0, "with_website": 0, "partners": 0, "campuses": 0}
found = {"with_contact": 1, "with_email": 1, "personal_email": 1, "generic_email": 0}
ls = [{"name": "School A", "website": 1, "batch": "b1"}, {"name": "School A", "website": 1, "batch": "b2"}, {"name": "School B", "website": 1, "batch": "b1"},
      {"name": "School C", "website": 1, "batch": "b1"}, {"name": "School D", "website": 1, "batch": "b1"},
      {"name": "School A", "website": 1, "batch": "website-recheck-x", "found": found, "partners": 0, "campuses": 0, "type": "", "country": ""},
      {"name": "School A", "website": 1, "batch": "website-recheck-y", "found": found, "partners": 0, "campuses": 0, "type": "", "country": ""},
      {"name": "School B", "website": 1, "batch": "website-recheck-x", "found": found, "partners": 0, "campuses": 0, "type": "", "country": ""},
      {"name": "School C", "website": 1, "batch": "website-recheck-x", "found": {**found, "with_contact": 0, "personal_email": 0, "generic_email": 1}, "partners": 0, "campuses": 0, "type": "", "country": ""},
      {"name": "School D", "website": 1, "batch": "website-recheck-x", "found": found, "partners": 0, "campuses": 0, "type": "", "country": ""}]
bd2.apply_rechecks(t2, ls, {bd2._key("School A"): 0, bd2._key("School B"): 1, bd2._key("School C"): 0})
check("every row of a name gets the contact, once; an old inbox is not counted twice; a name not sent gets nothing",
      (t2["with_contact"], t2["personal_email"], t2["with_email"], t2["generic_email"]), (3, 3, 3, 1))

t3 = {"with_contact": 0, "personal_email": 0, "generic_email": 0, "with_email": 0, "no_website": 0, "with_website": 0, "partners": 0, "campuses": 0}
ls3 = [{"name": "School E", "website": 1, "batch": "b1"}, {"name": "School E", "website": 1, "batch": "website-recheck-x", "found": found, "partners": 0, "campuses": 0, "type": "", "country": ""}]
bd2.apply_rechecks(t3, ls3, {bd2._key("School E"): 0}, {bd2._key("School E"): 1, bd2._key("School F"): 2})
check("a sheet's contact counts, and only once if the scraper also found one", t3["with_contact"], 3)

# ---- a batch imported wrongly and scraped again: one of the two counts, never both
import json as _j2, tempfile as _t2
from pathlib import Path as _P2
rd = _P2(_t2.mkdtemp())
(rd / "old").mkdir(); (rd / "new").mkdir()
(rd / "replaced_batches.json").write_text(_j2.dumps({"old": "new"}))
(rd / "new" / "progress.json").write_text(_j2.dumps({"status": "running"}))
check("while the rebuild runs, the old batch counts and the rebuild does not", bd2.hidden_batches(rd), {"new"})
(rd / "new" / "progress.json").write_text(_j2.dumps({"status": "done"}))
check("once it has finished, only the rebuild counts", bd2.hidden_batches(rd), {"old"})

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
