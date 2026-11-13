"""The chat knows how the match percentage is calculated, numbers requirements the way the page does, and gets exact answers
to "what if" questions from the app's own scoring instead of guessing."""
import pytest

import match
from llm import chat


def item(text, kind, verdict, **kw):
    return {"idx": 0, "text": text, "kind": kind, "verdict": verdict, "reason": "r", "evidence": "", "section": "",
            "chunk_id": None, **kw}


# minimum met (2 x 1), minimum not shown (2 x 0), nice-to-have partly (1 x .5): (2 + 0 + .5) / 5 = 50 %
A = item("Owned a search product", "minimum", "met")
B = item("A degree in computer science", "minimum", "not_on_record")
C = item("Familiar with reranking", "desirable", "partial")


def test_requirements_are_numbered_the_way_the_page_shows_them_minimum_first():
    assert [i["text"] for i in match.display_order([C, A, B])] == [A["text"], B["text"], C["text"]]


def test_what_if_numbers_are_exact():
    now, after = match.rescore([A, B, C])
    assert round(now) == 50 and round(after) == 50
    assert round(match.rescore([A, B, C], leave_out=[2])[1], 1) == 83.3        # B (number 2) left out: 2.5 / 3
    assert round(match.rescore([A, B, C], treat_as_met=[2])[1]) == 90          # B met: 4.5 / 5
    assert round(match.rescore([C, A, B], leave_out=[2])[1], 1) == 83.3        # numbers follow the page, not the stored order
    assert match.rescore([A], leave_out=[1])[1] is None                        # nothing left to score


def test_a_number_the_report_does_not_have_is_refused():
    with pytest.raises(ValueError, match="no requirement 9"):
        match.rescore([A, B, C], leave_out=[9])


def test_the_scoring_rules_are_written_from_the_constants():
    text = match.scoring_rules()
    assert "weight" in text and "divided by" in text
    assert f"minimum requirement {match.DEFAULT_WEIGHT['minimum']}" in text and "partly 0.5" in text and "not shown 0" in text


def test_a_chat_bot_without_tools_is_given_the_formula_and_the_figures():
    entry = {"tools": 0}
    assert chat.instructions_for(entry) == chat.INSTRUCTIONS
    assert match.scoring_rules() in chat.INSTRUCTIONS and "how the match percentage is calculated" in chat.INSTRUCTIONS
    assert "never calculate" in chat.INSTRUCTIONS.lower()


def test_a_chat_bot_with_tools_is_not_given_the_formula_and_must_ask_the_match_engine():
    text = chat.instructions_for({"tools": 1})
    assert match.scoring_rules() not in text and "divided by" not in text
    assert "match engine" in text and "explain_scoring" in text and "rescore" in text
    assert "what this service offers" in text                                     # the rest of the scope is unchanged


def test_the_report_text_numbers_from_one_and_gives_the_effect_of_each_requirement_when_asked():
    text = chat.report_text([C, A, B], match.score([C, A, B]), match.minimum_gap([C, A, B]))
    lines = [l for l in text.splitlines() if l.startswith("Requirement ")]
    assert lines[0].startswith("Requirement 1 [minimum") and "Owned a search product" in lines[0]
    assert lines[1].startswith("Requirement 2 [minimum") and "degree" in lines[1]
    assert "Requirement 0" not in text
    assert "left out: 83%" in lines[1] and "met: 90%" in lines[1]
    assert "Overall score: 50% match" in text
    bare = chat.report_text([C, A, B], match.score([C, A, B]), match.minimum_gap([C, A, B]), effects=False)
    assert "left out" not in bare and "Requirement 2 [minimum" in bare


def test_a_figure_that_only_the_match_engine_returned_is_allowed_in_the_answer():
    deps = chat.ChatDeps(chunks={}, context_text="Overall score: 50% match.", items=[A, B, C])
    out = chat.ChatAnswerOut(scope="in_scope", answer="Leaving it out gives a match of 83%.", basis=["report"])
    assert any("83" in p for p in chat.check_answer(out, deps))                   # not known: refused
    chat.call_engine(deps, "rescore", {"leave_out": [2]})
    assert chat.check_answer(out, deps) == []


def test_every_call_to_the_engine_is_kept_as_a_request_and_a_response():
    deps = chat.ChatDeps(chunks={}, context_text="", items=[A, B, C])
    text = chat.call_engine(deps, "rescore", {"leave_out": [2]})
    assert text == "Match now: 50%. With requirement 2 left out: 83%."
    (ex,) = deps.exchanges
    assert ex["from"] == "chat bot" and ex["to"] == "match engine"
    assert ex["request"]["method"] == "tools/call" and ex["request"]["params"] == {"name": "rescore", "arguments": {"leave_out": [2], "treat_as_met": []}}
    assert ex["response"]["result"]["structuredContent"]["match_after"] == 83.3
    assert "no requirement 9" in chat.call_engine(deps, "rescore", {"leave_out": [9]}) and len(deps.exchanges) == 2


def test_the_chat_bot_asks_the_engine_instead_of_calculating(tmp_path, monkeypatch):
    """A scripted model that calls the rescore tool, then answers with the engine's number."""
    import json as _json
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
    from pydantic_ai.models.function import FunctionModel
    from webfixtures import make_app

    def script(messages, info):
        returned = any(isinstance(p, ToolReturnPart) for m in messages for p in getattr(m, "parts", []))
        if not returned:
            return ModelResponse(parts=[ToolCallPart("rescore", {"leave_out": [2]})])
        body = {"scope": "in_scope", "answer": "Leaving requirement 2 out would raise the match to 83%.", "basis": ["report"],
                "evidence_ids": []}
        return ModelResponse(parts=[TextPart(_json.dumps(body))])

    tracing = make_app(tmp_path, monkeypatch).extensions["svc"].tracing
    entry = {"provider": "openrouter", "model_name": "x", "output_mode": "prompted", "tools": 1, "effort": ""}
    rc = chat.ReportChat(entry, tracing, FunctionModel(script, model_name="scripted"), "A posting.", [A, B, C],
                         [{"id": "c1", "section": "Work", "text": "Owned a search product"}],
                         score_value=match.score([A, B, C]), gap=match.minimum_gap([A, B, C]))
    assert "left out" not in rc.report                                            # no precomputed effects for a bot with tools
    out = rc.ask("What if the degree requirement were removed?")
    assert out["scope"] == "in_scope" and "83%" in out["content"]
    assert len(out["exchanges"]) == 1 and out["exchanges"][0]["request"]["params"]["name"] == "rescore"
