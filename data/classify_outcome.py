"""Deterministic outcome classification from the Erasmus Calling SOP.

The language model only EXTRACTS three facts from the transcript:
    interested              bool   - the contact showed interest
    student_arrival_months  float  - months until the expected student arrival (None if unknown)
    meeting_booked          bool   - a meeting with Dr. Nadia was actually booked
This function turns those facts into the label. The model never picks the label.

Rules (SOP "Interaction Classification"):
    booked  + arrival < 4 months      -> qualified_appointment
    booked  + arrival 4-6 months      -> engagement_appointment
    booked  + arrival > 6 months      -> follow_up_appointment
    no meet + arrival 4-6 months      -> engagement
    no meet + arrival > 6 months      -> follow_up
Boundary handling (the SOP table leaves 4 and 6 exactly ambiguous): "less than 4" is
strict, "4-6" is inclusive of both ends, "more than 6" is strict. Confirm with the client.

Cases the SOP does not cover return a flagged label rather than guessing:
    not interested            -> none
    interested, unknown date  -> needs_arrival_date (agent must ask; human review otherwise)
    no meeting, arrival < 4   -> needs_review (interested, imminent, but no meeting: a hot lead
                                 the SOP table has no row for)
"""

NONE = "none"
NEEDS_ARRIVAL_DATE = "needs_arrival_date"
NEEDS_REVIEW = "needs_review"


def classify(interested, student_arrival_months, meeting_booked):
    if not interested:
        return NONE
    if student_arrival_months is None:
        return NEEDS_ARRIVAL_DATE

    m = student_arrival_months
    if meeting_booked:
        if m < 4:
            return "qualified_appointment"
        if m <= 6:
            return "engagement_appointment"
        return "follow_up_appointment"

    if m < 4:
        return NEEDS_REVIEW
    if m <= 6:
        return "engagement"
    return "follow_up"


if __name__ == "__main__":
    # (interested, months, booked) -> expected. First five rows are the SOP table.
    cases = [
        ((True, 8, False), "follow_up"),
        ((True, 5, False), "engagement"),
        ((True, 3, True), "qualified_appointment"),
        ((True, 5, True), "engagement_appointment"),
        ((True, 8, True), "follow_up_appointment"),
        # edges called out by the SOP key rule: booked but arrival not within 4 months is NOT qualified
        ((True, 4, True), "engagement_appointment"),
        ((True, 6, True), "engagement_appointment"),
        ((True, 6.5, True), "follow_up_appointment"),
        ((True, 3.9, True), "qualified_appointment"),
        # cases outside the table
        ((False, 2, True), NONE),
        ((True, None, False), NEEDS_ARRIVAL_DATE),
        ((True, 2, False), NEEDS_REVIEW),
    ]
    bad = 0
    for args, want in cases:
        got = classify(*args)
        flag = "ok " if got == want else "FAIL"
        bad += got != want
        print(f"{flag} classify{args} -> {got} (want {want})")
    raise SystemExit(1 if bad else 0)
