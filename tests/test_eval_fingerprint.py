"""Every eval run records what it ran with, so a change in a result can be traced to a cause."""
import re

import match
from llm import agents
from test_evals import run_eval, svc, with_model, careful  # noqa: F401


def test_a_run_records_the_model_the_prompt_and_the_flags_in_force(svc):
    with_model(svc, careful)
    run, _ = run_eval(svc, "synthetic-sanity")
    f = run["fingerprint"]
    assert f["model"] and f["whole_cv_chars"] == agents.WHOLE_CV_CHARS and f["max_tokens"] == agents.SETTINGS["max_tokens"]
    assert re.fullmatch(r"[0-9a-f]{8}", f["judge_rules"]) and re.fullmatch(r"[0-9a-f]{8}", f["dataset"]) and f["code"]
    assert f["equivalent_experience_lines"] is True


def test_the_fingerprint_follows_a_changed_prompt_or_flag(svc, monkeypatch):
    with_model(svc, careful)
    before, _ = run_eval(svc, "synthetic-sanity")
    monkeypatch.setattr(agents, "JUDGE_RULES", agents.JUDGE_RULES + " Another rule.")
    monkeypatch.setattr(agents, "WHOLE_CV_CHARS", 0)
    monkeypatch.setattr(match, "EQUIVALENT", re.compile(r"(?!)"))
    after, _ = run_eval(svc, "synthetic-sanity")
    a, b = before["fingerprint"], after["fingerprint"]
    assert a["judge_rules"] != b["judge_rules"] and b["whole_cv_chars"] == 0 and b["equivalent_experience_lines"] is False
    assert a["dataset"] == b["dataset"]


def test_a_rerun_can_say_which_state_of_the_app_it_stands_for(svc):
    eid = svc.db.create_eval_run("synthetic-sanity", "synthetic", "cli:haiku", as_of="whole-cv")
    assert svc.db.get_eval_run(eid)["as_of"] == "whole-cv"


def test_the_run_page_shows_what_the_run_ran_with(tmp_path, monkeypatch):
    from webfixtures import Browser, make_app
    app = make_app(tmp_path, monkeypatch)
    s = app.extensions["svc"]
    with_model(s, careful)
    eid = s.db.create_eval_run("synthetic-sanity", "synthetic", "cli:haiku", as_of="whole-cv")
    from evals import runner
    runner.execute(s, eid)
    page = Browser(app, addr="127.0.0.1").get(f"/admin/evals/{eid}").data.decode()
    assert "Ran with" in page and "Judge rules" in page and "Whole cv chars" in page and 'stands for the state "whole-cv"' in page
