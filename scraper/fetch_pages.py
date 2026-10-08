"""Polite page fetcher used by the scraping skills.

    python scraper/fetch_pages.py URL [--links] [--offset N] [--max-chars N] [--refresh]

What it does, so the skills never crawl blindly:
  * checks robots.txt for the host and refuses disallowed pages
  * waits at least MIN_GAP seconds between requests to the same host
  * caches every page in data/cache/ (a page is downloaded once)
  * turns HTML into readable text (tables keep " | " between cells, mailto:/tel: links
    are written out as <address>) and PDFs into text with "--- page N ---" markers
  * prints the text in slices, so long partner lists can be read with --offset

Uses only lxml, pypdf and the standard library.
"""
import argparse
import hashlib
import io
import json
import os
import re
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
import urllib.robotparser
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse, urlunparse

from lxml import html as lhtml

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache"
UA = "ParagonResearchBot/1.0 (institution contact research)"
MIN_GAP = float(os.environ.get("FETCH_MIN_GAP", "1.0"))   # seconds between requests to one host
TIMEOUT = 25
MAX_BYTES = 15 * 1024 * 1024
MAX_PDF_PAGES = 250

BLOCK = {"p", "div", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "section",
         "article", "header", "footer", "address", "table", "dt", "dd", "blockquote"}
DROP = ("script", "style", "noscript", "svg", "iframe", "template")
# A logo-only membership badge (e.g. an association's logo with no surrounding text) would
# otherwise vanish entirely - text_content() only reads text nodes, never an <img>'s alt attribute.
# Generic alt text like "logo" carries no institutional information, so it's skipped rather than
# turned into meaningless "[logo] [logo] [logo]" noise.
GENERIC_ALT = {"", "logo", "icon", "image", "photo", "picture", "banner", "placeholder", "spacer",
               "arrow", "bullet"}

_robots = {}


class FetchError(Exception):
    pass


def iri_to_uri(url):
    """Percent-encode non-ASCII characters (/ueber-uns/ written as /über-uns/, IDN hosts) so urllib can send the
    request. Without this every German/Czech/Polish/Nordic page with an accented path crashed the fetcher."""
    p = urlparse(url)
    host = p.netloc
    if not host.isascii():
        try:
            host = host.encode("idna").decode("ascii")
        except Exception:
            pass
    return urlunparse((p.scheme, host, quote(p.path, safe="/%:@&=+$,!~*'();"), p.params,
                       quote(p.query, safe="=&%+/:@?,;!~*'()"), quote(p.fragment, safe="")))


def _open(url, accept="*/*"):
    url = iri_to_uri(url)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept,
                                               "Accept-Language": "en;q=0.9,*;q=0.5"})
    return urllib.request.urlopen(req, timeout=TIMEOUT)


def robots_allowed(url):
    p = urlparse(url)
    host = f"{p.scheme}://{p.netloc}"
    if host not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        try:
            with _open(host + "/robots.txt", "text/plain") as r:
                rp.parse(r.read(500_000).decode("utf-8", "replace").splitlines())
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                rp.disallow_all = True
            else:
                rp.allow_all = True
        except Exception:
            rp.allow_all = True
        _robots[host] = rp
    return _robots[host].can_fetch(UA, url)


_host_locks = {}
_locks_guard = threading.Lock()


