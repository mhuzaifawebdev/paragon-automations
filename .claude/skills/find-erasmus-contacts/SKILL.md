---
name: find-erasmus-contacts
description: Finds the person responsible for Erasmus+ / international partnerships at one institute, plus the institute's country, type and main phone, from its own public web pages. Use when given a row from data/numbers_clean.csv to verify or enrich. Returns one JSON object.
---

# find-erasmus-contacts

Given ONE institute (a row from `data/numbers_clean.csv`), return the six sheet fields and where each came from.

| Sheet column | Field returned |
|---|---|
| A institution | `institution_name` (official name) |
| B country | `country` |
| C phone number | `phone` (+ `phone_alt`) |
| D responsible person name | `contact.name` |
| E designation | `contact.designation` |
| F responsible person email | `contact.email` |

## Input (in the prompt)
The row's `row_id`, institution name (may be blank or a phone number), country (may be blank), phone(s), current contact name/designation/email, and `already_tried` (people already called).

## How to work
1. **Identify the institute.** Use WebSearch with the name and, if known, the country/phone. Find its **official website** (its own domain, not a directory). If the name field is only a phone number, search the number. If you cannot identify the institute with confidence, stop and return `needs_human_check: true` with empty fields.
2. **Classify** `institution_type`: `university` (higher education, degrees), `college` (vocational/professional or further-education), `school` (secondary/primary), or `unknown`. Give a one-line `type_evidence`.
3. **Country**: take it from the website (address, domain, "contact" page). Record the source. Never guess it from the phone prefix alone.
4. **Find the Erasmus+ contact.** Look for the international relations office, Erasmus+ or mobility page, and the staff/contact page. Fetch pages with:
   `python scraper/fetch_pages.py <url> --links`
   (it caches, obeys robots.txt, rate-limits and reads PDFs). **Use only this command to read pages; do not use WebFetch.** Use WebSearch only to find URLs. Read at most **12 pages** per institute. Never crawl or loop over many pages; follow only links that plausibly lead to the international office, Erasmus+, mobility or contact.
   Local-language search terms: *Erasmus coordinator / coordinador Erasmus / coordinateur Erasmus / Erasmus-Koordinator / koordynator Erasmus / Erasmus koordinátor / oficina de relaciones internacionales / service des relations internationales / International Office / Akademisches Auslandsamt*.
5. **Pick the person by this order:**
   - `university`: (1) Head of Unit for Erasmus+/International Office, (2) Deputy Head.
   - `college` or `school`: (1) Headmaster/Principal, (2) Erasmus+ coordinator or the person responsible for Erasmus+, (3) any staff member confirmed to speak English.
   Prefer the person explicitly labelled as Erasmus+ responsible over a higher title. Skip anyone in `already_tried`. If the sheet already names someone, check that name against the site and report whether it matches.
6. **Phone**: the main switchboard or the international office line, in international format (+CC ...). Put other published numbers in `phone_alt`.

## Rules (non-negotiable)
- **Leave a field `null` rather than guess.** Every non-null value needs `source_url` and a short `evidence_quote` **copied verbatim from `fetch_pages.py` output for that URL.** A WebSearch result snippet is not an evidence quote and must never be used as one, even labelled as uncertain: if `fetch_pages.py` cannot read a page (SSL error, 404, robots.txt block, timeout), do not fall back to search snippets for that field. Instead set it `null`, add one line to `notes` naming the URL and the error, and set `needs_human_check: true`. It is correct for a row to come back mostly `null` when the site could not be read.
- Only **public work contact details** published by the institution itself. No personal social media, no private addresses, no data from people-search sites.
- Names as written on the page. Give `designation` in English, and keep the original wording in `designation_local`.
- Do not collect course or teaching content.
- Confidence: 0.9+ = role and name both stated on an official page; 0.6-0.8 = role stated but name or currency uncertain; below 0.6 = inferred. `needs_human_check: true` if below 0.7, if the site could not be read, or if the person's role does not exactly match the rung.

## Output
Return ONLY one JSON object (no prose, no code fence):

```json
{
  "row_id": "r012",
  "institution_name": "official name",
  "institution_type": "university|college|school|unknown",
  "type_evidence": "one line",
  "official_website": "https://...",
  "country": {"value": "Spain", "source_url": "...", "evidence_quote": "..."},
  "phone": {"value": "+34 ...", "source_url": "...", "evidence_quote": "..."},
  "phone_alt": ["+34 ..."],
  "contact": {
    "name": "...", "designation": "Erasmus+ coordinator", "designation_local": "Coordinador Erasmus+",
    "email": "personal work email, or null", "office_email": "shared office inbox, or null",
    "phone": "...", "source_url": "...", "evidence_quote": "..."
  },
  "matches_sheet_contact": true,
  "rung": "school:erasmus_coordinator",
  "source": "web",
  "confidence": 0.85,
  "needs_human_check": false,
  "pages_read": 5,
  "notes": "anything a human should know"
}
```
Use `null` for any object or value not found (for example `"contact": null`).
