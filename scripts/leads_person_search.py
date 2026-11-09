"""Named contacts for institutes whose own website names nobody: a free DuckDuckGo search for the person.

Why: the site reader (leads_local_fill people step) has read every one of these sites and found no name, and the gap
worker only looks for emails and websites. Search results often name the person the site does not: a public LinkedIn
title "Maria Rossi - Dirigente Scolastico - IIS Galilei", or a snippet "Dirigente scolastico: Prof. Mario Rossi".

Strict, so nothing is made up: a name is kept only when
  - it looks like a person (2-4 capitalised words, no school/company words),
  - the same result names the institute (its own words, or its website),
  - and a role is given next to it (director, principal, coordinator, owner, manager ...).
The quote and the link are kept as evidence and written to the sheet with the name.

  export (PC):     python scripts/leads_person_search.py export leads_local/gap/todo_people.csv
  search (server): python scripts/leads_person_search.py run todo_people.csv people.jsonl --split 0/2
  write  (PC):     python scripts/leads_person_search.py apply people1.jsonl people2.jsonl [--dry-run]
Searching needs no Drive and no keys; export and apply read and write the local copies / Drive from the PC.
"""
import csv
import html
import json
import re
import sys
import time
import urllib.parse
import zlib
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scraper2"))
import erasmus_pass2 as p2  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
MARK = "personsearch"

ROLE = re.compile(
    r"\b(director|directora|directeur|directrice|direktor|dyrektor|dyrektorka|diretor|diretora|direttore|direttrice|"
    r"dirigente(?: scolastico| scolastica)?|preside|principal|head ?teacher|head of school|headmaster|headmistress|"
    r"head of [a-z ]{3,30}|deputy head|vice[- ]?principal|proviseur|proviseure|principal adjoint|schulleiter(?:in)?|"
    r"rektor|rector|rettore|recteur|dean|decano|doyen|dziekan|kanclerz|chancellor|president|presidente|présidente?|"
    r"prezes|ceo|managing director|general manager|founder|co-?founder|owner|proprietor|geschäftsführer(?:in)?|"
    r"gerente|g[ée]rant|amministratore|administrador|manager|store manager|coordinator|coordinatrice|coordinatore|"
    r"coordinador|coordinadora|coordinateur|coordinatrice|koordynator|koordynatorka|koordinator(?:in)?|"
    r"erasmus[+ ]*(?:coordinator|officer|contact)?|international (?:office|relations|coordinator|officer)|"
    r"head of international|relations internationales|relaciones internacionales|project manager|"
    r"admissions (?:officer|manager)|registrar|secretary general|segretario generale|bursar)\b", re.I)
BAD_ROLE = re.compile(r"\b(student|stagiaire|intern|trainee|alumn|estudiante|studente|uczeń|élève|praktikant)", re.I)
ORGWORD = re.compile(
    r"\b(school|schule|scuola|escuela|école|ecole|szkoła|szkola|escola|liceo|lycée|lycee|istituto|instituto|institut|"
    r"institute|college|colegio|collège|university|università|universidad|université|uniwersytet|academy|akademie|"
    r"accademia|academia|centre|center|centro|zentrum|ltd|limited|gmbh|s\.?r\.?l|s\.?p\.?a|s\.?l|sa|sas|association|"
    r"asociación|associazione|stowarzyszenie|fundación|fondazione|foundation|fundacja|group|gruppo|grupo|services|"
    r"linkedin|profile|profil|perfil|view|team|staff|office|ministry|department|erasmus|international|the|and|und|"
    r"store|shop|malta|italy|spain|france|poland|germany|greece|portugal)\b", re.I)
HON = (r"(?i:prof\.?ssa|prof(?:\.|essor|essa|esseur)?|dr\.?|dott(?:\.|ssa\.?|oressa)?|mr\.?|mrs\.?|ms\.?|mme\.?|m\.|"
       r"sig(?:\.|ra\.?)?|don|doña|dña\.?|sr\.?|sra\.?|pani?|ing\.?|arch\.?|avv\.?|mag\.?)")
