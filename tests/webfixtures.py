"""Shared by the web tests: an app on a temp database whose models are scripted, never real."""
import json
import match
import re
import time
from datetime import datetime, timedelta, timezone

import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

import web.auth as auth
from app import create_app
from config import Settings
from llm import catalog
from test_match import CV, POSTING

PUBLIC_POSTING = POSTING + "\n" + ("We value clear writing and honest communication. " * 8)


def _instructions(messages):
    for m in messages:
        if getattr(m, "instructions", None):
            return m.instructions
    return ""


def _user_text(messages):
    out = []
    for m in messages:
        for p in getattr(m, "parts", []):
            c = getattr(p, "content", None)
            if isinstance(c, str):
                out.append(c)
    return "\n".join(out)


SEEN = {}               # the last instructions the scripted model was given for a letter
SEEN_LETTERS = []       # every set of letter instructions it was given, in order
LEAK = {"on": False}   # tests switch this on to make the scripted model write CV line ids into its prose


def fake_llm(messages, info):
    """Answers every agent in the contract the app expects, using only what the prompt contains."""
    ins, text = _instructions(messages), _user_text(messages)
    if ins.startswith("You read a job posting"):
        body = {"requirements": [{"text": "Hands-on experience with retrieval evaluation", "kind": "minimum"},
                                 {"text": "Experience with usage-based pricing", "kind": "desirable"}]}
        if "ONLYNICE" in text:                                  # a posting with nothing it calls required
            body["requirements"] = [r for r in body["requirements"] if r["kind"] == "desirable"]
        if "NICEFIRST" in text:                                 # the nice-to-have is listed before the minimum requirement
            body["requirements"].reverse()
        if "Senior Product Manager, Search" in text:           # a posting that states its title and company
            body.update(title="Senior Product Manager, Search", company="Acme Search")
        if "HALLUCINATE" in text:                               # a model that invents a title the posting does not contain
            body.update(title="Invented Title", company="Invented Co", minimum_heading="Invented heading")
        if "WHATHEADINGS" in text:                              # a posting that heads its two lists with its own words
            body.update(minimum_heading="What You\u2019ll Do", desirable_heading="Examples of Desirable Skills, Knowledge, and Experience")
    elif ins.startswith("You judge"):
        judgements = []
        blocks = re.split(r"\n\n(?=Requirement )", text)
        whole = blocks[0].startswith("CV lines, any of which")      # the whole CV is shown once: pick by word overlap, as the ranking would
        if whole:
            shown = [{"id": m[0], "section": m[1], "text": m[2]} for m in re.findall(r"^  (c\d+) \| ([^|]*) \| (.*)$", blocks[0], re.M)]
            blocks = blocks[1:]
        for i, b in enumerate(blocks):
            ids = [c["id"] for c in match.rank_evidence(b.split(": ", 1)[-1], shown, 5)] if whole else re.findall(r"^  (c\d+) \|", b, re.M)
            reason = "from the line" if ids else "nothing shown"
            if LEAK["on"] and ids:
                reason = f"stakeholder influence shown in {ids[0]}."
            judgements.append({"idx": i, "verdict": "met" if ids else "not_on_record",
                               "evidence_id": ids[0] if ids else None, "reason": reason})
        body = {"judgements": judgements}
    elif ins.startswith("You check a job posting"):
        n = len(re.findall(r"^Criterion \d+", text, re.M))
        body = {"terms": [{"idx": i, "verdict": "unclear", "quote": None, "reason": "silent"} for i in range(n)]}
        if "Experience" in text or "experience" in text:
            body["terms"][0] = {"idx": 0, "verdict": "fits", "quote": "Hands-on experience", "reason": "stated"}
    elif ins.startswith("You give an overall reading"):
        ids = re.findall(r"^(c\d+) \|", text.split("CV lines you may cite")[-1], re.M)
        body = {"level": "good", "summary": "The CV shows the kind of work the posting describes, with some gaps."
                + (f" The strongest sign is in {ids[0]}." if LEAK["on"] and ids else ""),
                "points": [{"kind": "strength", "text": "The roles match the posting's domain.", "basis": "on_record", "evidence_ids": ids[:1]},
                           {"kind": "expected", "text": "Likely familiar with evaluation practice.", "basis": "inferred", "evidence_ids": ids[:1]},
                           {"kind": "gap", "text": "Pricing work is not shown.", "basis": "on_record", "evidence_ids": []}],
                "interview_checks": ["How did you decide a change was good enough to ship?"]}
    elif ins.startswith("You plan"):
        body = {"titles": ["Product Manager"], "steps": [{"label": "Search", "requirement_idx": 0, "any_of": ["search", "retrieval"]}]}
    elif ins.startswith("You draft"):
        SEEN["letter"] = ins
        SEEN_LETTERS.append(ins)
        ids = re.findall(r"^(c\d+) \|", text.split("Evidence lines you may cite:")[-1], re.M)
        body = {"paragraphs": [
            {"kind": "greeting", "text": "Dear hiring team,", "evidence_ids": []},
            {"kind": "body", "text": "How would you decide a change is good enough to ship?", "evidence_ids": ids[:1]},
            {"kind": "closing", "text": "I would be glad to talk." + (f" ({ids[0]})" if LEAK["on"] and ids else ""), "evidence_ids": []}]}
        if "Write in German" in text and "FAIL" in text:
            body["paragraphs"][1]["text"] = "I leverage a robust paradigm."
        if "FORCEFAIL" in text:
            body["paragraphs"][1]["evidence_ids"] = []
    elif ins.startswith("You are the assistant"):
        question = text.split("<question>")[-1]
        ids = re.findall(r"^(c\d+) \|", text.split("CV lines that may be relevant")[-1], re.M)
        if "poem" in question:
            body = {"scope": "out_of_scope", "answer": "", "basis": []}
        else:
            body = {"scope": "in_scope", "answer": "The CV line says so." + (f" It rests on {ids[0]}." if LEAK["on"] and ids else ""),
                    "basis": ["cv", "report"], "evidence_ids": ids[:1]}
    else:
        raise AssertionError("unscripted agent: " + ins[:60])
    return ModelResponse(parts=[TextPart(json.dumps(body))])


