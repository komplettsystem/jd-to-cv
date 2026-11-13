"""The overall reading: code checks on what the model returns, how it is stored and shown, and that its failure
never takes the point-by-point report with it."""
import json
import re

import match
from llm import overall
from llm.overall import OverallOut, PointOut, check
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

import webfixtures
from webfixtures import Browser, make_app, public_check, token_of

IDS = {"c1", "c2"}
CONTEXT = "Led product teams across five locations. Retrieval evaluation. Posting wants search experience."


def point(**kw):
    base = {"kind": "strength", "text": "The roles match.", "basis": "on_record", "evidence_ids": ["c1"]}
    return PointOut(**{**base, **kw})


def reading(points=None, **kw):
    base = {"level": "good", "summary": "A solid fit with some gaps.",
            "points": points or [point(), point(kind="expected", basis="inferred"), point(kind="gap", evidence_ids=[])],
            "interview_checks": []}
    return OverallOut(**{**base, **kw})


def test_a_reading_that_follows_the_rules_passes():
    assert check(reading(), IDS, CONTEXT) == []


def test_every_rule_has_a_check():
    cases = {
        "not CV lines": reading([point(evidence_ids=["c9"]), point(), point(kind="gap", evidence_ids=[])]),
        "cites no CV line": reading([point(evidence_ids=[]), point(), point(kind="gap", evidence_ids=[])]),
        "basis inferred": reading([point(kind="expected", basis="on_record"), point(), point(kind="gap", evidence_ids=[])]),
        "figures": reading(summary="Twelve hundred is wrong but 4500 people were led."),
        "email address": reading([point(text="Write to jo@example.com"), point(), point(kind="gap", evidence_ids=[])]),
        "score or a percentage": reading(summary="A 72% match overall."),
        "longer than": reading([point(text="word " * 60), point(), point(kind="gap", evidence_ids=[])]),
    }
    for expected, out in cases.items():
        assert any(expected in p for p in check(out, IDS, CONTEXT)), expected


def test_a_gap_may_cite_nothing_because_absence_is_the_point():
    assert check(reading([point(), point(kind="gap", evidence_ids=[]), point(kind="gap", evidence_ids=[], basis="inferred")]),
                 IDS, CONTEXT) == []


def scripted_agent(replies):
    state = {"i": 0}

    def fn(messages, info):
        text = replies[min(state["i"], len(replies) - 1)]
        state["i"] += 1
        return ModelResponse(parts=[TextPart(json.dumps(text))])
    return FunctionModel(fn, model_name="scripted"), state


def test_the_agent_retries_with_the_reason_and_returns_only_valid_ids(tmp_path):
    from config import Settings
    from db import DB
    from llm.agents import Agents
    from llm.tracing import Tracing
    from test_llm import ENTRY
    db = DB(str(tmp_path / "t.db"))
    chunks = [{"id": "c1", "section": "Experience", "text": "Led product teams."},
              {"id": "c2", "section": "Experience", "text": "Built retrieval evals."}]
    bad = {"level": "good", "summary": "Fine.", "points": [
        {"kind": "strength", "text": "Led teams.", "basis": "on_record", "evidence_ids": ["c9"]},
        {"kind": "strength", "text": "Built evals.", "basis": "on_record", "evidence_ids": ["c2"]},
        {"kind": "gap", "text": "Pricing not shown.", "basis": "on_record", "evidence_ids": []}]}
    good = json.loads(json.dumps(bad))
    good["points"][0]["evidence_ids"] = ["c1"]
    model, state = scripted_agent([bad, good])
    agents = Agents(ENTRY, Tracing(db, Settings(data_dir=tmp_path)), model=model)
    items = [{"kind": "minimum", "text": "Led product teams", "verdict": "met", "override_verdict": None,
              "override_weight": None}]
    out = agents.overall("posting text", items, chunks)
    assert state["i"] == 2  # the first answer cited an id that is not a CV line, so it was sent back once
    assert [p["evidence_ids"] for p in out["points"]] == [["c1"], ["c2"], []]


