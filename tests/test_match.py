import json
import re

import pytest

import match
from backends import LLMBackend, BaselineBackend

CV = """<!--
Internal note: Example Corp ended 2026-09-02, approved by Ada.
-->

# Ada Example

Milan, Italy | +39 000 000 | someone@example.com | github.com/x

## Profile

Product manager who has led product teams for ten years. I build agents with coding
agents every day and score my own search tool.

## Experience

### Example Corp, Senior Director of Product

- Led a team of five product managers across the API gateway and catalog enrichment.
- Built a proof of concept that checked an LLM's output against a specification per category.

### Local Knowledge Search

- Built a hybrid BM25 and vector retrieval tool with a 143-question test set and a reranker.
"""

POSTING = """About the role
We build things. Compensation is generous.

What you'll do
- Own the roadmap for the search product and talk to developers every day.

Minimum Requirements
- A record of building AI products and delivering measurable customer outcomes.
- Hands-on experience with retrieval evaluation, including a test set and relevance judgments.
Short.

Preferred Qualifications
- Experience with usage-based pricing and marketplaces.

Benefits
- Medical insurance and a 401(k) plan for everyone.
"""

BLOB = (
    "About the role Search is being rebuilt around AI. What You'll Do Own the strategy for "
    "Agentic Search from ingestion through ranking. Examples of Desirable Skills, Knowledge, "
    "and Experience Proven experience as a PM shipping search or data infrastructure products. "
    "Deep understanding of hybrid retrieval with BM25 and vectors. Compensation The salary is "
    "high. Benefits Health care for you and your family."
)


def test_strip_comments_removes_internal_notes():
    out = match.strip_comments(CV)
    assert "Internal note" not in out
    assert "# Ada Example" in out


def test_chunk_cv_splits_bullets_records_sections_and_skips_contact_line():
    chunks = match.chunk_cv(match.strip_comments(CV))
    texts = [c["text"] for c in chunks]
    assert any(t.startswith("Led a team of five") for t in texts)
    assert any(t.startswith("Built a hybrid BM25") for t in texts)
    assert not any("someone@example.com" in t for t in texts)
    led = next(c for c in chunks if c["text"].startswith("Led a team"))
    assert led["section"] == "Example Corp, Senior Director of Product"
    assert len({c["id"] for c in chunks}) == len(chunks)


def test_chunk_cv_joins_wrapped_paragraph_lines():
    chunks = match.chunk_cv(match.strip_comments(CV))
    profile = next(c for c in chunks if c["text"].startswith("Product manager"))
    assert "score my own search tool" in profile["text"]


def test_rank_evidence_puts_the_matching_chunk_first():
    chunks = match.chunk_cv(match.strip_comments(CV))
    ranked = match.rank_evidence("retrieval tool with a test set and reranker", chunks, k=2)
    assert ranked[0]["text"].startswith("Built a hybrid BM25")
    assert len(ranked) == 2


def test_extract_baseline_keeps_only_requirement_sections_with_kinds():
    reqs = match.extract_baseline(POSTING)
    by_text = {r["text"]: r["kind"] for r in reqs}
    assert by_text["A record of building AI products and delivering measurable customer outcomes."] == "minimum"
    assert by_text["Hands-on experience with retrieval evaluation, including a test set and relevance judgments."] == "minimum"
    assert by_text["Experience with usage-based pricing and marketplaces."] == "desirable"
    joined = " ".join(by_text)
    assert "roadmap" not in joined
    assert "Medical insurance" not in joined
    assert "Short." not in joined


def test_extract_baseline_handles_a_posting_pasted_as_one_line():
    reqs = match.extract_baseline(BLOB)
    texts = [r["text"] for r in reqs]
    assert any(t.startswith("Proven experience as a PM") for t in texts)
    assert any(t.startswith("Deep understanding of hybrid retrieval") for t in texts)
    assert all(r["kind"] == "desirable" for r in reqs)
    assert not any("Own the strategy" in t or "salary" in t or "Health care" in t for t in texts)


def item(kind, verdict, override=None, weight=None):
    return {"kind": kind, "verdict": verdict, "override_verdict": override, "override_weight": weight}


def test_score_weights_minimum_twice_as_much_as_desirable():
    items = [item("minimum", "met"), item("minimum", "not_on_record"), item("desirable", "partial")]
    # weights 2, 2, 1; values 1, 0, 0.5 -> 2.5 / 5
    assert match.score(items) == pytest.approx(50.0)


