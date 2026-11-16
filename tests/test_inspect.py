"""deploy/inspect.py shows errors, recent reports and spend, and never a posting, a CV line or a chat text."""
import os
import subprocess
import sys
from pathlib import Path

from webfixtures import Browser, PUBLIC_POSTING, make_app, public_check, token_of

SCRIPT = Path(__file__).resolve().parent.parent / "deploy" / "inspect.py"


def run(db_path, what):
    out = subprocess.run([sys.executable, "-I", str(SCRIPT), what], env={"MATCH_DB": str(db_path)}, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return out.stdout


def test_inspect_shows_metadata_and_errors_but_no_texts(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    b = Browser(app, addr="203.0.113.231")
    token_of(public_check(b))
    db = app.extensions["svc"].db
    db.run("UPDATE results SET overall=?", ('{"error": "UnexpectedModelBehavior: figures that are not in the posting"}',))
    db.run("UPDATE runs SET title='Search PM'")
    text = run(tmp_path / "t.db", "errors") + run(tmp_path / "t.db", "recent") + run(tmp_path / "t.db", "spend")
    assert "figures that are not in the posting" in text and "Search PM" in text and "== model spend" in text
    assert PUBLIC_POSTING[:60] not in text and "Zebrafish" not in text


def test_labels_shows_only_reports_with_confirmed_or_changed_requirements_and_never_a_cv_line_or_a_reason(tmp_path, monkeypatch):
    """The owner's labels are what a judge is measured against, so `labels` prints, for reports where he confirmed or changed
    requirements, each requirement with its state, the model's verdict and his final one. Never the CV line or the reason, which retell the CV."""
    app = make_app(tmp_path, monkeypatch)
    b = Browser(app, addr="203.0.113.232")
    token_of(public_check(b))
    db = app.extensions["svc"].db
    result = db.one("SELECT id FROM results WHERE kind='fit'")
    items = db.all("SELECT * FROM items WHERE result_id=? ORDER BY idx", (result["id"],))
    assert items
    first = items[0]
    before = run(tmp_path / "t.db", "labels")
    assert first["text"] not in before                       # nothing confirmed or changed yet: nothing of it is shown
    db.run("UPDATE items SET override_verdict='not_on_record' WHERE id=?", (first["id"],))
    db.confirm_remaining(result["id"])
    after = run(tmp_path / "t.db", "labels")
    assert first["text"] in after and "changed" in after and "confirmed" in after and f"model {first['verdict']}" in after
    for it in items:
        for field in ("evidence", "reason"):
            if it[field] and len(it[field]) > 20:
                assert it[field] not in after
    assert PUBLIC_POSTING[:60] not in after
