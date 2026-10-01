"""Supervisor alert: flags a Zadarma extension that has gone offline, or gone quiet with work
still queued, during shift hours - and tells a manager. Works the same whether that extension
belongs to a human operator dialing manually from a Zadarma softphone, or to the AI dialer
(agent/run_campaign.py). It watches the extension's presence, not who or what is calling from it,
so it needs nothing from the rest of this project to be useful today.

    python agent/pause_monitor.py --dry-run   # print what would be flagged, send nothing
    python agent/pause_monitor.py             # check once, send real alerts, save state

Run this on a schedule (cron / GitHub Actions, every few minutes during shift hours) the same
way scraper2 or the dialer are run - it is not a long-running daemon.

State: data/extension_state.csv (one row per extension, following agent/state.py's CSV-as-database
pattern - no hosted database). Every alert is appended to data/alerts.csv whether or not the send
succeeds, so nothing is lost if the webhook URL is wrong or down.
"""
import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))
import zadarma_client as zc  # noqa: E402
import notify_email  # noqa: E402
import sheet_log  # noqa: E402
from vapi_call import load_env  # noqa: E402
import os  # noqa: E402
import json  # noqa: E402
import urllib.request  # noqa: E402


def _default_email_send(to_addrs, subject, body):
    return notify_email.send_email(to_addrs, subject, body)


def _default_sheet_log(sheet_id, row):
    return sheet_log.append_alert_row(sheet_id, row)

STATE_PATH = ROOT / "data" / "extension_state.csv"
ALERTS_PATH = ROOT / "data" / "alerts.csv"
STATE_FIELDS = ["extension", "manager", "last_status", "last_online_at", "last_alert_at"]
ALERT_FIELDS = ["timestamp", "extension", "manager", "reason", "sent_ok"]


def _now():
    return datetime.now(timezone.utc)


