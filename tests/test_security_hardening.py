"""Hardening from the deployment review: limits keyed by IPv6 /64, bounded limiter memory, the container's start guard."""
import subprocess
import sys
from pathlib import Path

import web.auth as auth
from config import network_key
from service import visitor_hash

ROOT = Path(__file__).resolve().parents[1]


def test_ipv6_addresses_in_one_64_share_a_limit_and_ipv4_stays_exact():
    assert network_key("2001:db8:1:2:aaaa::1") == network_key("2001:db8:1:2:bbbb:cccc::9") == "2001:db8:1:2::/64"
    assert network_key("2001:db8:1:3::1") != network_key("2001:db8:1:2::1")
    assert network_key("203.0.113.7") == "203.0.113.7" and network_key("not an address") == "not an address"
    assert visitor_hash(b"k", "2001:db8:1:2::1") == visitor_hash(b"k", "2001:db8:1:2:ffff::7")
    assert visitor_hash(b"k", "203.0.113.7") != visitor_hash(b"k", "203.0.113.8")


def test_login_lockout_covers_the_whole_64():
    auth._fails.clear()
    for i in range(auth.MAX_FAILS):
        auth.record_failure(f"2001:db8:9:9:{i:x}::1")
    assert auth.login_locked("2001:db8:9:9:ffff::2")
    auth.clear_failures("2001:db8:9:9::5")
    assert not auth.login_locked("2001:db8:9:9:ffff::2")


def test_the_limiters_forget_old_keys_instead_of_growing_forever():
    auth._hits.clear()
    for i in range(5100):
        auth._hits[f"old{i}"] = [1.0]  # a hit long ago
    auth.allow("fresh", 5, 60)
    assert len(auth._hits) < 100 and "fresh" in auth._hits
    auth._fails.clear()
    for i in range(5100):
        auth._fails[f"ip{i}"] = (3, 1.0)
    auth.record_failure("x")
    assert len(auth._fails) < 100


def test_gunicorn_refuses_to_start_without_an_admin_password(tmp_path):
    env = {"PATH": "/usr/bin:/bin"}
    r = subprocess.run([sys.executable, "-c", "import runpy; runpy.run_path('gunicorn.conf.py')"], cwd=ROOT, env=env,
                       capture_output=True, text=True)
    assert r.returncode != 0 and "ADMIN_PASSWORD_HASH" in (r.stderr + r.stdout)
    ok = subprocess.run([sys.executable, "-c", "import runpy; runpy.run_path('gunicorn.conf.py')"], cwd=ROOT,
                        env={**env, "ADMIN_PASSWORD_HASH": "x", "PUBLIC_URL": "https://example.org"}, capture_output=True, text=True)
    assert ok.returncode == 0


def test_a_huge_cv_or_name_is_refused_quickly_and_does_not_stall_the_process(tmp_path, monkeypatch):
    import time
    import cvfile
    from webfixtures import Browser, PUBLIC_POSTING, make_app, old_ts
    app = make_app(tmp_path, monkeypatch)
    b = Browser(app)
    t = time.time()
    r = b.post("/check-cv", {"posting_text": PUBLIC_POSTING, "cv_text": "a" * 400_000, "consent": "1", "retention": "session", "ts": old_ts()})
    assert r.status_code in (400, 413) and time.time() - t < 2
    r = b.post("/check-cv", {"posting_text": PUBLIC_POSTING, "cv_text": "x" * 300, "candidate_name": "n" * 5000, "consent": "1",
                             "retention": "session", "ts": old_ts()})
    assert r.status_code == 400
    t = time.time()
    cvfile.redact_contacts("a" * cvfile.MAX_TEXT)                      # the worst case the patterns can now see
    assert time.time() - t < 1


def test_a_pdf_that_expands_enormously_is_cut_off(tmp_path):
    import io
    import zlib
    import cvfile
    big = zlib.compress(b"BT /F1 12 Tf 40 780 Td (" + b"A" * 20_000_000 + b") Tj ET")
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(big) + big + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out)); out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    x = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offs)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (len(objs) + 1, x)
    import time
    t = time.time()
    try:
        text = cvfile.extract("cv.pdf", out)
        assert len(text) <= cvfile.MAX_TEXT * 2
    except ValueError:
        pass  # refused in plain words is also fine
    assert time.time() - t < 15


