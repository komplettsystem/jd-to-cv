"""A requirement is the owner's label only when he actively confirmed it or changed it. A verdict he never looked at is not a label,
so the admin tells the three states apart: not reviewed, confirmed, changed."""
import re

import pytest

import views
from webfixtures import Browser, make_app
from test_match import POSTING


@pytest.fixture
def app(tmp_path, monkeypatch):
    return make_app(tmp_path, monkeypatch)


@pytest.fixture
def admin(app):
    return Browser(app, addr="127.0.0.1")


def make_run(admin, app):
    r = admin.post("/admin/new", {"posting_text": POSTING, "title": "Search PM", "company": "Acme", "backends": ["cli:haiku"]})
    assert r.status_code == 302, r.data
    run_id = int(r.headers["Location"].split("/")[-1])
    db = app.extensions["svc"].db
    result_id = db.results_for(run_id, "fit")[0]["id"]
    return run_id, result_id, db.items_for(result_id)


def states(db, result_id):
    from match import item_state
    return [item_state(i) for i in db.items_for(result_id)]


def test_every_requirement_starts_not_reviewed_and_the_page_offers_a_confirm_button(app, admin):
    run_id, result_id, items = make_run(admin, app)
    assert len(items) == 2 and states(app.extensions["svc"].db, result_id) == ["unreviewed", "unreviewed"]
    page = admin.get(f"/admin/runs/{run_id}").data.decode()
    assert page.count("Not reviewed") >= 2
    for it in items:
        assert f'/admin/items/{it["id"]}/confirm' in page
    assert "Mark reviewed" not in page


def test_confirming_marks_one_requirement_and_can_be_undone(app, admin):
    run_id, result_id, items = make_run(admin, app)
    db = app.extensions["svc"].db
    admin.post(f"/admin/items/{items[0]['id']}/confirm", {"value": "1"})
    assert states(db, result_id) == ["confirmed", "unreviewed"]
    assert "Confirmed" in admin.get(f"/admin/runs/{run_id}").data.decode()
    admin.post(f"/admin/items/{items[0]['id']}/confirm", {"value": "0"})
    assert states(db, result_id) == ["unreviewed", "unreviewed"]


def test_changing_a_verdict_counts_as_a_label_without_a_confirm(app, admin):
    run_id, result_id, items = make_run(admin, app)
    db = app.extensions["svc"].db
    admin.post(f"/admin/items/{items[0]['id']}", {"override_verdict": "partial", "override_weight": ""})
    assert states(db, result_id) == ["changed", "unreviewed"]
    admin.post(f"/admin/items/{items[0]['id']}", {"override_verdict": "", "override_weight": ""})  # back to the model's verdict
    assert states(db, result_id) == ["unreviewed", "unreviewed"]  # clearing a change is not a confirmation


def test_confirm_remaining_confirms_only_what_is_not_reviewed(app, admin):
    run_id, result_id, items = make_run(admin, app)
    db = app.extensions["svc"].db
    admin.post(f"/admin/items/{items[0]['id']}", {"override_verdict": "partial", "override_weight": ""})
    admin.post(f"/admin/results/{result_id}/confirm-all", {})
    assert states(db, result_id) == ["changed", "confirmed"]
    assert db.get_item(items[0]["id"])["confirmed"] == 0     # the changed one was not touched


def test_a_report_counts_as_reviewed_when_every_requirement_is_confirmed_or_changed(app, admin):
    run_id, result_id, items = make_run(admin, app)
    page = admin.get(f"/admin/runs/{run_id}").data.decode()
    assert re.search(r"0 of 2 requirements? (reviewed|confirmed)", page)
    admin.post(f"/admin/items/{items[0]['id']}/confirm", {"value": "1"})
    admin.post(f"/admin/items/{items[1]['id']}", {"override_verdict": "met", "override_weight": ""})
    page = admin.get(f"/admin/runs/{run_id}").data.decode()
    assert re.search(r"2 of 2 requirements? (reviewed|confirmed)", page) and '<span class="tag">Reviewed</span>' in page


def test_the_agreement_figure_counts_only_confirmed_and_changed_requirements(app, admin):
    run_id, result_id, items = make_run(admin, app)
    db = app.extensions["svc"].db
    row = lambda: next(m for m in views.model_stats(db) if m["done"])
    assert row()["n_items"] == 0 and row()["agreement"] is None            # nothing reviewed: no figure
    admin.post(f"/admin/items/{items[0]['id']}/confirm", {"value": "1"})
    assert (row()["n_items"], row()["unchanged"], row()["agreement"]) == (1, 1, 1.0)
    admin.post(f"/admin/items/{items[1]['id']}", {"override_verdict": "partial", "override_weight": ""})
    assert (row()["n_items"], row()["unchanged"], row()["agreement"]) == (2, 1, 0.5)


def test_a_visitor_never_sees_the_review_controls(app, admin):
    run_id, result_id, items = make_run(admin, app)
    token = app.extensions["svc"].db.get_run(run_id)["share_token"]
    page = Browser(app, addr="198.51.100.9").get(f"/r/{token}").data.decode()
    assert "Not reviewed" not in page and "/confirm" not in page
