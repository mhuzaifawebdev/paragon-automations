"""Builds data_hei/dashboard_stats.json for the scraper page's dashboard: totals, institutes scraped per day, and
the list of batches; and data_hei/institute_counts.json: campuses and partners per institute (the Institutes view). Read straight from the saved per-institute results, so it is always consistent with them.

Run by .github/workflows/scrape_batch.yml after every chunk; safe to run by hand:  python scripts/build_dashboard_stats.py
Contains counts only - no names, emails or other contact details (the page is public).
"""
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data_hei"
OUT = DATA / "dashboard_stats.json"
OUT_INSTITUTES = DATA / "institute_counts.json"


RECHECK_PREFIX = "website-recheck-"


def _key(name):
    return " ".join(re.sub(r"[^\w]+", " ", str(name).lower()).split())


def _empty():
    return {"institutes": 0, "with_contact": 0, "with_email": 0, "personal_email": 0, "generic_email": 0,
            "contact_personal": 0, "contact_generic": 0, "with_phone": 0,
            "partners": 0, "campuses": 0, "no_website": 0, "with_website": 0, "reachable": 0, "reachable_missing": 0,
            "reachable_missing_sites": 0, "website_dead": 0}


def institute_counts(result):
    c = result.get("contacts") or {}
    net = result.get("network") or {}
    contact = c.get("contact") or {}
    personal = 1 if contact.get("email") else 0
    generic = 1 if (c.get("generic_email") or contact.get("office_email")) else 0     # the institute's info@ / office@ inbox
    no_site = 1 if str(c.get("notes") or "").startswith("Website not found") else 0
    # its website never loaded (scripts/boost_free.py) and gave nothing earlier either
    dead = 1 if c.get("site_dead") and not no_site and not contact and not (personal or generic) else 0
    # reachable: an email, or a phone number (the institute's own, or the contact's) to call instead
    phone = c.get("phone")
    phone = (phone.get("value") if isinstance(phone, dict) else phone) or contact.get("phone")
    # counted among the institutes with a working website (the share's base), so it never passes 100%
    reachable = 1 if (personal or generic or phone) and not no_site and not dead else 0
    # a contact is "personal" when the named person has their own work email, "generic" when they are reached only
    # through an office / department inbox; the rest (name with a phone, or name only) is with_contact minus both
    cp = 1 if contact and personal else 0
    cg = 1 if contact and not personal and generic else 0
    return {"reachable": reachable, "institutes": 1, "with_contact": 1 if contact else 0,
            "contact_personal": cp, "contact_generic": cg, "with_phone": 1 if phone and not no_site else 0, "with_email": 1 if personal or generic else 0,
            "personal_email": personal, "generic_email": generic,
            "partners": len(net.get("external_collaboration") or []), "campuses": len(net.get("internal_collaboration") or []),
            "no_website": no_site, "with_website": 1 - no_site - dead, "website_dead": dead}


def broken_row(result):
    """A broken row of an uploaded sheet (a phone number as the name, several institutes merged in one cell) that
    never became an institute: left out of every figure. Same rule as the re-check (recheck_no_contact.clean_name)."""
    from recheck_no_contact import clean_name
    c = result.get("contacts") or {}
    return str(c.get("notes") or "").startswith("Website not found") and not clean_name(c.get("institution_name"))


def institute_row(result, batch):
    """One line of the Institutes view: counts only, no people or addresses (the page is public)."""
    c = result.get("contacts") or {}
    n = institute_counts(result)
    line = {"name": " ".join(str(c.get("institution_name") or "").strip(" \"'").split()), "country": (c.get("country") if isinstance(c.get("country"), str) else (c.get("country") or {}).get("value")) or "",
            "type": c.get("institution_type") or "", "batch": batch, "campuses": n["campuses"], "partners": n["partners"],
            "website": n["with_website"],
            # what the row already counts on its own, so a re-check never counts it a second time
            "has": {"c": n["with_contact"], "e": n["with_email"], "r": n["reachable"], "p": n["personal_email"]}}
    if batch.startswith(RECHECK_PREFIX):
        line["found"] = {k: n[k] for k in ("with_contact", "with_email", "personal_email", "generic_email", "reachable",
                                       "contact_personal", "contact_generic", "with_phone")}
    return line


try:
    RC_BASE = set(json.loads((Path(__file__).resolve().parent.parent / "data_hei" / "_rc_missing_base.json").read_text(encoding="utf-8")))
except Exception:
    RC_BASE = set()


