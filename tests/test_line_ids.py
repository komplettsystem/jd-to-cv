"""The app's own numbers for CV lines (c10 and so on) belong in the id fields only. A visitor cannot know what "shown in c10"
means, so they are removed from every sentence a model writes: verdict reasons, the overall reading, chat answers, letters."""
import json
import re

import webfixtures
from match import strip_ids
from test_web import ask, messages
from webfixtures import Browser, make_app, public_check, token_of

ID = re.compile(r"\b[cf]\d+\b")


def test_ids_are_replaced_or_removed_and_only_known_ids_are_touched():
    ids = {"c10", "c11"}
    assert strip_ids("Stakeholder influence shown in c10.", ids) == "Stakeholder influence shown in the CV line."
    assert strip_ids("The CV (c10) shows it.", ids) == "The CV shows it."
    assert strip_ids("See [c11] for the dates.", ids) == "See for the dates."
    assert strip_ids("Line c10 and evidence c11 agree.", ids) == "The CV line and the CV line agree."
    assert strip_ids("Version c100 and c99 stay.", ids) == "Version c100 and c99 stay."     # not ids of this run
    assert strip_ids("Nothing to change here.", ids) == "Nothing to change here."


def leaking(monkeypatch):
    monkeypatch.setitem(webfixtures.LEAK, "on", True)


def test_verdict_reasons_and_the_overall_reading_never_show_line_ids(tmp_path, monkeypatch):
    leaking(monkeypatch)
    app = make_app(tmp_path, monkeypatch)
    token_of(public_check(Browser(app, addr="203.0.113.111")))
    db = app.extensions["svc"].db
    reasons = [r["reason"] for r in db.all("SELECT reason FROM items")]
    assert reasons and not any(ID.search(r) for r in reasons) and any("the CV line" in r for r in reasons)
    overall = json.loads(db.one("SELECT overall FROM results WHERE kind='fit'")["overall"])
    assert "error" not in overall and not ID.search(overall["summary"]) and "the CV line" in overall["summary"]
    assert not any(ID.search(p["text"]) for p in overall["points"])


def test_chat_answers_never_show_line_ids(tmp_path, monkeypatch):
    leaking(monkeypatch)
    app = make_app(tmp_path, monkeypatch)
    b = Browser(app, addr="203.0.113.112")
    token = token_of(public_check(b))
    assert ask(b, token, "Why is retrieval evaluation met?").status_code == 200
    answer = [m for m in messages(b, token) if m["role"] == "assistant"][0]["content"]
    assert answer.startswith("The CV line says so.") and not ID.search(answer)


def test_letters_never_show_line_ids(tmp_path, monkeypatch):
    leaking(monkeypatch)
    app = make_app(tmp_path, monkeypatch)
    b = Browser(app, addr="203.0.113.113")
    token = token_of(public_check(b))
    b.post(f"/r/{token}/letter", {"language": "English", "length": "short"})
    page = b.get(f"/r/{token}").data.decode()
    closing = re.search(r"glad to talk.{0,40}", page, re.S).group(0)         # where the scripted letter put its id
    assert "glad to talk" in closing and not ID.search(closing)


def test_em_dashes_a_model_used_as_pauses_become_commas_but_number_ranges_stay():
    assert strip_ids("Led onboarding \u2014 customers waited for teams \u2014 and shipped.", set()) == "Led onboarding, customers waited for teams, and shipped."
    assert strip_ids("Ran it from 2006\u20132011 and again 2019 \u2013 2021.", set()) == "Ran it from 2006\u20132011 and again 2019, 2021."
