"""Finds up to 30 best-fit professionals per role for the HR team, matched ONLY on the role's keywords (the "Keywords"
Google Sheet, one tab per role), in Pakistan, 3+ years; an education / training sector background counts in favour.

Paid Gemini (GEMINI_PAID_API_KEY in .env) with Google Search, HARD CAP $5 for all runs together (hr_profiles/_spend.json).
Never the Claude plan. Nobody is made up: a person is kept only when their name sits in a part of Gemini's answer that
Google ties to a real source page (same rule as hr_tool Sourcing.gs groundedPeople_), and only keywords from the role's
own list count as matched.

    python scripts/hr_best_profiles.py --role "Claude AI Expert" --limit 5     # trial
    python scripts/hr_best_profiles.py                                           # all roles, 30 each

Output: hr_profiles/Best_Profiles_<date>.xlsx (one tab per role + Summary) and the same as a Google Sheet in the
shared Drive folder "HR Best Profiles".
"""
import argparse
import json
import os
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scraper2"))
OUT = ROOT / "hr_profiles"
SPEND = OUT / "_spend.json"
CAP_USD = 5.0
KEYWORDS_SHEET = "1mWHPjK5B_i96P8oFoEQ7Muo8H2HnMecoQVkmBZ_8pXk"
DRIVE_ROOT = "1MtxgKG_4zVEHCEgDPRFOinuR0hSTAoBe"
MODEL = "gemini-3.1-flash-lite"
PRICE = {"per_search": 0.014, "input_per_m": 0.25, "output_per_m": 1.50}
EDU = re.compile(r"school|universit|college|academy|education|training|institute|edtech|e-learning|elearning|campus|lms",
                 re.I)
EXTRA = {   # per-role extras from the Job Descriptions prompts (ranking only; the keywords decide the fit)
    "Claude AI Expert": "They must visibly work with Claude (Anthropic) itself - not generic AI/ML engineers.",
    "Sales": "High-volume outbound sales with closing numbers; education, training or recruitment sales preferred.",
    "Ghost Writers": "Ghostwriters / content writers with a published portfolio (not academic writers).",
    "Recruiters": "High-volume recruiters with LinkedIn sourcing and cold messaging.",
    "VA Administrators": "Executive / virtual assistants supporting senior management or CEOs.",
}


def load_env():
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*([A-Z_]+)\s*=\s*(.*)\s*$", line)
            if m and m.group(1) == "GEMINI_PAID_API_KEY":
                os.environ.setdefault(m.group(1), m.group(2).strip().strip('"').strip("'"))


def spent():
    try:
        return json.loads(SPEND.read_text(encoding="utf-8"))["usd"]
    except Exception:
        return 0.0


def add_spend(usd):
    OUT.mkdir(exist_ok=True)
    SPEND.write_text(json.dumps({"usd": round(spent() + usd, 4)}), encoding="utf-8")


def norm(s):
    s = unicodedata.normalize("NFKD", str(s or "").lower())
    return re.sub(r"[^a-z0-9]+", " ", "".join(c for c in s if not unicodedata.combining(c))).strip()


