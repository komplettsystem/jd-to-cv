"""When the CV is short, the judge reads all of it for every requirement instead of the five lines that share the most words."""
from llm import agents as agents_mod
from test_llm import CANDS, REQS, agents_for, env, judged_ids  # noqa: F401  (env is a fixture)

CHUNKS = [{"id": "c1", "section": "Experience", "text": "Led product teams."}, {"id": "c2", "section": "Experience", "text": "Ran evals."},
          {"id": "c3", "section": "Skills", "text": "Pricing work."}]


def test_the_whole_cv_is_shown_once_and_any_line_may_be_cited(env, monkeypatch):
    monkeypatch.setattr(agents_mod, "WHOLE_CV_CHARS", 10_000)
    agents, state = agents_for(env, judged_ids("c3", "c2"))          # c3 was not among requirement 0's five best lines
    out = agents.judge(REQS, CANDS, CHUNKS)
    assert [o["chunk_id"] for o in out] == ["c3", "c2"] and not any(o["flag"] for o in out) and state["i"] == 1
    prompt = " ".join(str(p) for p in state["prompts"][0])
    assert prompt.count("Ran evals.") == 1 and "Requirement 0" in prompt and "Requirement 1" in prompt


def test_a_long_cv_falls_back_to_the_best_matching_lines(env, monkeypatch):
    monkeypatch.setattr(agents_mod, "WHOLE_CV_CHARS", 10)               # the CV is longer than this
    agents, state = agents_for(env, judged_ids("c1", "c3"))
    out = agents.judge(REQS, CANDS, CHUNKS)
    assert [o["chunk_id"] for o in out] == ["c1", "c3"]
    assert "c3 | Skills" in " ".join(str(p) for p in state["prompts"][0]) and state["i"] == 1


def test_on_by_default_for_a_cv_of_ordinary_length():
    import cvfile
    assert agents_mod.WHOLE_CV_CHARS >= cvfile.MAX_TEXT      # every CV a visitor may upload is read whole
