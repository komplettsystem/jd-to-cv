"""A made-up example for the landing page: fictional candidate, fictional posting, nothing from a real person.
It exists so a visitor can see what a report looks like before pasting anything."""

SAMPLE = {
    "role": "Senior product manager, search",
    "company": "Northwind Labs (fictional)",
    "candidate": "Ada Example (fictional)",
        "gap": (1, 4),
    "items": [
        {"kind": "minimum", "text": "Owned a search or retrieval product used by developers", "verdict": "met",
         "evidence": "Product owner for the site search API used by 2,000 developers.", "section": "Example Corp",
         "reason": "The CV line states ownership of a developer-facing search API."},
        {"kind": "minimum", "text": "Defined relevance metrics and ran offline evaluations", "verdict": "partial",
         "evidence": "Set up weekly relevance reviews with the data team.", "section": "Example Corp",
         "reason": "Reviews are named, but no metrics or offline evaluation are on record."},
        {"kind": "minimum", "text": "Experience with usage-based pricing", "verdict": "not_on_record", "evidence": "",
         "section": "", "reason": "Nothing in the CV mentions pricing."},
        {"kind": "minimum", "text": "Led a cross-functional team of engineers and designers", "verdict": "met",
         "evidence": "Led a team of eight across engineering, design and data.", "section": "Example Corp",
         "reason": "A team size and the functions are stated."},
        {"kind": "desirable", "text": "Familiarity with embeddings and reranking", "verdict": "partial",
         "evidence": "Shipped semantic search using an off-the-shelf embedding model.", "section": "Side project",
         "reason": "Embeddings are named; reranking is not."},
    ],
}


def _with_score():
    import match
    items = [{"kind": i["kind"], "verdict": i["verdict"], "override_verdict": None, "override_weight": None}
             for i in SAMPLE["items"]]
    SAMPLE["score"] = round(match.score(items))
    SAMPLE["gap"] = match.minimum_gap(items)


_with_score()  # the shown score is computed by the same formula the report explains
