"""The thirty-case benchmark, with scripted models only."""
import pytest

import match
from evals import benchmark, registry
from test_evals import (MODEL, _instructions, _judge_blocks, _user_text, reply, run_eval, svc, with_model)  # noqa: F401  (svc is a fixture)

EXPECTED = {}
for c in benchmark.CASES:
    for r in c["requirements"]:
        EXPECTED.setdefault(r["text"], r)


def test_thirty_six_cases_each_written_so_the_answer_follows_from_the_text():
    assert len(benchmark.CASES) == 36 and len({c["name"] for c in benchmark.CASES}) == 36
    for c in benchmark.CASES:
        chunks = match.chunk_cv(benchmark.cv_text(c))
        assert [x["text"] for x in chunks] == c["cv"], c["name"]
        assert 6 <= len(c["cv"]) <= 12 and 2 <= len(c["requirements"]) <= 4, c["name"]
        for r in c["requirements"]:
            assert r["text"] in c["posting"] and set(r["verdict"]) <= set(match.VERDICTS), (c["name"], r["text"])
            if r["evidence"]:
                assert any(r["evidence"].lower() in line.lower() for line in c["cv"]), (c["name"], r["evidence"])
    same_text = {}
    for c in benchmark.CASES:
        for r in c["requirements"]:
            same_text.setdefault(r["text"], []).append((r["verdict"], r["evidence"]))
    assert all(len(set(v)) == 1 for v in same_text.values())     # a requirement that recurs has one right answer


def test_every_group_is_present_and_the_dataset_is_listed():
    assert {c["trap"] for c in benchmark.CASES} >= {"paraphrase", "neighbour", "numbers", "degree", "absence", "hostile", "kinds", "evidence", "overcredit"}
    assert "reverse-apply-bench" in [d["name"] for d in registry.datasets()]


def answering(verdict_of):
    def model(messages, info):
        ins, text = _instructions(messages), _user_text(messages)
        if ins.startswith("You read a job posting"):
            case = next(c for c in benchmark.CASES if c["posting"] in text)
            return reply({"requirements": [{"text": r["text"], "kind": r["kind"] or "minimum"} for r in case["requirements"]]})
        out = []
        for i, (req_text, lines) in enumerate(_judge_blocks(text)):
            want = EXPECTED[req_text]
            verdict = verdict_of(want)
            eid = None
            if verdict != "not_on_record":
                eid = next((cid for cid, line in lines if want["evidence"] and want["evidence"].lower() in line.lower()), lines[0][0])
            out.append({"idx": i, "verdict": verdict, "evidence_id": eid, "reason": "as written"})
        return reply({"judgements": out})
    return model


def written(want):
    verdict = want["verdict"][0]
    return "not_on_record" if verdict == "partial" and not want["evidence"] else verdict


def test_a_model_that_answers_as_written_passes_every_case_and_credits_nothing_wrongly(svc):
    with_model(svc, answering(written))
    run, cases = run_eval(svc, "reverse-apply-bench")
    failed = {c["name"]: c["output"].get("why_failed") or c["error"] for c in cases if not c["passed"]}
    assert run["status"] == "done" and not failed
    m = run["summary"]["metrics"]
    assert run["summary"]["pass_rate"] == 1.0 and m["over_credit_rate"] == 0.0 and m["under_credit_rate"] == 0.0


def test_a_generous_model_shows_up_as_over_crediting_and_a_strict_one_as_under_crediting(svc):
    with_model(svc, answering(lambda w: "met"))
    generous, _ = run_eval(svc, "reverse-apply-bench")
    assert generous["summary"]["metrics"]["over_credit_rate"] == 1.0 and generous["summary"]["metrics"]["under_credit_rate"] == 0.0
    assert generous["summary"]["pass_rate"] < 0.5
    with_model(svc, answering(lambda w: "partial"))
    strict, _ = run_eval(svc, "reverse-apply-bench")
    assert strict["summary"]["metrics"]["under_credit_rate"] == 1.0
