"""One judgement call over an evidence pack, through a switchable provider.

The model receives numbered lines and answers with LINE NUMBERS (see evidence.py), so it
cannot invent a quote, email or phone. Providers (config.yaml `scraper.provider`):

  claude_api   Anthropic Messages API, forced tool call = schema-valid JSON. Needs ANTHROPIC_API_KEY.
               This is the one for GitHub Actions. Default model: Haiku 4.5.
  claude_cli   One no-tools `claude -p` call on the user's Claude plan. Local use. Hooks/plugins/
               default system prompt are switched off: ~2-4 s and ~$0.003 overhead instead of ~14 s
               and ~$0.08 (measured).
  gemini       Google Gemini REST, JSON response schema. Free tier. NOT tested live (no key yet).
  rules        No model at all; pipeline falls back to scraper2/rank.py.
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROLE_CLASSES = ["institutional_erasmus_coordinator", "international_relations_head",
                "international_relations_officer", "mobility_coordinator", "vice_rector_international",
                "faculty_coordinator", "principal_or_director", "secretary_or_admin", "other"]


def _int():
    return {"type": "integer"}


def _str():
    return {"type": "string"}


SCHEMA = {
    "type": "object",
    "properties": {
        "institution_type": {"type": "string", "enum": ["university", "college", "school", "unknown"]},
        "country": {"type": "object", "properties": {"name": _str(), "line": _int()}, "required": ["name", "line"]},
        "contacts": {"type": "array", "items": {"type": "object", "properties": {
            "name": _str(), "designation_english": _str(),
            "role_class": {"type": "string", "enum": ROLE_CLASSES},
            "name_line": _int(), "role_line": _int(), "email_line": _int(), "phone_line": _int(), "why": _str()},
            "required": ["name", "designation_english", "role_class", "name_line", "role_line",
                         "email_line", "phone_line", "why"]}},
        "partners": {"type": "array", "items": {"type": "object", "properties": {
            "line": _int(), "name": _str(), "country": _str(),
            "type": {"type": "string", "enum": ["university", "college", "school", "company", "network", "other"]},
            "mobility": {"type": "string", "enum": ["study", "traineeship", "staff", "unknown"]}},
            "required": ["line", "name", "country", "type", "mobility"]}},
        "partners_stated_total": {"type": "object", "properties": {"value": _int(), "line": _int()},
                                  "required": ["value", "line"]},
        "campuses": {"type": "array", "items": {"type": "object", "properties": {
            "line": _int(), "name": _str(), "city": _str(), "country": _str(),
            "role": {"type": "string", "enum": ["main", "branch", "international"]}},
            "required": ["line", "name", "city", "country", "role"]}},
        "coverage_note": _str(),
    },
    "required": ["institution_type", "country", "contacts", "partners", "partners_stated_total", "campuses",
                 "coverage_note"],
}

SCHEMA_PARTNERS = {"type": "object", "properties": {
    "partners": SCHEMA["properties"]["partners"], "partners_stated_total": SCHEMA["properties"]["partners_stated_total"]},
    "required": ["partners", "partners_stated_total"]}

SYSTEM = (
    "You extract facts about ONE educational institution from numbered evidence lines. "
    "You never write quotes, emails or phone numbers yourself: you only give LINE NUMBERS (the number after "
    "'L', e.g. L17 -> 17) and the code copies the text from those lines. Use 0 whenever a line does not "
    "exist. Never use knowledge from outside the evidence. If the evidence does not show something, leave "
    "it out. Answer only with the requested JSON."
)

RULES = """TASK. From the evidence below return:
1. institution_type: university (awards degrees), college (vocational/professional/further education), school (primary/secondary), or unknown. country: name + the line that shows it (an address or the country named).
2. contacts (up to {NCONTACTS}, best first): the people to call about Erasmus+ / international mobility at THIS institution. Rank by relevance to Erasmus+ partnerships:
   institutional_erasmus_coordinator > international_relations_head > international_relations_officer > mobility_coordinator > vice_rector_international > faculty_coordinator > principal_or_director > secretary_or_admin.
   Prefer a NAMED person who has their own email on the same page as their name. Prefer institution-level roles over faculty/department roles.
   If no Erasmus/international person exists you MUST still return the principal/director and any named secretary/administrator (role_class principal_or_director / secretary_or_admin), best first. Return an empty list ONLY if no named person with a role appears anywhere in the evidence.
   {ONFILE_RULE}
   A person qualifies only if a line STATES their role or job title. A name on its own (a byline, a caption, a date line) is not a contact.
   name_line = the line containing the person's name; role_line = the line stating their role (may be the same line); email_line / phone_line = a line on the SAME page containing their own email / phone (0 if none). designation_english = their role in English; why = one plain-English sentence on why this person is the right contact.
