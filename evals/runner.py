"""Runs one eval: reads the eval_runs row, runs the dataset with pydantic-evals, stores one row per case.

Each case runs inside `Tracing.subject("eval.case", "eval_case", <case index>, entry)`, so it has its own trace.
Time, tokens and cost are read from that trace row, the same source the dashboards use.

Eval runs with a billable model count toward the monthly spend cap. The cap is checked before every case; once it
is reached the run stops as failed and keeps the cases it finished."""
import os
from datetime import datetime, timezone

from opentelemetry import context as otel_context
from pydantic_evals import Dataset

from evals import fingerprint, registry
from evals.base import KIND_NOTES, EvalError

DEFAULT_CONCURRENCY = 4


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _concurrency(value):
    try:
        return max(1, int(value if value is not None else os.environ.get("EVAL_CONCURRENCY", DEFAULT_CONCURRENCY)))
    except ValueError:
        return DEFAULT_CONCURRENCY


def execute(svc, eval_run_id, concurrency=None):
    """Run the eval and record the outcome on its row. Never raises for a failed eval; the row says why."""
    run = svc.db.get_eval_run(eval_run_id)
    if run is None:
        raise ValueError(f"no eval run {eval_run_id}")
    svc.db.update_eval_run(eval_run_id, status="running")
    try:
        svc.db.update_eval_run(eval_run_id, fingerprint=fingerprint.current(svc.entry(run["backend"]), registry.get(run["dataset"])))
        summary, n, stopped = _run(svc, run, _concurrency(concurrency))
        if stopped:
            svc.db.update_eval_run(eval_run_id, status="failed", n_cases=n, finished=_now(), summary=summary,
                                   error=f"{stopped} The run stopped after {n} of {summary['planned']} cases; "
                                         "the finished cases are kept.")
        else:
            svc.db.update_eval_run(eval_run_id, status="done", n_cases=n, finished=_now(), summary=summary)
    except EvalError as e:
        svc.db.update_eval_run(eval_run_id, status="failed", error=str(e), finished=_now())
    except Exception as e:  # recorded on the row and shown in the admin
        svc.db.update_eval_run(eval_run_id, status="failed", error=f"{type(e).__name__}: {e}", finished=_now())


class _CapReached(Exception):
    """A case that did not start because an earlier check found the spend cap reached."""


def _run(svc, run, concurrency):
    spec = registry.get(run["dataset"])
    entry = svc.entry(run["backend"])
    plan = spec.plan(svc, entry)
    traces, stopped, skipped = {}, [], set()

    def task(inputs):
        # The spend cap is checked before every case. Cases already running when it is reached still finish,
        # so a run can pass the cap by at most `concurrency` cases.
        try:
            if stopped:
                raise _CapReached(stopped[0])
            svc.check_budget(owner=True)
        except Exception as e:
            if not stopped:
                stopped.append(str(e))
            skipped.add(inputs["index"])
            raise
        # A fresh context makes the case span a root span, so each case is one trace of its own.
        token = otel_context.attach(otel_context.Context())
        try:
            with svc.tracing.subject("eval.case", "eval_case", inputs["index"], plan.entry) as trace_id:
                traces[inputs["index"]] = trace_id
                return plan.task(inputs)
        finally:
            otel_context.detach(token)

    dataset = Dataset(name=spec.name, cases=plan.cases, evaluators=plan.evaluators)
    report = dataset.evaluate_sync(task, name=f"{spec.name} / {entry['id']}", max_concurrency=concurrency,
                                   progress=False)

    rows = sorted([_case_row(svc, c, traces) for c in report.cases]
                  + [_failure_row(svc, f, traces) for f in report.failures if f.inputs["index"] not in skipped],
                  key=lambda r: r["index"])
    for r in rows:
        svc.db.add_eval_case(run["id"], r["name"], r["passed"], r["scores"], r["assertions"], r["output"],
                             r["error"], r["seconds"], r["trace_id"])

    metrics = {k: round(v, 4) for k, v in (report.averages().scores if report.cases else {}).items()}
    notes = [KIND_NOTES[spec.kind], *plan.notes]
    if plan.summarise:
        extra_metrics, extra_notes = plan.summarise(rows)
        metrics.update(extra_metrics)
        notes += extra_notes
    measures, measure_notes = _trace_measures(rows)
    metrics.update(measures)
    notes += measure_notes
    errors = sum(1 for r in rows if r["error"])
    if errors:
        notes.append(f"{errors} of {len(rows)} cases ended with an error and count as failed.")
    passed = sum(1 for r in rows if r["passed"])
    summary = {"pass_rate": passed / len(rows) if rows else 0.0, "metrics": metrics, "kind": spec.kind, "notes": notes,
               "passed": passed, "model": entry["id"], "planned": len(plan.cases)}
    if stopped:
        notes.append(f"Stopped early by the spend cap: {len(rows)} of {len(plan.cases)} cases ran, "
                     "so these figures cover only part of the dataset.")
    return summary, len(rows), stopped[0] if stopped else ""


def _trace_of(svc, trace_id):
    t = svc.db.get_trace(trace_id) if trace_id else None
    if not t:
        return {}
    return {"duration_ms": round(t["duration_ms"], 1), "input_tokens": t["input_tokens"],
            "output_tokens": t["output_tokens"], "cost_usd": t["cost_usd"], "model_requests": t["model_requests"]}


def _case_row(svc, case, traces):
    index = case.inputs["index"]
    assertions = {k: bool(v.value) for k, v in case.assertions.items()}
    why = {k: v.reason for k, v in case.assertions.items() if not v.value and v.reason}
    output = {k: v for k, v in case.output.items() if not k.startswith("_")}
    if why:
        output["why_failed"] = why
    output["trace"] = _trace_of(svc, traces.get(index))
    error = "; ".join(f"evaluator {f.name} failed: {f.error_message}" for f in case.evaluator_failures)
    return {"index": index, "name": case.name, "passed": not error and all(assertions.values()),
            "scores": {k: round(v.value, 4) for k, v in case.scores.items()}, "assertions": assertions,
            "output": output, "error": error, "seconds": case.task_duration, "trace_id": traces.get(index)}


def _failure_row(svc, failure, traces):
    """The task itself raised: the model failed, or the app's parsing rejected its answer after the retries."""
    index = failure.inputs["index"]
    trace = _trace_of(svc, traces.get(index))
    return {"index": index, "name": failure.name, "passed": False, "scores": {}, "assertions": {},
            "output": {"trace": trace}, "error": failure.error_message,
            "seconds": trace["duration_ms"] / 1000 if trace else None, "trace_id": traces.get(index)}


def _trace_measures(rows):
    traced = [r["output"]["trace"] for r in rows if r["output"].get("trace")]
    if not traced:
        return {}, []
    metrics = {"seconds_per_case": round(sum(t["duration_ms"] for t in traced) / len(traced) / 1000, 2),
               "input_tokens": sum(t["input_tokens"] for t in traced),
               "output_tokens": sum(t["output_tokens"] for t in traced),
               "model_requests": sum(t["model_requests"] for t in traced)}
    priced = [t["cost_usd"] for t in traced if t["cost_usd"] is not None]
    notes = []
    if priced:
        metrics["cost_usd"] = round(sum(priced), 4)
        if len(priced) < len(traced):
            notes.append(f"Cost covers {len(priced)} of {len(traced)} cases; the others have no known price.")
    else:
        notes.append("No price is known for this model, so no cost is shown.")
    return metrics, notes
