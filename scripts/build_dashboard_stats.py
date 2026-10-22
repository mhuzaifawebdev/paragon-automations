"""Builds data_hei/dashboard_stats.json for the scraper page's dashboard: totals, institutes scraped per day, and
the list of batches; and data_hei/institute_counts.json: campuses and partners per institute (the Institutes view). Read straight from the saved per-institute results, so it is always consistent with them.

Run by .github/workflows/scrape_batch.yml after every chunk; safe to run by hand:  python scripts/build_dashboard_stats.py
Contains counts only - no names, emails or other contact details (the page is public).
"""
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data_hei"
OUT = DATA / "dashboard_stats.json"
OUT_INSTITUTES = DATA / "institute_counts.json"


def _empty():
    return {"institutes": 0, "with_contact": 0, "with_email": 0, "personal_email": 0, "generic_email": 0,
            "partners": 0, "campuses": 0, "no_website": 0, "with_website": 0}


def institute_counts(result):
    c = result.get("contacts") or {}
    net = result.get("network") or {}
    contact = c.get("contact") or {}
    personal = 1 if contact.get("email") else 0
    generic = 1 if (c.get("generic_email") or contact.get("office_email")) else 0     # the institute's info@ / office@ inbox
    no_site = 1 if str(c.get("notes") or "").startswith("Website not found") else 0
    return {"institutes": 1, "with_contact": 1 if contact else 0, "with_email": 1 if personal or generic else 0,
            "personal_email": personal, "generic_email": generic,
            "partners": len(net.get("external_collaboration") or []), "campuses": len(net.get("internal_collaboration") or []),
            "no_website": no_site, "with_website": 1 - no_site}


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
    return {"name": " ".join(str(c.get("institution_name") or "").strip(" \"'").split()), "country": (c.get("country") or {}).get("value") or "",
            "type": c.get("institution_type") or "", "batch": batch, "campuses": n["campuses"], "partners": n["partners"],
            "website": n["with_website"]}


def build(data=DATA, rows=None):
    """rows: if a list is given, the Institutes view lines are appended to it."""
    totals, by_day, batches = _empty(), defaultdict(_empty), []
    for bdir in sorted(p for p in Path(data).iterdir() if p.is_dir() and p.name != "uploads"):
        b = _empty()
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
            if rows is not None:
                rows.append(institute_row(r, bdir.name))
            day = str(r.get("scraped_at") or "")[:10]
            for k, v in n.items():
                totals[k] += v
                b[k] += v
                if day:
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
