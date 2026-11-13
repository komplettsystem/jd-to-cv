"""The server-side SVG charts: empty data, one point, many points, escaping, and no inline styles (the CSP forbids them)."""
import re

import pytest
from flask import Flask

from web import charts


@pytest.fixture()
def ctx():
    app = Flask(__name__)
    app.add_url_rule("/admin/runs/<int:run_id>", "admin.run_view", lambda run_id: "")
    app.add_url_rule("/admin/evals/<int:eval_run_id>", "admin.eval_view", lambda eval_run_id: "")
    charts.register(app)
    with app.test_request_context():
        yield app


def point(i, **extra):
    return {"run_id": i, "label": f"Company {i}", "x": (i * 37) % 101, "y": (i * 53) % 101, "conflicts": 0, **extra}


def assert_clean(html):
    assert "style=" not in html and "<script" not in html and "onclick" not in html


def test_register_adds_the_globals_and_filters(ctx):
    for name in ("chart_quadrant", "chart_trend", "chart_funnel", "chart_meter", "chart_bar", "chart_span",
                 "chart_cost_quality", "chart_eval_matrix"):
        assert name in ctx.jinja_env.globals
    assert ctx.jinja_env.filters["short_model"]("openrouter:anthropic/claude-haiku-5.5") == "claude-haiku-5.5"
    assert ctx.jinja_env.filters["day"]("2026-11-11T20:11:13+00:00") == "11 Nov 2026"
    assert ctx.jinja_env.filters["day"]("") == "" and ctx.jinja_env.filters["day"]("soon") == "soon"


def test_empty_data_draws_nothing(ctx):
    assert charts.quadrant([]) == "" and charts.quadrant(None) == ""
    assert charts.quadrant([{"run_id": 1, "label": "No terms score", "x": 50, "y": None}]) == ""
    assert charts.trend([]) == "" and charts.funnel([]) == "" and charts.cost_quality([]) == ""
    assert charts.eval_matrix([]) == "" and charts.meter(None) == "" and charts.bar(None) == ""
    assert charts.bar(3, 0) == ""
    assert charts.eval_matrix([{"id": 1, "status": "running", "summary": {}, "dataset": "d", "backend": "m"}]) == ""


def test_quadrant_one_point(ctx):
    svg = str(charts.quadrant([point(1, x=80, y=20, conflicts=1)]))
    assert svg.startswith("<svg") and svg.count("<circle") == 1
    assert "<title" in svg and "<desc" in svg and "1 posting." in svg
    assert 'href="/admin/runs/1"' in svg and "q-cross" in svg and "dealbreaker conflict" in svg
    assert_clean(svg)


def test_quadrant_many_points_stay_inside_the_plot(ctx):
    pts = [point(i) for i in range(40)] + [point(99, x=250, y=-30)]
    svg = str(charts.quadrant(pts))
    assert svg.count("<circle") == 41
    for cx, cy in re.findall(r'<circle[^>]* cx="([\d.]+)" cy="([\d.]+)"', svg):
        assert charts.Q_L <= float(cx) <= charts.Q_R and charts.Q_T <= float(cy) <= charts.Q_B
    assert_clean(svg)


def test_labels_are_escaped(ctx):
    evil = '<script>alert(1)</script> & "Co"'
    for html in (
        str(charts.quadrant([point(1, label=evil)])),
        str(charts.funnel([{"label": evil, "count": 10, "count_text": "10", "source": evil, "measured": evil}])),
        str(charts.cost_quality([{"label": evil, "cost": 0.01, "quality": 0.5}])),
        str(charts.eval_matrix([{"id": 1, "status": "done", "dataset": evil, "dataset_kind": evil, "backend": evil,
                                 "n_cases": 3, "summary": {"pass_rate": 0.5}}])),
        str(charts.meter(50, evil)), str(charts.bar(1, 2, evil)),
    ):
        assert "<script>" not in html and "&lt;script&gt;" in html
        assert_clean(html)


def test_trend_scales_each_strip_and_survives_all_zero_days(ctx):
    days = [{"day": f"2026-11-{d:02d}", "runs": d % 4, "requests": d * 3, "spend": d * 0.01} for d in range(1, 15)]
    html = str(charts.trend(days))
    assert html.count("<svg") == 3 and html.count('class="trend-bar"') == 42
    assert "1 Nov" in html and "14 Nov" in html and "<title>" in html
    quiet = str(charts.trend([{"day": "2026-11-01", "runs": 0, "requests": 0, "spend": 0.0}]))
    assert 'height="0"' in quiet and "most in a day: 0" in quiet
    assert_clean(html + quiet)


