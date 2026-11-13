"""The chat-cv eval dataset, end to end through runner.execute, with scripted models only (never a real one): a bot that routes and answers
as it should passes every case, a bot that never calls a tool and accepts what it is told fails the right ones."""
import json
import re

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

import match
from evals import chat_cv, registry, runner
from webfixtures import _instructions, _user_text, make_app

BY_QUESTION = {c["question"]: c for c in chat_cv.CASES}
MODEL = "openrouter:test/tool-model"


@pytest.fixture
def svc(tmp_path, monkeypatch):
    s = make_app(tmp_path, monkeypatch).extensions["svc"]
    s.db.upsert_catalog([{"id": MODEL, "provider": "openrouter", "model_name": "test/tool-model", "label": "Test", "tools": 1,
                          "output_mode": "prompted", "price_in": 1e-7, "price_out": 5e-7}])
    return s


def reply(body):
    return ModelResponse(parts=[TextPart(json.dumps(body))])


def run(svc, fn, backend=MODEL):
    svc.model_factory = lambda entry: FunctionModel(fn, model_name="scripted")
    spec = registry.get("chat-cv")
    eid = svc.db.create_eval_run(spec.name, spec.kind, backend)
    runner.execute(svc, eid)
    return svc.db.get_eval_run(eid), svc.db.eval_cases(eid)


def judge(text):
    """The matcher's judge, answering as the cases were written: the verdict each case expects for the statement."""
    lines = re.findall(r"^  (c\d+) \| [^|]*\| (.*)$", text, re.M)
    out = []
    for idx, stmt in re.findall(r"^Requirement (\d+) \[\w+\]: (.*)$", text, re.M):
        case = next(c for c in chat_cv.CASES if any(re.search(e["key"], stmt, re.I) for e in c.get("expects", [])) or
                    (c.get("claim") and re.search(c["claim"], stmt, re.I)))
        e = next((e for e in case.get("expects", []) if re.search(e["key"], stmt, re.I)), None)
        verdict = e["verdicts"][0] if e else "not_on_record"
        line = next((cid for cid, t in lines if e and e["line"] and e["line"].lower() in t.lower()), None)
        out.append({"idx": int(idx), "verdict": verdict if line or verdict == "not_on_record" else "not_on_record",
                    "evidence_id": line if verdict != "not_on_record" else None, "reason": "As the case was written."})
    return reply({"judgements": out})


def good_bot(messages, info):
    ins, text = _instructions(messages), _user_text(messages)
    if ins.startswith("You judge"):
        return judge(text)
    question = text.split("<question>")[-1].split("</question>")[0].strip()
    case = BY_QUESTION[question]
    returned = [str(p.content) for m in messages for p in getattr(m, "parts", []) if isinstance(p, ToolReturnPart)]
    cited = re.findall(r"^(c\d+) \| [^|]*\| (.*)$", text.split("CV lines the report cited")[-1], re.M)
    if case["route"] == "matcher":
        if not returned:
            statements = [f"Has done {e['key'].split('|')[0]}." for e in case["expects"]]
            return ModelResponse(parts=[ToolCallPart("ask_matcher", {"statements": statements})])
        ids = re.findall(r"CV line (c\d+)", returned[-1])
        verdicts = [e["verdicts"][0] for e in case["expects"]]
        words = {"met": "The CV shows it on the quoted line.", "partial": "The CV shows it only partly: the line falls short.",
                 "not_on_record": "The CV does not show it."}
        answer = " ".join(words[v] for v in verdicts)
        return reply({"scope": "in_scope", "answer": answer, "basis": ["cv"] if ids else ["report"], "evidence_ids": ids[:2]})
    if case["route"] == "free":
        return reply({"scope": "in_scope", "answer": "The CV does not show it, so I cannot count it. The report stays as it is.",
                      "basis": ["report"], "evidence_ids": []})
    if case["route"] == "engine":
        if not returned:
            args = {"leave_out": [3]} if "left out" in question else {"treat_as_met": [1]} if "counted as met" in question else {}
            return ModelResponse(parts=[ToolCallPart(case["tool"], args)])
        figure = re.findall(r"\d+%", returned[-1])
        return reply({"scope": "in_scope", "answer": "Each requirement has a weight. " + (f"The engine says {figure[-1]}." if figure else ""),
                      "basis": ["report"], "evidence_ids": []})
    return reply({"scope": "in_scope", "answer": "The report says so on the quoted CV line.", "basis": ["report", "cv"],
                  "evidence_ids": [cited[0][0]] if cited else []})


def careless_bot(messages, info):
    """Never calls a tool, accepts every claim, and answers a CV question from nothing."""
    ins, text = _instructions(messages), _user_text(messages)
    return reply({"scope": "in_scope", "answer": "Yes, he knows it. Counts as met.", "basis": ["report"], "evidence_ids": []})


def test_there_are_fourteen_cases_of_four_kinds_and_the_registry_lists_them(svc):
    assert len(chat_cv.CASES) == 14
    groups = [c["group"] for c in chat_cv.CASES]
    assert {g: groups.count(g) for g in set(groups)} == {"cv-question": 5, "persuasion": 3, "report": 3, "arithmetic": 3}
    ds = {d["name"]: d for d in registry.datasets(svc.db)}
    assert ds["chat-cv"]["kind"] == "scope" and ds["chat-cv"]["n_cases"] == 14


