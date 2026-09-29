"""One-time setup: register Paragon's Zadarma line as a Vapi bring-your-own SIP
trunk, following Zadarma's own published Vapi integration guide exactly
(zadarma.com/en/support/instructions/vapiai/). Run this once, not per call.

Before running:
    1. my.zadarma.com -> My PBX -> Extensions: note your extension (e.g. "1234-100"),
       generate its SIP password, and know your Zadarma number (+15551111111 format).
    2. Fill config.yaml's `vapi.zadarma.pbx_extension` and `zadarma_number`.
    3. Put VAPI_API_KEY and ZADARMA_SIP_PASSWORD in .env (copy .env.example if needed).

    python agent/setup_zadarma_trunk.py --dry-run   # print the two API calls, send nothing
    python agent/setup_zadarma_trunk.py               # actually register with Vapi

On success, prints the phoneNumberId to put in .env as VAPI_PHONE_NUMBER_ID - that,
not anything in this script, is what every real call in agent/vapi_call.py uses.

Last step (manual, not scriptable): back in Zadarma Extensions, enable "Call
forwarding and voicemail" and forward to "External server (SIP URI)":
    +<your-zadarma-number>@sip.vapi.ai
"""
import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))
from vapi_call import load_env  # noqa: E402
import os  # noqa: E402

VAPI_CREDENTIAL_API = "https://api.vapi.ai/credential"
VAPI_PHONE_NUMBER_API = "https://api.vapi.ai/phone-number"
ZADARMA_SIP_GATEWAY = "pbx.zadarma.com"   # fixed, per Zadarma's guide


def call_vapi(url, api_key, body):
    # Vapi's edge blocks requests with no User-Agent as bot traffic (HTTP 403,
    # "error code: 1010") even with a valid key - confirmed when first testing a real key.
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
                                          "User-Agent": "Mozilla/5.0 (compatible; ParagonCallingAgent/1.0)"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise SystemExit(f"Vapi API error {e.code} calling {url}: {e.read().decode('utf-8', 'replace')}")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="print the two API request bodies, send nothing")
    a = ap.parse_args()

    load_env()
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    z = cfg["vapi"]["zadarma"]
    extension, number = z["pbx_extension"], z["zadarma_number"]
    password = os.environ.get("ZADARMA_SIP_PASSWORD")

    missing = [n for n, v in [("vapi.zadarma.pbx_extension", extension), ("vapi.zadarma.zadarma_number", number)]
               if v == "REPLACE_ME"]
    if missing:
        raise SystemExit(f"Fill in config.yaml first: {', '.join(missing)}")
    if not password and not a.dry_run:
        raise SystemExit("ZADARMA_SIP_PASSWORD not set in .env (the SIP password from My PBX -> Extensions)")

    trunk_body = {
        "provider": "byo-sip-trunk",
        "name": "Zadarma Trunk",
        "gateways": [{"ip": ZADARMA_SIP_GATEWAY}],
        "authUsername": extension,
        "authPassword": password or "<from .env: ZADARMA_SIP_PASSWORD>",
    }

    if a.dry_run:
        print("Step 1 - create SIP trunk credential:")
        print(f"  POST {VAPI_CREDENTIAL_API}")
        print(json.dumps(trunk_body, indent=2))
        print("\nStep 2 - register the number (needs the credential id from step 1):")
        print(f"  POST {VAPI_PHONE_NUMBER_API}")
        print(json.dumps({"provider": "byo-phone-number", "number": number,
                          "credentialId": "<id from step 1>"}, indent=2))
        return

    api_key = os.environ.get("VAPI_API_KEY")
    if not api_key:
        raise SystemExit("VAPI_API_KEY not set in .env")

    print("Registering SIP trunk credential with Vapi...", file=sys.stderr)
    cred = call_vapi(VAPI_CREDENTIAL_API, api_key, trunk_body)
    credential_id = cred.get("id")
    if not credential_id:
        raise SystemExit(f"No credential id in Vapi's response: {json.dumps(cred, indent=2)}")
    print(f"  credential id: {credential_id}", file=sys.stderr)

    print("Registering the Zadarma number with Vapi...", file=sys.stderr)
    phone = call_vapi(VAPI_PHONE_NUMBER_API, api_key,
                      {"provider": "byo-phone-number", "number": number, "credentialId": credential_id})
    phone_number_id = phone.get("id")
    if not phone_number_id:
        raise SystemExit(f"No phone number id in Vapi's response: {json.dumps(phone, indent=2)}")

    print("\nDone. Add this to .env:")
    print(f"VAPI_PHONE_NUMBER_ID={phone_number_id}")
    print("\nThen in Zadarma (My PBX -> Extensions), enable Call forwarding and voicemail,")
    print(f"forward to External server (SIP URI): {number}@sip.vapi.ai")


if __name__ == "__main__":
    main()