def apply_rechecks(totals, lines, baseline=None, sheet_contacts=None, uncounted=()):
    """A website re-check (scripts/recheck_websites.py) improves institutes already counted; it adds none. Each site it
    found moves every row of that name out of Website not found. Removes the re-check's lines from `lines`.
    baseline: {name key: 1 if it already had a general inbox, else 0} for the institutes with a website but no contact
    that were sent to be read again (recheck_contact_baseline.json): a contact found moves every row of that name, and
    an old inbox is not counted twice.
    sheet_contacts: {name key: rows}, institutes with a website where the site named nobody but the client's own sheet
    does (sheet_contacts.json). They count as contacts, unless a re-check has already counted that name."""
    rechecks = [x for x in lines if x["batch"].startswith(RECHECK_PREFIX)]
    lines[:] = [x for x in lines if not x["batch"].startswith(RECHECK_PREFIX)]
    missing, by_key = defaultdict(list), defaultdict(list)
    for x in lines:
        by_key[_key(x["name"])].append(x)
        if not x["website"]:
            missing[_key(x["name"])].append(x)
    baseline = baseline or {}
    nobody, counted = defaultdict(list), set()
    for x in lines:
        if x["website"] and _key(x["name"]) in baseline:
            nobody[_key(x["name"])].append(x)
    credited = defaultdict(set)         # name key -> what a re-check already added for it ("contact", "email")
    for r in rechecks:
        key, f = _key(r["name"]), r.get("found") or {}
        # several re-checks can find the same institute (the scraper, outside research): its contact and its email are
        # each counted once, whichever re-check found them, so an email-only find never blocks a later contact
        rows_ = nobody.get(key, [])
        lacks = lambda k: [x for x in rows_ if not (x.get("has") or {}).get(k)]  # noqa: E731
        if f.get("with_contact") and "contact" not in credited[key]:
            credited[key].add("contact")
            counted.add(key)            # the client sheet's contact for this name is not counted on top
            add = len(lacks("c"))
            totals["with_contact"] += add
            totals["contact_personal"] = totals.get("contact_personal", 0) + add * f.get("contact_personal", 0)
            totals["contact_generic"] = totals.get("contact_generic", 0) + add * f.get("contact_generic", 0)
        if f.get("personal_email") and "personal" not in credited[key]:
            credited[key].add("personal")
            totals["personal_email"] += len(lacks("p"))
        # reachable (an email or a phone): only for names that had no inbox before, so none is counted twice
        if f.get("reachable") and not baseline.get(key) and "reachable" not in credited[key]:
            credited[key].add("reachable")
            totals["reachable"] += sum(1 for x in lacks("r") if x["batch"] not in uncounted)   # its batch is in the share
        if f.get("with_email") and not baseline.get(key) and "email" not in credited[key]:
            credited[key].add("email")
            add = len(lacks("e"))
            totals["with_email"] += add
            totals["generic_email"] += add if f.get("generic_email") else 0
        rows = missing.pop(_key(r["name"]), []) if r["website"] else []
        if r["website"] and not rows:
            # rows that had no website last night and got one from the local gap fill (_rc_missing_base.json) are no
            # longer "missing": the contact / partners / campuses the re-check read for them still count, once each.
            # Emails are not credited here: the row's own email (often the same one) already counts.
            for x in by_key.get(key, []):
                if not x["website"] or x.get("_rc") or f"{x['batch']}|{x['name']}" not in RC_BASE:
                    continue
                x["_rc"] = 1
                totals["partners"] += r["partners"] if not x.get("partners") else 0
                totals["campuses"] += r["campuses"] if not x.get("campuses") else 0
                if r["found"].get("with_contact"):
                    totals["with_contact"] += 1
                    totals["contact_personal"] = totals.get("contact_personal", 0) + r["found"].get("contact_personal", 0)
                    totals["contact_generic"] = totals.get("contact_generic", 0) + r["found"].get("contact_generic", 0)
        for x in rows:
            x.update(website=1, type=r["type"], country=r["country"], campuses=r["campuses"], partners=r["partners"])
            totals["no_website"] -= 1
            totals["with_website"] += 1
            totals["partners"] += r["partners"]
            totals["campuses"] += r["campuses"]
            for k, v in r["found"].items():
                totals[k] = totals.get(k, 0) + v
    for key, n in (sheet_contacts or {}).items():
        if key not in counted:
            # [rows, with the person's own email, office email only] (scripts/build_sheet_contacts.py); a plain
            # number is the older form, rows only
            n, own, office = (n + [0, 0])[:3] if isinstance(n, list) else (n, 0, 0)
            totals["with_contact"] += n
            totals["contact_personal"] = totals.get("contact_personal", 0) + own
            totals["contact_generic"] = totals.get("contact_generic", 0) + office