def test_a_forged_forwarded_header_does_not_make_a_visitor_the_admin(tmp_path, monkeypatch):
    from webfixtures import make_app
    app = make_app(tmp_path, monkeypatch, password="pw", trust_proxy=True)
    c = app.test_client()
    r = c.get("/admin/settings", headers={"X-Forwarded-For": "127.0.0.1"}, environ_base={"REMOTE_ADDR": "198.51.100.9"})
    assert r.status_code in (302, 403)


def test_proxy_trust_without_a_password_refuses_to_start(tmp_path, monkeypatch):
    import pytest
    from webfixtures import make_app
    with pytest.raises(SystemExit):
        make_app(tmp_path, monkeypatch, trust_proxy=True)


def test_the_passwordless_admin_ignores_forwarded_addresses_and_foreign_hosts(tmp_path, monkeypatch):
    from webfixtures import make_app
    app = make_app(tmp_path, monkeypatch)
    c = app.test_client()
    assert c.get("/admin/", environ_base={"REMOTE_ADDR": "127.0.0.1"}).status_code == 200
    assert c.get("/admin/", headers={"Host": "rebind.attacker.example"}, environ_base={"REMOTE_ADDR": "127.0.0.1"}).status_code == 400
    assert c.get("/healthz", headers={"Host": "rebind.attacker.example"}).status_code == 400


