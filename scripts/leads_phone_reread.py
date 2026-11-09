"""Phones from the institute's own website, read better than the first sweep did. No search engine: runs in parallel.

Why (measured on 40 "already read" sites without a phone): the first sweep (erasmus_pass2.sweep_site) only took a
tel: link or a number right after a "Tel/Phone" label, stripped <script> before looking (so the business data
"telephone": "+356..." was lost), and tried the contact / legal-notice paths only when it had found nothing at all.
12 of the 40 had a phone on the site.

Read here: the homepage, up to 8 same-site contact / legal / privacy / about pages, and the country's usual
contact / legal-notice paths while there is still no phone. Taken, most trusted first:
  1. tel: links            2. structured data ("telephone" in JSON-LD, itemprop="telephone")
  3. a number next to a Tel / Phone / Telefono label (not fax)
  4. a number with no label, only when it is unmistakably a phone of the institute's country: +<its dialling code>,
     or that country's own national format; never a date, a VAT / tax / bank number or a 01 02 03 04 05 sequence.
The number seen most often in the best class wins. Sites that refuse us (403) are respected.

  python scripts/leads_phone_reread.py todo_phone.csv phone_reread.jsonl --split 0/3 [--workers 16] [--limit 40]
Output lines: {"key", "phone", "phone_src", "phone_quote", "how"} or {"key", "why"}; written in by
  python scripts/leads_person_search.py apply phone_reread*.jsonl --todo leads_local/gap/todo_phone.csv --mark phonereread
"""
import csv
import html as htmllib
import json
import re
import sys
import threading
import time
import urllib.parse
import zlib
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scraper2"))
import erasmus_pass2 as p2  # noqa: E402

DIAL = {"IT": "39", "FR": "33", "ES": "34", "PT": "351", "DE": "49", "AT": "43", "PL": "48", "NL": "31", "BE": "32",
        "RO": "40", "EL": "30", "GR": "30", "BG": "359", "MT": "356", "UK": "44", "GB": "44", "IE": "353", "TR": "90",
        "CZ": "420", "HR": "385", "SI": "386", "LT": "370", "LV": "371", "EE": "372", "FI": "358", "SE": "46",
        "DK": "45", "HU": "36", "SK": "421", "CY": "357", "LU": "352", "NO": "47", "IS": "354", "CH": "41",
        "RS": "381", "MK": "389", "AL": "355", "BA": "387", "ME": "382", "US": "1", "CA": "1", "UA": "380"}
TLD_CC = {"uk": "UK", "gr": "EL", "com": "", "org": "", "net": "", "eu": "", "edu": "US", "info": "", "io": ""}
# national formats (digits only, no leading +): what a phone of that country looks like written the local way
NATIONAL = {"FR": r"0[1-9]\d{8}", "BE": r"0\d{8,9}", "IT": r"0\d{5,10}|3\d{9}", "ES": r"[6789]\d{8}",
            "PT": r"[29]\d{8}", "MT": r"[279]\d{7}", "DE": r"0[1-9]\d{5,11}", "AT": r"0[1-9]\d{4,11}",
            "PL": r"[1-9]\d{8}", "UK": r"0[1-9]\d{8,9}", "EL": r"(2\d|69)\d{8}", "RO": r"0[2-7]\d{8}",
            "NL": r"0[1-9]\d{8}", "IE": r"0[1-9]\d{6,9}", "CY": r"[29]\d{7}", "HR": r"0[1-9]\d{6,8}",
            "SI": r"0[1-9]\d{7}", "BG": r"0[2-9]\d{6,8}", "HU": r"06\d{8,9}", "CZ": r"[2-9]\d{8}",
            "SK": r"0[2-9]\d{7,8}", "LT": r"8\d{8}", "TR": r"0[2-5]\d{9}", "US": r"[2-9]\d{9}"}
NUM = re.compile(r"(?<![\w/.=#@-])((?:\+\(?|00)\d{1,3}\)?[\s.\-]?\(?\d{1,4}\)?(?:[\s.\-/]?\d{2,4}){1,4}|"
                 r"\(?0?\d{1,4}\)?(?:[\s.\-/]\d{2,4}){1,4}|\d{8,10})(?![\w/])")
NOT_PHONE = re.compile(r"(fax|telefax|p\.?\s?iva|vat|iva|cif|nif|siret|siren|c\.?\s?f\.|codice fiscale|iban|rea|"
                       r"n[°º]|nr\.?|ust|steuer|regon|nip|krs|uid|cod\.|cap|zip|isbn|issn)\W{0,4}$", re.I)
LEGAL_WORDS = re.compile(r"privacy|privacidad|datenschutz|aviso|legal|impressum|imprint|mentions|note-legali|"
                         r"rodo|confidential|footer|location|find-us|how-to-reach|dove-siamo|donde-estamos|acces",
                         re.I)


def country_of(row, site):
    cc = (row.get("cc") or "").upper()
    if cc:
        return {"GB": "UK", "GR": "EL"}.get(cc, cc)
    tld = (p2.host(site) or "").rsplit(".", 1)[-1].lower()
    if tld in TLD_CC:
        return TLD_CC[tld]
    return {"gr": "EL"}.get(tld, tld.upper() if len(tld) == 2 else "")


