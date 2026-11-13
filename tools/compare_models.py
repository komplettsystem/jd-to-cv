"""Run a folder of job postings through several models with the app's own check, and write one JSON line per run.

Calibration tool, not part of the site: it builds the app on a throw-away database, uses the OpenRouter key from deploy/.env and
the CV given with --cv (contact details are removed first, as for the public CV), and stops by itself when the spend reaches
--cap USD or the OpenRouter credits left drop below --reserve USD. Posting texts are other people's: keep the folder out of git.

Usage: python tools/compare_models.py --postings DIR --models models.json --cv cv.md --out runs.jsonl [--passes 2]
models.json: a list of {"id": "openrouter:vendor/model", "mode": "tool|prompted", "effort": "", "price_in": per_million, "price_out": per_million}"""
import argparse
import json
import os
import sys
import tempfile
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
ROOT = Path(__file__).resolve().parent.parent


def load_env(path):
    for line in Path(path).read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def credits_left():
    req = urllib.request.Request("https://openrouter.ai/api/v1/credits", headers={"Authorization": "Bearer " + os.environ["OPENROUTER_API_KEY"]})
    data = json.loads(urllib.request.urlopen(req, timeout=20).read())["data"]
    return data["total_credits"] - data["total_usage"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--postings", required=True)
    ap.add_argument("--models", required=True)
    ap.add_argument("--cv", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--passes", type=int, default=1)
    ap.add_argument("--cap", type=float, default=9.0)
    ap.add_argument("--reserve", type=float, default=0.6)
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--only", default="", help="comma separated posting names (for a trial)")
    ap.add_argument("--max-tokens", type=int, default=0, help="answer limit per model request (the site uses 8192); 0 keeps the site's value")
    ap.add_argument("--whole-cv", type=int, default=0, help="show the judge the whole CV when it is at most this many characters (0: the five best word matches per requirement, as the site does)")
    ap.add_argument("--redo-failed-from", default="", help="earlier results file: run only the (posting, model, pass) combinations that did not finish there, plus any that are missing")
    args = ap.parse_args()

    load_env(ROOT / "deploy" / ".env")
    work = Path(tempfile.mkdtemp(prefix="compare-models-"))
    import cvfile
    cv_file = work / "cv.md"
    cv_file.write_text(cvfile.redact_contacts(cvfile.clean(Path(args.cv).read_text())))
    os.environ.update(MATCH_DATA=str(work), MATCH_DB=str(work / "t.db"), MATCH_CV=str(cv_file), MATCH_PUBLIC_CV=str(cv_file))
    from app import create_app
    from config import Settings
    import match
    from llm import agents
    agents.WHOLE_CV_CHARS = args.whole_cv
    if args.max_tokens:
        agents.SETTINGS["max_tokens"] = args.max_tokens
    app = create_app(Settings.from_env())
    svc = app.extensions["svc"]
    db = svc.db

    models = json.loads(Path(args.models).read_text())
    rows = [{"id": m["id"], "provider": "openrouter", "model_name": m["id"].split(":", 1)[1], "label": m["id"], "enabled": 1,
             "output_mode": m.get("mode", "tool"), "tools": 1, "price_in": m["price_in"] / 1e6, "price_out": m["price_out"] / 1e6,
             "effort": m.get("effort", ""), "source": "compare_models"} for m in models]
    db.upsert_catalog(rows)
    for m in models:   # the database already knows some of these from the defaults: use the settings given
        db.run("UPDATE catalog SET output_mode=?, effort=?, price_in=?, price_out=?, enabled=1 WHERE id=?",
               (m.get("mode", "tool"), m.get("effort", ""), m["price_in"] / 1e6, m["price_out"] / 1e6, m["id"]))

    postings = {p.stem: p.read_text() for p in sorted(Path(args.postings).glob("*.txt"))}
    if args.only:
        postings = {k: v for k, v in postings.items() if k in args.only.split(",")}
    tasks = [(name, m["id"], n) for n in range(args.passes) for name in postings for m in models]
    if args.redo_failed_from:
        earlier = [json.loads(l) for l in Path(args.redo_failed_from).read_text().splitlines() if l.strip()]
        finished = {(r["posting"], r["model"], r["pass"]) for r in earlier if r.get("ok")}
        tasks = [t for t in tasks if t not in finished]
    lock, state = threading.Lock(), {"spent": 0.0, "stop": "", "done": 0}
    out = open(args.out, "a")

    def one(task):
        name, model_id, n = task
        with lock:
            if state["stop"]:
                return
            if state["spent"] >= args.cap:
                state["stop"] = f"spend reached the cap of {args.cap} USD"
                return
            if state["done"] % 12 == 0:
                try:
                    left = credits_left()
                    if left < args.reserve:
                        state["stop"] = f"only {left:.2f} USD of credits left"
                        return
                except Exception as e:   # a failed balance lookup is not a reason to stop, the cap still holds
                    print("credit lookup failed:", e, flush=True)
            state["done"] += 1
        started = time.time()
        rec = {"posting": name, "model": model_id, "pass": n, "ok": False}
        try:
            run_id = db.create_run(name, "", "", postings[name], "")
            result_id = db.add_result(run_id, model_id)
            svc.run_fit(result_id)
            result = next(r for r in db.results_for(run_id, "fit") if r["id"] == result_id)
            items = db.items_for(result_id)
            trace = db.get_trace(result["trace_id"]) if result.get("trace_id") else None
            overall = None
            try:
                overall = json.loads(result.get("overall") or "null")
            except ValueError:
                pass
            rec.update(ok=result["status"] == "done", status=result["status"], error=(result.get("error") or "")[:300],
                       seconds=round(time.time() - started, 1), cost=(trace or {}).get("cost_usd") or 0.0,
                       tokens_in=(trace or {}).get("input_tokens", 0), tokens_out=(trace or {}).get("output_tokens", 0),
                       requests=(trace or {}).get("model_requests", 0),
                       score=match.score(items) if items else None,
                       overall_level=(overall or {}).get("level"), overall_error=(overall or {}).get("error"),
                       items=[{"text": i["text"], "kind": i["kind"], "verdict": match.effective_verdict(i), "flag": i["flag"]} for i in items])
        except Exception as e:
            rec.update(status="exception", error=f"{type(e).__name__}: {str(e)[:300]}", seconds=round(time.time() - started, 1), cost=0.0)
        with lock:
            state["spent"] += rec.get("cost") or 0.0
            out.write(json.dumps(rec) + "\n")
            out.flush()
            print(f"{rec['posting']:<18} {rec['model']:<48} pass {n} {rec.get('status'):<9} {rec.get('score') and round(rec['score'])}% "
                  f"{rec.get('seconds')}s ${rec.get('cost', 0):.4f}  total ${state['spent']:.3f}", flush=True)

    with ThreadPoolExecutor(args.workers) as pool:
        list(pool.map(one, tasks))
    print("finished" + (f" early: {state['stop']}" if state["stop"] else "") + f"; spent {state['spent']:.3f} USD in this run", flush=True)


if __name__ == "__main__":
    main()