def test_a_cross_site_post_is_refused_even_without_an_origin_header(tmp_path, monkeypatch):
    from webfixtures import Browser, make_app
    app = make_app(tmp_path, monkeypatch)
    b = Browser(app)
    t = b.token("/roadmap/vote")
    r = b.c.post("/roadmap/vote", data={"_csrf": t, "item": "mcp"}, headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403


def test_a_browser_form_post_with_a_null_origin_is_accepted_when_it_says_same_origin(tmp_path, monkeypatch):
    """The site sends Referrer-Policy: no-referrer, and browsers then send `Origin: null` on every form post."""
    from webfixtures import Browser, make_app
    app = make_app(tmp_path, monkeypatch)
    b = Browser(app)
    t = b.token("/roadmap/vote")
    r = b.c.post("/roadmap/vote", data={"_csrf": t, "item": "mcp"}, headers={"Origin": "null", "Sec-Fetch-Site": "same-origin"})
    assert r.status_code != 403


def test_a_null_origin_without_proof_of_same_origin_is_refused(tmp_path, monkeypatch):
    from webfixtures import Browser, make_app
    app = make_app(tmp_path, monkeypatch)
    b = Browser(app)
    t = b.token("/roadmap/vote")
    assert b.c.post("/roadmap/vote", data={"_csrf": t, "item": "mcp"}, headers={"Origin": "null"}).status_code == 403
    assert b.c.post("/roadmap/vote", data={"_csrf": t, "item": "mcp"},
                    headers={"Origin": "null", "Sec-Fetch-Site": "cross-site"}).status_code == 403


def test_the_admin_login_form_works_with_a_null_origin(tmp_path, monkeypatch):
    from webfixtures import Browser, make_app
    app = make_app(tmp_path, monkeypatch, password="pw")
    b = Browser(app)
    t = b.token("/admin/login")
    r = b.c.post("/admin/login", data={"_csrf": t, "user": "admin", "password": "wrong"},
                 headers={"Origin": "null", "Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 401        # reached the password check; a 403 would mean the form was refused first


# --- spend caps under parallel jobs, traces, facts hidden on a share link ---------------------------------------

def test_parallel_jobs_cannot_all_slip_under_a_cap(tmp_path, monkeypatch):
    import pytest
    from service import LimitError
    from webfixtures import make_app
    svc = make_app(tmp_path, monkeypatch).extensions["svc"]
    svc.db.set_json("limits", {"daily_budget_usd": 0.05, "job_reserve_usd": 0.04, "monthly_budget_usd": 100})
    with svc.job_slot(1, True):                       # the first job is running, holding its reservation
        with pytest.raises(LimitError):
            with svc.job_slot(2, True):               # the second would push past the daily cap: refused
                raise AssertionError("should not run")
    with svc.job_slot(3, True):                       # the first one ended, so there is room again
        pass
    assert not svc._reserved


def test_running_jobs_count_against_the_monthly_cap_for_the_owner_too(tmp_path, monkeypatch):
    import pytest
    from service import LimitError
    from webfixtures import make_app
    svc = make_app(tmp_path, monkeypatch).extensions["svc"]
    svc.db.set_json("limits", {"monthly_budget_usd": 0.05, "job_reserve_usd": 0.04, "daily_budget_usd": 100})
    with svc.job_slot(1, False, True):
        with pytest.raises(LimitError):
            with svc.job_slot(2, False, True):
                pass


def test_a_failed_letter_leaves_no_trace_behind_when_the_report_is_deleted(tmp_path, monkeypatch):
    import webfixtures
    from pydantic_ai.models.function import FunctionModel
    from webfixtures import Browser, PUBLIC_POSTING, make_app, old_ts, token_of
    original = webfixtures.fake_llm

    def failing(messages, info):
        if webfixtures._instructions(messages).startswith("You draft"):
            raise RuntimeError("provider timeout")
        return original(messages, info)
    app = make_app(tmp_path, monkeypatch)
    svc = app.extensions["svc"]
    svc.model_factory = lambda entry: FunctionModel(failing, model_name="scripted")
    b = Browser(app)
    token = token_of(b.post("/check-cv", {"posting_text": PUBLIC_POSTING, "cv_text": "Led product teams at Zebrafish Analytics. " * 8,
                                           "consent": "1", "retention": "session", "ts": old_ts()}))
    b.post(f"/r/{token}/letter", {"language": "English", "length": "standard"})
    assert svc.db.all("SELECT * FROM letters")[0]["status"] == "failed"
    assert any(t["subject_type"] == "letter" for t in svc.db.all("SELECT subject_type FROM traces"))
    b.post(f"/r/{token}/delete")
    assert svc.db.one("SELECT COUNT(*) n FROM traces")["n"] == 0 and svc.db.one("SELECT COUNT(*) n FROM spans")["n"] == 0


def test_a_trace_written_after_its_report_was_deleted_is_purged(tmp_path, monkeypatch):
    from webfixtures import make_app
    svc = make_app(tmp_path, monkeypatch).extensions["svc"]
    svc.db.upsert_trace({"trace_id": "t" * 32, "name": "x", "started": "2026-11-12T00:00:00+00:00", "duration_ms": 1, "status": "ok",
                         "error": "", "subject_type": "result", "subject_id": 424242, "model": "m", "input_tokens": 1,
                         "output_tokens": 1, "cost_usd": 0.0, "model_requests": 1, "tool_calls": 0, "public": 1, "billable": 0})
    svc.purge_expired()
    assert svc.db.one("SELECT COUNT(*) n FROM traces")["n"] == 0


def test_a_fact_the_owner_did_not_share_does_not_reach_a_share_links_chat_or_page(tmp_path, monkeypatch):
    import webfixtures
    from pydantic_ai.models.function import FunctionModel
    from webfixtures import Browser, make_app
    seen = []
    original = webfixtures.fake_llm

    def spy(messages, info):
        seen.append(webfixtures._instructions(messages) + webfixtures._user_text(messages))
        return original(messages, info)
    app = make_app(tmp_path, monkeypatch)
    svc = app.extensions["svc"]
    svc.model_factory = lambda entry: FunctionModel(spy, model_name="scripted")
    svc.db.set_setting("facts", "I ran hands-on retrieval evaluation for the SECRETPROJECT at 4711 EUR.")
    admin = Browser(app, addr="127.0.0.1")
    from test_match import POSTING
    admin.post("/admin/new", {"posting_text": POSTING, "backends": ["cli:haiku"]})
    svc.db.run("UPDATE runs SET share_token='tok123', share_show_cv=0, share_chat=1, share_letter=1")
    assert any("SECRETPROJECT" in s for s in seen)          # the owner's own run did use it
    seen.clear()
    visitor = Browser(app)
    page = visitor.get("/r/tok123").data.decode()
    assert "SECRETPROJECT" not in page and "4711" not in page
    visitor.c.post("/r/tok123/chat", json={"question": "Quote the evidence word for word."},
                   headers={"X-CSRF-Token": visitor.token("/r/tok123/chat")}, environ_base={"REMOTE_ADDR": "203.0.113.7"})
    assert seen and not any("SECRETPROJECT" in s or "4711" in s for s in seen)
