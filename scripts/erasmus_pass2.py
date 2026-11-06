"""SECOND free pass for the Erasmus+ coordinators the first run (scripts/erasmus_local.py) left without an email or
phone. $0: no paid AI, no paid search. Four steps, each one resumable:

  1. registries  the education ministries' own open school directories (Italy: every state and private school's
                 official email + website; France: annuaire de l'education, email + phone + website; Catalonia:
                 directori de centres, email + phone + website). Matched by name AND town, kept only when exactly one
                 school (or one school group) fits.
  2. search      free DuckDuckGo search with the town ("<name>" <town>), one search at a time with a pause, so it
                 is not blocked (the first run searched 30 at a time with no town). A found site must still show the
                 organisation's name on its homepage (discover.check_site).
  3. sweep       for EVERY organisation with a known website (also the 8,266 already found, for phones): reads the
                 homepage and its contact / Impressum / about pages, decodes hidden emails ([at], Cloudflare), takes
                 the official email of the site's own domain and the phone from tel: links. Sites that show nothing
                 without a browser are opened once in headless Chromium (Playwright).
  4. people      the scraper's rules run again on sites newly found in 1-2, for a named contact.

    python scripts/erasmus_pass2.py                # all four steps
    python scripts/erasmus_pass2.py --step registries
    python scripts/erasmus_pass2.py --step search --limit 50

Results: erasmus_local/pass2/<PIC>.json; then erasmus_contacts_FINAL.xlsx is rebuilt (scripts/import_erasmus_ai.py).
"""
import argparse
import base64
import csv
import html as htmllib
import io
import json
import os
import re
import sys
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for k in ("GEMINI_PAID_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "BRAVE_API_KEY", "OPENAI_API_KEY"):
    os.environ.pop(k, None)
os.environ["PARAGON_DATA"] = str(ROOT / "erasmus_local")
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scraper2"))
sys.path.insert(0, str(ROOT / "scraper"))

LOCAL = ROOT / "erasmus_local"
FIRST = LOCAL / "results"
PASS2 = LOCAL / "pass2"
REG = LOCAL / "registries"
XLSX = ROOT / "erasmus_KA1_ongoing_projects.xlsx"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
      "Accept-Language": "en,de;q=0.8,fr;q=0.7,it;q=0.6,es;q=0.5"}
EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
FREEMAIL = ("gmail.", "hotmail.", "yahoo.", "outlook.", "live.", "libero.it", "gmx.", "web.de", "wp.pl", "o2.pl",
            "orange.fr", "wanadoo.fr", "free.fr", "abv.bg", "mail.ru", "seznam.cz", "t-online.de", "tiscali.it", "icloud.")
# public education mail domains a school's official address often lives on (not the website's own domain)
EDU_MAIL = ("istruzione.it", "pec.it", "ac-", "educa", "edu.gva.es", "edu.xunta", "juntadeandalucia.es", "educarex.es",
            "meb.k12.tr", "meb.gov.tr", "edu.", ".edu", "schule", "school", "skola", "skole", "koulu", "kommune", "kommun",
            "gov.", "cat", "ensino", "escola", "scoala", "szkola")
BAD_EMAIL = re.compile(r"\.(png|jpe?g|gif|svg|webp|css|js)$|example\.|sentry|wixpress|domain\.|yourdomain|@\d|"
                       r"noreply|no-reply|webmaster@|privacy@|gdpr@|dpo@|rgpd@|iod@|rodo@|abuse@|hostmaster@|postmaster@|helpdesk@|"
                       r"support@|pomoc@|hilfe@|datenschutz@|protecciondedatos@|privacidad@|bibliotec|library@|"
                       r"biblio@|jobs@|career|press@|presse@|stampa@|marketing@|shop@|sales@", re.I)
GOOD_LOCAL = re.compile(r"erasmus|international|internacional|internazional|mobility|mobilit|relint|ri@|"
                        r"info|office|secret|segreter|sekretar|kontakt|contact|admin|direc|dirig|post|mail|school|"
                        r"schule|escola|escuela|scuola|ecole|lycee|college|skola|skole|koulu|szkola|scoala|ce\.", re.I)
