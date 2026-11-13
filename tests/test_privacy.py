"""The owner's private chat and letters, and the CV a visitor may see."""
from app import create_app, maintain
from config import Settings
from webfixtures import make_app


def test_public_checks_are_off_until_a_public_cv_is_chosen_on_purpose(tmp_path, monkeypatch):
    from llm import catalog
    monkeypatch.setattr(catalog, "availability", lambda e, s: (True, ""))
    cv = tmp_path / "cv.md"
    cv.write_text("# A\n\n- A line long enough to be a chunk of the CV.\n")
    app = create_app(Settings(data_dir=tmp_path, db_path=str(tmp_path / "t.db"), cv_path=str(cv), sync_jobs=True,
                              secret_key=b"k", allow_local_public=True))
    svc = app.extensions["svc"]
    svc.db.set_setting("public_model", "cli:haiku")
    assert not svc.settings.public_cv_explicit
    assert not svc.public_available()[0]
    assert "Upload the CV" in svc.public_admin_reason() and "MATCH_PUBLIC_CV" not in svc.public_admin_reason()
    assert svc.public_admin_fix() == "settings"
    svc.settings.public_cv_explicit = True
    assert svc.public_admin_reason() == "" and svc.public_available()[0]


def test_a_local_model_cannot_answer_visitors_in_production(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    svc = app.extensions["svc"]
    assert svc.public_admin_reason() == ""
    svc.settings.allow_local_public = False
    assert "runs locally" in svc.public_admin_reason()


def test_chats_and_letters_are_kept_per_audience(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    db = app.extensions["svc"].db
    rid = db.create_run("T", "C", "", "posting text", "")
    owner = db.get_or_create_chat(rid, "cli:haiku", "owner")
    shared = db.get_or_create_chat(rid, "cli:haiku", "shared")
    assert owner["id"] != shared["id"]
    assert db.chat_for_run(rid, "owner")["id"] == owner["id"] and db.chat_for_run(rid, "shared")["id"] == shared["id"]
    a = db.create_letter(rid, "cli:haiku", "", "owner")
    b = db.create_letter(rid, "cli:haiku", "", "shared")
    assert [l["id"] for l in db.letters_for(rid, "shared")] == [b]
    assert [l["id"] for l in db.letters_for(rid, "owner")] == [a]
    assert {l["id"] for l in db.letters_for(rid)} == {a, b}


def test_creating_the_app_does_not_fail_jobs_of_another_process(tmp_path, monkeypatch):
    """Recovery is an explicit start-up step (maintain), so an eval run against the same database is safe."""
    app = make_app(tmp_path, monkeypatch)
    db = app.extensions["svc"].db
    rid = db.create_run("T", "C", "", "posting text", "")
    res = db.add_result(rid, "baseline")
    db.set_status(res, "running")
    create_app(app.config["SETTINGS"])  # a second process starting
    assert db.get_result(res)["status"] == "running"
    assert maintain(app)[0] == 1 and db.get_result(res)["status"] == "failed"
