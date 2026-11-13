"""my-labels: the owner's reviewed reports as ground truth for the judge step.

Nothing here is invented. A report counts once the owner marked it reviewed; the expected verdict of each
requirement is the verdict that stands after his overrides. The model under test re-judges the same requirements
against the CV lines `match.candidates_for` finds today, through `Agents.judge`, the call a real report makes."""
from collections import Counter
from dataclasses import dataclass

from pydantic_evals import Case
from pydantic_evals.evaluators import Evaluator, EvaluatorContext

import match
from evals.base import EvalError, Plan, Spec, check, rate

SMALL_N = 30      # below this many items the figures are a hint, not a measure
KAPPA_MIN_N = 10  # below this many items kappa is not computed at all

NO_LABELS = "mark at least one report as reviewed first"


def reviewed_results(db):
    """Finished fit results the owner marked reviewed, with their items."""
    out = []
    for r in db.all_results("fit"):
        if r["reviewed"] and r["status"] == "done":
            items = db.items_for(r["id"])
            if items:
                out.append((r, items))
    return out


def cohen_kappa(pairs):
    """Agreement between two raters beyond what their label frequencies would give by chance.
    None when it is undefined (both raters used a single, identical label)."""
    n = len(pairs)
    if not n:
        return None
    observed = sum(1 for a, b in pairs if a == b) / n
    left, right = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    by_chance = sum(left[c] * right[c] for c in match.VERDICTS) / (n * n)
    if by_chance >= 1:
        return None
    return (observed - by_chance) / (1 - by_chance)


@dataclass
class LabelAgreement(Evaluator):
    def evaluate(self, ctx: EvaluatorContext):
        items = ctx.output["items"]
        off = [f"{i['text']} owner {i['expected']}, model {i['got']}" for i in items if not i["agree"]]
        return {"agreement": rate(len(items) - len(off), len(items)),
                "all_items_agree": check(not off, "; ".join(off))}


def summarise(rows):
    pairs = [(i["expected"], i["got"]) for r in rows for i in r["output"].get("items", [])]
    n = len(pairs)
    agree = sum(1 for a, b in pairs if a == b)
    changed = sum(1 for r in rows for i in r["output"].get("items", []) if i["overridden"])
    if not n:
        return {"n_items": 0}, ["No item could be compared: every case ended with an error, so there is no agreement figure."]
    metrics = {"agreement": round(agree / n, 4), "n_items": n, "n_overridden": changed}
    notes = [f"Agreement with the owner's verdicts: {agree} of {n} items, from {len(rows)} reviewed reports. "
             f"He changed {changed} of them; the others he left as the first model judged them, "
             "so a model that resembles that first model is favoured.",
             "The pass rate is the share of reports where the model matched every item; agreement counts items."]
    if n < KAPPA_MIN_N:
        notes.append(f"Cohen's kappa is not computed: {n} items are fewer than {KAPPA_MIN_N}.")
    else:
        kappa = cohen_kappa(pairs)
        if kappa is None:
            notes.append("Cohen's kappa is undefined here: owner and model both used one and the same verdict throughout.")
        else:
            metrics["cohen_kappa"] = round(kappa, 4)
    if n < SMALL_N:
        notes.append(f"Only {n} items (fewer than {SMALL_N}): read these figures as a hint, not a measure.")
    return metrics, notes


class MyLabels(Spec):
    def count(self, db):
        return len(reviewed_results(db)) if db is not None else 0

    def plan(self, svc, entry):
        reviewed = reviewed_results(svc.db)
        if not reviewed:
            raise EvalError(NO_LABELS)
        cases = []
        for i, (result, items) in enumerate(reviewed):
            run = svc.db.get_run(result["run_id"])
            cases.append(Case(
                name=f"report {result['id']}: {(run['title'] or 'untitled')[:60]}",
                inputs={"index": i, "result_id": result["id"]},
                metadata={"first_judged_by": result["backend"], "run_id": run["id"]}))

        def task(inputs):
            result = svc.db.get_result(inputs["result_id"])
            run = svc.db.get_run(result["run_id"])
            public = run["source"] == "public"
            items = svc.db.items_for(result["id"])
            chunks = match.chunk_cv(svc.cv_text(public)) + ([] if public else match.chunk_extra(run["extra_facts"]))
            reqs = [{"text": it["text"], "kind": it["kind"]} for it in items]
            candidates = [match.candidates_for(r["text"], chunks, match.EVIDENCE_K) for r in reqs]
            backend = svc.backend(entry)
            judged = backend.judge(reqs, candidates, chunks) if getattr(backend, "reads_whole_cv", False) else backend.judge(reqs, candidates)
            return {"items": [{"text": it["text"], "expected": match.effective_verdict(it), "got": j["verdict"],
                               "agree": match.effective_verdict(it) == j["verdict"],
                               "overridden": bool(it["override_verdict"]), "flag": j["flag"]}
                              for it, j in zip(items, judged)]}

        return Plan(cases=cases, evaluators=[LabelAgreement()], task=task, entry=entry, summarise=summarise)


SPEC = MyLabels(
    name="my-labels", kind="labels", label="My labels (reviewed reports)",
    description="Reports you marked reviewed. The model judges the same requirements again and is compared with "
                "the verdicts that stand after your changes. Needs at least one reviewed report.")
