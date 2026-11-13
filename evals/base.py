"""What every eval dataset shares: the plan a dataset hands to the runner, and small helpers.

A dataset is a `Spec`. Its `plan(svc, entry)` returns the pydantic-evals cases and evaluators plus the function
that runs one case through the app's own code. The runner wraps that function in a trace and stores the results."""
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic_evals.evaluators import EvaluationReason

KINDS = ("synthetic", "labels", "judge", "scope")

# One sentence per kind on what the ground truth is. The runner puts it first in every summary's notes.
KIND_NOTES = {
    "synthetic": "Synthetic cases: the expected answer follows from how each case was written, not from anyone's opinion.",
    "labels": "Own labels: the expected verdicts are the owner's reviewed reports, including the verdicts he changed.",
    "judge": "Model-judged: the numbers describe agreement with another model, not truth.",
    "scope": "Scope checks: fixed questions with rules checked by code; no model grades the answers.",
}


class EvalError(Exception):
    """A reason a dataset cannot run, written for the owner (shown as the run's error text)."""


@dataclass
class Plan:
    cases: list                       # pydantic_evals.Case; inputs is a dict that carries "index"
    evaluators: list                  # pydantic_evals Evaluator instances, applied to every case
    task: Callable[[dict], dict]      # runs one case through the app's code; called inside the case's trace
    entry: dict                       # catalog entry of the model the task calls (the trace is priced with it)
    notes: list = field(default_factory=list)
    # From the stored case rows to (extra metrics, extra notes). Optional.
    summarise: Callable[[list], tuple[dict, list]] | None = None


@dataclass
class Spec:
    name: str
    kind: str
    label: str
    description: str

    def count(self, db) -> int:
        raise NotImplementedError

    def plan(self, svc, entry) -> Plan:
        raise NotImplementedError


def check(ok: bool, why: str = "") -> Any:
    """An assertion result that carries the reason when it fails, so the admin can see why."""
    return True if ok else EvaluationReason(value=False, reason=why or None)


def rate(part: int, whole: int, empty: float = 1.0) -> float:
    return part / whole if whole else empty
