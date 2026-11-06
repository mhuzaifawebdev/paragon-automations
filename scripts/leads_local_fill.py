"""Fills the gaps of every finished leads batch on THIS computer, for free, straight into the batch's own files:
"<batch> results.xlsx" in Drive/Results (empty cells only, nothing already there is changed), "<batch> data.json.gz"
in Drive/_archive, and data_hei/<batch>/summary.json (the dashboard figures).

Gaps: no website, no email (personal or general), no phone, no named contact. The same free steps as the Erasmus+
second pass (scripts/erasmus_pass2.py), no paid AI, no paid search:
  1. ministry school directories (Italy, France, Catalonia): the school's official email / phone / website, kept only
     when exactly one school of that name in a town named in the institute's name fits
  2. contact-page sweep of the website: hidden emails decoded, the official address of the site's own domain, tel: phones
  3. the scraper's rules on the site for a named contact (kept only when the name looks like a person and their own
     email matches the name)
  4. (phase 2, slow) free DuckDuckGo search for institutes without a website, one search every 8 s; a found site
     must show the name, then steps 2-3 run on it

    python scripts/leads_local_fill.py                    # phase 1 on every batch, then phase 2
    python scripts/leads_local_fill.py --batch batch-1-3230 --dry-run   # one batch, nothing uploaded
    python scripts/leads_local_fill.py --phase 1

Resumable: what each institute went through is kept in leads_local/<batch>/state.json.
"""
import argparse
import gzip
import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import erasmus_pass2 as p2  # noqa: E402  (also removes every paid key from this process)

os.environ["PARAGON_DATA"] = str(ROOT / "leads_local")
os.environ.setdefault("DRIVE_FOLDER_ID", "1MtxgKG_4zVEHCEgDPRFOinuR0hSTAoBe")   # 00_CLEAN_SCRAPER_DATABASE (robot's shared drive)
DATA = ROOT / "data_hei"
WORK = ROOT / "leads_local"
CC = {"italy": "IT", "france": "FR", "spain": "ES"}
_drive = {}
_dlock = threading.Lock()


def drive():
    import drive_upload as d
    with _dlock:
        if "svc" not in _drive:
            _drive["svc"] = d.robot_service()
            _drive["arch"] = d.subfolder(_drive["svc"], "_archive")
            _drive["res"] = d.subfolder(_drive["svc"], "Results")
    return d, _drive["svc"]


def finished_batches(only=None):
    out = []
    for b in sorted(p for p in DATA.iterdir() if p.is_dir() and (p / "summary.json").exists()
                    and not (p / "scrape_results").exists()):
        if only and b.name != only:
            continue
        out.append(b.name)
    return out


def fetch(batch):
    """(results dict, local xlsx path) from Drive, or (None, None) when the batch has no archive."""
    with _dlock:
        _drive.clear()
    d, svc = drive()
    wd = WORK / batch
    wd.mkdir(parents=True, exist_ok=True)
    with _dlock:
        raw = d.get_bytes(svc, f"{batch} data.json.gz", _drive["arch"])
        f = d._find(svc, f"{batch} results.xlsx", parent=_drive["res"])
        xl = svc.files().get_media(fileId=f["id"], supportsAllDrives=True).execute() if f else None
    if not raw or not xl:
        return None, None
    (wd / "results.xlsx").write_bytes(xl)
    return json.loads(gzip.decompress(raw).decode("utf-8")), wd / "results.xlsx"


