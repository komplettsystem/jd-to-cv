"""Public pages: the landing page, a visitor's check, the report link with its chat and letter, privacy, health."""
import time

from flask import (Blueprint, Response, abort, current_app, flash, jsonify, redirect, render_template, request, url_for)

import cvfile
import cvpdf
import service as service_mod
from service import LETTER_LANGUAGES, LETTER_LENGTHS, LimitError, PostingError
from web import auth, postings
from web.common import report_context, state_payload
from web.sample import SAMPLE

bp = Blueprint("public", __name__)
MIN_FORM_SECONDS = 3


def svc():
    return current_app.extensions["svc"]


def _ip():
    return request.remote_addr or ""


def _visitor():
    return service_mod.visitor_hash(current_app.secret_key, _ip())


def _landing(error=None, status=200, form=None, fallback=False):
    ok, why = svc().public_available()
    lim = svc().limits()
    return render_template("landing.html", ok=ok, why=why, limits=lim, error=error, sample=SAMPLE, max_file_mb=cvfile.MAX_BYTES // 1_000_000,
                           form=form or {}, fallback=fallback, form_ts=int(time.time())), status


@bp.get("/llms.txt")
def llms_txt():
    """A plain text description for automated visitors: what this is, how to use it, what exists and what does not."""
    s, lim = current_app.extensions["svc"].settings, svc().limits()
    base = (s.public_url or request.host_url).rstrip("/")
    contact = "".join(f"- {name}: {url}\n" for name, url in (("LinkedIn", s.linkedin_url), ("GitHub", s.github_url)) if url)
    text = f"""# Reverse Apply: {s.owner_name}

> Checks one job posting against {s.owner_name}'s CV and shows, for each requirement, a verdict with the CV line behind it.
> Written by software. A verdict is a model's reading of two texts, not a fact.

## What you can do
- Hiring or recruiting: give the link to a posting at {base}/ . If the page cannot be read, the form then also accepts a PDF or the pasted text. You get a report, can ask questions about it and get a draft cover letter.
- Looking for a job yourself: check your own CV against a posting at {base}/check-cv (you must agree to the processing of your CV and choose how long it is kept).
- See what a report looks like: the first fold-out on {base}/

## For automated agents
No API or MCP server yet. Both are on the public roadmap: {base}/roadmap
Until then, use the web forms the way a person would, one posting at a time. Please respect the limits (refused requests are not counted):
- checks per visitor per day: {lim['checks_per_day']}; per visitor per minute: {lim['checks_per_visitor_per_minute']}
- all visitors together: {lim['checks_per_minute_all']} checks per minute, {lim['checks_per_hour_all']} per hour
Report pages are private links. Please do not crawl them.

## Privacy and contact
- Privacy: {base}/privacy
- Imprint: {base}/imprint
{contact}"""
    return Response(text, mimetype="text/plain")


@bp.get("/")
def landing():
    return _landing()


@bp.post("/check")
def check():
    form = request.form
    if form.get("website"):  # honeypot: real visitors never see this field
        abort(400)
    if time.time() - int(form.get("ts", "0") or 0) < MIN_FORM_SECONDS:
        return _landing("Send it again; the form keeps your text.", 429, form)
    if not auth.allow(f"check:{_visitor()}", 10, 3600):
        return _landing("Too many attempts from this address. Try again in an hour.", 429, form)
    try:
        posting = postings.posting_text(form, request.files, _visitor(), svc().limits()["posting_max_chars"])
        _, token = svc().create_public_check(posting, form.get("title", "") or getattr(posting, "job_title", ""),
                                             form.get("company", "") or getattr(posting, "job_company", ""), _visitor())
    except PostingError as e:   # the form now offers the PDF upload, and under it the pasted text
        return _landing(str(e), 429 if isinstance(e, LimitError) else 400, form, fallback=True)
    except LimitError as e:
        return _landing(str(e), 429, form)
    except ValueError as e:
        return _landing(str(e), 400, form)
    return redirect(url_for("public.report", token=token))


def _shared_run(token):
    svc().maybe_purge()
    return svc().db.run_by_token(token) or abort(404)


@bp.get("/r/<token>")
def report(token):
    run = _shared_run(token)
    svc().touch_run(run)
    ctx = report_context(svc(), run, admin=False, show_cv=bool(run["share_show_cv"]),
                         can_chat=bool(run["share_chat"]), can_letter=bool(run["share_letter"]))
    lim = svc().limits()
    who = svc().candidate_name(run)
    return render_template("report.html", limits=lim, letter_languages=LETTER_LANGUAGES,
                           letter_lengths=list(LETTER_LENGTHS), token=token, owner=who, owner_name=who,
                           session_hours=service_mod.SESSION_HOURS,
                           is_owner=auth.is_admin(svc().settings), **ctx)