def keyword_tabs():
    """{tab name: [keywords]} from the Keywords Google Sheet (read through the robot account)."""
    import io
    import drive_upload as d
    import openpyxl
    svc = d.drive_service("robot")
    b = svc.files().export(fileId=KEYWORDS_SHEET,
                           mimeType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet").execute()
    wb = openpyxl.load_workbook(io.BytesIO(b), read_only=True)
    out = {}
    for ws in wb.worksheets:
        words = []
        for r in ws.iter_rows(values_only=True):
            v = str(r[0] or "").strip() if r else ""
            if len(v) > 1 and v.lower() not in {w.lower() for w in words}:
                words.append(v)
        if words:
            out[re.sub(r"\s+", " ", ws.title).strip()] = words
    return out


def grounded(prompt):
    """One Google-Search-grounded Gemini call: (answer text, supported text, {source url: title}, cost)."""
    key = os.environ["GEMINI_PAID_API_KEY"]
    body = json.dumps({"contents": [{"role": "user", "parts": [{"text": prompt}]}], "tools": [{"google_search": {}}],
                       "generationConfig": {"temperature": 0.2}}).encode("utf-8")
    req = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={key}",
                                 data=body, headers={"Content-Type": "application/json"})
    for i in range(3):
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                resp = json.loads(r.read().decode())
            break
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 503) and i < 2:
                time.sleep(15 * (i + 1))
                continue
            raise
    cand = (resp.get("candidates") or [{}])[0]
    meta = cand.get("groundingMetadata") or {}
    use = resp.get("usageMetadata") or {}
    queries = max(1, len(meta.get("webSearchQueries") or []))
    cost = (queries * PRICE["per_search"] + ((use.get("promptTokenCount") or 0) + (use.get("toolUsePromptTokenCount") or 0))
            * PRICE["input_per_m"] / 1e6 + ((use.get("candidatesTokenCount") or 0) + (use.get("thoughtsTokenCount") or 0))
            * PRICE["output_per_m"] / 1e6)
    text = " ".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", []))
    chunks = meta.get("groundingChunks") or []
    supported, seg_src = [], []
    for s in meta.get("groundingSupports") or []:
        idx = s.get("groundingChunkIndices") or []
        if idx:
            seg = (s.get("segment") or {}).get("text", "")
            supported.append(seg)
            seg_src.append((norm(seg), [(chunks[i].get("web") or {}) for i in idx if i < len(chunks)]))
    return text, seg_src, chunks, cost


def resolve(u):
    if "grounding-api-redirect" not in (u or ""):
        return u or ""
    try:
        req = urllib.request.Request(u, method="HEAD", headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.geturl()
    except urllib.error.HTTPError as e:
        return e.geturl() if "grounding-api-redirect" not in (e.geturl() or "") else ""
    except Exception:
        return ""


def people_from(text, seg_src, chunks, keywords, role):
    """PERSON lines whose name is in a source-backed segment. Matched keywords only from the role's own list."""
    out = []
    li_by_name = {}
    for c in chunks:                                    # LinkedIn pages Google itself returned
        w = c.get("web") or {}
        title = str(w.get("title") or "")
        if "linkedin" in title.lower() or "linkedin" in str(w.get("uri", "")).lower():
            li_by_name[norm(re.split(r"\s+[-–|]\s+", title)[0])] = w.get("uri", "")
    for line in text.splitlines():
        m = re.search(r"PERSON:\s*(.+)$", line, re.I)
        if not m:
            continue
        f = [x.replace("*", "").strip() for x in m.group(1).split("|")]
        f += [""] * (8 - len(f))
        name, title, employer, city, years, seen, li = f[:7]
        nn = norm(name)
        if len(nn.split()) < 2:
            continue
        backing = [srcs for seg, srcs in seg_src if f" {nn} " in f" {seg} "]
        if not backing:                                 # not tied to a real source: dropped (never made up)
            continue
        src = next((resolve(s.get("uri", "")) for srcs in backing for s in srcs if s.get("uri")), "")
        blob = norm(" ".join([title, employer, seen]))
        matched = [k for k in keywords if norm(k) and norm(k) in blob]
        if not matched:
            continue
        if role.startswith("Claude") and not re.search(r"claude|anthropic", blob):
            continue
        yrs = re.search(r"\d+", years or "")
        yrs = int(yrs.group()) if yrs else None
        if yrs is not None and yrs < 3:
            continue
        lm = re.search(r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/in/[^\s|)\]]+", li or "")
        link, link_kind = (lm.group(0), "profile") if lm else ("", "")
        if not link and nn in li_by_name:
            r = resolve(li_by_name[nn])
            if "/in/" in r:
                link, link_kind = r, "profile"
        if not link:
            link = "https://www.linkedin.com/search/results/people/?keywords=" + urllib.parse.quote(f"{name} {employer}".strip())
            link_kind = "search link"
        score = 2 * len(matched) + (1 if (yrs or 0) >= 5 else 0) + (1 if EDU.search(employer + " " + title) else 0) + \
            (1 if link_kind == "profile" else 0)
        out.append({"name": name, "title": title, "employer": employer, "city": city, "years": yrs if yrs is not None else "",
                    "linkedin": link, "link_kind": link_kind, "matched": matched, "source": src, "score": score,
                    "edu": bool(EDU.search(employer + " " + title))})
    return out


