"""The no-model backend: keyword overlap between a requirement and its best-ranked CV line. A floor to compare models against."""
import match


class BaselineBackend:
    def extract_requirements(self, posting):
        return match.extract_baseline(posting)

    def judge(self, requirements, candidates):
        out = []
        for req, cands in zip(requirements, candidates):
            want = set(match.tokens(req["text"]))
            if not cands or not want:
                out.append({"verdict": "not_on_record", "chunk_id": None,
                            "reason": "No CV line shares a word with this requirement.", "flag": None})
                continue
            best = cands[0]
            shared = len(want & set(match.tokens(best["text"])))
            cover = shared / len(want)
            verdict = "met" if cover >= 0.5 else "partial" if cover >= 0.25 else "not_on_record"
            out.append({"verdict": verdict, "chunk_id": best["id"] if verdict != "not_on_record" else None,
                        "reason": f"{shared} of {len(want)} requirement words appear in this line.",
                        "flag": None})
        return out
