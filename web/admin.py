"""Admin pages (everything under /admin). Guarded by the login in app.py."""
import random
from pathlib import Path

from flask import (Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, session, url_for)

import cvfile
import ingest
import letters as letters_mod
import match
import pool as pool_mod
import views
from llm import catalog
from service import DEFAULT_LIMITS, DEFAULT_TERMS, LimitError
from web import auth
from web.common import report_context, state_payload

bp = Blueprint("admin", __name__, url_prefix="/admin")
HUMAN_VERDICTS = ("", "apply", "maybe", "skip")


def svc():
    return current_app.extensions["svc"]


def db():
    return svc().db


def settings():
    return svc().settings


def usable():
    return catalog.usable(db(), settings())


# --- login ------------------------------------------------------------------------------

@bp.route("/login", methods=["GET", "POST"])
def login():
    s, ip = settings(), request.remote_addr or ""
    if request.method == "POST":
        if auth.login_locked(ip):
            return render_template("login.html", error="Too many attempts. Wait 15 minutes and try again."), 429
        if auth.check_login(s, request.form.get("user"), request.form.get("password")):
            auth.clear_failures(ip)
            session.clear()
            session["admin"] = True
            session.permanent = True
            nxt = request.form.get("next") or url_for("admin.dashboard")
            return redirect(nxt if nxt.startswith("/admin") else url_for("admin.dashboard"))
        auth.record_failure(ip)
        return render_template("login.html", error="Wrong user name or password."), 401
    return render_template("login.html", error=None, configured=bool(s.admin_password_hash))


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("public.landing"))


# --- dashboard -----------------------------------------------------------------------------

@bp.get("/")
def dashboard():
    d = db()
    scores = views.run_scores(d)
    runs = d.list_runs()
    for r in runs:
        r["scores"] = scores.get(r["id"], {})
    models = usable()
    ok_public, why_public = svc().public_available()
    return render_template(
        "admin/dashboard.html", kpi=views.overview(d), trend=views.trend(d), models=views.model_stats(d),
        quadrant=views.quadrant(d), recent_runs=runs[:8], public_runs=[r for r in runs if r["source"] == "public"][:6],
        recent_traces=d.list_traces(limit=8), limits=svc().limits(),
        available_models=[m for m in models if m["available"] and m["enabled"]],
        spend=svc().spend(), public_ok=ok_public, public_why=svc().public_admin_reason() or why_public, public_fix=svc().public_admin_fix(),
        public_model=svc().public_model(),
        eval_runs=d.eval_runs()[:5])


# --- new run, run list -------------------------------------------------------------------------

@bp.route("/new", methods=["GET", "POST"])
def new():
    error = None
    if request.method == "POST":
        try:
            text, source = (request.form.get("posting_text") or "").strip(), ""
            upload = request.files.get("posting_file")
            url = (request.form.get("posting_url") or "").strip()
            if not text and upload and upload.filename:
                text = ingest.read_upload(upload.filename, upload.read()).strip()
            if not text and url:
                text, source = ingest.fetch_url(url), url
            if not text:
                raise ValueError("Paste the posting, upload a file or give its address.")
            ids = request.form.getlist("backends")
            if not ids:
                raise ValueError("Choose at least one model.")
            if any(i != "baseline" for i in ids):
                svc().check_budget(owner=True)  # a LimitError is a ValueError for the form below
            known = {m["id"]: m for m in usable()}
            for i in ids:
                m = known.get(i)
                if m is None or not m["available"]:
                    raise ValueError(f"{i} is not available" + (f": {m['reason']}" if m else ""))
            facts = "\n".join(x for x in (d_get("facts"), request.form.get("extra_facts", "").strip()) if x.strip())
            run_id = db().create_run(request.form.get("title", "").strip(), request.form.get("company", "").strip(),
                                     source or url, text, facts)
            for i in ids:
                svc().submit(svc().run_fit, db().add_result(run_id, i))
            if request.form.get("also_terms"):
                llm_ids = [i for i in ids if i != "baseline"]
                if not llm_ids:
                    flash("Checking your terms needs a model; the baseline cannot do it.")
                else:
                    svc().submit(svc().run_terms, db().add_result(run_id, llm_ids[0], "terms"))
            return redirect(url_for("admin.run_view", run_id=run_id))
        except (ValueError, OSError) as e:
            error = str(e)
    return render_template("admin/new.html", models=usable(), error=error, form=request.form,
                           default_model=settings().default_fit_model), (400 if error else 200)


