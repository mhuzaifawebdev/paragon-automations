"""One institution, end to end: find site -> crawl -> evidence pack -> ONE model call -> resolve line
numbers into verbatim quotes -> v1-shaped result (so scraper/run_batch.merge() and
scraper/build_sheet.py work unchanged; verification happens there, in scraper/verify.py).

Nothing in a result can be invented by the model: quotes are copied by code from the numbered
lines it pointed at, emails/phones are regex-read from those lines, and a contact is dropped
if their name is not on the line the model cited.
"""
import re
import sys
import threading
import time
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "scraper"))
import crawl  # noqa: E402
import discover  # noqa: E402
import evidence  # noqa: E402
import fetch_pages  # noqa: E402
import llm  # noqa: E402
import rank  # noqa: E402

# A person is only accepted if the cited line(s) actually STATE a role. Without this, "verified on the page"
# is not enough: a news byline ("Rob Dawson - 9 September") would pass as a "mobility coordinator".
ROLE_STEMS = re.compile(
    r"coordin|koordin|referent|responsab|officer|manager|head|chief|director|direktor|dyrektor|directeur|directrice|"
    r"riaditel|igazgat|reditel|principal|rector|rektor|dekan|dean|leiter|leitung|secretar|sekretar|titkar|ugyintez|"
    r"munkatars|administrat|asistent|assistant|jefe|jefa|encargad|delegad|conseill|charge de|mitarbeit|beauftragt|"
    r"proviseur|vedouc|veduc|vezeto|zastup|zastep|kancel|szakmai|tanacsad|schulleit|headmaster|headteacher|kierownik|"
    r"pracownik|responsable|advisor|adviser|lecturer|professor|teacher|tutor|consultant|liaison|delegate|representative|"
    r"lehrer|lehrkr|enseignant|insegnant|docent|nauczyciel|tanar|ucitel|mokytoj|opettaja|larare|leraar|profesor|professeur|"
    r"maestr|native speaker|specialist|speciali|spezialist|expert|inspector|technician|clerk|employee|administrator|"
    r"skyri|vadov|contact person|point of contact|kontaktperson|nachalnik|rukovod|carer|attache|delegat|counsel")

LLM_SLOTS = threading.Semaphore(4)      # run.py resizes this from config (free tiers have requests-per-minute caps)

ROLE_LABEL = {
    "institutional_erasmus_coordinator": "Institutional Erasmus+ coordinator",
    "international_relations_head": "Head of international relations",
    "international_relations_officer": "International relations officer",
    "mobility_coordinator": "Mobility coordinator",
    "vice_rector_international": "Vice-rector for international affairs",
    "faculty_coordinator": "Faculty-level Erasmus/international coordinator",
    "principal_or_director": "Principal / director (no Erasmus contact found)",
    "secretary_or_admin": "Secretary / administration (last resort)",
    "other": "Other role",
}
ROLE_ORDER = list(ROLE_LABEL)
ROLE_CONF = {"institutional_erasmus_coordinator": 0.9, "international_relations_head": 0.85,
             "international_relations_officer": 0.8, "mobility_coordinator": 0.8, "vice_rector_international": 0.7,
             "faculty_coordinator": 0.6, "principal_or_director": 0.55, "secretary_or_admin": 0.4, "other": 0.3}

