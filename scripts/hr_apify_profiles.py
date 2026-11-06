"""30 best-fit LinkedIn profiles per role through the HR tool's Apify LinkedIn search (harvestapi linkedin-profile-search):
Pakistan, 3+ years (LinkedIn's own experience filter), searched and ranked ONLY on the role's keywords from the
"Keywords" Google Sheet (one tab per role). An education / training background ranks higher. Claude AI Expert: the
profile must name Claude or Anthropic.

    python scripts/hr_apify_profiles.py                      # all roles
    python scripts/hr_apify_profiles.py --role Ghost         # one role
    python scripts/hr_apify_profiles.py --searches 3         # searches per role (25 profiles each, about $0.10 each)

Token: APIFY_API_TOKEN in .env (typed there by the user). Hard cap: --max-usd (default 3.50) for the whole run.
Output: hr_profiles/Best_Profiles_<date>.xlsx (Summary + one tab per role) and the same as a Google Sheet in the shared
Drive folder "HR Best Profiles".
"""
import argparse
import json
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scraper2"))
import hr_best_profiles as hb  # noqa: E402  (keyword sheet reader, Excel writer, Drive upload)

CACHE = hb.OUT / "_apify_cache"
PER_SEARCH_USD = 0.10
BAD = re.compile(r"\bintern\b|\bstudent\b|fresh graduate|trainee|looking for (a )?job|seeking", re.I)
EDU = hb.EDU


def token():
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*APIFY_API_TOKEN\s*=\s*(.+)$", line)
        if m:
            return m.group(1).strip().strip('"').strip("'")
    return ""


def search(tok, query):
    CACHE.mkdir(parents=True, exist_ok=True)
    cached = CACHE / (re.sub(r"\W+", "_", query)[:120] + ".json")
    if cached.exists():
        return json.loads(cached.read_text(encoding="utf-8")), 0.0
    body = json.dumps({"profileScraperMode": "Short", "startPage": 1, "takePages": 1, "maxItems": 25,
                       "locations": ["Pakistan"], "yearsOfExperienceIds": ["3", "4", "5"], "searchQuery": query}).encode()
    url = ("https://api.apify.com/v2/acts/harvestapi~linkedin-profile-search/run-sync-get-dataset-items"
           f"?token={tok}&timeout=280")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=320) as r:
        items = json.loads(r.read().decode("utf-8"))
    if items:
        cached.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    return items, PER_SEARCH_USD


def queries(role, keywords, n):
    """Search phrases from the role's keywords only: the most specific ones first (multi-word, not generic)."""
    ks = sorted(keywords, key=lambda k: (-len(k.split()), keywords.index(k)))
    if role.lower().startswith("claude"):
        ks = [k if "claude" in k.lower() else "Claude " + k for k in keywords]
    return ks[:n]


def text_of(p):
    parts = [p.get("headline") or "", p.get("summary") or "", p.get("about") or ""]
    for x in (p.get("currentPositions") or []) + (p.get("experience") or []):
        if isinstance(x, dict):
            parts += [x.get("title") or x.get("position") or "", x.get("companyName") or "", x.get("description") or ""]
    for s in p.get("skills") or []:
        parts.append(s.get("name", "") if isinstance(s, dict) else str(s))
    return " ".join(parts)


def rank(role, keywords, items):
    rows, seen = [], set()
    for p in items:
        pid = p.get("id") or p.get("linkedinUrl")
        if not pid or pid in seen:
            continue
        seen.add(pid)
        pos = (p.get("currentPositions") or [{}])[0] or {}
        title = pos.get("title") or p.get("headline") or ""
        company = pos.get("companyName") or ""
        blob = hb.norm(text_of(p))
        matched = [k for k in keywords if hb.norm(k) and f" {hb.norm(k)} " in f" {blob} "]
        if not matched or BAD.search(title + " " + (p.get("headline") or "")):
            continue
        if role.lower().startswith("claude") and not re.search(r"claude|anthropic", blob):
            continue
        edu = bool(EDU.search(text_of(p)))
        loc = p.get("location") or {}
        rows.append({"name": f"{p.get('firstName', '')} {p.get('lastName', '')}".strip(), "title": title,
                     "employer": company, "city": loc.get("linkedinText") if isinstance(loc, dict) else str(loc or ""),
                     "years": "3+ (LinkedIn filter)", "linkedin": p.get("linkedinUrl", ""), "link_kind": "profile",
                     "matched": matched, "source": p.get("linkedinUrl", ""), "edu": edu,
                     "score": 3 * len(matched) + (2 if edu else 0) + (1 if p.get("summary") else 0)})
    rows.sort(key=lambda r: -r["score"])
    return rows


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--role")
    ap.add_argument("--searches", type=int, default=3)
    ap.add_argument("--max-usd", type=float, default=3.5)
    ap.add_argument("--no-upload", action="store_true")
    a = ap.parse_args()
    tok = token()
    if not tok:
        sys.exit("Add APIFY_API_TOKEN=... to .env first.")
    tabs = hb.keyword_tabs()
    roles = [r for r in tabs if not a.role or hb.norm(a.role) in hb.norm(r)]
    jobs = [(r, q) for r in roles for q in queries(r, tabs[r], a.searches)]
    budget = int(a.max_usd / PER_SEARCH_USD)
    if len(jobs) > budget:
        jobs = jobs[:budget]
    print(f"{len(roles)} roles, {len(jobs)} LinkedIn searches (up to ${len(jobs) * PER_SEARCH_USD:.2f}).", flush=True)
    found, spent = {r: [] for r in roles}, 0.0

    def run(job):
        r, q = job
        try:
            items, c = search(tok, q)
        except Exception as e:
            print(f"   {r} / {q}: failed ({str(e)[:120]})", flush=True)
            return r, q, [], 0.0
        return r, q, items, c

    with ThreadPoolExecutor(3) as pool:          # the free Apify plan runs only a few actors at once
        for r, q, items, c in pool.map(run, jobs):
            found[r] += items
            spent += c
            print(f"   {r} / {q}: {len(items)} profiles", flush=True)
    results = {}
    for r in roles:
        rows = rank(r, tabs[r], found[r])[:30]
        results[r] = (rows, 0.0)
        print(f"== {r}: {len(rows)} kept", flush=True)
    hb.OUT.mkdir(exist_ok=True)
    path = hb.OUT / f"Best_Profiles_{time.strftime('%Y-%m-%d_%H%M')}.xlsx"
    hb.save(results, tabs, path)
    print(f"\nSaved {path} (Apify spend this run about ${spent:.2f}).", flush=True)
    if not a.no_upload:
        try:
            print("Google Sheet:", hb.upload(path), flush=True)
        except Exception as e:
            print(f"(Drive upload failed: {str(e)[:200]})", flush=True)


if __name__ == "__main__":
    main()