def d_get(key):
    return db().get_setting(key)


@bp.get("/runs")
def runs():
    scores = views.run_scores(db())
    rows = db().list_runs()
    for r in rows:
        r["scores"] = scores.get(r["id"], {})
        r["results"] = db().results_for(r["id"])
    return render_template("admin/runs.html", runs=rows)


# --- one run -----------------------------------------------------------------------------------------

def _run(run_id):
    return db().get_run(run_id) or abort(404)


def line_picker(chunks):
    """For a requirement's text: (the five best-matching CV lines, all the other lines), for the owner's line choice."""
    def pick(text):
        best = match.candidates_for(text, chunks)
        ids = {c["id"] for c in best}
        return best, [c for c in chunks if c["id"] not in ids]
    return pick


@bp.get("/runs/<int:run_id>")
def run_view(run_id):
    run = _run(run_id)
    ctx = report_context(svc(), run, admin=True, show_cv=True, can_chat=True, can_letter=True)
    share = (settings().public_url or request.host_url.rstrip("/")) + url_for("public.report", token=run["share_token"]) \
        if run["share_token"] else None
    run_chunks = svc()._chunks(run, run["source"] == "public")
    return render_template("admin/run.html", models=usable(), human_verdicts=HUMAN_VERDICTS, share_url=share, line_picker=line_picker(run_chunks),
                           letter_rules=svc().letter_rules(), terms_criteria=svc().terms_criteria(),
                           chat_model=svc().chat_model_for(run) or settings().default_fit_model,
                           votes=db().votes_for(run_id), **ctx)


@bp.get("/runs/<int:run_id>/state")
def run_state(run_id):
    return jsonify(state_payload(svc(), _run(run_id), int(request.args.get("after", 0) or 0), admin=True))


@bp.post("/runs/<int:run_id>/add")
def run_add(run_id):
    _run(run_id)
    kind = request.form.get("kind", "fit")
    if kind not in ("fit", "terms"):
        abort(400)
    known = {m["id"]: m for m in usable()}
    m = known.get(request.form.get("backend", ""))
    if m is None or not m["available"]:
        flash("That model is not available.")
    else:
        svc().submit(svc().run_fit if kind == "fit" else svc().run_terms, db().add_result(run_id, m["id"], kind))
    return redirect(url_for("admin.run_view", run_id=run_id))


@bp.post("/runs/<int:run_id>/meta")
def run_meta(run_id):
    run = _run(run_id)
    verdict = request.form.get("human_verdict", "")
    if verdict not in HUMAN_VERDICTS:
        abort(400)
    db().update_meta(run_id, request.form.get("title", run["title"]).strip(),
                     request.form.get("company", run["company"]).strip(), request.form.get("notes", ""), verdict)
    return redirect(url_for("admin.run_view", run_id=run_id))


@bp.post("/runs/<int:run_id>/share")
def run_share(run_id):
    _run(run_id)
    action = request.form.get("action")
    if action not in ("enable", "disable"):
        abort(400)
    db().set_share(run_id, action == "enable", bool(request.form.get("show_cv")))
    if action == "enable":
        db().run("UPDATE runs SET share_chat=?, share_letter=? WHERE id=?",
                 (int(bool(request.form.get("share_chat"))), int(bool(request.form.get("share_letter"))), run_id))
    return redirect(url_for("admin.run_view", run_id=run_id))


