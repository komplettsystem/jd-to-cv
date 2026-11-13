"""The CV a visitor can open from a report is a PDF made from the same public CV the report was checked against, without a phone
number or an email address, and with the LinkedIn and GitHub links kept (clickable)."""
import io

from pypdf import PdfReader

import cvpdf
from datetime import date
from webfixtures import Browser, make_app, public_check, token_of

CV = """<!-- a comment that must not appear -->
# Ada Example

Head of Product

Milan, Italy | [phone removed] | [email removed] | linkedin.com/in/ada, github.com/ada

## Profile

Product manager who led a team of five. Moved from Director → SVP in 2019.

## Experience

### Director — Acme | Milan

*May 2021 — September 2026*

- **Led the team that cut onboarding from days to minutes**, replacing a manual process.
- Wrote the public API documentation.
"""


def pdf_text(data):
    reader = PdfReader(io.BytesIO(data))
    return "\n".join(page.extract_text() for page in reader.pages), reader


def test_the_pdf_has_the_cv_without_contact_markers_and_keeps_the_profile_links_clickable():
    data = cvpdf.render(CV, "Ada Example")
    text, reader = pdf_text(data)
    assert data.startswith(b"%PDF") and "Ada Example" in text and "Head of Product" in text
    assert "removed" not in text and "a comment" not in text
    assert "Milan, Italy | linkedin.com/in/ada, github.com/ada" in " ".join(text.split())
    assert "days to minutes" in text and "Director -> SVP" in text and "—" in text
    uris = [a.get_object()["/A"]["/URI"] for page in reader.pages for a in page.get("/Annots", [])]
    assert "https://linkedin.com/in/ada" in uris and "https://github.com/ada" in uris


def test_a_long_cv_runs_over_several_pages():
    body = "\n".join(f"- Shipped item number {i} for customers of every size across the world." for i in range(160))
    assert len(PdfReader(io.BytesIO(cvpdf.render("# A B\n\n## Experience\n\n" + body, "A B"))).pages) >= 2


def test_the_route_serves_the_public_cv_as_a_pdf_that_search_engines_skip(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    app.extensions["svc"].db.set_setting("public_cv", CV.replace("[phone removed]", "[phone removed]"))
    r = Browser(app, addr="203.0.113.211").get("/cv.pdf")
    assert r.status_code == 200 and r.mimetype == "application/pdf" and "noindex" in r.headers["X-Robots-Tag"]
    assert f'filename="ada_example-cv-{date.today():%Y%m%d}.pdf"' in r.headers["Content-Disposition"]
    text, _ = pdf_text(r.data)
    assert "days to minutes" in text and "removed" not in text


def test_checked_against_in_a_report_links_to_the_pdf(tmp_path, monkeypatch):
    b = Browser(make_app(tmp_path, monkeypatch), addr="203.0.113.212")
    html = b.get(f"/r/{token_of(public_check(b))}").data.decode()
    assert f'<a href="/cv/ada_example-cv-{date.today():%Y%m%d}.pdf"' in html and "Ada Example</a>" in html


def test_the_file_name_has_the_owners_name_and_the_date():
    assert cvpdf.filename("Ada Example", date(2026, 11, 13)) == "ada_example-cv-20261113.pdf"
    assert cvpdf.filename("Jürgen Müller-Lüdenscheid", date(2026, 1, 2)) == "jurgen_muller_ludenscheid-cv-20260102.pdf"
    assert cvpdf.filename("", date(2026, 1, 2)) == "cv-cv-20260102.pdf"


def test_the_pdf_is_served_under_an_address_that_carries_the_name_and_still_under_the_old_one(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    app.extensions["svc"].db.set_setting("public_cv", CV)
    b = Browser(app, addr="203.0.113.213")
    for url in (f"/cv/ada_example-cv-{date.today():%Y%m%d}.pdf", "/cv/whatever.pdf", "/cv.pdf"):
        r = b.get(url)
        assert r.status_code == 200 and r.mimetype == "application/pdf", url
        assert f"ada_example-cv-{date.today():%Y%m%d}.pdf" in r.headers["Content-Disposition"], url
