"""A visitor can give the link to a posting instead of pasting it. The server reads the page once, so the reading has
limits (usual web ports, a short deadline, a size cap, few at a time), and a failure always tells the visitor to upload a PDF."""
import json
import sqlite3
import urllib.error

import pytest

import ingest
from webfixtures import Browser, PUBLIC_POSTING, make_app, old_ts, token_of

BODY = "You will own the search roadmap and work with engineers, designers and data scientists every day. " * 12
JOB = {"@context": "https://schema.org", "@type": "JobPosting", "title": "Senior Product Manager",
       "hiringOrganization": {"@type": "Organization", "name": "Acme Search"},
       "jobLocation": {"@type": "Place", "address": {"addressLocality": "Milan", "addressCountry": "IT"}},
       "description": f"<p>{BODY}</p><ul><li>Ten years in product</li><li>SQL</li></ul>"}


def page(ld=None, body=""):
    script = f'<script type="application/ld+json">{json.dumps(ld)}</script>' if ld is not None else ""
    return f"<html><head>{script}</head><body>{body}</body></html>"


# --- reading the page -------------------------------------------------------------------------------------------

def test_a_javascript_career_page_is_read_from_its_job_posting_data():
    text = ingest.posting_text_from_html(page(JOB, '<div id="root"></div>'))
    assert "Senior Product Manager" in text and "Acme Search" in text and "Milan" in text
    assert "- Ten years in product" in text and "search roadmap" in text


@pytest.mark.parametrize("wrap", [lambda j: {"@graph": [{"@type": "WebSite"}, j]}, lambda j: [j], lambda j: {**j, "@type": ["JobPosting"]}])
def test_the_posting_data_is_found_in_a_graph_a_list_or_a_type_list(wrap):
    assert "Senior Product Manager" in ingest.posting_text_from_html(page(wrap(JOB)))


def test_html_entities_in_the_job_data_title_and_company_are_decoded():
    job = {**JOB, "title": "Senior Director, Technical Product Management &#8212; AI Quality &amp; Evaluation",
           "hiringOrganization": {"@type": "Organization", "name": "Salesforce, Inc. &amp; Partners"}}
    text = ingest.posting_text_from_html(page(job))
    assert text.job_title == "Senior Director, Technical Product Management \u2014 AI Quality & Evaluation"
    assert text.job_company == "Salesforce, Inc. & Partners" and "&amp;" not in text.splitlines()[0] + text.splitlines()[1]


def test_page_text_is_used_when_there_is_no_posting_data_or_it_is_broken():
    body = f"<h1>Senior PM</h1><p>{BODY}</p>"
    assert "Senior PM" in ingest.posting_text_from_html(page(None, body))
    broken = "<html><head><script type=\"application/ld+json\">{not json</script></head><body>" + body + "</body></html>"
    assert "Senior PM" in ingest.posting_text_from_html(broken)


def test_a_page_with_no_posting_text_says_to_upload_a_pdf():
    with pytest.raises(ValueError, match="JavaScript.*PDF"):
        ingest.posting_text_from_html(page(None, '<div id="root"></div>'))


# --- the limits of a fetch for visitors -----------------------------------------------------------------------------

def fake_download(monkeypatch, html=None, ctype="text/html; charset=utf-8", error=None):
    def fake(url, public=False):
        assert public is True                          # the visitor path always uses the stricter settings
        if error:
            raise error
        return html, ctype
    monkeypatch.setattr(ingest, "_download", fake)


def test_a_link_is_read_into_text(monkeypatch):
    fake_download(monkeypatch, page(JOB))
    assert "Senior Product Manager" in ingest.fetch_posting("https://jobs.example.test/pm", 15000)


def test_only_the_usual_web_ports_are_read(monkeypatch):
    fake_download(monkeypatch, page(JOB))
    with pytest.raises(ValueError, match="port.*PDF"):
        ingest.fetch_posting("http://jobs.example.test:8080/pm", 15000)
    ingest.fetch_posting("https://jobs.example.test:443/pm", 15000)


def test_a_link_to_something_that_is_not_a_web_page_is_refused(monkeypatch):
    fake_download(monkeypatch, "%PDF-1.4", ctype="application/pdf")
    with pytest.raises(ValueError, match="web page.*PDF"):
        ingest.fetch_posting("https://jobs.example.test/pm.pdf", 15000)


def test_failures_become_plain_sentences_without_network_details(monkeypatch):
    fake_download(monkeypatch, error=urllib.error.HTTPError("u", 403, "Forbidden", {}, None))
    with pytest.raises(ValueError, match="403.*PDF"):
        ingest.fetch_posting("https://jobs.example.test/pm", 15000)
    fake_download(monkeypatch, error=urllib.error.URLError("[Errno 111] Connection refused"))
    with pytest.raises(ValueError) as e:
        ingest.fetch_posting("https://jobs.example.test/pm", 15000)
    assert "could not reach" in str(e.value) and "Errno" not in str(e.value) and "PDF" in str(e.value)
    fake_download(monkeypatch, error=TimeoutError("timed out"))
    with pytest.raises(ValueError, match="could not reach.*PDF"):
        ingest.fetch_posting("https://jobs.example.test/pm", 15000)