@bp.post("/runs/<int:run_id>/delete")
def run_delete(run_id):
    _run(run_id)
    db().delete_run(run_id)
    flash("Run deleted, with its results, chat, letters and traces.")
    return redirect(url_for("admin.runs"))


@bp.post("/items/<int:item_id>")
def item_override(item_id):
    item = db().get_item(item_id) or abort(404)
    verdict = request.form.get("override_verdict") or None
    weight = request.form.get("override_weight", "")
    if verdict is not None and verdict not in match.VERDICTS:
        abort(400)
    if weight != "" and (not weight.isdigit() or int(weight) > 5):
        abort(400)
    result = db().get_result(item["result_id"])
    chunk = False
    if "override_chunk" in request.form:       # a form that does not send the field leaves the line as it is
        wanted = request.form["override_chunk"]
        chunk = None
        if wanted:
            run = db().get_run(result["run_id"])
            chunk = next((c for c in svc()._chunks(run, run["source"] == "public") if c["id"] == wanted), None) or abort(400)
    db().set_override(item_id, verdict, int(weight) if weight != "" else None, chunk)
    return redirect(url_for("admin.run_view", run_id=result["run_id"]) + f"#item-{item_id}")


@bp.post("/term-items/<int:item_id>")
def term_override(item_id):
    item = db().get_term_item(item_id) or abort(404)
    verdict = request.form.get("override_verdict") or None
    if verdict is not None and verdict not in match.TERM_VERDICTS:
        abort(400)
    db().set_term_override(item_id, verdict)
    result = db().get_result(item["result_id"])
    return redirect(url_for("admin.run_view", run_id=result["run_id"]) + "#both-ways")


@bp.post("/results/<int:result_id>/reviewed")
def result_reviewed(result_id):
    result = db().get_result(result_id) or abort(404)
    db().set_reviewed(result_id, request.form.get("value") == "1")
    return redirect(url_for("admin.run_view", run_id=result["run_id"]))


# --- talent pool ----------------------------------------------------------------------------------------

@bp.post("/runs/<int:run_id>/pool/plan")
def pool_plan(run_id):
    _run(run_id)
    try:
        svc().run_pool_plan(run_id, request.form.get("backend", ""))
    except (ValueError, RuntimeError) as e:
        flash(str(e))
    return redirect(url_for("admin.run_view", run_id=run_id) + "#pool")


@bp.post("/pool/<int:step_id>")
def pool_count(step_id):
    step = db().one("SELECT * FROM pool_steps WHERE id = ?", (step_id,)) or abort(404)
    text = request.form.get("count_text", "").strip()
    count = pool_mod.parse_count(text)
    if text and count is None:
        flash("Could not read a number from that. Write it as LinkedIn shows it, for example 1,200 or 1K+.")
    else:
        db().set_pool_count(step_id, count, text, request.form.get("source", "").strip() or "entered by hand",
                            request.form.get("note", "").strip())
    return redirect(url_for("admin.run_view", run_id=step["run_id"]) + "#pool")


# --- cover letters ------------------------------------------------------------------------------------------

@bp.post("/runs/<int:run_id>/letter")
def letter_create(run_id):
    _run(run_id)
    known = {m["id"]: m for m in usable()}
    m = known.get(request.form.get("backend", ""))
    if m is None or not m["available"] or m["provider"] == "baseline":
        flash("Choose a model that can write.")
    else:
        try:
            svc().request_letter(run_id, m["id"], request.form.get("instructions", "").strip())
        except LimitError as e:
            flash(str(e))
    return redirect(url_for("admin.run_view", run_id=run_id) + "#letter")


@bp.post("/letters/<int:letter_id>")
def letter_edit(letter_id):
    letter = db().get_letter(letter_id) or abort(404)
    content = request.form.get("content", "")
    db().update_letter(letter_id, content=content, edited=1)
    return redirect(url_for("admin.run_view", run_id=letter["run_id"]) + "#letter")