def find_for_role(role, keywords, want, role_cap):
    found, seen, cost, rounds = [], set(), 0.0, 0
    pairs = [keywords[i:i + 2] for i in range(0, len(keywords), 2)]
    while len(found) < want and rounds < 2 * len(pairs) + 4 and cost < role_cap and spent() + 0.06 < CAP_USD:
        kw = pairs[rounds % len(pairs)]
        city = ["Lahore", "Karachi", "Islamabad", "Rawalpindi", "Faisalabad", "Pakistan"][rounds % 6]
        prompt = (f"Use Google Search (LinkedIn profiles, company team pages, portfolios, directories) to find real, named "
                  f"professionals in {city}, Pakistan whose profile shows: {' and/or '.join(kw)}. "
                  f"{EXTRA.get(role, '')} They need at least 3 years of experience. People from the education / training "
                  f"sector are welcome. All keywords of this role: {', '.join(keywords)}.\n"
                  "Search several times. List up to 15 different people, ONE PER LINE, exactly as:\n"
                  "PERSON: full name | current job title | employer | city | years of experience (number, or -) | "
                  "which of the role's keywords their page shows (comma separated) | LinkedIn profile URL if a result showed it, else -\n"
                  "Only people a search result actually names. No commentary.")
        try:
            text, seg_src, chunks, c = grounded(prompt)
        except Exception as e:
            print(f"   {role}: search failed ({str(e)[:100]})", flush=True)
            break
        cost += c
        add_spend(c)
        rounds += 1
        new = 0
        for p in people_from(text, seg_src, chunks, keywords, role):
            k = norm(p["name"]) + "|" + norm(p["employer"])[:20]
            if k in seen or any(norm(p["name"]) == norm(x["name"]) for x in found):
                continue
            seen.add(k)
            found.append(p)
            new += 1
        print(f"   {role}: search {rounds} ({', '.join(kw)} / {city}): +{new}, {len(found)} kept, ${cost:.3f}", flush=True)
    found.sort(key=lambda p: -p["score"])
    return found[:want], cost


def save(results, keywords_by_role, path, want=30):
    import openpyxl
    from openpyxl.styles import Font
    wb = openpyxl.Workbook()
    summ = wb.active
    summ.title = "Summary"
    summ.append(["Role", "Profiles kept", "Keywords used", "Spend ($)", "Note"])
    for role, (rows, cost) in results.items():
        summ.append([role, len(rows), ", ".join(keywords_by_role[role]), round(cost, 3),
                     "" if len(rows) >= want else f"Fewer than {want} people genuinely matched the keywords; not padded."])
        ws = wb.create_sheet(re.sub(r"[\[\]:*?/\\]", "", role)[:31])
        # one link column (clickable); a separate source page only when it is not the LinkedIn profile itself
        extra = any(p["source"] and p["source"].rstrip("/") != (p["linkedin"] or "").rstrip("/") for p in rows)
        ws.append(["Rank", "Name", "LinkedIn profile", "Current title", "Employer", "City", "Years",
                   "Keywords matched", "Education / training sector"] + (["Found on"] if extra else []))
        for i, p in enumerate(rows, 1):
            link = p["linkedin"] or p["source"]
            ws.append([i, p["name"], link, p["title"], p["employer"], p["city"], p["years"],
                       ", ".join(p["matched"]), "yes" if p["edu"] else ""] +
                      ([p["source"] if p["source"].rstrip("/") != (link or "").rstrip("/") else ""] if extra else []))
            for col in ((3, 10) if extra else (3,)):
                cell = ws.cell(row=i + 1, column=col)
                if isinstance(cell.value, str) and cell.value.startswith("http"):
                    cell.hyperlink = cell.value
                    cell.font = Font(color="1155CC", underline="single")
        for col, width in zip("ABCDEFGHIJ", (6, 26, 48, 44, 24, 14, 7, 40, 12, 40)):
            ws.column_dimensions[col].width = width
    summ.column_dimensions["A"].width, summ.column_dimensions["C"].width = 34, 90
    for ws in wb.worksheets:
        for c in ws[1]:
            c.font = Font(bold=True)
        ws.freeze_panes = "A2"
    wb.save(path)


