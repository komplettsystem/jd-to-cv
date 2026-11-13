"""When the line a model cites for a verdict is not one it was given, the verdict falls back to "not shown". The reader is told
that in one plain sentence, without the model's contradicting text, internal wording or line numbers."""
import re
import sqlite3

import match
from db import DB
from test_caps import setup

SENTENCE = "The CV line first cited could not be verified, so this counts as not shown."


def test_a_citation_that_was_not_offered_gives_the_plain_sentence_and_keeps_the_flag():
    cands = [[{"id": "c1", "section": "Work", "text": "Led a team."}]]
    rows = {"judgements": [{"idx": 0, "verdict": "met", "evidence_id": "c31", "reason": "Led product managers for the API gateway."}]}
    (j,) = match.parse_judgements(rows, cands)
    assert j["verdict"] == "not_on_record" and j["flag"] == "invalid_citation" and j["chunk_id"] is None
    assert j["reason"] == SENTENCE and not re.search(r"\bc\d+\b|downgraded|model cited", j["reason"])


def test_a_valid_citation_keeps_the_models_own_reason():
    cands = [[{"id": "c1", "section": "Work", "text": "Led a team."}]]
    rows = {"judgements": [{"idx": 0, "verdict": "met", "evidence_id": "c1", "reason": "The line states it."}]}
    assert match.parse_judgements(rows, cands)[0]["reason"] == "The line states it."


def test_reasons_stored_with_the_old_annotation_are_cleaned_once_when_the_database_opens(tmp_path, monkeypatch):
    _, svc = setup(tmp_path, monkeypatch, {})
    run_id, _ = svc.db.create_public_run("", "", "x" * 300, "v", "2999-01-01T00:00:00")
    with sqlite3.connect(svc.db.path) as c:
        rid = c.execute("INSERT INTO results (run_id, backend, status, created) VALUES (?, 'm', 'done', '2026-11-12T00:00:00')", (run_id,)).lastrowid
        c.execute("INSERT INTO items (result_id, idx, text, kind, verdict, evidence, section, reason, flag) VALUES "
                  "(?, 0, 't', 'minimum', 'not_on_record', '', '', ?, 'invalid_citation')",
                  (rid, "[model cited no evidence it was given: 'c31'; downgraded] Led product managers."))
    DB(svc.db.path)                                                              # opening the database runs the clean-up
    assert svc.db.one("SELECT reason FROM items")["reason"] == SENTENCE


def test_stored_explanations_with_a_line_number_are_scrubbed_once_when_the_database_opens(tmp_path, monkeypatch):
    _, svc = setup(tmp_path, monkeypatch, {})
    run_id, _ = svc.db.create_public_run("", "", "x" * 300, "v", "2999-01-01T00:00:00")
    with sqlite3.connect(svc.db.path) as c:
        rid = c.execute("INSERT INTO results (run_id, backend, status, created) VALUES (?, 'm', 'done', '2026-11-12T00:00:00')", (run_id,)).lastrowid
        for reason in ("Led a five-PM team, with stakeholder influence shown in c10.", "Nothing to change here.", "Version c100 is fine as a name."):
            c.execute("INSERT INTO items (result_id, idx, text, kind, verdict, reason) VALUES (?, 0, 't', 'minimum', 'met', ?)", (rid, reason))
    DB(svc.db.path)
    reasons = [r["reason"] for r in svc.db.all("SELECT reason FROM items ORDER BY id")]
    assert reasons[0] == "Led a five-PM team, with stakeholder influence shown in the CV line."
    assert reasons[1] == "Nothing to change here." and "c100" not in reasons[2]


def test_the_judge_is_told_not_to_retell_the_evidence_line_the_reader_already_sees():
    from llm.agents import JUDGE_RULES
    assert "never retell or paraphrase it" in JUDGE_RULES and "what the line does not show" in JUDGE_RULES


def test_the_extractor_is_told_to_follow_the_postings_lists_and_keep_its_wording():
    from llm.agents import EXTRACT_RULES
    assert "whatever their headings say" in EXTRACT_RULES and "What you'll do" in EXTRACT_RULES
    assert "in the posting's own words" in EXTRACT_RULES and "Never move an item between the lists" in EXTRACT_RULES
