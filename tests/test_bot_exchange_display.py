"""The exchange between the chat bot and the match engine is stored with the answer and shown under it, request and reply,
with the raw messages one click away."""
from pathlib import Path

from test_web import ask, messages
from webfixtures import Browser, make_app, public_check, token_of

EXCHANGE = {"from": "chat bot", "to": "match engine",
            "request": {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": "rescore", "arguments": {"leave_out": [6], "treat_as_met": []}}},
            "response": {"jsonrpc": "2.0", "id": 1, "result": {
                "content": [{"type": "text", "text": "Match now: 43%. With requirement 6 left out: 47%."}], "isError": False}}}


def with_exchange(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    b = Browser(app, addr="203.0.113.131")
    token = token_of(public_check(b))
    assert ask(b, token, "Why is retrieval evaluation met?").status_code == 200
    answer = [m for m in messages(b, token) if m["role"] == "assistant"][0]
    app.extensions["svc"].db.update_message(answer["id"], exchanges=[EXCHANGE])
    return app, b, token


def test_the_exchange_is_stored_and_comes_back_with_the_message(tmp_path, monkeypatch):
    app, b, token = with_exchange(tmp_path, monkeypatch)
    stored = app.extensions["svc"].db.messages_for(app.extensions["svc"].db.one("SELECT id FROM chats")["id"])
    assert stored[-1]["exchanges"] == [EXCHANGE] and stored[0]["exchanges"] == []


def test_the_live_state_carries_the_exchange(tmp_path, monkeypatch):
    app, b, token = with_exchange(tmp_path, monkeypatch)
    answer = [m for m in messages(b, token) if m["role"] == "assistant"][0]
    assert answer["exchanges"][0]["request"]["params"]["name"] == "rescore"
    assert answer["exchanges"][0]["response"]["result"]["content"][0]["text"].startswith("Match now: 43%")


def test_the_page_shows_who_asked_whom_and_what_came_back(tmp_path, monkeypatch):
    app, b, token = with_exchange(tmp_path, monkeypatch)
    html = b.get(f"/r/{token}").data.decode()
    assert 'class="exchange"' in html and "The chat bot asked the match engine" in html
    assert "Chat bot → match engine" in html and "Match engine → chat bot" in html
    assert "Match now: 43%. With requirement 6 left out: 47%." in html
    assert 'class="exchange-raw"' in html and "&#34;method&#34;: &#34;tools/call&#34;" in html or '"method": "tools/call"' in html


def test_an_answer_without_an_exchange_shows_no_exchange_block(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    b = Browser(app, addr="203.0.113.132")
    token = token_of(public_check(b))
    ask(b, token, "Why is retrieval evaluation met?")
    assert 'class="exchange"' not in b.get(f"/r/{token}").data.decode()


def test_the_script_can_draw_an_exchange_that_arrives_while_the_page_is_open():
    js = (Path(__file__).resolve().parent.parent / "static" / "app.js").read_text()
    assert "renderExchange" in js and "m.exchanges" in js
