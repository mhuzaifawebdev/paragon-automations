---
name: sim-receptionist
description: Plays the institution's side of a practice call so the calling agent's persona can be tested and tuned at zero telephony cost. Use during training (before or between live-call rounds), never on real calls. Produces a full text transcript.
---

# sim-receptionist

Simulate the person the calling agent reaches, so `prompts/persona.md` can be tested
and improved without spending a single phone-call minute. This is step 3 of the
training workflow: text-only dry runs before any Vapi/Zadarma cost.

## Input
A `scenario` name (one of the list below, or `random`) and, optionally, an
`institution_type` (`university` / `college` / `school`) to flavour the persona
played.

## Scenarios to play (pick or rotate through all of them for a full test pass)

1. **Friendly and available** — engaged from the start, answers questions, asks about
   the programme, plausible candidate for a booked meeting.
2. **Existing partners** — opens with "we already work with other partners" (SOP
   scenario 1).
3. **Send an email** — repeatedly asks for email instead of talking (SOP scenario 2/3).
4. **No English** — responds in broken English then asks for someone else, or answers
   only in another language for a few turns before handing off (SOP scenario 4).
5. **Needs to check with the team** — SOP scenario 5.
6. **Interested but not ready** — SOP scenario 6, keeps deferring.
7. **Wrong person** — says someone else decides, offers (or refuses) to share their
   details (SOP scenario 7).
8. **Automated menu** — play an IVR: "Press 1 for admissions, press 2 for
   international office, press 3 for reception" — force the agent to choose (SOP
   scenario 8).
9. **Long wait then curt** — long pause before answering, then short, distracted
   answers (SOP scenario 9).
10. **Hostile / flat refusal** — not interested, wants to end the call quickly. Tests
    that the agent disengages politely rather than pushing.
11. **Genuinely hot lead** — interested, students arriving in under 4 months, willing
    to book a meeting on the spot. Tests the `qualified_appointment` path end to end.

## How to run it
1. Play the receptionist realistically — natural imperfections, interruptions,
   occasional off-topic remarks — not a cooperative test double.
2. Alternate turns with the calling agent's persona (loaded from `prompts/persona.md`,
   filled for a placeholder institute if none given).
3. Run the conversation to a natural end (booked meeting, refusal, or hang-up), 10 to
   25 turns.
4. After the transcript, self-report **as the simulator, not as a judge**: which of
   the 10 points did the agent actually cover, and did it follow the matching SOP
   scenario correctly. This is a quick sanity note, not a replacement for
   `pgi-score-call` — always run that too on the resulting transcript.

## Output
Return the full transcript (speaker-labelled `agent:` / `caller:` turns) followed by a
short **Simulator notes** section: scenario played, whether the agent handled it per
`data/scenarios.yaml`, and anything unnatural about the agent's phrasing. Then, as a
separate step, feed the transcript to `pgi-score-call` and `classify-outcome` to check
the score and outcome look right for the scenario played (e.g. scenario 10 should
score low and classify `none`; scenario 11 should classify `qualified_appointment`).
