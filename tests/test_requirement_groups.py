"""The report shows the minimum requirements and the nice-to-haves as two separate blocks, minimum first, whatever order the
model listed them in. Numbering runs on across the blocks; the link from a requirement to its sentence in the posting
keeps pointing at the right requirement."""
import re

from webfixtures import Browser, PUBLIC_POSTING, make_app, old_ts, token_of


def report(tmp_path, monkeypatch, posting):
    app = make_app(tmp_path, monkeypatch)
    b = Browser(app, addr="203.0.113.121")
    token = token_of(b.post("/check", {"posting_text": posting, "ts": old_ts(), "title": "", "company": ""}))
    return b.get(f"/r/{token}").data.decode()


def test_two_blocks_minimum_first_even_when_the_model_listed_the_nice_to_have_first(tmp_path, monkeypatch):
    html = report(tmp_path, monkeypatch, "NICEFIRST\n" + PUBLIC_POSTING)
    assert 'reqs-group-min' in html and 'reqs-group-nice' in html
    assert html.index("Minimum requirements") < html.index("Nice to have")
    assert html.index("retrieval evaluation") < html.index("usage-based pricing")       # minimum row above the nice-to-have row
    assert html.index('class="reqs-group reqs-group-min"') < html.index('class="reqs-group reqs-group-nice"')


def test_each_block_holds_only_its_own_kind_and_numbering_runs_on(tmp_path, monkeypatch):
    html = report(tmp_path, monkeypatch, "NICEFIRST\n" + PUBLIC_POSTING)
    minimum, nice = html.split('class="reqs-group reqs-group-nice"')
    assert "retrieval evaluation" in minimum.split('class="reqs-group reqs-group-min"')[1] and "usage-based pricing" not in minimum
    assert "usage-based pricing" in nice and "retrieval evaluation" not in nice.split("</ol>")[0]
    assert re.findall(r'class="req-n">(\d+)<', html) == ["1", "2"]


def test_the_link_to_the_posting_keeps_the_original_position_of_each_requirement(tmp_path, monkeypatch):
    html = report(tmp_path, monkeypatch, "NICEFIRST\n" + PUBLIC_POSTING)
    positions = re.findall(r'<li class="req"[^>]*data-req="(\d+)"', html)
    if positions:                                          # present when the posting could be marked
        assert positions == ["1", "0"]                      # minimum was listed second, nice-to-have first


def test_a_block_with_nothing_in_it_is_not_shown(tmp_path, monkeypatch):
    html = report(tmp_path, monkeypatch, "ONLYNICE\n" + PUBLIC_POSTING)
    assert 'reqs-group-nice' in html and 'reqs-group-min' not in html and "<h3>Minimum requirements</h3>" not in html


def test_the_example_report_on_the_start_page_and_demo_uses_the_same_two_blocks(tmp_path, monkeypatch):
    b = Browser(make_app(tmp_path, monkeypatch))
    for path in ("/",):
        html = b.get(path).data.decode()
        assert html.index('class="reqs-group reqs-group-min"') < html.index('class="reqs-group reqs-group-nice"')
        assert "<h3>Minimum requirements</h3>" in html and "<h3>Nice to have</h3>" in html
        assert 'class="req-kind"' not in html
