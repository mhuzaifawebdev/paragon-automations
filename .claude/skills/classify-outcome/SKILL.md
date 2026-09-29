---
name: classify-outcome
description: Extracts the 3 facts (interest, student arrival timing, meeting booked) from a finished call transcript. Use after pgi-score-call, on the same transcript. Returns one JSON object — it does NOT pick the outcome label itself, a fixed rule in data/classify_outcome.py does that.
---

# classify-outcome

**You extract facts. You do not choose the outcome label.** The label is decided by a
plain, tested function (`data/classify_outcome.py`) precisely so the same facts always
give the same label. Your only job is reading the transcript correctly.

## Input
A transcript, same as `pgi-score-call` takes.

## Extract exactly these three facts

1. **`interested`** (`true` / `false`): did the contact show genuine interest at any
   point — asked questions, engaged with the pitch, gave a positive signal — as
   opposed to a flat refusal, hang-up, or the call never being answered?
2. **`student_arrival_months`** (number, or `null`): how many months from now the
   contact said students would be available/arrive, converted to a number. "Next
   spring" / "in the autumn" etc. must be resolved to an approximate month count using
   the call's date if given, otherwise leave `null` — never guess a season into a
   number without a stated or inferable date. If a range was given ("4 to 6 months"),
   use the midpoint and note it.
3. **`meeting_booked`** (`true` / `false`): was a meeting with Dr. Nadia **actually
   agreed with a specific date/time or a confirmed way to schedule one** — not just
   "maybe later" or "send me an email".

## Rules
- If the call was never answered, or ended before any real exchange, set all three to
  `false`/`false`/`null` and `answered: false`.
- Quote the transcript for each fact you set to a non-default value.
- Do not round or estimate `student_arrival_months` beyond what the transcript
  actually supports; `null` is correct and expected when it was never discussed —
  this is itself useful signal (the agent should have asked, per persona point 10).

## Output
Return ONLY this JSON object (no prose, no code fence):

```json
{
  "call_id": "...",
  "answered": true,
  "interested": true,
  "interested_evidence": "quote",
  "student_arrival_months": 8,
  "student_arrival_evidence": "quote, or null",
  "meeting_booked": false,
  "meeting_booked_evidence": "quote, or null",
  "notes": "anything ambiguous a human should check"
}
```

## After this skill runs
The caller (a script, not you) passes `interested`, `student_arrival_months` and
`meeting_booked` into `data/classify_outcome.py`'s `classify()` function to get the
final label — one of `qualified_appointment`, `engagement_appointment`,
`follow_up_appointment`, `engagement`, `follow_up`, `none`, `needs_arrival_date`, or
`needs_review`. Do not compute this label yourself even if it looks obvious; the
whole point of separating extraction from classification is that the rule never
drifts between calls.
