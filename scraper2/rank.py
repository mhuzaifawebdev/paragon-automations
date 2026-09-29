"""Rules-only extraction (no model): the free fallback, and the safety net when a provider is down
or rate-limited. Returns the SAME shape the model returns (line numbers into the evidence pack),
so resolution and verification are identical.

Lower recall than a model on unusual pages; equally accurate, because everything it returns is
still checked against the page by scraper/verify.py.
"""
import re
import unicodedata

from evidence import EMAIL_RE

# role class -> patterns (accent/case-insensitive), best first
ROLE_PATTERNS = [
    ("institutional_erasmus_coordinator", [r"erasmus\+?\s*(institutional\s*)?(coordinat|koordinat|officer|responsable|referent)",
        r"(coordinat\w*|responsable|referent\w*|koordinat\w*)\s+(institucional\s+)?(de\s+|del\s+|for\s+)?erasmus",
        r"erasmus\s*(\+)?\s*(coordinator|koordinator|coordinador|coordinateur)", r"institutional coordinator"]),
    ("international_relations_head", [r"head of (the )?international", r"director\w*\s+(of\s+)?(the\s+)?international",
        r"(responsable|directeur|directrice|jefe|jefa|leiter\w*)\s+(des?\s+|del?\s+)?(relations|relaciones|internationale|auslands)",
        r"international relations (office )?(head|manager|director)"]),
    ("international_relations_officer", [r"international (relations )?(officer|office|affairs officer)", r"relations internationales",
        r"relaciones internacionales", r"auslandsamt", r"internationales? (buro|büro)"]),
    ("mobility_coordinator", [r"mobility (coordinator|officer|manager|referent)", r"mobilit\w*\s+(coordinat|referent|responsable)",
        r"(coordinat\w*|referent\w*|responsable)\s+(de\s+)?(la\s+)?mobilit\w*"]),
    ("vice_rector_international", [r"vice[- ]?rector\w*.*international", r"pro[- ]?rector\w*.*international",
        r"vice[- ]?president.*international"]),
    ("faculty_coordinator", [r"faculty.*(erasmus|international)", r"department.*erasmus", r"faculte.*erasmus"]),
    ("principal_or_director", [r"\bprincipal\b", r"head ?master", r"head ?teacher", r"\bdirector(a)?\b", r"directeur", r"proviseur",
        r"schulleiter", r"riaditel", r"igazgat", r"dyrektor", r"direktor"]),
    ("secretary_or_admin", [r"secretar", r"secrétariat", r"reception", r"administration"]),
]
NAME_WORD = r"[A-ZÀ-ÖØ-ÞĀ-Ž][^\W\d_]+(?:['’\-][^\W\d_]+)*"
NAME_RE = re.compile(rf"(?:(?:Dr|Prof|Mgr|Ing|Mme|Mr|Mrs|Ms|Herr|Frau|Sr|Sra|Dr\.|Prof\.)\.?\s+)?({NAME_WORD}(?:\s+{NAME_WORD}){{1,3}})")
NOT_NAMES = {"erasmus", "coordinator", "coordinateur", "coordinador", "international", "office", "university", "universit",
             "department", "faculty", "contact", "director", "principal", "secretary", "relations", "mobility", "students",
             "student", "staff", "team", "school", "college", "institute", "institut", "erasmus+", "programme", "program",
             "internationale", "internacionales", "responsable", "service", "services", "head", "the", "our", "and",
             "for", "des", "del", "de", "la", "le", "les", "und", "der", "die", "das", "email", "phone", "tel",
             "centre", "center", "centro", "universite", "universidad", "universitat", "universita", "institute", "instituto",
             "academy", "akademia", "akademija", "research", "group", "association", "foundation", "society", "council",
             "europe", "european", "national", "regional", "digital", "media", "news", "consulting", "technology",
             "technologies", "systems", "solutions", "partners", "partner", "network", "project", "projects", "education",
             "training", "learning", "campus", "library", "science", "sciences", "studies", "management", "business",
             "economics", "engineering", "polytechnic", "technical", "vocational", "secondary", "primary", "higher"}
