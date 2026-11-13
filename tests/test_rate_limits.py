"""How fast checks may start: per visitor and for everyone together, by the minute and by the hour. A burst from one source
cannot take the whole service, and the checks are spread out so real visitors still get a turn."""
import re
import sqlite3
from datetime import datetime, timedelta, timezone

from service import DEFAULT_LIMITS
from test_caps import setup
from webfixtures import Browser, public_check

ISO = lambda dt: dt.isoformat(timespec="seconds")
BIG = {"checks_per_day": 99, "daily_budget_usd": 99, "monthly_budget_usd": 99, "hourly_budget_usd": 99}


def notice(r):
    return re.search(rb'<p class="notice notice-error"[^>]*>(.*?)</p>', r.data, re.S).group(1)


def log_rows(svc, visitor, minutes_ago, n):
    with sqlite3.connect(svc.db.path) as c:
        for _ in range(n):
            c.execute("INSERT INTO check_log (visitor, created) VALUES (?, ?)",
                      (visitor, ISO(datetime.now(timezone.utc) - timedelta(minutes=minutes_ago))))


def test_the_defaults_exist_and_are_on_the_settings_page(tmp_path, monkeypatch):
    app, _ = setup(tmp_path, monkeypatch, {})
    assert (DEFAULT_LIMITS["checks_per_visitor_per_minute"], DEFAULT_LIMITS["checks_per_minute_all"],
            DEFAULT_LIMITS["checks_per_hour_all"]) == (2, 6, 60)
    html = Browser(app, addr="127.0.0.1").get("/admin/settings").data.decode()
    for key in ("checks_per_visitor_per_minute", "checks_per_minute_all", "checks_per_hour_all"):
        assert f'id="{key}"' in html
    assert "may start per minute" in html and "may start per hour" in html


def test_one_visitor_cannot_start_checks_in_quick_succession(tmp_path, monkeypatch):
    app, _ = setup(tmp_path, monkeypatch, {**BIG, "checks_per_visitor_per_minute": 1})
    assert public_check(Browser(app, addr="203.0.113.61")).status_code == 302
    r = public_check(Browser(app, addr="203.0.113.61"))
    assert r.status_code == 429 and b"a moment ago" in notice(r)
    assert public_check(Browser(app, addr="203.0.113.62")).status_code == 302     # someone else is not affected


def test_everyone_together_may_start_only_so_many_checks_per_minute(tmp_path, monkeypatch):
    app, _ = setup(tmp_path, monkeypatch, {**BIG, "checks_per_minute_all": 2})
    codes = [public_check(Browser(app, addr=f"203.0.113.{70 + i}")).status_code for i in range(3)]
    assert codes == [302, 302, 429]


def test_the_hourly_count_looks_at_the_last_hour_only_and_says_nothing_about_numbers(tmp_path, monkeypatch):
    app, svc = setup(tmp_path, monkeypatch, {**BIG, "checks_per_hour_all": 5})
    log_rows(svc, "other", minutes_ago=90, n=20)                    # older than an hour: does not count
    assert public_check(Browser(app, addr="203.0.113.81")).status_code == 302
    log_rows(svc, "other", minutes_ago=10, n=5)                     # five in the last hour: the hour is full
    r = public_check(Browser(app, addr="203.0.113.82"))
    assert r.status_code == 429 and b"busy right now" in notice(r) and not re.search(rb"[0-9]", notice(r))


def test_a_refused_attempt_is_not_counted_as_a_check(tmp_path, monkeypatch):
    app, svc = setup(tmp_path, monkeypatch, {**BIG, "checks_per_minute_all": 1})
    public_check(Browser(app, addr="203.0.113.91"))
    before = svc.db.one("SELECT COUNT(*) n FROM check_log")["n"]
    assert public_check(Browser(app, addr="203.0.113.92")).status_code == 429
    assert svc.db.one("SELECT COUNT(*) n FROM check_log")["n"] == before
