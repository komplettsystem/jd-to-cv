"""Which opening should a letter have? The owner can have three drafts written for a report (lead with proof, with a question,
with the role's main problem), then compare them blind, two at a time. The tally shows which opening was picked most."""
import re

import letters
import webfixtures
from test_web import POSTING
from webfixtures import Browser, make_app

VARIANTS = ("proof", "question", "problem")


def admin_run(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    admin = Browser(app, addr="127.0.0.1")
    r = admin.post("/admin/new", {"posting_text": POSTING, "backends": ["cli:haiku"]})
    run_id = int(re.search(r"/admin/runs/(\d+)", r.headers["Location"]).group(1))
    return app, admin, run_id


def make_variants(admin, run_id):
    return admin.post(f"/admin/runs/{run_id}/letter-variants", {"backend": "cli:haiku"})


def variant_letters(app, run_id):
    return {l["variant"]: l for l in app.extensions["svc"].db.letters_for(run_id) if l["variant"]}


def vote(admin, run_id, app, winner_variant, loser_variant):
    ls = variant_letters(app, run_id)
    a, b = ls[winner_variant], ls[loser_variant]
    return admin.post(f"/admin/runs/{run_id}/letter-vote", {"left": a["id"], "right": b["id"], "winner": "left"})


def test_the_three_openings_are_different_rules_and_the_default_is_the_proof_opening():
    assert set(letters.VARIANTS) == set(VARIANTS)
    assert letters.VARIANTS["proof"] == letters.DEFAULT_RULES_RECRUITER
    assert "Open with the method" in letters.VARIANTS["question"] and "most relevant proof" not in letters.VARIANTS["question"]
    assert "main problem" in letters.VARIANTS["problem"] and "most relevant proof" not in letters.VARIANTS["problem"]
    assert len(set(letters.VARIANTS.values())) == 3


def test_three_owner_drafts_are_written_each_with_its_own_rules_and_never_shown_to_visitors(tmp_path, monkeypatch):
    app, admin, run_id = admin_run(tmp_path, monkeypatch)
    before = len(webfixtures.SEEN_LETTERS)
    assert make_variants(admin, run_id).status_code == 302
    made = variant_letters(app, run_id)
    assert set(made) == set(VARIANTS) and all(l["status"] == "done" and l["audience"] == "owner" for l in made.values())
    seen = webfixtures.SEEN_LETTERS[before:]
    assert len(seen) == 3 and any("Open with the method" in t for t in seen) and any("main problem" in t for t in seen)
    assert app.extensions["svc"].db.letters_for(run_id, "shared") == []


def test_asking_twice_does_not_write_the_drafts_again(tmp_path, monkeypatch):
    app, admin, run_id = admin_run(tmp_path, monkeypatch)
    make_variants(admin, run_id)
    make_variants(admin, run_id)
    assert len([l for l in app.extensions["svc"].db.letters_for(run_id) if l["variant"]]) == 3


def test_the_arena_shows_two_letters_without_saying_which_opening_or_model(tmp_path, monkeypatch):
    app, admin, run_id = admin_run(tmp_path, monkeypatch)
    make_variants(admin, run_id)
    html = admin.get(f"/admin/runs/{run_id}/letter-arena").data.decode()
    assert "Letter A" in html and "Letter B" in html and "Dear hiring team," in html
    assert not any(v in html.lower() for v in ("proof-first", "variant", "haiku")) and "3 pairs" in html


def test_votes_are_stored_with_the_openings_and_all_pairs_get_used_up(tmp_path, monkeypatch):
    app, admin, run_id = admin_run(tmp_path, monkeypatch)
    make_variants(admin, run_id)
    vote(admin, run_id, app, "proof", "question")
    vote(admin, run_id, app, "proof", "problem")
    vote(admin, run_id, app, "question", "problem")
    rows = app.extensions["svc"].db.letter_votes()
    assert len(rows) == 3 and {(r["variant_a"], r["variant_b"], r["winner"]) for r in rows} >= {("proof", "question", "a")}
    assert "No unvoted pair" in admin.get(f"/admin/runs/{run_id}/letter-arena").data.decode()


def test_a_vote_for_letters_that_do_not_belong_together_is_refused(tmp_path, monkeypatch):
    app, admin, run_id = admin_run(tmp_path, monkeypatch)
    make_variants(admin, run_id)
    ls = variant_letters(app, run_id)
    assert admin.post(f"/admin/runs/{run_id}/letter-vote", {"left": ls["proof"]["id"], "right": 9999, "winner": "left"}).status_code == 400
    assert admin.post(f"/admin/runs/{run_id}/letter-vote", {"left": ls["proof"]["id"], "right": ls["proof"]["id"], "winner": "left"}).status_code == 400
    assert admin.post(f"/admin/runs/{run_id}/letter-vote", {"left": ls["proof"]["id"], "right": ls["question"]["id"], "winner": "x"}).status_code == 400


def test_the_results_count_picks_per_opening_and_say_how_little_that_proves(tmp_path, monkeypatch):
    app, admin, run_id = admin_run(tmp_path, monkeypatch)
    make_variants(admin, run_id)
    vote(admin, run_id, app, "proof", "question")
    vote(admin, run_id, app, "proof", "problem")
    ls = variant_letters(app, run_id)
    admin.post(f"/admin/runs/{run_id}/letter-vote", {"left": ls["question"]["id"], "right": ls["problem"]["id"], "winner": "tie"})
    stats = app.extensions["svc"].letter_vote_stats()
    assert stats["proof"] == {"picked": 2, "lost": 0, "tied": 0, "compared": 2, "passed_checks": 1, "drafts": 1, "mean_words": stats["proof"]["mean_words"]}
    assert stats["question"]["tied"] == 1 and stats["question"]["lost"] == 1 and stats["problem"]["tied"] == 1
    html = admin.get("/admin/letter-arena/results").data.decode()
    assert "Proof first" in html and "Question first" in html and "hint" in html.lower() and "readers" in html.lower()


def test_the_run_page_offers_the_comparison(tmp_path, monkeypatch):
    app, admin, run_id = admin_run(tmp_path, monkeypatch)
    html = admin.get(f"/admin/runs/{run_id}").data.decode()
    assert f"/admin/runs/{run_id}/letter-variants" in html and "Compare letter openings" in html
    make_variants(admin, run_id)
    html = admin.get(f"/admin/runs/{run_id}").data.decode()
    assert f"/admin/runs/{run_id}/letter-arena" in html and "/admin/letter-arena/results" in html


def test_comparison_drafts_are_marked_as_tests_in_the_letter_list_without_naming_the_opening(tmp_path, monkeypatch):
    app, admin, run_id = admin_run(tmp_path, monkeypatch)
    make_variants(admin, run_id)
    html = admin.get(f"/admin/runs/{run_id}").data.decode()
    assert html.count("(opening test)") == 3
    assert not any(label in html for label in letters.VARIANT_LABELS.values())