3. partners: every institution or network this institution says it partners/cooperates with (Erasmus partners, agreements, alliances, networks). One entry per partner per line (if one line names several, give several entries with the same line). Do not list the institution itself. mobility = study / traineeship / staff ONLY if the line says so, otherwise unknown. Capture ALL of them, do not stop early.
4. partners_stated_total: if a line states the total number of partners/agreements (e.g. '100 agreements in 34 countries'), give value and line; else value 0, line 0.
5. campuses: the institution's OWN campuses or sites (not partners), with city and country, role main/branch/international.
6. coverage_note: one or two sentences on what is missing (for example a partner list that is not in the evidence)."""


ONFILE_RULE = ("If the CONTACT CURRENTLY ON FILE (the client's operators spoke to them) appears in the evidence in any "
               "spelling, include that person with their best role line.")
ALT_RULE = ("The CONTACT CURRENTLY ON FILE does NOT answer calls. Find OTHER people the operators can call instead: rank the "
            "best DIFFERENT people first (a different department, or a colleague in the same office, is welcome). Do not "
            "list the on-file person unless nobody else with a stated role appears in the evidence.")

RULES_PARTNERS ="""TASK. In the evidence below (one slice of a longer page) list EVERY institution or network this institution says it partners/cooperates with (Erasmus partners, agreements, alliances, networks, project partners). Give one entry per partner per line (several entries may share a line). Do not list the institution itself. mobility = study / traineeship / staff ONLY if the line says so, otherwise unknown. Go through the slice line by line and do not stop early: completeness matters more than brevity. If a line states the total number of partners/agreements, give it in partners_stated_total (else value 0, line 0). Return nothing that is not in this slice."""


class LLMError(Exception):
    pass


def build_user_partners(institute, window):
    return (f"INSTITUTION: {institute.get('name') or '(name unknown)'}\n\n" + RULES_PARTNERS +
            "\n\nEVIDENCE SLICE (numbered lines):\n" + window.prompt_text())


def build_user(institute, pack):
    head = (f"INSTITUTION: {institute.get('name') or '(name unknown)'}\nCOUNTRY (from the client's sheet): "
            f"{institute.get('country') or 'unknown'}\nPHONE (from the client's sheet): {institute.get('phone') or ''}\n"
            f"CONTACT CURRENTLY ON FILE: {institute.get('contact') or 'none'}\n\n")
    if institute.get("alternatives"):
        rules = RULES.replace("{NCONTACTS}", "5").replace("{ONFILE_RULE}", ALT_RULE)
    else:
        rules = RULES.replace("{NCONTACTS}", "3").replace("{ONFILE_RULE}", ONFILE_RULE)
    return head + rules + "\n\nEVIDENCE (numbered lines, grouped by page):\n" + pack.prompt_text()


PRICES = {  # USD per million tokens (input, output); public list prices, used only for the cost ESTIMATE
    "claude-haiku-4-5-20251001": (1.0, 5.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-sonnet-5": (3.0, 15.0),
}


def _post(url, headers, body, timeout, retries=3):
    data = json.dumps(body).encode("utf-8")
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **headers})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "replace")[:400]
            if e.code in (429, 500, 502, 503, 529) and attempt < retries:
                time.sleep(min(3 * 2 ** attempt, 30))          # overload spikes clear in seconds; back off, do not hammer
                continue
            raise LLMError(f"HTTP {e.code}: {msg}")
        except Exception as e:
            if attempt < retries:
                time.sleep(2 * attempt)
                continue
            raise LLMError(f"{type(e).__name__}: {e}")


def _claude_api(system, user, model, timeout, schema=None):
    schema = schema or SCHEMA
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise LLMError("ANTHROPIC_API_KEY is not set")
    t0 = time.time()
    resp = _post("https://api.anthropic.com/v1/messages",
                 {"x-api-key": key, "anthropic-version": "2023-06-01"},
                 {"model": model, "max_tokens": 16000, "system": system,
                  "tools": [{"name": "submit_facts", "description": "Submit the extracted facts.", "input_schema": schema}],
                  "tool_choice": {"type": "tool", "name": "submit_facts"},
                  "messages": [{"role": "user", "content": user}]}, timeout)
    block = next((b for b in resp.get("content", []) if b.get("type") == "tool_use"), None)
    if not block:
        raise LLMError("no tool_use block in the reply")
    u = resp.get("usage", {})
    pin, pout = PRICES.get(model, (3.0, 15.0))
    cost = (u.get("input_tokens", 0) * pin + u.get("output_tokens", 0) * pout) / 1e6
    return block["input"], {"seconds": round(time.time() - t0, 1), "cost_usd_estimate": round(cost, 4),
                            "in_tokens": u.get("input_tokens"), "out_tokens": u.get("output_tokens")}


def _claude_cli(system, user, model, timeout, schema=None):
    schema = schema or SCHEMA
    t0 = time.time()
    cmd = ["claude", "-p", "--model", model, "--tools", "", "--no-session-persistence",
           "--output-format", "json", "--json-schema", json.dumps(schema), "--system-prompt", system,
           "--settings", '{"disableAllHooks":true}', "--strict-mcp-config", "--setting-sources", "project"]
    # extended thinking is switched off: measured 35-80 s per call with it on vs ~6-13 s with it off
    env = {**os.environ, "MAX_THINKING_TOKENS": "0"}
    try:
        p = subprocess.run(cmd, input=user, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, env=env)   # prompt goes through stdin: too long for a Windows command line
    except subprocess.TimeoutExpired:
        raise LLMError(f"claude timed out after {timeout}s")
    try:
        env = json.loads(p.stdout)
    except json.JSONDecodeError:
        raise LLMError(f"claude gave non-JSON output: {(p.stdout or p.stderr)[:300]}")
    if env.get("is_error") or not isinstance(env.get("structured_output"), dict):
        raise LLMError(f"claude error: {str(env.get('result'))[:300]}")
    return env["structured_output"], {"seconds": round(time.time() - t0, 1),
                                      "cost_usd_estimate": round(env.get("total_cost_usd") or 0, 4),
                                      "in_tokens": (env.get("usage") or {}).get("input_tokens"),
                                      "out_tokens": (env.get("usage") or {}).get("output_tokens")}


GEMINI_FALLBACKS = ["gemini-3.5-flash-lite", "gemini-3.5-flash"]     # tried when the chosen model is overloaded
GEMINI_REST_SECONDS = 120                                             # a per-minute limit clears well inside this
_GEMINI_RESTING = {}                                                  # model -> time until which it is skipped

# The paid safety net behind the free reader. Off unless a batch switches it on, and it never runs without a
# spending cap: it is used only when every Gemini model has just refused, and each call is counted against
# the batch's cap (discover.BUDGET), shared with the paid web searches.
FALLBACK = {"enabled": False, "model": "claude-haiku-4-5"}
_fallback_said = {"until": 0.0}


def _paid_fallback(system, user, timeout, schema, why):
    """One Claude Haiku call in place of a Gemini call that could not be made. Returns None when it must not or
    cannot be used (switched off, no key, no cap set, cap reached, or the call itself failed)."""
    import discover
    if not (FALLBACK["enabled"] and os.environ.get("ANTHROPIC_API_KEY")
            and discover.BUDGET["limit"] is not None and discover.budget_left()):
        return None
    try:
        data, meta = _claude_api(system, user, FALLBACK["model"], timeout, schema)
    except LLMError as e:
        print(f"(Claude Haiku fallback failed: {str(e)[:160]})", file=sys.stderr, flush=True)
        return None
    discover.record_reader(meta.get("in_tokens") or 0, meta.get("out_tokens") or 0)
    if _fallback_said["until"] <= time.time():
        _fallback_said["until"] = time.time() + GEMINI_REST_SECONDS
        print(f"(Gemini unavailable: {why} - Claude Haiku is reading instead; spent ${discover.spent_usd():.2f} "
              f"of ${discover.BUDGET['limit']:.2f})", file=sys.stderr, flush=True)
    # the cost is counted once, in the batch's paid total, so it is not repeated in the per-call estimate
    return data, {**meta, "cost_usd_estimate": 0.0, "model_used": FALLBACK["model"], "fallback": why}


def _gemini(system, user, model, timeout, schema=None):
    schema = schema or SCHEMA
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise LLMError("GEMINI_API_KEY is not set")
    t0, last = time.time(), None
    models = list(dict.fromkeys([model] + GEMINI_FALLBACKS))
    # A model that answered 429 has run out of free-tier quota: retrying it only burns time (measured: 26 of 33
    # calls refused, each one waiting over a minute before the fallback was tried). Rest it and go straight on.
    awake = [x for x in models if _GEMINI_RESTING.get(x, 0) <= time.time()]
    if not awake:                                     # every Gemini model refused within the last two minutes
        paid = _paid_fallback(system, user, timeout, schema, "out of free quota")
        if paid:
            return paid
    for m in awake or models:
        try:
            resp = _post(f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={key}", {},
                         {"systemInstruction": {"parts": [{"text": system}]},
                          "contents": [{"role": "user", "parts": [{"text": user}]}],
                          "generationConfig": {"responseMimeType": "application/json", "responseJsonSchema": schema,
                                               "temperature": 0}}, timeout, retries=2)
            data = json.loads(resp["candidates"][0]["content"]["parts"][0]["text"])
            return data, {"seconds": round(time.time() - t0, 1), "cost_usd_estimate": 0.0, "model_used": m}
        except LLMError as e:
            last = e
            if "429" in str(e):
                if _GEMINI_RESTING.get(m, 0) <= time.time():
                    print(f"(Gemini {m} is out of quota - using the next model for {GEMINI_REST_SECONDS // 60} min)",
                          file=sys.stderr, flush=True)
                _GEMINI_RESTING[m] = time.time() + GEMINI_REST_SECONDS
            if not any(c in str(e) for c in ("503", "429", "500", "502")):
                raise                                   # a real error (bad key, bad request): do not hide it
        except Exception as e:
            raise LLMError(f"unreadable Gemini reply: {e}")
    paid = _paid_fallback(system, user, timeout, schema, "all Gemini models refused or overloaded")
    if paid:
        return paid
    raise last


PROVIDERS = {"claude_api": _claude_api, "claude_cli": _claude_cli, "gemini": _gemini}
DEFAULT_MODELS = {"claude_api": "claude-sonnet-5", "claude_cli": "claude-sonnet-5",   # Haiku missed contacts on 3 of 3 test sites
                  "gemini": "gemini-3.1-flash-lite"}


def extract_partners(institute, window, provider, model=None, timeout=180):
    """Partners-only call over one slice of the evidence (used so long lists are captured in full)."""
    if provider not in PROVIDERS:
        raise LLMError(f"provider '{provider}' has no model call")
    return PROVIDERS[provider](SYSTEM, build_user_partners(institute, window), model or DEFAULT_MODELS[provider],
                               timeout, SCHEMA_PARTNERS)


def extract(institute, pack, provider, model=None, timeout=180):
    """Returns (data, meta). Raises LLMError; the caller falls back to the rules provider."""
    if provider not in PROVIDERS:
        raise LLMError(f"provider '{provider}' has no model call (use the rules fallback)")
    return PROVIDERS[provider](SYSTEM, build_user(institute, pack), model or DEFAULT_MODELS[provider], timeout)
