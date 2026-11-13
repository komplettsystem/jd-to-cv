"""Run the benchmark (evals/benchmark.py) under reconstructed earlier states of the app and write the outcomes to evals/history.json.

Calibration tool, not part of the site. Each state is the current code with one earlier setting put back: the judge wording
(evals/history_prompts.json), whole-CV judging off, the equivalent-experience lines off. Needs OPENROUTER_API_KEY (deploy/.env)
and spends a few USD at most; it stops when the credits left drop below --reserve.

Usage: python tools/backfill_benchmark.py [--runs 2] [--states start,tightened,degree-rule,whole-cv] [--out evals/history.json]"""
import argparse
import json
import os
import re
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

MODELS = [  # the production settings of each
    {"id": "openrouter:anthropic/claude-haiku-5.5", "mode": "tool", "effort": "medium", "price_in": 0.10, "price_out": 0.50},
    {"id": "openrouter:deepseek/deepseek-v4.1-flash-1017", "mode": "tool", "effort": "high", "price_in": 0.15, "price_out": 0.60},
    {"id": "openrouter:openai/gpt-6.1-terra", "mode": "tool", "effort": "medium", "price_in": 1.00, "price_out": 5.00},
]
STATES = {
    "start": dict(rules="start", whole_cv=0, equivalent=False),
    "tightened": dict(rules="tightened", whole_cv=0, equivalent=False),
    "degree-rule": dict(rules="current", whole_cv=0, equivalent=True),
    "whole-cv": dict(rules="current", whole_cv=60_000, equivalent=True),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--states", default=",".join(STATES))
    ap.add_argument("--out", default=str(ROOT / "evals" / "history.json"))
    ap.add_argument("--reserve", type=float, default=1.0)
    ap.add_argument("--workers", type=int, default=3)
    args = ap.parse_args()

    import compare_models as cm
    cm.load_env(ROOT / "deploy" / ".env")
    work = Path(tempfile.mkdtemp(prefix="backfill-"))
    os.environ.update(MATCH_DATA=str(work), MATCH_DB=str(work / "t.db"), MATCH_CV=str(ROOT / "tools" / "sample_cv.md"))
    import match
    from evals import benchmark, registry, runner
    from evals.run import build_service
    from llm import agents
    svc = build_service()
    agents.SETTINGS["max_tokens"] = 24000
    svc.db.upsert_catalog([{"id": m["id"], "provider": "openrouter", "model_name": m["id"].split(":", 1)[1], "label": m["id"], "enabled": 1,
                            "output_mode": m["mode"], "tools": 1, "price_in": m["price_in"] / 1e6, "price_out": m["price_out"] / 1e6,
                            "effort": m["effort"], "source": "backfill"} for m in MODELS])
    for m in MODELS:
        svc.db.run("UPDATE catalog SET output_mode=?, effort=?, price_in=?, price_out=?, enabled=1 WHERE id=?",
                   (m["mode"], m["effort"], m["price_in"] / 1e6, m["price_out"] / 1e6, m["id"]))
    prompts = json.loads((ROOT / "evals" / "history_prompts.json").read_text())
    current_rules, current_equiv = agents.JUDGE_RULES, match.EQUIVALENT
    trap = {c["name"]: c["trap"] for c in benchmark.CASES}
    spec = registry.get("reverse-apply-bench")
    out = Path(args.out)
    history = json.loads(out.read_text()) if out.exists() else []

    def one(state, model, n):
        eid = svc.db.create_eval_run(spec.name, spec.kind, model["id"], as_of=state)
        runner.execute(svc, eid)
        run, cases = svc.db.get_eval_run(eid), svc.db.eval_cases(eid)
        s = run["summary"] or {}
        m = s.get("metrics", {})
        by = {}
        for c in cases:
            by.setdefault(trap[c["name"]], []).append(bool(c["passed"]))
        return {"state": state, "model": model["id"].split("/")[-1], "run": n, "status": run["status"], "passed": s.get("passed"),
                "planned": s.get("planned"), "over_credit": m.get("over_credit_rate"), "under_credit": m.get("under_credit_rate"),
                "cost_usd": m.get("cost_usd"), "by_group": {k: [sum(v), len(v)] for k, v in by.items()},
                "failed": [c["name"] for c in cases if not c["passed"]], "fingerprint": run["fingerprint"]}

    for state in args.states.split(","):
        cfg = STATES[state]
        left = cm.credits_left()
        print(f"state {state}: credits left {left:.2f}", flush=True)
        if left < args.reserve:
            sys.exit("stopping: credits below the reserve")
        agents.JUDGE_RULES = current_rules if cfg["rules"] == "current" else prompts[cfg["rules"]]
        agents.WHOLE_CV_CHARS = cfg["whole_cv"]
        match.EQUIVALENT = current_equiv if cfg["equivalent"] else re.compile(r"(?!)")
        jobs = [(state, m, n) for n in range(args.runs) for m in MODELS]
        with ThreadPoolExecutor(args.workers) as pool:
            for rec in pool.map(lambda j: one(*j), jobs):
                history = [h for h in history if (h["state"], h["model"], h["run"]) != (rec["state"], rec["model"], rec["run"])] + [rec]
                out.write_text(json.dumps(history, indent=1))
                print(f"  {rec['model']:<28} run {rec['run']}  {rec['passed']}/{rec['planned']}  over {rec['over_credit']}  under {rec['under_credit']}  ${rec['cost_usd']}", flush=True)
    agents.JUDGE_RULES, match.EQUIVALENT = current_rules, current_equiv


if __name__ == "__main__":
    main()
