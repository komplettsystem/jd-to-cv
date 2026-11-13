"""The demo database (tools/seed_demo.py) holds one scripted chat answer that went through the matcher, with fictional data, and the run page
renders it with the report's labels and the quoted CV line."""
import os
import subprocess
import sys
from pathlib import Path

from app import create_app
from config import Settings
from webfixtures import Browser

ROOT = Path(__file__).resolve().parent.parent


def test_the_seeded_demo_shows_a_matcher_exchange_with_its_label_and_quoted_line(tmp_path):
    env = {**os.environ, "MATCH_DB": str(tmp_path / "demo.db"), "MATCH_DATA": str(tmp_path), "MATCH_CV": str(ROOT / "tools" / "sample_cv.md")}
    done = subprocess.run([sys.executable, str(ROOT / "tools" / "seed_demo.py")], env=env, capture_output=True, text=True, cwd=ROOT)
    assert done.returncode == 0, done.stderr[-500:]
    app = create_app(Settings(data_dir=tmp_path, db_path=str(tmp_path / "demo.db"), cv_path=str(ROOT / "tools" / "sample_cv.md"),
                              sync_jobs=True, secret_key=b"k", owner_name="Ada Example"))
    db = app.extensions["svc"].db
    msgs = [m for m in db.all("SELECT id FROM chat_messages WHERE exchanges LIKE '%ask_matcher%'")]
    assert len(msgs) == 1                                                          # exactly one scripted matcher exchange
    ex = db.get_message(msgs[0]["id"])["exchanges"][0]
    assert ex["to"] == "matcher" and ex["request"]["params"]["name"] == "ask_matcher"
    (res,) = ex["response"]["result"]["structuredContent"]["results"]
    assert res["verdict"] in ("met", "partial", "not_on_record") and res["statement"]
    run_id = db.one("SELECT c.run_id FROM chats c JOIN chat_messages m ON m.chat_id=c.id WHERE m.id=?", (msgs[0]["id"],))["run_id"]
    page = Browser(app, addr="127.0.0.1").get(f"/admin/runs/{run_id}").data.decode()
    assert "The chat bot asked the matcher" in page and res["statement"] in page
    assert {"met": ">Met<", "partial": ">Partly<", "not_on_record": ">Not shown<"}[res["verdict"]] in page.split("matcher-item")[1]
    if res["line"]:
        assert res["line"] in page and "Not on record" not in page.split("matcher-item")[1]