TITLE = re.compile(rf"^{HON}\s+", re.I)
NAMEW = r"[A-ZÀ-ÖØ-ÞĀ-ſ][a-zà-öø-ÿā-ſ'’-]+"
PERSON = re.compile(rf"^{NAMEW}(?:\s+(?:de |del |della |di |da |van |von |la |le |dos |das )?{NAMEW}){{1,3}}$")
CC_WORDS = {"IT": "dirigente scolastico OR preside OR coordinatore", "FR": "directeur OR proviseur OR coordinateur",
            "ES": "director OR directora OR coordinador", "PT": "diretor OR diretora OR coordenador",
            "DE": "Schulleiter OR Geschäftsführer OR Leiter", "AT": "Schulleiter OR Direktor OR Geschäftsführer",
            "PL": "dyrektor OR koordynator", "EL": "director OR διευθυντής", "RO": "director OR coordonator",
            "BG": "директор OR director", "NL": "directeur OR coördinator", "BE": "directeur OR directie"}


def clean(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).replace("\xa0", " ").strip()


_state = {"next": 0.0, "fails": 0}


def ddg(query, gap=None):
    """[(url, title, snippet)] of one DuckDuckGo search. One stream, `gap` seconds apart; when DuckDuckGo asks to slow
    down it waits 2, 4, 6 ... minutes (at most 30) and tries again, never faster."""
    while True:
        wait = _state["next"] - time.time()
        if wait > 0:
            time.sleep(wait)
        try:
            _, body = p2.get("https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query), timeout=25)
        except Exception as e:
            body = f"ERROR {e}"
        _state["next"] = time.time() + (gap or float(__import__("os").environ.get("DDG_GAP", "8")))
        if "anomaly" in body.lower() or body.startswith("ERROR"):
            _state["fails"] += 1
            pause = min(1800, 120 * _state["fails"])
            print(f"   (DuckDuckGo asked to slow down: waiting {pause // 60} min)", flush=True)
            _state["next"] = time.time() + pause
            continue
        _state["fails"] = 0
        out = []
        for m in re.finditer(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</a>',
                             body, re.S):
            u = urllib.parse.unquote(re.sub(r".*uddg=", "", m.group(1)).split("&")[0])
            out.append((u, clean(m.group(2)), clean(m.group(3))))
        return out


def person_name(s):
    """'Prof. Mario Rossi' -> 'Mario Rossi', or '' when it is not a person's name."""
    s = TITLE.sub("", " ".join((s or "").replace("|", " ").split()).strip(" ,.;:-–—"))
    s = TITLE.sub("", s)
    if not PERSON.match(s) or ORGWORD.search(s) or ROLE.search(s) or len(s) > 40:
        return ""
    if s.isupper() and len(s.split()) < 2:
        return ""
    return s


def names_org(text, org, site_host):
    """True when the text names the institute: its website, or enough of its own words."""
    t = p2.fold(text)
    if site_host and site_host.split(".")[0] in t.replace(" ", "") and len(site_host.split(".")[0]) > 4:
        return True
    core = [w for w in p2.toks(org) if len(w) > 2]
    if not core:
        return False
    hit = sum(1 for w in core if f" {w} " in f" {t} ")
    return hit >= min(len(core), 2) if len(core) <= 3 else hit >= max(2, round(len(core) * 0.6))


def candidates(results, org, site_host):
    out = []
    for url, title, snip in results:
        blob = f"{title} {snip}"
        if BAD_ROLE.search(title):
            continue
        u_host = p2.host(url) or ""
        own_site = site_host and (u_host == site_host or u_host.endswith("." + site_host))
        if "linkedin.com/in/" in url:
            # "Name - Role - Organisation | LinkedIn" (or "Name – Role at Organisation")
            parts = [x.strip() for x in re.split(r"\s+[-–—|]\s+", title.replace(" | LinkedIn", "")) if x.strip()]
            if len(parts) < 2:
                continue
            name = person_name(parts[0])
            role = " - ".join(parts[1:-1]) if len(parts) > 2 else parts[1]
            m = ROLE.search(role) or ROLE.search(snip)
            if name and m and names_org(" ".join(parts[1:]) + " " + snip, org, site_host):
                out.append({"name": name, "designation": (role if ROLE.search(role) else m.group(0)).strip()[:80],
                            "source_url": url, "evidence_quote": f"{title} — {snip}"[:300], "_rank": rank(role + " " + snip)})
            continue
        if not (own_site or names_org(blob, org, site_host)):
            continue
        # "Dirigente scolastico: Prof. Mario Rossi" / "Mario Rossi, director of ..." inside a snippet
        for m in ROLE.finditer(blob):
            after = re.match(rf"\s*[:,\-–—]?\s*(?:is\s+|è\s+|es\s+)?((?:{HON}\s+)?{NAMEW}(?:\s+{NAMEW}){{1,3}})",
                             blob[m.end():])
            before = re.search(rf"({NAMEW}(?:\s+{NAMEW}){{1,3}})\s*[,(\-–—]\s*(?:the\s+|il\s+|la\s+|el\s+|le\s+)?$", blob[:m.start()])
            for g in (after.group(1) if after else "", before.group(1) if before else ""):
                name = person_name(g)
                if name:
                    i = blob.find(g)
                    out.append({"name": name, "designation": m.group(0).strip(), "source_url": url,
                                "evidence_quote": blob[max(0, i - 120): i + 160].strip(), "_rank": rank(m.group(0))})
    return out


def rank(role):
    r = (role or "").lower()
    if re.search(r"erasmus|international|coordinat|koordyn|coordon", r):
        return 0
    if re.search(r"principal|dirig|preside|director|directeur|direttor|dyrektor|diretor|head|proviseur|schulleit|rektor|"
                 r"rector|rettore|dean|owner|founder|ceo|managing|geschäftsführ|president|prezes", r):
        return 1
    return 2


PHONE_WORD = {"IT": "telefono", "FR": "téléphone", "ES": "teléfono", "PT": "telefone", "DE": "Telefon", "AT": "Telefon",
              "PL": "telefon", "NL": "telefoon", "BE": "téléphone", "RO": "telefon", "EL": "τηλέφωνο", "BG": "телефон",
              "CZ": "telefon", "SK": "telefón", "HU": "telefon", "HR": "telefon", "SI": "telefon", "LT": "telefonas",
              "LV": "tālrunis", "EE": "telefon", "FI": "puhelin", "SE": "telefon", "DK": "telefon", "NO": "telefon",
              "TR": "telefon"}


LEGAL = re.compile(r"(s\.?r\.?l\.?|s\.?p\.?a\.?|s\.?l\.?u?|s\.?a\.?s?|gmbh|ag|ltd\.?|limited|plc|inc\.?|llc|"
                   r"bv|nv|sarl|eurl|oy|ab|as|aps|kft|zrt|s\.?c\.?|sp\.? z o\.?o\.?|d\.?o\.?o\.?)$", re.I)
SHEET_JUNK = re.compile(r"^(main sheet|sheet\d*|images?|untitled|test|n/?a|none|unknown|data|copy of .*)$", re.I)


def searchable(name):
    """A name a search can find without mixing it up: 2+ real words, not a sheet label or a link."""
    n = (name or "").strip()
    if not n or SHEET_JUNK.match(n) or re.match(r"https?://|www\.", n, re.I):
        return False
    words = [w for w in re.findall(r"[^\W\d_]{3,}", n)]
    return len(words) >= 2 and bool(p2.toks(n))


def clean_query_name(name):
    n = re.split(r"\s+[-–|/]\s+", " ".join(name.split()))[0]          # "IES X - MADRID" -> "IES X"
    n = LEGAL.sub("", n).strip(" ,.-")
    return n if len(n) >= 5 else " ".join(name.split())


def phone_ok(phone, url, blob, org, site_host, cc):
    """A search-found number kept only if it is the institute's: from its own website, or a listing that names it
    (80% of its own words) and the number belongs to the institute's country."""
    import leads_phone_reread as rr
    u_host = (p2.host(url) or "").removeprefix("www.")
    if site_host and (u_host == site_host or u_host.endswith("." + site_host)):
        return True
    if site_host and p2.base_domain(u_host) == p2.base_domain(site_host):
        return True
    if not searchable(org):
        return False
    core = [w for w in re.findall(r"[^\W\d_]{3,}", p2.fold(org))]
    t = f" {p2.fold(blob)} "
    if sum(1 for w in core if f" {w} " in t or f" {w}" in t) < max(2, -(-len(core) * 4 // 5)):
        return False
    cc = {"GB": "UK", "GR": "EL"}.get(cc, cc)
    code = rr.DIAL.get(cc)
    if code:                       # the institute's country is known: the number must be from there
        return phone.startswith("+" + code)
    return phone.startswith("+")   # country unknown: only a full international number, named listing


def find_phone(results, org, site_host, cc=""):
    """The institute's phone from search snippets: a number next to a Tel / Phone / Telefono label (not fax), kept
    only when phone_ok says it is the institute's own number."""
    import leads_phone_reread as rr
    cc = {"GB": "UK", "GR": "EL"}.get((cc or "").upper(), (cc or "").upper())
    for url, title, snip in results:
        blob = f"{title} {snip}"
        for m in p2.PHONE_TEXT.finditer(blob):
            if re.search(r"fax\W{0,3}$", blob[max(0, m.start() - 8):m.start()], re.I):
                continue
            raw = m.group(1)
            t = rr.norm(raw, cc) or ("+" + re.sub(r"\D", "", raw) if raw.strip().startswith("+") else "")
            if not t or not 8 <= len(t) - 1 <= 15:
                continue
            if phone_ok(t, url, blob, org, site_host, cc):
                i = m.start()
                return {"phone": t, "phone_src": url, "phone_quote": blob[max(0, i - 100): i + 80].strip()}
    return None


def search_one(row):
    org, cc = row["name"], (row.get("cc") or "").upper()
    site = row.get("site") or ""
    sh = p2.host(site if "://" in site else "https://" + site) if site else ""
    sh = (sh or "").removeprefix("www.")
    out = {"key": row["key"], "steps": []}
    need_phone, need_person = row.get("need_phone") == "1", row.get("need_person", "1") == "1"
    if not searchable(org) and not sh:
        out["steps"].append("not searchable")
        return out
    q = clean_query_name(org)
    q = f'"{q}"' if len(q.split()) <= 5 else q
    if need_phone:
        res = ddg(f'{q} {row.get("country") or ""} ' + PHONE_WORD.get(cc, "phone"))
        out["steps"].append("phone")
        ph = find_phone(res, org if searchable(org) else "", sh, cc)
        if ph:
            out.update(ph)
            need_phone = False
    found = []
    if need_person:
        queries = [f'site:linkedin.com/in "{org}"', f'"{org}" ' + CC_WORDS.get(cc, "director OR principal OR coordinator OR owner")]
        for q in queries:
            res = ddg(q)
            out["steps"].append("person")
            found = candidates(res, org, sh)
            if need_phone:
                ph = find_phone(res, org if searchable(org) else "", sh, cc)
                if ph:
                    out.update(ph)
                    need_phone = False
            if found:
                break
    if found:
        best = sorted(found, key=lambda x: x["_rank"])[0]
        best.pop("_rank", None)
        out["person"] = best
    return out


# ----------------------------------------------------------------------------- export / apply (PC, local copies)
def export(out_csv):
    import build_dashboard_stats as bds
    work = ROOT / "leads_local"
    groups = {}
    for f in sorted(work.glob("*/scrape_results/r*.json")):
        b = f.parent.parent.name
        if b.startswith(bds.RECHECK_PREFIX):
            continue
        try:
            st = json.loads((f.parent.parent / "state.json").read_text(encoding="utf-8"))
        except Exception:
            st = {}
        rid = f.stem
        if MARK in st.get(rid, []):
            continue
        r = json.loads(f.read_text(encoding="utf-8"))
        if bds.broken_row(r):
            continue
        n = bds.institute_counts(r)
        c = r.get("contacts") or {}
        ph = c.get("phone")
        ph = (ph.get("value") if isinstance(ph, dict) else ph) or (c.get("contact") or {}).get("phone")
        if n["with_contact"] and ph:
            continue
        name = " ".join(str(c.get("institution_name") or "").strip(" \"'").split())
        if sum(ch.isalpha() for ch in name) < 5 or len(name) > 150:
            continue
        site = c.get("official_website")
        site = (site.get("value") if isinstance(site, dict) else site) or ""
        if str(c.get("notes") or "").startswith("Website not found"):
            site = ""
        country = (c.get("country") or {}).get("value") if isinstance(c.get("country"), dict) else (c.get("country") or "")
        k = (p2.fold(name), (country or "").lower())
        g = groups.setdefault(k, {"key": f"{b}|{rid}", "name": name, "country": country or "", "site": site, "also": [],
                                  "need_phone": "0", "need_person": "0"})
        if not ph:
            g["need_phone"] = "1"
        if not n["with_contact"]:
            g["need_person"] = "1"
        if g["key"] != f"{b}|{rid}":
            g["also"].append(f"{b}|{rid}")
        if site and not g["site"]:
            g["site"] = site
    import leads_local_fill as lf
    rows = list(groups.values())
    for r in rows:
        r["cc"] = lf.country_code(r["country"]) or ""
        r["also"] = " ".join(r["also"])
    rows.sort(key=lambda r: (not r["site"], r["name"].lower()))
    with open(out_csv, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, ["key", "name", "country", "cc", "site", "also", "need_phone", "need_person"])
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} institutes to search ({sum(1 + len(r['also'].split()) for r in rows)} rows; phone "
          f"{sum(r['need_phone'] == '1' for r in rows)}, named person {sum(r['need_person'] == '1' for r in rows)}), "
          f"with website {sum(1 for r in rows if r['site'])} -> {out_csv}")


def apply(files, todo_csv, dry, mark=MARK):
    import leads_local_fill as lf
    also = {r["key"]: r["also"].split() for r in csv.DictReader(open(todo_csv, encoding="utf-8-sig"))}
    per = defaultdict(dict)
    phones = defaultdict(dict)
    searched = defaultdict(set)
    for f in files:
        for line in Path(f).read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(line)
            except Exception:
                continue
            for key in [d["key"]] + also.get(d["key"], []):
                b, rid = key.split("|", 1)
                searched[b].add(rid)
                if d.get("person"):
                    per[b][rid] = {**d["person"], "_inst": d["key"]}
                if d.get("phone"):
                    phones[b][rid] = d
    # one person named for several different institutes = a network / chain page, not theirs
    owners = defaultdict(set)
    for b, it in per.items():
        for rid, p in it.items():
            owners[p2.fold(p["name"])].add(p.get("_inst") or b + rid)
    phone_uses = defaultdict(set)         # one number for several different institutes = a shared switchboard / directory
    for b, it in phones.items():
        for rid, d in it.items():
            phone_uses[d["phone"]].add(d["key"])
    total = nphone = 0
    for b in lf.finished_batches():
        if b not in searched:
            continue
        try:
            results, xlsx = lf.fetch(b)
            if not results:
                continue
            st = lf.state(b)
            changed = set()
            for rid in searched[b]:
                if rid not in results or mark in st.get(rid, []):
                    continue
                p = per[b].get(rid)
                if p and len(owners[p2.fold(p["name"])]) <= 3 and lf.gaps(results[rid])["person"]:
                    c = results[rid].setdefault("contacts", {})
                    old = c.get("contact") or {}
                    c["contact"] = {**old, **{k: v for k, v in {
                        "name": p["name"], "designation": p.get("designation", ""), "source_url": p["source_url"],
                        "found_by": "free web search (named in search results)",
                        "evidence_quote": p.get("evidence_quote", "")}.items() if v and not old.get(k)}}
                    c["local_fill"] = time.strftime("%Y-%m-%d")
                    changed.add(rid)
                ph = phones[b].get(rid)
                if ph and lf.gaps(results[rid])["phone"] and len(phone_uses[ph["phone"]]) <= 3:
                    c = results[rid].setdefault("contacts", {})
                    import leads_phone_reread as rr
                    num = rr.norm(ph["phone"], "") if ph["phone"].startswith("+") else ph["phone"]
                    c["phone"], c["phone_source"] = num or ph["phone"], ph.get("phone_src", "")
                    c["local_fill"] = time.strftime("%Y-%m-%d")
                    changed.add(rid)
                    nphone += 1
                st.setdefault(rid, []).append(mark)
            if changed:
                cells, totals = lf.write_back(b, results, xlsx, changed, dry)
                print(f"{b}: {len(changed)} institutes filled ({cells} cells) -> contact {totals.get('with_contact')}/"
                      f"{totals.get('institutes')}{' [dry run]' if dry else ''}", flush=True)
                total += len(changed)
            if not dry:
                lf.save_state(b, st)
        except Exception as e:
            print(f"{b}: error {str(e)[:150]}", flush=True)
    print(f"applied: {total} institutes filled ({nphone} phones, the rest named contacts)")
    if not dry and total:
        lf.publish_dashboard()


def run(todo, out, split="0/1", limit=0):
    k, n = (int(x) for x in split.split("/"))
    done = set()
    outp = Path(out)
    if outp.exists():
        for line in outp.read_text(encoding="utf-8").splitlines():
            try:
                done.add(json.loads(line)["key"])
            except Exception:
                pass
    rows = [r for r in csv.DictReader(open(todo, encoding="utf-8-sig"))
            if zlib.crc32(r["key"].encode()) % n == k and r["key"] not in done]
    if limit:
        rows = rows[:limit]
    print(f"{time.strftime('%H:%M')} {len(rows)} institutes to search (share {split}, {len(done)} already done)", flush=True)
    got = gotp = 0
    for i, r in enumerate(rows, 1):
        try:
            res = search_one(r)
        except Exception as e:
            res = {"key": r["key"], "error": str(e)[:120]}
        got += bool(res.get("person"))
        gotp += bool(res.get("phone"))
        with open(outp, "a", encoding="utf-8") as f:
            f.write(json.dumps(res, ensure_ascii=False) + "\n")
        if i % 25 == 0:
            print(f"{time.strftime('%H:%M')} {i}/{len(rows)}: named contacts {got}, phones {gotp}", flush=True)
    print(f"{time.strftime('%H:%M')} done {len(rows)}: named contacts {got}, phones {gotp}", flush=True)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    a = sys.argv[1:]
    if a[0] == "export":
        export(a[1])
    elif a[0] == "run":
        opt = lambda f, d: a[a.index(f) + 1] if f in a else d  # noqa: E731
        run(a[1], a[2], opt("--split", "0/1"), int(opt("--limit", "0")))
    elif a[0] == "apply":
        import erasmus_local  # noqa: F401
        todo = a[a.index("--todo") + 1] if "--todo" in a else "leads_local/gap/todo_people.csv"
        files = [x for x in a[1:] if x.endswith(".jsonl")]
        apply(files, todo, "--dry-run" in a, a[a.index("--mark") + 1] if "--mark" in a else MARK)
