"""Reuse an institute already scraped in an earlier batch instead of scraping (and paying for) it again.

data_hei/_index.json maps an institute key to where its result is: {"n:<name>|<country>": {"b": batch, "r": row_id,
"at": "YYYY-MM-DD"}, "w:<website host>": {...}}. scripts/queue_tick.py adds every finished batch to it. The result
itself is read from the batch folder while it is still in the repository, else from the batch's archive in Google
Drive ("_archive/<batch> data.json.gz"), downloaded once per job.
"""
import copy
import gzip
import json
import re
import time
import unicodedata
from pathlib import Path
from urllib.parse import urlparse

INDEX_NAME = "_index.json"
_CACHE = {"index": None, "archives": {}}


def norm(s):
    s = unicodedata.normalize("NFKD", str(s or "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


def host(url):
    u = str(url or "").strip()
    if not u:
        return ""
    h = urlparse(u if "//" in u else "https://" + u).netloc.lower()
    return h[4:] if h.startswith("www.") else h


def keys(name, country, website=""):
    out = []
    n = norm(name)
    if len(n) >= 4:
        out.append(f"n:{n}|{norm(country)}")
    h = host(website)
    if h and "." in h:
        out.append(f"w:{h}")
    return out


def row_keys(row):
    """Keys of an uploaded row (numbers_clean.csv): its name and country, and the website the sheet gives."""
    site = (row.get("website_override") or "").strip() or ((row.get("seed_urls") or "").split() or [""])[0]
    return keys(row.get("institution_name"), row.get("country_sheet"), site)


def reusable(result):
    """Only a real result is worth reusing: a website was found and read."""
    c = (result or {}).get("contacts") or {}
    return bool(c.get("official_website")) and not str(c.get("notes") or "").startswith(("Website not found",
                                                                                        "Could not be read"))


def load_index(data_root):
    if _CACHE["index"] is None:
        p = Path(data_root) / INDEX_NAME
        try:
            _CACHE["index"] = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        except Exception:
            _CACHE["index"] = {}
    return _CACHE["index"]


def add_batch(index, batch, rows, results):
    """Adds a finished batch: rows from its numbers_clean.csv, results {row_id: result}. Returns how many were added.
    The result's own website is a key too, so a later sheet that gives the website finds it."""
    added = 0
    for row in rows:
        res = results.get(row.get("row_id"))
        if not reusable(res):
            continue
        entry = {"b": batch, "r": row["row_id"], "at": str(res.get("scraped_at") or "")[:10]}
        for k in set(row_keys(row) + keys("", "", res["contacts"].get("official_website"))):
            index[k] = entry
        added += 1
    return added


def archive_bytes(results):
    return gzip.compress(json.dumps(results, ensure_ascii=False).encode("utf-8"))


def _batch_results(data_root, batch, fetch_archive):
    if batch in _CACHE["archives"]:
        return _CACHE["archives"][batch]
    folder = Path(data_root) / batch / "scrape_results"
    found = None
    if folder.exists():
        found = {f.stem: json.loads(f.read_text(encoding="utf-8")) for f in folder.glob("r*.json")}
    elif fetch_archive:
        try:
            raw = fetch_archive(batch)
            found = json.loads(gzip.decompress(raw).decode("utf-8")) if raw else None
        except Exception as e:
            print(f"(archive of batch {batch} could not be read: {str(e)[:120]})", flush=True)
    _CACHE["archives"][batch] = found or {}
    return _CACHE["archives"][batch]


def lookup(row, data_root, current_batch, max_age_days=180, fetch_archive=None, today=None):
    """A copy of the earlier result for this row, ready to save under the row's own id, or None."""
    index = load_index(data_root)
    today = today or time.strftime("%Y-%m-%d", time.gmtime())
    for k in row_keys(row):
        hit = index.get(k)
        if not hit or hit.get("b") == current_batch:
            continue
        if hit.get("at") and _age_days(hit["at"], today) > max_age_days:
            continue
        earlier = _batch_results(data_root, hit["b"], fetch_archive).get(hit["r"])
        if not reusable(earlier):
            continue
        res = copy.deepcopy(earlier)
        rid = row["row_id"]
        res["row_id"] = rid
        res["contacts"]["row_id"] = rid
        res["contacts"]["notes"] = (f"Reused from batch {hit['b']} (scraped {hit.get('at') or 'earlier'}). "
                                    + str(res["contacts"].get("notes") or ""))
        res["reused_from"] = {"batch": hit["b"], "row_id": hit["r"]}
        res["timing"] = {"total_seconds": 0, "cost_usd_estimate": 0, "ai_calls": 0, "mode": "reused"}
        return res
    return None


def _age_days(day, today):
    try:
        a = time.mktime(time.strptime(day, "%Y-%m-%d"))
        b = time.mktime(time.strptime(today, "%Y-%m-%d"))
        return (b - a) / 86400
    except Exception:
        return 0