def digits(s):
    return re.sub(r"\D", "", s)


def norm(raw, cc):
    """'+39 06 123 4567' -> '+39061234567'; a national number gets its country's code; '' when it is no phone."""
    raw = raw.replace("(0)", " ")
    d = digits(raw)
    if raw.strip().startswith("+") or (raw.strip().startswith("00") and len(d) > 10):
        d = d[2:] if raw.strip().startswith("00") else d
        code = next((c for c in sorted(set(DIAL.values()), key=len, reverse=True) if d.startswith(c)), "")
        if code and code != "39" and d[len(code):].startswith("0"):      # +33 0450... written with the trunk 0
            d = code + d[len(code) + 1:]
        return "+" + d if 8 <= len(d) <= 15 else ""
    if not 6 <= len(d) <= 12:
        return ""
    if cc in DIAL and re.fullmatch(NATIONAL.get(cc, r"\d{8,12}"), d):
        if cc in ("FR", "BE", "DE", "AT", "UK", "NL", "IE", "HR", "SI", "BG", "RO", "SK", "TR") and d.startswith("0"):
            d = d[1:]
        elif cc == "HU" and d.startswith("06"):
            d = d[2:]
        elif cc == "LT" and d.startswith("8"):
            d = d[1:]
        return "+" + DIAL[cc] + d
    return ""


def plausible(raw, cc):
    """A number with no label: kept only when it can only be a phone of this country."""
    s = raw.strip()
    d = digits(s)
    if re.fullmatch(r"\d{1,2}[./-]\d{1,2}[./-](19|20)\d{2}", s) or re.fullmatch(r"(19|20)\d{2}[./-]\d{1,2}[./-]\d{1,2}", s):
        return ""                                                     # a date
    if re.search(r"(19|20)\d{2}\s*[-–/]\s*(19|20)\d{2}", s) or re.fullmatch(r"(19|20)\d{2}(19|20)\d{2}", d):
        return ""                                                     # a year range (© 2016-2022)
    if len(set(d)) <= 2 or d in "01234567890123456789" or re.fullmatch(r"0?1\s?0?2\s?0?3.*", s):
        return ""                                                     # 0000 0000 / 01 02 03 04 05
    if s.startswith(("+", "00")):
        code = DIAL.get(cc)
        dd = d[2:] if s.startswith("00") else d
        return norm(s, cc) if code and dd.startswith(code) else ""
    if len(d) < 8 or (" " not in s and "." not in s and "-" not in s and "/" not in s and len(d) > 9):
        return ""                                                     # one long run of digits: an id, not a phone
    return norm(s, cc)


def page_phones(url, page, cc):
    """[(trust, phone, quote)] of one page (trust 1 = best)."""
    h = htmllib.unescape(page or "")
    out = []
    for t in re.findall(r'href=["\']tel:([^"\']+)', h, re.I):
        n = norm(urllib.parse.unquote(t), cc) or ("+" + digits(t) if t.strip().startswith("+") else "")
        if n and 8 <= len(digits(n)) <= 15:
            out.append((1, n, f"tel: link {urllib.parse.unquote(t)}"))
    for t in re.findall(r'"telephone"\s*:\s*"([^"]{6,30})"', h) + \
            re.findall(r'itemprop=["\']telephone["\'][^>]{0,200}>\s*([^<]{6,30})<', h, re.I) + \
            re.findall(r'itemprop=["\']telephone["\'][^>]{0,200}content=["\']([^"\']{6,30})', h, re.I):
        n = norm(t, cc) or ("+" + digits(t) if t.strip().startswith("+") and 8 <= len(digits(t)) <= 15 else "")
        if n:
            out.append((2, n, f'business data "telephone": {t.strip()}'))
    text = re.sub(r"<script.*?</script>|<style.*?</style>|<svg.*?</svg>|<!--.*?-->", " ", h, flags=re.S | re.I)
    text = re.sub(r"<[^>]*>", " ", text)
    text = re.sub(r"\s{2,}", " ", text)[:400_000]
    for m in p2.PHONE_TEXT.finditer(text):
        before = text[max(0, m.start() - 12):m.start()]
        if re.search(r"fax\W{0,4}$", before, re.I) or re.match(r"\s*fax", text[m.start():m.start() + 6], re.I):
            continue
        raw = m.group(1)
        n = norm(raw, cc) or ("+" + digits(raw) if raw.strip().startswith("+") and 8 <= len(digits(raw)) <= 15 else "")
        if n:
            out.append((3, n, text[max(0, m.start() - 60): m.end() + 30].strip()))
    if not out and cc in DIAL:
        for m in NUM.finditer(text):
            if NOT_PHONE.search(text[max(0, m.start() - 22):m.start()]):
                continue
            n = plausible(m.group(1), cc)
            if n:
                out.append((4, n, text[max(0, m.start() - 60): m.end() + 30].strip()))
    return out