@bp.post("/runs/<int:run_id>/chat")
def chat(run_id):
    run = _run(run_id)
    body = request.get_json(silent=True) or {}
    model_id = body.get("backend") or svc().chat_model_for(run)
    known = {m["id"]: m for m in usable()}
    m = known.get(model_id)
    if m is None or not m["available"] or m["provider"] == "baseline":
        return jsonify(error="Choose a model that can answer."), 400
    try:
        uid, aid = svc().ask(run_id, model_id, str(body.get("question", "")), bool(body.get("skeptic")))
    except LimitError as e:
        return jsonify(error=str(e)), 429
    except ValueError as e:
        return jsonify(error=str(e)), 400
    return jsonify(ok=True, user_id=uid, assistant_id=aid)


# --- model arena ---------------------------------------------------------------------------------------------------

@bp.post("/runs/<int:run_id>/arena")
def arena_start(run_id):
    _run(run_id)
    ids = [i for i in request.form.getlist("backends") if i != "baseline"]    # the keyword baseline is no opponent
    known = {m["id"]: m for m in usable()}
    if len(ids) < 2 or any(i not in known or not known[i]["available"] for i in ids):
        flash("Choose two or more available models.")
    else:
        for i in ids:
            svc().submit(svc().run_fit, db().add_result(run_id, i))
    return redirect(url_for("admin.run_view", run_id=run_id) + "#arena")


@bp.get("/runs/<int:run_id>/arena")
def arena(run_id):
    """Two finished reports of the same posting, in random order, model names hidden, with a vote form."""
    run = _run(run_id)
    done = [r for r in db().results_for(run_id, "fit") if r["status"] == "done" and r["backend"] != "baseline"]   # models against models
    voted = {frozenset((v["result_a"], v["result_b"])) for v in db().votes_for(run_id)}
    pairs = [(a, b) for i, a in enumerate(done) for b in done[i + 1:]
             if a["backend"] != b["backend"] and frozenset((a["id"], b["id"])) not in voted]
    if not pairs:
        return render_template("admin/arena.html", run=run, pair=None)
    a, b = random.choice(pairs)
    if random.random() < 0.5:
        a, b = b, a
    cards = []
    for r in (a, b):
        items = db().items_for(r["id"])
        for it in items:
            it["eff_verdict"] = match.effective_verdict(it)
        cards.append({"id": r["id"], "items": items, "score": match.score(items), "gap": match.minimum_gap(items)})
    return render_template("admin/arena.html", run=run, pair=cards, remaining=len(pairs),
                           rows=match.align_requirements(cards[0]["items"], cards[1]["items"]))


@bp.post("/runs/<int:run_id>/vote")
def arena_vote(run_id):
    _run(run_id)
    try:
        a, b = int(request.form["left"]), int(request.form["right"])
    except (KeyError, ValueError):
        abort(400)
    winner = {"left": "a", "right": "b", "tie": "tie"}.get(request.form.get("winner", ""))
    if winner is None:
        abort(400)
    db().add_vote(run_id, a, b, winner)
    return redirect(url_for("admin.arena", run_id=run_id))


# --- letter arena: which opening reads better, compared blind -----------------------------------------------------------

@bp.post("/runs/<int:run_id>/letter-variants")
def letter_variants(run_id):
    _run(run_id)
    known = {m["id"]: m for m in usable()}
    m = known.get(request.form.get("backend", ""))
    if m is None or not m["available"] or m["provider"] == "baseline":
        flash("Choose a model that can write.")
    else:
        try:
            svc().request_letter_variants(run_id, m["id"])
        except LimitError as e:
            flash(str(e))
    return redirect(url_for("admin.run_view", run_id=run_id) + "#letter")


