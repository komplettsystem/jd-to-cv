"""Admin login, CSRF protection, a login rate limit and security headers.

Generate a password hash with:  python -m web.auth hash
and put it in ADMIN_PASSWORD_HASH. Without a hash, the admin answers only to this machine."""
import hmac
import secrets
import sys
import time
from getpass import getpass
from urllib.parse import urlparse

from flask import abort, current_app, g, request, session
from itsdangerous import BadSignature, URLSafeTimedSerializer
from markupsafe import Markup
from werkzeug.security import check_password_hash, generate_password_hash

from config import LOOPBACK, network_key

TOKEN_SECONDS = 12 * 3600  # how long a visitor's form stays valid
MAX_FAILS = 5
LOCK_SECONDS = 15 * 60
_fails = {}  # ip -> (count, first failure time). In memory: fine for one process, see docs/DEPLOYMENT.md

CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; "
       "font-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")


def hash_password(password):
    return generate_password_hash(password, method="scrypt")


def check_bind(host, settings):
    if host not in LOOPBACK and not settings.admin_password_hash:
        raise SystemExit("Refusing to listen on a public address without ADMIN_PASSWORD_HASH "
                         "(create one with `python -m web.auth hash`).")


def _signer():
    return URLSafeTimedSerializer(current_app.secret_key, salt="form-token")


def csrf_token():
    """The admin's pages carry a token tied to the login session. Every other page carries a signed, expiring token
    that needs no cookie at all, so a visitor's browser stores nothing."""
    if request.blueprint == "admin":
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(24)
        return session["csrf"]
    return _signer().dumps(secrets.token_urlsafe(8))


def csrf_field():
    return Markup(f'<input type="hidden" name="_csrf" value="{csrf_token()}">')


def verify_csrf():
    """Every POST must carry the session's token, as a form field or the X-CSRF-Token header."""
    sent = request.form.get("_csrf") or request.headers.get("X-CSRF-Token") or ""
    if request.blueprint == "admin":
        expected = session.get("csrf") or ""
        ok = bool(expected) and hmac.compare_digest(sent, expected)
    else:
        try:
            _signer().loads(sent, max_age=TOKEN_SECONDS)
            ok = True
        except BadSignature:
            ok = False
    if not ok:
        abort(403, description="The form expired. Reload the page and try again.")
    fetch_site = request.headers.get("Sec-Fetch-Site")
    if (fetch_site or "same-origin") not in ("same-origin", "none"):
        abort(403)
    origin = request.headers.get("Origin")
    if origin == "null":
        # The site sends Referrer-Policy: no-referrer, so browsers send "Origin: null" on its own form posts. Accept that
        # only when the browser also states the post comes from this same origin; without that proof it is refused.
        if fetch_site != "same-origin":
            abort(403)
    elif origin and urlparse(origin).netloc != request.host:
        abort(403)


def login_locked(ip):
    ip = network_key(ip)
    count, first = _fails.get(ip, (0, 0))
    if count >= MAX_FAILS and time.time() - first < LOCK_SECONDS:
        return True
    if count and time.time() - first >= LOCK_SECONDS:
        _fails.pop(ip, None)
    return False


def record_failure(ip):
    ip = network_key(ip)
    now = time.time()
    if len(_fails) > 5000:  # keep memory bounded: forget lockouts that have run out
        for k in [k for k, (_, first) in _fails.items() if now - first >= LOCK_SECONDS]:
            _fails.pop(k, None)
    count, first = _fails.get(ip, (0, now))
    _fails[ip] = (count + 1, first)


def clear_failures(ip):
    _fails.pop(network_key(ip), None)


_hits = {}  # key -> list of timestamps


def allow(key, limit, window_seconds):
    """Sliding-window limiter: True if `key` has made fewer than `limit` calls in the last `window_seconds`."""
    now = time.time()
    if len(_hits) > 5000:  # keep memory bounded: drop keys with no hit in the last day
        for k in [k for k, ts in _hits.items() if not ts or now - ts[-1] > 86400]:
            _hits.pop(k, None)
    recent = [t for t in _hits.get(key, []) if now - t < window_seconds]
    if len(recent) >= limit:
        _hits[key] = recent
        return False
    recent.append(now)
    _hits[key] = recent
    return True


def check_login(settings, user, password):
    """Constant-time on both fields; the hash is always checked so timing does not reveal the user name."""
    hashed = settings.admin_password_hash or generate_password_hash("x")
    password_ok = check_password_hash(hashed, password or "")
    user_ok = hmac.compare_digest((user or "").encode(), settings.admin_user.encode())
    return bool(settings.admin_password_hash) and password_ok and user_ok


def is_admin(settings):
    if settings.demo:        # the read-only demo: fictional data, and the app refuses every write (app.py guard)
        return True
    if settings.admin_password_hash:
        return bool(session.get("admin"))
    # No password configured: this machine only, judged by the real connection and never by a forwarded header.
    real = request.environ.get("werkzeug.proxy_fix.orig", {}).get("REMOTE_ADDR", request.remote_addr)
    return real in LOOPBACK


def add_headers(response, settings):
    response.headers.setdefault("Content-Security-Policy", CSP)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if settings.cookie_secure:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    if settings.demo or request.path.startswith(("/admin", "/r/")):
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        response.headers["Cache-Control"] = "no-store"
    return response


if __name__ == "__main__":
    if sys.argv[1:] == ["hash"]:
        pw = getpass("New admin password: ")
        if pw != getpass("Repeat: "):
            raise SystemExit("Passwords differ.")
        if len(pw) < 12:
            raise SystemExit("Use at least 12 characters.")
        print(hash_password(pw))
    else:
        raise SystemExit("usage: python -m web.auth hash")