COUNTRY_ALIASES = {
    "spain": ["spain", "espana", "espagne", "spanien", "spagna", "hiszpania"],
    "france": ["france", "francia", "frankreich", "francja"],
    "cyprus": ["cyprus", "chypre", "chipre", "zypern", "cipro", "kypros"],
    "czechia": ["czech", "tcheq", "tschech", "cesk", "ceska"],
    "poland": ["poland", "pologne", "polonia", "polen", "polska"],
    "slovenia": ["slovenia", "slovenie", "eslovenia", "slowenien", "slovenij"],
    "slovakia": ["slovak", "slovaquie", "eslovaquia", "slowakei", "slovensk"],
    "sweden": ["sweden", "suede", "suecia", "schweden", "sverige"],
    "hungary": ["hungary", "hongrie", "hungria", "ungarn", "magyar"],
    "austria": ["austria", "autriche", "osterreich", "oesterreich"],
    "netherlands": ["netherlands", "pays-bas", "nederland", "holland", "paises bajos"],
    "ireland": ["ireland", "irlande", "irlanda", "eire"],
    "lithuania": ["lithuania", "lituanie", "lituania", "litauen", "lietuva"],
    "germany": ["germany", "allemagne", "alemania", "deutschland"],
    "portugal": ["portugal"], "italy": ["italy", "italie", "italia", "italien"],
    "bulgaria": ["bulgaria", "bulgarie", "bulgarien", "balgar"],
    "belgium": ["belgium", "belgique", "belgica", "belgien", "belgie"],
    "latvia": ["latvia", "lettonie", "letonia", "lettland", "latvij"],
    "estonia": ["estonia", "estonie", "estland", "eesti"],
    "romania": ["romania", "roumanie", "rumania", "rumanien"],
    "greece": ["greece", "grece", "grecia", "griechenland", "ellada", "hellas"],
    "finland": ["finland", "finlande", "finlandia", "finnland", "suomi"],
    "denmark": ["denmark", "danemark", "dinamarca", "danmark"],
    "croatia": ["croatia", "croatie", "croacia", "kroatien", "hrvatska"],
    "malta": ["malta"], "norway": ["norway", "norvege", "noruega", "norwegen", "norge"],
    "united kingdom": ["united kingdom", "royaume-uni", "reino unido", "england", "scotland", "wales", " uk"],
    "serbia": ["serbia", "serbie", "srbija"],
}