@bp.get("/runs/<int:run_id>/letter-arena")
def letter_arena(run_id):
    """Two drafts of the same report with different openings, in random order, with nothing that says which opening or model."""
    run = _run(run_id)
    done = [l for l in db().letters_for(run_id) if l["variant"] and l["status"] == "done" and l["content"]]
    voted = {frozenset((v["letter_a"], v["letter_b"])) for v in db().letter_votes_for(run_id)}
    pairs = [(a, b) for i, a in enumerate(done) for b in done[i + 1:]
             if a["variant"] != b["variant"] and frozenset((a["id"], b["id"])) not in voted]
    if not pairs:
        return render_template("admin/letter_arena.html", run=run, pair=None)
    a, b = random.choice(pairs)
    if random.random() < 0.5:
        a, b = b, a
    cards = [{"id": l["id"], "paragraphs": l["paragraphs"], "content": l["content"]} for l in (a, b)]
    return render_template("admin/letter_arena.html", run=run, pair=cards, remaining=len(pairs))


@bp.post("/runs/<int:run_id>/letter-vote")
def letter_vote(run_id):
    _run(run_id)
    try:
        a, b = int(request.form["left"]), int(request.form["right"])
    except (KeyError, ValueError):
        abort(400)
    winner = {"left": "a", "right": "b", "tie": "tie"}.get(request.form.get("winner", ""))
    la, lb = db().get_letter(a), db().get_letter(b)
    if winner is None or a == b or not la or not lb or la["run_id"] != run_id or lb["run_id"] != run_id \
            or not la["variant"] or not lb["variant"] or la["variant"] == lb["variant"]:
        abort(400)
    db().add_letter_vote(run_id, a, b, la["variant"], lb["variant"], winner)
    return redirect(url_for("admin.letter_arena", run_id=run_id))


@bp.get("/letter-arena/results")
def letter_arena_results():
    return render_template("admin/letter_results.html", stats=svc().letter_vote_stats(), labels=letters_mod.VARIANT_LABELS,
                           votes=len(db().letter_votes()))


# --- traces --------------------------------------------------------------------------------------------------------------

@bp.get("/traces")
def traces():
    kind, model = request.args.get("type") or None, request.args.get("model") or None
    status = request.args.get("status") or None
    rows = db().list_traces(limit=300, subject_type=kind, model=model, status=status)
    return render_template("admin/traces.html", traces=rows, filters={"type": kind, "model": model, "status": status},
                           types=sorted({t["subject_type"] for t in db().list_traces(limit=1000)}),
                           model_names=sorted({t["model"] for t in db().list_traces(limit=1000) if t["model"]}))


@bp.get("/traces/<trace_id>")
def trace(trace_id):
    view = views.trace_view(db(), trace_id) or abort(404)
    return render_template("admin/trace.html", **view)


# --- evals -----------------------------------------------------------------------------------------------------------------

@bp.get("/evals")
def evals():
    from evals import explain, registry, timeline
    runs = db().eval_runs()
    tl = timeline.build(runs)
    return render_template("admin/evals.html", eval_runs=runs, datasets=registry.datasets(),
                           models=[m for m in usable() if m["available"]], timeline=tl,
                           deltas=timeline.deltas(tl) if tl else {}, change_log=timeline.changes(), cards=explain.CARDS)


@bp.post("/evals/run")
def evals_run():
    from evals import registry, runner
    dataset, backend = request.form.get("dataset", ""), request.form.get("backend", "")
    ds = {d["name"]: d for d in registry.datasets()}
    known = {m["id"]: m for m in usable()}
    if dataset not in ds or backend not in known or not known[backend]["available"]:
        flash("Choose a dataset and an available model.")
        return redirect(url_for("admin.evals"))
    eval_run_id = db().create_eval_run(dataset, ds[dataset]["kind"], backend)
    svc().submit(runner.execute, svc(), eval_run_id)
    return redirect(url_for("admin.eval_view", eval_run_id=eval_run_id))


