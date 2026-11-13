"""Run one eval from the command line, in the foreground:

    python -m evals.run --dataset synthetic-sanity --backend cli:haiku
    python -m evals.run --list

It uses the same database and settings as the app (MATCH_DB, MATCH_DATA, MATCH_CV and the API keys), creates the
eval run row, runs it and prints the summary. The result also shows up under Evals in the admin."""
import argparse
import json
import sys
from pathlib import Path

from evals import registry, runner


def build_service():
    """The app's service without the web layer."""
    from config import Settings
    from db import DB
    from llm import catalog
    from llm.tracing import Tracing
    from service import Service
    settings = Settings.from_env()
    Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
    db = DB(settings.db_path)
    catalog.seed(db)
    return Service(db, settings, Tracing(db, settings))


def parser():
    p = argparse.ArgumentParser(prog="python -m evals.run", description="Run one eval dataset with one model.")
    p.add_argument("--dataset", choices=[s.name for s in registry.SPECS])
    p.add_argument("--backend", metavar="MODEL_ID", help="a model id from the catalog, for example cli:haiku")
    p.add_argument("--concurrency", type=int, default=None, help="cases run at the same time (default 4)")
    p.add_argument("--list", action="store_true", help="list the datasets and exit")
    return p


def main(argv=None, svc=None, out=None):
    out = out or sys.stdout
    p = parser()
    args = p.parse_args(argv)
    if not args.list and not (args.dataset and args.backend):
        p.error("--dataset and --backend are both needed (or --list)")
    svc = svc or build_service()
    if args.list:
        for d in registry.datasets(svc.db):
            print(f"{d['name']:<18} {d['kind']:<10} {d['n_cases']:>3} cases  {d['label']}", file=out)
        return 0
    if svc.db.get_catalog(args.backend) is None:
        p.error(f"unknown model {args.backend!r}; ids look like cli:haiku or openrouter:anthropic/claude-haiku-5.5")
    spec = registry.get(args.dataset)
    eval_run_id = svc.db.create_eval_run(spec.name, spec.kind, args.backend)
    runner.execute(svc, eval_run_id, concurrency=args.concurrency)
    run = svc.db.get_eval_run(eval_run_id)
    print(f"eval run {eval_run_id}: {spec.name} ({spec.kind}) with {args.backend}: {run['status']}", file=out)
    if run["status"] != "done":
        print(f"error: {run['error']}", file=out)
        return 1
    s = run["summary"]
    print(f"passed {s['passed']} of {run['n_cases']} cases (pass rate {s['pass_rate']:.0%})", file=out)
    print(json.dumps(s["metrics"], indent=2), file=out)
    for note in s["notes"]:
        print(f"note: {note}", file=out)
    for c in svc.db.eval_cases(eval_run_id):
        if not c["passed"]:
            failed = [k for k, v in c["assertions"].items() if not v]
            print(f"FAILED {c['name']}: {c['error'] or ', '.join(failed)}", file=out)
            for k, why in (c["output"].get("why_failed") or {}).items():
                print(f"    {k}: {why}", file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
