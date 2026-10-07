"""Deterministic verification of scraper claims against the pages they cite.

No model is involved: every check is a string comparison against the cached copy of
the cited page (scraper/fetch_pages.py's cache). A claim is 'verified' only if the code
finds its evidence on that page. Anything else is reported with the reason, never
silently accepted.

A claim whose cited page simply isn't in today's cache comes back as `verified=None`,
not `False` - "can't check right now" is a different outcome from "checked and it
failed", and run_batch.merge() relies on that distinction to avoid downgrading a claim
that was genuinely verified in an earlier run (in a since-discarded cache) just because
this run's cache happens to be empty.

What 'verified' means, precisely: the quote is on the cited page, the entity named in the
claim is on that page (email/phone literally, never inferred), and the page is on the
institute's own domain. It does NOT mean the page is current or correct - only that the
claim matches its source.
"""
import re
import sys
import unicodedata
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_pages import cached_record  # noqa: E402

ELLIPSIS = re.compile(r"\s*(?:\.\.\.|…)\s*")


def normalize(s):
    """Case-, accent-, whitespace-, quote- and dash-insensitive form for comparison."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = (s.replace("‘", "'").replace("’", "'").replace("“", '"').replace("”", '"')
          .replace("–", "-").replace("—", "-").replace("\xa0", " "))
    return re.sub(r"\s+", " ", s).strip().casefold()


def quote_in_text(quote, text):
    """True if the quote is on the page. '...' in a quote means 'text elided here', so
    each fragment must appear, in order; a fragment too short to mean anything (<8 chars)
    is not accepted on its own."""
    frags = [f for f in ELLIPSIS.split(quote or "") if normalize(f)]
    if not frags:
        return False
    hay, pos = normalize(text), 0
    for f in frags:
        n = normalize(f)
        if len(frags) == 1 and len(n) < 3:
            return False
        i = hay.find(n, pos)
        if i < 0:
            return False
        pos = i + len(n)
    return True


def email_in_text(email, text):
    return bool(email) and email.strip().lower() in (text or "").lower()


def phone_in_text(phone, text):
    """Compare on digits only (page formatting varies: '+353 (0)21 432 6100')."""
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) < 7:
        return False
    flat = re.sub(r"[\s().\-/]", "", text or "")
    return digits[-8:] in re.sub(r"\D", "", flat) or digits[-8:] in flat


def _host(url):
    h = urlparse((url or "").strip()).netloc.lower()
    return h[4:] if h.startswith("www.") else h


def is_official(source_url, official_website):
    """Source must be on the institute's own domain (or a subdomain of it)."""
    s, o = _host(source_url), _host(official_website)
    if not s or not o:
        return False
    # same host, or one is a subdomain of the other - never "same country/TLD suffix"
    # (that would accept any other .ac.cy or .edu site as official)
    return s == o or s.endswith("." + o) or o.endswith("." + s)


def _page(url):
    rec = cached_record(url)
    return (rec["text"], rec) if rec else (None, None)


def verify_partner_or_campus(item, official_website, name_key):
    """item has source_url + evidence_quote + a name field. Returns (verified, note).
    `verified` is None (not False) when the cited page simply isn't in today's cache - that's
    "can't check right now", not "checked and it's wrong", and callers (run_batch.merge()) treat
    the two very differently."""
    url, quote = item.get("source_url") or "", item.get("evidence_quote") or ""
    if not url or not quote:
        return False, "no source URL or quote given"
    text, rec = _page(url)
    if text is None:
        return None, "cited page was not read by the fetcher (cannot check)"
    if not quote_in_text(quote, text):
        return False, "quote not found on the cited page"
    if not is_official(url, official_website):
        return False, "source is not on the institute's own domain"
    note = "quote found on page"
    if rec.get("certificate_unverified"):
        note += " (site certificate could not be verified)"
    return True, note


def verify_contact(contact, official_website):
    """contact: {name, email, phone, source_url, evidence_quote}. Returns (verified, note)."""
    if not contact or not contact.get("name"):
        return False, "no contact found"
    url, quote = contact.get("source_url") or "", contact.get("evidence_quote") or ""
    if not url:
        return False, "no source URL given"
    text, rec = _page(url)
    if text is None:
        # None, not False: the page just isn't in today's cache (see verify_partner_or_campus).
        return None, "cited page was not read by the fetcher (cannot check)"
    problems = []
    if not quote_in_text(quote, text):
        problems.append("quote not on page")
    if normalize(contact["name"]) not in normalize(text):
        problems.append("name not on page")
    # A personal email read from the person's own profile page is checked against THAT page.
    email_url = contact.get("email_source_url") or url
    email_text = text if email_url == url else (_page(email_url)[0] or "")
    if contact.get("email") and not email_in_text(contact["email"], email_text):
        problems.append("email not on page")
    # That page may be off-site (found by web search), so the ADDRESS itself must be on the institute's mail domain.
    if email_url != url and not is_official("https://" + contact["email"].split("@", 1)[-1], official_website):
        problems.append("email is not on the institute's own domain")
    if contact.get("phone") and not phone_in_text(contact["phone"], text):
        problems.append("phone not on page")
    if not is_official(url, official_website):
        problems.append("source not on the institute's own domain")
    if problems:
        return False, "; ".join(problems)
    note = "name" + (", email" if contact.get("email") else "") + (", phone" if contact.get("phone") else "") + " found on page"
    if rec.get("certificate_unverified"):
        note += " (site certificate could not be verified)"
    return True, note