def read_site(row):
    site = row["site"] if row["site"].startswith("http") else "https://" + row["site"]
    out = {"key": row["key"]}
    cc = country_of(row, site)
    try:
        final, home = p2.get(site)
    except Exception as e:
        out["why"] = "unreadable: " + str(e)[:60]
        return out
    if not home:
        out["why"] = "empty page"
        return out
    pages = [(final, home)]
    links = []
    for href, label in re.findall(r'<a\s[^>]{0,800}?href=["\']([^"\'#]{1,500})["\'][^>]{0,800}>(.{0,300}?)</a>',
                                  home, re.S | re.I):
        u = urllib.parse.urljoin(final, htmllib.unescape(href))
        lab = href + " " + re.sub(r"<[^>]+>", "", label)
        if p2.host(u) == p2.host(final) and u not in links and u.rstrip("/") != final.rstrip("/") and \
                (p2.CONTACT_WORDS.search(lab) or LEGAL_WORDS.search(lab)) and \
                not re.search(r"\.(pdf|jpe?g|png|docx?|xlsx?|zip)$", u, re.I):
            links.append(u)
    links.sort(key=lambda u: 0 if re.search(r"contact|kontakt|contatt|contacto|impressum|mentions|legal", u, re.I) else 1)
    found = page_phones(final, home, cc)
    for u in links[:8]:
        if any(t <= 2 for t, _, _ in found) and len(found) >= 2:
            break
        try:
            fu, h = p2.get(u)
        except Exception:
            continue
        pages.append((fu, h))
        found += [(t, n, q) for t, n, q in page_phones(fu, h, cc)]
    if not found:
        base = f"{urllib.parse.urlparse(final).scheme}://{urllib.parse.urlparse(final).netloc}/"
        for path in p2.PROBE.get(cc, []) + ["contact", "contacts", "kontakt", "contatti", "contacto", "impressum"]:
            u = base + path
            if u in links:
                continue
            try:
                fu, h = p2.get(u)
            except Exception:
                continue
            got = page_phones(fu, h, cc)
            if got:
                pages.append((fu, h))
                found += got
                break
    if not found:
        out["why"] = "no phone on the site"
        return out
    # a landline before a mobile when both are equally trusted (the switchboard is what callers need)
    mob = lambda n: bool(re.match(r"\+(39 ?3|34 ?[67]|33 ?[67]|49 ?1[5-7]|48 ?[5-8]|351 ?9|356 ?[79]|44 ?7|30 ?69)", n))  # noqa: E731
    found.sort(key=lambda x: (x[0], mob(x[1])))
    best = min(t for t, _, _ in found)
    cnt = Counter(n for t, n, _ in found if t == best)
    top = max(cnt, key=lambda n: (cnt[n] - 0.5 * mob(n), -[x[1] for x in found].index(n)))
    quote = next(q for t, n, q in found if t == best and n == top)
    src = next((u for u, h in pages if top in [n for _, n, _ in page_phones(u, h, cc)]), final)
    out.update(phone=top, phone_src=src, phone_quote=quote[:200], how={1: "tel link", 2: "business data",
                                                                         3: "labelled", 4: "unlabelled"}[best])
    return out


def main():
    a = sys.argv[1:]
    opt = lambda f, d: a[a.index(f) + 1] if f in a else d  # noqa: E731
    todo, outp = a[0], Path(a[1])
    k, n = (int(x) for x in opt("--split", "0/1").split("/"))
    workers, limit = int(opt("--workers", "16")), int(opt("--limit", "0"))
    p2.TIMEOUT = int(opt("--timeout", "20"))
    done = set()
    if outp.exists():
        for line in outp.read_text(encoding="utf-8").splitlines():
            try:
                done.add(json.loads(line)["key"])
            except Exception:
                pass
    rows = [r for r in csv.DictReader(open(todo, encoding="utf-8-sig"))
            if r.get("site") and zlib.crc32(r["key"].encode()) % n == k and r["key"] not in done]
    if limit:
        rows = rows[:limit]
    print(f"{time.strftime('%H:%M')} {len(rows)} sites to read (share {k}/{n}, {len(done)} already done)", flush=True)
    lock, stats = threading.Lock(), Counter()

    def run(r):
        try:
            res = read_site(r)
        except Exception as e:
            res = {"key": r["key"], "why": "error: " + str(e)[:80]}
        with lock:
            with open(outp, "a", encoding="utf-8") as f:
                f.write(json.dumps(res, ensure_ascii=False) + "\n")
            stats["n"] += 1
            stats[res.get("how") or "none"] += 1
            if stats["n"] % 100 == 0:
                print(f"{time.strftime('%H:%M')} {stats['n']}/{len(rows)}: phones "
                      f"{stats['n'] - stats['none']} {dict(stats)}", flush=True)

    with ThreadPoolExecutor(workers) as pool:
        list(pool.map(run, rows))
    print(f"{time.strftime('%H:%M')} done {stats['n']}: phones {stats['n'] - stats['none']} {dict(stats)}", flush=True)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
