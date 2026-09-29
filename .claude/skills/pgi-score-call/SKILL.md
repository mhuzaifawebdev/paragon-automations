---
name: pgi-score-call
description: Scores one finished call transcript against Paragon's 10-point PGI Sales Score Card. Use after a call ends, on its saved transcript. Returns one JSON object with a 0/0.5/1 score and an evidence quote for each of the 10 points.
---

# pgi-score-call

Score a transcript against the 10 points below. This is the only judge of call quality —
it never decides the outcome label (that is `classify-outcome`'s job).

## Input
A transcript (speaker-labelled turns: `agent:` / `caller:`), given directly in the prompt
or as a path to `data/calls/<call_id>.json` to read.

## The 10 points and how to score each

| # | Point | 1 = clearly done | 0.5 = attempted / partial | 0 = not done |
|---|---|---|---|---|
| 1 | Introduced self, PGI, and destination (Malta) | Said who they are, who they represent, and the destination, early in the call | Said some but not all of these | None of it said |
| 2 | Confirmed speaking to the right person | Explicitly checked the person handles Erasmus+ | Asked indirectly or late | Never checked |
| 3 | Stated the approved credentials only | Said the 20+ years / 900+ / 45,000+ figures accurately, no invented figures | Said some of them, or close but imprecise | Not mentioned, or a fabricated number used |
| 4 | Established the reason for the call | Clearly stated: internship opportunities in Malta | Vague or implied only | Not stated |
| 5 | Asked about mobility | Asked about short-term, long-term, AND staff mobility | Asked about only one or two of these | Not asked |
| 6 | Used the brand card | Referenced brand-card talking points to build trust | Attempted but generic | Not used, or skipped because none was provided |
| 7 | Discovered needs | Asked open questions (numbers, target, departments, timelines, challenges) AND followed up on the answer | Asked but did not follow up | Not asked |
| 8 | Used unique selling points / created value | Connected PGI's strengths to what the caller said they needed | Listed generic features, not connected to their needs | Not attempted |
| 9 | Established tangible outcome | Stated a concrete benefit (e.g. placements) and checked it mattered to them | Stated a benefit but did not check it landed | Not stated |
| 10 | Got a clear buy-in | Asked for the meeting with Dr. Nadia and got a date/time or firm yes | Asked but no commitment secured | Not asked, or a flat refusal |

## Rules
- Score from the transcript only. Do not infer intent beyond what was said.
- Every score needs a short `evidence` quote copied from the transcript (or `null` for a 0 with nothing to quote).
- If the call never reached step 1 (no answer, wrong number, disconnected before speech), still return all 10 as 0 with `evidence: null` and set `call_too_short: true` rather than skipping the object.
- Never adjust a score based on the outcome you think the call had — score each point independently.

## Output
Return ONLY this JSON object (no prose, no code fence):

```json
{
  "call_id": "...",
  "points": {"p1": 1, "p2": 1, "p3": 0.5, "p4": 1, "p5": 1, "p6": 0, "p7": 1, "p8": 1, "p9": 0.5, "p10": 0},
  "total": 7.0,
  "evidence": {"p1": "quote", "p3": "quote", "...": "..."},
  "call_too_short": false,
  "notes": "anything a human reviewer should know"
}
```
`total` is the sum of the 10 point values (max 10). Include an `evidence` entry for every point that scored above 0.