CONTACT_WORDS = re.compile(r"contact|kontakt|contatt|contacto|contacte|kapcsolat|kontakty|yhteys|impressum|imprint|"
                           r"mentions|legal|about|chi-siamo|quienes|qui-sommes|ueber|uber-uns|o-nas|despre|segreteria|"
                           r"secretar|erasmus|international|staff|team|personale|organigram", re.I)


def fold(s):
    s = unicodedata.normalize("NFKD", str(s or "").lower())
    return re.sub(r"[^a-z0-9 ]+", " ", "".join(c for c in s if not unicodedata.combining(c))).strip()


def get(url, timeout=25, limit=1_500_000):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read(limit)
        cs = r.headers.get_content_charset() or "utf-8"
        return r.geturl(), raw.decode(cs, "replace")


def host(u):
    h = urllib.parse.urlparse(u if "//" in u else "https://" + u).netloc.lower()
    return h[4:] if h.startswith("www.") else h


def base_domain(h):
    parts = h.split(".")
    if len(parts) >= 3 and parts[-2] in ("co", "com", "edu", "gov", "org", "ac", "gob", "k12", "net", "gv", "sch"):
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def save(pic, **kw):
    PASS2.mkdir(parents=True, exist_ok=True)
    f = PASS2 / f"{pic}.json"
    d = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    for k, v in kw.items():
        if v and not d.get(k):
            d[k] = v
    d.setdefault("steps", [])
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    tmp.replace(f)
    return d


def load2(pic):
    f = PASS2 / f"{pic}.json"
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {}


def mark(pic, step):
    d = load2(pic)
    d.setdefault("steps", [])
    if step not in d["steps"]:
        d["steps"].append(step)
    PASS2.mkdir(parents=True, exist_ok=True)
    (PASS2 / f"{pic}.json").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------- the organisations and what the first run found
def organisations():
    import openpyxl
    wb = openpyxl.load_workbook(XLSX, read_only=True)
    out = {}
    for r in wb["Coordinators"].iter_rows(min_row=2, values_only=True):
        name, pic, country, city, typ, site = r[:6]
        if name and pic and str(pic) not in out:
            out[str(pic)] = {"pic": str(pic), "name": str(name).strip(), "cc": str(country or ""), "city": str(city or ""),
                             "type": str(typ or ""), "sheet_site": str(site or "")}
    for pic, o in out.items():
        f = FIRST / f"{pic}.json"
        c = {}
        if f.exists():
            try:
                c = json.loads(f.read_text(encoding="utf-8")).get("contacts") or {}
            except Exception:
                pass
        p = c.get("contact") or {}
        ph = c.get("phone")
        o["site"] = c.get("official_website") or ""
        o["email"] = p.get("email") or c.get("generic_email") or ""
        o["phone"] = (ph.get("value") if isinstance(ph, dict) else ph) or p.get("phone") or ""
        o["named"] = bool(p.get("name"))
        d2 = load2(pic)
        o["site"] = o["site"] or d2.get("website", "")
        o["email"] = o["email"] or d2.get("general_email", "")
        o["phone"] = o["phone"] or d2.get("phone", "")
        o["steps"] = d2.get("steps", [])
    return out


# ---------------------------------------------------------------- step 1: ministry school directories
GENERIC = set("""istituto istruzione superiore statale comprensivo scuola liceo tecnico professionale scientifico classico
linguistico artistico paritario paritaria i c iis itis itc ipsia ipc iti ite itt isis ic is ss scuole sec secondaria
primaria primo secondo grado di del della dei degli delle e ed per lo la le il gli da in a al alla circolo didattico
direzione didattica convitto nazionale educandato cpia polo onnicomprensivo omnicomprensivo istituti
lycee lyc college ecole elementaire maternelle primaire publique prive privee general generale technologique
professionnel polyvalent agricole des du de la le les et d l en sur sous saint ste st lp lgt lpo ep ee lg
institut institucio escola escuela centre centro instituto educacion educacio secundaria publica public privat
ies ins cee ceip cfa cifp ipep zer eso fp batxillerat formacio professional collegi colegio""".split())


