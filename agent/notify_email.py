"""Send a plain-text email via SMTP, using stdlib smtplib only (no new dependency). Used by
agent/pause_monitor.py to tell an operator and their manager about a delay, in addition to the
existing webhook alert.

Needs SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM in .env (see .env.example). If
SMTP_HOST isn't set, send_email() returns False without raising - callers already treat "not sent"
as non-fatal and keep logging to data/alerts.csv regardless (see pause_monitor.py's ALERT_FIELDS'
sent_ok column), so a missing/wrong SMTP setup never breaks the actual monitoring.

A Gmail account needs an "app password" (myaccount.google.com/apppasswords), not your normal
login password, if 2-step verification is on (it usually is) - a plain password will just fail
authentication.
"""
import os
import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))
from vapi_call import load_env  # noqa: E402


def send_email(to_addrs, subject, body, _smtp_cls=None):
    """to_addrs: list of email addresses (duplicates/blanks are fine, filtered here). Returns True
    only if the message was actually handed to an SMTP server; False for "not configured" or any
    send failure - never raises, so a bad mail setup can't take down the monitor calling this."""
    to_addrs = sorted({a.strip() for a in to_addrs if a and a.strip()})
    if not to_addrs:
        return False
    host = os.environ.get("SMTP_HOST")
    if not host:
        return False
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    from_addr = os.environ.get("SMTP_FROM") or user

    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, from_addr, ", ".join(to_addrs)
    msg.set_content(body)

    smtp_cls = _smtp_cls or smtplib.SMTP
    try:
        with smtp_cls(host, port, timeout=15) as s:
            s.starttls()
            if user and password:
                s.login(user, password)
            s.send_message(msg)
        return True
    except Exception as e:
        print(f"(email send failed, non-fatal: {e})", file=sys.stderr)
        return False


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    load_env()
    to = sys.argv[1] if len(sys.argv) > 1 else input("Send a test email to: ")
    ok = send_email([to], "Paragon monitor: test email", "This is a test from agent/notify_email.py.")
    print("sent" if ok else "NOT sent (see .env.example for SMTP_* settings, or the error above)")