def test_a_visitor_sees_the_reading_but_only_line_counts_unless_the_cv_is_shared(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    token = token_of(public_check(Browser(app)))
    app.extensions["svc"].db.run("UPDATE runs SET share_show_cv=0")
    page = Browser(app).get(f"/r/{token}").data.decode()
    assert "Overall reading" in page and "Good fit" in page and "Inferred from the CV" in page
    assert "does not change the percentage" in page
    assert "The text is not shared on this link" in page
    app.extensions["svc"].db.run("UPDATE runs SET share_show_cv=1")
    page = Browser(app).get(f"/r/{token}").data.decode()
    assert "The text is not shared on this link" not in page and "cv-line" in page


def test_the_reading_never_changes_the_score(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    svc = app.extensions["svc"]
    token = token_of(public_check(Browser(app)))
    run = svc.db.run_by_token(token)
    result = svc.primary_result(run["id"])
    before = match.score(svc.db.items_for(result["id"]))
    svc.db.set_overall(result["id"], {"level": "weak", "summary": "x", "points": [], "interview_checks": []})
    assert match.score(svc.db.items_for(result["id"])) == before


def test_a_failed_reading_leaves_the_report_complete_and_shows_visitors_no_error_text(tmp_path, monkeypatch):
    original = webfixtures.fake_llm

    def failing(messages, info):
        if webfixtures._instructions(messages).startswith("You give an overall reading"):
            raise RuntimeError("secret provider detail")
        return original(messages, info)
    monkeypatch.setattr(webfixtures, "fake_llm", failing)
    app = make_app(tmp_path, monkeypatch)
    app.extensions["svc"].model_factory = lambda entry: FunctionModel(failing, model_name="scripted")
    token = token_of(public_check(Browser(app)))
    page = Browser(app).get(f"/r/{token}").data.decode()
    assert "% match" in page and "could not be produced for this report" in page and "secret provider detail" not in page
    admin_page = Browser(app, addr="127.0.0.1").get(f"/admin/runs/{app.extensions['svc'].db.run_by_token(token)['id']}").data.decode()
    assert "The overall reading failed" in admin_page


def test_a_retry_does_not_send_the_models_earlier_thinking_back(tmp_path):
    """Through OpenRouter the provider rejects an earlier thinking block ("Invalid signature in thinking block") when a retry
    replays it: that took a whole overall reading down in production. Retries send only the visible turns."""
    from config import Settings
    from db import DB
    from llm.agents import Agents
    from llm.tracing import Tracing
    from pydantic_ai.messages import ThinkingPart
    from test_llm import ENTRY
    db = DB(str(tmp_path / "t.db"))
    chunks = [{"id": "c1", "section": "Experience", "text": "Led product teams."}]
    more = [{"kind": "strength", "text": "Led teams again.", "basis": "on_record", "evidence_ids": ["c1"]},
            {"kind": "gap", "text": "Pricing not shown.", "basis": "on_record", "evidence_ids": []}]
    bad = {"level": "good", "summary": "Fine.", "points": [{"kind": "strength", "text": "Led teams.", "basis": "on_record", "evidence_ids": ["c9"]}, *more]}
    good = {**bad, "points": [{**bad["points"][0], "evidence_ids": ["c1"]}, *more]}
    replayed, calls = [], {"i": 0}

    def fn(messages, info):
        replayed.append(any(isinstance(p, ThinkingPart) for m in messages if isinstance(m, ModelResponse) for p in m.parts))
        reply = [bad, good][min(calls["i"], 1)]
        calls["i"] += 1
        return ModelResponse(parts=[ThinkingPart(content="let me think", signature="sig-bound-to-the-first-request"), TextPart(json.dumps(reply))])
    agents = Agents(ENTRY, Tracing(db, Settings(data_dir=tmp_path)), model=FunctionModel(fn, model_name="scripted"))
    out = agents.overall("posting text", [{"kind": "minimum", "text": "Led product teams", "verdict": "met", "override_verdict": None,
                                           "override_weight": None}], chunks)
    assert calls["i"] == 2 and replayed == [False, False] and out["points"][0]["evidence_ids"] == ["c1"]


def test_the_overall_reading_leads_the_report_and_its_detail_sits_in_a_closed_box_after_the_table(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    b = Browser(app, addr="203.0.113.251")
    html = b.get(f"/r/{token_of(public_check(b))}").data.decode()
    assert html.index('id="overall"') < html.index("Posting against the CV") < html.index('id="overall-detail"')
    head, detail = html[html.index('id="overall"'):html.index("Posting against the CV")], html[html.index('id="overall-detail"'):]
    assert "Good fit" in head and "What fits" not in head
    assert "What fits" in detail and not re.search(r'id="overall-detail"[^>]*\bopen\b', html)
