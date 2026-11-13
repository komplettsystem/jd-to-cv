"""What a recruiter reads says what the CV shows, not what is wrong with the person: no "Weak fit", no "Gaps and risks"."""
from pathlib import Path

from llm import overall

TEMPLATE = (Path(__file__).resolve().parent.parent / "templates" / "_report.html").read_text()


def test_the_fit_levels_are_descriptions_of_the_match_not_verdicts_on_the_person():
    assert overall.LEVEL_LABELS == {"strong": "Strong fit", "good": "Good fit", "stretch": "A stretch", "weak": "Little overlap"}


def test_the_overall_readings_third_heading_says_where_cv_and_role_differ():
    assert "Gaps and risks" not in TEMPLATE
    assert "('gap', 'Where the CV and the role differ')" in TEMPLATE
