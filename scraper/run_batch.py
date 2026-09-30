"""Run the two scraping skills over institutes from data/numbers_clean.csv.

    python scraper/run_batch.py --rows r009,r020,r047        # chosen rows (the pilot)
    python scraper/run_batch.py --limit 10                   # first 10 not yet done
    python scraper/run_batch.py --all                        # every row not yet done
    python scraper/run_batch.py --merge-only                 # rebuild the CSVs from saved results

For each institute it calls `claude -p` twice: find-erasmus-contacts (fields A-F, institute
type) and, for universities and colleges, map-institution-network (partners and campuses).
Raw JSON per institute is saved in data/scrape_results/ so the run can resume, and the three
output CSVs are rebuilt after every institute. The operators' data is never overwritten:
enriched values sit next to the originals.
"""
import argparse
import csv
import json
import re
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import verify  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(__import__("os").environ.get("PARAGON_DATA") or ROOT / "data")   # a batch can have its own workspace
RESULTS = DATA / "scrape_results"
NUMBERS = DATA / "numbers_clean.csv"
MODEL = "claude-sonnet-5"
CALL_TIMEOUT = 900   # seconds per claude call
# WebFetch is deliberately not allowed: every page must go through fetch_pages.py so that
# robots.txt, rate limits, the cache and PDF reading apply. WebSearch only finds URLs.
ALLOWED = "WebSearch,Read,Bash(python scraper/fetch_pages.py:*)"
DISALLOWED = "WebFetch"
NETWORK_TYPES = {"university", "college"}   # schools rarely publish partner lists (see --network-all)


def read_numbers():
    with open(NUMBERS, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def call_claude(prompt, attempts=3):
    cmd = ["claude", "-p", prompt, "--model", MODEL, "--output-format", "json",
           "--allowedTools", ALLOWED, "--disallowedTools", DISALLOWED]
    last_err = None
    for attempt in range(1, attempts + 1):
        try:
            p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=CALL_TIMEOUT)
        except subprocess.TimeoutExpired:
            last_err = RuntimeError(f"claude timed out after {CALL_TIMEOUT}s")
            time.sleep(5 * attempt)
            continue
        if p.returncode != 0:
            last_err = RuntimeError(f"claude exited {p.returncode}: {(p.stderr or p.stdout)[:2000]}")
            time.sleep(5 * attempt)
            continue
        try:
            envelope = json.loads(p.stdout)
        except json.JSONDecodeError:
            last_err = RuntimeError(f"claude gave non-JSON output: {p.stdout[:2000]}")
            time.sleep(5 * attempt)
            continue
        if envelope.get("is_error"):
            # a stop_sequence/0-token result with no cost is usually a transient CLI
            # hiccup (seen empirically in the pilot), not a real model failure - retry it
            last_err = RuntimeError(f"claude error (stop_reason={envelope.get('stop_reason')}, "
                                     f"cost={envelope.get('total_cost_usd')}): {str(envelope.get('result'))[:800]}")
            time.sleep(5 * attempt)
            continue
        return envelope.get("result", ""), envelope
    raise last_err


def parse_json(text):
    """Pull the first JSON object out of a reply, tolerating code fences and stray prose."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in reply")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise ValueError("unterminated JSON object")


def row_brief(r):
    keys = ["row_id", "institution_name", "country_sheet", "phone_1", "phone_2",
            "contact_name", "designation", "email", "status_raw"]
    return "\n".join(f"{k}: {r.get(k, '')}" for k in keys)


def _timing(env):
    """Where the time and money went, straight from the claude CLI's own envelope."""
    return {"seconds": round((env.get("duration_ms") or 0) / 1000, 1),
            "model_seconds": round((env.get("duration_api_ms") or 0) / 1000, 1),
            "turns": env.get("num_turns"), "cost_usd": round(env.get("total_cost_usd") or 0, 3)}


def scrape_row(r, network_all):
    out = {"row_id": r["row_id"], "scraped_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    prompt1 = ("Use the find-erasmus-contacts skill on this institute and reply with the JSON object only.\n\n"
               + row_brief(r))
    text, env1 = call_claude(prompt1)
    out["contacts"] = parse_json(text)
    out["timing"] = {"contacts": _timing(env1)}
    itype = (out["contacts"].get("institution_type") or "unknown").lower()
    if itype in NETWORK_TYPES or network_all:
        site = out["contacts"].get("official_website") or ""
        prompt2 = ("Use the map-institution-network skill on this institute and reply with the JSON object only.\n\n"
                   f"row_id: {r['row_id']}\ninstitution_name: {out['contacts'].get('institution_name') or r['institution_name']}\n"
                   f"official_website: {site}\ninstitution_type: {itype}\n"
                   f"country: {(out['contacts'].get('country') or {}).get('value') or r['country_sheet']}")
        text2, env2 = call_claude(prompt2)
        out["network"] = parse_json(text2)
        out["timing"]["network"] = _timing(env2)
    else:
        out["network"] = None
        out["network_skipped"] = f"institution_type={itype}"
    return out


