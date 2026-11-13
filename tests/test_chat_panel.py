"""The chat panel: headline, suggested questions that the report does not already answer (two at a time), a textarea, and room to grow."""
import re
from pathlib import Path

from webfixtures import Browser, make_app, public_check, token_of
from llm import chat

CSS = (Path(__file__).resolve().parent.parent / "static" / "style.css").read_text()
JS = (Path(__file__).resolve().parent.parent / "static" / "app.js").read_text()
REPORT_ANSWERS = ("Which minimum requirements are met, and by which CV lines?", "Which minimum requirements have the weakest evidence?")


def item(text, kind="minimum", verdict="met"):
    return {"kind": kind, "text": text, "verdict": verdict, "weight": 2, "reason": "", "evidence": "", "chunk_id": None, "flag": ""}


PLAIN = [item("Has shipped a mobile app."), item("Writes clear specifications.", "desirable")]


def test_the_suggestions_never_repeat_what_the_report_already_shows():
    qs = chat.suggest_questions(PLAIN + [item("Some gap here.", verdict="not_on_record")])
    assert qs and len(qs) <= 6
    assert not any(q.startswith("Why is") or q in REPORT_ANSWERS for q in qs)


def test_a_question_about_something_the_posting_asks_for_is_left_out_because_the_report_covers_it():
    team = [item("Has led a team of product managers."), item("Writes specifications.")]
    assert not any("led a team" in q for q in chat.suggest_questions(team))
    assert any("led a team" in q for q in chat.suggest_questions(PLAIN))
    ai = [item("Hands-on experience building AI agents."), item("Writes specifications.")]
    assert not any("AI agents" in q for q in chat.suggest_questions(ai))
    assert any("AI agents" in q for q in chat.suggest_questions(PLAIN))


def test_a_question_that_was_already_asked_is_not_suggested_again():
    first = chat.suggest_questions(PLAIN)
    rest = chat.suggest_questions(PLAIN, asked=first[:2])
    assert first[0] not in rest and first[1] not in rest and rest[0] == first[2]


def test_one_requirement_offers_no_what_would_change_question():
    assert "What would change the match most?" not in chat.suggest_questions([item("Has shipped a mobile app.")])
    assert "What would change the match most?" in chat.suggest_questions(PLAIN)


def page(tmp_path, monkeypatch, addr):
    b = Browser(make_app(tmp_path, monkeypatch), addr=addr)
    return b.get(f"/r/{token_of(public_check(b))}").data.decode()


def test_the_panel_is_called_ask_the_cv_and_the_question_box_is_a_textarea(tmp_path, monkeypatch):
    html = page(tmp_path, monkeypatch, "203.0.113.181")
    assert '<h2 id="chat-title">Ask the CV</h2>' in html and "Ask about this report" not in html
    assert '<textarea id="chat-q"' in html and '<input id="chat-q"' not in html
    assert 'class="ask-bar no-print" href="#chat">Ask the CV</a>' in html


def test_the_chips_start_with_two_and_none_of_them_is_something_the_report_shows(tmp_path, monkeypatch):
    html = page(tmp_path, monkeypatch, "203.0.113.182")
    assert 'class="chips"' in html and 'data-show="2"' in html
    assert not any(q in html for q in REPORT_ANSWERS)


def test_the_script_keeps_two_suggestions_visible_and_sends_on_enter():
    assert "dataset.show" in JS and "refreshChips" in JS and "updateLeft" in JS
    assert "'Enter'" in JS and "shiftKey" in JS


def test_the_panel_grows_to_the_page_height_once_there_is_a_conversation_and_the_box_is_not_an_input_row():
    assert "#chat:has(.msg)" in CSS and "height: calc(100vh - 2rem)" in CSS
    assert ".chat-row textarea" in CSS and "resize: none" in CSS


def test_there_is_no_counter_and_a_notice_appears_only_when_few_questions_are_left(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    b = Browser(app, addr="203.0.113.183")
    url = f"/r/{token_of(public_check(b))}"
    html = b.get(url).data.decode()
    assert "questions asked" not in html and "chat-count" not in html
    assert re.search(r'id="chat-left"[^>]*hidden', html)            # twenty left: nothing to say
    app.extensions["svc"].db.set_json("limits", {"messages_per_chat": 3})
    html = b.get(url).data.decode()
    assert "You have 3 questions left." in html and not re.search(r'id="chat-left"[^>]*hidden', html)
    app.extensions["svc"].db.set_json("limits", {"messages_per_chat": 1})
    assert "You have 1 question left." in b.get(url).data.decode()
