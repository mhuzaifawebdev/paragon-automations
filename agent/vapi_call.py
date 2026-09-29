"""Build (and optionally place) a Vapi outbound call for one institute.

    python agent/vapi_call.py r035 --dry-run          # print the payload, call nothing
    python agent/vapi_call.py r035                     # place the real call (needs .env)

Reads config.yaml for the assistant's model/voice/transcriber/server_url/SIP-trunk
settings, and agent/fill_persona.py for the filled system prompt. --dry-run needs
no API key and no network access, so it is safe to run before Vapi credentials or
a Zadarma SIP trunk exist - use it to review the exact payload the real call will
send (this is what step 1-3 of the training workflow are for).
"""
import argparse
import csv
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))
from fill_persona import fill  # noqa: E402

VAPI_API = "https://api.vapi.ai/call"
# Vapi's edge (Cloudflare) blocks requests with no User-Agent as bot traffic (HTTP 403,
# "error code: 1010") even with a valid key - confirmed when first testing a real key.
VAPI_UA = "Mozilla/5.0 (compatible; ParagonCallingAgent/1.0)"


def load_env():
    env_path = ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def build_tools(v):
    """Vapi's documented shape for a custom function tool: {type: "function",
    function: {name, description, parameters}, server: {url}}. Each tool's own
    server URL lets different tools point at different backends if ever needed;
    here they all share tools_server_url. Verify this exact shape against a real
    Vapi assistant creation call during training step 5, same caveat as the rest
    of this integration."""
    tools_url = v.get("tools_server_url", "")
    return [
        {
            "type": "function",
            "function": {"name": t["name"], "description": t["description"], "parameters": t["parameters"]},
            "server": {"url": tools_url},
        }
        for t in v.get("tools", [])
    ]


def build_payload(row_id, cfg, to_number, override_phone_number_id=None,
                  skip_webhook=False, skip_tools=False):
    """skip_webhook/skip_tools omit serverUrl/tools entirely rather than requiring
    them configured - for supervised smoke-testing before the webhooks are deployed
    (e.g. `agent/process_call.py path/to/call.json` run manually afterwards instead
    of a live webhook). Never use these for a real unattended campaign call: without
    serverUrl nothing captures the transcript automatically, and without tools the
    agent can't do book_meeting/log_callback_request/escalate_to_human mid-call."""
    system_prompt, source = fill(row_id)
    v = cfg["vapi"]
    # Note: Zadarma connection is NOT checked here - it's a one-time setup
    # (agent/setup_zadarma_trunk.py) that produces VAPI_PHONE_NUMBER_ID, which IS
    # checked below via phoneNumberId. See config.yaml's `zadarma` section.
    checks = {"voice.voice_id": v["voice"]["voice_id"]}
    if not skip_webhook:
        checks["server_url"] = v["server_url"]
    if not skip_tools:
        checks["tools_server_url"] = v.get("tools_server_url", "")
    unresolved = [k for k, val in checks.items() if val == "REPLACE_ME" or val.startswith("REPLACE_ME_")]
    if (override_phone_number_id or os.environ.get("VAPI_PHONE_NUMBER_ID", "REPLACE_ME")) == "REPLACE_ME":
        unresolved.append("VAPI_PHONE_NUMBER_ID (.env) - run agent/setup_zadarma_trunk.py first")

    model = {
        "provider": v["model"]["provider"],
        "model": v["model"]["model"],
        "messages": [{"role": "system", "content": system_prompt}],
    }
    if not skip_tools:
        model["tools"] = build_tools(v)

    assistant = {
        "model": model,
        "voice": {"provider": v["voice"]["provider"], "voiceId": v["voice"]["voice_id"]},
        "transcriber": {"provider": v["transcriber"]["provider"], "language": v["transcriber"]["language"]},
    }
    if not skip_webhook:
        assistant["serverUrl"] = v["server_url"]

    payload = {
        "assistant": assistant,
        "phoneNumberId": override_phone_number_id or os.environ.get("VAPI_PHONE_NUMBER_ID", "REPLACE_ME"),
        "customer": {"number": to_number},
        # echoed back on every webhook event (message.call.metadata) - this is how
        # process_call.py maps a finished call back to an institute. Verify this
        # field name against a real webhook payload during training step 5; Vapi's
        # own docs confirm `metadata` exists on the call object but do not show its
        # exact webhook path end-to-end.
        "metadata": {"row_id": row_id},
    }
    return payload, source, unresolved


class CallBlocked(Exception):
    """Raised instead of placing a call when config.yaml still has REPLACE_ME values."""


def place_call(row_id, to_number, cfg, dry_run=True, phone_number_id=None,
               skip_webhook=False, skip_tools=False):
    """Used by both the CLI and run_campaign.py. Returns (result, payload, source)
    where result is the parsed Vapi API response for a real call, or None for a
    dry run. Raises CallBlocked if config isn't filled in and dry_run is False."""
    payload, source, unresolved = build_payload(row_id, cfg, to_number, phone_number_id,
                                                 skip_webhook, skip_tools)
    if unresolved and not dry_run:
        raise CallBlocked("config.yaml still has REPLACE_ME for: " + ", ".join(unresolved))
    if dry_run:
        return None, payload, source

    api_key = os.environ.get("VAPI_API_KEY")
    if not api_key:
        raise CallBlocked("VAPI_API_KEY not set (copy .env.example to .env and fill it in)")
    req = urllib.request.Request(VAPI_API, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
                                          "User-Agent": VAPI_UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            result = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Vapi API error {e.code}: {e.read().decode('utf-8', 'replace')}")
    call_id = result.get("id")
    if call_id:
        # Local record of call_id -> row_id at the moment of placing the call. This is the most
        # reliable of process_call.py's three ways to map a finished call back to an institute -
        # it does not depend on any webhook payload field surviving intact, only on Vapi echoing
        # back the same call id it just handed us (which it always does).
        log = ROOT / "data" / "calls" / "placed.csv"
        log.parent.mkdir(parents=True, exist_ok=True)
        new = not log.exists()
        with open(log, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["call_id", "row_id", "to_number", "placed_at"])
            w.writerow([call_id, row_id, to_number, datetime.now(timezone.utc).isoformat()])
    return result, payload, source


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("row_id")
    ap.add_argument("--to", help="override the number to call (default: the institute's phone on file)")
    ap.add_argument("--dry-run", action="store_true", help="print the payload only; place no call, need no API key")
    ap.add_argument("--skip-webhook", action="store_true",
                    help="omit serverUrl (no webhook deployed yet) - smoke-testing only, never for a real campaign call")
    ap.add_argument("--skip-tools", action="store_true",
                    help="omit tools (no tools webhook deployed yet) - smoke-testing only, never for a real campaign call")
    a = ap.parse_args()

    load_env()
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))

    to_number = a.to
    if not to_number:
        import csv
        for row in csv.DictReader(open(ROOT / "data" / "numbers_clean.csv", encoding="utf-8-sig")):
            if row["row_id"] == a.row_id:
                to_number = row.get("phone_1")
                break
    if not to_number:
        raise SystemExit(f"no phone number on file for {a.row_id}; pass --to +<number>")

    try:
        result, payload, source = place_call(a.row_id, to_number, cfg, dry_run=a.dry_run,
                                             skip_webhook=a.skip_webhook, skip_tools=a.skip_tools)
    except CallBlocked as e:
        raise SystemExit(str(e) + " (use --dry-run to inspect the payload without these)")
    except RuntimeError as e:
        raise SystemExit(str(e))
    print(f"[persona filled from {source}]", file=sys.stderr)
    print(json.dumps(payload if a.dry_run else result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