def batch_figures(bdir):
    """(counts, {day: counts}, Institutes view lines) of one batch, from its per-institute results; for a finished
    batch whose working files were archived to Drive (scripts/queue_tick.py), from its summary.json."""
    bdir = Path(bdir)
    summary = bdir / "summary.json"
    if summary.exists() and not (bdir / "scrape_results").exists():
        try:
            s = json.loads(summary.read_text(encoding="utf-8"))
            t = {**_empty(), **s.get("totals", {})}
            if "reachable" not in s.get("totals", {}):      # counted before this figure existed: not known yet
                t["reachable_missing"] = t["institutes"]
                t["reachable_missing_sites"] = t["with_website"]     # kept out of the Reachable share until counted
            return t, s.get("days", {}), s.get("institutes", [])
        except Exception:
            pass
    b, days, lines = _empty(), defaultdict(_empty), []
    done = list((bdir / "scrape_results").glob("r*.json"))
    names = {f.name for f in done}
    # an institute set aside by a re-check (scripts/recheck_no_contact.py) still counts, with its old result
    waiting = [f for f in (bdir / "scrape_results_prev").glob("r*.json") if f.name not in names]
    for f in done + waiting:
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if broken_row(r):
            continue
        n = institute_counts(r)
        lines.append(institute_row(r, bdir.name))
        day = str(r.get("scraped_at") or "")[:10]
        for k, v in n.items():
            b[k] += v
            if day:
                days[day][k] += v
    return b, dict(days), lines


def hidden_batches(data=DATA):
    """replaced_batches.json {old batch: its rebuild}: a batch imported wrongly and scraped again. Until the rebuild
    has finished the old batch still counts and the rebuild does not; after that only the rebuild counts, so the
    institutes are never counted twice and the totals never dip."""
    try:
        pairs = json.loads((Path(data) / "replaced_batches.json").read_text(encoding="utf-8"))
    except Exception:
        return set()
    hidden = set()
    for old, new in pairs.items():
        nd = Path(data) / new
        try:
            finished = (nd / "summary.json").exists() or json.loads((nd / "progress.json").read_text(encoding="utf-8")).get("status") == "done"
        except Exception:
            finished = False
        hidden.add(old if finished else new)
    return hidden


def build(data=DATA, rows=None):
    """rows: if a list is given, the Institutes view lines are appended to it."""
    totals, by_day, batches, lines_all = _empty(), defaultdict(_empty), [], []
    uncounted = set()                   # batches counted before the Reachable figure existed
    hidden = hidden_batches(data)
    for bdir in sorted(p for p in Path(data).iterdir() if p.is_dir() and p.name != "uploads" and not p.name.startswith("_")):
        b, days, lines = batch_figures(bdir)
        if bdir.name in hidden:
            b, days, lines = _empty(), {}, []
        lines_all.extend(lines)
        if b.get("reachable_missing"):
            uncounted.add(bdir.name)
        if not bdir.name.startswith(RECHECK_PREFIX):
            for k, v in b.items():
                totals[k] += v
            for day, counts in days.items():
                for k, v in counts.items():
                    by_day[day][k] += v
        prog = {}
        if (bdir / "progress.json").exists():
            try:
                prog = json.loads((bdir / "progress.json").read_text(encoding="utf-8"))
            except Exception:
                prog = {}
        if not b["institutes"] and not prog:
            continue
        batches.append({"batch": bdir.name, "status": prog.get("status") or ("done" if b["institutes"] else "unknown"),
                        "total_rows": prog.get("total_rows") or b["institutes"], "done": prog.get("done", b["institutes"]),
                        "updated": prog.get("generated_at") or "", **b})
    try:
        baseline = json.loads((Path(data) / "recheck_contact_baseline.json").read_text(encoding="utf-8"))
    except Exception:
        baseline = {}
    try:
        sheet_contacts = json.loads((Path(data) / "sheet_contacts.json").read_text(encoding="utf-8"))
    except Exception:
        sheet_contacts = {}
    apply_rechecks(totals, lines_all, baseline, sheet_contacts, uncounted)
    if rows is not None:
        rows.extend(lines_all)
    batches.sort(key=lambda x: x["updated"] or "", reverse=True)
    return {"generated_at": datetime.now(timezone.utc).isoformat(), "totals": {**totals, "batches": len(batches)},
            "days": [{"day": d, **v} for d, v in sorted(by_day.items())], "batches": batches}


def _write_if_changed(path, obj, indent=1):
    text = json.dumps(obj, indent=indent, ensure_ascii=False, separators=None if indent else (",", ":")) + "\n"
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    # generated_at changes every run; only rewrite when the numbers changed, so no empty commits
    strip = lambda t: json.dumps({k: v for k, v in json.loads(t).items() if k != "generated_at"}, sort_keys=True) if t else ""
    if strip(old) != strip(text):
        path.write_text(text, encoding="utf-8")


def main():
    rows = []
    stats = build(rows=rows)
    _write_if_changed(OUT, stats)
    rows.sort(key=lambda x: (x["name"].lower(), x["batch"]))
    _write_if_changed(OUT_INSTITUTES, {"generated_at": stats["generated_at"], "institutes": rows}, indent=None)
    print(f"{stats['totals']['institutes']} institutes, {len(stats['days'])} days, {len(stats['batches'])} batches")


if __name__ == "__main__":
    main()
