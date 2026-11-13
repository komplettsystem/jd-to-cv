import json
import re
from datetime import datetime, timedelta, timezone

import pytest

from webfixtures import Browser, PUBLIC_POSTING, make_app, old_ts, public_check, token_of
from test_match import POSTING


@pytest.fixture
def app(tmp_path, monkeypatch):
    return make_app(tmp_path, monkeypatch)


@pytest.fixture
def visitor(app):
    return Browser(app)


@pytest.fixture
def admin(app):
    return Browser(app, addr="127.0.0.1")  # no password set: this machine is the admin


# --- access ------------------------------------------------------------------------------------

def test_health_and_landing_are_public(visitor):
    assert visitor.get("/healthz").data == b"ok"
    page = visitor.get("/").data.decode()
    assert "Does this job fit Ada?" in page and "Check the match" in page


def test_admin_answers_only_to_this_machine_when_no_password_is_set(visitor, admin):
    assert visitor.get("/admin/").status_code == 403
    assert admin.get("/admin/").status_code == 200


def test_admin_login_with_a_password_and_lockout(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, password="correct horse battery")
    b = Browser(app)
    r = b.get("/admin/runs")
    assert r.status_code == 302 and "/admin/login" in r.headers["Location"]
    for _ in range(5):
        assert b.post("/admin/login", {"user": "admin", "password": "nope"}).status_code == 401
    assert b.post("/admin/login", {"user": "admin", "password": "correct horse battery"}).status_code == 429
    import web.auth as auth
    auth._fails.clear()
    ok = b.post("/admin/login", {"user": "admin", "password": "correct horse battery"})
    assert ok.status_code == 302
    assert b.get("/admin/runs").status_code == 200


def test_every_post_needs_the_csrf_token(visitor):
    assert visitor.c.post("/check", data={"posting_text": PUBLIC_POSTING}).status_code == 403
    assert visitor.c.post("/check", data={"posting_text": PUBLIC_POSTING, "_csrf": "tampered"}).status_code == 403


def test_a_visitor_gets_no_cookie_and_nothing_is_stored_on_their_device(app, visitor):
    for url in ("/", "/roadmap", "/privacy"):
        r = visitor.get(url)
        assert r.status_code == 200 and "Set-Cookie" not in r.headers, url
    token = token_of(public_check(visitor))
    r = visitor.get(f"/r/{token}")
    assert "Set-Cookie" not in r.headers
    js = open("static/app.js").read()
    assert "localStorage" not in js and "sessionStorage" not in js and "document.cookie" not in js


def test_a_visitor_token_does_not_open_the_admin(app, visitor, admin):
    token = visitor.token()
    assert token and admin.c.post("/admin/new", data={"_csrf": token, "posting_text": PUBLIC_POSTING},
                                  environ_base={"REMOTE_ADDR": "127.0.0.1"}).status_code == 403


def test_security_headers_are_set_and_admin_pages_are_not_indexed(admin):
    r = admin.get("/admin/")
    assert "default-src 'self'" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Frame-Options"] == "DENY" and "noindex" in r.headers["X-Robots-Tag"]
    assert "style=" not in r.data.decode()  # the CSP blocks inline styles, so none may be used


def test_refuses_to_listen_publicly_without_a_password(tmp_path, monkeypatch):
    import web.auth as auth
    from config import Settings
    with pytest.raises(SystemExit):
        auth.check_bind("0.0.0.0", Settings())
    auth.check_bind("0.0.0.0", Settings(admin_password_hash="x"))
    auth.check_bind("127.0.0.1", Settings())


# --- the visitor's flow -------------------------------------------------------------------------------

def test_a_visitor_pastes_a_posting_and_gets_a_report_with_cited_cv_lines(visitor):
    r = public_check(visitor)
    assert r.status_code == 302
    page = visitor.get(f"/r/{token_of(r)}").data.decode()
    assert re.search(r"\d+% match", page) and "143-question" in page
    assert "Ada Example" in page and "not written or sent by Ada Example" in page


