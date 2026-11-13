"""A long CV line is shown cut to its first clause with the whole line one click away, and the explanation under it stays short."""
import html as html_lib
import re

from app import clip_line
from llm.agents import JUDGE_RULES
from webfixtures import Browser, make_app, public_check, token_of

LONG = ("Built a suite of proof-of-concept apps myself, deployed as separate applications on AWS and in a production "
        "environment, on production data, to show what AI could add: website scraping (a third-party service plus a fleet of "
        "headless Chrome browsers running in Lambda), analysis of websites against user event data.")


def test_a_short_line_is_left_alone_and_a_long_one_is_cut_at_a_clause_with_an_ellipsis():
    assert clip_line("Led the team that cut onboarding from days to minutes.") is None
    short = clip_line(LONG)
    assert short == "Built a suite of proof-of-concept apps myself, deployed as separate applications on AWS…" or (
        len(short) <= 150 and short.endswith("…") and LONG.startswith(short[:-1].rstrip(" ,;")))
    assert len(short) < len(LONG) / 2 + 20


def test_a_report_shows_the_short_quote_and_the_whole_line_behind_a_toggle(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    b = Browser(app, addr="203.0.113.221")
    token = token_of(public_check(b))
    app.extensions["svc"].db.run("UPDATE items SET evidence=? WHERE evidence IS NOT NULL AND evidence != ''", (LONG,))
    html = b.get(f"/r/{token}").data.decode()
    assert re.search(r'<details class="clip"><summary><q class="cv-line clip-short">[^<]{20,150}…</q><q class="cv-line clip-full">' + re.escape(html_lib.escape(LONG)), html)


def test_the_judge_keeps_each_explanation_short():
    assert "at most 15 words" in JUDGE_RULES