def norm(s):
    return re.sub(r"[^a-z0-9@.]", "", (s or "").lower())


def method_label(res):
    """Provenance shown in the sheet: a person reading a row should know whether an AI read the pages."""
    t = res.get("timing") or {}
    if (res.get("contacts") or {}).get("source") == "none":
        return "not read"
    mode = t.get("mode") or ("agent" if "contacts" in t else "")
    # Deliberately generic - a team's downloaded results shouldn't reveal which specific AI vendor
    # was used under the hood, only whether the page was actually read by AI or not.
    return {"claude_cli": "AI-read", "claude_api": "AI-read", "gemini": "AI-read",
            "rules": "rules-only (lower accuracy)", "agent": "AI-read"}.get(mode, mode)


def coverage_text(n, captured, verified):
    """Honest coverage line for the sheet: how many of what the page states were captured
    AND source-confirmed. `stated_total` (phase 2) is the count the page itself claims;
    `total_seen` is the older field the skill filled when it saw a bigger list."""
    if not n:
        return ""
    stated = n.get("stated_total") or (n.get("total_seen") if n.get("truncated") else None)
    if stated and stated > captured:
        return f"{verified} verified of ~{stated} stated (rest of the list not readable)"
    if captured == 0:
        return "none published"
    return f"{verified} of {captured} verified"


