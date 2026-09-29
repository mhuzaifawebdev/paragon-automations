"""Offline tests for agent/pause_monitor.py and agent/zadarma_client.py. No real HTTP calls, no
Zadarma account needed, no cost. Run: python tests/test_pause_monitor.py
"""
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))
os.environ.setdefault("ZADARMA_API_KEY", "test-key")
os.environ.setdefault("ZADARMA_API_SECRET", "test-secret")
import zadarma_client as zc  # noqa: E402
import pause_monitor as pm  # noqa: E402
import notify_email  # noqa: E402

results = []


def check(name, got, want):
    ok = got == want
    results.append(ok)
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f": got {got!r}, want {want!r}"))


# ---- zadarma_client: signing shape and response parsing, against a fake transport ----
calls = []


def fake_get(url, auth_header):
    calls.append((url, auth_header))
    if "/status/" in url:
        return {"status": "success", "is_online": "true"}
    if "/internal/" in url and "status" not in url and "info" not in url:
        return {"status": "success", "numbers": [100, 101]}
    return {"status": "success", "balance": 1.5, "currency": "USD"}


check("balance() reads the balance field", zc.balance(_get_fn=fake_get)["balance"], 1.5)
check("extensions() returns the numbers list", zc.extensions(_get_fn=fake_get), [100, 101])
check("extension_status() maps 'true' to True", zc.extension_status(100, _get_fn=fake_get), True)
check("Authorization header has the key:signature shape", ":" in calls[0][1] and calls[0][1].startswith("test-key:"), True)


def fake_get_error(url, auth_header):
    raise zc.ZadarmaError("boom")


try:
    zc.extension_status(100, _get_fn=fake_get_error)
    raised = False
except zc.ZadarmaError:
    raised = True
check("a Zadarma API error propagates as ZadarmaError, not a crash", raised, True)


# ---- pause_monitor: shift-hours and alert logic ----
noon = datetime(2026, 9, 27, 10, 0, tzinfo=timezone.utc)   # 10:00 UTC
check("in_shift is True inside the configured window", pm.in_shift({"shift_start_utc": "07:00", "shift_end_utc": "16:00"}, noon), True)
check("in_shift is False outside the configured window", pm.in_shift({"shift_start_utc": "07:00", "shift_end_utc": "16:00"},
      datetime(2026, 9, 27, 20, 0, tzinfo=timezone.utc)), False)


def get_offline(url, auth_header):
    if "/status/" in url:
        return {"status": "success", "is_online": "false"}
    return {"status": "success", "numbers": [100]}


cfg = {"shift_start_utc": "00:00", "shift_end_utc": "23:59", "idle_minutes": 15, "extensions": {"100": {"manager": "ops"}}}
events = pm.check(cfg, get_fn=get_offline, dry_run=True, now=noon)
check("an offline extension during shift hours with work queued is flagged", [e[2] for e in events],
      ["offline during shift hours with work still queued"])
check("the manager label from config is carried into the alert", [e[1] for e in events], ["ops"])

events_again = pm.check(cfg, get_fn=get_offline, dry_run=True, now=noon)
check("dry-run never writes state, so the same offline extension is flagged again immediately", len(events_again), 1)


def get_online(url, auth_header):
    if "/status/" in url:
        return {"status": "success", "is_online": "true"}
    return {"status": "success", "numbers": [100]}


narrow = {**cfg, "shift_start_utc": "07:00", "shift_end_utc": "16:00"}
check("an offline extension outside shift hours is never flagged",
      pm.check(narrow, get_fn=get_offline, dry_run=True, now=datetime(2026, 9, 27, 20, 0, tzinfo=timezone.utc)), [])


# ---- notify_email: no real SMTP server, ever, in this suite ----
class FakeSMTP:
    sent = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port

    def __enter__(self): return self
    def __exit__(self, *a): return False
    def starttls(self): pass
    def login(self, user, pw): FakeSMTP.sent.append(("login", user, pw))
    def send_message(self, msg): FakeSMTP.sent.append(("sent", msg["To"], msg["Subject"]))


os.environ.update(SMTP_HOST="smtp.example.com", SMTP_PORT="587", SMTP_USER="u@example.com",
                  SMTP_PASSWORD="pw", SMTP_FROM="alerts@example.com")
FakeSMTP.sent = []
ok = notify_email.send_email(["a@x.com", "", "b@x.com", "a@x.com"], "Subj", "Body", _smtp_cls=FakeSMTP)
check("send_email reports success when the fake SMTP server accepts the message", ok, True)
check("blank/duplicate recipients are dropped before sending", FakeSMTP.sent[-1][1], "a@x.com, b@x.com")
check("send_email actually calls login with the configured credentials", FakeSMTP.sent[0], ("login", "u@example.com", "pw"))

check("send_email with no real recipients returns False, tries nothing", notify_email.send_email([], "s", "b"), False)

del os.environ["SMTP_HOST"]
check("send_email returns False (not an exception) when SMTP_HOST isn't configured",
      notify_email.send_email(["a@x.com"], "s", "b"), False)


def raising_smtp(*a, **k):
    raise ConnectionRefusedError("no such host")


os.environ["SMTP_HOST"] = "smtp.example.com"
check("a real send failure is swallowed, not raised", notify_email.send_email(["a@x.com"], "s", "b", _smtp_cls=raising_smtp), False)
del os.environ["SMTP_HOST"]


# ---- pause_monitor: an alert emails both the operator and the shared manager address ----
# dry_run=False writes state/alert CSVs for real, so redirect those paths to a temp location -
# this test must never touch the real data/extension_state.csv or data/alerts.csv.
import tempfile  # noqa: E402
real_state_path, real_alerts_path = pm.STATE_PATH, pm.ALERTS_PATH
tmpdir = Path(tempfile.mkdtemp())
pm.STATE_PATH, pm.ALERTS_PATH = tmpdir / "extension_state.csv", tmpdir / "alerts.csv"
try:
    sent_calls = []
    cfg_email = {"shift_start_utc": "00:00", "shift_end_utc": "23:59", "idle_minutes": 15,
                "manager_email": "boss@example.com",
                "extensions": {"100": {"manager": "Nadia", "email": "nadia@example.com"}}}
    pm.check(cfg_email, get_fn=get_offline, dry_run=False,
            email_fn=lambda to, subj, body: sent_calls.append((sorted(to), subj)), now=noon)
    check("a real (non-dry-run) alert emails the operator and the shared manager address",
          sent_calls and sorted(sent_calls[0][0]) == ["boss@example.com", "nadia@example.com"], True)

    sent_calls.clear()
    cfg_no_manager = {**cfg_email, "manager_email": "REPLACE_ME"}
    pm.check(cfg_no_manager, get_fn=get_offline, dry_run=False,
            email_fn=lambda to, subj, body: sent_calls.append(sorted(to)),
            now=datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc))
    check("'REPLACE_ME' manager_email is treated as unset, not emailed literally",
          "REPLACE_ME" not in (sent_calls[0] if sent_calls else []), True)
finally:
    pm.STATE_PATH, pm.ALERTS_PATH = real_state_path, real_alerts_path
    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)


print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