def toks(s):
    return [t for t in fold(s).split() if len(t) >= 3 and t not in GENERIC and not t.isdigit()]


def dl(url, path, timeout=300):
    if path.exists() and path.stat().st_size > 1000:
        return path
    print(f"   downloading {path.name} ...", flush=True)
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        path.write_bytes(r.read())
    return path


def registry_rows():
    """[(country, town, name, group_key, email, phone, website, source)] from the three open directories."""
    REG.mkdir(parents=True, exist_ok=True)
    rows = []
    base = "https://dati.istruzione.it/opendata/opendata/catalogo/elements1/"
    it_src = "https://dati.istruzione.it/opendata/ (Ministero dell'Istruzione, anagrafe scuole)"
    stat = None
    for f in ("SCUANAGRAFESTAT20252620250901.csv", "SCUANAGRAFESTAT20242520250831.csv"):
        try:
            stat = dl(base + f, REG / f)
            break
        except Exception:
            continue
    nd = lambda v: "" if not v or v.strip().lower() in ("non disponibile", "-") else v.strip()  # noqa: E731
    if stat:
        data = list(csv.DictReader(io.StringIO(stat.read_text(encoding="utf-8", errors="replace"))))
        head = {r["CODICESCUOLA"]: r for r in data}                    # the group's head school holds its email
        for r in data:
            h = head.get(r["CODICEISTITUTORIFERIMENTO"], r)
            mail = nd(h.get("INDIRIZZOEMAILSCUOLA")) or nd(r.get("INDIRIZZOEMAILSCUOLA"))
            web = nd(h.get("SITOWEBSCUOLA")) or nd(r.get("SITOWEBSCUOLA"))
            for nm in {r["DENOMINAZIONEISTITUTORIFERIMENTO"], r["DENOMINAZIONESCUOLA"]}:
                rows.append(("IT", r["DESCRIZIONECOMUNE"], nm, r["CODICEISTITUTORIFERIMENTO"], mail, "", web, it_src))
    try:
        par = dl(base + "SCUANAGRAFEPAR20242520250831.csv", REG / "SCUANAGRAFEPAR20242520250831.csv")
        for r in csv.DictReader(io.StringIO(par.read_text(encoding="utf-8", errors="replace"))):
            rows.append(("IT", r["DESCRIZIONECOMUNE"], r["DENOMINAZIONESCUOLA"], r["CODICESCUOLA"],
                         nd(r.get("INDIRIZZOEMAILSCUOLA")), "", nd(r.get("SITOWEBSCUOLA")), it_src))
    except Exception as e:
        print(f"   (Italian private schools not loaded: {e})", flush=True)
    try:
        fr = dl("https://data.education.gouv.fr/api/explore/v2.1/catalog/datasets/fr-en-annuaire-education/exports/csv"
                "?select=identifiant_de_l_etablissement,nom_etablissement,nom_commune,mail,telephone,web&delimiter=%3B",
                REG / "fr_annuaire.csv")
        for r in csv.DictReader(io.StringIO(fr.read_text(encoding="utf-8-sig", errors="replace")), delimiter=";"):
            rows.append(("FR", r["nom_commune"], r["nom_etablissement"], r["identifiant_de_l_etablissement"],
                         r.get("mail") or "", r.get("telephone") or "", r.get("web") or "",
                         "https://data.education.gouv.fr (annuaire de l'education)"))
    except Exception as e:
        print(f"   (French directory not loaded: {e})", flush=True)
    try:
        cat = REG / "cat_centres.json"
        dl("https://analisi.transparenciacatalunya.cat/resource/kvmv-ahh4.json?$limit=200000&$where=curs%3D%272025%2F2026%27",
           cat)
        for r in json.loads(cat.read_text(encoding="utf-8")):
            rows.append(("ES", r.get("nom_municipi", ""), r.get("denominaci_completa", ""), r.get("codi_centre", ""),
                         r.get("e_mail_centre", ""), r.get("tel_fon", ""), (r.get("url") or {}).get("url", "")
                         if isinstance(r.get("url"), dict) else r.get("url", ""),
                         "https://analisi.transparenciacatalunya.cat (directori de centres docents)"))
    except Exception as e:
        print(f"   (Catalan directory not loaded: {e})", flush=True)
    return rows