@bp.get("/evals/<int:eval_run_id>")
def eval_view(eval_run_id):
    run = db().get_eval_run(eval_run_id) or abort(404)
    return render_template("admin/eval.html", ev=run, cases=db().eval_cases(eval_run_id),
                           busy=run["status"] in ("queued", "running"))


# --- models, terms, settings ---------------------------------------------------------------------------------------------------

@bp.get("/models")
def models():
    bedrock_ok = catalog.bedrock_configured(settings())
    rows = [m for m in catalog.usable(db(), settings(), include_disabled=True) if bedrock_ok or m["provider"] != "bedrock"]
    current = svc().public_model()
    lim = svc().limits()
    public_choices = [m for m in rows if m["enabled"] and m["provider"] != "baseline"
                      and (m["id"] == current or svc().public_price_ok(m) or (m["local_only"] and settings().allow_local_public))]
    by_id = {m["id"]: m for m in rows}
    chat_model = d_get("chat_model")
    no_tools_roles = {key: bool(by_id.get(val) and not by_id[val]["tools"])
                      for key, val in (("public_model", current), ("chat_model", chat_model or current))}   # an empty chat role follows the public model
    return render_template("admin/models.html", models=rows, bedrock_ok=bedrock_ok, public_choices=public_choices,
                           no_tools=catalog.NO_TOOLS, tool_alternatives=catalog.tool_alternatives(rows), no_tools_roles=no_tools_roles,
                           ceiling_in=lim["public_price_ceiling_in"], ceiling_out=lim["public_price_ceiling_out"],
                           stats={s["backend"]: s for s in views.model_stats(db())},
                           public_model=svc().public_model(), chat_model=d_get("chat_model"),
                           judge_model=d_get("judge_model") or settings().default_judge_model)


@bp.post("/models/toggle")
def models_toggle():
    db().set_catalog_enabled(request.form.get("id", ""), request.form.get("enabled") == "1")
    return redirect(url_for("admin.models"))


@bp.post("/models/effort")
def models_effort():
    effort = request.form.get("effort", "")
    if effort not in ("", "minimal", "low", "medium", "high", "xhigh", "max"):
        abort(400)
    if db().get_catalog(request.form.get("id", "")) is None:
        abort(404)
    db().set_catalog_effort(request.form["id"], effort)
    return redirect(url_for("admin.models"))


@bp.post("/models/sync")
def models_sync():
    which = request.form.get("which")
    try:
        if which == "openrouter":
            n = catalog.sync_openrouter(db())
        elif which == "bedrock":
            n = catalog.sync_bedrock(db(), settings())
        elif which == "ollama":
            n = catalog.sync_ollama(db(), settings())
        else:
            abort(400)
        flash(f"Refreshed {which}: {n} models.")
    except Exception as e:  # network or credentials; shown to the admin as the reason
        flash(f"Could not refresh {which}: {type(e).__name__}: {e}")
    return redirect(url_for("admin.models"))


@bp.post("/models/roles")
def models_roles():
    known = {m["id"] for m in db().list_catalog()}
    wanted = request.form.get("public_model", "")
    entry = db().get_catalog(wanted) if wanted in known else None
    if entry and not svc().public_price_ok(entry) and not (entry["local_only"] and settings().allow_local_public):
        flash(f"Not saved: {entry['label']} costs more than the ceiling for the model that answers visitors "
              f"(change the ceiling under Settings).")
        return redirect(url_for("admin.models"))
    for key in ("public_model", "chat_model", "judge_model"):
        val = request.form.get(key, "")
        if val and val not in known:
            abort(400)
        db().set_setting(key, val)
    return redirect(url_for("admin.models"))


