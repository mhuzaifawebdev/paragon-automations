"""LinkedIn profiles per role through Google (serper.dev API, free 2,500 searches on signup): searches
site:linkedin.com/in "<keyword>" Pakistan for each of the role's keywords (the "Keywords" Google Sheet), reads each
result's name, headline and snippet, and keeps people in Pakistan whose headline/snippet shows at least one of the
role's keywords (Claude AI Expert: must name Claude or Anthropic). Ranked by keywords matched, education background,
years when shown. LinkedIn profiles only.

    python scripts/hr_serper_profiles.py --limit 20
Key: SERPER_API_KEY in .env (typed there by the user).
Output: hr_profiles/Best_Profiles_<date>.xlsx + Google Sheet in Drive "HR Best Profiles".
"""
import argparse
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scraper2"))
import hr_best_profiles as hb  # noqa: E402
import hr_ddg_profiles as hd  # noqa: E402  (Pakistan / keyword / name rules for one search result)


def key():
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*SERPER_API_KEY\s*=\s*(.+)$", line)
        if m:
            return m.group(1).strip().strip('"').strip("'")
    return ""


def google(k, q, page=1):
    body = json.dumps({"q": q, "gl": "pk", "num": 10, "page": page}).encode()
    req = urllib.request.Request("https://google.serper.dev/search", data=body,
                                 headers={"X-API-KEY": k, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.loads(r.read().decode())
    return [(x.get("link", ""), x.get("title", ""), x.get("snippet", "")) for x in d.get("organic") or []]


SEEN = hb.OUT / "_seen_linkedin.json"     # every profile already delivered, so each run brings NEW people


def load_seen():
    """LinkedIn profiles delivered before: this file plus every earlier Best_Profiles*.xlsx and *_shortlist_30.csv."""
    seen = set()
    try:
        seen |= set(json.loads(SEEN.read_text(encoding="utf-8")))
    except Exception:
        pass
    import csv
    import openpyxl
    for f in hb.OUT.glob("Best_Profiles*.xlsx"):
        try:
            for ws in openpyxl.load_workbook(f, read_only=True).worksheets:
                for row in ws.iter_rows(values_only=True):
                    seen |= {str(v).lower().rstrip("/") for v in row if v and "linkedin.com/in/" in str(v)}
        except Exception:
            continue
    for f in (ROOT.parent / "hr_tool").glob("*_shortlist_30.csv"):
        with open(f, encoding="utf-8-sig") as fh:
            seen |= {(r.get("linkedin") or "").lower().rstrip("/") for r in csv.DictReader(fh)}
    return {s.split("?")[0] for s in seen if s}


def slug(u):
    m = re.search(r"linkedin\.com/in/([^/?#]+)", u or "", re.I)
    return m.group(1).lower() if m else (u or "").lower()


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--role")
    ap.add_argument("--max-searches", type=int, default=400)
    a = ap.parse_args()
    k = key()
    if not k:
        sys.exit("Add SERPER_API_KEY=... to .env first (serper.dev, free signup).")
    tabs = hb.keyword_tabs()
    roles = [r for r in tabs if not a.role or hb.norm(a.role) in hb.norm(r)]
    used, results = 0, {}
    delivered = {slug(s) for s in load_seen()}
    print(f"{len(delivered)} profiles delivered earlier are skipped.", flush=True)
    for role in roles:
        kws = tabs[role]
        found, seen = [], set(delivered)
        qs = []
        for page in (1, 2, 3, 4, 5):           # deeper Google pages when the first ones hold people already delivered
            for kw in kws:
                base = f'"{kw}"' if not role.lower().startswith("claude") or "claude" in kw.lower() else f'"Claude" "{kw}"'
                qs += [(f"site:linkedin.com/in {base} Pakistan", page)] + \
                    ([(f"site:pk.linkedin.com/in {base}", page)] if page <= 2 else [])
        for q, page in qs:
            if len(found) >= a.limit * 2 or used >= a.max_searches:
                break
            try:
                res = google(k, q, page)
            except Exception as e:
                print(f"   {role}: {q} failed ({str(e)[:80]})", flush=True)
                continue
            used += 1
            for u, t, s in res:
                p = hd.person(role, kws, u, t, s)
                if p and slug(p["linkedin"]) not in seen:
                    seen.add(slug(p["linkedin"]))
                    found.append(p)
        found.sort(key=lambda p: -p["score"])
        results[role] = (found[:a.limit], 0.0)
        print(f"== {role}: {len(results[role][0])} LinkedIn profiles ({used} searches used so far)", flush=True)
    path = hb.OUT / f"Best_Profiles_LinkedIn_{time.strftime('%Y-%m-%d_%H%M')}.xlsx"
    hb.OUT.mkdir(exist_ok=True)
    hb.save(results, tabs, path, want=a.limit)
    delivered |= {slug(p["linkedin"]) for rows, _ in results.values() for p in rows}
    SEEN.write_text(json.dumps(sorted(delivered)), encoding="utf-8")
    print(f"Saved {path}; {used} Google searches used of the free 2,500.")
    try:
        print("Google Sheet:", hb.upload(path))
    except Exception as e:
        print(f"(Drive upload failed: {str(e)[:200]})")


if __name__ == "__main__":
    main()
