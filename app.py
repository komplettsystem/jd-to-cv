"""Reverse Apply: a posting is checked against a CV, and the visitor can ask follow-up questions and get a cover letter.

Run locally:      python app.py                       (http://127.0.0.1:8765)
Run in a server:  gunicorn -w 1 --threads 8 'app:create_app()'   (one process: see docs/DEPLOYMENT.md)
"""
import argparse
from urllib.parse import urlparse
import os
from datetime import timedelta
from pathlib import Path

from flask import Flask, abort, render_template, request, session
from jinja2 import Undefined
from werkzeug.middleware.proxy_fix import ProxyFix

import cvpdf
from config import Settings
from db import DB
from llm import catalog
from llm.tracing import Tracing
from service import Service
from web import auth
from web.admin import bp as admin_bp
from web.public import bp as public_bp
from web.roadmap import bp as roadmap_bp
from web.contact import bp as contact_bp
from web.upload import bp as upload_bp

HERE = Path(__file__).resolve().parent
PUBLIC_ENDPOINTS = {"static"}


def clip_line(text, limit=150):
    """The start of a long CV line, cut at a clause break (a comma, colon or semicolon) when there is one early enough and
    otherwise at a word, with an ellipsis. None when the line is short enough to show whole."""
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return None
    window = text[:limit]
    cut = max((window.rfind(mark) for mark in (", ", ": ", "; ", ". ")), default=-1)
    cut = cut if cut >= 50 else window.rfind(" ")
    return text[:cut].rstrip(" ,;:.") + "\u2026"


def _first_name(full):
    """The name the headline uses: the first word of a two-word name ("Ada Example" -> "Ada"); anything else is kept whole."""
    words = full.split()
    return words[0] if len(words) == 2 and words[0].lower() != "the" else full


def create_app(settings=None):
    settings = settings or Settings.from_env()
    Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
    app = Flask(__name__, template_folder=str(HERE / "templates"), static_folder=str(HERE / "static"))
    app.config["MAX_CONTENT_LENGTH"] = 3_000_000
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SECURE"] = settings.cookie_secure
    app.permanent_session_lifetime = timedelta(hours=12)
    if not settings.secret_key:
        keyfile = Path(settings.data_dir) / "secret.key"
        if not keyfile.exists():
            keyfile.write_bytes(os.urandom(32))
            keyfile.chmod(0o600)
        settings.secret_key = keyfile.read_bytes()
    app.secret_key = settings.secret_key
    if settings.trust_proxy and not settings.admin_password_hash and not settings.demo:
        raise SystemExit("TRUST_PROXY needs ADMIN_PASSWORD_HASH: without a password the admin trusts the local address.")
    hosts = ["localhost", "127.0.0.1", "[::1]"]
    if settings.public_url:  # a page on another domain that points at this machine must not be able to talk to it
        host = urlparse(settings.public_url).hostname
        if host:
            hosts.append(host)
    app.config["TRUSTED_HOSTS"] = hosts
    if settings.trust_proxy:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

    db = DB(settings.db_path)
    catalog.seed(db)
    tracing = Tracing(db, settings)
    svc = Service(db, settings, tracing)
    app.extensions["svc"] = svc
    app.config["SETTINGS"] = settings

    from web import charts
    charts.register(app)
    app.register_blueprint(public_bp)
    app.register_blueprint(roadmap_bp)
    app.register_blueprint(contact_bp)
    app.register_blueprint(upload_bp)
    app.register_blueprint(admin_bp)

    @app.before_request
    def guard():
        if request.endpoint in PUBLIC_ENDPOINTS:
            return None
        if settings.demo and request.method not in ("GET", "HEAD", "OPTIONS"):
            abort(403, description="This is a read-only demo with made-up data: nothing can be changed here.")
        if request.method == "POST":
            auth.verify_csrf()
        if request.blueprint == "admin" and request.endpoint != "admin.login" and not auth.is_admin(settings):
            if settings.admin_password_hash:
                from flask import redirect, url_for
                return redirect(url_for("admin.login", next=request.path))
            abort(403)
        return None

    @app.after_request
    def headers(response):
        return auth.add_headers(response, settings)

    @app.context_processor
    def inject():
        return {"csrf_field": auth.csrf_field, "csrf_token": auth.csrf_token, "owner_name": settings.owner_name,
                "owner_first": _first_name(settings.owner_name),
                "contact_email": settings.contact_email, "is_admin": auth.is_admin(settings), "password_set": bool(settings.admin_password_hash),
                "admin_nav": request.blueprint == "admin" and request.endpoint != "admin.login",
                "demo_mode": settings.demo, "main_url": settings.main_url,
                "repo_url": settings.repo_url.rstrip("/"), "demo_url": settings.demo_url,
                "cv_pdf_stem": cvpdf.filename(settings.owner_name)[:-len(".pdf")]}

    app.add_template_filter(clip_line, "clip_line")

    @app.template_filter("verdict_label")
    def verdict_label(v):
        return {"met": "Met", "partial": "Partial", "not_on_record": "Not shown", "fits": "Fits",
                "conflict": "Conflicts", "unclear": "Posting is silent"}.get(v, v)

    def missing(v):
        return v is None or isinstance(v, Undefined)  # a score that was never computed is "n/a", not an error

    @app.template_filter("pct")
    def pct(v):
        return "n/a" if missing(v) else f"{v:.0f}%"

    @app.template_filter("usd")
    def usd(v):
        if missing(v):
            return "n/a"
        return f"${v:.4f}" if v < 0.1 else f"${v:.2f}"

    @app.template_filter("secs")
    def secs(v):
        if missing(v):
            return "n/a"
        return f"{v * 1000:.0f} ms" if v < 1 else f"{v:.1f} s"

    @app.template_filter("ms")
    def ms(v):
        return "n/a" if missing(v) else (f"{v:.0f} ms" if v < 1000 else f"{v / 1000:.1f} s")

    @app.template_filter("num")
    def num(v):
        return "n/a" if missing(v) else f"{v:,.0f}"

    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(429)
    @app.errorhandler(500)
    def error(e):
        code = getattr(e, "code", 500)
        return render_template("error.html", code=code, message=getattr(e, "description", "")), code

    return app


def maintain(app):
    """Start-up housekeeping: fail jobs a restart interrupted and delete expired visitor reports. Not part of
    create_app, so a second process on the same database (an eval run, a script) cannot fail a running job."""
    svc = app.extensions["svc"]
    result = svc.recover_stalled(), svc.purge_expired()
    if not svc.settings.sync_jobs:  # not in tests: expired reports are deleted every ten minutes, whether or not anyone visits
        import threading

        def loop():
            import time
            while True:
                time.sleep(600)
                try:
                    svc.purge_expired()
                except Exception:
                    pass
        threading.Thread(target=loop, daemon=True, name="purge").start()
    return result


def main():
    p = argparse.ArgumentParser(description="Reverse Apply")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    args = p.parse_args()
    settings = Settings.from_env()
    auth.check_bind(args.host, settings)
    app = create_app(settings)
    maintain(app)
    app.run(host=args.host, port=args.port, threaded=True)


if __name__ == "__main__":
    main()
