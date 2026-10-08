"""Offline tests for scraper2/drive_upload.py, with Google Drive faked. Run: python tests/test_drive_upload.py"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scraper2"))
import drive_upload as du  # noqa: E402

results = []


def check(name, got, want):
    ok = got == want
    results.append(ok)
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f": got {got!r}, want {want!r}"))


class Call:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class FakeDrive:
    """Remembers files as {id: (name, parent, mime)} and counts creates/updates, like the real files() API."""
    def __init__(self, refuse=None):
        self.items, self.creates, self.updates, self.refuse = {}, 0, 0, refuse

    def files(self):
        return self

    def list(self, q, **kw):
        def run():
            hits = [{"id": i, "webViewLink": f"https://drive.example/{i}"} for i, (n, p, m) in self.items.items()
                    if f"name = '{n}'" in q and (p is None or f"'{p}' in parents" in q or "in parents" not in q)
                    and ("mimeType" not in q or m == du.FOLDER_MIME)]
            return {"files": hits[:1]}
        return Call(run)

    def create(self, body, media_body=None, **kw):
        def run():
            if self.refuse:
                raise RuntimeError(self.refuse)
            self.creates += 1
            fid = f"f{len(self.items) + 1}"
            self.items[fid] = (body["name"], (body.get("parents") or [None])[0], body.get("mimeType"))
            return {"id": fid, "webViewLink": f"https://drive.example/{fid}"}
        return Call(run)

    def update(self, fileId, media_body=None, **kw):
        def run():
            self.updates += 1
            return {"id": fileId, "webViewLink": f"https://drive.example/{fileId}"}
        return Call(run)


def put_without_media(svc, path, name, folder):       # the real one needs googleapiclient only to wrap the file
    old = du._find(svc, name, parent=folder)
    if old:
        return svc.files().update(fileId=old["id"]).execute()["webViewLink"]
    return svc.files().create(body={"name": name, "parents": [folder]}).execute()["webViewLink"]


du.put_file = put_without_media

check("nothing configured: not set up", du.sign_in_mode({}), None)
check("folder but no key: not set up", du.sign_in_mode({"DRIVE_FOLDER_ID": "F"}) if not du.KEY_FILE.exists() else None, None)
check("robot key + folder: robot route", du.sign_in_mode({"DRIVE_FOLDER_ID": "F", "GOOGLE_SERVICE_ACCOUNT_JSON": "{}"}), "robot")
oauth_env = {"GOOGLE_OAUTH_CLIENT_ID": "a", "GOOGLE_OAUTH_CLIENT_SECRET": "b", "GOOGLE_OAUTH_REFRESH_TOKEN": "c"}
check("client sign-in present: it is used, even if a robot key also exists",
      du.sign_in_mode({**oauth_env, "DRIVE_FOLDER_ID": "F", "GOOGLE_SERVICE_ACCOUNT_JSON": "{}"}), "oauth")
check("an incomplete client sign-in is not used", du.sign_in_mode({"GOOGLE_OAUTH_CLIENT_ID": "a"}), None)

tmp = Path(tempfile.mkdtemp())
(tmp / "results.xlsx").write_bytes(b"x")
(tmp / "progress.json").write_text(json.dumps({"batch": "b1", "status": "done", "pending": 0}), encoding="utf-8")
for k in list(oauth_env) + ["DRIVE_FOLDER_ID", "GOOGLE_SERVICE_ACCOUNT_JSON"]:
    os.environ.pop(k, None)

check("not set up: nothing is uploaded and progress.json is left alone",
      (du.upload(tmp, "b1", svc=FakeDrive(), mode=None) if not du.sign_in_mode() else (None, None),
       "drive_link" in json.loads((tmp / "progress.json").read_text(encoding="utf-8"))), ((None, None), False))

os.environ["DRIVE_FOLDER_ID"] = "FOLDER"
drive = FakeDrive()
link, err = du.upload(tmp, "b1", svc=drive, mode="robot")
prog = json.loads((tmp / "progress.json").read_text(encoding="utf-8"))
check("first upload creates one file in the configured folder",
      (drive.creates, drive.updates, list(drive.items.values())), (1, 0, [("b1 results.xlsx", "FOLDER", None)]))
check("its link is recorded for the upload page", (link, prog.get("drive_link"), err), ("https://drive.example/f1",) * 2 + (None,))
check("the rest of progress.json is untouched", (prog["status"], prog["pending"]), ("done", 0))
du.upload(tmp, "b1", svc=drive, mode="robot")
check("uploading the same batch again replaces the file instead of adding a second", (drive.creates, drive.updates), (1, 1))

refused = FakeDrive(refuse="HttpError 403 storageQuotaExceeded: Service Accounts do not have storage quota")
link, err = du.upload(tmp, "b2", svc=refused, mode="robot")
prog = json.loads((tmp / "progress.json").read_text(encoding="utf-8"))
check("a refused upload is reported, not raised", (link, "personal Drive" in err), (None, True))
check("the error replaces the old link in progress.json", ("drive_link" in prog, prog.get("drive_error") == err), (False, True))

off = FakeDrive(refuse='HttpError 403 "Google Drive API has not been used in project 123 before or it is disabled."')
check("Drive API switched off: the message says exactly what to enable",
      "Enable 'Google Drive API'" in du.upload(tmp, "b2", svc=off, mode="robot")[1], True)

os.environ.pop("DRIVE_FOLDER_ID")
own = FakeDrive()
du.upload(tmp, "b3", svc=own, mode="oauth")
du.upload(tmp, "b4", svc=own, mode="oauth")
names = sorted(n for n, p, m in own.items.values())
check("signed in as the client with no folder set: the tool makes its own folder once and reuses it",
      names, ["Paragon Scraper Results", "b3 results.xlsx", "b4 results.xlsx"])
folder_id = next(i for i, (n, p, m) in own.items.items() if n == "Paragon Scraper Results")
check("both files are inside that folder", {p for n, p, m in own.items.values() if n != "Paragon Scraper Results"}, {folder_id})

(tmp / "results.xlsx").unlink()
link, err = du.upload(tmp, "b5", svc=FakeDrive(), mode="oauth")
check("no Excel built yet: reported as an error, no crash", (link, "has not been built" in err), (None, True))

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
