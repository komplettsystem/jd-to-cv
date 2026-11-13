"""A role title or company with HTML codes in it ("AI Quality &amp; Evaluation") is shown as plain text: decoded when it is
stored, and once for reports stored before that, when the database opens. The chat note names the owner by first name."""
from db import DB

from webfixtures import Browser, make_app, public_check, token_of


def test_titles_are_decoded_when_stored_and_in_old_rows_when_the_database_opens(tmp_path):
    path = str(tmp_path / "t.db")
    db = DB(path)
    rid = db.create_public_run("Director &#8212; AI Quality &amp; Evaluation", "Salesforce &amp; Co", "x" * 300, "v", "2999-01-01")[0]
    row = db.one("SELECT title, company FROM runs WHERE id=?", (rid,))
    assert row == {"title": "Director — AI Quality & Evaluation", "company": "Salesforce & Co"}
    db.run("UPDATE runs SET title=?, company=? WHERE id=?", ("Old &amp; stored", "Old &amp; Co", rid))
    row = DB(path).one("SELECT title, company FROM runs WHERE id=?", (rid,))
    assert row == {"title": "Old & stored", "company": "Old & Co"}
    db.run("UPDATE runs SET title='', company='' WHERE id=?", (rid,))
    db.fill_run_title(rid, "Lead &amp; Learn", "R&amp;D Inc")
    assert db.one("SELECT title, company FROM runs WHERE id=?", (rid,)) == {"title": "Lead & Learn", "company": "R&D Inc"}


def test_the_chat_note_uses_the_first_name_only(tmp_path, monkeypatch):
    b = Browser(make_app(tmp_path, monkeypatch), addr="203.0.113.201")
    html = b.get(f"/r/{token_of(public_check(b))}").data.decode()
    assert "You are chatting with an AI model, not with Ada." in html and "not with Ada Example" not in html
