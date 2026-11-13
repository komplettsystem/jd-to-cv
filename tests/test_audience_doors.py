"""Two audiences, two doors: recruiters (and their bots) get the start page; a job seeker is pointed to the page for their own
CV, and a recruiter who lands there is pointed back. A plain text page tells automated visitors what the service is."""
import re

from webfixtures import Browser, make_app


def test_the_start_page_points_job_seekers_to_their_own_page(tmp_path, monkeypatch):
    html = Browser(make_app(tmp_path, monkeypatch)).get("/").data.decode()
    box = re.search(r'<aside class="audience-box"[^>]*>(.*?)</aside>', html, re.S).group(1)
    assert "Not a recruiter?" in box and 'href="/check-cv"' in box and "your own CV" in box
    assert html.index('class="audience-box"') > html.index("Does this job fit")     # after the headline, before the form
    assert html.index('class="audience-box"') < html.index('name="posting_url"')


def test_the_job_seeker_page_points_recruiters_back(tmp_path, monkeypatch):
    html = Browser(make_app(tmp_path, monkeypatch)).get("/check-cv").data.decode()
    box = re.search(r'<aside class="audience-box"[^>]*>(.*?)</aside>', html, re.S).group(1)
    assert "Are you a recruiter?" in box and 'href="/"' in box and "Ada Example" in box


def test_automated_visitors_get_a_plain_text_description(tmp_path, monkeypatch):
    r = Browser(make_app(tmp_path, monkeypatch)).get("/llms.txt")
    assert r.status_code == 200 and r.mimetype == "text/plain"
    text = r.data.decode()
    assert text.startswith("# ") and "Ada Example" in text
    assert "/check" in text and "/check-cv" in text and "/roadmap" in text
    assert "No API or MCP server yet" in text                                    # honest about what does not exist
    assert "checks per" in text                                                   # the limits an agent should respect
