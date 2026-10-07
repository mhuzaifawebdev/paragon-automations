---
name: map-institution-network
description: Maps who a university or college collaborates with (partner institutions and networks) and where its own campuses are, from its public website. Captures the "multiplier effect" data Paragon's callers use. Use for one institute after find-erasmus-contacts has classified it. Returns one JSON object.
---

# map-institution-network

Paragon places students in work placements. It does not teach, so **do not collect course or teaching content**. What Paragon needs is *who the institution already works with*, because a caller can then say: "you already collaborate with X; we would like to receive your students for work placements."

Universities collaborate to exchange staff and students in two steps: (1) students study at a partner university, then (2) students work at a partner institution. Universities may not care about placements themselves, but their **network** is the opportunity.

## Two kinds of information

**External collaboration** = other institutions or networks the institute cooperates with. Look for pages titled *partner universities, Erasmus+ partners, inter-institutional agreements, international cooperation, exchange partners, strategic partners, alliances/networks (e.g. European University alliances), bilateral agreements*. Local terms: *convenios / universidades socias, universités partenaires / accords, Partneruniversitäten / Kooperationen, uczelnie partnerskie, partnerské univerzity, partnerintézmények, universidades parceiras*. Often a table or a PDF.

A page also often shows a row/grid of **membership or accreditation logos** (e.g. "Members of," "Accredited by," "Affiliated with," "International associations/networks") - these now come through as bracketed names like `[EAEC]`, since the fetcher reads image alt text. Treat each one as its own external-collaboration row, `partner_type: "network"` - these are real, valuable international-network data, not noise to skip.

**Internal collaboration** = the institute's **own campuses** in other cities or countries and the cooperation between them. Look for *campuses, locations, branches, international campus, our sites*. Only count campuses that belong to this institution (not partners).

## Input (in the prompt)
`row_id`, institution name, `official_website`, `institution_type`, country.

## How to work
1. Start from `official_website` (already found). Use WebSearch only to find the partner/campus pages if the site's own menu does not lead there.
2. Fetch pages with `python scraper/fetch_pages.py <url> --links` (caches, obeys robots.txt, rate-limits, reads PDFs). **Use only this command to read pages; do not use WebFetch.** Long lists and PDFs: continue with `--offset`. Partner lists are often PDFs linked from the international office page: try them. **Read at most 15 pages.** Do not crawl the site.
   Each partner's `evidence_quote` should be the line naming that partner, not a generic sentence about the list.
3. For each **external** partner found record: `partner_name`, `partner_country`, `partner_type` (`university|college|school|company|network|unknown`), `mobility_type` (`study` for student study exchange, `traineeship` for student work placements, `staff` for staff mobility, `unknown`), `source_url`, `evidence_quote`, `confidence`.
   - For a `network`-type membership covering many countries (e.g. a pan-European association), leave `partner_country` **blank** rather than guessing one country for it - the pipeline classifies a blank-country network as international automatically. Do not judge "international vs domestic" yourself for anything else either; that's computed afterward from the countries you record, not from your own assessment.
   - If a list is long, capture **up to 150 partners** and set `truncated: true` with `total_seen`.
   - When the list gives Erasmus+ codes (e.g. SMS = study, SMT = traineeship, STA/STT = staff), use them for `mobility_type`.
4. For each **internal** campus record: `campus_name`, `country`, `city`, `role` (`main|branch|international`), `source_url`, `evidence_quote`.
5. `multiplier_hook`: ONE sentence, only if you captured at least one external partner with evidence, in the form
   "Your institution already collaborates with {partner_name} ({partner_country})."
   Pick the highest-confidence partner. Leave it `""` if there is none. Never claim anything about Paragon's own relationship with a partner.

## Rules (non-negotiable)
- **No invented rows.** If the institute publishes no partner list or campus list, return empty arrays and say so in `coverage_note`. Schools often publish nothing; that is a valid result.
- Every row needs `source_url` and a short `evidence_quote` copied from the page.
- Institutional information only. Do not collect names of individual staff here.
- Confidence: 0.9+ = clearly listed as a partner/campus of this institution on its own site; 0.6-0.8 = listed but ambiguous (old list, unclear type); below 0.6 = inferred.

## Output
Return ONLY one JSON object (no prose, no code fence):

```json
{
  "row_id": "r020",
  "external_collaboration": [
    {"partner_name": "...", "partner_country": "...", "partner_type": "university",
     "mobility_type": "study", "source_url": "...", "evidence_quote": "...", "confidence": 0.9}
  ],
  "internal_collaboration": [
    {"campus_name": "...", "country": "...", "city": "...", "role": "branch",
     "source_url": "...", "evidence_quote": "..."}
  ],
  "multiplier_hook": "",
  "truncated": false,
  "total_seen": 0,
  "pages_read": 0,
  "coverage_note": "what was found, what was not, and why",
  "needs_human_check": false
}
```
