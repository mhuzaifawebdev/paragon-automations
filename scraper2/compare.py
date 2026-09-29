"""Head-to-head of two scraper runs on the same institutes, judged by the SAME deterministic verifier.

    python scraper2/compare.py data/scrape_results_v1 data/scrape_results r007,r008,r011,r018,r019,r020,r021,r022

Counts only what the verifier confirms against the cited page (verify.py), plus time and cost recorded
in each result. Nothing here is a model's opinion of its own work.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scraper"))
import verify  # noqa: E402


def stats(path):
    d = json.loads(path.read_text(encoding="utf-8"))
    c, n, t = d.get("contacts") or {}, d.get("network") or {}, d.get("timing") or {}
    site = c.get("official_website") or ""
    ct = c.get("contact") or {}
    contact_ok = verify.verify_contact(ct, site)[0] if ct.get("name") else None
    partners = n.get("external_collaboration") or []
    campuses = n.get("internal_collaboration") or []
    p_ok = sum(1 for p in partners if verify.verify_partner_or_campus(p, site, "partner_name")[0])
    m_ok = sum(1 for m in campuses if verify.verify_partner_or_campus(m, site, "campus_name")[0])
    if "total_seconds" in t and "contacts" not in t:                      # v2 timing
        secs, cost = t.get("total_seconds", 0), t.get("cost_usd_estimate", 0)
    else:                                                                  # v1 timing (two agent passes)
        secs = t.get("total_seconds") or sum((t.get(k) or {}).get("seconds", 0) for k in ("contacts", "network"))
        cost = sum((t.get(k) or {}).get("cost_usd", 0) for k in ("contacts", "network"))
    return {"contact": ct.get("name") or "-", "contact_ok": contact_ok, "email": bool(ct.get("email")),
            "partners": (p_ok, len(partners)), "campuses": (m_ok, len(campuses)), "secs": secs, "cost": cost}


def main():
    a_dir, b_dir, rows = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3].split(",")
    tot = {"a": {"contacts": 0, "ok": 0, "p": 0, "m": 0, "secs": 0, "cost": 0}, "b": {"contacts": 0, "ok": 0, "p": 0, "m": 0, "secs": 0, "cost": 0}}
    print(f"{'row':5} | {'A: ' + a_dir.name:58} | {'B: ' + b_dir.name:58}")
    for rid in rows:
        line = []
        for key, d in (("a", a_dir), ("b", b_dir)):
            f = d / f"{rid}.json"
            if not f.exists():
                line.append(f"{'(no result)':58}")
                continue
            s = stats(f)
            t = tot[key]
            t["contacts"] += 1 if s["contact"] != "-" else 0
            t["ok"] += 1 if s["contact_ok"] else 0
            t["p"] += s["partners"][0]
            t["m"] += s["campuses"][0]
            t["secs"] += s["secs"]
            t["cost"] += s["cost"]
            mark = {True: "confirmed", False: "NOT confirmed", None: "-"}[s["contact_ok"]]
            line.append(f"{s['contact'][:22]:22} {mark:13} P{s['partners'][0]}/{s['partners'][1]} C{s['campuses'][0]}/{s['campuses'][1]} {s['secs']:5.0f}s ${s['cost']:.2f}")
        print(f"{rid:5} | {line[0]:58} | {line[1]:58}")
    print()
    for key, d in (("a", a_dir), ("b", b_dir)):
        t = tot[key]
        print(f"{d.name:22} contacts found {t['contacts']}, CONFIRMED on cited page {t['ok']} | partners confirmed {t['p']} | campuses confirmed {t['m']} | "
              f"sum of time {t['secs']:.0f}s | cost ${t['cost']:.2f}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
