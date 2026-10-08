"""One-time Google sign-in so the scraper can put result files in a PERSONAL Google Drive.

Only needed when the Drive is a personal Gmail account (no "Shared drives"). Run it once on any computer:

    pip install google-auth-oauthlib
    python scripts/drive_authorize.py --client-id XXXX.apps.googleusercontent.com --client-secret YYYY

A browser opens; the Drive's owner signs in and clicks Allow. The permission asked for is Google's narrowest for
Drive: the tool can only see and change files it created itself, never the owner's other files. The script then
prints three values to save as GitHub secrets (see web_scraper/README.md). Nothing is written to disk.
"""
import argparse

SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--client-id", required=True)
    ap.add_argument("--client-secret", required=True)
    a = ap.parse_args()
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        raise SystemExit("First run:  pip install google-auth-oauthlib")
    flow = InstalledAppFlow.from_client_config(
        {"installed": {"client_id": a.client_id, "client_secret": a.client_secret,
                       "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                       "token_uri": "https://oauth2.googleapis.com/token", "redirect_uris": ["http://localhost"]}}, SCOPES)
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    if not creds.refresh_token:
        raise SystemExit("Google did not return a long-lived token. Remove this app's access at "
                         "myaccount.google.com/permissions and run this again.")
    print("\nSigned in. Add these three GitHub secrets (Settings > Secrets and variables > Actions):\n")
    print(f"GOOGLE_OAUTH_CLIENT_ID      {a.client_id}")
    print(f"GOOGLE_OAUTH_CLIENT_SECRET  {a.client_secret}")
    print(f"GOOGLE_OAUTH_REFRESH_TOKEN  {creds.refresh_token}")


if __name__ == "__main__":
    main()
