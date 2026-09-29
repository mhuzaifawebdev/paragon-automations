"""The dialer loop: place calls for eligible institutes, respecting config.yaml's
daily limits. This is what a GitHub Actions cron runs (see
.github/workflows/run-campaign.yml) in place of a human operator dialing manually.

    python agent/run_campaign.py --dry-run --limit 5    # exercise the whole loop, place nothing
    python agent/run_campaign.py --limit 20              # place real calls (needs .env + config.yaml filled in)

Calls are async: this script only PLACES calls and records that an attempt was made.
The outcome (answered / no answer, score, classified label) is not known until Vapi's
end-of-call-report webhook arrives, which agent/process_call.py handles separately.
A no-answer is picked up on the NEXT run of this script (status goes back to
'pending'), not synchronously - there is no live wait-for-answer here.
"""
import argparse
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))
import state as st  # noqa: E402
from vapi_call import place_call, CallBlocked  # noqa: E402


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=20, help="max calls to place this run")
    ap.add_argument("--dry-run", action="store_true", help="print what would be called; place nothing")
    ap.add_argument("--only", help="comma-separated row_ids to restrict to (for testing)")
    a = ap.parse_args()

    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    limits = cfg["limits"]

    state = st.reset_daily_counters_if_new_day(st.load())
    row_ids = [x.strip() for x in a.only.split(",")] if a.only else sorted(state)

    placed, skipped_ineligible, blocked = 0, 0, None
    for row_id in row_ids:
        if placed >= a.limit:
            break
        row = state[row_id]
        if not st.eligible(row, limits):
            skipped_ineligible += 1
            continue
        if not row["current_contact_phone"]:
            continue

        try:
            result, payload, source = place_call(row_id, row["current_contact_phone"], cfg, dry_run=a.dry_run)
        except CallBlocked as e:
            blocked = str(e)
            break   # config isn't ready - stop the whole run, don't burn through every row with the same error
        except RuntimeError as e:
            print(f"{row_id}: FAILED to place call: {e}", file=sys.stderr)
            continue

        call_id = "dry-run" if a.dry_run else (result or {}).get("id", "unknown")
        st.record_call_placed(row, call_id)
        placed += 1
        print(f"{row_id}: called {row['current_contact_phone']} ({row['current_contact_name'] or 'no name'}) "
              f"-> call_id={call_id}")

    if not a.dry_run:
        st.save(state)

    print(f"\nplaced={placed} skipped_ineligible={skipped_ineligible} "
          f"dry_run={a.dry_run} state_saved={not a.dry_run}")
    if blocked:
        raise SystemExit(f"Stopped early: {blocked}. Use --dry-run to test the loop without these, "
                          f"or fill in config.yaml / .env to place real calls.")


if __name__ == "__main__":
    main()