@bp.route("/terms", methods=["GET", "POST"])
def terms():
    if request.method == "POST":
        rows = []
        for i in range(30):
            name = request.form.get(f"name_{i}", "").strip()
            if not name:
                continue
            importance = request.form.get(f"importance_{i}", "important")
            if importance not in match.TERM_WEIGHT:
                abort(400)
            rows.append({"name": name, "description": request.form.get(f"description_{i}", "").strip(),
                         "importance": importance})
        db().set_json("terms", rows or DEFAULT_TERMS)
        flash("Terms saved. New checks use them.")
        return redirect(url_for("admin.terms"))
    return render_template("admin/terms.html", criteria=svc().terms_criteria(), importances=list(match.TERM_WEIGHT),
                           is_default=db().get_json("terms", None) is None)


@bp.get("/feedback")
def feedback_page():
    from web.roadmap import ITEMS, STATUSES
    return render_template("admin/feedback.html", items=ITEMS, statuses=STATUSES, votes=db().roadmap_vote_counts(),
                           wishes=db().feedback(), titles={i["key"]: i["title"] for i in ITEMS})


@bp.post("/feedback/<int:feedback_id>/delete")
def feedback_delete(feedback_id):
    db().delete_feedback(feedback_id)
    return redirect(url_for("admin.feedback_page"))


@bp.route("/settings", methods=["GET", "POST"])
def settings_page():
    if request.method == "POST":
        section = request.form.get("section")
        if section == "facts":
            db().set_setting("facts", request.form.get("facts", ""))
        elif section == "letter_rules":
            db().set_setting("letter_rules", request.form.get("letter_rules", "").strip())
        elif section == "letter_rules_seeker":
            db().set_setting("letter_rules_seeker", request.form.get("letter_rules_seeker", "").strip())
        elif section == "public_cv":
            if request.form.get("action") == "clear":
                db().set_setting("public_cv", "")
                flash("The uploaded public CV is removed. Visitors' checks are off until you upload a public CV again.")
                return redirect(url_for("admin.settings_page"))
            text, upload = request.form.get("cv_text", ""), request.files.get("cv_file")
            try:
                if upload and upload.filename:
                    text = cvfile.extract(upload.filename, upload.read(cvfile.MAX_BYTES + 1))
                if len(text) > cvfile.MAX_TEXT:
                    raise ValueError(f"The CV is longer than {cvfile.MAX_TEXT:,} characters.")
                n = svc().save_public_cv(text)
            except ValueError as e:
                flash(str(e))
                return redirect(url_for("admin.settings_page"))
            flash(f"Public CV saved: {n} lines that reports can quote. Email addresses and phone numbers were removed.")
            return redirect(url_for("admin.settings_page"))
        elif section == "limits":
            lim = {}
            for k, default in DEFAULT_LIMITS.items():
                raw = request.form.get(k, "")
                try:
                    lim[k] = type(default)(raw)
                except ValueError:
                    flash(f"{k}: {raw!r} is not a number.")
                    return redirect(url_for("admin.settings_page"))
            db().set_json("limits", lim)
        else:
            abort(400)
        flash({"facts": "Extra facts saved.", "letter_rules": "Cover letter rules for recruiters saved.",
               "letter_rules_seeker": "Cover letter rules for visitors' own CVs saved.",
               "limits": "Limits and spend caps saved."}[section])
        return redirect(url_for("admin.settings_page"))
    cv = svc().cv_text(public=False)
    uploaded = svc().uploaded_public_cv()
    return render_template("admin/settings.html", facts=d_get("facts"), letter_rules=svc().letter_rules(),
                           letter_rules_seeker=svc().letter_rules("seeker"),
                           limits=svc().limits(), cv=cv, cv_path=settings().cv_path,
                           public_cv_path=settings().public_cv_path, owner=settings().owner_name,
                           uploaded_cv=uploaded, uploaded_lines=len(match.chunk_cv(match.strip_comments(uploaded))) if uploaded else 0,
                           public_cv_file=settings().public_cv_explicit, cv_path_set=settings().cv_explicit)
