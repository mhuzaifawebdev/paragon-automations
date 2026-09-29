"""Unit tests for scraper/verify.py. Run: python tests/test_verify.py

Each case mirrors a real failure found when auditing the first scraper results
(paraphrased quote, email that was not on the page, source on another domain).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scraper"))
import verify as v  # noqa: E402

PAGE = """Open University of Cyprus - International Relations
Kazan Federal University (Ρωσία)
Universität Krems: Donau-Universität Krems
Contact: Colleen Legrand, Erasmus coordinator
Tel: +353 (0)21 432 6100
Email: erasmus@example.edu
"""

cases = [
    # (name, actual, expected)
    ("quote verbatim", v.quote_in_text("Kazan Federal University (Ρωσία)", PAGE), True),
    ("quote: case/whitespace ignored", v.quote_in_text("kazan   federal\nuniversity (ρωσία)", PAGE), True),
    ("quote: accents ignored", v.quote_in_text("Donau-Universitat Krems", PAGE), True),
    ("quote: paraphrase rejected", v.quote_in_text("Kazan University is a partner of OUC", PAGE), False),
    ("quote: ellipsis fragments in order", v.quote_in_text("Colleen Legrand ... Erasmus coordinator", PAGE), True),
    ("quote: ellipsis fragments out of order", v.quote_in_text("Erasmus coordinator ... Colleen Legrand", PAGE), False),
    ("quote: empty rejected", v.quote_in_text("", PAGE), False),
    ("email present", v.email_in_text("erasmus@example.edu", PAGE), True),
    ("email NOT on page (inferred) rejected", v.email_in_text("c.legrand@example.edu", PAGE), False),
    ("phone with formatting differences", v.phone_in_text("+353 21 432 6100", PAGE), True),
    ("phone not on page", v.phone_in_text("+353 21 999 0000", PAGE), False),
    ("official: same host", v.is_official("https://www.ouc.ac.cy/a", "https://ouc.ac.cy"), True),
    ("official: subdomain", v.is_official("https://erasmus.ouc.ac.cy/x", "https://www.ouc.ac.cy"), True),
    ("official: other site, same TLD suffix rejected", v.is_official("https://other.ac.cy/x", "https://www.ouc.ac.cy"), False),
    ("official: legacy domain rejected", v.is_official("https://international.cit.ie/erasmus", "https://www.mtu.ie"), False),
    ("official: third-party profile rejected", v.is_official("https://www.linkedin.com/in/x", "https://www.mtu.ie"), False),
]

bad = 0
for name, got, want in cases:
    ok = got == want
    bad += not ok
    print(("ok   " if ok else "FAIL ") + f"{name}: got {got}, want {want}")
print(f"\n{len(cases) - bad}/{len(cases)} passed")
sys.exit(1 if bad else 0)