def scripted_factory(entry):
    return FunctionModel(fake_llm, model_name="scripted")


def make_app(tmp_path, monkeypatch, *, password=None, **overrides):
    cv = tmp_path / "cv.md"
    cv.write_text(CV)
    monkeypatch.setattr(catalog, "availability", lambda entry, settings: (True, ""))
    s = Settings(data_dir=tmp_path, db_path=str(tmp_path / "t.db"), cv_path=str(cv), public_cv_path=str(cv),
                 sync_jobs=True, secret_key=b"test-secret", owner_name="Ada Example", allow_local_public=True,
                 admin_password_hash=auth.hash_password(password) if password else "", **overrides)
    app = create_app(s)
    app.extensions["svc"].model_factory = scripted_factory
    app.extensions["svc"].db.set_setting("public_model", "cli:haiku")
    return app


class Browser:
    """A test client that keeps its CSRF token and posts with it."""

    def __init__(self, app, addr="203.0.113.7"):
        self.c = app.test_client()
        self.addr = addr

    def get(self, url, **kw):
        return self.c.get(url, environ_base={"REMOTE_ADDR": self.addr}, **kw)

    def token(self, url="/"):
        """Visitors get a signed token in the page itself (no cookie); the admin's token lives in its session."""
        if url.startswith("/admin"):
            self.get("/admin/")
            with self.c.session_transaction() as s:
                if not s.get("csrf"):
                    self.get("/admin/login")  # not logged in: the login form starts the session
            with self.c.session_transaction() as s:
                return s.get("csrf") or ""
        m = re.search(r'name="csrf-token" content="([^"]+)"', self.get("/").data.decode())
        return m.group(1) if m else ""

    def post(self, url, data=None, json_body=None, **kw):
        t = self.token(url)
        if json_body is not None:
            return self.c.post(url, json=json_body, headers={"X-CSRF-Token": t},
                               environ_base={"REMOTE_ADDR": self.addr}, **kw)
        return self.c.post(url, data={"_csrf": t, **(data or {})}, environ_base={"REMOTE_ADDR": self.addr}, **kw)


def old_ts():
    return str(int(time.time()) - 60)


def public_check(browser, **extra):
    data = {"posting_text": PUBLIC_POSTING, "title": "Search PM", "company": "Acme", "ts": old_ts()}
    data.update(extra)
    return browser.post("/check", data)


def token_of(response):
    return response.headers["Location"].split("/r/")[1].split("?")[0]
