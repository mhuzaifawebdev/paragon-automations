"""How good is the FREE (no-model) extraction, judged against the model's answers on the same pages?

    python scraper2/eval_rules.py            # uses only pages already in the cache; no model call, no cost

For every institute the model already answered (timing.mode == claude_cli) it rebuilds the same evidence pack,
runs the rules extraction (scraper2/rank.py), and compares:
  contact   does the rules primary contact match the model's primary (same person)?
  partners  how many of the model's partners do the rules also find (by cited line)?
The result decides when the pipeline may skip the model (see pipeline.rules_confident).
"""
import csv
import json
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scraper2"))
sys.path.insert(0, str(ROOT / "scraper"))
import crawl  # noqa: E402
import discover  # noqa: E402
import evidence  # noqa: E402
import pipeline  # noqa: E402
import rank  # noqa: E402


def _n(s):
    s = unicodedata.normalize("NFKD", (s or "").lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def same_person(a, b):
    ta, tb = set(re.findall(r"[a-z]{3,}", _n(a))), set(re.findall(r"[a-z]{3,}", _n(b)))
    return bool(ta and tb and len(ta & tb) >= min(2, len(ta), len(tb)))


def main():
    rows = {r["row_id"]: r for r in csv.DictReader(open(ROOT / "data" / "numbers_clean.csv", encoding="utf-8-sig"))}
    memo = discover.load_memo()
    out = []
    for f in sorted((ROOT / "data" / "scrape_results").glob("r*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        if (d.get("timing") or {}).get("mode") != "claude_cli":
            continue
        rid = f.stem
        model_contact = ((d["contacts"] or {}).get("contact") or {}).get("name") or ""
        model_partners = len((d.get("network") or {}).get("external_collaboration") or [])
        site = memo.get(rid) or d["contacts"].get("official_website")
        if not site:
            continue
        cr = crawl.crawl(site, max_pages=10)
        pack = evidence.build_pack(cr["pages"])
        inst = {"name": rows[rid]["institution_name"], "country": rows[rid]["country_sheet"], "phone": rows[rid]["phone_1"],
                "contact": rows[rid]["contact_name"]}
        rd = rank.extract(pack, inst)
        rc = pipeline.boost_on_file(pipeline.resolve_contacts(rd, pack), inst["contact"])
        rp = rc[0]["name"] if rc else ""
        conf = pipeline.rules_confident(rc, rd, pack) if hasattr(pipeline, "rules_confident") else None
        out.append((rid, d["contacts"].get("institution_type"), model_contact, rp, same_person(model_contact, rp), conf,
                    model_partners, len(rd["partners"])))
    print(f"{'row':5} {'type':10} {'model contact':26} {'rules contact':26} {'agree':6} {'gate':6} partners model/rules")
    for rid, t, mc, rp, ag, conf, mp, rpn in out:
        print(f"{rid:5} {str(t):10} {mc[:25]:26} {rp[:25]:26} {('YES' if ag else ('-' if not mc and not rp else 'NO')):6} {str(conf):6} {mp}/{rpn}")
    have = [o for o in out if o[2]]
    print(f"\nmodel found a contact on {len(have)} of {len(out)}; rules agree on {sum(1 for o in have if o[4])} of those")
    gated = [o for o in out if o[5]]
    if gated:
        print(f"rules-confident gate accepted {len(gated)}; of those the model agreed on {sum(1 for o in gated if o[4])}; "
              f"gate accepted a contact where model found none: {sum(1 for o in gated if not o[2])}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
