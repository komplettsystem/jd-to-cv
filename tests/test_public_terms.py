"""A visitor's check is also scored against the owner's saved terms. Only the owner sees that score; the public report, its state feed,
the share link, the chat and the letters never carry it. One point per posting on the both-ways chart: the latest run."""
import views
from webfixtures import PUBLIC_POSTING, Browser, make_app, public_check, token_of

TERMS = [{"name": "Zebra-stripe policy", "description": "The employer allows zebra-stripe socks", "importance": "dealbreaker"}]


def setup(tmp_path, monkeypatch, addr, save_terms=True):
    app = make_app(tmp_path, monkeypatch)
    svc = app.extensions["svc"]
    if save_terms:
        svc.db.set_json("terms", TERMS)
    return app, svc, Browser(app, addr=addr)


def test_a_public_check_also_checks_the_saved_terms(tmp_path, monkeypatch):
    app, svc, b = setup(tmp_path, monkeypatch, "203.0.113.221")
    token = token_of(public_check(b))
    run = svc.db.one("SELECT * FROM runs WHERE share_token = ?", (token,))
    terms = svc.db.results_for(run["id"], "terms")
    assert len(terms) == 1 and terms[0]["status"] == "done"
    assert [i["name"] for i in svc.db.term_items_for(terms[0]["id"])] == ["Zebra-stripe policy"]


def test_without_saved_terms_no_terms_check_runs(tmp_path, monkeypatch):
    app, svc, b = setup(tmp_path, monkeypatch, "203.0.113.222", save_terms=False)
    token = token_of(public_check(b))
    run = svc.db.one("SELECT * FROM runs WHERE share_token = ?", (token,))
    assert svc.db.results_for(run["id"], "terms") == []


def test_the_public_page_and_its_state_feed_never_show_the_terms_score(tmp_path, monkeypatch):
    app, svc, b = setup(tmp_path, monkeypatch, "203.0.113.223")
    token = token_of(public_check(b))
    page = b.get(f"/r/{token}").data.decode()
    feed = b.get(f"/r/{token}/state?after=0").data.decode()
    assert "Zebra-stripe" not in page and "Zebra-stripe" not in feed and "dealbreaker" not in feed.lower()
    assert "your terms" not in page.lower()           # not even the count line the owner sees ("1 check against your terms")
    run = svc.db.one("SELECT * FROM runs WHERE share_token = ?", (token,))
    admin = Browser(app, addr="127.0.0.1").get(f"/admin/runs/{run['id']}").data.decode()
    assert "Zebra-stripe" in admin                    # the owner does see it


def test_a_visitor_who_uploads_their_own_cv_gets_no_terms_check(tmp_path, monkeypatch):
    app, svc, b = setup(tmp_path, monkeypatch, "203.0.113.224")
    cv = "Product manager. " + "Led a team that shipped a search product used by many customers. " * 6
    run_id, _ = svc.create_upload_check(PUBLIC_POSTING, cv, "PM", "Acme", "Pat", "session",
                                        "203.0.113.224", True)
    assert svc.db.results_for(run_id, "terms") == []


def run(i, company="", title="", text=""):
    return {"id": i, "company": company, "title": title, "posting_text": text}


def test_the_same_posting_run_again_counts_once_and_the_latest_run_wins():
    runs = [run(9, "Globex", "Senior PM, Agentic Search", "text two"), run(7, "globex", "senior pm,  agentic search", "text one"),
            run(5, "Initech", "Senior TPM", "other posting")]          # newest first
    assert [r["id"] for r in views.latest_per_posting(runs)] == [9, 5]


def test_postings_without_names_match_on_their_text():
    runs = [run(4, text="Same   text\nhere"), run(3, text="same text here"), run(2, text="different")]
    assert [r["id"] for r in views.latest_per_posting(runs)] == [4, 2]


def test_two_roles_at_one_company_stay_apart():
    runs = [run(3, "Acme", "Designer", "a"), run(2, "Acme", "Engineer", "b")]
    assert len(views.latest_per_posting(runs)) == 2


def test_the_chart_keeps_an_older_complete_run_when_the_newest_has_no_terms_score_yet(monkeypatch):
    class Db:
        def list_runs(self):
            return [{"id": 8, "company": "Acme", "title": "PM", "posting_text": "t", "human_verdict": "", "source": "public"},
                    {"id": 6, "company": "Acme", "title": "PM", "posting_text": "t", "human_verdict": "", "source": "public"}]
    monkeypatch.setattr(views, "run_scores", lambda db: {8: {"fit": 40}, 6: {"fit": 50, "terms": 60}})
    pts = views.quadrant(Db())
    assert [(p["run_id"], p["x"], p["y"]) for p in pts] == [(6, 50, 60)]


def test_the_backfill_checks_a_run_once_and_skips_one_that_has_a_terms_result(tmp_path, monkeypatch):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    import backfill_terms
    app, svc, b = setup(tmp_path, monkeypatch, "203.0.113.225", save_terms=False)
    run_id = svc.db.one("SELECT * FROM runs WHERE share_token = ?", (token_of(public_check(b)),))["id"]
    assert svc.db.results_for(run_id, "terms") == []
    svc.db.set_json("terms", TERMS)
    assert backfill_terms.backfill(svc, [run_id, 9999]) == [run_id]
    assert svc.db.results_for(run_id, "terms")[0]["status"] == "done"
    assert backfill_terms.backfill(svc, [run_id]) == []