def test_a_page_longer_than_the_posting_limit_is_refused_and_not_cut_quietly(monkeypatch):
    fake_download(monkeypatch, page(JOB))
    with pytest.raises(ValueError, match="longer than 200 characters.*PDF"):
        ingest.fetch_posting("https://jobs.example.test/pm", 200)


def test_only_a_few_fetches_run_at_once(monkeypatch):
    fake_download(monkeypatch, page(JOB))
    taken = []
    try:
        while ingest._FETCH_SLOTS.acquire(blocking=False):
            taken.append(1)
        with pytest.raises(ValueError, match="right now.*PDF"):
            ingest.fetch_posting("https://jobs.example.test/pm", 15000)
    finally:
        for _ in taken:
            ingest._FETCH_SLOTS.release()
    ingest.fetch_posting("https://jobs.example.test/pm", 15000)   # a slot is free again


def test_a_slow_trickle_is_cut_off_and_the_size_is_capped():
    class Trickle:
        def read(self, n): return b"x" * 10

    ticks = iter([0, 1, 2, 100])
    with pytest.raises(ValueError, match="too slowly"):
        ingest._read_limited(Trickle(), 1_000_000, 25, clock=lambda: next(ticks))

    class Endless:
        def read(self, n): return b"y" * n

    assert len(ingest._read_limited(Endless(), 150_000, 25, clock=lambda: 0)) == 150_000


# --- the forms ------------------------------------------------------------------------------------------------------

def posted(app, path, addr, **data):
    b = Browser(app, addr=addr)
    return b.post(path, {"ts": old_ts(), "title": "", "company": "", **data})


def upload_data():
    return {"cv_text": "# Jo Example\n\n- Led product teams at Zebrafish Analytics from 2013 to 2019 and ran retrieval evaluation.\n"
                       "- Built a self-serve onboarding flow that cut setup from days to minutes at Zebrafish Analytics.\n"
                       "- Wrote the public API documentation and ran weekly calls with twelve customers.\n",
            "candidate_name": "Jo Example", "retention": "session", "consent": "1"}


def test_both_forms_offer_a_link_field_and_the_text_box_is_no_longer_required(tmp_path, monkeypatch):
    b = Browser(make_app(tmp_path, monkeypatch))
    for path in ("/", "/check-cv"):
        html = b.get(path).data.decode()
        assert 'name="posting_url"' in html and 'type="url"' in html
        assert 'id="posting_text" name="posting_text" required' not in html


def test_a_link_alone_starts_a_check_on_the_landing_page_and_the_upload_page(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    seen = []
    monkeypatch.setattr(ingest, "fetch_posting", lambda url, limit: seen.append((url, limit)) or PUBLIC_POSTING)
    r = posted(app, "/check", "203.0.113.21", posting_url="https://jobs.example.test/pm")
    assert r.status_code == 302 and "/r/" in r.headers["Location"]
    r = posted(app, "/check-cv", "203.0.113.22", posting_url="https://jobs.example.test/pm2", **upload_data())
    assert r.status_code == 302 and "/r/" in r.headers["Location"]
    assert [u for u, _ in seen] == ["https://jobs.example.test/pm", "https://jobs.example.test/pm2"]
    assert seen[0][1] == app.extensions["svc"].limits()["posting_max_chars"]


def test_pasted_text_wins_when_both_are_given_and_no_page_is_fetched(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    monkeypatch.setattr(ingest, "fetch_posting", lambda *a: pytest.fail("must not fetch"))
    r = posted(app, "/check", "203.0.113.23", posting_text=PUBLIC_POSTING, posting_url="https://jobs.example.test/pm")
    assert r.status_code == 302


def test_a_failed_fetch_shows_its_message_and_keeps_the_link(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)

    def fail(url, limit):
        raise ValueError("That page showed no posting text. Paste the posting text instead.")
    monkeypatch.setattr(ingest, "fetch_posting", fail)
    r = posted(app, "/check", "203.0.113.24", posting_url="https://jobs.example.test/pm")
    assert r.status_code == 400 and b"Paste the posting text instead" in r.data and b"https://jobs.example.test/pm" in r.data


def test_links_are_rate_limited_per_visitor(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)

    def fail(url, limit):
        raise ValueError("no")
    monkeypatch.setattr(ingest, "fetch_posting", fail)
    codes = [posted(app, "/check", "203.0.113.25", posting_url="https://jobs.example.test/pm").status_code for _ in range(7)]
    assert codes[:6] == [400] * 6 and codes[6] == 429


def test_the_link_itself_is_not_kept(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    monkeypatch.setattr(ingest, "fetch_posting", lambda url, limit: PUBLIC_POSTING)
    posted(app, "/check", "203.0.113.26", posting_url="https://secret-board.example.test/jobs/4711?token=abc123")
    dump = "\n".join(sqlite3.connect(app.extensions["svc"].settings.db_path).iterdump())
    assert "secret-board" not in dump and "abc123" not in dump
