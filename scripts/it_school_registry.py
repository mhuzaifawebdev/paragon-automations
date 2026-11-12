"""Italian schools: official email, certified email (PEC) and website from the Ministry of Education's open register
(dati.istruzione.it, state + private schools), matched by name inside the province the phone's area code points to.

  python scripts/it_school_registry.py match <batch> [<batch> ...]      -> leads_local/it_registry/<batch>.matches.json
Register files (free download, see README in leads_local/it_registry): SCUANAGRAFESTAT*.csv, SCUANAGRAFEPAR*.csv;
area codes: Prefissi_telefonici_italiani.txt (it.wikipedia raw text).
"""
import csv
import json
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REG = ROOT / "leads_local" / "it_registry"

STOP = set("""I IC ICS C S IS IIS ISS ISIS IPSIA IPSSAR IPSSEOA IPSEOA IPC IPSC ITC ITCG ITG ITI ITIS ITT ITE ITET IST ISTIT
ISTITUTO ISTITUTI COMPRENSIVO COMPRENSIVA STATALE STATALI SCUOLA SCUOLE SECONDARIA PRIMARIA INFANZIA PRIMO SECONDO GRADO
DI DEL DELLA DELLE DEI DEGLI DELL D DA E ED IN AL ALLA A LA LE LO IL GLI L SAN SANTA SANTO S ST N NR NO DE DI
LICEO LICEI LS LC LA LSS CLASSICO SCIENTIFICO LINGUISTICO ARTISTICO MAGISTRALE MUSICALE SCIENZE UMANE TECNICO
PROFESSIONALE SUPERIORE SUPERIORI ISTRUZIONE CONVITTO NAZIONALE EDUCANDATO CPIA CENTRO PROVINCIALE ADULTI
POLO OMNICOMPRENSIVO DIREZIONE DIDATTICA CIRCOLO DD SMS SM TECNOLOGICO ECONOMICO LINGUA SLOVENA PARITARIA
PROF DOTT DON PADRE""".split())


def norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().upper()
    return re.sub(r"[^A-Z0-9]+", " ", s).strip()


def tokens(s):
    return {t for t in norm(s).split() if len(t) > 1 and t not in STOP and not t.isdigit()}


def area_provinces():
    """'049' -> {'PD'}: the area code's province sigle; rows without one inherit the row above (rowspan)."""
    out, last = {}, set()
    for line in (REG / "Prefissi_telefonici_italiani.txt").read_text(encoding="utf-8").splitlines():
        m = re.match(r"\|\s*align=center\s*\|\s*\[\[(?:[^|\]]*\|)?(0\d{1,3})", line)
        if not m:
            continue
        sig = set(re.findall(r"\b([A-Z]{2})\b", re.sub(r"\[\[[^\]]*\]\]", " ", line)))
        sig |= set(re.findall(r"\(([A-Z]{2})\)", line))
        sig = {s for s in sig if s not in {"NO", "SI"}}
        last = sig or last
        out[m.group(1)] = set(last)
    return out


def area_code(phone):
    d = re.sub(r"\D", "", str(phone or "").replace(".0", ""))
    if d.startswith("0039"):
        d = d[4:]
    elif d.startswith("39") and len(d) >= 8:
        d = d[2:]
    if d.startswith("3"):            # a mobile number: no place
        return ""
    if not d.startswith("0"):
        d = "0" + d
    return d


def load_register():
    """One entry per institute (the school group with one office): name parts, towns, province, contacts."""
    inst = {}
    files = sorted(REG.glob("SCUANAGRAFESTAT2026*.csv")) + sorted(REG.glob("SCUANAGRAFESTAT2025*.csv")) + sorted(REG.glob("SCUANAGRAFEPAR*.csv"))
    for f in files:
        for r in csv.DictReader(open(f, encoding="utf-8-sig", errors="replace")):
            code = r.get("CODICEISTITUTORIFERIMENTO") or r.get("CODICESCUOLA")
            if not code:
                continue
            e = inst.setdefault(code, {"code": code, "sig": code[:2], "names": set(), "towns": set(), "email": "", "pec": "",
                                       "web": "", "label": "", "paritaria": "PAR" in f.name})
            if not e["label"]:
                e["label"] = r.get("DENOMINAZIONEISTITUTORIFERIMENTO") or r.get("DENOMINAZIONESCUOLA") or ""
            e["names"] |= tokens(r.get("DENOMINAZIONEISTITUTORIFERIMENTO")) | tokens(r.get("DENOMINAZIONESCUOLA"))
            e["towns"] |= tokens(r.get("DESCRIZIONECOMUNE"))
            head = (r.get("INDICAZIONESEDEDIRETTIVO") or "").upper() == "SI" or r.get("CODICESCUOLA") == code
            for k, col in (("email", "INDIRIZZOEMAILSCUOLA"), ("pec", "INDIRIZZOPECSCUOLA"), ("web", "SITOWEBSCUOLA")):
                v = (r.get(col) or "").strip()
                if v and "non disponibile" not in v.lower() and (head or not e[k]):
                    e[k] = v
    for e in inst.values():
        if not e["email"] and not e["paritaria"]:
            e["email"] = e["code"].lower() + "@istruzione.it"
        if e["web"] and not e["web"].lower().startswith("http"):
            e["web"] = "https://" + e["web"].strip("/")
        e["web"] = re.sub(r"^https?//", "https://", e["web"])
    return inst