def reformat(xlsx, sheet_id):
    """Re-writes an earlier Best Profiles file in the current layout and replaces the Google Sheet's content in place."""
    import openpyxl
    wb = openpyxl.load_workbook(xlsx, read_only=True)
    results, kws = {}, {}
    for s in wb.worksheets:
        rows = list(s.iter_rows(values_only=True))
        if s.title == "Summary":
            kws = {r[0]: str(r[2] or "").split(", ") for r in rows[1:] if r and r[0]}
            continue
        h = list(rows[0])
        g = lambda r, k: r[h.index(k)] if k in h and h.index(k) < len(r) else ""  # noqa: E731
        results[s.title] = ([{"name": g(r, "Name"), "title": g(r, "Current title") or "", "employer": g(r, "Employer") or "",
                              "city": g(r, "City") or "", "years": g(r, "Years") or "", "linkedin": g(r, "LinkedIn") or "",
                              "link_kind": "", "matched": [x for x in str(g(r, "Keywords matched") or "").split(", ") if x],
                              "edu": g(r, "Education / training sector") == "yes", "source": g(r, "Source page") or ""}
                             for r in rows[1:] if r and r[1]], 0.0)
    full = {k: next((v for t, v in kws.items() if t[:31] == k), []) for k in results}
    out = Path(xlsx).with_name(Path(xlsx).stem + "_v2.xlsx")
    save(results, full, out, want=20)
    import drive_upload as d
    from googleapiclient.http import MediaFileUpload
    os.environ.setdefault("DRIVE_FOLDER_ID", DRIVE_ROOT)
    svc = d.robot_service()
    media = MediaFileUpload(str(out), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    f = svc.files().update(fileId=sheet_id, media_body=media, fields="webViewLink", supportsAllDrives=True).execute()
    return f.get("webViewLink")


def upload(path):
    import drive_upload as d
    from googleapiclient.http import MediaFileUpload
    os.environ.setdefault("DRIVE_FOLDER_ID", DRIVE_ROOT)
    svc = d.robot_service()
    folder = d.subfolder(svc, "HR Best Profiles")
    media = MediaFileUpload(str(path), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    f = svc.files().create(body={"name": path.stem.replace("_", " "), "parents": [folder],
                                 "mimeType": "application/vnd.google-apps.spreadsheet"},
                           media_body=media, fields="webViewLink", supportsAllDrives=True).execute()
    return f.get("webViewLink")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--role", help="one keyword tab, e.g. 'Claude AI Expert'")
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--no-upload", action="store_true")
    a = ap.parse_args()
    load_env()
    if not os.environ.get("GEMINI_PAID_API_KEY"):
        sys.exit("GEMINI_PAID_API_KEY is not in .env")
    tabs = keyword_tabs()
    roles = [r for r in tabs if not a.role or norm(a.role) in norm(r)]
    left = CAP_USD - spent()
    print(f"{len(roles)} role(s); ${spent():.2f} of the ${CAP_USD:.2f} cap spent so far.", flush=True)
    results = {}
    for i, role in enumerate(roles):
        role_cap = (CAP_USD - spent()) / max(1, len(roles) - i)
        rows, cost = find_for_role(role, tabs[role], a.limit, role_cap)
        results[role] = (rows, cost)
    OUT.mkdir(exist_ok=True)
    path = OUT / f"Best_Profiles_{time.strftime('%Y-%m-%d_%H%M')}.xlsx"
    save(results, tabs, path)
    print(f"\nSaved {path}. Spent this run ${sum(c for _, c in results.values()):.2f}; total ${spent():.2f} of ${CAP_USD:.2f}.")
    if not a.no_upload:
        try:
            print("Google Sheet:", upload(path))
        except Exception as e:
            print(f"(Drive upload failed: {str(e)[:200]})")
    _ = left


if __name__ == "__main__":
    main()