def _n(s):
    s = unicodedata.normalize("NFKD", (s or "").lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def country_in_line(name, text):
    """A country claim needs the country (or a known local spelling) on the cited line."""
    n, t = _n(name).strip(), _n(text)
    if not n:
        return False
    aliases = next((v for k, v in COUNTRY_ALIASES.items() if n == k or n.startswith(k) or k.startswith(n)), [n])
    return any(a in t for a in aliases)


def partner_line_ok(name, text):
    """Latin-script lines must contain a distinctive word of the partner's name. Lines in other scripts
    (Greek, Cyrillic, Chinese, Arabic...) cannot be compared with an English name and are accepted;
    their quote is still verified verbatim against the page."""
    letters = [c for c in text if c.isalpha()]
    if letters and sum(1 for c in letters if "LATIN" in unicodedata.name(c, "")) / len(letters) < 0.5:
        return True
    toks = [t for t in re.findall(r"[a-z0-9]{4,}", _n(name)) if t not in discover.STOP] or re.findall(r"[a-z0-9]{3,}", _n(name))
    return any(t in _n(text) for t in toks)


PARTICLES = {"van", "von", "de", "del", "della", "di", "da", "der", "den", "le", "la", "el", "al", "bin", "ben", "ibn",
             "dos", "das", "du", "st", "mc"}
TITLES = re.compile(r"^(dr|prof|mgr|ing|mag|mr|mrs|ms|mme|herr|frau|sr|sra|ba|ma|phd|msc|bsc|jr|doc|judr|mudr|phdr|rndr|paeddr|pani|pan)\.?,?$", re.I)


def looks_like_person(name):
    """A callable contact needs a real personal name: two or more capitalised words, no digits or '@', and not a
    job title ('titkar', 'Secretary Office') that the model mistook for a name."""
    words = [w for w in re.split(r"\s+", (name or "").strip()) if w]
    core = [w for w in words if w.strip(".,").lower() not in PARTICLES and not TITLES.match(w)]
    if len(core) < 2 or any("@" in w or any(ch.isdigit() for ch in w) for w in words):
        return False
    if not all(w[0].isupper() for w in core if w[0].isalpha()):
        return False
    return not any(ROLE_STEMS.search(_n(w)) for w in core)


def _pick_email(name, texts):
    found = []
    for t in texts:
        for e in evidence.emails_in(t):
            if e.lower() not in [x.lower() for x in found]:
                found.append(e)
    if not found:
        return ""
    toks = _name_toks(name)
    for e in found:
        if _matches_name(e, toks):
            return e
    return found[0] if len(found) == 1 else ""       # several unrelated emails: attach none rather than guess


def _name_toks(name):
    return re.findall(r"[a-z]{3,}", _n(name))


def _matches_name(email, toks):
    """A personal address carries part of its owner's name (ruta@, a.kovacs@); a shared inbox (trs@, info@) does not."""
    return bool(toks) and any(t in _n((email or "").split("@", 1)[0]) for t in toks)


def _is_own_email(email, toks):
    """Stricter than _matches_name, for taking an address off a page found by search: EVERY piece of the local
    part must fit this person's name. 'katja.kaikkonen' is not Meira Kaikkonen's address and 'paola.cioffi' is not
    Paola Teti's, although each shares one name word - both were wrongly accepted before this check existed.
    A piece fits if it is a name word, the start of one (an initial), or a name word plus such a start."""
    def fits(piece):
        if not piece or any(t.startswith(piece) for t in toks):
            return True
        return any(t in piece and (lambda rest: not rest or any(o.startswith(rest) for o in toks if o != t))(piece.replace(t, "", 1))
                   for t in toks)
    pieces = [re.sub(r"\d+", "", p) for p in re.split(r"[._\-+]", _n((email or "").split("@", 1)[0]))]
    return bool(toks) and any(p in toks for p in pieces if len(p) >= 3) and all(fits(p) for p in pieces) \
        or bool(toks) and len(pieces) == 1 and any(t in pieces[0] for t in toks) and fits(pieces[0])


def find_profile_link(name, pages, site):
    """A same-site link whose label is this person's full name: the staff-directory -> own-profile pattern.
    Every name word must be in the label - a shared first name alone would point at somebody else's page."""
    toks = _name_toks(name)
    if len(toks) < 2:
        return None
    for p in pages:
        for label, url in p.get("links") or []:
            if url.startswith("http") and crawl.same_site(url, site) and all(t in _n(label) for t in toks):
                return url
    return None


MAX_EMAIL_PAGES = 3

# A "contact" that is really an office ("Ufficio Relazioni Internazionali", "Erasmus Office") has no personal
# address to find: searching for one costs a paid search and can only turn up another shared inbox. Words that
# are also ordinary surnames (Bureau, Centre, Service) are deliberately left out.
OFFICE_WORDS = re.compile(
    r"\b(office|ufficio|oficina|officina|biuro|buro|buero|department|departament|departamento|departement|dipartimento|"
    r"oddeleni|oddelenie|skyrius|dzial|referat|abteilung|sekretariat|secretariat|secretaria|segreteria|titkarsag|"
    r"international|internacional|internazional\w*|internationale\w*|miedzynarodow\w*|nemzetkozi|mezinarodni\w*|"
    r"medzinarodn\w*|tarptautini\w*|relations|relazioni|relaciones|relacoes|kapcsolat\w*|kozpont|erasmus|mobility|"
    r"mobilite|mobilidad|movilidad|mobilita|admissions|auslandsamt|team|unit)\b")


def is_office(name):
    return bool(OFFICE_WORDS.search(_n(name)))


def upgrade_generic_email(contact, pages, site, institution="", web_search=None):
    """When the chosen contact has only a shared inbox (or no email), look for their OWN work address: first the
    person's profile page linked on the site, then - if web_search is given - pages a web search returns for
    their name. No model reads the address: each candidate page is opened and must itself show the person's full
    name and an address that carries their name on the institute's own mail domain. The shared inbox is kept as
    office_email; if nothing qualifies the contact is returned untouched."""
    toks = _name_toks(contact.get("name", ""))
    old = contact.get("email") or ""
    if len(toks) < 2 or _matches_name(old, toks) or is_office(contact.get("name", "")):
        return contact
    domains = {crawl._host(site)} | ({old.split("@", 1)[1].lower()} if "@" in old else set())

    def own_email(url):
        try:
            rec = fetch_pages.fetch(url)
        except Exception:
            return None
        text = rec.get("text", "")
        if not all(t in _n(text) for t in toks):
            return None                               # this page is not about this person
        for e in evidence.emails_in(text):
            d = e.split("@", 1)[-1].lower()
            if _is_own_email(e, toks) and any(d == x or d.endswith("." + x) or x.endswith("." + d) for x in domains):
                return e, rec.get("final_url") or url
        return None

    link = find_profile_link(contact["name"], pages, site)
    hit = own_email(link) if link else None
    if not hit and web_search:
        try:
            found = web_search(contact["name"], institution, crawl._host(site))
        except Exception:
            found = []
        for url in [u for u in found if u != link][:MAX_EMAIL_PAGES]:
            hit = own_email(url)
            if hit:
                break
    if not hit or hit[0].lower() == old.lower():
        return contact
    return {**contact, "office_email": old, "email": hit[0], "email_source_url": hit[1],
            "why_chosen": (contact.get("why_chosen", "") + " Own work email found on a page naming them.").strip()}


def _same_page(line, ref):
    return ref is not None and line is not None and ref["url"] == line["url"]


TOPIC = re.compile(r"erasmus|international|mobilit|outgoing|incoming|exchange|relazioni|relations|auslandsamt|internacional", re.I)


def topic_role_ok(nl, rl, c, pack):
    """Staff lists often label a person by their remit, not a title ('Veronica Bonanno / Erasmus Students'). Accept that only when
    the label sits within two lines of the name AND the same person has their own email or phone on that page - a byline
    with no contact details still fails."""
    if not TOPIC.search(_n(rl["text"])) or abs(rl["n"] - nl["n"]) > 2:
        return False
    for key in ("email_line", "phone_line"):
        ln = pack.get(c.get(key))
        if ln and _same_page(nl, ln) and abs(ln["n"] - nl["n"]) <= 6:
            return True
    return False


def resolve_contacts(data, pack):
    out = []
    for c in data.get("contacts") or []:
        nl = pack.get(c.get("name_line"))
        name = (c.get("name") or "").strip()
        if not nl or not name or not looks_like_person(name):
            continue
        toks = re.findall(r"[a-z]{2,}", _n(name))
        if not toks or not all(t in _n(nl["text"]) for t in toks):
            continue                                    # model pointed at a line that does not contain this person
        rl = pack.get(c.get("role_line"))
        rl = rl if _same_page(nl, rl) else nl
        if not ROLE_STEMS.search(_n(rl["text"] + " " + nl["text"])) and not topic_role_ok(nl, rl, c, pack):
            continue                                    # no role is stated on the cited line(s): not evidence of a contact
        if rl["n"] == nl["n"]:
            quote = nl["text"]
        else:
            first, second = sorted([nl, rl], key=lambda x: x["n"])
            quote = first["text"] + " ... " + second["text"]
        el = pack.get(c.get("email_line"))
        el = el if _same_page(nl, el) else None
        email = _pick_email(name, [el["text"] if el else "", nl["text"], rl["text"]])
        pl = pack.get(c.get("phone_line"))
        phone = evidence.first_phone(pl["text"]) if _same_page(nl, pl) else ""
        cls = c.get("role_class") if c.get("role_class") in ROLE_CONF else "other"
        conf = ROLE_CONF[cls] + (0.05 if email else -0.1)
        out.append({"name": name, "designation": c.get("designation_english") or "", "designation_local": "",
                    "email": email, "office_email": "", "phone": phone, "source_url": nl["url"],
                    "evidence_quote": quote, "role_class": cls,
                    "why_chosen": f"{ROLE_LABEL[cls]}. {c.get('why', '').strip()}".strip(),
                    "confidence": round(max(0.3, min(conf, 0.95)), 2)})
    out.sort(key=lambda x: (ROLE_ORDER.index(x["role_class"]), 0 if x["email"] else 1))
    return out


def boost_on_file(contacts, on_file):
    """The client's operators already spoke to someone at this institution. If that person shows up in the
    evidence with a stated role, they go first: a real phone conversation beats any ranking of titles."""
    toks = re.findall(r"[a-z]{4,}", _n(on_file))
    hit = next((c for c in contacts if toks and any(t in _n(c["name"]) for t in toks)), None)
    if not hit:
        return contacts
    hit["why_chosen"] = "Matches the contact your operators already spoke to. " + hit["why_chosen"]
    return [hit] + [c for c in contacts if c is not hit]


def put_on_file_last(contacts, on_file, on_file_email=""):
    """The person on file does not answer calls: rank everyone else first, keep the on-file person only as a last resort."""
    toks = re.findall(r"[a-z]{3,}", _n(on_file))
    mail = (on_file_email or "").strip().lower()

    def is_on_file(c):
        return bool((toks and all(t in _n(c["name"]) for t in toks[:2])) or (mail and c.get("email", "").lower() == mail))
    others = [c for c in contacts if not is_on_file(c)]
    same = [c for c in contacts if is_on_file(c)]
    for c in same:
        c["why_chosen"] = "This is the person already on file (not answering). " + c["why_chosen"]
    return others + same


def _institution_phone(pack, sheet_phone):
    digits = re.sub(r"\D", "", sheet_phone or "")
    if len(digits) < 8:
        return None
    suffix = digits[-8:]
    for ln in pack.lines:
        if suffix in re.sub(r"\D", "", re.sub(r"[\s().\-/]", "", ln["text"])):
            return {"value": evidence.first_phone(ln["text"]) or sheet_phone, "source_url": ln["url"],
                    "evidence_quote": ln["text"]}
    return None


def resolve_network(data, pack, institution_name):
    own = set(discover.name_tokens(institution_name))
    partners, seen = [], set()
    for p in data.get("partners") or []:
        ln, name = pack.get(p.get("line")), (p.get("name") or "").strip()
        if not ln or not name or _n(name) in seen or not partner_line_ok(name, ln["text"]):
            continue
        if own and set(discover.name_tokens(name)) and set(discover.name_tokens(name)) <= own:
            continue                                    # the institution itself, not a partner
        seen.add(_n(name))
        partners.append({"partner_name": name, "partner_country": p.get("country") or "", "partner_type": p.get("type") or "other",
                         "mobility_type": p.get("mobility") or "unknown", "source_url": ln["url"],
                         "evidence_quote": ln["text"], "confidence": 0.9})
    campuses, seen_c = [], set()
    for m in data.get("campuses") or []:
        ln, name = pack.get(m.get("line")), (m.get("name") or "").strip()
        if not ln or not name or _n(name) in seen_c:
            continue
        seen_c.add(_n(name))
        campuses.append({"campus_name": name, "city": m.get("city") or "", "country": m.get("country") or "",
                         "role": m.get("role") or "branch", "source_url": ln["url"], "evidence_quote": ln["text"]})
    st = data.get("partners_stated_total") or {}
    stated_line = pack.get(st.get("line"))
    stated = int(st.get("value") or 0) if stated_line else 0
    return partners, campuses, stated, (stated_line or {})


def partner_windows(pack, size=140, min_hits=4):
    """Slices of the evidence that look like a list of institutions. A model asked about a whole page tends to stop
    after a dozen partners; asked about one slice at a time it goes through it line by line."""
    hit = lambda t: bool(rank.INSTITUTION_WORD.search(t)) and len(t) < 160     # noqa: E731
    wins, total = [], len(pack.lines)
    for start in range(1, total + 1, size):
        end = min(start + size - 1, total)
        if sum(1 for l in pack.window(start, end).lines if hit(l["text"])) >= min_hits:
            wins.append((start, end))
    return wins


def extract_partner_slices(inst, pack, provider, model):
    """Partners-only model calls over list-like slices, in parallel. Returns ({partners, stated, cost}, slices_missed)."""
    from concurrent.futures import ThreadPoolExecutor
    wins = partner_windows(pack)
    out = {"partners": [], "stated": {"value": 0, "line": 0}, "cost": 0.0}
    if not wins:
        return out, 0
    missed = 0

    def one(w):
        with LLM_SLOTS:
            return llm.extract_partners(inst, pack.window(*w), provider, model)
    with ThreadPoolExecutor(max_workers=min(len(wins), 4)) as pool:
        futs = [pool.submit(one, w) for w in wins]
        for f in futs:
            try:
                d, meta = f.result()
            except llm.LLMError:
                missed += 1
                continue
            out["partners"] += d.get("partners") or []
            out["cost"] += meta.get("cost_usd_estimate", 0)
            st = d.get("partners_stated_total") or {}
            if st.get("value", 0) > out["stated"]["value"] and st.get("line", 0):
                out["stated"] = st
    return out, missed


def unresolved(row, note, timing):
    return {"row_id": row["row_id"], "scraped_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "contacts": {"row_id": row["row_id"], "institution_name": row.get("institution_name") or "",
                         "institution_type": "unknown", "official_website": "", "country": None, "phone": None,
                         "contact": None, "alternates": [], "rung": "", "source": "none", "confidence": 0,
                         "needs_human_check": True, "pages_read": 0, "notes": note},
            "network": None, "network_skipped": "website not found", "timing": timing}