def merge(rows):
    """Rebuild every output CSV from the saved per-institute results, running the
    deterministic verifier on each claim. CSVs keep ALL rows with a `verified` flag
    (audit trail); the sheet builder shows only verified rows in the main tabs and
    lists the rest in 'Needs Review'."""
    enriched, partners, campuses, contacts, review = [], [], [], [], []
    for r in rows:
        f = RESULTS / f"{r['row_id']}.json"
        dup_of = ""
        if not f.exists():
            marker = RESULTS / f"{r['row_id']}.duplicate"
            if marker.exists():
                dup_of = marker.read_text(encoding="utf-8").strip()
            else:
                continue
            base = json.loads((RESULTS / f"{dup_of}.json").read_text(encoding="utf-8")) \
                if (RESULTS / f"{dup_of}.json").exists() else None
            if not base:
                continue
            res = base
        else:
            res = json.loads(f.read_text(encoding="utf-8"))
        c = res.get("contacts") or {}
        ct = c.get("contact") or {}
        site = c.get("official_website") or ""
        country = (c.get("country") or {}).get("value") or ""
        phone = (c.get("phone") or {}).get("value") or ""
        n = res.get("network") or {}
        name = c.get("institution_name") or r["institution_name"]

        contact_ok, contact_note = verify.verify_contact(ct, site)
        country_ok = bool(country) and verify.verify_partner_or_campus(c.get("country") or {}, site, "value")[0]
        phone_ok = bool(phone) and verify.verify_partner_or_campus(c.get("phone") or {}, site, "value")[0]
        p_items = n.get("external_collaboration") or []
        m_items = n.get("internal_collaboration") or []
        p_res = [verify.verify_partner_or_campus(p, site, "partner_name") for p in p_items]
        m_res = [verify.verify_partner_or_campus(m, site, "campus_name") for m in m_items]
        p_ok, m_ok = sum(1 for ok, _ in p_res if ok), sum(1 for ok, _ in m_res if ok)

        changed = []
        if country and norm(country) != norm(r["country_sheet"]):
            changed.append("country")
        if ct.get("name") and norm(ct["name"]) != norm(r["contact_name"]):
            changed.append("contact_name")
        if ct.get("email") and norm(ct["email"]) not in norm(r["email"]):
            changed.append("email")
        enriched.append({
            "row_id": r["row_id"],
            "duplicate_of": dup_of,
            "institution_original": r["institution_name"],
            "country_original": r["country_sheet"],
            "phone_original": r["phone_1"],
            "person_original": r["contact_name"],
            "designation_original": r["designation"],
            "email_original": r["email"],
            "institution_scraped": c.get("institution_name") or "",
            "institution_type": c.get("institution_type") or "",
            "official_website": site,
            "country_scraped": country,
            "country_source_url": (c.get("country") or {}).get("source_url") or "",
            "country_verified": "yes" if country_ok else ("no" if country else ""),
            "phone_scraped": phone,
            "phone_source_url": (c.get("phone") or {}).get("source_url") or "",
            "phone_verified": "yes" if phone_ok else ("no" if phone else ""),
            "phone_alt": "; ".join(c.get("phone_alt") or []),
            "person_scraped": ct.get("name") or "",
            "designation_scraped": ct.get("designation") or "",
            "designation_local": ct.get("designation_local") or "",
            "email_scraped": ct.get("email") or "",
            "office_email": ct.get("office_email") or "",
            "contact_phone": ct.get("phone") or "",
            "contact_source_url": ct.get("source_url") or "",
            "contact_evidence": ct.get("evidence_quote") or "",
            "contact_verified": "yes" if contact_ok else ("no" if ct.get("name") else ""),
            "contact_verify_note": contact_note,
            "rung": c.get("rung") or "",
            "confidence": c.get("confidence", ""),
            "needs_human_check": "yes" if c.get("needs_human_check") or n.get("needs_human_check") else "",
            "changed_vs_sheet": ";".join(changed),
            "partners_found": len(p_items) if res.get("network") else "",
            "partners_verified": p_ok if res.get("network") else "",
            "campuses_found": len(m_items) if res.get("network") else "",
            "campuses_verified": m_ok if res.get("network") else "",
            "partners_coverage": coverage_text(n, len(p_items), p_ok),
            "campuses_coverage": coverage_text(n, len(m_items), m_ok) if res.get("network") else "",
            "network_truncated": "yes" if n.get("truncated") else "",
            "multiplier_hook": n.get("multiplier_hook") or "",
            "network_note": n.get("coverage_note") or res.get("network_skipped") or "",
            "notes": c.get("notes") or "",
            "scraped_at": res.get("scraped_at", ""),
            "method": method_label(res),
        })
        if dup_of:
            continue   # detail rows are written once, under the original row

        if ct.get("name"):
            contacts.append({"row_id": r["row_id"], "institution": name, "rank": 1,
                             "name": ct.get("name"), "designation": ct.get("designation") or "",
                             "designation_local": ct.get("designation_local") or "",
                             "email": ct.get("email") or "", "phone": ct.get("phone") or "",
                             "source_url": ct.get("source_url") or "", "evidence_quote": ct.get("evidence_quote") or "",
                             "why_chosen": c.get("rung") or "", "verified": "yes" if contact_ok else "no",
                             "verify_note": contact_note})
            if not contact_ok:
                review.append({"row_id": r["row_id"], "institution": name, "kind": "contact", "item": ct.get("name"),
                               "source_url": ct.get("source_url") or "", "evidence_quote": ct.get("evidence_quote") or "",
                               "why_flagged": contact_note})

        for k, alt in enumerate(c.get("alternates") or [], start=2):      # backups: same source check as the main contact
            if not alt.get("name"):
                continue
            alt_ok, alt_note = verify.verify_contact(alt, site)
            contacts.append({"row_id": r["row_id"], "institution": name, "rank": k,
                             "name": alt.get("name"), "designation": alt.get("designation") or "",
                             "designation_local": alt.get("designation_local") or "",
                             "email": alt.get("email") or "", "phone": alt.get("phone") or "",
                             "source_url": alt.get("source_url") or "", "evidence_quote": alt.get("evidence_quote") or "",
                             "why_chosen": alt.get("why_chosen") or "Backup contact", "verified": "yes" if alt_ok else "no",
                             "verify_note": alt_note})
            if not alt_ok:
                review.append({"row_id": r["row_id"], "institution": name, "kind": "contact", "item": alt.get("name"),
                               "source_url": alt.get("source_url") or "", "evidence_quote": alt.get("evidence_quote") or "",
                               "why_flagged": alt_note})

        for p, (ok, note) in zip(p_items, p_res):
            row = {"row_id": r["row_id"], "institution": name, **{
                k: p.get(k, "") for k in ("partner_name", "partner_country", "partner_type",
                                           "mobility_type", "source_url", "evidence_quote", "confidence")},
                "verified": "yes" if ok else "no", "verify_note": note, "scraped_at": res.get("scraped_at", "")}
            partners.append(row)
            if not ok:
                review.append({"row_id": r["row_id"], "institution": name, "kind": "partner", "item": p.get("partner_name", ""),
                               "source_url": p.get("source_url", ""), "evidence_quote": p.get("evidence_quote", ""),
                               "why_flagged": note})
        for m, (ok, note) in zip(m_items, m_res):
            row = {"row_id": r["row_id"], "institution": name, **{
                k: m.get(k, "") for k in ("campus_name", "country", "city", "role",
                                           "source_url", "evidence_quote")},
                "verified": "yes" if ok else "no", "verify_note": note, "scraped_at": res.get("scraped_at", "")}
            campuses.append(row)
            if not ok:
                review.append({"row_id": r["row_id"], "institution": name, "kind": "campus", "item": m.get("campus_name", ""),
                               "source_url": m.get("source_url", ""), "evidence_quote": m.get("evidence_quote", ""),
                               "why_flagged": note})
    write_csv(DATA / "institutions_enriched.csv", enriched, ["row_id"])
    write_csv(DATA / "partners.csv", partners, ["row_id", "institution", "partner_name", "partner_country",
              "partner_type", "mobility_type", "source_url", "evidence_quote", "confidence", "verified",
              "verify_note", "scraped_at"])
    write_csv(DATA / "campuses.csv", campuses, ["row_id", "institution", "campus_name", "country", "city",
              "role", "source_url", "evidence_quote", "verified", "verify_note", "scraped_at"])
    write_csv(DATA / "contacts.csv", contacts, ["row_id", "institution", "rank", "name", "designation",
              "designation_local", "email", "phone", "source_url", "evidence_quote", "why_chosen", "verified",
              "verify_note"])
    write_csv(DATA / "needs_review.csv", review, ["row_id", "institution", "kind", "item", "source_url",
              "evidence_quote", "why_flagged"])
    return len(enriched), len(partners), len(campuses), len(contacts), len(review)


