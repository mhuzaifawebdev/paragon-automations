"""Shared campaign-state helpers: data/campaign_state.csv.

One row per institute (row_id), tracking which contact is current, how many
attempts have been made, and whether it's eligible to be called again today.
This is the "database" the free-infra architecture calls for: a repo-committed
CSV, not a hosted database. Concurrent access isn't a concern at this volume
(one campaign run at a time, via a single GitHub Actions job).
"""
import csv
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "data" / "campaign_state.csv"
NUMBERS_PATH = ROOT / "data" / "numbers_clean.csv"
ENRICHED_PATH = ROOT / "data" / "institutions_enriched.csv"

FIELDS = ["row_id", "current_contact_name", "current_contact_phone", "status",
          "attempts_total", "attempts_today", "last_attempt_date", "last_call_id",
          "last_outcome", "already_tried", "escalation_level", "updated_at"]

STATUS_PENDING, STATUS_CALLING, STATUS_DONE, STATUS_EXHAUSTED = "pending", "calling", "done", "exhausted"


def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _read_csv(path):
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def load():
    """Returns {row_id: state_dict}, seeding any institute from numbers_clean.csv
    that has no state row yet (first time it's ever considered)."""
    existing = {r["row_id"]: r for r in _read_csv(STATE_PATH)}
    numbers = {r["row_id"]: r for r in _read_csv(NUMBERS_PATH)}
    for row_id, n in numbers.items():
        if row_id in existing:
            continue
        existing[row_id] = {
            "row_id": row_id, "current_contact_name": n.get("contact_name") or "",
            "current_contact_phone": n.get("phone_1") or "", "status": STATUS_PENDING,
            "attempts_total": "0", "attempts_today": "0", "last_attempt_date": "",
            "last_call_id": "", "last_outcome": "", "already_tried": "",
            "escalation_level": "0", "updated_at": "",
        }
    return existing


def save(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_PATH, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for row_id in sorted(state):
            w.writerow({k: state[row_id].get(k, "") for k in FIELDS})


def reset_daily_counters_if_new_day(state):
    """attempts_today resets when last_attempt_date isn't today - call once per run."""
    today = _today()
    for r in state.values():
        if r.get("last_attempt_date") != today:
            r["attempts_today"] = "0"
    return state


def eligible(row, limits):
    if row["status"] in (STATUS_DONE, STATUS_EXHAUSTED):
        return False
    if int(row.get("attempts_today") or 0) >= limits["max_attempts_per_contact_per_day"]:
        return False
    tried = [t for t in (row.get("already_tried") or "").split(";") if t]
    if len(tried) >= limits["max_contacts_per_institute_per_day"]:
        return False
    return True


def record_call_placed(row, call_id):
    row["status"] = STATUS_CALLING
    row["attempts_total"] = str(int(row.get("attempts_total") or 0) + 1)
    row["attempts_today"] = str(int(row.get("attempts_today") or 0) + 1)
    row["last_attempt_date"] = _today()
    row["last_call_id"] = call_id
    tried = set(t for t in (row.get("already_tried") or "").split(";") if t)
    tried.add(row["current_contact_phone"])
    row["already_tried"] = ";".join(sorted(tried))
    row["updated_at"] = datetime.now(timezone.utc).isoformat()


def record_call_result(row, outcome, answered):
    row["last_outcome"] = outcome
    if answered and outcome not in ("none", "needs_review", "needs_arrival_date"):
        row["status"] = STATUS_DONE
    elif not answered:
        row["status"] = STATUS_PENDING   # eligible again next run, up to daily limits
    row["updated_at"] = datetime.now(timezone.utc).isoformat()


def escalate_contact(row, new_name, new_phone, limits):
    """After a no-answer, move to the next contact found by find-erasmus-contacts.
    Returns False (and marks the row exhausted) if the distinct-contact limit is hit."""
    tried = [t for t in (row.get("already_tried") or "").split(";") if t]
    if new_phone in tried or len(tried) >= limits["max_contacts_per_institute_per_day"]:
        row["status"] = STATUS_EXHAUSTED
        return False
    row["current_contact_name"] = new_name
    row["current_contact_phone"] = new_phone
    row["escalation_level"] = str(int(row.get("escalation_level") or 0) + 1)
    row["status"] = STATUS_PENDING
    return True
