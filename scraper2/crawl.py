"""Deterministic, parallel crawl of one institution's website. No AI involved.

Finds the pages that matter for Erasmus+ (international office, mobility, partners, contact,
campuses) by scoring every link with a multilingual keyword table, then fetches the best ones
through scraper/fetch_pages.py (cache, robots.txt, PDF text, per-host politeness).

Two rounds: homepage links first, then links found on the best of those pages (the
international office page usually links to the partner list and the team page).
"""
import re
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scraper"))
import fetch_pages  # noqa: E402

# (weight, terms) - matched against the link label and the URL path, accent/case-insensitive.
KEYWORDS = [
    (6, ["erasmus", "auslandsamt", "international office", "international relations", "relations internationales",
         "relaciones internacionales", "relazioni internazionali", "internationales buro", "mobility",
         "mobilite", "movilidad", "mobilitat", "mobilitaet", "mobilita", "mobilnosc", "mobilnost",
         "tarptautin", "starptautisk", "rahvusvahel", "kansainvalis", "internacional", "nemzetkozi",
         "inter-relations", "interrelations", "international-affairs", "external-relations", "internationalisation",
         "internationalization", "internacionalizacion", "data sheet", "datasheet", "erasmus data",
         "διεθν", "ερασμ", "κινητικοτ",     # Greek: international, erasmus, mobility
         "международ", "еразм", "ерасм",   # Cyrillic
         "мобилност"]),
    (4, ["partner", "partenaire", "socios", "cooperation", "cooperacion", "cooperation", "kooperation",
         "wspolprac", "spolupr", "bendradarb", "egyuttmukod", "agreements", "convenios", "accords",
         "alliance", "network", "reseau", "netzwerk", "erasmus+", "strategic", "bilateral",
         "συνεργασ", "εταιρ", "δίκτυ",                # Greek: cooperation, partner, network
         "сътруднич", "сотруднич", "партньор", "партнер",
         "співпрац", "suradnj", "sodelovanj"]),
    (4, ["munkatars", "kollega", "mitarbeiter", "kollegium", "zespol", "pracownicy", "kadra", "zamestnanci", "kolektiv",
         "darbuotoj", "personalul", "our staff", "our team", "staff", "equipo", "plantilla", "professeurs", "l'equipe",
         "equipe", "team", "personal", "direction", "vedenie", "vezetoseg", "leadership", "management", "secretariat",
         "titkarsag", "ugyintez", "administratie", "faculty and staff", "who we are", "people",
         "προσωπικ", "επικοινων",                        # Greek: staff, contact
         "персонал", "контакт", "zaposleni", "osoblje"]),
    (3, ["contact", "kontakt", "contacto", "contatti", "team", "staff", "personal", "equipo", "equipe",
         "campus", "locations", "our sites", "centres", "sedes", "standorte", "about us",
         "a propos", "quienes somos", "ueber uns", "o nas", "o nama"]),
]
NEGATIVE = ["login", "cookie", "privacy", "datenschutz", "impressum", "facebook", "twitter", "instagram",
            "linkedin", "youtube", "tiktok", "whatsapp", "javascript:", "mailto:", "tel:", ".jpg", ".png",
            ".gif", ".svg", ".zip", ".mp4", "shop", "cart", "calendar", "feed", "wp-json", "sitemap.xml"]
STRONG_MIN = 4          # a link must score at least this to be followed
NEWSY = re.compile(r"announc|/news|news/|blog|press|/event|noticias|actualit|aktualit|novosti|anakoin|/nea/|hirek|neuigkeit|aktuell|"
                   r"nyheter|nieuws|wiadomosci|aktuality|ilmoitus|paziņojum|pranesim|kutsu", re.I)
NEWS_URL = re.compile(r"/(19|20)\d\d/\d{1,2}/")          # dated blog/news post: rarely the page we want
# what a page is likely to give us; slots are shared across types so one topic (say, 40 Erasmus blog posts) cannot
# use the whole page budget and crowd out the staff or partner page
TYPE_TERMS = {
    "staff": KEYWORDS[2][1] + ["contact", "kontakt", "contacto", "contatti"],
    "partners": KEYWORDS[1][1],
    "erasmus": KEYWORDS[0][1],
    "campus": ["campus", "locations", "our sites", "centres", "sedes", "standorte"],
}


