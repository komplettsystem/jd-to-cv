"""Cover letter rules are split by door: recruiters get the owner's voice, visitors checking their own CV get neutral defaults.
Neither tells the writer to open with a question. Each is editable on its own, and the settings page says where the defaults
came from and what the code checks whatever the rules say."""
import letters
import webfixtures
from service import DEFAULT_LIMITS
from test_caps import setup
from test_upload import upload
from webfixtures import Browser, make_app, public_check, token_of


def test_neither_default_leads_with_a_question_and_they_are_not_the_same_text():
    for rules in (letters.DEFAULT_RULES_RECRUITER, letters.DEFAULT_RULES_SEEKER):
        assert "most relevant proof" in rules and "Open with the method" not in rules and "questions you would ask" not in rules
        assert "do not open with a question" in rules.lower()
    assert letters.DEFAULT_RULES_RECRUITER != letters.DEFAULT_RULES_SEEKER
    assert "colleague" in letters.DEFAULT_RULES_RECRUITER and "colleague" not in letters.DEFAULT_RULES_SEEKER
    assert "apologise" in letters.DEFAULT_RULES_SEEKER


def test_each_door_uses_its_own_rules_with_the_default_as_the_fallback(tmp_path, monkeypatch):
    _, svc = setup(tmp_path, monkeypatch, {})
    assert svc.letter_rules("recruiter") == letters.DEFAULT_RULES_RECRUITER and svc.letter_rules("seeker") == letters.DEFAULT_RULES_SEEKER
    assert svc.letter_rules() == letters.DEFAULT_RULES_RECRUITER                    # the owner's own letters
    svc.db.set_setting("letter_rules_seeker", "Say it plainly.")
    assert svc.letter_rules("seeker") == "Say it plainly." and svc.letter_rules("recruiter") == letters.DEFAULT_RULES_RECRUITER
    svc.db.set_setting("letter_rules_seeker", "")
    assert svc.letter_rules("seeker") == letters.DEFAULT_RULES_SEEKER


def test_a_letter_is_written_with_the_rules_of_the_door_its_report_came_through(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    svc = app.extensions["svc"]
    svc.db.set_setting("letter_rules", "RECRUITER-RULES-MARKER")
    svc.db.set_setting("letter_rules_seeker", "SEEKER-RULES-MARKER")
    public_check(Browser(app, addr="203.0.113.181"))                                # the automatic letter
    assert "RECRUITER-RULES-MARKER" in webfixtures.SEEN["letter"] and "SEEKER-RULES-MARKER" not in webfixtures.SEEN["letter"]
    b = Browser(app, addr="203.0.113.182")
    token = token_of(upload(b))
    b.post(f"/r/{token}/letter", {"language": "English", "length": "short"})
    assert "SEEKER-RULES-MARKER" in webfixtures.SEEN["letter"] and "RECRUITER-RULES-MARKER" not in webfixtures.SEEN["letter"]


def test_the_writer_is_told_a_question_paragraph_is_optional():
    from llm import agents
    assert "optional" in agents.LETTER_RULES_HEAD.lower() and "greeting, body and closing" in agents.LETTER_RULES_HEAD


def test_the_settings_page_has_two_rule_boxes_with_notes_and_saves_them_separately(tmp_path, monkeypatch):
    app, svc = setup(tmp_path, monkeypatch, {})
    admin = Browser(app, addr="127.0.0.1")
    html = admin.get("/admin/settings").data.decode()
    assert 'id="letter_rules"' in html and 'id="letter_rules_seeker"' in html
    assert "replaces the default" in html and "checked in code" in html and "do not open with a question" in html.lower()
    token = admin.token("/admin/settings")
    r = admin.c.post("/admin/settings", data={"_csrf": token, "section": "letter_rules_seeker", "letter_rules_seeker": "Neutral."},
                     environ_base={"REMOTE_ADDR": "127.0.0.1"}, follow_redirects=True)
    assert b"visitors&#39; own CVs saved" in r.data or b"visitors' own CVs saved" in r.data
    assert svc.db.get_setting("letter_rules_seeker") == "Neutral." and svc.db.get_setting("letter_rules", "") == ""
