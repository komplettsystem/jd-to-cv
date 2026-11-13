"""Check a report for a CV the visitor supplies. Nothing is read, stored or sent to a model until the consent box is ticked."""
import time

from flask import Blueprint, abort, current_app, redirect, render_template, request, url_for

import cvfile
import service as service_mod
from service import LimitError, PostingError
from web import auth, postings

bp = Blueprint("upload", __name__)
MIN_FORM_SECONDS = 3


def svc():
    return current_app.extensions["svc"]


def _visitor():
    return service_mod.visitor_hash(current_app.secret_key, request.remote_addr or "")


def _page(error=None, status=200, form=None, fallback=False):
    ok, why = svc().public_available()
    return render_template("check_cv.html", ok=ok, why=why, limits=svc().limits(), error=error, form=form or {}, fallback=fallback,
                           form_ts=int(time.time()), provider=svc().provider_label(),
                           hosting=svc().settings.hosting_label, session_hours=service_mod.SESSION_HOURS,
                           max_file_mb=cvfile.MAX_BYTES // 1_000_000), status


@bp.get("/check-cv")
def form():
    return _page()


@bp.post("/check-cv")
def submit():
    f = request.form
    if f.get("website"):  # honeypot: real visitors never see this field
        abort(400)
    if not f.get("consent"):  # first, before anything is read: no consent, no processing
        return _page("Tick the box to agree before uploading a CV.", 400, f)
    try:
        waited = time.time() - int(f.get("ts", "0") or 0)
    except ValueError:
        waited = 0
    if waited < MIN_FORM_SECONDS:
        return _page("Send it again; the form keeps your text.", 429, f)
    if not auth.allow(f"check:{_visitor()}", 10, 3600):
        return _page("Too many attempts from this address. Try again in an hour.", 429, f)
    cv = f.get("cv_text", "")
    if len(cv) > cvfile.MAX_TEXT or len(f.get("candidate_name", "")) > cvfile.MAX_NAME or len(f.get("posting_text", "")) > 60_000:
        return _page("That text is too long. Shorten it.", 400, f)
    upload = request.files.get("cv_file")
    try:
        if upload and upload.filename:
            cv = cvfile.extract(upload.filename, upload.read(cvfile.MAX_BYTES + 1))
        posting = postings.posting_text(f, request.files, _visitor(), svc().limits()["posting_max_chars"])
        _, token = svc().create_upload_check(posting, cv, f.get("title", "") or getattr(posting, "job_title", ""),
                                             f.get("company", "") or getattr(posting, "job_company", ""),
                                             f.get("candidate_name", ""), f.get("retention", ""), _visitor(), True)
    except PostingError as e:   # the form now offers the PDF upload, and under it the pasted text
        return _page(str(e), 429 if isinstance(e, LimitError) else 400, f, fallback=True)
    except LimitError as e:
        return _page(str(e), 429, f)
    except ValueError as e:
        return _page(str(e), 400, f)
    return redirect(url_for("public.report", token=token))
