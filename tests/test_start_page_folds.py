"""The boxes under the start page's form (the example report and the three notes) are closed until their title is clicked;
the example report page itself stays open."""
import re

from webfixtures import Browser, make_app


def test_the_start_page_boxes_are_closed_folds_and_the_example_page_is_not(tmp_path, monkeypatch):
    b = Browser(make_app(tmp_path, monkeypatch))
    html = b.get("/").data.decode()
    folds = re.findall(r'<details class="[^"]*\bfold-box\b[^"]*"[^>]*>\s*<summary class="ribbon[^"]*"><h2[^>]*>([^<]*)</h2>', html)
    assert folds == ["What a report looks like", "How it works", "What it will not do", "How it is built"]
    assert not re.search(r'<details class="[^"]*\bfold-box\b[^"]*"[^>]*\bopen\b', html)
    old = b.get("/demo")      # the separate example page is gone: the old address leads to the fold-out on the start page
    assert old.status_code in (301, 308) and old.headers["Location"].endswith("/#sample-title")


def test_this_link_on_a_report_is_a_link_to_the_report(tmp_path, monkeypatch):
    from webfixtures import public_check, token_of
    b = Browser(make_app(tmp_path, monkeypatch), addr="203.0.113.271")
    token = token_of(public_check(b))
    assert f'Anyone with <a href="/r/{token}">this link</a> can read it.' in b.get(f"/r/{token}").data.decode()
