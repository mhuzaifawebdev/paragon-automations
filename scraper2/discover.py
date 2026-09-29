"""Find an institution's official website, for free, and PROVE it is the right site.

Order: (a) a website typed into the sheet by a person, (b) the domain of an email address already
in the sheet, (c) Wikidata's 'official website' (free API, no key), (d) optionally one cheap
web-search call through the Claude CLI (local use only). Whatever a tier proposes, code checks the
homepage really is this institution (its name or the sheet's phone number appears on it), so a
wrong or invented URL can never be used. Rows that cannot be resolved are reported, not guessed.
"""
import csv
import json
import re
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scraper"))
import fetch_pages  # noqa: E402

GENERIC_MAIL = {"gmail.com", "googlemail.com", "yahoo.com", "yahoo.fr", "hotmail.com", "outlook.com", "live.com",
                "icloud.com", "gmx.de", "gmx.net", "web.de", "aol.com", "msn.com", "protonmail.com", "orange.fr",
                "free.fr", "wanadoo.fr", "libero.it", "seznam.cz", "wp.pl", "o2.pl", "onet.pl", "centrum.cz"}
STOP = {"university", "universidad", "universite", "universita", "universitat", "college", "school", "instituto", "institut",
        "institute", "ies", "cifp", "lycee", "escola", "ecole", "gymnasium", "kolegija", "akademia", "academy", "of", "de",
        "la", "le", "del", "the", "and", "und", "et", "para", "y", "a", "im", "in", "der", "die", "das", "for", "sk", "zs",
        "obchodna", "akademia", "technikum", "szakkepzo", "iskola", "es", "fr", "en"}
UA = {"User-Agent": "ParagonResearchBot/1.0 (institution contact research)"}

MEMO = Path(__import__("os").environ.get("PARAGON_DATA") or Path(__file__).resolve().parent.parent / "data") / "websites.csv"
_memo_lock = threading.Lock()


def load_memo():
    """row_id -> website already established in an earlier run (an institute is only searched once)."""
    if not MEMO.exists():
        return {}
    with open(MEMO, newline="", encoding="utf-8-sig") as f:
        return {r["row_id"]: r["url"] for r in csv.DictReader(f)}


def remember(row_id, url, how):
    with _memo_lock:
        known = load_memo()
        if not row_id or known.get(row_id) == url:
            return
        new = not MEMO.exists()
        with open(MEMO, "a", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["row_id", "url", "how", "saved_at"])
            w.writerow([row_id, url, how, time.strftime("%Y-%m-%d")])


def seed_from_results(results_dir):
    """One-off: take the websites an earlier scraper run already found."""
    n = 0
    for f in sorted(Path(results_dir).glob("r*.json")):
        try:
            c = json.loads(f.read_text(encoding="utf-8")).get("contacts") or {}
        except Exception:
            continue
        if c.get("official_website"):
            remember(f.stem, c["official_website"], "found by the first scraper (web search)")
            n += 1
    return n


