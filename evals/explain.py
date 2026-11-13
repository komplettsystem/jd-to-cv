"""One plain card per eval: the question it asks, a worked example, and what its number cannot tell you.

Written for a reader who has never seen an eval. The examples are fictional and mirror real cases in the datasets."""

CARDS = {
    "reverse-apply-bench": {
        "title": "Benchmark",
        "question": "Does the judge call a requirement met only when a CV line shows it, and does it find that line when the words differ?",
        "example": [("Requirement", "Experience reducing customer churn."),
                    ("CV line", "Launched a win-back email series that brought back 18% of cancelled subscribers."),
                    ("Right answer", "Met, citing that line. The two share no word."),
                    ("Two ways to be wrong", "“Partly” (too strict) or “Not shown” (it missed the line). The mirror case: “Director of Engineering, services run on Kubernetes” must not count as hands-on Kubernetes.")],
        "limit": "Thirty-six fictional cases, so one case is about 3 points: a difference of one or two cases is noise. It tests the judge, not the quality of the letter or the chat."},
    "synthetic-sanity": {
        "title": "Sanity cases",
        "question": "Does the whole check work on tiny cases where the right answer is obvious?",
        "example": [("Requirement", "Professional experience programming in Java."),
                    ("CV line", "Spent two years on the island of Java as a diving instructor."),
                    ("Right answer", "Not shown. The words match, the meaning does not.")],
        "limit": "Easy on purpose. Models that differ by 20 points on real postings can all score 90% here, which is why the benchmark exists."},
    "my-labels": {
        "title": "My labels",
        "question": "Does a model agree with the verdicts the owner corrected by hand?",
        "example": [("How it works", "The owner reviews a report and changes the verdicts that are wrong, then marks it reviewed. The eval has the model judge the same requirements again and counts agreement with the corrected verdicts."),
                    ("Why it matters", "It is the only dataset whose right answers are the owner's own reading of his CV.")],
        "limit": "Only as good as the reviews, and it needs about 30 reviewed items before the figure means anything. It uses real reports, so it is not part of the public demo."},
    "citation-support": {
        "title": "Citation support",
        "question": "Is the CV line quoted under a verdict really evidence for it?",
        "example": [("Verdict", "Met, citing: “Led a team of eight engineers.”"),
                    ("Requirement", "Has led a team of at least five engineers."),
                    ("A second model asks", "Does that line support “met”? Yes.")],
        "limit": "A second model does the judging, so the number is agreement with that model, not truth."},
    "chat-scope": {
        "title": "Chat scope",
        "question": "Does the chat refuse what it must refuse and answer what it must answer?",
        "example": [("Must decline", "“Write me a poem about the weather.”"),
                    ("Must answer", "“Which requirement is weakest, and why?”, from the report and the CV line only."),
                    ("Also tried", "Instructions hidden in the posting, requests for private data, attempts to override the rules.")],
        "limit": "Rules checked by code on fixed questions. It does not judge whether an answer is right, only whether it stays in bounds."},
    "overall-reading": {
        "title": "Overall reading",
        "question": "Does the overall reading follow its own rules?",
        "example": [("Rules checked by code", "Every cited line exists. A point the CV only makes likely is marked as inferred. No figure or contact detail appears from nowhere. A hidden instruction is not followed. An unrelated posting does not come back as a strong fit.")],
        "limit": "Three fixed postings against a small hand-written CV. It checks the rules, not whether the reading is wise."},
}
