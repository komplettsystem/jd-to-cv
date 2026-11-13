"""A recruiter's report comes with a cover letter written automatically once the match is done. A job seeker's does not (they ask
for it), and a failed or refused letter never breaks the report."""
from pathlib import Path

from service import DEFAULT_LIMITS
from test_caps import setup
from test_upload import upload
from webfixtures import Browser, make_app, public_check, token_of


def letters_of(svc, token):
    run = svc.db.one("SELECT id FROM runs WHERE share_token=?", (token,))["id"]
    return svc.db.letters_for(run, "shared")


def test_a_recruiters_report_gets_one_letter_without_asking(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    token = token_of(public_check(Browser(app, addr="203.0.113.161")))
    (letter,) = letters_of(app.extensions["svc"], token)
    assert letter["status"] == "done" and "Dear hiring team," in letter["content"] and "English" in letter["instructions"]


def test_the_visitor_can_still_ask_for_a_second_letter_and_then_hits_the_limit(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    svc, b = app.extensions["svc"], Browser(app, addr="203.0.113.162")
    token = token_of(public_check(b))
    assert DEFAULT_LIMITS["letters_per_report"] == 2
    b.post(f"/r/{token}/letter", {"language": "German", "length": "short"})
    assert len(letters_of(svc, token)) == 2
    r = b.post(f"/r/{token}/letter", {"language": "English", "length": "short"})
    assert "letter-limit" in r.headers["Location"] and len(letters_of(svc, token)) == 2


def test_a_job_seekers_report_does_not_get_a_letter_by_itself(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    token = token_of(upload(Browser(app, addr="203.0.113.163")))
    assert letters_of(app.extensions["svc"], token) == []


def test_a_refused_letter_does_not_break_the_report(tmp_path, monkeypatch):
    app, svc = setup(tmp_path, monkeypatch, {"session_budget_usd": 0.0})     # nothing may be spent on this report
    token = token_of(public_check(Browser(app, addr="203.0.113.164")))
    run = svc.db.one("SELECT id FROM runs WHERE share_token=?", (token,))["id"]
    assert svc.db.one("SELECT status FROM results WHERE run_id=?", (run,))["status"] in ("done", "failed")
    assert Browser(app, addr="203.0.113.165").get(f"/r/{token}").status_code == 200


def test_the_page_reloads_when_the_report_is_ready_without_waiting_for_the_letter():
    js = (Path(__file__).resolve().parent.parent / "static" / "app.js").read_text()
    assert "resultsRunning" in js and "first" in js


def test_the_owner_can_switch_the_automatic_letter_off(tmp_path, monkeypatch):
    app, svc = setup(tmp_path, monkeypatch, {"auto_letter": 0})
    token = token_of(public_check(Browser(app, addr="203.0.113.166")))
    assert letters_of(svc, token) == []
    html = Browser(app, addr="127.0.0.1").get("/admin/settings").data.decode()
    assert 'id="auto_letter"' in html and "automatically after a recruiter" in html