def state(batch):
    try:
        return json.loads((WORK / batch / "state.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(batch, st):
    (WORK / batch / "state.json").write_text(json.dumps(st), encoding="utf-8")


# ------------------------------------------------------------------------------------- what an institute lacks
def phone_of(c):
    ph = c.get("phone")
    return (ph.get("value") if isinstance(ph, dict) else ph) or (c.get("contact") or {}).get("phone") or ""


def gaps(r):
    c = r.get("contacts") or {}
    p = c.get("contact") or {}
    return {"site": not c.get("official_website"), "email": not (p.get("email") or c.get("generic_email") or p.get("office_email")),
            "phone": not phone_of(c), "person": not p.get("name")}


# ------------------------------------------------------------------------------------- step 1: ministry directories
_reg = {}


def registry_index():
    if "idx" not in _reg:
        idx = {}
        for r in p2.registry_rows():
            tw = tuple(p2.fold(r[1]).split())
            if tw:
                idx.setdefault((r[0], tw), []).append((r, p2.toks(r[2]), p2.toks(r[1])))
        _reg["idx"] = idx
    return _reg["idx"]


def registry_match(name, country):
    cc = CC.get(p2.fold(country))
    if not cc:
        return None
    idx = registry_index()
    words = p2.fold(name).split()
    towns = {tuple(words[i:i + n]) for n in (1, 2, 3, 4) for i in range(len(words) - n + 1)}
    cands = [x for t in towns if len(" ".join(t)) > 3 for x in idx.get((cc, t), [])]
    if not cands:
        return None
    want = p2.toks(name)
    hits = []
    for r, rname, rtown in cands:
        core = [t for t in want if t not in set(rtown)]
        reg_core = [t for t in rname if t not in set(rtown)]
        have = set(rname + rtown)
        if core and all(t in have for t in core) and all(t in set(want) for t in reg_core):
            hits.append(r)
    if len({h[3] for h in hits}) != 1:
        return None
    web = hits[0][6].strip()
    return {"email": next((h[4] for h in hits if h[4]), "").lower(), "phone": next((h[5] for h in hits if h[5]), ""),
            "website": (web if web.startswith("http") or not web else "https://" + web), "source": hits[0][7]}


# ------------------------------------------------------------------------------------- one institute
def fill_one(rid, r, steps, people=True):
    """Fills r["contacts"] in place, blanks only. Returns the list of what was added."""
    c = r.setdefault("contacts", {})
    added = []
    g = gaps(r)
    name, country = c.get("institution_name") or "", c.get("country") or r.get("_country") or ""
    if "registry" not in steps and (g["site"] or g["email"] or g["phone"]):
        m = registry_match(name, country)
        if m:
            if g["email"] and m["email"]:
                c.update(generic_email=m["email"], generic_email_url=m["source"])
                added.append("email")
            if g["phone"] and m["phone"]:
                c["phone"] = m["phone"]
                c["phone_source"] = m["source"]
                added.append("phone")
            if g["site"] and m["website"]:
                c["official_website"] = m["website"]
                c["notes"] = "Website from the education ministry's school directory (local fill)"
                added.append("website")
        steps.append("registry")
    g = gaps(r)
    site = c.get("official_website") or ""
    if site and "sweep" not in steps and (g["email"] or g["phone"]):
        got = None
        try:
            got = p2.sweep_site(site if site.startswith("http") else "https://" + site)
        except Exception:
            pass
        if got:
            email, src, phone, psrc = got
            if g["email"] and email:
                c.update(generic_email=email, generic_email_url=src)
                added.append("email")
            if g["phone"] and phone:
                c["phone"] = phone
                c["phone_source"] = psrc
                added.append("phone")
        steps.append("sweep")
    g = gaps(r)
    if people and site and g["person"] and "people" not in steps:
        try:
            import erasmus_local as el
            import import_erasmus_ai as imp
            import pipeline
            res = pipeline.scrape({"row_id": rid, "institution_name": name, "country_sheet": country, "phone_1": "",
                                   "contact_name": "", "email": "", "seed_urls": site, "website_override": site},
                                  dict(el.free_cfg()))
            p = (res.get("contacts") or {}).get("contact") or {}
            pn, role = imp.local_person(p) if p else ("", "")
            if pn:
                c["contact"] = {**p, "designation": role, "found_by": "local fill (rules)"}
                added.append("person")
            rc = res.get("contacts") or {}
            if gaps(r)["email"] and rc.get("generic_email"):
                c.update(generic_email=rc["generic_email"], generic_email_url=rc.get("generic_email_url", ""))
                added.append("email")
        except Exception:
            pass
        steps.append("people")
    if added:
        c["local_fill"] = time.strftime("%Y-%m-%d")
    return added


# ------------------------------------------------------------------------------------- write back
COLS = {"Contact Person": lambda c: (c.get("contact") or {}).get("name"),
        "Designation": lambda c: (c.get("contact") or {}).get("designation"),
        "Email (personal if found)": lambda c: (c.get("contact") or {}).get("email"),
        "Department Email (general)": lambda c: c.get("generic_email") or (c.get("contact") or {}).get("office_email"),
        "Phone": phone_of,
        "Website": lambda c: c.get("official_website"),
        "Contact Source": lambda c: (c.get("contact") or {}).get("source_url"),
        "Contact Evidence Quote": lambda c: (c.get("contact") or {}).get("evidence_quote"),
        "Contact's Direct Phone": lambda c: (c.get("contact") or {}).get("phone")}


def patch_excel(xlsx, results, changed):
    import openpyxl
    wb = openpyxl.load_workbook(xlsx)
    ws = wb["Institutions"]
    head = {cell.value: i for i, cell in enumerate(ws[1], start=1)}
    rid_col = head.get("Row ID")
    n = 0
    for row in ws.iter_rows(min_row=2):
        rid = row[rid_col - 1].value if rid_col else None
        if rid not in changed:
            continue
        c = results[rid].get("contacts") or {}
        for col, fn in COLS.items():
            if col in head:
                cell = row[head[col] - 1]
                v = fn(c)
                if v and not (cell.value or "").__str__().strip():
                    cell.value = v
                    n += 1
    wb.save(xlsx)
    return n


def write_back(batch, results, xlsx, changed, dry):
    import build_dashboard_stats as bds
    import reuse
    cells = patch_excel(xlsx, results, changed)
    sr = WORK / batch / "scrape_results"
    sr.mkdir(parents=True, exist_ok=True)
    for rid, r in results.items():
        (sr / f"{rid}.json").write_text(json.dumps(r, ensure_ascii=False), encoding="utf-8")
    totals, days, lines = bds.batch_figures(WORK / batch)
    if dry:
        return cells, totals
    for i in range(4):                     # a Drive connection left idle during the scraping is often dropped
        try:
            with _dlock:
                _drive.clear()
            d, svc = drive()
            with _dlock:
                d.put_file(svc, xlsx, f"{batch} results.xlsx", _drive["res"])
                d.put_bytes(svc, reuse.archive_bytes(results), f"{batch} data.json.gz", _drive["arch"],
                            mime="application/gzip")
            break
        except Exception:
            if i == 3:
                raise
            time.sleep(10 * (i + 1))
    sp = DATA / batch / "summary.json"
    s = json.loads(sp.read_text(encoding="utf-8"))
    s.update(totals=totals, days=days, institutes=lines, local_fill=time.strftime("%Y-%m-%d %H:%M"))
    sp.write_text(json.dumps(s, ensure_ascii=False), encoding="utf-8")
    return cells, totals


# ------------------------------------------------------------------------------------- phases
def run_batch(batch, phase, workers, dry):
    results, xlsx = fetch(batch)
    if not results:
        print(f"{batch}: no archive in Drive, skipped", flush=True)
        return 0
    import openpyxl
    wb = openpyxl.load_workbook(xlsx, read_only=True)
    ws = wb["Institutions"]
    it = ws.iter_rows(values_only=True)
    head = list(next(it))
    ci, ri, di = head.index("Country") if "Country" in head else None, head.index("Row ID"), \
        head.index("Duplicate Of") if "Duplicate Of" in head else None
    dup = set()
    for row in it:
        rid = row[ri]
        if rid in results and ci is not None and row[ci]:
            results[rid]["_country"] = row[ci]
        if di is not None and row[di]:
            dup.add(rid)
    wb.close()
    st = state(batch)
    before = {rid: gaps(r) for rid, r in results.items()}
    todo = [rid for rid, g in before.items() if any(g.values()) and rid not in dup]
    if phase == 2:                                 # search: only institutes still without a website
        todo = [rid for rid in todo if before[rid]["site"] and "search" not in st.get(rid, [])]
    changed = set()
    lock = threading.Lock()

    def one(rid):
        steps = st.setdefault(rid, [])
        r = results[rid]
        if phase == 2:
            c = r["contacts"]
            row = {"name": c.get("institution_name") or "", "city": "", "cc": "", "pic": rid}
            site = search_site(row, r.get("_country") or "")
            steps.append("search")
            if site:
                c["official_website"] = site
                c["notes"] = "Website found by free search (local fill)"
                for s in ("sweep", "people"):
                    if s in steps:
                        steps.remove(s)
                added = ["website"] + fill_one(rid, r, steps)
            else:
                added = []
        else:
            added = fill_one(rid, r, steps)
        if added:
            with lock:
                changed.add(rid)

    with ThreadPoolExecutor(workers) as pool:
        list(pool.map(one, todo))
    for r in results.values():
        r.pop("_country", None)
    save_state(batch, st)
    if not changed:
        print(f"{batch}: {len(todo)} with gaps checked, nothing new", flush=True)
        return 0
    cells, totals = write_back(batch, results, xlsx, changed, dry)
    print(f"{batch}: {len(todo)} with gaps checked, {len(changed)} institutes filled ({cells} cells){' [dry run]' if dry else ''}"
          f" -> email {totals.get('with_email')}/{totals.get('institutes')}, contact {totals.get('with_contact')}",
          flush=True)
    return len(changed)


def search_site(row, country):
    """A website for the institute from the free search, checked like the Erasmus+ pass (stricter than the cloud
    runs: the address holds the name, or the homepage shows two of the name's own words)."""
    import discover
    name = row["name"]
    check_row = {"name": name, "country": country, "phone": "", "email": ""}
    for q in (f'"{name}" {country}', f"{name} {country} contact"):
        res = None
        while res is None:
            res = p2.ddg(q)
        for u in res[:5]:
            h = p2.host(u)
            if any(s in h for s in p2.SKIP) or re.match(r"(mail|webmail|buzon|correo|intranet|moodle|campus|aula|login|sso|portal)\.", h):
                continue
            root = "https://" + re.sub(r"^https?://", "", u).split("/")[0] + "/"
            try:
                ok, why, rec = discover.check_site(root, check_row)
            except Exception:
                ok, rec = False, None
            if not ok:
                continue
            core = p2.toks(name)
            page = p2.fold((rec or {}).get("text", ""))
            words = sum(1 for t in core if f" {t} " in f" {page} ")
            if discover.name_in_domain(root, name) or (core and words >= min(2, len(core))):
                return root
    return ""


def publish_dashboard():
    subprocess.run([sys.executable, "scripts/build_dashboard_stats.py"], cwd=ROOT, check=False)
    subprocess.run(["git", "add", "data_hei/*/summary.json", "web_scraper/"], cwd=ROOT, check=False)
    r = subprocess.run(["git", "commit", "-q", "-m", "leads: gaps filled locally (free), batch figures updated [automated]"],
                       cwd=ROOT, check=False)
    if r.returncode == 0:
        subprocess.run(["git", "pull", "--rebase", "--autostash", "-q"], cwd=ROOT, check=False)
        subprocess.run(["git", "push", "-q"], cwd=ROOT, check=False)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--batch")
    ap.add_argument("--phase", type=int, choices=[1, 2])
    ap.add_argument("--workers", type=int, default=24)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    import erasmus_local  # noqa: F401  (the free scraper settings)
    import pipeline
    pipeline.LLM_SLOTS = threading.Semaphore(1)
    batches = finished_batches(a.batch)
    print(f"{len(batches)} finished batches. Free: no paid service is used.", flush=True)
    for phase in ([a.phase] if a.phase else [1, 2]):
        print(f"\n== phase {phase} ({time.strftime('%H:%M')})", flush=True)
        filled = 0
        for i, b in enumerate(batches, 1):
            try:
                filled += run_batch(b, phase, a.workers if phase == 1 else 4, a.dry_run)
            except Exception as e:
                print(f"{b}: error {str(e)[:200]}", flush=True)
            if not a.dry_run and i % 15 == 0:
                publish_dashboard()
        print(f"phase {phase}: {filled} institutes filled", flush=True)
        if not a.dry_run:
            publish_dashboard()


if __name__ == "__main__":
    main()
