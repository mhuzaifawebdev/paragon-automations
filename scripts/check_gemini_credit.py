"""Asks Google whether the paid Gemini key still works, and writes the answer to data_hei/gemini_status.json for the
dashboard's Budget tracker (its "credit left" figure is only our estimate; Google's own answer comes first).

    python scripts/check_gemini_credit.py          # check now
    (scripts/queue_tick.py runs it at most once an hour)

One tiny request (a few tokens, far below a cent). States: ok, depleted (Google: "prepayment credits are depleted"),
error (anything else, e.g. no network), no_key.
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATUS = ROOT / "data_hei" / "gemini_status.json"
MODEL = "gemini-3.1-flash-lite"


def key():
    k = os.environ.get("GEMINI_PAID_API_KEY")
    env = ROOT / ".env"
    if not k and env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*GEMINI_PAID_API_KEY\s*=\s*(.*)$", line)
            if m:
                k = m.group(1).strip().strip('"').strip("'")
    return k


def check():
    k = key()
    if not k:
        return {"state": "no_key", "message": "no paid Gemini key in this environment"}
    body = json.dumps({"contents": [{"role": "user", "parts": [{"text": "ok"}]}],
                       "generationConfig": {"maxOutputTokens": 1}}).encode()
    req = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={k}",
                                 data=body, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=60).read()
        return {"state": "ok", "message": "the paid key works"}
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("error", {}).get("message", "")
        except Exception:
            msg = ""
        state = "depleted" if e.code == 402 or "depleted" in msg.lower() else "error"
        return {"state": state, "message": f"HTTP {e.code}: {msg[:200]}"}
    except Exception as e:
        return {"state": "error", "message": str(e)[:200]}


def run(min_minutes=0):
    """Checks unless the last check is younger than min_minutes. Returns the status."""
    try:
        old = json.loads(STATUS.read_text(encoding="utf-8"))
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(old["checked"])).total_seconds() / 60
        if age < min_minutes:
            return old
    except Exception:
        pass
    st = check()
    if st["state"] == "no_key":
        return st
    st["checked"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    STATUS.write_text(json.dumps(st), encoding="utf-8")
    return st


if __name__ == "__main__":
    s = run()
    print(f"Gemini paid key: {s['state']} ({s.get('message', '')})")
    sys.exit(0)
