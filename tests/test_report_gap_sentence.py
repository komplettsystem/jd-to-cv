"""The public report counts the minimum requirements as met, partly met and not shown, and shows visitors no match percentage."""
import re
from webfixtures import Browser, make_app, public_check, token_of


def test_the_report_counts_met_partly_and_not_shown_and_shows_visitors_no_percentage(tmp_path, monkeypatch):
    b = Browser(make_app(tmp_path, monkeypatch), addr="203.0.113.191")
    html = b.get(f"/r/{token_of(public_check(b))}").data.decode()
    assert re.search(r"\d+ met, \d+ partly, \d+ not shown", html) and "minimum requirements in the posting" in html
    assert "% match" not in html
    assert "minimum requirements not shown" not in html
    assert "A requirement the CV does not show is marked" in html


def test_the_chat_is_told_the_same_positive_count():
    import match
    from llm import chat
    items = [{"kind": "minimum", "text": "A", "verdict": "met", "weight": 2, "reason": "", "evidence": "", "chunk_id": None, "flag": ""},
             {"kind": "minimum", "text": "B", "verdict": "not_on_record", "weight": 2, "reason": "", "evidence": "", "chunk_id": None, "flag": ""}]
    text = chat.report_text(items, gap=match.minimum_gap(items))
    assert "1 of 2 minimum requirements are met or partly met." in text