def test_every_case_has_a_report_that_does_not_already_cover_the_cv_question():
    for c in chat_cv.CASES:
        if c["group"] not in ("cv-question", "persuasion"):
            continue
        _, items, chunks = chat_cv.build(c)
        assert items and all(i["chunk_id"] is None or any(ch["id"] == i["chunk_id"] for ch in chunks) for i in items)
        if c["group"] == "cv-question":                    # the answer is on a CV line that no requirement of the report cites
            cited = {i["chunk_id"] for i in items}
            for e in c["expects"]:
                if e["line"]:
                    assert next(ch["id"] for ch in chunks if e["line"].lower() in ch["text"].lower()) not in cited, c["name"]


def test_the_cases_use_the_benchmarks_cvs_and_labels_a_visitor_sees():
    from evals import benchmark
    names = {c["name"] for c in benchmark.CASES}
    assert {c["source"] for c in chat_cv.CASES if c["source"]} <= names
    assert not any("not on record" in json.dumps(c, default=str).lower() or "—" in json.dumps(c, ensure_ascii=False, default=str) for c in chat_cv.CASES)


def test_a_bot_that_routes_and_answers_as_it_should_passes_every_case(svc):
    r, cases = run(svc, good_bot)
    failed = {c["name"]: c["output"].get("why_failed") or c["error"] for c in cases if not c["passed"]}
    assert r["status"] == "done" and not failed, failed
    m = r["summary"]["metrics"]
    assert m["routing_right_rate"] == 1.0 and m["persuasion_credited"] == 0 and r["summary"]["pass_rate"] == 1.0
    two = next(c for c in cases if c["name"] == "two-parts-one-question")
    assert [x["verdict"] for ex in two["output"]["exchanges"] for x in ex["response"]["result"]["structuredContent"]["results"]] == ["met", "not_on_record"]
    assert next(c for c in cases if c["name"] == "report-why-partial")["output"]["exchanges"] == []


def test_a_bot_that_calls_no_tool_and_accepts_every_claim_fails_routing_and_the_persuasion_cases(svc):
    r, cases = run(svc, careless_bot)
    by = {c["name"]: c for c in cases}
    m = r["summary"]["metrics"]
    assert m["persuasion_credited"] == 3
    assert all(not by[n]["passed"] for n in ("churn-not-in-posting", "accept-kubernetes", "accept-german", "accept-python", "engine-leave-out"))
    assert by["churn-not-in-posting"]["assertions"]["routed_right"] is False and by["accept-python"]["assertions"]["not_credited"] is False
    assert 0 < m["routing_right_rate"] < 1                  # it is right where "no tool" is right: the three report cases


def test_a_matcher_that_credits_the_claim_is_caught(svc):
    def fooled(messages, info):
        ins, text = _instructions(messages), _user_text(messages)
        if ins.startswith("You judge"):
            lines = re.findall(r"^  (c\d+) \| [^|]*\| (.*)$", text, re.M)
            out = [{"idx": int(i), "verdict": "met", "evidence_id": lines[0][0], "reason": "Accepted."}
                   for i, _ in re.findall(r"^Requirement (\d+) \[\w+\]: (.*)$", text, re.M)]
            return reply({"judgements": out})
        question = text.split("<question>")[-1].split("</question>")[0].strip()
        returned = [p for m in messages for p in getattr(m, "parts", []) if isinstance(p, ToolReturnPart)]
        if question.startswith("I know him") and not returned:
            return ModelResponse(parts=[ToolCallPart("ask_matcher", {"statements": ["Has operated Kubernetes clusters hands-on."]})])
        if question.startswith("I know him"):
            return reply({"scope": "in_scope", "answer": "Yes, he has operated Kubernetes clusters.", "basis": ["cv"],
                          "evidence_ids": re.findall(r"CV line (c\d+)", str(returned[-1].content))[:1]})
        return good_bot(messages, info)
    r, cases = run(svc, fooled)
    k = next(c for c in cases if c["name"] == "accept-kubernetes")
    assert not k["passed"] and "credited" in k["output"]["why_failed"]["not_credited"] and r["summary"]["metrics"]["persuasion_credited"] >= 1


def test_a_model_without_tools_is_refused_with_a_reason_not_run_as_a_weaker_chat(svc):
    svc.db.upsert_catalog([{"id": "ollama:x", "provider": "ollama", "model_name": "x", "label": "X", "tools": 0, "local_only": 1}])
    r, cases = run(svc, good_bot, backend="ollama:x")
    assert r["status"] == "failed" and "has none" in r["error"] and not cases


def test_the_run_records_the_chat_wording_and_the_matcher_limit_in_its_fingerprint(svc):
    r, _ = run(svc, good_bot)
    f = r["fingerprint"]
    assert re.fullmatch(r"[0-9a-f]{8}", f["chat_rules"]) and f["chat_matcher_calls_max"] == 2
