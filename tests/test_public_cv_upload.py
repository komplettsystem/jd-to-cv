"""The public CV is uploaded in the admin and kept in the database; visitors' checks quote it."""
import io

from webfixtures import Browser, PUBLIC_POSTING, make_app, old_ts, public_check, token_of

CV = """# Pat Example

## Experience

- Led the data platform team at Marigold Systems from 2014 to 2020 and ran retrieval evaluation with the data team.
- Built a self-serve onboarding flow at Marigold Systems that cut setup from days to minutes.
- Wrote the public API documentation. Contact: pat@example.com, +49 170 5550123.
"""


def post_cv(admin, **kw):
    return admin.c.post("/admin/settings", data={"_csrf": admin.token("/admin/settings"), "section": "public_cv", **kw},
                        environ_base={"REMOTE_ADDR": "127.0.0.1"}, follow_redirects=True)


def test_an_uploaded_public_cv_switches_visitor_checks_on_and_is_what_they_quote(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    svc = app.extensions["svc"]
    svc.settings.public_cv_path = ""
    svc.settings.public_cv_explicit = False               # nothing configured by file
    assert "Upload the CV" in svc.public_admin_reason()
    assert svc.public_admin_fix() == "settings"           # the banner on the dashboard must point at the upload
    admin = Browser(app, addr="127.0.0.1")
    dash = admin.get("/admin/").data.decode()
    assert 'href="/admin/settings#pubcv-title"' in dash and "MATCH_PUBLIC_CV" not in dash
    r = post_cv(admin, cv_file=(io.BytesIO(CV.encode()), "cv.md"))
    assert b"Public CV saved" in r.data
    stored = svc.uploaded_public_cv()
    assert "Marigold Systems" in stored and "@" not in stored and "5550123" not in stored   # contact details removed
    assert svc.public_admin_reason() == ""
    token = token_of(public_check(Browser(app)))
    page = Browser(app).get(f"/r/{token}").data.decode()
    assert "Marigold Systems" in page


def test_pasted_text_works_clearing_reverts_and_bad_input_is_refused(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    svc = app.extensions["svc"]
    admin = Browser(app, addr="127.0.0.1")
    assert b"too short" in post_cv(admin, cv_text="tiny").data
    assert b"Public CV saved" in post_cv(admin, cv_text=CV).data
    assert b"Marigold Systems" in admin.get("/admin/settings").data
    assert b"removed" in post_cv(admin, action="clear").data and svc.uploaded_public_cv() == ""
    assert b"Upload the CV or" not in admin.get("/admin/settings").data


def test_a_visitor_cannot_change_the_public_cv(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    b = Browser(app)
    r = b.c.post("/admin/settings", data={"_csrf": b.token(), "section": "public_cv", "cv_text": CV})
    assert r.status_code == 403 and app.extensions["svc"].uploaded_public_cv() == ""
