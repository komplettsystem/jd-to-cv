"""The benchmark over time: what each deployed (or tried) state of the app scored, per model, next to the change that made the state.

Two sources are merged. `history.json` holds runs made on purpose under reconstructed earlier states (tools/backfill_benchmark.py).
Finished runs of the benchmark in the database add themselves: a run with an `as_of` belongs to that state, any other run is a
live run and goes to the last column. `changes.json` is the hand-kept log of changes (what, when, whether it was deployed, what
it measurably did)."""
import json
from pathlib import Path
from statistics import mean

HERE = Path(__file__).resolve().parent
DATASET = "reverse-apply-bench"
LATEST = "latest"
STATE_LABELS = {"start": "Start, 12 Nov", "tightened": "Tightened wording (parked)", "degree-rule": "Degree rule",
                "whole-cv": "Whole CV", LATEST: "Latest runs here"}


def changes():
    return json.loads((HERE / "changes.json").read_text())


def history():
    p = HERE / "history.json"
    return json.loads(p.read_text()) if p.exists() else []


def _short(model_id):
    return str(model_id).split("/")[-1].split(":")[-1]


def _live_rows(eval_runs):
    out = []
    for e in eval_runs or []:
        s = e.get("summary") or {}
        if e.get("dataset") != DATASET or e.get("status") != "done" or not s.get("planned"):
            continue
        m = s.get("metrics", {})
        out.append({"state": e.get("as_of") or LATEST, "model": _short(e["backend"]), "run": f"db{e['id']}", "passed": s.get("passed"),
                    "planned": s.get("planned"), "over_credit": m.get("over_credit_rate"), "under_credit": m.get("under_credit_rate"),
                    "cost_usd": m.get("cost_usd"), "eval_run_id": e["id"]})
    return out


def build(eval_runs=()):
    """{"states": [...], "models": [...], "points": {model: {state: {...}}}} or None when there is nothing to show."""
    live = _live_rows(eval_runs)
    stored = {(r["state"], r["model"]) for r in live if r["state"] != LATEST}     # a database run replaces the file's runs for that state
    rows = [r for r in history() if r.get("status", "done") == "done" and r.get("planned") and (r["state"], r["model"]) not in stored] + live
    if not rows:
        return None
    entries = {c["state"]: c for c in changes() if c.get("state")}
    order = [c["state"] for c in changes() if c.get("state")]
    if any(r["state"] == LATEST for r in rows):
        order.append(LATEST)
    present = {r["state"] for r in rows}
    states = []
    for sid in order:
        if sid not in present:
            continue
        c = entries.get(sid, {})
        states.append({"id": sid, "label": STATE_LABELS.get(sid, sid), "date": c.get("date", ""), "title": c.get("title", "Runs made on this server"),
                       "deployed": c.get("deployed", True), "change_id": c.get("id")})
    models = sorted({r["model"] for r in rows})
    points = {m: {} for m in models}
    for m in models:
        for s in states:
            runs = [r for r in rows if r["model"] == m and r["state"] == s["id"]]
            if not runs:
                continue
            planned = runs[0]["planned"]
            passed = [r["passed"] for r in runs]
            points[m][s["id"]] = {"runs": len(runs), "planned": planned, "mean": mean(passed), "lo": min(passed), "hi": max(passed),
                                  "over": mean([r["over_credit"] for r in runs if r.get("over_credit") is not None] or [0]),
                                  "under": mean([r["under_credit"] for r in runs if r.get("under_credit") is not None] or [0]),
                                  "cost": mean([r["cost_usd"] for r in runs if r.get("cost_usd") is not None] or [0])}
    return {"states": states, "models": models, "points": points}


def deltas(tl):
    """For each model, the change in cases passed from one deployed state to the next deployed one: {model: {state: delta}}."""
    out = {}
    for m in tl["models"]:
        prev, d = None, {}
        for s in tl["states"]:
            p = tl["points"][m].get(s["id"])
            if p is None or not s["deployed"]:
                continue
            if prev is not None:
                d[s["id"]] = p["mean"] - prev["mean"]
            prev = p
        out[m] = d
    return out