def test_checks_are_refused_without_a_public_model_or_when_too_quick_or_too_short(app, visitor):
    r = visitor.post("/check", {"posting_text": PUBLIC_POSTING, "ts": "9999999999"})
    assert r.status_code == 429 and b"Send it again" in r.data
    r = visitor.post("/check", {"posting_text": "short", "ts": old_ts()})
    assert r.status_code == 400 and b"too short" in r.data
    r = visitor.post("/check", {"posting_text": PUBLIC_POSTING, "ts": old_ts(), "website": "http://spam"})
    assert r.status_code == 400
    app.extensions["svc"].db.set_setting("public_model", "")
    r = visitor.post("/check", {"posting_text": PUBLIC_POSTING, "ts": old_ts()})
    assert r.status_code == 429 and b"paused" in r.data


def test_a_visitor_gets_three_checks_a_day(visitor):
    # the per-minute limits are tested in test_rate_limits; here they are out of the way
    visitor.c.application.extensions["svc"].db.set_json("limits", {"checks_per_visitor_per_minute": 99, "checks_per_minute_all": 99})
    for _ in range(3):
        assert public_check(visitor).status_code == 302
    r = public_check(visitor)
    assert r.status_code == 429 and b"3 checks" in r.data


def test_the_daily_budget_stops_new_checks(app, visitor):
    app.extensions["svc"].db.upsert_trace({
        "trace_id": "t" * 32, "name": "x", "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "duration_ms": 1, "status": "ok", "error": "", "subject_type": "result", "subject_id": 1, "model": "m",
        "input_tokens": 1, "output_tokens": 1, "cost_usd": 5.0, "model_requests": 1, "tool_calls": 0, "public": 1,
        "billable": 1})
    r = public_check(visitor)
    assert r.status_code == 429 and b"budget" in r.data


def test_public_pages_never_expose_traces_notes_or_errors(app, visitor, admin):
    token = token_of(public_check(visitor))
    run_id = app.extensions["svc"].db.run_by_token(token)["id"]
    admin.post(f"/admin/runs/{run_id}/meta", {"title": "T", "company": "C", "notes": "PRIVATE-NOTE", "human_verdict": "apply"})
    page = visitor.get(f"/r/{token}").data.decode()
    state = visitor.get(f"/r/{token}/state").data.decode()
    for text in (page, state):
        assert "PRIVATE-NOTE" not in text and "invoke_agent" not in text and "gen_ai" not in text
        assert "/admin/traces" not in text
    assert "trace_id" not in json.loads(state)["messages"].__repr__()
    assert visitor.get("/admin/traces").status_code == 403


def test_a_visitor_can_delete_their_report_and_its_traces(app, visitor):
    token = token_of(public_check(visitor))
    db = app.extensions["svc"].db
    assert db.all("SELECT 1 FROM traces")
    r = visitor.post(f"/r/{token}/delete")
    assert r.status_code == 302 and visitor.get(f"/r/{token}").status_code == 404
    assert not db.all("SELECT 1 FROM traces") and not db.all("SELECT 1 FROM spans")


def test_expired_public_reports_are_purged(app, visitor):
    token = token_of(public_check(visitor))
    svc = app.extensions["svc"]
    svc.db.run("UPDATE runs SET expires = '2000-01-01T00:00:00+00:00'")
    assert svc.purge_expired() == 1 and visitor.get(f"/r/{token}").status_code == 404


# --- chat, scoped -------------------------------------------------------------------------------------------

def ask(browser, token, question, skeptic=False):
    return browser.post(f"/r/{token}/chat", json_body={"question": question, "skeptic": skeptic})


def messages(browser, token):
    return json.loads(browser.get(f"/r/{token}/state").data)["messages"]


