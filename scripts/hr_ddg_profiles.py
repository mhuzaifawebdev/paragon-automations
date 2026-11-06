"""Free: up to N public LinkedIn profiles per role from DuckDuckGo (site:linkedin.com/in), searched and matched ONLY on
the role's keywords (the "Keywords" Google Sheet), Pakistan only (pk.linkedin.com or a Pakistani city / "Pakistan" in the
result). Claude AI Expert: the result must name Claude or Anthropic. One search every 8 s; when DuckDuckGo asks to slow
down it waits (2, 4, 6 ... minutes) and continues. $0.

    python scripts/hr_ddg_profiles.py --limit 20
Output: hr_profiles/Best_Profiles_<date>.xlsx + Google Sheet in Drive "HR Best Profiles" (rebuilt after every role).
"""
import argparse
import html
import re
import sys
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scraper2"))
import erasmus_pass2 as p2  # noqa: E402  (polite fetch)
import hr_best_profiles as hb  # noqa: E402

PK = re.compile(r"pakistan|lahore|karachi|islamabad|rawalpindi|faisalabad|multan|peshawar|quetta|sialkot|gujranwala|"
                r"hyderabad, sindh|abbottabad|bahawalpur|sargodha", re.I)
BAD = re.compile(r"\bintern\b|\bstudent\b|fresh graduate|trainee", re.I)
_state = {"next": 0.0, "fails": 0}


def ddg(query):
    """[(url, title, snippet)] of one DuckDuckGo search; waits through its slow-down answers."""
    while True:
        wait = _state["next"] - time.time()
        if wait > 0:
            time.sleep(wait)
        try:
            _, body = p2.get("https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query), timeout=25)
        except Exception as e:
            body = f"ERROR {e}"
        _state["next"] = time.time() + 8
        if "anomaly" in body.lower() or body.startswith("ERROR"):
            _state["fails"] += 1
            pause = min(900, 120 * _state["fails"])
            print(f"   (DuckDuckGo asked to slow down: waiting {pause // 60} min)", flush=True)
            _state["next"] = time.time() + pause
            continue
        _state["fails"] = 0
        out = []
        for m in re.finditer(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</a>',
                             body, re.S):
            u = urllib.parse.unquote(re.sub(r".*uddg=", "", m.group(1)).split("&")[0])
            strip = lambda s: html.unescape(re.sub(r"<[^>]+>", "", s)).strip()  # noqa: E731
            out.append((u, strip(m.group(2)), strip(m.group(3))))
        return out


def person(role, keywords, url, title, snippet):
    if "/in/" not in url or "linkedin.com" not in url:
        return None
    blob = f"{title} {snippet}"
    if not (url.startswith("https://pk.linkedin.com") or PK.search(blob)) or BAD.search(title):
        return None
    nb = f" {hb.norm(blob)} "
    matched = [k for k in keywords if hb.norm(k) and f" {hb.norm(k)} " in nb]
    if not matched or (role.lower().startswith("claude") and not re.search(r"claude|anthropic", blob, re.I)):
        return None
    parts = re.split(r"\s+[-–|]\s+", re.sub(r"\s*\|\s*LinkedIn.*$", "", title))
    name = parts[0].strip()
    if len(name.split()) < 2:
        return None
    yrs = re.search(r"(\d{1,2})\+?\s*(?:years|yrs)", blob, re.I)
    yrs = int(yrs.group(1)) if yrs else ""
    if yrs != "" and yrs < 3:
        return None
    edu = bool(hb.EDU.search(blob))
    city = (PK.search(blob).group(0).title() if PK.search(blob) else "Pakistan")
    return {"name": name, "title": " - ".join(parts[1:])[:150], "employer": "", "city": city, "years": yrs,
            "linkedin": url.split("?")[0], "link_kind": "profile", "matched": matched, "source": url.split("?")[0],
            "edu": edu, "score": 3 * len(matched) + (2 if edu else 0) + (1 if (yrs or 0) >= 5 else 0)}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--role")
    a = ap.parse_args()
    tabs = hb.keyword_tabs()
    roles = [r for r in tabs if not a.role or hb.norm(a.role) in hb.norm(r)]
    # the roles in the order the HR team asked about them first
    order = ["claude", "sales", "recruit", "ghost", "va ", "instructional", "fundrais", "event"]
    roles.sort(key=lambda r: next((i for i, o in enumerate(order) if o in hb.norm(r) + " "), 99))
    path = hb.OUT / f"Best_Profiles_{time.strftime('%Y-%m-%d_%H%M')}.xlsx"
    hb.OUT.mkdir(exist_ok=True)
    results = {}
    for role in roles:
        kws = tabs[role]
        found, seen = [], set()
        qs = [f'site:linkedin.com/in "{k}" Pakistan' for k in kws] + [f'site:pk.linkedin.com/in "{k}"' for k in kws]
        for q in qs:
            if len(found) >= a.limit:
                break
            for u, t, s in ddg(q):
                p = person(role, kws, u, t, s)
                if p and p["linkedin"] not in seen:
                    seen.add(p["linkedin"])
                    found.append(p)
            print(f"   {role}: {q} -> {len(found)} so far", flush=True)
        found.sort(key=lambda p: -p["score"])
        results[role] = (found[:a.limit], 0.0)
        hb.save(results, tabs, path)
        print(f"== {role}: {len(results[role][0])} kept (saved {path.name})", flush=True)
    try:
        print("Google Sheet:", hb.upload(path), flush=True)
    except Exception as e:
        print(f"(Drive upload failed: {str(e)[:200]})", flush=True)


if __name__ == "__main__":
    main()
