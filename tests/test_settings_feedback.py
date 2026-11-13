"""The Settings page must say which boxes are saved, and a save must say which box it saved."""
from service import DEFAULT_LIMITS
from webfixtures import Browser, make_app


def post(admin, **data):
    return admin.c.post("/admin/settings", data={"_csrf": admin.token("/admin/settings"), **data},
                        environ_base={"REMOTE_ADDR": "127.0.0.1"}, follow_redirects=True)


def test_every_editable_box_has_a_save_status_and_the_script_that_updates_it(tmp_path, monkeypatch):
    admin = Browser(make_app(tmp_path, monkeypatch), addr="127.0.0.1")
    html = admin.get("/admin/settings").data.decode()
    assert html.count("data-save-state") == 5          # public CV, limits, facts, two sets of letter rules
    assert html.count("data-track") == 5
    assert "app.js" in html


def test_a_save_names_the_box_it_saved(tmp_path, monkeypatch):
    admin = Browser(make_app(tmp_path, monkeypatch), addr="127.0.0.1")
    assert b"Extra facts saved." in post(admin, section="facts", facts="One fact").data
    assert b"Cover letter rules for recruiters saved." in post(admin, section="letter_rules", letter_rules="Short").data
    limits = {k: str(v) for k, v in DEFAULT_LIMITS.items()}
    assert b"Limits and spend caps saved." in post(admin, section="limits", **limits).data