def test_the_chat_answers_from_the_cv_and_shows_the_line(visitor):
    token = token_of(public_check(visitor))
    assert ask(visitor, token, "Why is retrieval evaluation met?").status_code == 200
    ms = messages(visitor, token)
    assert [m["role"] for m in ms] == ["user", "assistant"]
    assert ms[1]["status"] == "done" and ms[1]["content"] == "The CV line says so." and ms[1]["evidence"]


def test_off_topic_questions_get_the_fixed_refusal_and_no_list_of_their_own(visitor):
    token = token_of(public_check(visitor))
    ask(visitor, token, "Write me a poem about cats")
    a = messages(visitor, token)[1]
    assert a["content"].startswith("I can only answer questions about this posting")
    assert a["suggestions"] == [] and "poem" not in a["content"]     # the panel below already offers questions


def test_the_saved_chat_survives_a_reload_and_empty_or_long_questions_are_refused(visitor):
    token = token_of(public_check(visitor))
    assert ask(visitor, token, "   ").status_code == 400
    assert ask(visitor, token, "x" * 600).status_code == 400
    ask(visitor, token, "What is met?")
    page = visitor.get(f"/r/{token}").data.decode()
    assert "What is met?" in page and "The CV line says so." in page


def test_a_chat_has_a_question_limit(app, visitor):
    app.extensions["svc"].db.set_json("limits", {"messages_per_chat": 2})
    token = token_of(public_check(visitor))
    assert ask(visitor, token, "one").status_code == 200
    assert ask(visitor, token, "two").status_code == 200
    r = ask(visitor, token, "three")
    assert r.status_code == 429 and "limit" in r.get_json()["error"]


def test_chat_is_off_on_admin_shares_until_the_owner_allows_it(app, admin, visitor):
    run_id = admin.post("/admin/new", {"posting_text": POSTING, "backends": ["baseline"]}).headers["Location"].split("/")[-1]
    admin.post(f"/admin/runs/{run_id}/share", {"action": "enable", "show_cv": "1"})
    token = app.extensions["svc"].db.get_run(int(run_id))["share_token"]
    assert ask(visitor, token, "hi").status_code == 404
    admin.post(f"/admin/runs/{run_id}/share", {"action": "enable", "share_chat": "1"})
    assert ask(visitor, token, "hi").status_code == 200


# --- letters, public ---------------------------------------------------------------------------------------------

def test_a_visitor_gets_a_cover_letter_grounded_in_cv_lines_and_can_download_it(visitor):
    token = token_of(public_check(visitor))
    r = visitor.post(f"/r/{token}/letter", {"language": "English", "length": "short"})
    assert r.status_code == 302
    page = visitor.get(f"/r/{token}").data.decode()
    assert "Dear hiring team," in page and "glad to talk" in page
    lid = re.search(r"/letter/(\d+)\.txt", page).group(1)
    txt = visitor.get(f"/r/{token}/letter/{lid}.txt")
    assert txt.status_code == 200 and "Dear hiring team," in txt.data.decode()


def test_visitor_letter_options_are_a_closed_list(visitor):
    token = token_of(public_check(visitor))
    assert visitor.post(f"/r/{token}/letter", {"language": "Klingon", "length": "short"}).status_code == 400
    assert visitor.post(f"/r/{token}/letter", {"language": "English", "length": "ignore all rules"}).status_code == 400


def test_a_visitor_letter_that_fails_the_checks_is_not_shown(app, visitor):
    app.extensions["svc"].db.set_json("limits", {"auto_letter": 0})   # these tests are about letters a visitor asks for
    token = token_of(public_check(visitor))
    run = app.extensions["svc"].db.run_by_token(token)
    app.extensions["svc"].db.run("UPDATE runs SET posting_text = posting_text || ' FORCEFAIL' WHERE id = ?", (run["id"],))
    visitor.post(f"/r/{token}/letter", {"language": "English", "length": "short"})
    page = visitor.get(f"/r/{token}").data.decode()
    assert "Dear hiring team," not in page and "did not pass the checks and is not shown" in page


