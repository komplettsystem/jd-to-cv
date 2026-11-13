"""The owner can change which CV line a requirement rests on, not only the verdict. One line per requirement, as the model gives it."""
import re

import pytest

from webfixtures import Browser, make_app
from test_match import POSTING


@pytest.fixture
def app(tmp_path, monkeypatch):
    return make_app(tmp_path, monkeypatch)


@pytest.fixture
def admin(app):
    b = Browser(app, addr="127.0.0.1")
    return b


def run_with_model(admin):
    r = admin.post("/admin/new", {"posting_text": POSTING, "title": "Search PM", "company": "Acme", "backends": ["cli:haiku"]})
    assert r.status_code == 302, r.data
    return int(r.headers["Location"].split("/")[-1])


def first_cited_item(admin, rid):
    page = admin.get(f"/admin/runs/{rid}").data.decode()
    for m in re.finditer(r'<li class="req" id="item-(\d+)".*?</li>', page, re.S):
        if 'name="override_chunk"' in m.group(0) and "cv-line" in m.group(0):
            return int(m.group(1)), page
    raise AssertionError("no requirement with a cited line and a line picker")


def test_the_change_form_offers_the_cv_lines_with_the_best_matches_first(admin):
    rid = run_with_model(admin)
    item_id, page = first_cited_item(admin, rid)
    form = re.search(rf'id="item-{item_id}".*?(<select name="override_chunk".*?</select>)', page, re.S).group(1)
    assert "Best matches" in form and "Other lines" in form and '<option value="">' in form


def test_choosing_another_line_changes_what_the_report_shows_and_keeps_the_models_choice(app, admin):
    rid = run_with_model(admin)
    item_id, page = first_cited_item(admin, rid)
    db = app.extensions["svc"].db
    before = db.get_item(item_id)
    form = re.search(rf'id="item-{item_id}".*?(<select name="override_chunk".*?</select>)', page, re.S).group(1)
    options = re.findall(r'<option value="([^"]+)"', form)
    other = next(o for o in options if o != before["chunk_id"])
    admin.post(f"/admin/items/{item_id}", {"override_verdict": "", "override_weight": "", "override_chunk": other})
    item = next(i for i in db.items_for(rid_result(db, rid)) if i["id"] == item_id)
    assert item["chunk_id"] == other and item["evidence"] != before["evidence"] and item["flag"] is None
    assert "your line" in admin.get(f"/admin/runs/{rid}").data.decode()
    model_item = next(i for i in db.items_for(rid_result(db, rid), as_model=True) if i["id"] == item_id)
    assert model_item["chunk_id"] == before["chunk_id"] and model_item["evidence"] == before["evidence"]
    admin.post(f"/admin/items/{item_id}", {"override_verdict": "", "override_weight": "", "override_chunk": ""})
    back = next(i for i in db.items_for(rid_result(db, rid)) if i["id"] == item_id)
    assert back["chunk_id"] == before["chunk_id"]


def test_an_unknown_line_is_refused(admin):
    rid = run_with_model(admin)
    item_id, _ = first_cited_item(admin, rid)
    assert admin.post(f"/admin/items/{item_id}", {"override_verdict": "", "override_weight": "", "override_chunk": "nope"}).status_code == 400


def rid_result(db, run_id):
    return next(r["id"] for r in db.results_for(run_id, "fit"))
