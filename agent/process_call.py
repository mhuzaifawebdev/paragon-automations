"""Turn one finished Vapi call into a saved transcript, a 10-point score, and a
classified outcome, and update the campaign state so the dialer loop knows what
happened. This is what runs on each file in data/calls/inbox/ (dropped there by
the Cloudflare Worker webhook receiver - see webhook/worker.js), driven by
.github/workflows/process-calls.yml on a schedule.

    python agent/process_call.py data/calls/inbox/<call_id>.json
    python agent/process_call.py --all                  # process every file in inbox/

Runs pgi-score-call and classify-outcome as real Claude Code skills via `claude -p`
(same pattern as scraper/run_batch.py). data/classify_outcome.py's tested rule -
never the model - makes the final outcome decision.
"""
import argparse
import csv
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(ROOT / "data"))
import state as st  # noqa: E402
from classify_outcome import classify  # noqa: E402

INBOX = ROOT / "data" / "calls" / "inbox"
CALLS_DIR = ROOT / "data" / "calls"
RESULTS_DIR = ROOT / "data" / "results"
MODEL = "claude-sonnet-5"
CALL_TIMEOUT = 300


def call_claude(prompt, attempts=3):
    import time
    cmd = ["claude", "-p", prompt, "--model", MODEL, "--output-format", "json",
           "--allowedTools", "Read"]
    last_err = None
    for attempt in range(1, attempts + 1):
        try:
            p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=CALL_TIMEOUT)
        except subprocess.TimeoutExpired:
            last_err = RuntimeError("claude timed out"); time.sleep(5 * attempt); continue
        if p.returncode != 0:
            last_err = RuntimeError(f"claude exited {p.returncode}: {(p.stderr or p.stdout)[:1500]}")
            time.sleep(5 * attempt); continue
        try:
            envelope = json.loads(p.stdout)
        except json.JSONDecodeError:
            last_err = RuntimeError(f"non-JSON output: {p.stdout[:1500]}"); time.sleep(5 * attempt); continue
        if envelope.get("is_error"):
            last_err = RuntimeError(f"claude error: {str(envelope.get('result'))[:800]}")
            time.sleep(5 * attempt); continue
        return envelope.get("result", "")
    raise last_err


def parse_json(text):
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in reply: " + text[:300])
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc: esc = False
            elif c == "\\": esc = True
            elif c == '"': in_str = False
        elif c == '"': in_str = True
        elif c == "{": depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise ValueError("unterminated JSON object")


PLACED_LOG = ROOT / "data" / "calls" / "placed.csv"


def _placed_lookup(call_id):
    """agent/vapi_call.py logs call_id -> row_id locally the instant a call is placed - this
    does not depend on any webhook field surviving intact, only on Vapi echoing back the same
    call id it issued (which it always does), so it is checked first."""
    if not call_id or not PLACED_LOG.exists():
        return None
    with open(PLACED_LOG, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["call_id"] == call_id:
                return r["row_id"]
    return None


def extract_row_id(payload):
    """Three ways, most reliable first, since webhook field paths can shift between API
    versions and none of this was verified against a live payload before launch (flagged in
    the training workflow, step 5):
      1. our own placed.csv log, keyed by the call id Vapi itself issued (see vapi_call.place_call)
      2. Vapi's documented message.call.metadata.row_id
      3. matching the dialled number against campaign_state.csv"""
    msg = payload.get("message", payload)
    call = msg.get("call") or {}
    call_id = call.get("id")
    row_id = _placed_lookup(call_id)
    if row_id:
        return row_id, "placed_log"
    row_id = (call.get("metadata") or {}).get("row_id")
    if row_id:
        return row_id, "metadata"
    number = ((call.get("customer") or {}).get("number") or "").strip()
    if number:
        digits = re.sub(r"\D", "", number)
        for r in st.load().values():
            if re.sub(r"\D", "", r["current_contact_phone"]) == digits:
                return r["row_id"], "phone_match"
    return None, "unresolved"


def extract_transcript(payload):
    msg = payload.get("message", payload)
    artifact = msg.get("artifact") or {}
    transcript = artifact.get("transcript")
    if not transcript and artifact.get("messages"):
        transcript = "\n".join(f"{m.get('role')}: {m.get('message')}" for m in artifact["messages"])
    ended_reason = msg.get("endedReason", "")
    return transcript or "", ended_reason


def process_one(path):
    payload = json.loads(path.read_text(encoding="utf-8"))
    row_id, how = extract_row_id(payload)
    transcript, ended_reason = extract_transcript(payload)
    call_id = ((payload.get("message", payload).get("call")) or {}).get("id", path.stem)

    if row_id is None:
        review_dir = ROOT / "data" / "calls" / "needs_review"
        review_dir.mkdir(parents=True, exist_ok=True)
        path.rename(review_dir / path.name)
        print(f"{path.name}: could not map to a row_id (matched by: {how}) - moved to needs_review/")
        return

    CALLS_DIR.mkdir(parents=True, exist_ok=True)
    (CALLS_DIR / f"{call_id}.json").write_text(
        json.dumps({"call_id": call_id, "row_id": row_id, "transcript": transcript,
                    "ended_reason": ended_reason, "raw": payload}, ensure_ascii=False, indent=1),
        encoding="utf-8")

    answered_lengthwise = len(transcript.strip()) > 40   # a real exchange happened, not just ringing/voicemail click
    if not answered_lengthwise:
        score, outcome_facts, label = None, {"answered": False}, "none"
    else:
        score = parse_json(call_claude(
            f"Use the pgi-score-call skill on this transcript and reply with the JSON object only.\n\n"
            f"call_id: {call_id}\n\n{transcript}"))
        outcome_facts = parse_json(call_claude(
            f"Use the classify-outcome skill on this transcript and reply with the JSON object only.\n\n"
            f"call_id: {call_id}\n\n{transcript}"))
        label = classify(outcome_facts.get("interested", False),
                         outcome_facts.get("student_arrival_months"),
                         outcome_facts.get("meeting_booked", False))

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    result = {"call_id": call_id, "row_id": row_id, "answered": outcome_facts.get("answered", answered_lengthwise),
               "ended_reason": ended_reason, "score": score, "outcome_facts": outcome_facts, "outcome": label}
    (RESULTS_DIR / f"{call_id}.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

    state = st.load()
    if row_id in state:
        st.record_call_result(state[row_id], label, result["answered"])
        st.save(state)

    path.unlink()   # processed - remove from inbox
    print(f"{call_id} ({row_id}, mapped by {how}): answered={result['answered']} outcome={label} "
          f"score={(score or {}).get('total')}")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", nargs="?", help="one inbox JSON file")
    ap.add_argument("--all", action="store_true", help="process every file in data/calls/inbox/")
    a = ap.parse_args()

    if a.all:
        files = sorted(INBOX.glob("*.json"))
        if not files:
            print("inbox is empty - nothing to process")
        for f in files:
            try:
                process_one(f)
            except Exception as e:
                print(f"{f.name}: FAILED: {e}", file=sys.stderr)
    elif a.path:
        process_one(Path(a.path))
    else:
        ap.error("give a file path or --all")


if __name__ == "__main__":
    main()
