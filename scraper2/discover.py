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
import urllib.error
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


# Letters that Unicode does not split into "plain letter + accent", so stripping accents leaves them as they are:
# without this "Norges idrettshøgskole" on a homepage never matches "NORGES IDRETTSHOGSKOLE" in a sheet.
_PLAIN = str.maketrans({"ø": "o", "đ": "d", "ł": "l", "ß": "ss", "æ": "ae", "œ": "oe", "ı": "i", "þ": "th", "ð": "d"})


def _n(s):
    s = unicodedata.normalize("NFKD", (s or "").lower().translate(_PLAIN))
    return "".join(c for c in s if not unicodedata.combining(c))


def _fold(s):
    """Sheets often spell German names without umlauts (UNIVERSITAET, MUENCHEN) while the site writes them with
    (Universität, München). Folding ae/oe/ue to a/o/u on BOTH sides lets the two spellings meet."""
    return s.replace("ae", "a").replace("oe", "o").replace("ue", "u")


def name_tokens(name):
    return [t for t in re.findall(r"[a-z0-9]{4,}", _n(name)) if t not in STOP]


def _home(u):
    """The site's home: the domain root, or on a many-schools portal the school's own first path folder."""
    p = urllib.parse.urlparse(u)
    seg = next((s for s in p.path.split("/") if s), "")
    if seg and p.netloc.lower() in fetch_pages.PORTAL_HOSTS:
        return f"{p.scheme or 'https'}://{p.netloc}/{seg}/"
    return f"{p.scheme or 'https'}://{p.netloc}/"


def check_site(url, row):
    """True only if the homepage is demonstrably this institution."""
    try:
        rec = fetch_pages.fetch(url)
    except Exception as e:
        return False, f"could not read {url} ({e})", None
    text = _fold(_n(rec.get("text", "")))
    digits = re.sub(r"\D", "", row.get("phone") or "")
    if len(digits) >= 8 and digits[-8:] in re.sub(r"\D", "", rec.get("text", "")):
        return True, "the client's phone number appears on the homepage", rec
    toks = name_tokens(row.get("name"))
    if toks:
        hit = [t for t in toks if _fold(t) in text]
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
        root = _home(u)
        if root not in out:
            out.append(root)
        if len(out) >= limit:
            break
    return out


NO_EMAIL_HOSTS = ("facebook.", "linkedin.", "instagram.", "youtube.", "twitter.", "x.com", "tiktok.", "google.", "bing.",
                  "duckduckgo.", "wikipedia.", "wikidata.")
_search_lock = threading.Lock()