def _throttle(host):
    """At most one request per MIN_GAP seconds per host, across threads (per-host lock) and
    across processes (shared state file). Different hosts never wait for each other."""
    with _locks_guard:
        lock = _host_locks.setdefault(host, threading.Lock())
    with lock:
        CACHE.mkdir(parents=True, exist_ok=True)
        state_file = CACHE / "_last_request.json"
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            state = {}
        wait = MIN_GAP - (time.time() - state.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        state[host] = time.time()
        tmp = state_file.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        os.replace(tmp, state_file)


def cf_decode(hexstr):
    """Cloudflare 'email protection' hides addresses as hex: first byte is an XOR key."""
    try:
        b = bytes.fromhex(hexstr)
        return "".join(chr(c ^ b[0]) for c in b[1:])
    except Exception:
        return ""


def _html_to_text(data, charset, base_url):
    try:
        text = data.decode(charset or "utf-8", "replace")
        doc = lhtml.fromstring(text)
    except Exception:
        doc = lhtml.fromstring(data)
    links, seen = [], set()
    for el in doc.iter():
        cf = el.get("data-cfemail") if isinstance(el.tag, str) else None
        if cf and cf_decode(cf):
            el.text = cf_decode(cf)                 # "[email protected]" -> the real address
            for child in list(el):
                el.remove(child)
    for img in doc.iter("img"):
        alt = (img.get("alt") or img.get("title") or "").strip()
        if len(alt) < 2 or alt.casefold() in GENERIC_ALT or img.text:
            continue
        img.text = f"[{alt}]"   # makes a logo-only membership badge readable, and gives a bare
                                 # <a><img alt="EAEC"></a> a real link label too (next loop reads it)
    for a in doc.iter("a"):
        href = (a.get("href") or "").strip()
        label = " ".join(a.text_content().split())
        if "/cdn-cgi/l/email-protection#" in href:
            addr = cf_decode(href.split("#", 1)[1])
            if addr:
                a.text = a.text if a.text and "@" in a.text and "[email" not in a.text else addr
                a.tail = f" <{addr}>" + (a.tail or "")
            continue
        if href.lower().startswith("mailto:"):
            a.tail = f" <{href[7:].split('?')[0]}>" + (a.tail or "")
        elif href.lower().startswith("tel:"):
            a.tail = f" <tel {href[4:]}>" + (a.tail or "")
        elif href and not href.startswith(("#", "javascript:")):
            url = urljoin(base_url, href)
            if url not in seen:
                seen.add(url)
                links.append((label[:90], url))
    # links were read above, so menus can now be dropped from the text without losing them
    for el in list(doc.iter(*DROP, "nav")):
        el.drop_tree()
    for el in doc.iter():
        tag = el.tag if isinstance(el.tag, str) else ""
        if tag in ("td", "th"):
            el.tail = " | " + (el.tail or "")
        elif tag == "tr" or tag in BLOCK or tag == "br":
            el.tail = "\n" + (el.tail or "")
    raw = doc.text_content()
    lines = [re.sub(r"[ \t\r\f\v]+", " ", ln).strip() for ln in raw.split("\n")]
    out, blank = [], 0
    for ln in lines:
        if ln:
            out.append(ln)
            blank = 0
        elif blank == 0 and out:
            out.append("")
            blank = 1
    return "\n".join(out).strip(), links


def _pdf_to_text(data):
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    parts = []
    for i, page in enumerate(reader.pages[:MAX_PDF_PAGES], start=1):
        try:
            parts.append(f"--- page {i} ---\n{page.extract_text() or ''}")
        except Exception:
            parts.append(f"--- page {i} --- (unreadable)")
    if len(reader.pages) > MAX_PDF_PAGES:
        parts.append(f"[PDF has {len(reader.pages)} pages; only the first {MAX_PDF_PAGES} read]")
    return "\n".join(parts), []


def fetch(url, refresh=False):
    if urlparse(url).scheme not in ("http", "https"):
        raise FetchError("only http(s) URLs are allowed")
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / (hashlib.sha1(url.encode()).hexdigest() + ".json")
    if path.exists() and not refresh:
        return json.loads(path.read_text(encoding="utf-8"))
    if not robots_allowed(url):
        raise FetchError("blocked by robots.txt - do not fetch this page")
    _throttle(urlparse(url).netloc)
    try:
        with _open(url, "text/html,application/pdf;q=0.9,*/*;q=0.5") as r:
            final_url = r.geturl()
            ctype = (r.headers.get_content_type() or "").lower()
            charset = r.headers.get_content_charset()
            data = r.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        raise FetchError(f"HTTP {e.code}")
    except urllib.error.URLError as e:
        # urllib wraps certificate failures inside URLError(reason=SSLCertVerificationError). Some institution
        # sites (seen: fernuni-hagen.de, univ-lemans.fr) serve an incomplete certificate chain: retry once
        # without verification for THAT failure only, and mark the record so a human knows the check was weaker.
        if not isinstance(getattr(e, "reason", None), ssl.SSLCertVerificationError):
            raise FetchError(f"{type(e).__name__}: {e}")
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            req = urllib.request.Request(iri_to_uri(url), headers={"User-Agent": UA, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as r:
                final_url = r.geturl()
                ctype = (r.headers.get_content_type() or "").lower()
                charset = r.headers.get_content_charset()
                data = r.read(MAX_BYTES + 1)
            unverified = True
        except Exception:
            raise FetchError(f"SSLCertVerificationError: {e}")
    except Exception as e:
        raise FetchError(f"{type(e).__name__}: {e}")
    else:
        unverified = False
    if len(data) > MAX_BYTES:
        raise FetchError("file larger than 15 MB")
    if ctype == "application/pdf" or data[:5] == b"%PDF-":
        text, links = _pdf_to_text(data)
        ctype = "application/pdf"
    elif "html" in ctype or "xml" in ctype or not ctype:
        text, links = _html_to_text(data, charset, final_url)
    else:
        raise FetchError(f"unsupported content type {ctype}")
    rec = {"url": url, "final_url": final_url, "content_type": ctype,
           "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "text": text, "links": links, "certificate_unverified": unverified}
    path.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    # cached_record() builds its lookup of the cache once per process. Without this, every page fetched AFTER the
    # verifier's first look was invisible to it, and claims citing those pages were reported as "cited page was
    # not read" - in a batch that meant only the first few institutes of each job could ever be verified.
    if _cache_index is not None:
        for u in (url, final_url):
            _cache_index[_url_key(u)] = path
    return rec


def fetch_raw(url, max_bytes=3_000_000):
    """Raw text of a small non-HTML resource (sitemap.xml). Same robots.txt check and politeness as fetch();
    not cached (it is small and only used to discover URLs). Returns "" if unavailable."""
    if urlparse(url).scheme not in ("http", "https") or not robots_allowed(url):
        return ""
    _throttle(urlparse(url).netloc)
    try:
        with _open(url, "application/xml,text/xml,text/plain,*/*") as r:
            return r.read(max_bytes).decode("utf-8", "replace")
    except Exception:
        return ""


def _url_key(url):
    """Loose URL identity for cache lookups: scheme, 'www.', fragment and a trailing
    slash don't make two URLs different pages."""
    p = urlparse((url or "").strip())
    host = p.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host + (p.path.rstrip("/") or "") + (("?" + p.query) if p.query else "")


_cache_index = None


def cached_record(url):
    """Return the cached page record for `url` (matching either the requested URL or
    the final URL after redirects), or None. Never touches the network - this is what
    the verifier uses to check a claim against the page it cites."""
    global _cache_index
    if _cache_index is None:
        _cache_index = {}
        for f in CACHE.glob("*.json"):
            if f.name.startswith("_"):
                continue
            try:
                rec = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            for u in (rec.get("url"), rec.get("final_url")):
                if u:
                    _cache_index[_url_key(u)] = f
    f = _cache_index.get(_url_key(url))
    if not f:
        return None
    return json.loads(f.read_text(encoding="utf-8"))


def cached_text(url):
    rec = cached_record(url)
    return rec["text"] if rec else None


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    ap.add_argument("--links", action="store_true", help="also list the page's links")
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--max-chars", type=int, default=40000)
    ap.add_argument("--refresh", action="store_true", help="ignore the cache")
    a = ap.parse_args()
    try:
        rec = fetch(a.url, a.refresh)
    except FetchError as e:
        print(f"ERROR: {e}")
        sys.exit(1)
    print(f"URL: {rec['final_url']}\nFETCHED: {rec['fetched_at']}\nTYPE: {rec['content_type']}")
    if rec.get("certificate_unverified"):
        print("WARNING: this site's TLS certificate could not be verified (incomplete chain); "
              "content below was fetched without verification - flag needs_human_check.")
    if a.links:
        print("LINKS:")
        for label, url in rec["links"][:150]:
            print(f"  {label} -> {url}")
    text = rec["text"]
    print(f"TEXT (chars {a.offset}-{min(len(text), a.offset + a.max_chars)} of {len(text)}):")
    print(text[a.offset:a.offset + a.max_chars])
    if a.offset + a.max_chars < len(text):
        print(f"\n[truncated: continue with --offset {a.offset + a.max_chars}]")


if __name__ == "__main__":
    main()