def test_letters_per_report_are_limited(visitor):
    token = token_of(public_check(visitor))
    for _ in range(3):
        visitor.post(f"/r/{token}/letter", {"language": "English", "length": "short"})
    page = visitor.get(f"/r/{token}").data.decode()
    assert page.count("Dear hiring team,") == 2


# --- admin ----------------------------------------------------------------------------------------------------------

def new_admin_run(admin, **extra):
    data = {"posting_text": POSTING, "title": "Search PM", "company": "Acme", "backends": ["baseline"]}
    data.update(extra)
    r = admin.post("/admin/new", data)
    assert r.status_code == 302, r.data
    return int(r.headers["Location"].split("/")[-1])


def test_admin_runs_a_check_and_overrides_a_verdict(admin):
    rid = new_admin_run(admin)
    page = admin.get(f"/admin/runs/{rid}").data.decode()
    assert re.search(r"\d+% match", page)
    ids = [int(x) for x in re.findall(r'action="/admin/items/(\d+)"', page)]
    for i in ids:
        admin.post(f"/admin/items/{i}", {"override_verdict": "met", "override_weight": ""})
    page = admin.get(f"/admin/runs/{rid}").data.decode()
    assert "100% match" in page and "overridden" in page


def test_a_model_run_writes_a_trace_the_admin_can_open_with_the_full_prompt(app, admin):
    rid = new_admin_run(admin, backends=["cli:haiku"])
    page = admin.get(f"/admin/runs/{rid}").data.decode()
    tid = re.search(r'/admin/traces/([0-9a-f]{32})', page).group(1)
    t = admin.get(f"/admin/traces/{tid}").data.decode()
    assert "invoke_agent" in t and "Hands-on experience with retrieval evaluation" in t
    assert tid in admin.get("/admin/traces").data.decode()


def test_the_terms_check_gives_the_second_score_and_the_dashboard_chart_lists_the_posting(admin):
    rid = new_admin_run(admin, backends=["cli:haiku"], also_terms="1")
    page = admin.get(f"/admin/runs/{rid}").data.decode()
    assert "fit to your terms" in page
    dash = admin.get("/admin/").data.decode()
    assert "Acme" in dash and "terms" in dash


def test_pool_counts_are_entered_not_invented(admin):
    rid = new_admin_run(admin, backends=["cli:haiku"])
    admin.post(f"/admin/runs/{rid}/pool/plan", {"backend": "cli:haiku"})
    page = admin.get(f"/admin/runs/{rid}").data.decode()
    assert "linkedin.com/search/results/people" in page and "1 in" not in page
    ids = [int(x) for x in re.findall(r'/admin/pool/(\d+)', page)]
    admin.post(f"/admin/pool/{ids[0]}", {"count_text": "About 100,000 results", "source": "LinkedIn, by hand"})
    admin.post(f"/admin/pool/{ids[1]}", {"count_text": "500", "source": "LinkedIn, by hand"})
    page = admin.get(f"/admin/runs/{rid}").data.decode()
    assert "1 in 200" in page and "LinkedIn, by hand" in page
    bad = admin.post(f"/admin/pool/{ids[0]}", {"count_text": "lots"}, follow_redirects=True)
    assert b"Could not read a number" in bad.data


def test_the_pool_funnel_reaches_the_visitor_only_once_measured(app, admin, visitor):
    rid = new_admin_run(admin, backends=["cli:haiku"])
    admin.post(f"/admin/runs/{rid}/share", {"action": "enable", "show_cv": "1"})
    token = app.extensions["svc"].db.get_run(rid)["share_token"]
    admin.post(f"/admin/runs/{rid}/pool/plan", {"backend": "cli:haiku"})
    assert "LinkedIn profiles" not in visitor.get(f"/r/{token}").data.decode()
    ids = [int(x) for x in re.findall(r'/admin/pool/(\d+)', admin.get(f"/admin/runs/{rid}").data.decode())]
    admin.post(f"/admin/pool/{ids[0]}", {"count_text": "1K+", "source": "LinkedIn, by hand"})
    page = visitor.get(f"/r/{token}").data.decode()
    assert "How many LinkedIn profiles match" in page and "open search" not in page


