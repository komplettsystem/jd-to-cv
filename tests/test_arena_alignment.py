"""The arena puts the same requirement of the two reports on one row, so 1 is read against 1 and 2 against 2, even though each model
worded and listed the requirements itself. A requirement only one report has gets a row with an empty side."""
import match


def item(text, kind="minimum", verdict="partial"):
    return {"text": text, "kind": kind, "verdict": verdict, "eff_verdict": verdict, "evidence": "", "reason": "", "section": ""}


A = [item("Own the strategy and roadmap for Agentic Search from ingestion through retrieval and ranking"),
     item("Talk directly with developers, AI companies, publishers and customers every day"),
     item("Passion for design details and polish", kind="desirable")]
B = [item("Talk directly with developers, AI companies, publishers, and customers every day."),
     item("Own the strategy, roadmap, and execution for Agentic Search, from ingestion and indexing through retrieval, ranking, and answer generation"),
     item("Represent Acme at conferences and community events")]


def test_rows_pair_the_same_requirement_even_when_the_order_and_wording_differ():
    rows = match.align_requirements(A, B)
    assert [(a["text"][:12] if a else None, b["text"][:12] if b else None) for a, b in rows] == [
        ("Own the stra", "Own the stra"), ("Talk directl", "Talk directl"), ("Passion for ", None), (None, "Represent Ac")]


def test_a_requirement_is_paired_once_and_nothing_is_lost():
    rows = match.align_requirements(A, A)
    assert [(a is b) for a, b in rows] == [True, True, True]
    only_a = match.align_requirements(A, [])
    assert len(only_a) == 3 and all(b is None for _, b in only_a)
    rows = match.align_requirements(A, B)
    assert sum(a is not None for a, _ in rows) == len(A) and sum(b is not None for _, b in rows) == len(B)


def test_the_arena_page_puts_the_two_reports_on_shared_rows(tmp_path, monkeypatch):
    import re
    from webfixtures import Browser, make_app, POSTING
    app = make_app(tmp_path, monkeypatch)
    admin = Browser(app, addr="127.0.0.1")
    r = admin.post("/admin/new", {"posting_text": POSTING, "title": "Search PM", "company": "Acme", "backends": ["cli:sonnet", "cli:haiku"]})
    rid = int(r.headers["Location"].split("/")[-1])
    page = admin.get(f"/admin/runs/{rid}/arena").data.decode()
    assert "Report A" in page and "Report B" in page and "cli:sonnet" not in page.split("<main>")[1]
    assert page.count('class="arena-row"') >= 2 and page.count('class="arena-head"') == 1
    assert "side by side" in page


def test_the_arena_never_pits_a_model_against_the_baseline(tmp_path, monkeypatch):
    from webfixtures import Browser, make_app, POSTING
    app = make_app(tmp_path, monkeypatch)
    admin = Browser(app, addr="127.0.0.1")
    r = admin.post("/admin/new", {"posting_text": POSTING, "title": "Search PM", "company": "Acme", "backends": ["baseline", "cli:haiku"]})
    rid = int(r.headers["Location"].split("/")[-1])
    assert "No unvoted pair" in admin.get(f"/admin/runs/{rid}/arena").data.decode()      # one model and the baseline: no pair
    admin.post(f"/admin/runs/{rid}/arena", {"backends": ["baseline", "cli:haiku"]})        # the baseline is not run for the arena
    assert len(app.extensions["svc"].db.results_for(rid, "fit")) == 2
    admin.post(f"/admin/runs/{rid}/arena", {"backends": ["baseline", "cli:haiku", "cli:sonnet"]})
    page = admin.get(f"/admin/runs/{rid}/arena").data.decode()
    assert "Report A" in page and "baseline" not in page.split("<main>")[1]
    assert 'value="baseline"' not in admin.get(f"/admin/runs/{rid}").data.decode().split('id="arena"')[1].split("</section>")[0]
