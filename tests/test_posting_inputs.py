"""A visitor gives the posting as a link. Only when the page cannot be read does the form offer an uploaded PDF or text file, and pasting
the text comes last."""
import io
import re
from pathlib import Path

import cvfile
import ingest
from test_posting_url import posted, upload_data
from test_upload import make_pdf
from webfixtures import Browser, PUBLIC_POSTING, make_app

JD_LINES = ["Senior Product Manager, Search at Acme",
            "Acme builds a search product used by thousands of companies.",
            "You will own the search roadmap and work with engineers, designers and data scientists every day.",
            "Requirements: ten years in product, strong SQL, experience with ranking and relevance evaluation.",
            "We value clear writing and honest communication in everything we do."]


def jd_pdf():
    return make_pdf(JD_LINES)


def run_text(app, response):
    token = response.headers["Location"].split("/r/")[1].split("?")[0]
    return app.extensions["svc"].db.one("SELECT posting_text FROM runs WHERE share_token=?", (token,))["posting_text"]


def test_a_pdf_of_the_posting_starts_a_check_on_both_forms(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    r = posted(app, "/check", "203.0.113.51", posting_file=(io.BytesIO(jd_pdf()), "jd.pdf"))
    assert r.status_code == 302 and "search roadmap" in run_text(app, r)
    r = posted(app, "/check-cv", "203.0.113.52", posting_file=(io.BytesIO(jd_pdf()), "jd.pdf"), **upload_data())
    assert r.status_code == 302 and "search roadmap" in run_text(app, r)


def test_pasted_text_beats_a_file_and_a_file_beats_a_link(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    monkeypatch.setattr(ingest, "fetch_posting", lambda *a: (_ for _ in ()).throw(AssertionError("must not fetch")))
    both = posted(app, "/check", "203.0.113.53", posting_text=PUBLIC_POSTING, posting_file=(io.BytesIO(jd_pdf()), "jd.pdf"))
    assert "search roadmap" not in run_text(app, both)
    file_and_link = posted(app, "/check", "203.0.113.54", posting_file=(io.BytesIO(jd_pdf()), "jd.pdf"),
                           posting_url="https://jobs.example.test/pm")
    assert "search roadmap" in run_text(app, file_and_link)


def test_file_problems_talk_about_the_posting_not_the_cv(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    r = posted(app, "/check", "203.0.113.55", posting_file=(io.BytesIO(b"MZ"), "jd.exe"))
    assert r.status_code == 400 and b"posting" in r.data and b"CV" not in r.data.split(b"notice-error")[1].split(b"</p>")[0]
    r = posted(app, "/check", "203.0.113.55", posting_file=(io.BytesIO(make_pdf(["x"])), "scan.pdf"))
    assert r.status_code == 400 and b"posting" in r.data
    assert "posting" in str(__import__("pytest").raises(ValueError, cvfile.extract, "a.txt", b"\xff\xfe\x00", what="posting").value)


def test_nothing_given_asks_for_the_link_and_then_offers_the_pdf(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    r = posted(app, "/check", "203.0.113.56")
    assert r.status_code == 400 and b"Give the link to the posting" in r.data and b'name="posting_file"' in r.data


def form_html(html):
    return html[html.index("<form method=\"post\""):html.index("</form>")]


def test_the_forms_start_with_the_link_only(tmp_path, monkeypatch):
    b = Browser(make_app(tmp_path, monkeypatch))
    for path in ("/", "/check-cv"):
        html = form_html(b.get(path).data.decode())
        assert 'name="posting_url"' in html and 'type="url"' in html
        assert 'name="posting_file"' not in html and 'name="posting_text"' not in html and "paste-box" not in html


def test_a_link_that_cannot_be_read_offers_the_pdf_first_and_pasting_last(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    monkeypatch.setattr(ingest, "fetch_posting", lambda *a: (_ for _ in ()).throw(ValueError("we could not reach that page; upload the page as a PDF instead")))
    for path, extra in (("/check", {}), ("/check-cv", upload_data())):
        r = posted(app, path, "203.0.113.58", posting_url="https://jobs.example.test/pm", **extra)
        html = form_html(r.data.decode())
        assert r.status_code == 400 and "Upload the page as a PDF instead." in r.data.decode()
        assert html.index('name="posting_url"') < html.index('name="posting_file"') < html.index('name="posting_text"')
        assert re.search(r"<details[^>]*paste-box[^>]*>\s*<summary", html) and "paste-box\" open" not in html
        assert "https://jobs.example.test/pm" in html          # the link stays in the field


def test_a_problem_with_the_cv_does_not_bring_back_the_pdf_and_paste_options(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    monkeypatch.setattr(ingest, "fetch_posting", lambda *a: PUBLIC_POSTING)
    r = posted(app, "/check-cv", "203.0.113.59", posting_url="https://jobs.example.test/pm", **{**upload_data(), "cv_text": "tiny"})
    assert r.status_code == 400 and 'name="posting_file"' not in form_html(r.data.decode())


def test_a_failed_form_reopens_the_pasted_text(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    r = posted(app, "/check", "203.0.113.57", posting_text="too short")
    assert b"paste-box\" open" in r.data and b"too short" in r.data


def test_the_start_page_boxes_are_stacked_in_one_column():
    css = (Path(__file__).resolve().parent.parent / "static" / "style.css").read_text()
    hero = [line for line in css.splitlines() if ".hero-grid" in line and "grid-template-columns" in line]
    assert hero == []