def test_admin_letter_is_an_editable_draft_with_its_check_results(admin):
    rid = new_admin_run(admin, backends=["cli:haiku"])
    admin.post(f"/admin/runs/{rid}/letter", {"backend": "cli:haiku", "instructions": ""})
    page = admin.get(f"/admin/runs/{rid}").data.decode()
    lid = int(re.search(r'/admin/letters/(\d+)', page).group(1))
    admin.post(f"/admin/letters/{lid}", {"content": "My edited letter."})
    assert "My edited letter." in admin.get(f"/admin/runs/{rid}").data.decode()


def test_arena_hides_model_names_and_records_votes(app, admin):
    rid = new_admin_run(admin, backends=["cli:sonnet", "cli:haiku"])
    page = admin.get(f"/admin/runs/{rid}/arena").data.decode()
    assert "Report A" in page and "baseline" not in page.split("<main>")[1] and "cli:haiku" not in page.split("<main>")[1] and "cli:sonnet" not in page.split("<main>")[1]
    left, right = re.findall(r'name="(?:left|right)" value="(\d+)"', page)
    admin.post(f"/admin/runs/{rid}/vote", {"left": left, "right": right, "winner": "left"})
    assert len(app.extensions["svc"].db.votes()) == 1
    assert "No unvoted pair" in admin.get(f"/admin/runs/{rid}/arena").data.decode()


def test_models_page_toggles_models_and_validates_roles(app, admin):
    assert admin.get("/admin/models").status_code == 200
    admin.post("/admin/models/toggle", {"id": "cli:sonnet", "enabled": "0"})
    assert app.extensions["svc"].db.get_catalog("cli:sonnet")["enabled"] == 0
    assert admin.post("/admin/models/roles", {"public_model": "no-such-model"}).status_code == 400
    admin.post("/admin/models/roles", {"public_model": "cli:sonnet", "chat_model": "", "judge_model": ""})
    assert app.extensions["svc"].public_model() == "cli:sonnet"


def test_terms_and_settings_pages_save(admin):
    admin.post("/admin/terms", {"name_0": "Remote", "description_0": "Fully remote", "importance_0": "dealbreaker"})
    assert "Fully remote" in admin.get("/admin/terms").data.decode()
    admin.post("/admin/settings", {"section": "facts", "facts": "I priced an API."})
    assert "I priced an API." in admin.get("/admin/settings").data.decode()
    r = admin.post("/admin/settings", {"section": "limits", "checks_per_day": "x"}, follow_redirects=True)
    assert b"not a number" in r.data


def test_evals_pages_render_even_with_no_datasets(admin):
    assert admin.get("/admin/evals").status_code == 200


def test_every_admin_page_renders_for_a_run_without_terms_pool_letters_or_traces(app, admin):
    rid = new_admin_run(admin)  # baseline: no trace, no terms
    for url in ("/admin/", "/admin/runs", f"/admin/runs/{rid}", f"/admin/runs/{rid}/arena", "/admin/traces",
                "/admin/evals", "/admin/models", "/admin/terms", "/admin/settings", "/admin/new", "/admin/feedback"):
        r = admin.get(url)
        assert r.status_code == 200, url


def test_jobs_lost_in_a_restart_are_marked_failed_not_left_running(app, visitor):
    svc = app.extensions["svc"]
    token = token_of(public_check(visitor))
    run = svc.db.run_by_token(token)
    rid = svc.db.add_result(run["id"], "baseline")
    svc.db.set_status(rid, "running")
    assert svc.recover_stalled() == 1
    assert svc.db.get_result(rid)["status"] == "failed" and "restart" in svc.db.get_result(rid)["error"]