def _norm(s):
    s = unicodedata.normalize("NFKD", (s or "").lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def score_link(label, url):
    """How likely a link leads to Erasmus/international/partner/contact/campus content."""
    hay_label, hay_url = _norm(label), _norm(urlparse(url).path + " " + urlparse(url).query)
    if any(n in _norm(url) for n in NEGATIVE):
        return 0
    score = 0
    for weight, terms in KEYWORDS:
        for t in terms:
            if t in hay_label:
                score += weight * 2
            if t in hay_url:
                score += weight
    if url.lower().split("?")[0].endswith(".pdf") and score:
        score += 2      # partner/agreement lists are very often PDFs
    if NEWS_URL.search(url):
        score -= 8      # dated news/blog post
    if NEWSY.search(urlparse(url).path):
        score -= 25     # announcements / news / blog section: they come in dozens and outscored the real pages
    return score


def link_types(label, url):
    hay = _norm(label) + " " + _norm(urlparse(url).path + " " + urlparse(url).query)
    return [k for k, terms in TYPE_TERMS.items() if any(t in hay for t in terms)]


def pick(cands, k):
    """cands: [(url, score, [types])] best first. Round-robin over types so each kind of page gets slots."""
    by_type = {t: [c for c in cands if t in c[2]] for t in ("staff", "partners", "erasmus", "campus")}
    chosen, seen = [], set()
    while len(chosen) < k and any(by_type.values()):
        for t in ("staff", "partners", "erasmus", "campus"):
            while by_type[t] and by_type[t][0][0] in seen:
                by_type[t].pop(0)
            if by_type[t] and len(chosen) < k:
                c = by_type[t].pop(0)
                chosen.append(c)
                seen.add(c[0])
    for c in cands:                         # anything left with no clear type, by score
        if len(chosen) >= k:
            break
        if c[0] not in seen:
            chosen.append(c)
            seen.add(c[0])
    return [(u, sc) for u, sc, _ in chosen]


def _host(u):
    h = urlparse(u).netloc.lower()
    return h[4:] if h.startswith("www.") else h


def same_site(a, b):
    ha, hb = _host(a), _host(b)
    return ha == hb or ha.endswith("." + hb) or hb.endswith("." + ha)


def _fetch_many(urls, workers):
    """Fetch pages in parallel. Returns ([records], [(url, reason)])."""
    pages, errors = [], []

    def one(u):
        try:
            return fetch_pages.fetch(u), None
        except Exception as e:          # FetchError or anything unexpected: report, never crash the run
            return None, str(e)
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for u, (rec, err) in zip(urls, pool.map(one, urls)):
            (pages.append(rec) if rec else errors.append((u, err)))
    return pages, errors


def crawl(site, max_pages=10, workers=5, seeds=()):
    """Returns {"pages": [{url, final_url, text, score, cert_unverified}], "errors": [...], "site": final_site}."""
    seen, pages, errors = set(), [], []
    home_recs, errs = _fetch_many([site], 1)
    errors += errs
    if not home_recs:
        return {"pages": [], "errors": errors, "site": site}
    home = home_recs[0]
    site_final = home.get("final_url") or site
    seen.add(fetch_pages._url_key(site)); seen.add(fetch_pages._url_key(site_final))
    pages.append({**_slim(home), "score": 100})

    seed_urls = [u for u in dict.fromkeys(seeds) if u.startswith("http") and same_site(u, site_final)
                 and fetch_pages._url_key(u) not in seen][:4]
    if seed_urls:                      # pages the client's sheet already points at (contacts / partners / campuses)
        seed_recs, seed_errs = _fetch_many(seed_urls, workers)
        errors += seed_errs
        for rec in seed_recs:
            seen.add(fetch_pages._url_key(rec["url"]))
            pages.append({**_slim(rec), "score": 90})

    extra = []
    try:
        extra = [("", u) for u in sitemap_urls(site_final) if same_site(u, site_final)]
    except Exception:
        extra = []

    def candidates(rec_list, exclude, use_extra=False):
        best = {}
        pool = [(l, u) for rec in rec_list for l, u in rec.get("links", [])] + (extra if use_extra else [])
        for label, url in pool:
            if True:
                if not url.startswith("http") or not same_site(url, site_final):
                    continue
                k = fetch_pages._url_key(url)
                if k in seen or k in exclude:
                    continue
                sc = score_link(label, url)
                if sc >= STRONG_MIN and sc > best.get(url, (0,))[0]:
                    best[url] = (sc, link_types(label, url))
        return sorted(((u, v[0], v[1]) for u, v in best.items()), key=lambda x: -x[1])

    round1 = pick(candidates([home], set(), use_extra=True), max(1, max_pages * 6 // 10))
    got1, errs1 = _fetch_many([u for u, _ in round1], workers)
    errors += errs1
    scores = dict(round1)
    for rec in got1:
        seen.add(fetch_pages._url_key(rec["url"]))
        pages.append({**_slim(rec), "score": scores.get(rec["url"], 4)})

    got2 = []
    room = max_pages - len(pages)
    if room > 0:
        exclude = {fetch_pages._url_key(p["url"]) for p in pages}
        round2 = pick(candidates(got1, exclude), room)
        got2, errs2 = _fetch_many([u for u, _ in round2], workers)
        errors += errs2
        scores2 = dict(round2)
        for rec in got2:
            pages.append({**_slim(rec), "score": scores2.get(rec["url"], 4)})
    room = max_pages - len(pages)
    if room > 0 and pages:                                   # third round: links found on the newest pages
        exclude = {fetch_pages._url_key(p["url"]) for p in pages}
        round3 = pick(candidates(got2 or got1, exclude), room)
        got3, errs3 = _fetch_many([u for u, _ in round3], workers)
        errors += errs3
        scores3 = dict(round3)
        for rec in got3:
            pages.append({**_slim(rec), "score": scores3.get(rec["url"], 4)})
    return {"pages": pages, "errors": errors, "site": site_final}


LOC_RE = re.compile(r"<loc>\s*(?:<!\[CDATA\[)?\s*([^<\s]+?)\s*(?:\]\]>)?\s*</loc>", re.I)


def sitemap_urls(site, max_urls=4000):
    """URLs listed in the site's sitemap(s). Finds pages that no menu links to (e.g. a staff page).
    Looks at robots.txt's Sitemap lines and the usual file names, both at the domain root and under the
    site's own base path (a site can live in /wp/), page sitemaps before post/category ones."""
    root = f"{urlparse(site).scheme}://{urlparse(site).netloc}"
    base = site.rstrip("/")
    todo = re.findall(r"(?im)^\s*sitemap:\s*(\S+)", fetch_pages.fetch_raw(root + "/robots.txt"))
    for b in dict.fromkeys([base, root]):
        todo += [b + "/wp-sitemap.xml", b + "/sitemap.xml", b + "/sitemap_index.xml"]
    urls, seen_sm, fetched = [], set(), 0
    while todo and fetched < 4 and len(urls) < max_urls:
        sm = todo.pop(0)
        if sm in seen_sm:
            continue
        seen_sm.add(sm)
        body = fetch_pages.fetch_raw(sm)
        if not body or "<loc" not in body.lower():
            continue
        fetched += 1
        locs = LOC_RE.findall(body)
        child = [u for u in locs if u.lower().split("?")[0].endswith(".xml")]
        if child:
            child.sort(key=lambda u: 0 if "page" in u.lower() else 1)
            todo = child[:3] + todo
        urls += [u for u in locs if u not in child]
    return urls[:max_urls]


def _slim(rec):
    return {"url": rec.get("final_url") or rec["url"], "text": rec.get("text", ""),
            "cert_unverified": bool(rec.get("certificate_unverified")), "links": rec.get("links", [])}
