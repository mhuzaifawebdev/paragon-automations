"""Clean the operators' call-log sheet into two tables.

    source_calls_sheet.csv  ->  numbers_clean.csv   one row per sheet row (institute + contact)
                                attempts.csv        one row per non-empty attempt cell (long format)

Nothing is guessed or merged silently: missing/incorrect countries are left blank and flagged,
duplicates get a shared `duplicate_group` id, and attempt text that the keyword rules cannot
place cleanly is flagged `needs_review`.

Run:  python clean_sheet.py
"""
import csv
import re
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).parent
SRC = HERE / "source_calls_sheet.csv"

# Longest prefix wins. Used only to FLAG a country/phone mismatch, never to fill a country.
DIAL = {
    "353": "Ireland", "357": "Cyprus", "359": "Bulgaria", "370": "Lithuania", "381": "Serbia",
    "386": "Slovenia", "420": "Czechia", "421": "Slovakia", "351": "Portugal",
    "31": "Netherlands", "32": "Belgium", "33": "France", "34": "Spain", "36": "Hungary",
    "39": "Italy", "43": "Austria", "46": "Sweden", "48": "Poland", "49": "Germany", "66": "Thailand",
}

# Column positions in the source (the sheet has no real date row; headers are kept verbatim).
C_INST, C_COUNTRY, C_PHONE, C_PERSON, C_DESIG, C_EMAIL = 0, 1, 2, 3, 4, 5
C_FIRST_ATTEMPT, C_LAST_ATTEMPT, C_STATUS = 6, 14, 15

# (category, regex). Order = priority; the first hit is the primary category.
RULES = [
    ("language_barrier", r"\blb\b|no e?n?glish|dont understand|unable to understand"),
    ("no_erasmus_project", r"no erasmus project"),
    ("email_requested", r"send email"),
    ("left_message", r"left msg"),
    ("transferred_failed", r"transfer"),
    ("hung_up", r"hung|hang"),
    ("callback_requested", r"call (on|at|tomorrow|tommorrow|back)|call tomorrow|available in|availabe call"),
    ("speaker_unclear", r"speaker"),
    ("not_connected", r"not connected|not being connected|unreachable|not working|single tone"),
    ("not_answered", r"not answer|not recieved|not recived|not being rec|didnt respond|not available|not availabe"),
]
NO_HUMAN = {"not_answered", "not_connected", "hung_up", "speaker_unclear", "transferred_failed"}


def digits(s):
    return re.sub(r"\D", "", s)