def write_csv(path, rows, fallback_fields):
    fields = list(rows[0].keys()) if rows else fallback_fields
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rows", help="comma-separated row_ids, e.g. r009,r020")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--network-all", action="store_true", help="also map networks for schools")
    ap.add_argument("--merge-only", action="store_true")
    ap.add_argument("--workers", type=int, default=1, help="institutes to scrape in parallel")
    a = ap.parse_args()

    RESULTS.mkdir(parents=True, exist_ok=True)
    rows = read_numbers()
    if a.merge_only:
        print("enriched / partners / campuses rows:", merge(rows))
        return
    if a.rows:
        wanted = [x.strip() for x in a.rows.split(",") if x.strip()]
        todo = [r for r in rows if r["row_id"] in wanted]
    elif a.all or a.limit:
        todo = rows
    else:
        ap.error("give --rows, --limit, --all or --merge-only")

    # duplicates are scraped once: later rows in a duplicate group point at the first
    first_in_group = {}
    for r in rows:
        for g in r["duplicate_group"].split():
            first_in_group.setdefault(g, r["row_id"])
    queue = []
    for r in todo:
        rid = r["row_id"]
        if (RESULTS / f"{rid}.json").exists() or (RESULTS / f"{rid}.duplicate").exists():
            continue
        origin = next((first_in_group[g] for g in r["duplicate_group"].split()
                       if first_in_group[g] != rid), None)
        if origin:
            (RESULTS / f"{rid}.duplicate").write_text(origin, encoding="utf-8")
            print(f"{rid}: duplicate of {origin}, scraped once")
            continue
        if a.limit and len(queue) >= a.limit:
            break
        queue.append(r)

    lock = threading.Lock()

    def work(r):
        rid = r["row_id"]
        t0 = time.time()
        with lock:
            print(f"{rid}: {r['institution_name'] or r['phone_1']} ...", flush=True)
        try:
            res = scrape_row(r, a.network_all)
            secs = round(time.time() - t0, 1)
            res.setdefault("timing", {})["total_seconds"] = secs
            with lock:
                (RESULTS / f"{rid}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
                c = res["contacts"]
                print(f"{rid}: done in {secs}s | type={c.get('institution_type')} "
                      f"contact={(c.get('contact') or {}).get('name')} conf={c.get('confidence')} "
                      f"network={'yes' if res.get('network') else 'skipped'}", flush=True)
                merge(rows)
            return secs
        except Exception as e:
            with lock:
                print(f"{rid}: FAILED after {round(time.time() - t0, 1)}s: {e}", flush=True)
                with open(RESULTS / "errors.log", "a", encoding="utf-8") as log:
                    log.write(f"{time.strftime('%F %T')} {rid}: {e}\n")
            return None

    wall0 = time.time()
    with ThreadPoolExecutor(max_workers=max(1, a.workers)) as pool:
        secs = [x for x in pool.map(work, queue) if x is not None]
    wall = time.time() - wall0
    if queue:
        print(f"\nTIMING: {len(secs)}/{len(queue)} institutes finished with {a.workers} worker(s) in "
              f"{wall / 60:.1f} min wall-clock = {wall / max(len(queue), 1):.0f}s per institute effective "
              f"(each one took {sum(secs) / max(len(secs), 1):.0f}s on its own)")
    print("enriched / partners / campuses rows:", merge(rows))


if __name__ == "__main__":
    main()
