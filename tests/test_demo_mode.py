"""The read-only public demo: the admin is open to anyone, nothing can be changed, and it says so."""
from webfixtures import Browser, make_app


def demo(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, demo=True, main_url="https://real.example.com")
    return Browser(app, addr="203.0.113.7")        # a stranger, not this machine


def test_anyone_may_read_the_admin_pages_and_every_page_says_it_is_a_demo(tmp_path, monkeypatch):
    stranger = demo(tmp_path, monkeypatch)
    for path in ("/admin/", "/admin/evals", "/admin/runs", "/admin/models", "/admin/traces", "/"):
        r = stranger.get(path)
        assert r.status_code == 200, path
        page = r.data.decode()
        assert "made-up data" in page and "read only" in page.lower() and "https://real.example.com" in page, path
        assert "noindex" in r.headers.get("X-Robots-Tag", ""), path


def test_nothing_can_be_changed_whatever_the_form(tmp_path, monkeypatch):
    stranger = demo(tmp_path, monkeypatch)
    for path, data in (("/admin/evals/run", {"dataset": "reverse-apply-bench", "backend": "cli:haiku"}),
                       ("/admin/new", {"posting_text": "x", "backends": ["baseline"]}),
                       ("/admin/settings", {"public_model": "cli:haiku"}),
                       ("/check", {"posting_text": "x"}),
                       ("/contact", {"message": "hello"})):
        r = stranger.post(path, data)
        assert r.status_code == 403, (path, r.status_code)
        assert "read-only demo" in r.data.decode().lower(), path


def test_without_demo_mode_a_stranger_still_cannot_open_the_admin(tmp_path, monkeypatch):
    stranger = Browser(make_app(tmp_path, monkeypatch), addr="203.0.113.7")
    assert stranger.get("/admin/").status_code == 403
    assert "made-up data" not in stranger.get("/").data.decode()


def test_the_demo_holds_no_secrets_and_no_model_key(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, demo=True)
    s = app.config["SETTINGS"]
    assert not s.openrouter_api_key and not s.anthropic_api_key and not s.legal_address and not s.contact_email