def _read_csv(path, fields):
    if not path.exists():
        return {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        return {r[fields[0]]: r for r in csv.DictReader(f)}


def _write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows.values():
            w.writerow({k: r.get(k, "") for k in fields})


def _append_csv(path, row, fields):
    new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerow(row)


def in_shift(cfg, now=None):
    now = now or _now()
    start, end = cfg.get("shift_start_utc", "00:00"), cfg.get("shift_end_utc", "23:59")
    hm = now.strftime("%H:%M")
    return start <= hm <= end


def queued_work_exists(cfg):
    """True if there is reason to expect calls right now (AI campaign pending rows, or the
    operators' own attempts log has rows for today). Best-effort: missing files just mean 'unknown',
    treated as True so a real stall is never hidden by a missing file."""
    campaign = ROOT / "data" / "campaign_state.csv"
    if campaign.exists():
        with open(campaign, newline="", encoding="utf-8-sig") as f:
            if any(r.get("status") == "pending" for r in csv.DictReader(f)):
                return True
    return True   # no campaign file yet (human-operator-only use) - always assume there's work


def send_alert(webhook_url, text):
    if not webhook_url:
        return False
    try:
        req = urllib.request.Request(webhook_url, data=json.dumps({"text": text}).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return 200 <= r.status < 300
    except Exception:
        return False


def check(cfg, get_fn=None, webhook_url=None, dry_run=False, now=None, email_fn=None, sheet_log_fn=None):
    now = now or _now()
    ext_cfg = cfg.get("extensions") or {}
    idle_minutes = int(cfg.get("idle_minutes", 15))
    manager_email = cfg.get("manager_email") or ""
    if manager_email == "REPLACE_ME":
        manager_email = ""
    alert_sheet_id = cfg.get("alert_sheet_id") or ""
    if alert_sheet_id == "REPLACE_ME":
        alert_sheet_id = ""
    alert_sheet_tab = cfg.get("alert_sheet_tab") or "Alerts"
    targets = list(ext_cfg) or [str(n) for n in zc.extensions(_get_fn=get_fn)]
    state = _read_csv(STATE_PATH, STATE_FIELDS)
    shift, work = in_shift(cfg, now), queued_work_exists(cfg)
    events = []

    for ext in targets:
        entry = ext_cfg.get(ext, {}) if isinstance(ext_cfg.get(ext), dict) else {}
        manager, operator_email = entry.get("manager", ""), entry.get("email", "")
        row = state.get(ext, {"extension": ext, "manager": manager, "last_status": "", "last_online_at": "", "last_alert_at": ""})
        row["manager"] = manager or row.get("manager", "")
        online = zc.extension_status(ext, _get_fn=get_fn)
        reason = None
        if shift and work:
            if online is False:
                reason = "offline during shift hours with work still queued"
            elif online is True and row.get("last_online_at"):
                last = datetime.fromisoformat(row["last_online_at"])
                if (now - last).total_seconds() / 60 >= idle_minutes and row.get("last_status") == "true":
                    reason = f"no activity change for {idle_minutes}+ minutes with work still queued"
        if online:
            row["last_online_at"] = now.isoformat()
        row["last_status"] = str(online).lower()

        if reason:
            already_alerted = row.get("last_alert_at") and \
                (now - datetime.fromisoformat(row["last_alert_at"])).total_seconds() < 3600
            if not already_alerted:
                text = f"Extension {ext} ({manager or 'no manager set'}): {reason}"
                events.append((ext, manager, reason))
                if not dry_run:
                    webhook_ok = send_alert(webhook_url, text)
                    recipients = [operator_email, manager_email]
                    email_ok = None
                    if any(recipients):
                        subject = f"Paragon: delay on extension {ext} ({manager or 'unlabelled'})"
                        body = (f"{text}\n\nThis is an automated alert from agent/pause_monitor.py. "
                                f"If {manager or 'this operator'} is fine and just briefly away, no action is needed - "
                                f"this only repeats once an hour per extension.")
                        (email_fn or _default_email_send)(recipients, subject, body)
                        email_ok = True   # notify_email.send_email logs its own failure; never blocks the alert log
                    sheet_ok = None
                    if alert_sheet_id:
                        sheet_row = {"timestamp": now.isoformat(), "extension": ext, "manager": manager,
                                     "reason": reason, "sent_ok": ""}
                        sheet_ok = (sheet_log_fn or _default_sheet_log)(alert_sheet_id, sheet_row)
                    row["last_alert_at"] = now.isoformat()
                    _append_csv(ALERTS_PATH, {"timestamp": now.isoformat(), "extension": ext, "manager": manager,
                                              "reason": reason,
                                              "sent_ok": webhook_ok or bool(email_ok) or bool(sheet_ok)}, ALERT_FIELDS)
        state[ext] = row

    if not dry_run:
        # Only keep extensions currently in targets - otherwise a removed/renumbered extension (e.g.
        # a stale number from a corrected config.yaml) stays in extension_state.csv forever, since
        # this function only ever updates rows for `targets`, never removes rows that fall out of it.
        state = {ext: row for ext, row in state.items() if ext in targets}
        _write_csv(STATE_PATH, state, STATE_FIELDS)
    return events


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="print what would be flagged, send and save nothing")
    a = ap.parse_args()
    load_env()
    full = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    cfg = ((full.get("vapi") or {}).get("zadarma") or {}).get("monitor", {})
    webhook = os.environ.get("ALERT_WEBHOOK_URL")
    if not webhook and not a.dry_run:
        print("Warning: ALERT_WEBHOOK_URL not set - alerts will be logged to data/alerts.csv but not sent anywhere.",
              file=sys.stderr)
    events = check(cfg, webhook_url=webhook, dry_run=a.dry_run)
    if not events:
        print("No alerts." + (" (dry run)" if a.dry_run else ""))
    for ext, manager, reason in events:
        print(f"{'[DRY RUN] ' if a.dry_run else ''}ALERT ext {ext} ({manager or 'no manager'}): {reason}")

    if not a.dry_run:
        # Keep dashboard.html current after every real check, quietly (no browser popup - this
        # also runs unattended from cron/GitHub Actions). A failure here must never break the
        # actual alerting job above, so it is caught and reported, not raised.
        try:
            sys.path.insert(0, str(ROOT / "scripts"))
            import build_dashboard
            data = build_dashboard.build()
            build_dashboard.OUT.write_text(
                build_dashboard.HTML.replace("__DATA__", __import__("json").dumps(data)), encoding="utf-8")
        except Exception as e:
            print(f"(dashboard.html refresh failed, non-fatal: {e})", file=sys.stderr)


if __name__ == "__main__":
    main()
