"""The file a manager uploads into the PGI CRM (admin panel > Leads) for one finished batch.

The CRM owner's rules (docs/02-scraper-integration.md in the CRM project): the template's columns only, every row the
batch was given back plus the extra numbers found, one row per contact person. Partners, campuses and websites stay in
the scraper. Up to 3 numbers in the Phone cell become the lead's first, second and third numbers. A number that appears
twice in one file would be skipped by the CRM as a duplicate, so each number is used once.

Numbers and emails already tried on the dialer are compared as hashes only: the repository is public.
"""
import csv
import hashlib
import io
import re
import unicodedata

CRM_COLS = ["Phone", "Name", "Designation", "Institute", "Email", "Country", "Notes"]
MAX_NUMBERS = 3


def _norm(s):
    s = unicodedata.normalize("NFKD", str(s or "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


def inst_key(institute, country):
    return f"{_norm(institute)}|{_norm(country)}"


def _digits(phone):
    # last 8 digits (Malta's whole national number): +356 2123 4567, 0035621234567 and 21234567 compare equal
    return re.sub(r"\D", "", str(phone or ""))[-8:]


def _hash(v):
    return hashlib.sha256(v.encode("utf-8")).hexdigest()[:16]


def phone_hash(phone):
    d = _digits(phone)
    return _hash("p:" + d) if len(d) >= 6 else None


def email_hash(email):
    e = str(email or "").strip().lower()
    return _hash("e:" + e) if "@" in e else None


def _value(x):
    return str((x.get("value") if isinstance(x, dict) else x) or "").strip()


def people(result):
    """The people in a scrape result, best first: dicts with name, designation, phone, email."""
    c = (result or {}).get("contacts") or {}
    return [{"name": p.get("name") or "", "designation": p.get("designation") or "", "phone": _value(p.get("phone")),
             "email": p.get("email") or p.get("office_email") or ""}
            for p in [c.get("contact") or {}] + list(c.get("alternates") or []) if isinstance(p, dict)]


def office_numbers(result):
    c = (result or {}).get("contacts") or {}
    return [v for v in (_value(x) for x in [c.get("phone")] + list(c.get("phone_alt") or [])) if v]


def file_rows(rows, results, tried=None, note=""):
    """(CSV rows, summary). rows: the batch's numbers_clean.csv rows; results: {row_id: scrape result}.
    tried: {inst_key: hashes of numbers already tried}: those are left out, for a rescrape."""
    tried = tried or {}
    used, out = set(), []
    without_number = 0

    def fresh(number, skip):
        h = phone_hash(number)
        if not h or h in used or h in skip:
            return None
        used.add(h)
        return number

    for r in rows:
        institute = str(r.get("institution_name") or "").strip()
        if not institute:
            without_number += 1
            continue
        country = str(r.get("country_sheet") or "").strip()
        res = results.get(r.get("row_id"))
        skip = tried.get(inst_key(institute, country), set())
        found = people(res)
        main = found[0] if found else {"name": "", "designation": "", "phone": "", "email": ""}
        wanted = [r.get("phone_1"), r.get("phone_2"), main["phone"]] + office_numbers(res)
        numbers = []
        for n in wanted:
            if len(numbers) < MAX_NUMBERS and fresh(n, skip):
                numbers.append(str(n).strip())
        if not numbers:
            without_number += 1
        else:
            out.append({"Phone": " / ".join(numbers), "Name": main["name"] or r.get("contact_name") or "",
                        "Designation": main["designation"] or r.get("designation") or "", "Institute": institute,
                        "Email": main["email"] or r.get("email") or "", "Country": country, "Notes": note})
        for p in found[1:]:                     # another person with a number of their own is a lead of their own
            number = fresh(p["phone"], skip)
            if number:
                out.append({"Phone": str(number).strip(), "Name": p["name"], "Designation": p["designation"],
                            "Institute": institute, "Email": p["email"], "Country": country, "Notes": note})
    return out, {"rows": len(out), "without_number": without_number}


def csv_text(rows):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CRM_COLS, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()
