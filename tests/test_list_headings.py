"""A report groups its requirements under the posting's own headings for the two lists (for example "What You'll Do" and
"Examples of Desirable Skills"), and says how each list is counted. Without a heading it falls back to the plain names."""
from webfixtures import Browser, PUBLIC_POSTING, make_app, old_ts, token_of

HEADED = (PUBLIC_POSTING + "\nWHATHEADINGS\nWhat You’ll Do\nOwn the roadmap for agentic search.\n"
          "Examples of Desirable Skills, Knowledge, and Experience\nProven experience shipping search products.\n")


def check(app, addr, text):
    return Browser(app, addr=addr).post("/check", {"ts": old_ts(), "title": "", "company": "", "posting_text": text})


def test_headings_are_stored_from_the_posting_and_shown_over_the_two_groups(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    r = check(app, "203.0.113.261", HEADED)
    token = token_of(r)
    run = app.extensions["svc"].db.one("SELECT * FROM runs WHERE share_token=?", (token,))
    assert run["min_heading"] == "What You’ll Do" and run["nice_heading"].startswith("Examples of Desirable Skills")
    html = Browser(app).get(f"/r/{token}").data.decode()
    assert "<h3>What You’ll Do</h3>" in html and "Counted as the minimum requirements" in html
    assert "<h3>Examples of Desirable Skills, Knowledge, and Experience</h3>" in html and "Counted as nice to have" in html
    assert "<h3>Minimum requirements</h3>" not in html and "<h3>Nice to have</h3>" not in html
    # the summary tile over the counts uses the posting's heading too, not "Minimum requirements"
    assert '<span class="field-label">What You’ll Do</span>' in html and "items under “What You’ll Do” in the posting" in html
    assert '<span class="field-label">Minimum requirements</span>' not in html


def test_without_headings_the_plain_names_stay(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    token = token_of(check(app, "203.0.113.262", PUBLIC_POSTING))
    html = Browser(app).get(f"/r/{token}").data.decode()
    assert "<h3>Minimum requirements</h3>" in html and "<h3>Nice to have</h3>" in html
    assert '<span class="field-label">Minimum requirements</span>' in html and "minimum requirements in the posting" in html


def test_a_heading_the_posting_does_not_contain_is_dropped(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    run = app.extensions["svc"].db.one("SELECT * FROM runs WHERE share_token=?",
                                       (token_of(check(app, "203.0.113.263", "HALLUCINATE\n" + PUBLIC_POSTING)),))
    assert run["min_heading"] == "" and run["nice_heading"] == ""


def test_headings_are_filled_once(tmp_path, monkeypatch):
    db = make_app(tmp_path, monkeypatch).extensions["svc"].db
    run_id, _ = db.create_public_run("", "", "x" * 300, "v", "2999-01-01T00:00:00")
    db.fill_run_headings(run_id, {"minimum": "What You&#8217;ll Do", "desirable": ""})
    db.fill_run_headings(run_id, {"minimum": "Other", "desirable": "Bonus"})
    row = db.one("SELECT min_heading, nice_heading FROM runs WHERE id=?", (run_id,))
    assert (row["min_heading"], row["nice_heading"]) == ("What You’ll Do", "Bonus")


# --- the chat and the overall reading name the lists the way the posting does ---------------------------------------------------
NAMES = {"minimum": "What You’ll Do", "desirable": "Examples of Desirable Skills, Knowledge, and Experience"}


def _items():
    base = {"idx": 0, "reason": "r", "evidence": "", "section": "", "chunk_id": None}
    return [{**base, "text": "Owned a search product", "kind": "minimum", "verdict": "met"},
            {**base, "text": "A degree", "kind": "minimum", "verdict": "not_on_record"}]


def test_the_chat_is_told_the_postings_own_heading_and_the_count_uses_it():
    import match
    from llm import chat
    items = _items()
    text = chat.report_text(items, gap=match.minimum_gap(items), headings=NAMES)
    assert "1 of 2 items under “What You’ll Do” are met or partly met." in text
    assert "headed “What You’ll Do”" in text and "headed “Examples of Desirable Skills" in text and "never as" in text
    assert "minimum requirements are met" not in text


def test_without_headings_the_chat_text_is_unchanged():
    import match
    from llm import chat
    items = _items()
    text = chat.report_text(items, gap=match.minimum_gap(items))
    assert "1 of 2 minimum requirements are met or partly met." in text and "headed" not in text


def test_the_what_if_answer_uses_the_postings_heading_for_the_counts():
    from llm import chat
    deps = chat.ChatDeps(chunks={}, context_text="", items=_items(), headings=NAMES)
    text = chat.call_overall_check(deps, leave_out=[2])
    assert "What You’ll Do now:" in text and "Minimum requirements now:" not in text
    plain = chat.ChatDeps(chunks={}, context_text="", items=_items())
    assert "Minimum requirements now:" in chat.call_overall_check(plain, leave_out=[2])


def test_the_overall_reading_is_given_the_postings_headings_to_use():
    from llm import overall
    chunks = [{"id": "c1", "section": "Work", "text": "Owned a search product."}]
    with_names = overall.prompt("A posting.", _items(), chunks, NAMES)
    assert "headed “What You’ll Do”" in with_names
    assert "headed" not in overall.prompt("A posting.", _items(), chunks)
