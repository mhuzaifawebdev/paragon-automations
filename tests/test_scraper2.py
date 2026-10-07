"""Offline tests for scraper v2 (no network, no model). Run: python tests/test_scraper2.py

Each block pins down a real failure found while building and measuring v2, so it cannot come back.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scraper2"))
sys.path.insert(0, str(ROOT / "scraper"))
import crawl  # noqa: E402
import evidence  # noqa: E402
import fetch_pages as fp  # noqa: E402
import pipeline as pl  # noqa: E402
import verify  # noqa: E402

results = []


def check(name, got, want):
    ok = got == want
    results.append(ok)
    print(("ok   " if ok else "FAIL ") + f"{name}" + ("" if ok else f": got {got!r}, want {want!r}"))


# ---- fetcher fixes ----
check("non-ASCII path is percent-encoded (was a crash on /uber-uns/ style pages)",
      fp.iri_to_uri("https://www.ptsperg.at/über-uns/lehrkräfte/"), "https://www.ptsperg.at/%C3%BCber-uns/lehrkr%C3%A4fte/")
check("already-encoded URL is left alone", fp.iri_to_uri("https://x.org/a%20b/c"), "https://x.org/a%20b/c")
check("Cloudflare-hidden email decodes",
      fp.cf_decode("0a696566666f6f6424666f6d786b646e4a6364796b27697c66246c78"), "colleen.legrand@insa-cvl.fr")
html = b'<p>Mail <a href="/cdn-cgi/l/email-protection#0a696566666f6f6424666f6d786b646e4a6364796b27697c66246c78">[email\xc2\xa0protected]</a></p>'
check("hidden email appears in page text", "colleen.legrand@insa-cvl.fr" in fp._html_to_text(html, "utf-8", "https://x.fr/")[0], True)

logo_html = b'<p>Members of <a href="https://eaec.info"><img alt="EAEC" src="eaec.png"></a></p>'
check("logo-only membership link alt text appears in page text",
      "[EAEC]" in fp._html_to_text(logo_html, "utf-8", "https://x.fr/")[0], True)
check("generic alt text is not injected as noise",
      "[logo]" in fp._html_to_text(b'<img alt="logo" src="x.png">', "utf-8", "https://x.fr/")[0], False)

# ---- evidence pack: numbered lines, dedupe, budget ----
pages = [{"url": "https://a.org/", "text": "Menu\nHome\nWelcome to the school\nMenu", "score": 100},
         {"url": "https://a.org/staff", "text": "Menu\nJane Doe\nErasmus coordinator\njane@a.org", "score": 10}]
pack = evidence.build_pack(pages)
texts = [l["text"] for l in pack.lines]
check("repeated menu lines are kept once", texts.count("Menu"), 1)
check("lines are numbered from 1 in order", [l["n"] for l in pack.lines], list(range(1, len(pack.lines) + 1)))
check("a line number resolves to its text and page", (pack.get(3)["text"], pack.get(3)["url"]) if pack.get(3) else None,
      ("Welcome to the school", "https://a.org/") if pack.get(3) else None)
check("out-of-range / zero line numbers resolve to nothing", (pack.get(0), pack.get(999), pack.get(None)), (None, None, None))
check("a glued duplicate address is repaired (a@x.atA.B -> a@x.at)", evidence.clean_email("Marion.Hartlauer@schule-ooe.atMarion.Hartlauer"), "Marion.Hartlauer@schule-ooe.at")
check("the fetcher's <mailto> marker wins over a glued display string",
      evidence.emails_in("E-Mail: Tracey.Giessauf@schule-ooe.atTracey.Giessauf <Tracey.Giessauf@schule-ooe.at>"), ["Tracey.Giessauf@schule-ooe.at"])
check("a normal address is untouched", evidence.emails_in("info@poli.hu, secretary"), ["info@poli.hu"])
check("email is read from the line, never invented", evidence.first_email("write to jane@a.org today"), "jane@a.org")

# ---- contact resolution: the model only points at lines ----
pack2 = evidence.Pack()
for u, t in [("https://a.org/staff", "Kovács Ágnes"), ("https://a.org/staff", "Pályazati ügyintéző"),
             ("https://a.org/staff", "agnes.kovacs@a.org"), ("https://a.org/news", "Rob Dawson - 2026. szeptember 9."),
             ("https://a.org/staff", "titkár"), ("https://a.org/staff", "Contact us: info@a.org")]:
    pack2.add(u, t)
data = {"contacts": [
    {"name": "Rob Dawson", "designation_english": "Mobility coordinator", "role_class": "mobility_coordinator", "name_line": 4,
     "role_line": 4, "email_line": 0, "phone_line": 0, "why": "author of the Erasmus posts"},
    {"name": "titkár", "designation_english": "Secretary", "role_class": "secretary_or_admin", "name_line": 5,
     "role_line": 5, "email_line": 6, "phone_line": 0, "why": "office"},
    {"name": "Kovács Ágnes", "designation_english": "Grants officer", "role_class": "international_relations_officer",
     "name_line": 1, "role_line": 2, "email_line": 3, "phone_line": 0, "why": "grants officer"},
]}
got = pl.resolve_contacts(data, pack2)
check("byline with no stated role is rejected (Rob Dawson)", "Rob Dawson" in [c["name"] for c in got], False)
check("a job title mistaken for a name is rejected (titkar)", "titkár" in [c["name"] for c in got], False)
check("the real contact is kept", [c["name"] for c in got], ["Kovács Ágnes"])
check("her email is taken from the cited line", got[0]["email"], "agnes.kovacs@a.org")
check("quote is the verbatim lines joined with '...'", got[0]["evidence_quote"], "Kovács Ágnes ... Pályazati ügyintéző")
check("that quote passes the same verifier used for the sheet",
      verify.quote_in_text(got[0]["evidence_quote"], "x\nKovács Ágnes\nPályazati ügyintéző\nagnes.kovacs@a.org"), True)
check("on-file contact is boosted to first",
      [c["name"] for c in pl.boost_on_file([{"name": "Other Person", "why_chosen": ""}, {"name": "Kovács Ágnes", "why_chosen": ""}], "Agnes")][0],
      "Kovács Ágnes")

# remit label instead of a title: accepted only with the person's own contact line nearby
pack3 = evidence.Pack()
for u, t in [("https://a.it/int", "Dr.ssa Veronica Bonanno"), ("https://a.it/int", "Erasmus Students"),
             ("https://a.it/int", "veronica.bonanno@a.it"), ("https://a.it/news", "Rob Dawson"), ("https://a.it/news", "Erasmus news")]:
    pack3.add(u, t)
got3 = pl.resolve_contacts({"contacts": [
    {"name": "Veronica Bonanno", "designation_english": "Erasmus students contact", "role_class": "mobility_coordinator",
     "name_line": 1, "role_line": 2, "email_line": 3, "phone_line": 0, "why": "x"},
    {"name": "Rob Dawson", "designation_english": "x", "role_class": "mobility_coordinator", "name_line": 4, "role_line": 5,
     "email_line": 0, "phone_line": 0, "why": "x"}]}, pack3)
check("remit label + own email nearby is accepted; remit label without contact details is not", [c["name"] for c in got3], ["Veronica Bonanno"])
check("on-file person is ranked last in alternatives mode",
      [c["name"] for c in pl.put_on_file_last([{"name": "Ana Old", "email": "", "why_chosen": ""}, {"name": "Bo New", "email": "", "why_chosen": ""}], "Ana Old")],
      ["Bo New", "Ana Old"])

# ---- person-name sanity ----
for n, want in [("Mgr. Marta Bačíková", True), ("Fehér Józsefné", True), ("Giessauf Tracey, BA", True),
                ("Jan van der Berg", True), ("Secretary Office", False), ("info@poli.hu", False), ("Agnes", False), ("Room 12 Staff", False)]:
    check(f"looks_like_person({n!r})", pl.looks_like_person(n), want)

# ---- country / partner line guards ----
check("country needs the country (or its local spelling) on the cited line", pl.country_in_line("Spain", "C/ Mayor 4, España"), True)
check("country not on the line is rejected", pl.country_in_line("Spain", "Contact us by phone"), False)
check("Latin line must contain a distinctive word of the partner name", pl.partner_line_ok("University of Oviedo", "Partner: Oviedo, Spain"), True)
check("unrelated Latin line is rejected", pl.partner_line_ok("University of Oviedo", "Welcome to our news page"), False)
check("Greek-script line is accepted (cannot compare with an English name)", pl.partner_line_ok("European University Cyprus", "Ευρωπαϊκό Πανεπιστήμιο"), True)

# ---- crawler: link scoring and page slots ----
check("Hungarian staff page scores as a staff page", crawl.link_types("Nem tanár munkatársak", "https://poli.hu/wp/nem-tanar-munkatarsak/"), ["staff"])
check("a dated blog post ranks below the real overview page with the same words",
      crawl.score_link("Erasmus project in Ghent", "https://poli.hu/wp/2026/09/04/erasmus-project-in-ghent-belgium/")
      < crawl.score_link("Erasmus project in Ghent", "https://poli.hu/wp/erasmus-project-in-ghent-belgium/"), True)
check("an announcement page ranks below the Erasmus Data Sheet PDF",
      crawl.score_link("8th Staff Week", "https://x.cy/erasmusplus/anakoinoseis-erasmus/2810-8thstaffweek")
      < crawl.score_link("Erasmus+ Data Sheet", "https://x.cy/files/Erasmus/ERASMUS_Data_Sheet_2026-2027.pdf"), True)
check("'specialist' counts as a stated role (was rejecting a real contact)",
      bool(pl.ROLE_STEMS.search(pl._n("Tarptautinių ryšių skyriaus specialistas"))), True)
cands = [("u1", 20, ["erasmus"]), ("u2", 19, ["erasmus"]), ("u3", 18, ["erasmus"]), ("s1", 8, ["staff"]), ("p1", 6, ["partners"])]
check("page slots are shared across types (staff/partners not crowded out by 3 Erasmus pages)",
      [u for u, _ in crawl.pick(cands, 3)], ["s1", "p1", "u1"])
check("sitemap <loc> parsing", crawl.LOC_RE.findall("<urlset><url><loc>https://a.org/x</loc></url><url><loc><![CDATA[https://a.org/y]]></loc></url></urlset>"),
      ["https://a.org/x", "https://a.org/y"])

# ---- model providers, against a mocked HTTP layer (no key, no network) ----
import os  # noqa: E402
import llm  # noqa: E402

sent = {}


def fake_post(url, headers, body, timeout, retries=3):
    sent.update(url=url, headers=headers, body=body)
    if "anthropic" in url:
        return {"content": [{"type": "tool_use", "name": "submit_facts", "input": {"contacts": [], "partners": []}}],
                "usage": {"input_tokens": 20000, "output_tokens": 1000}}
    return {"candidates": [{"content": {"parts": [{"text": '{"contacts": [], "partners": []}'}]}}]}


real_post, llm._post = llm._post, fake_post
os.environ["ANTHROPIC_API_KEY"] = os.environ["GEMINI_API_KEY"] = "test-key"
data, meta = llm._claude_api("sys", "user", "claude-sonnet-5", 30)
check("claude_api forces the schema tool call", sent["body"]["tool_choice"], {"type": "tool", "name": "submit_facts"})
check("claude_api sends the key in x-api-key, not in the URL", ("x-api-key" in sent["headers"], "test-key" in sent["url"]), (True, False))
check("claude_api returns the tool input as the answer", data, {"contacts": [], "partners": []})
check("claude_api cost estimate = tokens x list price ($3/$15 per M)", meta["cost_usd_estimate"], round((20000 * 3 + 1000 * 15) / 1e6, 4))
data, meta = llm._gemini("sys", "user", "gemini-3.1-flash-lite", 30)
check("gemini asks for JSON matching the schema", sent["body"]["generationConfig"]["responseMimeType"], "application/json")
check("gemini reply is parsed", data, {"contacts": [], "partners": []})
os.environ.pop("ANTHROPIC_API_KEY")
try:
    llm._claude_api("s", "u", "m", 5)
    raised = False
except llm.LLMError:
    raised = True
check("missing API key raises LLMError (pipeline then falls back to the rules provider)", raised, True)
llm._post = real_post

# ---- shared inbox -> the person's own email, from their staff profile page ----
real_fetch = fp.fetch
profile = "https://a.org/staff/ruta-puidoke"
pages_fx = [{"url": "https://a.org/staff", "text": "", "links": [("Rūta Puidokė", profile),
                                                                 ("Rūta Kitokia", "https://a.org/staff/other"),
                                                                 ("Rūta Puidokė", "https://elsewhere.com/ruta")]}]
generic = {"name": "Rūta Puidokė", "email": "trs@a.org", "office_email": "", "why_chosen": "x",
           "source_url": "https://a.org/kontaktai"}


def _no_fetch(url):
    raise AssertionError("no page should be fetched")


fp.fetch = _no_fetch
personal = {**generic, "email": "ruta.puidoke@a.org"}
check("an email that already carries the person's name is left alone, nothing fetched",
      pl.upgrade_generic_email(dict(personal), pages_fx, "https://a.org"), personal)
check("no link labelled with the full name: shared inbox kept, nothing fetched",
      pl.upgrade_generic_email(dict(generic), [{"url": "u", "text": "", "links": [("Rūta Kitokia", "https://a.org/x")]}],
                               "https://a.org"), generic)
fetched = []
fp.fetch = lambda url: fetched.append(url) or {"text": "Rūta Puidokė\nHead of unit\nruta@a.org\ninfo@a.org"}
up = pl.upgrade_generic_email(dict(generic), pages_fx, "https://a.org")
check("shared inbox is replaced by the personal email from the profile page", up["email"], "ruta@a.org")
check("the shared inbox is kept as office_email, not dropped", up["office_email"], "trs@a.org")
check("the profile page is recorded as the email's source", up["email_source_url"], profile)
check("exactly one extra page is read, and it is the same-site one", fetched, [profile])
fp.fetch = lambda url: {"text": "Rūta Puidokė\nHead of unit\ninfo@a.org"}
check("profile page without a personal email: contact unchanged",
      pl.upgrade_generic_email(dict(generic), pages_fx, "https://a.org"), generic)
fp.fetch = lambda url: {"text": "Somebody Else\nruta@a.org"}
check("linked page is about someone else: contact unchanged",
      pl.upgrade_generic_email(dict(generic), pages_fx, "https://a.org"), generic)

# web search: candidates are only used if the opened page itself shows the full name and an own-domain address
web = {"https://conf.example/speakers": {"text": "Speakers: Rūta Puidokė (A College), ruta.puidoke@a.org"},
       "https://other.example/x": {"text": "Rūta Puidokė ruta.puidoke@gmail.com"},
       "https://wrong.example/y": {"text": "Rūta Kitokia ruta@a.org"}}
fp.fetch = lambda url: web[url]
searched = []


def fake_search(name, institution, domain):
    searched.append((name, institution, domain))
    return ["https://wrong.example/y", "https://other.example/x", "https://conf.example/speakers"]


no_links = [{"url": "https://a.org/kontaktai", "text": "", "links": []}]
ws = pl.upgrade_generic_email(dict(generic), no_links, "https://www.a.org/", "A College", fake_search)
check("web search: another person's page and a gmail address are skipped, the real one is taken",
      ws["email"], "ruta.puidoke@a.org")
check("web search is asked for this person at this institute's domain", searched, [("Rūta Puidokė", "A College", "a.org")])
check("web search: shared inbox still kept alongside", ws["office_email"], "trs@a.org")
check("web search finds an email for a contact that had none",
      pl.upgrade_generic_email({**generic, "email": ""}, no_links, "https://a.org", "A College", fake_search)["email"],
      "ruta.puidoke@a.org")
check("web search with no qualifying page: contact unchanged",
      pl.upgrade_generic_email(dict(generic), no_links, "https://a.org", "A College", lambda *a: ["https://wrong.example/y"]),
      generic)
searched.clear()
pl.upgrade_generic_email(dict(personal), no_links, "https://a.org", "A College", fake_search)
check("no web search when the email is already personal", searched, [])
fp.fetch = real_fetch

# Claude web search: only result URLs are taken from the reply, and a tool error yields nothing
import io  # noqa: E402
import json as _json  # noqa: E402
import discover  # noqa: E402

real_urlopen, old_key = discover.urllib.request.urlopen, os.environ.get("ANTHROPIC_API_KEY")
os.environ["ANTHROPIC_API_KEY"] = "test-key"
sent = {}


def _fake_reply(content):
    def opener(req, timeout=0):
        sent.update(_json.loads(req.data.decode()), key=req.get_header("X-api-key"))
        return io.BytesIO(_json.dumps({"content": content, "usage": {"server_tool_use": {"web_search_requests": 1}}}).encode())
    return opener


discover.urllib.request.urlopen = _fake_reply([
    {"type": "text", "text": "Her email is invented@a.org, see https://made-up.example/"},
    {"type": "web_search_tool_result", "content": [
        {"type": "web_search_result", "url": "https://a.org/staff/ruta", "title": "Ruta"},
        {"type": "web_search_result", "url": "https://www.linkedin.com/in/ruta", "title": "LinkedIn"}]}])
check("Claude search: result URLs only, nothing from the model's own text, social sites dropped",
      discover._claude_urls("find her"), ["https://a.org/staff/ruta"])
check("Claude search is capped at one search per call", sent["tools"][0]["max_uses"], 1)
discover.urllib.request.urlopen = _fake_reply([{"type": "web_search_tool_result",
                                                "content": {"type": "web_search_tool_result_error", "error_code": "unavailable"}}])
check("Claude search: a tool error gives no URLs instead of crashing", discover._claude_urls("find her"), [])
discover.urllib.request.urlopen = real_urlopen
os.environ.pop("ANTHROPIC_API_KEY") if old_key is None else os.environ.__setitem__("ANTHROPIC_API_KEY", old_key)

real_cached = verify.cached_record
cache_fx = {"https://a.org/kontaktai": {"text": "Rūta Puidokė, Erasmus coordinator, trs@a.org"},
            profile: {"text": "Rūta Puidokė ruta@a.org"}}
verify.cached_record = cache_fx.get
vc = {"name": "Rūta Puidokė", "email": "ruta@a.org", "source_url": "https://a.org/kontaktai",
      "evidence_quote": "Rūta Puidokė, Erasmus coordinator"}
check("personal email cited to the contact page alone does not verify", verify.verify_contact(vc, "https://a.org")[0], False)
check("personal email verifies against its own profile page",
      verify.verify_contact({**vc, "email_source_url": profile}, "https://a.org")[0], True)
cache_fx["https://conf.example/speakers"] = {"text": "Rūta Puidokė ruta@a.org ruta@gmail.com"}
check("an own-domain email verifies even when the page naming it is off-site",
      verify.verify_contact({**vc, "email_source_url": "https://conf.example/speakers"}, "https://a.org")[0], True)
check("an email on another domain is rejected",
      verify.verify_contact({**vc, "email": "ruta@gmail.com", "email_source_url": "https://conf.example/speakers"},
                            "https://a.org")[0], False)
verify.cached_record = real_cached

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
