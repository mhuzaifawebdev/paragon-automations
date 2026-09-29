"""Live in-call tools the agent can invoke mid-conversation via Vapi's synchronous
tool-calling (message.toolCallList), as distinct from the post-call processing in
process_call.py. Confirmed payload shape (from a working reference n8n integration,
not just docs): Vapi POSTs {"message": {"toolCallList": [{"id", "function": {"name",
"arguments"}}]}} to the tools server URL and expects back
{"results": [{"toolCallId": "...", "result": "<text the agent will speak>"}]}.

Each function here takes (row_id, arguments) and returns the string Vapi should
speak back to the caller. row_id is resolved by the caller (webhook/tools_worker.js
or handle_tool_call below) from the call's metadata, same as process_call.py does.

Run locally with a synthetic payload (no live Vapi needed):
    python agent/tools.py tests/tool_calls/book_meeting_sample.json
"""
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))
import state as st  # noqa: E402

CALLBACKS_PATH = ROOT / "data" / "callback_requests.csv"
ESCALATIONS_PATH = ROOT / "data" / "escalations.csv"
CALLBACK_FIELDS = ["row_id", "requested_at", "callback_datetime", "raw_note"]
ESCALATION_FIELDS = ["row_id", "requested_at", "reason"]


def _append_csv(path, fields, row):
    is_new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if is_new:
            w.writeheader()
        w.writerow(row)


def book_meeting(row_id, args):
    """STUB pending Dr. Nadia's calendar access (open item, developer handover).
    Currently: never confirms a real slot, always returns an honest "cannot
    confirm yet" response, so the agent is never able to claim a meeting is
    booked when nothing was actually booked. Replace the body of this function
    with a real calendar API call once credentials exist - do not change what
    it returns for the "not wired up" case without also updating persona.md's
    matching instruction ("never claim a meeting is booked unless the booking
    tool actually confirmed a slot")."""
    date, time = args.get("date"), args.get("time")
    # TODO: replace with a real call to Dr. Nadia's calendar (Google Calendar API,
    # Calendly, or whatever the client confirms) once credentials are available.
    # On success this should return a confirmation phrase and separately persist
    # the confirmed slot (a booked-meetings CSV, mirroring campaign_state.csv)
    # so classify-outcome's meeting_booked fact can eventually be verified against
    # it rather than only read from the transcript.
    return (f"I'm not able to confirm a specific slot on this call yet - "
            f"I've noted {date or 'the date'} {time or ''} as a request and someone "
            f"from our team will confirm it with you shortly.")


def get_institute_context(row_id, args):
    """Live lookup, in case escalation changed the contact after the static
    system prompt was built for this call."""
    state = st.load()
    row = state.get(row_id)
    if not row:
        return "No additional context is available for this institute."
    bits = [f"Current contact on file: {row['current_contact_name'] or 'unknown'}."]
    if row.get("escalation_level") and int(row["escalation_level"]) > 0:
        bits.append(f"This is contact attempt {int(row['escalation_level']) + 1} at this institute.")
    return " ".join(bits)


def log_callback_request(row_id, args):
    when = args.get("callback_datetime") or args.get("date") or args.get("time") or "unspecified"
    _append_csv(CALLBACKS_PATH, CALLBACK_FIELDS, {
        "row_id": row_id,
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "callback_datetime": when,
        "raw_note": json.dumps(args, ensure_ascii=False),
    })
    return f"Understood, I've logged a callback request for {when}."


def escalate_to_human(row_id, args):
    reason = args.get("reason", "not specified")
    _append_csv(ESCALATIONS_PATH, ESCALATION_FIELDS, {
        "row_id": row_id,
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
    })
    return "Thank you, I'll have a member of our team follow up with you directly on this."


TOOLS = {
    "book_meeting": book_meeting,
    "get_institute_context": get_institute_context,
    "log_callback_request": log_callback_request,
    "escalate_to_human": escalate_to_human,
}


def resolve_row_id(call_obj):
    """Same resolution order as process_call.py's extract_row_id: metadata first,
    then match the dialled number against campaign state."""
    row_id = ((call_obj or {}).get("metadata") or {}).get("row_id")
    if row_id:
        return row_id
    import re
    number = ((call_obj or {}).get("customer") or {}).get("number", "")
    digits = re.sub(r"\D", "", number)
    if digits:
        for r in st.load().values():
            if re.sub(r"\D", "", r["current_contact_phone"]) == digits:
                return r["row_id"]
    return None


def handle_tool_call_list(payload):
    """payload is the full Vapi webhook body (or its "message" sub-object).
    Returns the exact {"results": [...]} envelope Vapi expects back."""
    msg = payload.get("message", payload)
    call = msg.get("call") or {}
    row_id = resolve_row_id(call)
    tool_calls = msg.get("toolCallList") or []

    results = []
    for tc in tool_calls:
        call_id = tc.get("id")
        fn = tc.get("function") or {}
        name = fn.get("name")
        raw_args = fn.get("arguments") or {}
        args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args

        handler = TOOLS.get(name)
        if row_id is None:
            text = "I'm unable to look that up right now - I'll have someone follow up."
        elif handler is None:
            text = f"[unknown tool {name!r} - not handled]"
        else:
            try:
                text = handler(row_id, args)
            except Exception as e:
                text = "I'm unable to complete that right now - I'll have someone follow up."
                print(f"tool {name} failed for {row_id}: {e}", file=sys.stderr)
        results.append({"toolCallId": call_id, "result": text})

    return {"results": results}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: python {sys.argv[0]} <path-to-sample-toolCallList-payload.json>")
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    print(json.dumps(handle_tool_call_list(payload), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