def town_ok(a, b):
    a, b = fold(a), fold(b)
    return bool(a and b) and (a == b or a.replace(" ", "") == b.replace(" ", "") or
                              (len(a) > 4 and len(b) > 4 and (a in b or b in a)))


def step_registries(orgs):
    rows = registry_rows()
    by_town = {}                                   # (country, town) -> [(row, its name words, town words)]
    for r in rows:
        by_town.setdefault((r[0], fold(r[1]).replace(" ", "")), []).append((r, toks(r[2]), toks(r[1])))
    towns = {}
    for cc, t in by_town:
        towns.setdefault(cc, []).append(t)
    print(f"   {len(rows)} directory entries loaded", flush=True)
    found = 0
    for o in orgs.values():
        if "registries" in o["steps"] or o["cc"] not in towns:
            continue
        want = toks(o["name"])
        city_t = set(toks(o["city"]))
        want_core = [t for t in want if t not in city_t] or want
        hits = []
        ct = fold(o["city"]).replace(" ", "")
        keys = [ct] if (o["cc"], ct) in by_town else [t for t in towns[o["cc"]] if len(ct) > 4 and len(t) > 4 and (ct in t or t in ct)]
        cands = [x for k in keys for x in by_town[(o["cc"], k)]]
        if want_core and ct:
            for r, rname, rtown in cands:
                have = set(rname + rtown)
                if all(t in have for t in want_core):
                    reg_core = [t for t in rname if t not in set(rtown)]
                    # and the directory's own distinctive words must be in our name (no "Fermi" for "Fermi-Galilei")
                    if all(t in set(want) for t in reg_core) or len(reg_core) <= len(want_core):
                        hits.append(r)
        groups = {r[3] for r in hits}
        if len(groups) == 1:
            r = hits[0]
            web = r[6].strip()
            if web and not web.startswith("http"):
                web = "https://" + web
            email = next((h[4] for h in hits if h[4]), "")
            phone = next((h[5] for h in hits if h[5]), "")
            save(o["pic"], general_email=email.lower(), phone=phone, website=web, registry_name=r[2],
                 registry_source=r[7], email_source=r[7] if email else "", phone_source=r[7] if phone else "")
            found += 1
        mark(o["pic"], "registries")
    print(f"   matched in a ministry directory: {found}", flush=True)


# ---------------------------------------------------------------- step 2: free search with the town
_slock = threading.Lock()
_state = {"next": 0.0, "pause_until": 0.0, "fails": 0, "flip": 0}
SKIP = ("facebook.", "instagram.", "linkedin.", "youtube.", "twitter.", "x.com", "tiktok.", "wikipedia.", "wikidata.",
        "europa.eu", "erasmus-plus", "google.", "bing.", "duckduckgo.", "tripadvisor", "yelp.", "mapcarta", "educaciontop",
        "infoempresa", "einforma", "paginasamarillas", "pagesjaunes", "dasoertliche", "gelbeseiten", "cylex", "kompass",
        "schulen.de", "scuolainchiaro", "comuni-italiani", "tuttitalia", "orientaeduc", "glassdoor", "indeed.", "uni-rank",
        "4icu.", "topuniversities", "studyportals", "mastersportal", "infobel", "firmy.cz", "panoramafirm", "zlatestranky")