def best_match(name, phone, by_sig, prov):
    want = tokens(name)
    if not want:
        return None
    ac = area_code(phone)
    sigs = set()
    for n in (4, 3, 2):
        if ac[:n] in prov:
            sigs = prov[ac[:n]]
            break
    pool = [e for s in sigs for e in by_sig.get(s, [])]
    scored = []
    for e in pool:
        hit = want & (e["names"] | e["towns"])
        if not hit:
            continue
        cover = len(hit) / len(want)                                  # how much of our name the entry explains
        lab = tokens(e["label"])
        precise = len(want & lab) / max(1, len(lab))                  # how much of the entry's own name is ours
        both = 0.25 if want & e["towns"] and want & e["names"] else 0
        scored.append(((round(cover + both, 3), round(precise, 3), not e["paritaria"]), e))
    scored.sort(key=lambda x: x[0], reverse=True)
    if not scored:
        return None
    top = scored[0]
    if top[0][0] < 0.99:                                # every distinctive word of our name must be in the register entry
        return None
    if len(scored) > 1 and scored[1][0] == top[0] and scored[1][1]["code"] != top[1]["code"]:
        return {"ambiguous": [s[1]["label"] for s in scored[:4]], "province": sorted(sigs)}
    e = top[1]
    return {"code": e["code"], "label": e["label"], "province": e["sig"], "email": e["email"], "pec": e["pec"], "web": e["web"],
            "score": top[0][0]}


def match(batches):
    prov = area_provinces()
    reg = load_register()
    by_sig = defaultdict(list)
    for e in reg.values():
        by_sig[e["sig"]].append(e)
    print(f"register: {len(reg)} institutes, area codes: {len(prov)}")
    for b in batches:
        rows = list(csv.DictReader(open(ROOT / "data_hei" / b / "numbers_clean.csv", encoding="utf-8-sig")))
        out, stats = {}, defaultdict(int)
        for r in rows:
            m = best_match(r["institution_name"], r.get("phone_1") or r.get("phone_2"), by_sig, prov)
            if not m:
                stats["no match"] += 1
            elif "ambiguous" in m:
                stats["ambiguous"] += 1
            else:
                stats["matched"] += 1
                stats["with website"] += bool(m["web"])
                out[r["row_id"]] = m
        (REG / f"{b}.matches.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
        print(b, len(rows), dict(stats))


def _has_email(res):
    c = (res or {}).get("contacts") or {}
    return "@" in json.dumps({k: c.get(k) for k in ("generic_email", "contact", "alternates")})


def apply(batches):
    """Register website -> the batch's websites.csv (later rows win); a matched school whose result has no email is
    cleared so the next run reads its real website."""
    from datetime import date
    for b in batches:
        bdir = ROOT / "data_hei" / b
        m = json.loads((REG / f"{b}.matches.json").read_text(encoding="utf-8"))
        memo = bdir / "websites.csv"
        new = not memo.exists()
        cleared = 0
        with open(memo, "a", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["row_id", "url", "how", "saved_at"])
            for rid, e in m.items():
                if e.get("web"):
                    w.writerow([rid, e["web"], "Italian school register (dati.istruzione.it)", date.today().isoformat()])
                rf = bdir / "scrape_results" / f"{rid}.json"
                res = json.loads(rf.read_text(encoding="utf-8")) if rf.exists() else None
                if rf.exists() and not _has_email(res) and e.get("web"):
                    rf.unlink()
                    cleared += 1
        print(f"{b}: {len(m)} register websites saved, {cleared} schools cleared for a new read")


def inject(batches):
    """After the new read: a matched school with no email gets the register's official address (and PEC in notes)."""
    for b in batches:
        bdir = ROOT / "data_hei" / b
        m = json.loads((REG / f"{b}.matches.json").read_text(encoding="utf-8"))
        added = 0
        for rid, e in m.items():
            rf = bdir / "scrape_results" / f"{rid}.json"
            if not rf.exists() or not e.get("email"):
                continue
            res = json.loads(rf.read_text(encoding="utf-8"))
            c = res.setdefault("contacts", {})
            if not c.get("generic_email"):
                c["generic_email"] = e["email"]
                c["generic_email_url"] = "https://dati.istruzione.it (official school register)"
                if not c.get("official_website") and e.get("web"):
                    c["official_website"] = e["web"]
                c["notes"] = (str(c.get("notes") or "") + f" Register: {e['label']} ({e['code']}), PEC {e.get('pec') or '-'}.").strip()
                rf.write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
                added += 1
        print(f"{b}: register email added to {added} schools")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 2 else ""
    {"match": match, "apply": apply, "inject": inject}.get(cmd, lambda _: print(__doc__))(sys.argv[2:])
