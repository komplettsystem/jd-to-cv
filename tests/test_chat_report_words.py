"""The chat is shown the report in the reader's words: a visitor must never be told "not on record" or "not_on_record"."""
import match
from llm import chat


def _item(verdict):
    return {"kind": "minimum", "text": "Has run sprints", "weight": 2, "verdict": verdict, "reason": "r", "evidence": "", "section": "",
            "chunk_id": None, "flag": ""}


def test_report_text_uses_the_readers_words():
    items = [_item("met"), _item("partial"), _item("not_on_record")]
    text = chat.report_text(items, score_value=50, gap=(1, 3))
    assert "-> met." in text and "-> partly." in text and "-> not shown." in text
    assert "not_on_record" not in text and "not on record" not in text
