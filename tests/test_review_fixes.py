"""Findings from the fresh-context copy and accessibility review: the copy may not promise what the code does not do."""
import json
import re

from webfixtures import Browser, make_app, public_check, token_of


def force_failing_letter(app, token):
    svc = app.extensions["svc"]
    run = svc.db.run_by_token(token)
    svc.db.run("UPDATE runs SET posting_text = posting_text || ' FORCEFAIL' WHERE id = ?", (run["id"],))


def test_a_letter_that_failed_the_checks_leaks_nothing_to_the_visitor(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    app.extensions["svc"].db.set_json("limits", {"auto_letter": 0})   # these tests are about letters a visitor asks for
    v = Browser(app)
    token = token_of(public_check(v))
    force_failing_letter(app, token)
    v.post(f"/r/{token}/letter", {"language": "English", "length": "short"})
    run = app.extensions["svc"].db.run_by_token(token)
    # an internal exception text on the row must never reach a visitor either
    app.extensions["svc"].db.run("UPDATE letters SET error = 'LimitError: internal detail', content = 'secret draft'")
    page = v.get(f"/r/{token}").data.decode()
    state = v.get(f"/r/{token}/state").data.decode()
    for text in (page, state):
        assert "internal detail" not in text and "secret draft" not in text and "LimitError" not in text
    assert "did not pass the checks and is not shown" in page
    assert json.loads(state)["letters"][0]["content"] == ""


def test_failed_drafts_do_not_use_up_the_visitors_limit(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    app.extensions["svc"].db.set_json("limits", {"auto_letter": 0})   # these tests are about letters a visitor asks for
    v = Browser(app)
    token = token_of(public_check(v))
    force_failing_letter(app, token)
    for _ in range(3):
        v.post(f"/r/{token}/letter", {"language": "English", "length": "short"})
    assert len(app.extensions["svc"].db.letters_for(app.extensions["svc"].db.run_by_token(token)["id"])) == 3


def test_a_report_is_gone_once_it_expires_without_waiting_for_a_restart(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    v = Browser(app)
    token = token_of(public_check(v))
    assert v.get(f"/r/{token}").status_code == 200
    app.extensions["svc"].db.run("UPDATE runs SET expires = '2000-01-01T00:00:00+00:00'")
    assert v.get(f"/r/{token}").status_code == 404


def test_the_visitor_sees_the_real_reason_a_draft_was_refused(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    app.extensions["svc"].db.set_json("limits", {"session_budget_usd": 0.0})
    v = Browser(app)
    token = token_of(public_check(v))
    r = v.post(f"/r/{token}/letter", {"language": "English", "length": "short"}, follow_redirects=True)
    assert b"reached its limit for questions and drafts" in r.data


def test_the_example_score_is_the_one_the_formula_gives():
    import match
    from web.sample import SAMPLE
    items = [{"kind": i["kind"], "verdict": i["verdict"], "override_verdict": None, "override_weight": None}
             for i in SAMPLE["items"]]
    assert SAMPLE["score"] == round(match.score(items))


def test_a_copied_or_printed_letter_carries_the_software_note_inside_the_letter_sheet(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    v = Browser(app)
    token = token_of(public_check(v))
    v.post(f"/r/{token}/letter", {"language": "German", "length": "short"})
    page = v.get(f"/r/{token}").data.decode()
    sheet = re.search(r'<div class="letter-paper".*?</div>', page, re.S).group(0)
    assert "Draft written by software from Ada Example's CV" in sheet and "Not written or sent by Ada Example." in sheet
    assert 'lang="de"' in sheet


def test_chat_quotes_are_hidden_on_a_link_that_hides_the_cv_text(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    svc = app.extensions["svc"]
    admin, v = Browser(app, addr="127.0.0.1"), Browser(app)
    from test_match import POSTING
    rid = int(admin.post("/admin/new", {"posting_text": POSTING, "backends": ["cli:haiku"]}).headers["Location"].split("/")[-1])
    admin.post(f"/admin/runs/{rid}/share", {"action": "enable", "share_chat": "1"})  # show_cv off
    token = svc.db.get_run(rid)["share_token"]
    v.post(f"/r/{token}/chat", json_body={"question": "Why is retrieval evaluation met?"})
    state = json.loads(v.get(f"/r/{token}/state").data)
    assert state["messages"][1]["content"] and state["messages"][1]["evidence"] == []
    assert "143-question" not in v.get(f"/r/{token}").data.decode()


def test_the_403_page_shows_its_message_and_does_not_reveal_how_to_get_in(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    v = Browser(app)
    r = v.c.post("/check", data={"posting_text": "x"}, environ_base={"REMOTE_ADDR": "203.0.113.7"})
    assert r.status_code == 403 and b"form expired" in r.data and b"machine the site runs on" not in r.data
    r = v.get("/admin/")
    assert r.status_code == 403 and b"for the owner" in r.data and b"set an admin password" not in r.data


def test_the_privacy_page_names_the_actual_provider_and_any_monitoring_export(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, otlp_endpoint="https://otel.example.test")
    svc = app.extensions["svc"]
    svc.db.set_setting("public_model", "openrouter:anthropic/claude-haiku-5.5")
    page = Browser(app).get("/privacy").data.decode()
    assert "OpenRouter" in page and "monitoring service" in page and "not deleted when you delete your report" in page
    app2 = make_app(tmp_path / "b", monkeypatch) if (tmp_path / "b").mkdir() is None else None
    assert "monitoring service" not in Browser(app2).get("/privacy").data.decode()


def test_the_login_page_does_not_name_the_environment_variable_to_the_public(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    page = Browser(app).get("/admin/login").data.decode()
    assert "ADMIN_PASSWORD_HASH" not in page
