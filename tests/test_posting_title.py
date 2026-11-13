"""The role title and company are found early: from the page's own job data when a link is given, and by the first model step
(which reads the posting anyway) otherwise. The page shows them while the match is still running."""
import re

import ingest
from webfixtures import Browser, PUBLIC_POSTING, make_app, old_ts, token_of
from test_posting_url import JOB, page

TITLED = "Senior Product Manager, Search\nAcme Search is hiring a product manager for its search team.\n" + PUBLIC_POSTING


def run_of(svc, response):
    return svc.db.one("SELECT * FROM runs WHERE share_token=?", (token_of(response),))


def check(app, addr, **data):
    b = Browser(app, addr=addr)
    return b.post("/check", {"ts": old_ts(), "title": "", "company": "", **data})


def test_a_blank_title_is_filled_once_and_never_overwrites_what_exists(tmp_path, monkeypatch):
    db = make_app(tmp_path, monkeypatch).extensions["svc"].db
    run_id, _ = db.create_public_run("", "", "x" * 300, "v", "2999-01-01T00:00:00")
    db.fill_run_title(run_id, "Role A", "Co A")
    db.fill_run_title(run_id, "Role B", "Co B")                  # a second result of the same run
    row = db.one("SELECT title, company FROM runs WHERE id=?", (run_id,))
    assert (row["title"], row["company"]) == ("Role A", "Co A")
    other, _ = db.create_public_run("Typed title", "", "x" * 300, "v", "2999-01-01T00:00:00")
    db.fill_run_title(other, "Model title", "Model Co")
    row = db.one("SELECT title, company FROM runs WHERE id=?", (other,))
    assert (row["title"], row["company"]) == ("Typed title", "Model Co")   # only the blank one is filled


def test_the_first_model_step_fills_the_title_and_company_the_posting_states(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    r = check(app, "203.0.113.101", posting_text=TITLED)
    run = run_of(app.extensions["svc"], r)
    assert (run["title"], run["company"]) == ("Senior Product Manager, Search", "Acme Search")


def test_a_title_the_visitor_typed_is_kept(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    run = run_of(app.extensions["svc"], check(app, "203.0.113.102", posting_text=TITLED, title="My own title"))
    assert run["title"] == "My own title" and run["company"] == "Acme Search"


def test_a_title_that_is_not_in_the_posting_is_dropped(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    run = run_of(app.extensions["svc"], check(app, "203.0.113.103", posting_text="HALLUCINATE\n" + PUBLIC_POSTING))
    assert run["title"] == "" and run["company"] == ""


def test_the_live_state_carries_the_title_and_company(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    token = token_of(check(app, "203.0.113.104", posting_text=TITLED))
    state = Browser(app).get(f"/r/{token}/state").get_json()
    assert state["title"] == "Senior Product Manager, Search" and state["company"] == "Acme Search"


def test_a_link_gives_the_title_and_company_at_once_from_the_job_data(monkeypatch):
    monkeypatch.setattr(ingest, "_download", lambda url, public=False: (page(JOB), "text/html"))
    got = ingest.fetch_posting("https://jobs.example.test/pm", 15000)
    assert got.job_title == "Senior Product Manager" and got.job_company == "Acme Search" and "search roadmap" in got
    monkeypatch.setattr(ingest, "_download", lambda url, public=False: (page(None, f"<p>{JOB['description']}</p>"), "text/html"))
    plain = ingest.fetch_posting("https://jobs.example.test/pm", 15000)
    assert plain.job_title == "" and plain.job_company == ""


def test_a_link_sets_the_title_before_any_model_has_run(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    monkeypatch.setattr(ingest, "fetch_posting", lambda url, limit: ingest.FetchedText(PUBLIC_POSTING, "Role X", "Company Y"))
    run = run_of(app.extensions["svc"], check(app, "203.0.113.105", posting_url="https://jobs.example.test/pm"))
    assert (run["title"], run["company"]) == ("Role X", "Company Y")


def test_the_report_page_can_show_a_title_that_arrives_late(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    token = token_of(check(app, "203.0.113.106", posting_text=PUBLIC_POSTING))
    html = Browser(app).get(f"/r/{token}").data.decode()
    assert 'id="report-title"' in html and "data-live-posting" in html and "data-live-company" in html
    js = (__import__("pathlib").Path(__file__).resolve().parent.parent / "static" / "app.js").read_text()
    assert "showTitle" in js and re.search(r"poll\(s => \{[^}]*showTitle", js)