def _ddg_urls(query, limit=5):
    """Free web search (DuckDuckGo HTML, no key): full result URLs, in rank order."""
    with _search_lock:                                   # one search at a time, so parallel workers do not get blocked
        try:
            req = urllib.request.Request("https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query}),
                                         headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"})
            with urllib.request.urlopen(req, timeout=25) as r:
                html = r.read().decode("utf-8", "replace")
        except Exception:
            return []
        finally:
            time.sleep(1)
    out = []
    for m in re.finditer(r'class="result__a"[^>]*href="([^"]+)"', html):
        m2 = re.search(r"uddg=([^&]+)", m.group(1))
        u = urllib.parse.unquote(m2.group(1) if m2 else m.group(1))
        host = urllib.parse.urlparse(u).netloc.lower()
        if host and u not in out and not any(h in host for h in NO_EMAIL_HOSTS):
            out.append(u)
    return out[:limit]


def _gemini_urls(prompt, limit=5):
    """One Google-Search-grounded Gemini call (free tier). Returns the pages it searched and any URL it names.
    Its answer is never trusted as data - the caller opens each page and reads the address from it."""
    import os
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return []
    for model in ("gemini-3.1-flash-lite", "gemini-3.5-flash"):
        body = json.dumps({"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                           "tools": [{"google_search": {}}], "generationConfig": {"temperature": 0}}).encode("utf-8")
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}", data=body,
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                cand = json.loads(r.read().decode())["candidates"][0]
        except Exception:
            continue
        text = " ".join(p_.get("text", "") for p_ in (cand.get("content") or {}).get("parts", []))
        urls = [u.rstrip(".,;") for u in re.findall(r"https?://[^\s\"'<>)\]]+", text)]
        urls += [(c.get("web") or {}).get("uri", "") for c in (cand.get("groundingMetadata") or {}).get("groundingChunks", [])]
        return list(dict.fromkeys(u for u in urls if u))[:limit]
    return []


SEARCH_MODEL = "claude-haiku-4-5"
# running paid totals. input/output_tokens are Claude Haiku tokens; reader_micro_usd is paid reading by any other
# model, kept as millionths of a dollar because each model has its own price
SEARCH_USAGE = {"searches": 0, "input_tokens": 0, "output_tokens": 0, "reader_calls": 0, "reader_micro_usd": 0,
                "brave_searches": 0, "gemini_searches": 0}
# USD: Claude $10 per 1,000 searches + Haiku 4.5 tokens. Brave $5 per 1,000; its monthly free credit (about 1,000
# searches) is NOT subtracted here, so the batch total can only overstate what Brave really charges.
SEARCH_PRICE = {"search": 0.01, "input_per_m": 1.0, "output_per_m": 5.0, "brave": 0.005}
BUDGET = {"limit": None, "file": None}
_usage_lock = threading.Lock()


def spent_usd():
    u = SEARCH_USAGE
    return round(u["searches"] * SEARCH_PRICE["search"] + u["input_tokens"] / 1e6 * SEARCH_PRICE["input_per_m"]
                 + u["output_tokens"] / 1e6 * SEARCH_PRICE["output_per_m"] + u["reader_micro_usd"] / 1e6
                 + u["brave_searches"] * SEARCH_PRICE["brave"], 4)


def set_budget(limit_usd, path=None):
    """Cap what paid searches may cost. `path` keeps the running total on disk, because a batch runs as many
    separate jobs (one per chunk) and a total held only in memory would start from zero in each of them."""
    BUDGET.update(limit=limit_usd, file=Path(path) if path else None)
    for k in SEARCH_USAGE:
        SEARCH_USAGE[k] = 0
    if BUDGET["file"] and BUDGET["file"].exists():
        try:
            saved = json.loads(BUDGET["file"].read_text(encoding="utf-8"))
            SEARCH_USAGE.update({k: int(saved.get(k, 0)) for k in SEARCH_USAGE})
        except Exception:
            pass


def _save_usage():
    if BUDGET["file"]:
        try:
            BUDGET["file"].write_text(json.dumps({**SEARCH_USAGE, "spent_usd": spent_usd(), "limit_usd": BUDGET["limit"]}),
                                      encoding="utf-8")
        except Exception:
            pass


def record_reader(input_tokens, output_tokens, price=None):
    """A paid call that stood in for the free reader (llm._paid_fallback), counted against the same cap as the web
    searches. price = (USD per million input tokens, per million output tokens); None means Claude Haiku."""
    with _usage_lock:
        SEARCH_USAGE["reader_calls"] += 1
        if price:
            SEARCH_USAGE["reader_micro_usd"] += round(input_tokens * price[0] + output_tokens * price[1])
        else:
            SEARCH_USAGE["input_tokens"] += input_tokens
            SEARCH_USAGE["output_tokens"] += output_tokens
        _save_usage()


def budget_left():
    return BUDGET["limit"] is None or spent_usd() < BUDGET["limit"]


# Google search through the PAID Gemini key (the free key has no search). Google lets 5,000 searches a month go free
# on Gemini 3 models, then charges $14 per 1,000; every search is counted at that price here anyway, so the cap can
# only overstate what is really spent. Tokens at the paid Gemini reader's price.
GEMINI_SEARCH_MODEL = "gemini-3.1-flash-lite"
GEMINI_SEARCH_PRICE = {"per_search": 0.014, "input_per_m": 0.25, "output_per_m": 1.50}
SEARCH_TRIES, SEARCH_WAIT_SECONDS = 3, 10


def _resolve(url):
    """Google's grounding results are redirect links; the real page address is where they lead."""
    if "grounding-api-redirect" not in url:
        return url
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.geturl()
    except urllib.error.HTTPError as e:            # the target refused HEAD, but the redirect already told us where
        return e.geturl() if "grounding-api-redirect" not in (e.geturl() or "") else ""
    except Exception:
        return ""


def _gemini_paid_urls(prompt, limit=5, with_text=False):
    """One Google-search-grounded call with the paid Gemini key. Returns the pages it found (real addresses), and
    with with_text=True also the answer's text. The answer is never trusted as data: the caller opens each page and
    reads names and addresses from it."""
    import os
    key = os.environ.get("GEMINI_PAID_API_KEY")
    if not key or not budget_left():
        return ([], "") if with_text else []
    body = json.dumps({"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                       "tools": [{"google_search": {}}], "generationConfig": {"temperature": 0}}).encode("utf-8")
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_SEARCH_MODEL}:generateContent?key={key}",
        data=body, headers={"Content-Type": "application/json"})
    for attempt in range(1, SEARCH_TRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                resp = json.loads(r.read().decode())
            break
        except urllib.error.HTTPError as e:
            # Workers searching at once hit Google's per-minute limit: wait and try again rather than lose the search.
            if e.code in (429, 500, 503) and attempt < SEARCH_TRIES:
                time.sleep(SEARCH_WAIT_SECONDS * attempt)
                continue
            print(f"(Gemini search unavailable: {str(e)[:120]})", file=sys.stderr, flush=True)
            return ([], "") if with_text else []
        except Exception as e:
            print(f"(Gemini search unavailable: {str(e)[:120]})", file=sys.stderr, flush=True)
            return ([], "") if with_text else []
    use = resp.get("usageMetadata") or {}
    p = GEMINI_SEARCH_PRICE
    cand = (resp.get("candidates") or [{}])[0]
    # Google bills every query the model runs (often 2-4 per call), the search results fed back in as input
    # (toolUsePromptTokenCount) and the thinking tokens as output; counting only one search and the visible
    # tokens put the estimate a third below the real bill.
    queries = max(1, len((cand.get("groundingMetadata") or {}).get("webSearchQueries") or []))
    tokens_in = (use.get("promptTokenCount") or 0) + (use.get("toolUsePromptTokenCount") or 0)
    tokens_out = (use.get("candidatesTokenCount") or 0) + (use.get("thoughtsTokenCount") or 0)
    with _usage_lock:
        SEARCH_USAGE["gemini_searches"] += queries
        SEARCH_USAGE["reader_micro_usd"] += round(queries * p["per_search"] * 1e6 + tokens_in * p["input_per_m"]
                                                  + tokens_out * p["output_per_m"])
        _save_usage()
    text = " ".join(x.get("text", "") for x in (cand.get("content") or {}).get("parts", []))
    urls = [u.rstrip(".,;:") for u in re.findall(r"https?://[^\s\"'<>)\]*`|]+", text)]     # no markdown ** or ` around it
    urls += [(c.get("web") or {}).get("uri", "") for c in (cand.get("groundingMetadata") or {}).get("groundingChunks", [])]
    out = []
    for u in dict.fromkeys(u for u in urls if u):
        real = _resolve(u)
        if real and real not in out:
            out.append(real)
        if len(out) >= limit:
            break
    return (out, text) if with_text else out


def _claude_urls(prompt, limit=5):
    """One Claude API call with the server-side web search tool (max one search: $10 per 1,000 searches plus
    tokens). Returns the result URLs of that search, in rank order. Only the URLs are used - the caller opens
    each page and reads the address from it, so nothing the model writes can end up in the sheet."""
    import os
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key or not budget_left():
        return []
    body = json.dumps({"model": SEARCH_MODEL, "max_tokens": 1024,
                       "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 1}],
                       "messages": [{"role": "user", "content": prompt}]}).encode("utf-8")
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body,
                                 headers={"Content-Type": "application/json", "x-api-key": key,
                                          "anthropic-version": "2023-06-01"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            resp = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        detail = e.read().decode("utf-8", "replace")[:200] if hasattr(e, "read") else str(e)
        print(f"(Claude web search unavailable: {detail})", file=sys.stderr, flush=True)
        return []
    u = resp.get("usage") or {}
    with _usage_lock:
        SEARCH_USAGE["searches"] += (u.get("server_tool_use") or {}).get("web_search_requests", 0)
        SEARCH_USAGE["input_tokens"] += u.get("input_tokens", 0)
        SEARCH_USAGE["output_tokens"] += u.get("output_tokens", 0)
        _save_usage()
    urls = []
    for block in resp.get("content", []):
        results = block.get("content") if block.get("type") == "web_search_tool_result" else None
        if isinstance(results, list):                          # an error comes back as an object, not a list
            urls += [x.get("url", "") for x in results if x.get("type") == "web_search_result"]
    return [x for x in dict.fromkeys(urls) if x and not any(h in urllib.parse.urlparse(x).netloc.lower()
                                                            for h in NO_EMAIL_HOSTS)][:limit]


def _brave_urls(query, limit=5):
    """One Brave Search API request (key in BRAVE_API_KEY): result URLs in rank order. About 1,000 searches a month
    are covered by Brave's free credit, then $5 per 1,000 - a quarter of what a Claude web search costs."""
    import os
    key = os.environ.get("BRAVE_API_KEY")
    if not key or not budget_left():
        return []
    with _search_lock:                                   # the free plan allows one request a second
        try:
            req = urllib.request.Request("https://api.search.brave.com/res/v1/web/search?" +
                                         urllib.parse.urlencode({"q": query, "count": 10}),
                                         headers={"Accept": "application/json", "X-Subscription-Token": key})
            with urllib.request.urlopen(req, timeout=25) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:
            detail = e.read().decode("utf-8", "replace")[:200] if hasattr(e, "read") else str(e)
            print(f"(Brave search unavailable: {detail})", file=sys.stderr, flush=True)
            return []
        finally:
            time.sleep(1.1)
    with _usage_lock:
        SEARCH_USAGE["brave_searches"] += 1
        _save_usage()
    urls = [x.get("url", "") for x in (data.get("web") or {}).get("results") or []]
    return [x for x in dict.fromkeys(urls) if x and not any(h in urllib.parse.urlparse(x).netloc.lower()
                                                            for h in NO_EMAIL_HOSTS)][:limit]


def person_pages(name, institution, domain, limit=4):
    """Web pages likely to publish this person's own work email. Candidates only: nothing here is used
    unless the page itself, once opened, shows the name and the address. Brave's search API when a Brave key
    is set (cheapest); otherwise Claude's web search when an Anthropic key is set (paid per search); then the
    free DuckDuckGo page, which gets blocked quickly. Gemini's Google-grounded search is no longer tried here:
    it is not available on the free tier and each attempt spent calls from the free reading allowance."""
    import os
    query = f'"{name}" {domain} email'
    if os.environ.get("BRAVE_API_KEY"):
        urls = _brave_urls(query, limit)
    elif os.environ.get("GEMINI_PAID_API_KEY"):
        urls = _gemini_paid_urls(f"Search Google for: {query}\nList the URLs of pages that publish the work email "
                                 f"address of {name} at {institution}.", limit)
    else:
        urls = _claude_urls(f'Search the web once for: "{name}" {domain} email\n'
                            f"The goal is to find pages that publish the work email address of {name} at {institution}. "
                            f"After the search, reply with one short line.", limit)
    if not urls:
        urls = _ddg_urls(query, limit)
    return urls[:limit]


def staff_pages(institution, domain, limit=4):
    """Pages on the institute's OWN site likely to name its leadership or staff (rector, director, head teacher,
    international office, team), for the deep re-check: the crawl reads them first. The free DuckDuckGo page first;
    Claude's web search (paid, counted against the batch cap) only if that finds nothing."""
    import os
    query = (f'site:{domain} (rector OR president OR director OR principal OR "head teacher" OR headmaster OR '
             f'"international office" OR erasmus OR staff OR team OR contact)')
    urls = _ddg_urls(query, limit * 2)
    if not urls and os.environ.get("GEMINI_PAID_API_KEY"):
        urls = _gemini_paid_urls(f"Search Google for pages on {domain} that name the leadership (rector, president, "
                                 f"director, principal, head teacher), the international or Erasmus office, or the "
                                 f"staff of {institution}. List the URLs.", limit * 2)
    if not urls and os.environ.get("ANTHROPIC_API_KEY") and budget_left():
        urls = _claude_urls(f"Search the web once for: {query}\nThe goal is to find pages on {domain} that name the "
                            f"leadership, the international/Erasmus office or the staff of {institution}. "
                            f"After the search, reply with one short line.", limit * 2)
    own = [u for u in urls if domain in re.sub(r"^https?://", "", u).split("/")[0].lower()]
    return own[:limit]


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


def searchable(name):
    """A name worth a paid search: real words, not a phone number or code that landed in the name column."""
    return len(re.findall(r"[^\W\d_]", name or "")) >= 4


def _claude_sites(row, limit=4):
    """Candidate site roots from one Claude web search. check_site() must still prove each one."""
    q = " ".join(x for x in (row.get("name"), row.get("country")) if x)
    urls = _claude_urls(f"Search the web once for: {q} official website\n"
                        f"The goal is to find the official website of this educational institution. "
                        f"After the search, reply with one short line.", 8)
    roots = []
    for u in urls:
        pu = urllib.parse.urlparse(u)
        root = _home(u)
        if pu.netloc and root not in roots and not any(h in pu.netloc.lower() for h in SKIP_HOSTS):
            roots.append(root)
    return roots[:limit]


def _gemini_paid_sites(row, limit=4):
    """Candidate official sites from one Google search through the paid Gemini key. Each is still proved by
    check_site() (the homepage must name the institution) and _country_ok() before it is used."""
    """Returns (homepage URLs, the institution's official name in its own language or "")."""
    found, text = _gemini_paid_urls(
        f"Search Google for the OFFICIAL website of this educational institution: {row.get('name')} "
        f"({row.get('country') or 'country unknown'}). Answer in exactly two lines:\nURL: <its own site, not a directory, "
        f"social page or news site>\nLOCAL NAME: <its official name in its own language, as written on that site>",
        limit * 2, with_text=True)
    m = re.search(r"LOCAL NAME:\s*\**\s*([^\n*]{3,120})", text or "")
    local = m.group(1).strip() if m else ""
    homes, seen = [], set()
    for u in found:
        p = urllib.parse.urlparse(u)
        home = _home(u)
        if not p.netloc or any(h in p.netloc.lower() for h in SKIP_HOSTS) or home in seen:
            continue
        seen.add(home)
        homes.append(home)
    return homes[:limit], local


def find_website(row, use_search=False, search_provider="claude_cli", paid_search=False, gemini_search=False):
    """row: {name, country, phone, email, website_override}. Returns (url, how, note); url None if unresolved.
    paid_search adds one Claude web search after the free one. It is OFF by default: in a 25-institute trial it
    recovered 3 sites for 23 paid searches, and one of the 3 was a university directory that passed check_site
    because it mentions the institution's name.
    gemini_search: one Google search through the PAID Gemini key (GEMINI_PAID_API_KEY; the free key has no search),
    counted against the batch cap. Every site it suggests must still pass check_site and the country check."""
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
    # The sheet's own website column comes before anything guessed from an email address: an institute's mail
    # domain is often not its website (hsmarnix.nl vs marnixacademie.nl). It still has to pass the homepage
    # proof, because sheets also contain wrong links; a social-media page is never taken as the website.
    given = (row.get("website") or "").strip()
    pu = urllib.parse.urlparse(given if "//" in given else "https://" + given) if given else None
    if pu and pu.netloc and not any(h in pu.netloc.lower() for h in SKIP_HOSTS):
        host = pu.netloc.lower()[4:] if pu.netloc.lower().startswith("www.") else pu.netloc.lower()
        agrees = any(host == d or host.endswith("." + d) or d.endswith("." + host) for d in _emails_domain(row))
        for u in dict.fromkeys((_home(urllib.parse.urlunparse(pu)), urllib.parse.urlunparse(pu))):
            hit = attempt(u, "website column in the sheet")
            if hit:
                return hit
            # The homepage was read but does not show the name as the sheet writes it (renamed institutes,
            # transliterated names: "MAELARDALENS HOEGSKOLA" is mdu.se, "Waterford IT" is now setu.ie). When the
            # sheet's own contact email is on that same domain, two separate columns agree, and that is taken as
            # proof. A link whose domain differs from the email's is still refused (wrong links do occur).
            if agrees and "does not mention" in tried[-1]:
                return u, "website column in the sheet (same domain as the contact's email)", "the sheet's website and email agree"
    for d in _emails_domain(row):
        for u in (f"https://www.{d}/", f"https://{d}/"):
            hit = attempt(u, "email domain")
            if hit:
                return hit
    for u in _wikidata(row.get("name"), row.get("country")):
        hit = attempt(u, "Wikidata")
        if hit:
            return hit
    if use_search and not searchable(row.get("name")):
        tried.append("no web search: the institution name is not a usable name")
    elif use_search:
        import os
        if gemini_search and os.environ.get("GEMINI_PAID_API_KEY"):
            homes, local = _gemini_paid_sites(row)
            for u in homes:
                hit = attempt(u, "web search (Google via Gemini)")
                if not hit and local and _n(local) != _n(row.get("name")):
                    # the site names itself in its own language ('Univerzita Komenského' for Comenius University)
                    ok, why, _ = check_site(u, {**row, "name": local})
                    tried.append(f"same site under its own name '{local}' -> {'ok' if ok else why}")
                    hit = (u, f"web search (Google via Gemini), named '{local}' on the site", why) if ok else None
                if hit and _country_ok(hit[0], row.get("country"), hit[2]):
                    return hit
                if hit:
                    tried.append(f"{u} rejected: country {row.get('country')} not shown")
        sources = [("web search (DuckDuckGo)", _ddg_search)] + ([("web search (Claude)", _claude_sites)] if paid_search else [])
        for how, candidates in sources:
            for u in candidates(row):                 # the paid Claude search only runs if the free one proved nothing
                hit = attempt(u, how)
                if hit and _country_ok(hit[0], row.get("country"), hit[2]):
                    return hit
                if hit:
                    tried.append(f"{u} rejected: country {row.get('country')} not shown")
        if search_provider != "gemini":
            found = _claude_search(row)
            hit = attempt(found, "web search (Claude)")
            if hit:
                return hit
    return None, "unresolved", "; ".join(tried) or "no website source available (add one in the 'Website override' column)"