def ddg(query):
    with _slock:
        wait = max(_state["next"], _state["pause_until"]) - time.time()
        if wait > 0:
            time.sleep(wait)
        _state["flip"] ^= 1
        url = ("https://lite.duckduckgo.com/lite/?q=" if _state["flip"] else "https://html.duckduckgo.com/html/?q=") + \
            urllib.parse.quote(query)
        try:
            _, body = get(url, timeout=25)
        except Exception as e:
            body = f"ERROR {e}"
        _state["next"] = time.time() + 8
        blocked = "anomaly" in body.lower() or body.startswith("ERROR") and ("403" in body or "429" in body or "202" in body)
        if blocked:
            _state["fails"] += 1
            pause = min(1800, 120 * _state["fails"])            # back off: 2, 4, 6 ... minutes, at most 30
            _state["pause_until"] = time.time() + pause
            print(f"   (DuckDuckGo asked to slow down: pausing {pause // 60} min)", flush=True)
            return None
        _state["fails"] = 0
    links = re.findall(r'href="([^"]+)"[^>]*class=.result-link', body) + \
        re.findall(r"class='result-link'[^>]*href=\"([^\"]+)\"", body) + re.findall(r'class="result__a"[^>]*href="([^"]+)"', body)
    out = []
    for l in links:
        m = re.search(r"uddg=([^&]+)", l)
        u = urllib.parse.unquote(m.group(1) if m else l)
        if u.startswith("//"):
            u = "https:" + u
        h = host(u)
        if h and not any(s in h for s in SKIP) and u not in out:
            out.append(u)
    return out


def step_search(orgs, limit=0, workers=6):
    import discover
    todo = [o for o in orgs.values() if not known_site(o) and "search" not in o["steps"]]
    if limit:
        todo = todo[:limit]
    print(f"   {len(todo)} organisations without a website to search (one search every 8 s; pauses by itself when DuckDuckGo asks)", flush=True)
    found = [0]

    def one(o):
        row = {"name": o["name"], "country": o["cc"], "phone": "", "email": ""}
        tried = set()
        for q in (f'"{o["name"]}" {o["city"]}', f'{o["name"]} {o["city"]} contact'):
            res = None
            while res is None:
                res = ddg(q)
            for u in res[:5]:
                root = f"{urllib.parse.urlparse(u).scheme}://{urllib.parse.urlparse(u).netloc}/"
                for cand in dict.fromkeys((root, u)):
                    if cand in tried:
                        continue
                    tried.add(cand)
                    h = host(cand)
                    if any(s in h for s in SKIP) or re.match(r"(mail|webmail|buzon|correo|intranet|moodle|campus|"
                                                             r"aula|login|sso|portal)\.", h):
                        continue
                    try:
                        ok, why, rec = discover.check_site(cand, row)
                    except Exception:
                        ok, rec = False, None
                    if ok:
                        # stricter than the first run: the address holds the name, or the homepage shows two of the
                        # name's own words (not the town's) and the town
                        core = [t for t in toks(o["name"]) if t not in set(toks(o["city"]))]
                        page = fold((rec or {}).get("text", ""))
                        in_dom = discover.name_in_domain(cand, o["name"])
                        words = sum(1 for t in core if f" {t} " in f" {page} ")
                        ok = in_dom or (words >= min(2, len(core)) and len(core) > 0 and
                                        (not o["city"] or fold(o["city"]).split()[0] in page))
                    if ok:
                        save(o["pic"], website=root, website_how=f"free search: {q} ({why})")
                        found[0] += 1
                        return
        return

    done = 0
    with ThreadPoolExecutor(workers) as pool:
        futs = {pool.submit(one, o): o for o in todo}
        for f in as_completed(futs):
            try:
                f.result()
            except Exception:
                pass
            mark(futs[f]["pic"], "search")
            done += 1
            if done % 100 == 0 or done == len(todo):
                print(f"   search {done}/{len(todo)}: websites found {found[0]}", flush=True)


# ---------------------------------------------------------------- step 3: contact-page sweep
def cf_decode(hexs):
    try:
        k = int(hexs[:2], 16)
        return "".join(chr(int(hexs[i:i + 2], 16) ^ k) for i in range(2, len(hexs), 2))
    except Exception:
        return ""


