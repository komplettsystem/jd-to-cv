"""A degree that the posting says experience can replace ("or equivalent experience") is judged on the years of work the CV shows."""
import match
from test_match import CV


def chunks():
    return match.chunk_cv(match.strip_comments(CV))


DEGREE = "BS or MS in Computer Science, Electrical Engineering or equivalent experience"


def test_such_a_requirement_is_offered_the_profile_line_that_states_the_years():
    offered = match.candidates_for(DEGREE, chunks())
    assert any(c["section"].lower().startswith("profile") for c in offered)
    assert len(offered) == len({c["id"] for c in offered})          # no line twice


def test_other_requirements_get_the_usual_five_lines():
    offered = match.candidates_for("Hands-on experience with retrieval evaluation", chunks())
    assert offered == match.rank_evidence("Hands-on experience with retrieval evaluation", chunks(), match.EVIDENCE_K)


def test_a_degree_with_no_substitute_gets_no_extra_line():
    offered = match.candidates_for("Bachelor's degree required", chunks())
    assert offered == match.rank_evidence("Bachelor's degree required", chunks(), match.EVIDENCE_K)


def test_the_judge_is_told_how_to_read_an_equivalent_experience_clause():
    from llm.agents import JUDGE_RULES
    assert "equivalent" in JUDGE_RULES and "ten or more years" in JUDGE_RULES