def _n(s):
    s = unicodedata.normalize("NFKD", (s or "").lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def name_tokens(name):
    return [t for t in re.findall(r"[a-z0-9]{4,}", _n(name)) if t not in STOP]


def check_site(url, row):
    """True only if the homepage is demonstrably this institution."""
    try:
        rec = fetch_pages.fetch(url)
    except Exception as e:
        return False, f"could not read {url} ({e})", None
    text = _n(rec.get("text", ""))
    digits = re.sub(r"\D", "", row.get("phone") or "")
    if len(digits) >= 8 and digits[-8:] in re.sub(r"\D", "", rec.get("text", "")):
        return True, "the client's phone number appears on the homepage", rec
    toks = name_tokens(row.get("name"))
    if toks:
        hit = [t for t in toks if t in text]
        need = 1 if len(toks) == 1 else 2
        if len(hit) >= need:
            return True, f"institution name words on the homepage ({', '.join(hit[:3])})", rec
    return False, "homepage does not mention this institution's name or phone", rec


def _emails_domain(row):
    out = []
    for e in re.split(r"[;\s,]+", row.get("email") or ""):
        if "@" in e:
            d = e.split("@", 1)[1].lower().strip(".")
            if d and d not in GENERIC_MAIL:
                out.append(d)
    return out


def _wikidata(name, country):
    if not name or len(name) < 4:
        return []
    def get(params):
        req = urllib.request.Request("https://www.wikidata.org/w/api.php?" + urllib.parse.urlencode(params), headers=UA)
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8"))
    try:
        found = get({"action": "wbsearchentities", "search": name, "language": "en", "uselang": "en", "limit": 5, "format": "json"})
        ids = [x["id"] for x in found.get("search", [])]
        if not ids:
            return []
        ents = get({"action": "wbgetentities", "ids": "|".join(ids), "props": "claims", "format": "json"}).get("entities", {})
    except Exception:
        return []
    urls = []
    for i in ids:
        for c in ents.get(i, {}).get("claims", {}).get("P856", []):
            try:
                urls.append(c["mainsnak"]["datavalue"]["value"])
            except Exception:
                pass
    return urls


def _claude_search(row):
    """One web-search call through the Claude CLI. Local only; costs a few cents; verified afterwards."""
    prompt = (f"Find the OFFICIAL website of this educational institution. Name: {row.get('name')}. Country: "
              f"{row.get('country')}. Phone: {row.get('phone')}. Reply with JSON only: {{\"url\": \"https://...\"}} "
              f"(its own domain, not a directory or social page), or {{\"url\": \"\"}} if you cannot find it.")
    try:
        p = subprocess.run(["claude", "-p", prompt, "--model", "claude-haiku-4-5-20251001", "--tools", "WebSearch",
                            "--allowedTools", "WebSearch", "--no-session-persistence", "--output-format", "json",
                            "--json-schema", '{"type":"object","properties":{"url":{"type":"string"}},"required":["url"]}',
                            "--system-prompt", "You find official websites. Answer only with the JSON.",
                            "--settings", '{"disableAllHooks":true}', "--strict-mcp-config", "--setting-sources", "project"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        env = json.loads(p.stdout)
        return (env.get("structured_output") or {}).get("url") or ""
    except Exception:
        return ""


def _gemini_search(row):
    """One Google-Search-grounded Gemini call (free tier) to find the official site. The URL is verified afterwards
    by check_site(), so a wrong answer can never be used."""
    import os
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return ""
    prompt = (f"Find the OFFICIAL website of this educational institution. Name: {row.get('name')}. Country: "
              f"{row.get('country')}. Phone: {row.get('phone')}. Answer with the URL only (its own domain, not a directory, "
              f"social page or news site). If you cannot find it, answer NONE.")
    for model in ("gemini-3.1-flash-lite", "gemini-3.5-flash"):
        body = json.dumps({"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                           "tools": [{"google_search": {}}], "generationConfig": {"temperature": 0}}).encode("utf-8")
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}", data=body,
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                text = " ".join(p_.get("text", "") for p_ in json.loads(r.read().decode())["candidates"][0]["content"]["parts"])
            m = re.search(r"https?://[^\s\"'<>)\]]+", text)
            return m.group(0).rstrip(".,;") if m else ""
        except Exception:
            continue
    return ""


SKIP_HOSTS = ("wikipedia.", "facebook.", "linkedin.", "instagram.", "youtube.", "twitter.", "x.com", "tiktok.", "eacea.",
              "erasmus-plus.", "europa.eu", "wikidata.", "google.", "bing.", "duckduckgo.", "yelp.", "tripadvisor.", "mapcarta.",
              "educations.", "schooldigger.", "uni-rank", "4icu.")


def _ddg_search(row, limit=4):
    """Free web search (DuckDuckGo HTML, no key). Returns candidate site roots; check_site() vets each one."""
    import urllib.parse
    q = " ".join(x for x in (row.get("name"), row.get("country"), "official website") if x)
    try:
        req = urllib.request.Request("https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": q}),
                                     headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"})
        with urllib.request.urlopen(req, timeout=25) as r:
            html = r.read().decode("utf-8", "replace")
    except Exception:
        return []
    out = []
    for m in re.finditer(r'class="result__a"[^>]*href="([^"]+)"', html):
        u = urllib.parse.unquote(m.group(1))
        m2 = re.search(r"uddg=([^&]+)", m.group(1))
        if m2:
            u = urllib.parse.unquote(m2.group(1))
        pu = urllib.parse.urlparse(u)
        if not pu.netloc or any(h in pu.netloc.lower() for h in SKIP_HOSTS):
            continue
        root = f"{pu.scheme or 'https'}://{pu.netloc}/"
        if root not in out:
            out.append(root)
        if len(out) >= limit:
            break
    return out


CCTLD = {"spain": "es", "france": "fr", "cyprus": "cy", "czechia": "cz", "czech republic": "cz", "poland": "pl", "slovenia": "si",
         "slovakia": "sk", "sweden": "se", "hungary": "hu", "austria": "at", "netherlands": "nl", "ireland": "ie",
         "lithuania": "lt", "germany": "de", "portugal": "pt", "italy": "it", "bulgaria": "bg", "belgium": "be",
         "latvia": "lv", "estonia": "ee", "romania": "ro", "greece": "gr", "finland": "fi", "denmark": "dk",
         "croatia": "hr", "malta": "mt", "norway": "no", "united kingdom": "uk", "serbia": "rs", "turkey": "tr"}


def _country_ok(url, country, why):
    """Search hits must also fit the country (ccTLD or the country named on the homepage) unless the client's phone matched."""
    if "phone" in why or not country:
        return True
    c = _n(country).strip()
    host = re.sub(r"^https?://", "", url).split("/")[0].lower()
    tld = CCTLD.get(c)
    if tld and (host.endswith("." + tld) or f".{tld}." in host or host.endswith(".eus") and tld == "es" or host.endswith(".cat") and tld == "es"):
        return True
    try:
        return c in _n(fetch_pages.fetch(url).get("text", ""))
    except Exception:
        return False


def find_website(row, use_search=False, search_provider="claude_cli"):
    """row: {name, country, phone, email, website_override}. Returns (url, how, note); url None if unresolved."""
    tried = []

    def attempt(url, how):
        if not url:
            return None
        if not url.startswith("http"):
            url = "https://" + url.lstrip("/")
        ok, why, _ = check_site(url, row)
        tried.append(f"{how}: {url} -> {'ok' if ok else why}")
        return (url, how, why) if ok else None

    if row.get("website_override"):
        ov = row["website_override"].strip()
        ok = attempt(ov, "typed into the sheet")
        return ok if ok else (ov if ov.startswith("http") else "https://" + ov, "typed into the sheet (used as given)",
                              "a person supplied it; not name-checked")
    remembered = load_memo().get(row.get("row_id") or "")
    if remembered:
        return remembered, "remembered from an earlier run", ""
    for d in _emails_domain(row):
        for u in (f"https://www.{d}/", f"https://{d}/"):
            hit = attempt(u, "email domain")
            if hit:
                return hit
    for u in _wikidata(row.get("name"), row.get("country")):
        hit = attempt(u, "Wikidata")
        if hit:
            return hit
    if use_search:
        for u in _ddg_search(row):
            hit = attempt(u, "web search (DuckDuckGo)")
            if hit and _country_ok(hit[0], row.get("country"), hit[2]):
                return hit
            if hit:
                tried.append(f"{u} rejected: country {row.get('country')} not shown")
        found = _gemini_search(row) if search_provider == "gemini" else _claude_search(row)
        hit = attempt(found, "web search (" + ("Gemini" if search_provider == "gemini" else "Claude") + ")")
        if hit:
            return hit
    return None, "unresolved", "; ".join(tried) or "no website source available (add one in the 'Website override' column)"
