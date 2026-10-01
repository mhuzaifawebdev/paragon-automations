"""Thin client for Zadarma's account REST API (balance, PBX extensions, presence).

This is NOT the SIP trunk Vapi uses for live calls (see setup_zadarma_trunk.py for that -
it needs a separate pbx_extension/zadarma_number/SIP password from My PBX -> Extensions).
This client only reads account-level information with ZADARMA_API_KEY / ZADARMA_API_SECRET
from .env, using Zadarma's documented signing scheme:

    query = urlencode(sorted(params))
    md5 = md5(query)
    signature = base64(hmac_sha1(secret, method + query + md5))
    header: Authorization: <key>:<signature>

Confirmed live against a real account (24 Sep): balance, pbx/internal (extension list),
pbx/internal/<n>/status (presence), pbx/internal/<n>/info all return real data with this scheme.
"""
import base64
import hashlib
import hmac
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))
from vapi_call import load_env  # noqa: E402
import os  # noqa: E402

BASE = "https://api.zadarma.com"


class ZadarmaError(Exception):
    pass


def _creds():
    key, secret = os.environ.get("ZADARMA_API_KEY"), os.environ.get("ZADARMA_API_SECRET")
    if not key or not secret:
        raise ZadarmaError("ZADARMA_API_KEY / ZADARMA_API_SECRET not set (see .env.example)")
    return key, secret


def _get(method, params=None, _get_fn=None):
    """_get_fn is a seam for tests: pass a fake to avoid any real HTTP call."""
    key, secret = _creds()
    params = params or {}
    query = urllib.parse.urlencode(sorted(params.items()))
    md5 = hashlib.md5(query.encode()).hexdigest()
    to_sign = method + query + md5
    sig = base64.b64encode(hmac.new(secret.encode(), to_sign.encode(), hashlib.sha1).hexdigest().encode()).decode()
    url = f"{BASE}{method}" + (f"?{query}" if query else "")
    fetch = _get_fn or _real_get
    return fetch(url, f"{key}:{sig}")


def _real_get(url, auth_header):
    req = urllib.request.Request(url, headers={"Authorization": auth_header})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise ZadarmaError(f"Zadarma API {e.code}: {e.read().decode('utf-8', 'replace')[:300]}")
    if data.get("status") != "success":
        raise ZadarmaError(f"Zadarma API error: {data}")
    return data


def balance(_get_fn=None):
    return _get("/v1/info/balance/", _get_fn=_get_fn)


def extensions(_get_fn=None):
    """Returns the list of PBX extension numbers (e.g. [100, 101, ...])."""
    return _get("/v1/pbx/internal/", _get_fn=_get_fn).get("numbers", [])


def extension_status(number, _get_fn=None):
    """Returns True/False for is_online, or None if the API didn't say."""
    data = _get(f"/v1/pbx/internal/{number}/status/", _get_fn=_get_fn)
    v = data.get("is_online")
    return {"true": True, "false": False}.get(v)


def extension_info(number, _get_fn=None):
    return _get(f"/v1/pbx/internal/{number}/info/", _get_fn=_get_fn)


def statistics_pbx(start, end, _get_fn=None):
    """Per-extension call history: each entry's "sip" field is the internal PBX extension number
    (e.g. "105"), unlike /v1/statistics/ (the generic account endpoint) whose "sip" field is the
    shared outbound trunk id, the same for every call regardless of which extension placed it -
    confirmed by testing both against the real account before relying on this one.
    start/end: "YYYY-MM-DD HH:MM:SS" strings, Zadarma's documented format (UTC)."""
    data = _get("/v1/statistics/pbx/", params={"start": start, "end": end, "version": "2"}, _get_fn=_get_fn)
    return data.get("stats", [])


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    load_env()
    b = balance()
    print(f"Balance: {b['balance']} {b['currency']}")
    for n in extensions():
        online = extension_status(n)
        print(f"  ext {n}: {'online' if online else 'offline' if online is False else 'unknown'}")