def test_score_uses_overrides_for_verdict_and_weight():
    items = [item("minimum", "not_on_record", override="met"), item("desirable", "met", weight=0)]
    assert match.score(items) == pytest.approx(100.0)
    items = [item("minimum", "met"), item("desirable", "not_on_record", weight=4)]
    assert match.score(items) == pytest.approx(2 / 6 * 100)


def test_score_is_none_without_weight():
    assert match.score([]) is None
    assert match.score([item("desirable", "met", weight=0)]) is None


def test_minimum_gap_counts_minimum_requirements_not_on_record():
    items = [
        item("minimum", "not_on_record"),
        item("minimum", "met"),
        item("minimum", "not_on_record", override="partial"),
        item("desirable", "not_on_record"),
    ]
    assert match.minimum_gap(items) == (1, 3)


def test_parse_json_accepts_fences_and_trailing_text():
    assert match.parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert match.parse_json('Here you go: {"a": [1, 2]} hope that helps') == {"a": [1, 2]}


def test_parse_json_raises_with_a_snippet_when_there_is_no_json():
    with pytest.raises(ValueError) as e:
        match.parse_json("I cannot do that.")
    assert "I cannot do that" in str(e.value)


def test_parse_judgements_downgrades_a_citation_that_was_not_offered():
    cands = [[{"id": "c1", "section": "S", "text": "t1"}], [{"id": "c2", "section": "S", "text": "t2"}]]
    obj = {"judgements": [
        {"idx": 0, "verdict": "met", "evidence_id": "c1", "reason": "ok"},
        {"idx": 1, "verdict": "met", "evidence_id": "c99", "reason": "made up"},
    ]}
    out = match.parse_judgements(obj, cands)
    assert out[0]["verdict"] == "met" and out[0]["chunk_id"] == "c1" and out[0]["flag"] is None
    assert out[1]["verdict"] == "not_on_record"
    assert out[1]["flag"] == "invalid_citation"


def test_parse_judgements_raises_when_a_requirement_is_missing():
    cands = [[{"id": "c1", "section": "S", "text": "t"}], [{"id": "c2", "section": "S", "text": "t"}]]
    with pytest.raises(ValueError):
        match.parse_judgements({"judgements": [{"idx": 0, "verdict": "met", "evidence_id": "c1", "reason": ""}]}, cands)


def test_parse_judgements_rejects_unknown_verdicts():
    cands = [[{"id": "c1", "section": "S", "text": "t"}]]
    with pytest.raises(ValueError):
        match.parse_judgements({"judgements": [{"idx": 0, "verdict": "yes", "evidence_id": "c1", "reason": ""}]}, cands)


def test_run_match_with_baseline_backend_cites_cv_lines():
    items = match.run_match(POSTING, CV, "", BaselineBackend())
    assert len(items) == 3
    retrieval = next(i for i in items if "retrieval evaluation" in i["text"])
    assert retrieval["verdict"] in ("met", "partial")
    assert "143-question" in retrieval["evidence"]
    pricing = next(i for i in items if "usage-based pricing" in i["text"])
    assert pricing["verdict"] == "not_on_record"


def test_run_match_includes_extra_facts_as_evidence():
    items = match.run_match(POSTING, CV, "I priced a usage-based API plan with marketplaces for partners.", BaselineBackend())
    pricing = next(i for i in items if "usage-based pricing" in i["text"])
    assert pricing["verdict"] in ("met", "partial")
    assert "usage-based API plan" in pricing["evidence"]


def test_run_match_with_an_llm_backend_follows_the_json_contract():
    seen = []

    def fake(prompt):
        seen.append(prompt)
        if "TASK: EXTRACT REQUIREMENTS" in prompt:
            return json.dumps({"requirements": [
                {"text": "Led product teams", "kind": "minimum"},
                {"text": "Built retrieval evals", "kind": "desirable"},
            ]})
        assert "TASK: JUDGE EVIDENCE" in prompt
        led_id = re.search(r"(c\d+) \| [^|]*\| Led a team of five", prompt).group(1)
        return "```json\n" + json.dumps({"judgements": [
            {"idx": 0, "verdict": "met", "evidence_id": led_id, "reason": "led five PMs"},
            {"idx": 1, "verdict": "not_on_record", "evidence_id": None, "reason": "no evidence"},
        ]}) + "\n```"

    items = match.run_match(POSTING, CV, "", LLMBackend(fake))
    assert [i["verdict"] for i in items] == ["met", "not_on_record"]
    assert items[0]["evidence"].startswith("Led a team of five")
    assert items[1]["evidence"] == ""
    assert "Internal note" not in seen[0] + seen[1]
