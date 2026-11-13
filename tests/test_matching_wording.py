"""The site is about checking a match, not about applying: the headline asks whether the job fits, and no label still says the
posting applies or that someone is applying."""
from webfixtures import Browser, make_app, public_check, token_of

OLD = ("Let the job posting apply", "Applying to", "Application of a job posting")


def test_the_start_page_asks_whether_the_job_fits_and_leads_with_the_check(tmp_path, monkeypatch):
    html = Browser(make_app(tmp_path, monkeypatch)).get("/").data.decode()
    assert "<h1>Does this job fit Ada?</h1>" in html
    assert "Each requirement is checked against Ada Example" in html and "draft cover letter comes with it" in html
    assert "Check a job posting" in html and "Checked against" not in html[html.index("<form method"):html.index("</form>")]
    assert not any(old in html for old in OLD)


def test_the_example_report_and_a_real_report_use_the_same_wording(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    b = Browser(app, addr="203.0.113.191")
    token = token_of(public_check(b))
    for html in (b.get("/").data.decode(), b.get(f"/r/{token}").data.decode()):
        assert "Checked against" in html and not any(old in html for old in OLD)
    assert "Posting against the CV" in b.get(f"/r/{token}").data.decode()
