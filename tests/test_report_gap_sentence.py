"""The line under "Minimum requirements" says how many are met or partly met, not how many are missing."""
from webfixtures import Browser, make_app, public_check, token_of


def test_the_report_counts_what_is_met_or_partly_met(tmp_path, monkeypatch):
    b = Browser(make_app(tmp_path, monkeypatch), addr="203.0.113.191")
    html = b.get(f"/r/{token_of(public_check(b))}").data.decode()
    assert "minimum requirements are met or partly met" in html
    assert "minimum requirements not shown" not in html
    assert "A requirement the CV does not show is marked" in html


def test_the_chat_is_told_the_same_positive_count():
    import match
    from llm import chat
    items = [{"kind": "minimum", "text": "A", "verdict": "met", "weight": 2, "reason": "", "evidence": "", "chunk_id": None, "flag": ""},
             {"kind": "minimum", "text": "B", "verdict": "not_on_record", "weight": 2, "reason": "", "evidence": "", "chunk_id": None, "flag": ""}]
    text = chat.report_text(items, score_value=50, gap=match.minimum_gap(items))
    assert "1 of 2 minimum requirements are met or partly met." in text
