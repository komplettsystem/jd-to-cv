"""The benchmark over time: history and live runs merged, the change log, and the chart."""
from evals import timeline
from web import charts


def rec(state, model, passed, run=0, planned=36):
    return {"state": state, "model": model, "run": run, "status": "done", "passed": passed, "planned": planned,
            "over_credit": 0.0, "under_credit": 0.1, "cost_usd": 0.01}


def test_the_change_log_is_well_formed_and_each_state_belongs_to_one_entry():
    log = timeline.changes()
    assert len({c["id"] for c in log}) == len(log)
    for c in log:
        assert {"id", "date", "kind", "deployed", "title", "text", "measured", "state", "commits"} <= set(c)
        assert c["kind"] in ("code", "prompt", "model", "feature", "study", "state")
    states = [c["state"] for c in log if c["state"]]
    assert len(states) == len(set(states)) and {"start", "tightened", "degree-rule", "whole-cv"} <= set(states)
    assert [c["date"] for c in log] == sorted(c["date"] for c in log)


def test_runs_are_averaged_per_state_and_model_and_a_parked_state_has_no_delta(monkeypatch):
    monkeypatch.setattr(timeline, "history", lambda: [rec("start", "haiku", 26, 0), rec("start", "haiku", 28, 1), rec("tightened", "haiku", 27),
                                                     rec("degree-rule", "haiku", 29), rec("whole-cv", "haiku", 33)])
    tl = timeline.build([])
    p = tl["points"]["haiku"]
    assert p["start"]["mean"] == 27 and p["start"]["runs"] == 2 and (p["start"]["lo"], p["start"]["hi"]) == (26, 28)
    assert [s["id"] for s in tl["states"]] == ["start", "tightened", "degree-rule", "whole-cv"]
    d = timeline.deltas(tl)["haiku"]
    assert d == {"degree-rule": 2, "whole-cv": 4} and "tightened" not in d


def test_a_finished_benchmark_run_in_the_database_is_added_as_a_live_column(monkeypatch):
    monkeypatch.setattr(timeline, "history", lambda: [rec("start", "haiku", 26)])
    live = {"id": 7, "dataset": timeline.DATASET, "status": "done", "backend": "openrouter:anthropic/haiku", "as_of": "",
            "summary": {"passed": 31, "planned": 36, "metrics": {"over_credit_rate": 0.0, "under_credit_rate": 0.05, "cost_usd": 0.013}}}
    other = dict(live, id=8, dataset="synthetic-sanity")
    tl = timeline.build([live, other])
    assert tl["states"][-1]["id"] == "latest" and tl["points"]["haiku"]["latest"]["mean"] == 31


def test_nothing_to_show_gives_none_and_an_empty_chart(monkeypatch):
    monkeypatch.setattr(timeline, "history", lambda: [])
    assert timeline.build([]) is None and str(charts.benchmark_timeline(None)) == ""


def test_the_chart_draws_a_line_for_deployed_states_and_a_hollow_point_for_a_parked_one(monkeypatch):
    monkeypatch.setattr(timeline, "history", lambda: [rec(s, m, v) for m, vals in (("haiku", (26, 27, 29, 33)), ("terra", (24, 25, 26, 28)))
                                                     for s, v in zip(("start", "tightened", "degree-rule", "whole-cv"), vals)])
    svg = str(charts.benchmark_timeline(timeline.build([])))
    assert svg.count("<polyline") == 2 and "tl-hollow" in svg and "<title>The benchmark over time</title>" in svg
    assert "Whole CV" in svg and "style=" not in svg
    assert timeline.deltas(timeline.build([]))["terra"] == {"degree-rule": 2, "whole-cv": 2}


def test_the_evals_page_shows_the_timeline_the_change_log_and_the_plain_cards(tmp_path, monkeypatch):
    from webfixtures import Browser, make_app
    monkeypatch.setattr(timeline, "history", lambda: [rec(s, m, v) for m, vals in (("haiku", (26, 27, 29, 33)), ("terra", (24, 25, 26, 28)))
                                                     for s, v in zip(("start", "tightened", "degree-rule", "whole-cv"), vals)])
    page = Browser(make_app(tmp_path, monkeypatch), addr="127.0.0.1").get("/admin/evals").data.decode()
    assert "How the app" in page and 'class="chart bench-chart"' in page and "tried, not deployed" in page
    assert "What changed, and what it did" in page and "From word matching to reading" in page and "Benchmark, cases passed of" in page
    assert "What each eval asks" in page and "Cannot tell you:" in page and "win-back email series" in page
    assert "<table" in page and "+4.0" in page


def test_runs_stored_in_the_database_for_a_state_replace_the_files_runs_for_that_state(monkeypatch):
    monkeypatch.setattr(timeline, "history", lambda: [rec("start", "haiku", 26, 0), rec("start", "haiku", 28, 1), rec("whole-cv", "haiku", 33)])
    stored = {"id": 3, "dataset": timeline.DATASET, "status": "done", "backend": "openrouter:anthropic/haiku", "as_of": "start",
              "summary": {"passed": 20, "planned": 36, "metrics": {}}}
    tl = timeline.build([stored])
    assert tl["points"]["haiku"]["start"]["runs"] == 1 and tl["points"]["haiku"]["start"]["mean"] == 20
    assert tl["points"]["haiku"]["whole-cv"]["mean"] == 33
