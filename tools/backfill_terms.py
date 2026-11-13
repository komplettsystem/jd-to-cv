"""Run the owner's terms check for runs that have none: the public runs made before visitors' checks were scored against the terms.

Scores only what is saved under "Your terms" in the admin; nothing is shown on a public page. Use it once, inside the app's container
(the posting texts stay on the server):

    docker exec -i reverse-apply-app-1 python - 8 9 10 < tools/backfill_terms.py

Each run id that has no terms result gets one, checked with the public model, one after the other. A run that already has one is skipped.
"""
import sys


def backfill(svc, run_ids):
    """The run ids a terms check was run for."""
    model = svc.public_model()
    done = []
    for run_id in run_ids:
        if not model or svc.db.get_run(run_id) is None or svc.db.results_for(run_id, "terms"):
            continue
        svc.run_terms(svc.db.add_result(run_id, model, kind="terms"))
        done.append(run_id)
    return done


if __name__ == "__main__":
    sys.path.insert(0, ".")
    from app import create_app
    svc = create_app().extensions["svc"]
    if not svc.db.get_json("terms", None):
        sys.exit("No terms are saved in the admin, so there is nothing to score against.")
    ids = [int(a) for a in sys.argv[1:]]
    print("terms checked for runs:", backfill(svc, ids) or "none (they already have one, or do not exist)")
