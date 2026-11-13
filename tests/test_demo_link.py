"""The live site's menu has "Demo" (the read-only copy with made-up data); the demo's menu has "Live" to go back. The separate example
report page is gone: the start page has the same example as a fold-out."""
from webfixtures import Browser, make_app


def menu(html):
    nav = html[html.index('aria-label="Site"'):]
    return nav[:nav.index("</nav>")]


def test_the_live_menu_links_to_the_demo_when_its_address_is_set(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, demo_url="https://demo.example.test")
    nav = menu(Browser(app).get("/").data.decode())
    assert '<a href="https://demo.example.test/admin/">Demo (Admin)</a>' in nav and "Example report" not in nav and ">Live<" not in nav


def test_without_a_demo_address_the_menu_has_no_demo_item(tmp_path, monkeypatch):
    nav = menu(Browser(make_app(tmp_path, monkeypatch)).get("/").data.decode())
    assert "Demo" not in nav and "Example report" not in nav


def test_the_demos_menu_says_live_and_goes_back_to_the_real_site(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, demo=True, main_url="https://real.example.test", demo_url="https://demo.example.test")
    nav = menu(Browser(app).get("/").data.decode())
    assert '<a href="https://real.example.test">Live</a>' in nav and "Demo" not in nav


def test_the_old_example_page_address_leads_to_the_start_page_fold(tmp_path, monkeypatch):
    r = Browser(make_app(tmp_path, monkeypatch)).get("/demo")
    assert r.status_code in (301, 308) and r.headers["Location"].endswith("/#sample-title")


def test_the_paused_notice_links_to_the_fold_on_the_start_page(tmp_path, monkeypatch):
    from webfixtures import PUBLIC_POSTING  # noqa: F401  (the notice is shown when checks are paused; see test_web)
    html = Browser(make_app(tmp_path, monkeypatch)).get("/").data.decode()
    assert 'id="sample-title"' in html            # the fold the redirect and the notice point at