def page_emails_phones(html):
    h = htmllib.unescape(html)
    found = [cf_decode(x) for x in re.findall(r'data-cfemail="([0-9a-f]+)"', h)]
    found += [cf_decode(x) for x in re.findall(r"/cdn-cgi/l/email-protection#([0-9a-f]+)", h)]
    found += re.findall(r'mailto:([^"\'?>\s]+)', h)
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", h, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    deob = re.sub(r"\s*[\[\(\{]\s*(at|AT|@|arroba|chiocciola)\s*[\]\)\}]\s*", "@", text)
    deob = re.sub(r"\s*[\[\(\{]\s*(dot|DOT|punto|point|\.)\s*[\]\)\}]\s*", ".", deob)
    found += EMAIL.findall(deob)
    emails = []
    for e in found:
        e = urllib.parse.unquote(e).strip().strip(".,;:").lower()
        if EMAIL.fullmatch(e) and not BAD_EMAIL.search(e) and e not in emails:
            emails.append(e)
    phones = []
    for t in re.findall(r'href=["\']tel:([^"\']+)', h, re.I):
        t = re.sub(r"[^\d+]", "", urllib.parse.unquote(t))
        if 8 <= len(re.sub(r"\D", "", t)) <= 15 and t not in phones:
            phones.append(t)
    return emails, phones, text


