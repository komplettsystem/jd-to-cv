"""The eval datasets the admin can run. Each has a kind that says what its ground truth is:

  synthetic  the answer follows from how the case was written
  labels     the owner's own reviewed reports
  judge      another model's opinion
  scope      rules checked by code on fixed questions

See docs/EVALS.md for what each one can and cannot tell you."""
from evals import benchmark, chat_cv, chat_scope, citation, labels, overall, synthetic

SPECS = [synthetic.SPEC, labels.SPEC, citation.SPEC, chat_scope.SPEC, overall.SPEC, benchmark.SPEC, chat_cv.SPEC]


def _app_db():
    """The running app's database, when called from a request. None on the command line without one."""
    try:
        from flask import current_app, has_app_context
        if has_app_context():
            return current_app.extensions["svc"].db
    except Exception:  # no Flask app around: counts that need the database show as 0
        pass
    return None


def datasets(db=None):
    """One dict per dataset: name, kind, label, description, n_cases (counted now, for datasets built from stored reports)."""
    db = db or _app_db()
    return [{"name": s.name, "kind": s.kind, "label": s.label, "description": s.description, "n_cases": s.count(db)}
            for s in SPECS]


def get(name):
    """The dataset called `name`; its plan(svc, entry) builds the cases."""
    for s in SPECS:
        if s.name == name:
            return s
    raise KeyError(f"unknown dataset {name!r}; known: {', '.join(s.name for s in SPECS)}")