@bp.get("/r/<token>/state")
def state(token):
    run = _shared_run(token)
    return jsonify(state_payload(svc(), run, int(request.args.get("after", 0) or 0), admin=False))


@bp.post("/r/<token>/chat")
def chat(token):
    run = _shared_run(token)
    if not run["share_chat"]:
        abort(404)
    svc().touch_run(run)
    body = request.get_json(silent=True) or {}
    model_id = svc().chat_model_for(run)
    if not model_id:
        return jsonify(error="The chat is not switched on yet."), 503
    if not auth.allow(f"chat:{_visitor()}", 6, 60):
        return jsonify(error="Slow down a little: wait a minute before the next question."), 429
    try:
        uid, aid = svc().ask(run["id"], model_id, str(body.get("question", "")), bool(body.get("skeptic")),
                             max_messages=svc().limits()["messages_per_chat"], audience="shared")
    except LimitError as e:
        return jsonify(error=str(e)), 429
    except ValueError as e:
        return jsonify(error=str(e)), 400
    return jsonify(ok=True, user_id=uid, assistant_id=aid)


@bp.post("/r/<token>/letter")
def letter(token):
    run = _shared_run(token)
    if not run["share_letter"]:
        abort(404)
    svc().touch_run(run)
    language = request.form.get("language", "English")
    length = request.form.get("length", "standard")
    if language not in LETTER_LANGUAGES or length not in LETTER_LENGTHS:
        abort(400)
    model_id = svc().chat_model_for(run)
    if not model_id:
        abort(503)
    lim = svc().limits()
    drafts = [l for l in svc().db.letters_for(run["id"], "shared") if l["status"] != "failed"]  # failed drafts do not count
    if len(drafts) >= lim["letters_per_report"]:
        return redirect(url_for("public.report", token=token, note="letter-limit") + "#letter")
    try:
        svc().request_letter(run["id"], model_id, f"Write in {language}. {LETTER_LENGTHS[length]}", audience="shared")
    except LimitError as e:
        flash(str(e))
        return redirect(url_for("public.report", token=token) + "#letter")
    return redirect(url_for("public.report", token=token) + "#letter")


@bp.get("/r/<token>/letter/<int:letter_id>.txt")
def letter_text(token, letter_id):
    run = _shared_run(token)
    letter_row = svc().db.get_letter(letter_id)
    if (not run["share_letter"] or not letter_row or letter_row["run_id"] != run["id"]
            or letter_row["status"] != "done" or letter_row["audience"] != "shared"):
        abort(404)
    owner = svc().candidate_name(run)
    note = (f"Draft written by software from {owner}'s CV on {letter_row['created'][:10]}. "
            f"Not written or sent by {owner}.\n\n")
    return Response(note + letter_row["content"], mimetype="text/plain; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="cover-letter-{letter_id}.txt"'})


@bp.post("/r/<token>/delete")
def delete(token):
    run = _shared_run(token)
    if run["source"] != "public":
        abort(404)
    svc().db.delete_run(run["id"])
    return redirect(url_for("public.landing", note="deleted"))


@bp.get("/cv.pdf", defaults={"name": None})
@bp.get("/cv/<name>.pdf")
def cv_pdf(name):
    """The public CV as a PDF: the text the reports are checked against, without phone number or email address."""
    try:
        text = svc().cv_text(public=True)
    except OSError:
        abort(404)
    owner = svc().settings.owner_name
    response = Response(cvpdf.render(text, owner), mimetype="application/pdf")
    response.headers["Content-Disposition"] = f'inline; filename="{cvpdf.filename(owner)}"'
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    response.headers["Cache-Control"] = "private, max-age=300"
    return response


@bp.get("/demo")
def demo():
    """The example report is the fold-out on the start page; this old address leads there and opens it."""
    return redirect(url_for("public.landing") + "#sample-title", code=301)


@bp.get("/privacy")
def privacy():
    return render_template("privacy.html", limits=svc().limits(), owner=svc().settings.owner_name,
                           provider_label=svc().provider_label(), hosting=svc().settings.hosting_label,
                           legal_name=svc().settings.legal_name,
                           otlp=bool(svc().settings.otlp_endpoint), session_hours=service_mod.SESSION_HOURS)


@bp.get("/healthz")
def healthz():
    svc().db.one("SELECT 1 AS ok")
    return Response("ok", mimetype="text/plain")
