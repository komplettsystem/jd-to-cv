import markup


def item(text, verdict, kind="minimum"):
    return {"text": text, "verdict": verdict, "kind": kind, "override_verdict": None, "override_weight": None}


POSTING = """About us
We build search for agents.

Requirements
- Hands-on experience with retrieval evaluation and relevance judgments.
- Experience with usage-based pricing and marketplaces.
Benefits
Health insurance for everyone."""


def test_each_requirement_marks_the_sentence_it_came_from():
    items = [item("Hands-on experience with retrieval evaluation, including relevance judgments", "met"),
             item("Experience with usage-based pricing and marketplaces", "not_on_record", "desirable")]
    rows = markup.mark_posting(POSTING, items)
    marked = {p["text"]: p for row in rows for p in row if p["item"] is not None}
    assert marked["- Hands-on experience with retrieval evaluation and relevance judgments."]["verdict"] == "met"
    assert marked["- Experience with usage-based pricing and marketplaces."]["verdict"] == "not_on_record"
    unmarked = [p["text"] for row in rows for p in row if p["item"] is None]
    assert "Health insurance for everyone." in unmarked


def test_a_requirement_with_no_matching_sentence_marks_nothing():
    rows = markup.mark_posting(POSTING, [item("Fluent in Klingon and Esperanto", "not_on_record")])
    assert all(p["item"] is None for row in rows for p in row)


def test_blank_lines_are_kept_as_empty_rows():
    rows = markup.mark_posting("a b c d\n\ne f g h", [])
    assert [len(r) for r in rows] == [1, 0, 1]
