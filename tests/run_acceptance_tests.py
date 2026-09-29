"""Run pgi-score-call + classify-outcome against every fixture in
tests/labelled_calls/ and check the result against each fixture's `expected`
block. This is the acceptance test the developer handover PDF calls for
(section 14) - run it whenever the persona, scenarios, or either skill changes.

    python tests/run_acceptance_tests.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(ROOT / "data"))
from process_call import call_claude, parse_json  # noqa: E402
from classify_outcome import classify  # noqa: E402

FIXTURES = ROOT / "tests" / "labelled_calls"


def run_fixture(path):
    fx = json.loads(path.read_text(encoding="utf-8"))
    call_id, transcript, expected = fx["call_id"], fx["transcript"], fx["expected"]

    if len(transcript.strip()) <= 40:
        got = {"answered": False, "outcome": "none", "total": 0}
    else:
        score = parse_json(call_claude(
            f"Use the pgi-score-call skill on this transcript and reply with the JSON object only.\n\n"
            f"call_id: {call_id}\n\n{transcript}"))
        facts = parse_json(call_claude(
            f"Use the classify-outcome skill on this transcript and reply with the JSON object only.\n\n"
            f"call_id: {call_id}\n\n{transcript}"))
        label = classify(facts.get("interested", False), facts.get("student_arrival_months"),
                         facts.get("meeting_booked", False))
        got = {"answered": facts.get("answered", True), "outcome": label, "total": score.get("total")}

    checks = []
    checks.append(("answered", got["answered"] == expected["answered"]))
    checks.append(("outcome", got["outcome"] == expected["outcome"]))
    if got["total"] is not None:
        checks.append(("total_in_range",
                       expected["total_min"] <= got["total"] <= expected["total_max"]))
    passed = all(ok for _, ok in checks)
    return passed, got, checks


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    fixtures = sorted(FIXTURES.glob("*.json"))
    if not fixtures:
        print("no fixtures in tests/labelled_calls/")
        raise SystemExit(1)

    results = []
    for f in fixtures:
        try:
            passed, got, checks = run_fixture(f)
        except Exception as e:
            print(f"{f.stem}: ERROR - {e}")
            results.append(False)
            continue
        status = "PASS" if passed else "FAIL"
        print(f"{f.stem}: {status}  got={got}")
        for name, ok in checks:
            if not ok:
                print(f"   {name} did not match expected")
        results.append(passed)

    n_pass = sum(results)
    print(f"\n{n_pass}/{len(results)} fixtures passed")
    raise SystemExit(0 if n_pass == len(results) else 1)


if __name__ == "__main__":
    main()