def norm_name(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def split_phones(cell):
    out = []
    for part in re.split(r"[\n/;]+", cell):
        d = digits(part)
        if d:
            out.append(d)
    return out


def dial_country(phone):
    for n in (3, 2):
        if phone[:n] in DIAL:
            return DIAL[phone[:n]]
    return ""


def categorise(text):
    t = text.lower()
    if "respond well" in t:
        return "other", ["other"]
    hits = [c for c, rx in RULES if re.search(rx, t)]
    return (hits[0], hits) if hits else ("other", ["other"])


def find_date(text):
    m = re.search(r"\b(\d{1,2})/(\d{1,2})\b", text)
    return f"{int(m.group(1)):02d}/{int(m.group(2)):02d}" if m else ""


def main():
    with open(SRC, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    header, body = rows[0], rows[1:]
    width = len(header)

    numbers, attempts = [], []
    for i, raw in enumerate(body, start=1):
        raw = (raw + [""] * width)[:width]
        rid = f"r{i:03d}"
        flags = []

        name = raw[C_INST].strip()
        if digits(name) == name.replace(" ", "") and digits(name):
            flags.append("name_is_phone")
            inst_name = ""
        else:
            inst_name = name

        country = raw[C_COUNTRY].strip()
        phones = split_phones(raw[C_PHONE])
        if not country:
            flags.append("country_missing")
        if len(phones) > 1:
            flags.append("multiple_phones")
        guess = dial_country(phones[0]) if phones else ""
        if country and guess and country != guess:
            flags.append(f"country_mismatch(sheet={country},phone_prefix={guess})")
        if phones and not guess:
            flags.append("phone_prefix_unrecognised")

        person = raw[C_PERSON].strip()
        if person in ("-", ""):
            person = ""
            flags.append("no_contact_name")
        desig = raw[C_DESIG].strip().lower().replace(" ", "_")
        emails = ";".join(e.strip() for e in raw[C_EMAIL].splitlines() if e.strip())
        status_lines = [s.strip() for s in raw[C_STATUS].splitlines() if s.strip()]

        numbers.append({
            "row_id": rid, "institution_name": inst_name, "country_sheet": country,
            "phone_1": phones[0] if phones else "", "phone_2": phones[1] if len(phones) > 1 else "",
            "contact_name": person, "designation": desig, "email": emails,
            "status_raw": " | ".join(status_lines), "duplicate_group": "", "flags": ";".join(flags),
        })

        for col in range(C_FIRST_ATTEMPT, C_LAST_ATTEMPT + 1):
            _add_attempt(attempts, rid, header[col].strip(), raw[col])
        for line in status_lines:
            _add_attempt(attempts, rid, "STATUS", line)

    _mark_duplicates(numbers)
    _write(HERE / "numbers_clean.csv", numbers)
    _write(HERE / "attempts.csv", attempts)
    _report(numbers, attempts)


def _add_attempt(attempts, rid, column, cell):
    text = " ".join(cell.split())
    if not text:
        return
    primary, hits = categorise(text)
    date = find_date(text)
    date_from = "text" if date else ""
    if not date and re.fullmatch(r"\d{1,2}/\d{1,2}", column):
        date, date_from = find_date(column), "column_header"
    attempts.append({
        "row_id": rid, "sheet_column": column, "text": text, "category": primary,
        "all_matches": "|".join(hits), "date_dm": date, "date_from": date_from,
        "needs_review": "yes" if len(hits) > 1 or primary in ("other", "speaker_unclear") else "",
    })


def _mark_duplicates(numbers):
    groups = defaultdict(list)
    for r in numbers:
        if r["phone_1"]:
            groups[("phone", r["phone_1"])].append(r)
        if r["institution_name"]:
            groups[("name", norm_name(r["institution_name"]))].append(r)
    gid = 0
    seen = set()  # same pair matched by both phone and name is one group, not two
    for members in groups.values():
        key = frozenset(r["row_id"] for r in members)
        if len(members) > 1 and key not in seen:
            seen.add(key)
            gid += 1
            for r in members:
                r["duplicate_group"] = (r["duplicate_group"] + f" g{gid:02d}").strip()


def _write(path, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def _report(numbers, attempts):
    print(f"source rows: {len(numbers)}  ->  numbers_clean.csv rows: {len(numbers)}")
    print(f"attempt records: {len(attempts)}  ->  attempts.csv")
    dupes = defaultdict(list)
    for r in numbers:
        for g in r["duplicate_group"].split():
            dupes[g].append(f'{r["row_id"]}:{r["institution_name"] or r["phone_1"]}')
    print(f"\nduplicate groups (not merged): {len(dupes)}")
    for g, m in sorted(dupes.items()):
        print(f"  {g}: {', '.join(m)}")
    flagcount = Counter(fl.split("(")[0] for r in numbers for fl in r["flags"].split(";") if fl)
    print("\nrow flags:", dict(flagcount))
    cats = Counter(a["category"] for a in attempts)
    print("\nattempt categories:", dict(cats.most_common()))
    no_human = sum(cats[c] for c in NO_HUMAN)
    print(f"attempts that did not reach a person: {no_human}/{len(attempts)} = {no_human / len(attempts):.0%}")
    review = sum(1 for a in attempts if a["needs_review"])
    print(f"attempts flagged needs_review: {review}")


if __name__ == "__main__":
    main()
