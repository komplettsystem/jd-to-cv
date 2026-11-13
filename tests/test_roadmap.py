"""The public roadmap: votes count once per visitor, wishes are validated and rate-limited, the admin sees and deletes them."""
from webfixtures import Browser, make_app, old_ts
from web.roadmap import ITEMS


def wish(browser, **extra):
    data = {"text": "Please add a German version of the reports.", "contact": "", "item": "languages", "ts": old_ts()}
    data.update(extra)
    return browser.post("/roadmap/wish", data)


def test_the_roadmap_is_public_lists_every_item_and_says_nothing_is_promised(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    page = Browser(app).get("/roadmap").data.decode()
    assert "Nothing here is promised or dated" in page
    assert all(i["title"] in page for i in ITEMS)
    assert "Planned" in page and "Exploring" in page and "Idea" in page
    assert 'href="/roadmap"' in Browser(app).get("/").data.decode()  # reachable from every page


def test_a_visitor_votes_once_per_item_and_the_count_shows(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    b = Browser(app)
    assert b.post("/roadmap/vote", {"item": "mcp"}).status_code == 302
    assert b.post("/roadmap/vote", {"item": "mcp"}).status_code == 302  # the second is a no-op, with a message
    page = b.get("/roadmap").data.decode()
    assert "You would use this" in page and "1 person" in page
    assert "1 person" not in Browser(app, addr="198.51.100.9").get("/roadmap").data.decode().split('id="item-api"')[1].split("</li>")[0]
    assert Browser(app, addr="198.51.100.9").post("/roadmap/vote", {"item": "mcp"}).status_code == 302
    assert "2 people" in Browser(app).get("/roadmap").data.decode()
    assert b.post("/roadmap/vote", {"item": "nope"}).status_code == 400


def test_a_wish_is_stored_and_shown_to_the_admin_who_can_delete_it(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    assert wish(Browser(app), contact="reader@example.com").status_code == 302
    admin = Browser(app, addr="127.0.0.1")
    page = admin.get("/admin/feedback").data.decode()
    assert "Please add a German version" in page and "reader@example.com" in page and "Reports in more languages" in page
    wid = app.extensions["svc"].db.feedback()[0]["id"]
    assert admin.post(f"/admin/feedback/{wid}/delete").status_code == 302
    assert "Please add a German version" not in admin.get("/admin/feedback").data.decode()


def test_a_visitor_cannot_read_the_wishes(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    wish(Browser(app))
    assert Browser(app).get("/admin/feedback").status_code == 403


def test_wish_validation_and_limits(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    b = Browser(app)
    assert wish(b, text="short").status_code == 400
    assert wish(b, text="x" * 1001).status_code == 400
    assert wish(b, contact="not an email").status_code == 400
    assert wish(b, item="nope").status_code == 400
    assert wish(b, ts="9999999999").status_code == 429            # sent too fast to be a person
    assert b.post("/roadmap/wish", {"text": "A honeypot filled in.", "website": "x", "ts": old_ts()}).status_code == 400
    assert not app.extensions["svc"].db.feedback()
    for _ in range(3):
        assert wish(b).status_code == 302
    r = wish(b)
    assert r.status_code == 429 and b"Try again tomorrow" in r.data
    assert len(app.extensions["svc"].db.feedback()) == 3


def test_the_contact_address_appears_only_when_set(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    assert b"mailto:" not in Browser(app).get("/roadmap").data
    app2 = make_app(tmp_path / "b", monkeypatch, contact_email="hello@example.org") if (tmp_path / "b").mkdir() is None else None
    assert "hello@example.org" in Browser(app2).get("/roadmap").data.decode()
    privacy = Browser(app2).get("/privacy").data.decode()
    assert "Garante per la protezione dei dati personali" in privacy and "Article 6(1)(b)" in privacy
    assert "The text is sent to ," not in privacy


def test_hardening_work_is_listed_apart_from_new_features(tmp_path, monkeypatch):
    page = Browser(make_app(tmp_path, monkeypatch)).get("/roadmap").data.decode()
    assert "Making it sturdier" in page and "Not new features" not in page
    features, hardening = page.split("Making it sturdier")
    assert "A job queue" in hardening and "Storage away from the web server" in hardening
    assert "A job queue" not in features and "A candidate pool" in features