def test_funnel_marks_capped_counts_and_tiny_shares(ctx):
    steps = [{"label": "Title", "count": 412000, "count_text": "412K", "source": "LinkedIn, by hand", "measured": "2026-11-11"},
             {"label": "Search", "count": 1000, "count_text": "1K+", "source": "", "measured": "", "share_of_previous": 0.0024}]
    html = str(charts.funnel(steps))
    assert html.count("<li") == 2 and "412,000" in html and "1,000+" in html
    assert "under 1% of the step above" in html and "LinkedIn, by hand, 11 Nov 2026" in html
    assert charts.funnel([{"label": "Not counted", "count": None}]) == ""
    assert_clean(html)


def test_meter_and_bars_clamp(ctx):
    assert 'width="100"' in str(charts.meter(140)).split("meter-fill")[1]
    assert 'width="0"' in str(charts.meter(-5)).split("meter-fill")[1]
    assert 'width="50"' in str(charts.bar(1, 2)).split("cellbar-fill")[1]
    wide = str(charts.span_bar(900, 500, 1000, "chat"))
    x, w = map(float, re.search(r'wf-chat" x="([\d.]+)" y="1" width="([\d.]+)"', wide).groups())
    assert x + w <= 1000
    assert 'width="1000"' in str(charts.span_bar(0, 0, 0)) and 'class="wf-fill wf-ab"' in str(charts.span_bar(0, 1, 2, "a b<"))


def test_cost_quality_leaves_out_models_without_both_numbers(ctx):
    models = [{"label": "a", "cost": 0.002, "quality": 0.9}, {"label": "b", "cost": None, "quality": 0.5},
              {"label": "c", "cost": 0.02, "quality": None}]
    svg = str(charts.cost_quality(models))
    assert svg.count("<circle") == 1 and "2 left out" in svg
    assert str(charts.cost_quality([{"label": "free", "cost": 0, "quality": 1}])).count("<circle") == 1
    many = str(charts.cost_quality([{"label": f"m{i}", "cost": i / 1000, "quality": i / 30} for i in range(30)]))
    assert many.count("<circle") == 30
    assert_clean(svg + many)


def test_eval_matrix_uses_the_newest_finished_run_per_cell(ctx):
    runs = [{"id": 1, "status": "done", "dataset": "synthetic", "dataset_kind": "synthetic", "backend": "p:a/m1",
             "n_cases": 10, "summary": {"pass_rate": 0.5}},
            {"id": 2, "status": "done", "dataset": "synthetic", "dataset_kind": "synthetic", "backend": "p:a/m1",
             "n_cases": 10, "summary": {"pass_rate": 0.9}},
            {"id": 3, "status": "done", "dataset": "labels", "dataset_kind": "labels", "backend": "p:b/m2",
             "n_cases": 4, "summary": {"pass_rate": 1.0}},
            {"id": 4, "status": "failed", "dataset": "labels", "dataset_kind": "labels", "backend": "p:a/m1",
             "n_cases": 4, "summary": {}}]
    html = str(charts.eval_matrix(runs))
    assert "<caption>" in html and "90%" in html and "50%" not in html and "100%" in html
    assert 'href="/admin/evals/2"' in html and html.count("not run") == 2
    assert_clean(html)


def test_by_kind_never_blends_kinds_and_takes_the_newest_run(ctx):
    runs = [{"id": 1, "status": "done", "dataset": "s1", "dataset_kind": "synthetic", "backend": "m", "n_cases": 12, "summary": {"pass_rate": 0.2}},
            {"id": 2, "status": "done", "dataset": "s1", "dataset_kind": "synthetic", "backend": "m", "n_cases": 12, "summary": {"pass_rate": 1.0}},
            {"id": 3, "status": "done", "dataset": "j", "dataset_kind": "judge", "backend": "m", "n_cases": 40, "summary": {"pass_rate": 0.5}},
            {"id": 4, "status": "failed", "dataset": "c", "dataset_kind": "scope", "backend": "m", "n_cases": 18, "summary": {}}]
    assert charts.by_kind(runs) == {"m": [("synthetic", 1.0, 12), ("judge", 0.5, 40)]}
    assert charts.by_kind([]) == {} and charts.by_kind(None) == {}