PARTNER_HEAD = re.compile(r"partner|partenaire|socios|cooperat|cooperaci|kooperation|agreement|convenio|accord|alliance|network|netzwerk|"
                          r"współprac|spolupr|bendradarb|együttm", re.I)
INSTITUTION_WORD = re.compile(r"universit|universidad|universidade|universitat|uniwersytet|univerzit|hochschule|college|"
                              r"école|escola|akademi|academy|institut|lycée|gymnas|school|kolegij|politechnik", re.I)


def _n(s):
    s = unicodedata.normalize("NFKD", (s or "").lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def _role_of(text):
    t = _n(text)
    for cls, pats in ROLE_PATTERNS:
        for p in pats:
            if re.search(p, t):
                return cls
    return None


def _person(text):
    for m in NAME_RE.finditer(text):
        words = m.group(1).split()
        if not any(_n(w).strip(".") in NOT_NAMES for w in words):
            return m.group(1)
    return ""


def extract(pack, institute):
    """Same output shape as the model's schema."""
    import discover
    own = set(discover.name_tokens(institute.get("name")))
    lines = pack.lines
    contacts, used = [], set()
    for i, ln in enumerate(lines):
        cls = _role_of(ln["text"])
        if not cls:
            continue
        window = [x for x in lines[max(0, i - 2): i + 3] if x["url"] == ln["url"]]
        person = next(((w, _person(w["text"])) for w in window if _person(w["text"])), None)
        if not person:
            continue
        pline, pname = person
        ptoks = set(re.findall(r"[a-z]{4,}", _n(pname)))
        if pname in used or (own and ptoks and len(ptoks & own) * 2 >= len(ptoks)):
            continue                                    # the institution's own name, not a person
        used.add(pname)
        email_ln = next((w for w in window + [x for x in lines[max(0, i - 5): i + 6] if x["url"] == ln["url"]]
                         if EMAIL_RE.search(w["text"])), None)
        contacts.append({"name": pname, "designation_english": ln["text"][:120], "role_class": cls,
                         "name_line": pline["n"], "role_line": ln["n"], "email_line": email_ln["n"] if email_ln else 0,
                         "phone_line": 0, "why": "found next to a role keyword on the institution's own page (rules-based)"})
    order = [c for c, _ in ROLE_PATTERNS]
    contacts.sort(key=lambda c: (order.index(c["role_class"]), 0 if c["email_line"] else 1))

    partners, campuses = [], []
    for i, ln in enumerate(lines):
        if not (PARTNER_HEAD.search(ln["text"]) and len(ln["text"]) < 90):
            continue
        run, gap = [], 0
        for nxt in lines[i + 1: i + 200]:
            if nxt["url"] != ln["url"]:
                break
            t = nxt["text"]
            if INSTITUTION_WORD.search(t) and len(t) < 120 and not (own and set(discover.name_tokens(t)) and set(discover.name_tokens(t)) <= own):
                run.append(nxt)
                gap = 0
            else:
                gap += 1
                if gap > 6:
                    break
        if len(run) >= 3:                               # a real list, not a stray mention
            partners += [{"line": r["n"], "name": r["text"], "country": "", "type": "other", "mobility": "unknown"} for r in run[:80]]
    seen_lines, uniq = set(), []
    for p_ in partners:
        if p_["line"] not in seen_lines:
            seen_lines.add(p_["line"])
            uniq.append(p_)
    partners = uniq
    text_all = " ".join(l["text"] for l in lines[:120]).lower()
    itype = ("university" if re.search(r"universit|universidad|hochschule|uniwersytet", text_all) else
             "school" if re.search(r"lycée|gymnas|secondary|\bies\b|escola|school", text_all) else "unknown")
    return {"institution_type": itype, "country": {"name": "", "line": 0}, "contacts": contacts[:3],
            "partners": partners[:300], "partners_stated_total": {"value": 0, "line": 0}, "campuses": campuses,
            "coverage_note": "Rules-only extraction (no model): partner and campus lists may be incomplete."}
