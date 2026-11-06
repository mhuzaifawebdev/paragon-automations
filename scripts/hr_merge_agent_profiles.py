"""Merges the per-role CSVs in hr_profiles/agent/ (one per keyword tab) into one Excel + Google Sheet "Best Profiles".
Each row is checked: its matched keywords must come from that role's own list (others are dropped), at least one must
remain, a LinkedIn link must look like a profile link (else it is cleared), the Claude role must mention Claude or
Anthropic, duplicates are removed.

    python scripts/hr_merge_agent_profiles.py
"""
import csv
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scraper2"))
import hr_best_profiles as hb  # noqa: E402

AGENT = hb.OUT / "agent"
FILES = {"claude_ai_expert": "claude", "sales": "sales", "recruiters": "recruit", "ghost_writers": "ghost",
         "va_administrators": "va administrators", "instructional_designer": "instructional",
         "fundraising_corporate_finance": "fundrais", "event_managers_digital_marketing": "event"}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    tabs = hb.keyword_tabs()
    results, dropped = {}, {}
    for stem, hint in FILES.items():
        role = next((t for t in tabs if hint in hb.norm(t)), None)
        files = [AGENT / f"{stem}.csv"] + sorted(AGENT.glob(f"{stem}_[0-9]*.csv"))     # every search round
        if not role or not any(f.exists() for f in files):
            continue
        kws = tabs[role]
        rows, seen, bad = [], set(), 0
        all_rows = []
        for f in files:
            if f.exists():
                with open(f, encoding="utf-8-sig", newline="") as fh:
                    all_rows += list(csv.DictReader(fh))
        if True:
            for r in all_rows:
                name = (r.get("name") or "").strip()
                given = [x.strip() for x in re.split(r"[;,]", r.get("keywords_matched") or "") if x.strip()]
                matched = [k for k in kws if any(hb.norm(k) == hb.norm(g) or hb.norm(k) in hb.norm(g) for g in given)]
                blob = " ".join(str(v) for v in r.values())
                li = (r.get("linkedin_url") or "").strip()
                if li and not re.match(r"https?://([a-z]{2,3}\.)?linkedin\.com/in/[^/\s]+", li):
                    li = ""
                key = li.lower().rstrip("/") or hb.norm(name)
                name = re.sub(r"\s*\((?:hubstaff|upwork|fiverr)?\s*display name\)", "", name, flags=re.I).strip()
                if len(name.split()) < 2 or re.search(r"\d|not shown|username", name, re.I) or not matched or key in seen or \
                        (stem == "claude_ai_expert" and not re.search(r"claude|anthropic", blob, re.I)):
                    bad += 1
                    continue
                seen.add(key)
                yrs = re.search(r"\d+", r.get("years") or "")
                edu = (r.get("education_sector") or "").strip().lower().startswith("y")
                rows.append({"name": name, "title": r.get("current_title", ""), "employer": r.get("employer", ""),
                             "city": r.get("city", ""), "years": int(yrs.group()) if yrs else "",
                             "linkedin": li, "link_kind": "profile" if li else "", "matched": matched,
                             "source": r.get("source_url", "") or li, "edu": edu,
                             "score": 3 * len(matched) + (2 if edu else 0) + (1 if li else 0)})
        rows.sort(key=lambda x: -x["score"])
        results[role] = (rows[:20], 0.0)
        dropped[role] = bad
        print(f"{role}: {len(results[role][0])} kept, {bad} dropped by the checks", flush=True)
    path = hb.OUT / f"Best_Profiles_{time.strftime('%Y-%m-%d_%H%M')}.xlsx"
    hb.save(results, tabs, path, want=20)
    print(f"Saved {path}")
    try:
        print("Google Sheet:", hb.upload(path))
    except Exception as e:
        print(f"(Drive upload failed: {str(e)[:200]})")


if __name__ == "__main__":
    main()
