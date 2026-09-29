"""Fill prompts/persona.md's placeholders for one institute.

    python agent/fill_persona.py r035
    python agent/fill_persona.py r035 --out data/calls/r035_prompt.txt

Reads, in priority order: data/institutions_enriched.csv (scraper output, if this
row has been scraped) falling back to data/numbers_clean.csv (the raw sheet), plus
data/partners.csv for the multiplier-effect hook. Prints the filled prompt so it
can be piped straight into vapi_call.py or read by a person before a call.

A placeholder left unfilled is a bug, not a style choice: the script fails loudly
rather than sending a call with "{{institution_name}}" literally in the prompt.
"""
import argparse
import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PERSONA = ROOT / "prompts" / "persona.md"

PLACEHOLDER = re.compile(r"\{\{([^}]+)\}\}")


def read_csv(path):
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def find_row(row_id):
    for path, id_field, name_field in [
        (ROOT / "data" / "institutions_enriched.csv", "row_id", "institution_scraped"),
        (ROOT / "data" / "numbers_clean.csv", "row_id", "institution_name"),
    ]:
        for r in read_csv(path):
            if r.get(id_field) == row_id:
                return path.name, r, name_field
    return None, None, None


def build_fields(row_id):
    source, row, name_field = find_row(row_id)
    if row is None:
        raise SystemExit(f"row_id {row_id!r} not found in institutions_enriched.csv or numbers_clean.csv")

    institution_name = (row.get(name_field) or row.get("institution_name") or row.get("institution_original") or "").strip()
    contact_name = (row.get("person_scraped") or row.get("contact_name") or "").strip()

    partners = [p for p in read_csv(ROOT / "data" / "partners.csv") if p.get("row_id") == row_id]
    multiplier_hook = ""
    if partners:
        best = max(partners, key=lambda p: float(p.get("confidence") or 0))
        multiplier_hook = (f"This institution already collaborates with "
                            f"{best['partner_name']} ({best['partner_country']}).")

    # brand_card_text has no source yet anywhere in the project - always the flagged default
    return {
        "institution_name": institution_name or f"institute {row_id}",
        "contact_name": contact_name or "the Erasmus coordinator",
        "brand_card_text": "[BRAND CARD NOT YET PROVIDED -- skip this point and flag the call for review]",
        "multiplier_hook": multiplier_hook,
    }, source


HTML_COMMENT = re.compile(r"<!--.*?-->\s*", re.S)


def fill(row_id):
    text = HTML_COMMENT.sub("", PERSONA.read_text(encoding="utf-8"), count=1)
    fields, source = build_fields(row_id)

    # an empty multiplier hook means: drop that whole section, not send a blank one
    if not fields["multiplier_hook"]:
        text = re.sub(r"\n# The multiplier effect.*?(?=\n# )", "\n", text, flags=re.S)

    missing = []

    def repl(m):
        key = m.group(1).strip()
        if key not in fields:
            missing.append(key)
            return m.group(0)
        return fields[key]

    filled = PLACEHOLDER.sub(repl, text)
    if missing:
        raise SystemExit("Unfilled placeholder(s), fix build_fields() or persona.md: " + "; ".join(missing))
    return filled, source


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("row_id")
    ap.add_argument("--out", help="write to this file instead of stdout")
    a = ap.parse_args()
    filled, source = fill(a.row_id)
    print(f"[filled from {source}]", file=sys.stderr)
    if a.out:
        Path(a.out).write_text(filled, encoding="utf-8")
        print(f"written to {a.out}", file=sys.stderr)
    else:
        print(filled)


if __name__ == "__main__":
    main()
