"""The roadmap lists only what is not built; what is built sits in a short "Already live" list at the bottom. The CV upload page tells people
how to use their LinkedIn profile."""
import re

from web import roadmap
from webfixtures import Browser, make_app


def page(tmp_path, monkeypatch, path="/roadmap"):
    return Browser(make_app(tmp_path, monkeypatch), addr="203.0.113.261").get(path).data.decode()


def test_the_groups_hold_no_available_item_and_the_note_no_longer_says_so(tmp_path, monkeypatch):
    html = page(tmp_path, monkeypatch)
    groups = html[:html.index('id="live-title"')]
    assert ">Available<" not in groups and "Only items marked Available are built" not in html
    for it in roadmap.ITEMS:
        if it["status"] == "live":
            assert f'id="item-{it["key"]}"' not in groups


def test_already_live_comes_last_and_lists_the_built_things_without_vote_buttons(tmp_path, monkeypatch):
    html = page(tmp_path, monkeypatch)
    assert html.index('id="wish-title"') < html.index('id="live-title"')
    live = html[html.index('id="live-title"'):]
    for title in ("Check your own CV against a posting", "The chat asks the match engine", "Interview prep from the gaps",
                  "Ask the CV about things the posting never asked"):
        assert title in live, title
    assert "I would use this" not in live
    assert sorted(it["key"] for it in roadmap.ITEMS if it["status"] == "live") == ["ask-the-cv", "chat-engine", "interview-prep", "own-cv"]


def test_the_wish_form_offers_only_items_that_are_not_built(tmp_path, monkeypatch):
    html = page(tmp_path, monkeypatch)
    options = re.findall(r'<option value="([^"]*)"', html[html.index('id="wish-item"'):html.index('id="wish-contact"')])
    assert "own-cv" not in options and "api" in options


def test_the_cv_upload_page_explains_how_to_use_a_linkedin_profile(tmp_path, monkeypatch):
    html = page(tmp_path, monkeypatch, "/check-cv")
    assert "Save to PDF" in html and "LinkedIn" in html
