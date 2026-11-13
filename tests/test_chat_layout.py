"""The chat sits next to the report on wide screens (docked, in view while the report scrolls) and is reachable from a clearly
labelled bar on narrow ones. Layout details are checked in a real browser; these tests check the markup and the rules exist."""
from pathlib import Path

from webfixtures import Browser, make_app, public_check, token_of

CSS = (Path(__file__).resolve().parent.parent / "static" / "style.css").read_text()


def test_a_report_with_a_chat_is_marked_so_and_has_the_ask_bar(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    b = Browser(app, addr="203.0.113.171")
    token = token_of(public_check(b))
    html = b.get(f"/r/{token}").data.decode()
    assert 'class="report-page stack has-chat"' in html and 'id="report" class="stack"' in html
    assert 'class="ask-bar no-print" href="#chat"' in html and "Ask the CV" in html


def test_a_report_without_a_chat_has_neither(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    b = Browser(app, addr="203.0.113.172")
    token = token_of(public_check(b))
    app.extensions["svc"].db.run("UPDATE runs SET share_chat = 0")
    html = b.get(f"/r/{token}").data.decode()
    assert "has-chat" not in html and "ask-bar" not in html


def test_the_styles_dock_the_chat_on_wide_screens_and_show_the_bar_on_narrow_ones():
    assert "@media (min-width: 1100px)" in CSS and ".report-page.has-chat #chat" in CSS and "position: sticky" in CSS
    assert ".ask-bar" in CSS and "@media (max-width: 1099px)" in CSS


def test_suggested_questions_are_small_links_with_an_enter_sign_and_an_empty_log_takes_no_room():
    rule = CSS[CSS.index("button.chip, button.chip:hover"):CSS.index("}", CSS.index("button.chip, button.chip:hover"))]
    assert "border: 0" in rule and "background: none" in rule
    assert 'button.chip::before { content: "\\21B5"' in CSS
    assert ".chat-log:not(:has(.msg)) { display: none; }" in CSS
