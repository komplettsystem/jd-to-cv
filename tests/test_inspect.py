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
