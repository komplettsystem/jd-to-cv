"""The overall reading's label (Strong fit, Good fit, A stretch, Little overlap) is shown in the admin runs list, in the both-ways chart's
tooltip and in the list under the chart."""
import views
from webfixtures import Browser, make_app, public_check, token_of

TERMS = [{"name": "Location", "description": "Remote or a city the person accepts", "importance": "dealbreaker"}]


def make(tmp_path, monkeypatch, addr, overall):
    app = make_app(tmp_path, monkeypatch)
    svc = app.extensions["svc"]
    svc.db.set_json("terms", TERMS)
    b = Browser(app, addr=addr)
    run = svc.db.one("SELECT * FROM runs WHERE share_token = ?", (token_of(public_check(b)),))
    fit = svc.primary_result(run["id"])
    svc.db.set_overall(fit["id"], overall)
    return app, svc, run, Browser(app, addr="127.0.0.1")


def test_run_scores_carries_the_label_of_the_overall_reading(tmp_path, monkeypatch):
    app, svc, run, admin = make(tmp_path, monkeypatch, "203.0.113.241", {"level": "stretch", "summary": "x", "points": []})
    assert views.run_scores(svc.db)[run["id"]]["overall"] == "A stretch"


def test_a_failed_or_missing_overall_reading_has_no_label(tmp_path, monkeypatch):
    app, svc, run, admin = make(tmp_path, monkeypatch, "203.0.113.242", {"error": "ModelRequestRejectedError"})
    assert views.run_scores(svc.db)[run["id"]].get("overall") is None


def test_the_runs_list_has_an_overall_reading_column(tmp_path, monkeypatch):
    app, svc, run, admin = make(tmp_path, monkeypatch, "203.0.113.243", {"level": "good", "summary": "x", "points": []})
    html = admin.get("/admin/runs").data.decode()
    heads = html[html.index("<thead>"):html.index("</thead>")]
    assert heads.index("<th>Overall</th>") < heads.index("<th>Fit</th>") < heads.index("<th>Terms</th>")
    assert "Overall reading</th>" not in heads and "Good fit" in html


def test_the_chart_tooltip_and_the_list_under_it_name_the_reading(tmp_path, monkeypatch):
    app, svc, run, admin = make(tmp_path, monkeypatch, "203.0.113.244", {"level": "weak", "summary": "x", "points": []})
    html = admin.get("/admin/").data.decode()
    assert "overall reading Little overlap" in html          # tooltip and label of the point
    assert 'class="note">Little overlap' in html or ">Little overlap<" in html      # the list under the chart


def test_the_four_labels_stand_for_25_50_75_and_100_percent(tmp_path, monkeypatch):
    for n, (level, pct) in enumerate((("weak", 25), ("stretch", 50), ("good", 75), ("strong", 100))):
        folder = tmp_path / level
        folder.mkdir()
        app, svc, run, admin = make(folder, monkeypatch, f"203.0.113.{250 + n}", {"level": level, "summary": "x", "points": []})
        assert views.run_scores(svc.db)[run["id"]]["overall_pct"] == pct


def test_the_runs_list_draws_a_bar_under_terms_and_under_the_overall_reading_like_the_fit(tmp_path, monkeypatch):
    app, svc, run, admin = make(tmp_path, monkeypatch, "203.0.113.246", {"level": "good", "summary": "x", "points": []})
    html = admin.get("/admin/runs").data.decode()
    assert "<title>Fit: " in html and "<title>Terms: " in html and "<title>Overall reading: 75%</title>" in html


def test_no_bar_without_a_reading(tmp_path, monkeypatch):
    app, svc, run, admin = make(tmp_path, monkeypatch, "203.0.113.247", {"error": "failed"})
    assert "<title>Overall reading:" not in admin.get("/admin/runs").data.decode()
