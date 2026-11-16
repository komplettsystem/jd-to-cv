"""The chat numbers requirements the way the page does, shows no percentage, and answers "what if" questions with the exact counts
of minimum requirements and the overall reading re-made on the changed requirements, instead of guessing."""
import pytest

import match
from llm import chat


def item(text, kind, verdict, **kw):
    return {"idx": 0, "text": text, "kind": kind, "verdict": verdict, "reason": "r", "evidence": "", "section": "",
            "chunk_id": None, **kw}


A = item("Owned a search product", "minimum", "met")
B = item("A degree in computer science", "minimum", "not_on_record")
C = item("Familiar with reranking", "desirable", "partial")
OVERALL = {"level": "stretch", "summary": "A real jump in scope."}


def test_requirements_are_numbered_the_way_the_page_shows_them_minimum_first():
    assert [i["text"] for i in match.display_order([C, A, B])] == [A["text"], B["text"], C["text"]]


def test_what_if_items_follow_the_page_numbers_and_refuse_a_number_the_report_does_not_have():
    assert [i["text"] for i in match.what_if_items([C, A, B], leave_out=[2])] == [A["text"], C["text"]]   # number 2 is B, not the stored second
    (changed,) = [i for i in match.what_if_items([A, B, C], treat_as_met=[2]) if i["text"] == B["text"]]
    assert match.effective_verdict(changed) == "met" and B["verdict"] == "not_on_record"                    # the stored item is untouched
    with pytest.raises(ValueError, match="no requirement 9"):
        match.what_if_items([A, B, C], leave_out=[9])


def test_a_chat_bot_without_tools_is_told_it_cannot_re_read_and_is_given_no_formula():
    assert chat.instructions_for({"tools": 0}) == chat.INSTRUCTIONS
    assert "cannot re-run it" in chat.INSTRUCTIONS and "divided by" not in chat.INSTRUCTIONS and "match percentage" not in chat.INSTRUCTIONS
    assert "check_overall" not in chat.INSTRUCTIONS


def test_a_chat_bot_with_tools_is_sent_to_check_overall_and_told_the_report_shows_no_percentage():
    text = chat.instructions_for({"tools": 1})
    assert "check_overall" in text and "rescore" not in text and "explain_scoring" not in text and "divided by" not in text
    assert "The report shows no percentage" in text and "what this service offers" in text


def test_the_report_text_shows_the_overall_reading_and_the_counts_but_no_percentage():
    text = chat.report_text([C, A, B], OVERALL, match.minimum_gap([C, A, B]))
    lines = [l for l in text.splitlines() if l.startswith("Requirement ")]
    assert lines[0].startswith("Requirement 1 [minimum") and "Owned a search product" in lines[0]
    assert lines[1].startswith("Requirement 2 [minimum") and "degree" in lines[1] and "Requirement 0" not in text
    assert text.startswith("Overall reading: A stretch. A real jump in scope.")
    assert "1 of 2 minimum requirements are met or partly met." in text and "%" not in text and "left out" not in text


def deps_with(items=(A, B, C), overall=OVERALL, level_after="good", fail=False):
    deps = chat.ChatDeps(chunks={}, context_text="", items=list(items), overall=overall)

    def read(changed):
        if fail:
            raise RuntimeError("model down")
        return {"level": level_after}
    deps.read_overall = read
    return deps


def test_check_overall_gives_exact_counts_and_the_overall_reading_re_made_on_the_change():
    deps = deps_with(level_after="good")
    text = chat.call_overall_check(deps, leave_out=[2])
    assert "Minimum requirements now: 1 met, 0 partly, 1 not shown. With requirement 2 left out: 1 met, 0 partly, 0 not shown." in text
    assert "Overall reading now: A stretch. Re-read with requirement 2 left out: Good fit (changed)." in text
    assert chat.call_overall_check(deps_with(level_after="stretch"), treat_as_met=[2]).endswith("A stretch (unchanged).")


def test_the_stored_report_is_not_touched_by_a_check():
    items = [dict(A), dict(B), dict(C)]
    before = repr(items)
    chat.call_overall_check(deps_with(items), leave_out=[1], treat_as_met=[2])
    assert repr(items) == before


def test_without_a_stored_overall_reading_or_when_the_re_read_fails_the_bot_gets_the_counts_and_a_line_not_to_guess():
    for deps in (deps_with(overall=None), deps_with(fail=True)):
        text = chat.call_overall_check(deps, leave_out=[2])
        assert "Minimum requirements now: 1 met" in text and "cannot tell whether the overall reading would change" in text
    unreadable = deps_with(overall=None)
    unreadable.read_overall = None
    assert "cannot tell" in chat.call_overall_check(unreadable, leave_out=[2])


def test_a_number_the_report_does_not_have_or_leaving_everything_out_is_refused_in_words():
    deps = deps_with()
    assert "no requirement 9" in chat.call_overall_check(deps, leave_out=[9])
    assert "nothing to read" in chat.call_overall_check(deps, leave_out=[1, 2, 3])
    assert all(ex["response"]["result"]["isError"] for ex in deps.exchanges)


def test_the_overall_reading_is_re_made_at_most_twice_per_answer():
    deps = deps_with()
    for _ in range(3):
        text = chat.call_overall_check(deps, leave_out=[2])
    assert deps.overall_calls == chat.MAX_OVERALL_CALLS and "cannot tell" in text


def test_every_check_is_kept_as_a_request_and_a_response_and_its_figures_are_allowed_in_the_answer():
    deps = deps_with()
    out = chat.ChatAnswerOut(scope="in_scope", answer="Leaving it out leaves 1 met and 0 not shown among 1 minimum requirements.", basis=["report"])
    chat.call_overall_check(deps, leave_out=[2])
    (ex,) = deps.exchanges
    assert ex["from"] == "chat bot" and ex["to"] == "overall reading" and ex["request"]["method"] == "tools/call"
    assert ex["request"]["params"] == {"name": "check_overall", "arguments": {"leave_out": [2], "treat_as_met": []}}
    assert chat.check_answer(out, deps) == []


def test_the_chat_bot_calls_check_overall_instead_of_calculating(tmp_path, monkeypatch):
    """A scripted model that calls the tool, then answers with what it returned."""
    import json as _json
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
    from pydantic_ai.models.function import FunctionModel
    from webfixtures import make_app

    def script(messages, info):
        returned = any(isinstance(p, ToolReturnPart) for m in messages for p in getattr(m, "parts", []))
        if not returned:
            return ModelResponse(parts=[ToolCallPart("check_overall", {"leave_out": [2]})])
        body = {"scope": "in_scope", "answer": "With the degree left out, 1 of the minimum requirements is met and none is not shown.",
                "basis": ["report"], "evidence_ids": []}
        return ModelResponse(parts=[TextPart(_json.dumps(body))])

    tracing = make_app(tmp_path, monkeypatch).extensions["svc"].tracing
    entry = {"provider": "openrouter", "model_name": "x", "output_mode": "prompted", "tools": 1, "effort": ""}
    rc = chat.ReportChat(entry, tracing, FunctionModel(script, model_name="scripted"), "A posting.", [A, B, C],
                         [{"id": "c1", "section": "Work", "text": "Owned a search product"}],
                         overall=OVERALL, gap=match.minimum_gap([A, B, C]))
    rc.deps.read_overall = lambda changed: {"level": "stretch"}
    out = rc.ask("What if the degree requirement were removed?")
    assert out["scope"] == "in_scope" and "none is not shown" in out["content"]
    assert len(out["exchanges"]) == 1 and out["exchanges"][0]["request"]["params"]["name"] == "check_overall"
