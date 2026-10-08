"""Put a finished batch's results.xlsx into a Google Drive folder, so nobody has to download and re-upload it.

    python scraper2/drive_upload.py --batch teamA

Two ways to sign in, chosen by what is configured:

  * As the client (personal Gmail): GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET and
    GOOGLE_OAUTH_REFRESH_TOKEN are set (scripts/drive_authorize.py produces the token once). The permission is
    Drive's narrowest one - only files this tool created - so it keeps its own folder, "Paragon Scraper Results".
  * As the project's robot account (company Workspace): GOOGLE_SERVICE_ACCOUNT_JSON, or the key file under
    credentials/, plus DRIVE_FOLDER_ID naming a folder on a SHARED DRIVE the robot is a member of. A robot
    account has no storage of its own, so it cannot keep a file in somebody's personal "My Drive".

Never fails a batch: whatever happens is written into the batch's progress.json as drive_link or drive_error,
and the process exits 0. With nothing configured it does nothing at all.
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KEY_FILE = ROOT / "credentials" / "google_service_account.json"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
FOLDER_MIME = "application/vnd.google-apps.folder"
OWN_FOLDER = "Paragon Scraper Results"


def sign_in_mode(env=None):
    """'oauth' (upload as the client), 'robot' (service account into a shared drive) or None (not set up)."""
    env = os.environ if env is None else env
    if all(env.get(k) for k in ("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "GOOGLE_OAUTH_REFRESH_TOKEN")):
        return "oauth"
    if env.get("DRIVE_FOLDER_ID") and (env.get("GOOGLE_SERVICE_ACCOUNT_JSON") or KEY_FILE.exists()):
        return "robot"
    return None


def drive_service(mode):
    from googleapiclient.discovery import build
    if mode == "oauth":
        from google.oauth2.credentials import Credentials
        creds = Credentials(None, refresh_token=os.environ["GOOGLE_OAUTH_REFRESH_TOKEN"],
                            token_uri="https://oauth2.googleapis.com/token",
                            client_id=os.environ["GOOGLE_OAUTH_CLIENT_ID"],
                            client_secret=os.environ["GOOGLE_OAUTH_CLIENT_SECRET"],
                            scopes=["https://www.googleapis.com/auth/drive.file"])
    else:
        from google.oauth2 import service_account
        scopes = ["https://www.googleapis.com/auth/drive"]
        raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
        creds = (service_account.Credentials.from_service_account_info(json.loads(raw), scopes=scopes) if raw
                 else service_account.Credentials.from_service_account_file(str(KEY_FILE), scopes=scopes))
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _quote(s):
    return s.replace("\\", "\\\\").replace("'", "\\'")


def _find(svc, name, parent=None, mime=None):
    q = f"name = '{_quote(name)}' and trashed = false"
    if parent:
        q += f" and '{parent}' in parents"
    if mime:
        q += f" and mimeType = '{mime}'"
    found = svc.files().list(q=q, fields="files(id, webViewLink)", pageSize=1, supportsAllDrives=True,
                             includeItemsFromAllDrives=True).execute().get("files", [])
    return found[0] if found else None


def target_folder(svc, mode, folder_id=None):
    """The configured folder; signed in as the client with none configured, this tool's own folder (made once)."""
    if folder_id:
        return folder_id
    if mode != "oauth":
        raise RuntimeError("DRIVE_FOLDER_ID is not set")
    own = _find(svc, OWN_FOLDER, mime=FOLDER_MIME)
    if own:
        return own["id"]
    return svc.files().create(body={"name": OWN_FOLDER, "mimeType": FOLDER_MIME}, fields="id").execute()["id"]


def put_file(svc, path, name, folder):
    """Create the file, or replace its contents if this batch was uploaded before. Returns its Drive link."""
    from googleapiclient.http import MediaFileUpload
    media = MediaFileUpload(str(path), mimetype=XLSX, resumable=False)
    old = _find(svc, name, parent=folder)
    if old:
        done = svc.files().update(fileId=old["id"], media_body=media, fields="id, webViewLink",
                                  supportsAllDrives=True).execute()
    else:
        done = svc.files().create(body={"name": name, "parents": [folder]}, media_body=media,
                                  fields="id, webViewLink", supportsAllDrives=True).execute()
    return done.get("webViewLink") or f"https://drive.google.com/file/d/{done['id']}/view"


def upload(batch_dir, batch, svc=None, mode=None):
    """Returns (link, error); both None when Drive is not set up. Records the outcome in progress.json."""
    batch_dir = Path(batch_dir)
    mode = mode or sign_in_mode()
    if not mode:
        return None, None
    link = error = None
    try:
        xlsx = batch_dir / "results.xlsx"
        if not xlsx.exists():
            raise RuntimeError("results.xlsx has not been built for this batch")
        svc = svc or drive_service(mode)
        link = put_file(svc, xlsx, f"{batch} results.xlsx", target_folder(svc, mode, os.environ.get("DRIVE_FOLDER_ID")))
    except Exception as e:
        error = _explain(e)
    progress = batch_dir / "progress.json"
    if progress.exists():
        data = json.loads(progress.read_text(encoding="utf-8"))
        data.pop("drive_link", None)
        data.pop("drive_error", None)
        data["drive_link" if link else "drive_error"] = link or error
        progress.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return link, error


def _explain(e):
    text = str(e)
    if "storageQuotaExceeded" in text or "do not have storage quota" in text:
        return ("Google refused the upload: the robot account cannot store files in a personal Drive. Use a folder "
                "on a shared drive, or sign in as the Drive's owner (scripts/drive_authorize.py).")
    if "has not been used in project" in text or "accessNotConfigured" in text or "it is disabled" in text:
        return ("The Google Drive API is switched off for the robot account's Google Cloud project. Enable "
                "'Google Drive API' there (APIs & Services > Library), wait a few minutes, and run the batch again.")
    if "notFound" in text or "File not found" in text:
        return "The Drive folder was not found, or it has not been shared with the account the tool signs in as."
    if "invalid_grant" in text:
        return "Google no longer accepts the saved sign-in. Run scripts/drive_authorize.py again and update the secret."
    return f"{type(e).__name__}: {text[:200]}"


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--batch", required=True)
    a = ap.parse_args()
    link, error = upload(ROOT / "data_hei" / a.batch, a.batch)
    print(f"Google Drive: {link}" if link else f"Google Drive upload failed (the download still works): {error}" if error
          else "Google Drive is not set up - nothing uploaded.")


if __name__ == "__main__":
    main()