def render(url):
    """Opens a page in headless Chromium once (sites that show nothing without a browser)."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return None
    try:
        with sync_playwright() as p:
            br = p.chromium.launch()
            pg = br.new_page()
            pg.goto(url, timeout=30000, wait_until="domcontentloaded")
            pg.wait_for_timeout(2500)
            out = pg.content()
            br.close()
            return out
    except Exception:
        return None


def pick_email(emails, site_host):
    own = base_domain(site_host)
    emails = sorted(emails, key=lambda e: (not re.search(r"erasmus|internation|mobilit", e.split("@")[0], re.I),
                                           not GOOD_LOCAL.search(e.split("@")[0])))
    for e in emails:                                  # the site's own domain first
        if e.split("@")[1].endswith(own):
            return e
    for e in emails:                                  # then a public education mail domain
        d = e.split("@")[1]
        if any(x in d for x in EDU_MAIL) and not any(f in d for f in FREEMAIL):
            return e
    return ""


def known_site(o):
    """The site found by the first run or by this pass, else the one the organisation itself registered on the
    Erasmus+ platform (the first run refused some of these only because the homepage words the name differently)."""
    return o["site"] or load2(o["pic"]).get("website", "") or o.get("sheet_site", "")


def sweep_one(o):
    site = known_site(o)
    if not site:
        return
    site = site if site.startswith("http") else "https://" + site
    got = sweep_site(site)
    if not got:
        return
    email, src, phone, psrc = got
    save(o["pic"], general_email=email if not o["email"] else "", email_source=src if email and not o["email"] else "",
         phone=phone if not o["phone"] else "", phone_source=psrc if phone and not o["phone"] else "", website=site)


def sweep_site(site):
    """Reads a site's homepage and up to 5 contact / Impressum / about pages. Returns (official email, its page,
    phone from a tel: link, its page), or None when the site cannot be read."""
    sh = host(site)
    emails, phones, used, pages = [], [], "", []
    try:
        final, home = get(site)
    except Exception:
        home, final = render(site) or "", site
    if len(re.sub(r"<[^>]+>", "", home or "")) < 400:
        home = render(final) or home
    if not home:
        return None
    pages.append((final, home))
    links = []
    for href, label in re.findall(r'<a[^>]+href=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>', home, re.S | re.I):
        u = urllib.parse.urljoin(final, htmllib.unescape(href))
        if host(u) == host(final) and CONTACT_WORDS.search(href + " " + re.sub(r"<[^>]+>", "", label)) and u not in links:
            links.append(u)
    for u in links[:5]:
        try:
            pages.append(get(u))
        except Exception:
            continue
    free_on_contact = ""
    for u, h in pages:
        es, ps, _ = page_emails_phones(h)
        for e in es:
            if e not in emails:
                emails.append(e)
                if not used:
                    used = u
        phones += [p for p in ps if p not in phones]
        if not free_on_contact and u != final:
            free_on_contact = next((e for e in es if any(f in e.split("@")[1] for f in FREEMAIL)), "")
    email = pick_email(emails, sh) or free_on_contact     # a small school's gmail, only when its contact page shows it
    src = next((u for u, h in pages if email and email in page_emails_phones(h)[0]), used)
    psrc = next((u for u, h in pages if phones and phones[0] in page_emails_phones(h)[1]), "")
    return email, src if email else "", phones[0] if phones else "", psrc


def step_sweep(orgs, limit=0, workers=40):
    todo = [o for o in orgs.values() if known_site(o) and "sweep" not in o["steps"]
            and not (o["email"] and o["phone"])]
    todo.sort(key=lambda o: bool(o["email"]))      # organisations with no email at all first, then phones for the rest
    if limit:
        todo = todo[:limit]
    print(f"   {len(todo)} websites to read for email / phone", flush=True)
    done = 0
    with ThreadPoolExecutor(workers) as pool:
        futs = {pool.submit(sweep_one, o): o for o in todo}
        for f in as_completed(futs):
            try:
                f.result()
            except Exception:
                pass
            mark(futs[f]["pic"], "sweep")
            done += 1
            if done % 250 == 0 or done == len(todo):
                print(f"   sweep {done}/{len(todo)}", flush=True)


# ---------------------------------------------------------------- step 4: named contact on newly found sites
def step_people(orgs, limit=0, workers=20):
    import erasmus_local as el
    import pipeline
    cfg = el.free_cfg()
    pipeline.LLM_SLOTS = threading.Semaphore(1)
    todo = [o for o in orgs.values() if not o["named"] and not o["site"] and known_site(o) and "people" not in o["steps"]]
    if limit:
        todo = todo[:limit]
    print(f"   {len(todo)} newly found websites to read for a named contact", flush=True)

    def one(o):
        site = known_site(o)
        row = {"row_id": o["pic"], "institution_name": o["name"], "country_sheet": el.COUNTRY.get(o["cc"], o["cc"]),
               "phone_1": "", "contact_name": "", "email": "", "seed_urls": site, "website_override": site}
        try:
            res = pipeline.scrape(row, dict(cfg))
        except Exception:
            return
        c = res.get("contacts") or {}
        p = c.get("contact") or {}
        if p.get("name") or p.get("email") or c.get("generic_email"):
            save(o["pic"], person=p, generic_from_scraper=c.get("generic_email") or "")

    done = 0
    with ThreadPoolExecutor(workers) as pool:
        futs = {pool.submit(one, o): o for o in todo}
        for f in as_completed(futs):
            mark(futs[f]["pic"], "people")
            done += 1
            if done % 250 == 0 or done == len(todo):
                print(f"   people {done}/{len(todo)}", flush=True)


def summary(orgs):
    em = sum(1 for o in orgs.values() if o["email"])
    ph = sum(1 for o in orgs.values() if o["phone"])
    st = sum(1 for o in orgs.values() if o["site"])
    print(f"   now: website {st}, email {em}, phone {ph} of {len(orgs)} organisations", flush=True)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--step", default="all", choices=["all", "registries", "search", "sweep", "people", "excel"])
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    t0 = time.time()
    steps = ["registries", "sweep", "people", "search", "sweep", "people"] if a.step == "all" else ([] if a.step == "excel" else [a.step])
    for s in steps:
        orgs = organisations()
        print(f"\n== step {s} ({time.strftime('%H:%M')})", flush=True)
        summary(orgs)
        {"registries": step_registries, "search": step_search, "sweep": step_sweep, "people": step_people}[s](
            orgs, **({} if s == "registries" else {"limit": a.limit}))
    summary(organisations())
    import import_erasmus_ai as imp
    imp.OUT = ROOT / "erasmus_contacts_FINAL.xlsx"
    try:
        imp.main(quiet=True)
    except PermissionError:
        imp.OUT = ROOT / f"erasmus_contacts_FINAL_{time.strftime('%Y%m%d_%H%M')}.xlsx"
        imp.main(quiet=True)
    print(f"done in {(time.time() - t0) / 60:.0f} min. $0 spent.", flush=True)


if __name__ == "__main__":
    main()