def scrape(row, cfg):
    """row: a numbers_clean.csv row (+ optional website_override). cfg: config.yaml `scraper` section."""
    t_all, timing = time.time(), {}
    inst = {"row_id": row["row_id"], "name": row.get("institution_name", ""), "country": row.get("country_sheet", ""), "phone": row.get("phone_1", ""),
            "contact": row.get("contact_name", ""), "email": row.get("email", ""),
            "website_override": row.get("website_override", ""), "alternatives": bool(cfg.get("alternatives"))}

    t = time.time()
    site, how, note = discover.find_website(inst, use_search=bool(cfg.get("discover_with_search")),
                                            paid_search=bool(cfg.get("claude_site_search")),
                                            search_provider=cfg.get("search_provider") or
                                            ("gemini" if cfg.get("provider") == "gemini" else "claude_cli"))
    timing["discover_s"] = round(time.time() - t, 1)
    if not site:
        timing["total_seconds"] = round(time.time() - t_all, 1)
        return unresolved(row, f"Website not found ({note})", timing)

    t = time.time()
    cr = crawl.crawl(site, max_pages=int(cfg.get("max_pages", 10)), workers=int(cfg.get("page_workers", 5)),
                     seeds=(row.get("seed_urls") or "").split())
    timing["crawl_s"] = round(time.time() - t, 1)
    if not cr["pages"]:
        timing["total_seconds"] = round(time.time() - t_all, 1)
        return unresolved(row, "Website could not be read: " + "; ".join(f"{u} ({e})" for u, e in cr["errors"][:3]), timing)

    if not how.startswith("remembered"):
        discover.remember(row["row_id"], cr["site"], how)       # never search for this institute again
    pack = evidence.build_pack(cr["pages"], budget_chars=int(cfg.get("evidence_chars", 90000)))
    provider, meta = cfg.get("provider", "claude_cli"), {}
    t = time.time()
    try:
        if provider == "rules":
            raise llm.LLMError("rules provider selected")
        with LLM_SLOTS:
            data, meta = llm.extract(inst, pack, provider, cfg.get("model") or None)
        mode = provider
    except llm.LLMError as e:
        if provider != "rules":
            # never save a degraded rules-only answer over (or instead of) a real one: the model was unavailable
            # (limit reached, network, bad key). The caller keeps whatever result already exists and retries later.
            timing["total_seconds"] = round(time.time() - t_all, 1)
            return {"row_id": row["row_id"], "retry": True, "reason": f"model unavailable: {str(e)[:160]}", "timing": timing}
        data = rank.extract(pack, inst)
        mode = "rules"
    slice_note = ""
    if mode == provider and provider != "rules":
        extra, missed = extract_partner_slices(inst, pack, provider, cfg.get("model") or None)
        data["partners"] = list(data.get("partners") or []) + extra["partners"]
        st = extra["stated"]
        if st.get("value", 0) > (data.get("partners_stated_total") or {}).get("value", 0):
            data["partners_stated_total"] = st
        timing["cost_usd_estimate"] = round(meta.get("cost_usd_estimate", 0) + extra["cost"], 4)
        if missed:
            slice_note = f" {missed} list slice(s) could not be read (model busy); the partner list may be incomplete."
    timing["model_s"] = round(time.time() - t, 1)
    timing.setdefault("cost_usd_estimate", meta.get("cost_usd_estimate", 0))
    timing["mode"] = mode
    if meta.get("fallback"):                      # the free reader was unavailable and the paid one stood in
        timing["reader"] = meta.get("model_used")

    contacts = resolve_contacts(data, pack)
    if cfg.get("alternatives"):
        contacts, n_alt = put_on_file_last(contacts, inst["contact"], inst["email"]), 3
    else:
        contacts, n_alt = boost_on_file(contacts, inst["contact"]), 2
    primary, alternates = (contacts[0] if contacts else None), contacts[1:1 + n_alt]
    if primary:
        t = time.time()
        primary = upgrade_generic_email(primary, cr["pages"], cr["site"], inst["name"],
                                        discover.person_pages if cfg.get("email_web_search", True) else None)
        timing["email_search_s"] = round(time.time() - t, 1)
    country_claim = None
    cl = pack.get((data.get("country") or {}).get("line"))
    if cl and country_in_line((data.get("country") or {}).get("name"), cl["text"]):
        country_claim = {"value": data["country"]["name"], "source_url": cl["url"], "evidence_quote": cl["text"]}
    name = inst["name"] or (pack.lines[0]["text"][:80] if pack.lines else "")
    partners, campuses, stated, st_line = resolve_network(data, pack, name)

    unread = f" {len(cr['errors'])} page(s) could not be read." if cr["errors"] else ""
    contact_obj = {"row_id": row["row_id"], "institution_name": name,
                   "institution_type": data.get("institution_type") or "unknown", "official_website": cr["site"],
                   "country": country_claim, "phone": _institution_phone(pack, inst["phone"]), "phone_alt": [],
                   "contact": ({k: v for k, v in primary.items() if k not in ("role_class", "why_chosen", "confidence")}
                               if primary else None),
                   "alternates": [{k: v for k, v in a.items() if k not in ("role_class", "confidence")} for a in alternates],
                   "rung": primary["why_chosen"] if primary else "", "source": "web",
                   "confidence": primary["confidence"] if primary else 0.2,
                   "needs_human_check": (not primary) or (not primary["email"]),
                   "pages_read": len(cr["pages"]), "notes": f"site found via: {how}. mode: {mode}{' (read by Claude Haiku: Gemini was unavailable)' if meta.get('fallback') else ''}.{unread}"}
    network_obj = {"external_collaboration": partners, "internal_collaboration": campuses,
                   "stated_total": stated or None, "stated_total_quote": st_line.get("text", ""),
                   "stated_total_url": st_line.get("url", ""), "truncated": bool(stated and stated > len(partners)),
                   "total_seen": stated or len(partners), "pages_read": len(cr["pages"]),
                   "coverage_note": ((data.get("coverage_note") or "") + unread + slice_note).strip(),
                   "needs_human_check": bool(slice_note),
                   "multiplier_hook": ""}
    timing["total_seconds"] = round(time.time() - t_all, 1)
    return {"row_id": row["row_id"], "scraped_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "contacts": contact_obj, "network": network_obj, "timing": timing}
