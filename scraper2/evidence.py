"""Turn crawled pages into an 'evidence pack': numbered lines, each tagged with its page.

The model never sees free text it could re-type. It sees `L17| ...` and answers with LINE
NUMBERS; code then copies the text of those lines. So every quote is verbatim by construction,
and an email/phone can only come from a line that really contains it.
"""
import re

ANGLE_EMAIL = re.compile(r"<([^<>\s@]+@[^<>\s]+)>")        # the fetcher writes every mailto: link as <address>
TLDS = {"com", "org", "net", "edu", "gov", "info", "eu", "int", "biz", "io", "ac", "co", "uk", "de", "fr", "es", "it", "pt", "nl",
        "be", "at", "ch", "pl", "cz", "sk", "hu", "si", "hr", "ro", "bg", "gr", "cy", "mt", "ie", "dk", "se", "no", "fi", "ee",
        "lv", "lt", "lu", "is", "tr", "rs", "ua", "ru", "mk", "al", "ba", "me", "xk", "cat", "eus", "gal", "ad", "li", "us", "ca"}
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?:\+|00)?\d[\d\s().\-/]{7,}\d")
MAX_LINE = 500


class Pack:
    def __init__(self):
        self.lines = []          # [{"n": int, "url": str, "text": str}]
        self.by_n = {}
        self.pages = []          # [(index, url)]

    def add(self, url, text):
        self.lines.append({"n": len(self.lines) + 1, "url": url, "text": text})
        self.by_n[self.lines[-1]["n"]] = self.lines[-1]

    def get(self, n):
        return self.by_n.get(int(n)) if isinstance(n, (int, str)) and str(n).lstrip("-").isdigit() else None

    def window(self, first_n, last_n):
        """A view of lines first_n..last_n that KEEPS the global line numbers, so a model answering about
        just this window still points at numbers the rest of the pipeline can resolve."""
        w = Pack()
        w.lines = [l for l in self.lines if first_n <= l["n"] <= last_n]
        w.by_n = {l["n"]: l for l in w.lines}
        return w

    def prompt_text(self):
        out, last = [], None
        for ln in self.lines:
            if ln["url"] != last:
                out.append(f"@@ PAGE {ln['url']}")
                last = ln["url"]
            out.append(f"L{ln['n']}| {ln['text']}")
        return "\n".join(out)


def _clean_lines(text):
    for raw in (text or "").split("\n"):
        t = re.sub(r"\s+", " ", raw).strip()
        if len(t) >= 3:
            yield t[:MAX_LINE]


def build_pack(pages, budget_chars=90000, min_page_chars=4000):
    """pages: [{url, text, score}]. Highest-scoring pages get their full text first; menus and
    other lines repeated across pages are dropped once."""
    ordered = sorted(pages, key=lambda p: -p.get("score", 0))
    per_page = {p["url"]: [] for p in ordered}
    seen = set()
    for p in ordered:
        for t in _clean_lines(p["text"]):
            key = t.casefold()
            if key in seen:
                continue
            seen.add(key)
            per_page[p["url"]].append(t)

    sizes = {u: sum(len(t) + 8 for t in ls) for u, ls in per_page.items()}
    quota = {u: min(sizes[u], max(min_page_chars, budget_chars // max(len(ordered), 1))) for u in sizes}
    spare = budget_chars - sum(quota.values())
    for p in ordered:                                   # hand leftover budget to pages that were cut
        if spare <= 0:
            break
        extra = min(sizes[p["url"]] - quota[p["url"]], spare)
        if extra > 0:
            quota[p["url"]] += extra
            spare -= extra

    pack = Pack()
    for p in ordered:
        used = 0
        for t in per_page[p["url"]]:
            if used + len(t) + 8 > quota[p["url"]]:
                break
            pack.add(p["url"], t)
            used += len(t) + 8
        pack.pages.append((len(pack.pages) + 1, p["url"]))
    return pack


def clean_email(e):
    """Sites that print an address twice ('a@x.atA.B') glue two strings together. Cut after a known TLD that is
    immediately followed by an uppercase letter or a duplicated local part."""
    e = e.rstrip(".,;:")
    local, _, domain = e.partition("@")
    labels = domain.split(".")
    for i, lab in enumerate(labels[1:], start=1):
        for tld in sorted(TLDS, key=len, reverse=True):
            if lab.startswith(tld) and len(lab) > len(tld) and lab[len(tld)].isupper():
                return local + "@" + ".".join(labels[:i] + [tld])
    return e


def emails_in(text):
    """Clean email addresses in a line: the fetcher's <mailto> markers first (never glued), else regex + repair."""
    marked = [m.rstrip(".,;:") for m in ANGLE_EMAIL.findall(text or "")]
    if marked:
        return list(dict.fromkeys(marked))
    return list(dict.fromkeys(clean_email(m) for m in EMAIL_RE.findall(text or "")))


def first_email(text):
    found = emails_in(text)
    return found[0] if found else ""


def first_phone(text):
    for m in PHONE_RE.finditer(text or ""):
        cand = m.group(0).strip()
        if len(re.sub(r"\D", "", cand)) >= 8:
            return cand
    return ""
