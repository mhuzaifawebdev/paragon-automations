"""Regression test for the container-boundary verification bug: a claim verified "yes" in an
earlier run must stay "yes" when a later run's merge() can't re-check it because the cited
page isn't in THIS run's cache (data/cache/ is git-ignored and container-local, so a multi-chunk
batch running across several GitHub Actions containers will see an empty cache for rows whose
pages were only ever fetched in an earlier, now-gone container).

Run: python tests/test_run_batch_merge.py
"""
import csv
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp())
os.environ["PARAGON_DATA"] = str(tmp)

sys.path.insert(0, str(ROOT / "scraper"))
import run_batch as rb  # noqa: E402

bad = 0


def check(name, got, want):
    global bad
    ok = got == want
    bad += not ok
    print(("ok   " if ok else "FAIL ") + f"{name}: got {got!r}, want {want!r}")


row = {"row_id": "r001", "institution_name": "Example University", "country_sheet": "Ireland",
       "phone_1": "", "contact_name": "", "designation": "", "email": ""}

res = {
    "row_id": "r001", "scraped_at": "2026-10-01T00:00:00Z",
    "contacts": {
        "institution_name": "Example University", "institution_type": "university",
        "official_website": "https://example.edu",
        "country": {"value": "Ireland", "source_url": "https://example.edu/about", "evidence_quote": "Located in Ireland"},
        "phone": {}, "contact": {
            "name": "Jane Doe", "designation": "Erasmus Coordinator", "email": "jane@example.edu",
            "source_url": "https://example.edu/contact", "evidence_quote": "Jane Doe, Erasmus Coordinator",
        }, "confidence": "high", "alternates": [],
    },
    "network": None, "network_skipped": "institution_type=university (no network call made in this fixture)",
}

(rb.RESULTS).mkdir(parents=True, exist_ok=True)
(rb.RESULTS / "r001.json").write_text(json.dumps(res), encoding="utf-8")

# Simulate a PRIOR run where the page WAS cached, so the claim was genuinely verified "yes" and
# committed. No scraper/data/cache entries are created here on purpose - this run's cache is empty,
# matching a fresh container that never fetched these pages itself.
prior_enriched = {
    "row_id": "r001", "contact_verified": "yes", "person_scraped": "Jane Doe",
    "contact_source_url": "https://example.edu/contact",
    "country_verified": "yes", "country_scraped": "Ireland",
    "country_source_url": "https://example.edu/about",
    "phone_verified": "", "phone_scraped": "", "phone_source_url": "",
}
with open(tmp / "institutions_enriched.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(prior_enriched.keys()))
    w.writeheader()
    w.writerow(prior_enriched)

rb.merge([row])

with open(tmp / "institutions_enriched.csv", newline="", encoding="utf-8-sig") as f:
    out = list(csv.DictReader(f))[0]

check("contact stays verified despite empty cache (reused prior result)", out["contact_verified"], "yes")
check("country stays verified despite empty cache (reused prior result)", out["country_verified"], "yes")
check("reused note says so, not a generic pass", "reused prior result" in out["contact_verify_note"], True)

# ---- partner_scope: International vs National, deterministic, never the model's own judgment ----
row2 = {"row_id": "r002", "institution_name": "Second College", "country_sheet": "Ireland",
        "phone_1": "", "contact_name": "", "designation": "", "email": ""}
res2 = {
    "row_id": "r002", "scraped_at": "2026-10-01T00:00:00Z",
    "contacts": {
        "institution_name": "Second College", "institution_type": "college",
        "official_website": "https://second.edu",
        "country": {"value": "Ireland", "source_url": "https://second.edu/about", "evidence_quote": "Located in Ireland"},
        "phone": {}, "contact": {}, "confidence": "low", "alternates": [],
    },
    "network": {
        "external_collaboration": [
            {"partner_name": "Uni of Lisbon", "partner_country": "Portugal", "partner_type": "university",
             "source_url": "https://second.edu/partners", "evidence_quote": "Uni of Lisbon"},
            {"partner_name": "Trinity College Dublin", "partner_country": "Ireland", "partner_type": "university",
             "source_url": "https://second.edu/partners", "evidence_quote": "Trinity College Dublin"},
            {"partner_name": "EAEC", "partner_country": "", "partner_type": "network",
             "source_url": "https://second.edu/partners", "evidence_quote": "EAEC"},
            {"partner_name": "Some School", "partner_country": "", "partner_type": "school",
             "source_url": "https://second.edu/partners", "evidence_quote": "Some School"},
        ],
        "internal_collaboration": [],
    },
    "network_skipped": "",
}
(rb.RESULTS / "r002.json").write_text(json.dumps(res2), encoding="utf-8")

rb.merge([row, row2])

with open(tmp / "partners.csv", newline="", encoding="utf-8-sig") as f:
    partner_rows = {r["partner_name"]: r for r in csv.DictReader(f)}

check("a partner in a different country is International",
      partner_rows["Uni of Lisbon"]["partner_scope"], "International")
check("a partner in the institute's own country is National",
      partner_rows["Trinity College Dublin"]["partner_scope"], "National")
check("a blank-country network/association is International (inherently cross-border)",
      partner_rows["EAEC"]["partner_scope"], "International")
check("a blank-country non-network partner is Unknown, not guessed either way",
      partner_rows["Some School"]["partner_scope"], "Unknown")

check("the word 'unknown' as a partner's country is not treated as a foreign country",
      rb.partner_scope("university", "unknown", "Ireland"), "Unknown")
check("a network whose country is 'unknown' is still international",
      rb.partner_scope("network", "Unknown", "Ireland"), "International")

shutil.rmtree(tmp, ignore_errors=True)

print(f"\n{9 - bad}/9 passed")
sys.exit(1 if bad else 0)
