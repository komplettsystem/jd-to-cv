"""All visitors together may spend only a slice of the daily budget in any one hour, so one burst (a bot with many addresses)
cannot use up the whole day for the real visitors who arrive later."""
import re
from datetime import datetime, timedelta, timezone

from service import DEFAULT_LIMITS
from test_caps import add_trace, setup
from webfixtures import Browser, public_check


def old_trace(db, cost, hours_ago, public=1):
    started = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat(timespec="seconds")
    db.upsert_trace({"trace_id": f"{hours_ago:02d}{public}" + "0" * 29, "name": "x", "started": started, "duration_ms": 1,
                     "status": "ok", "error": "", "subject_type": "result", "subject_id": None, "model": "m",
                     "input_tokens": 1, "output_tokens": 1, "cost_usd": cost, "model_requests": 1, "tool_calls": 0,
                     "public": public, "billable": 1})


def test_the_default_is_a_quarter_of_the_default_daily_budget():
    assert DEFAULT_LIMITS["hourly_budget_usd"] == DEFAULT_LIMITS["daily_budget_usd"] / 4


def test_visitors_are_paused_when_the_last_hour_used_its_slice_and_are_told_no_amounts(tmp_path, monkeypatch):
    app, svc = setup(tmp_path, monkeypatch, {"hourly_budget_usd": 0.10, "daily_budget_usd": 50, "monthly_budget_usd": 50})
    add_trace(svc.db, 0.20, public=1)
    r = public_check(Browser(app, addr="203.0.113.41"))
    assert r.status_code == 429
    notice = re.search(rb'<p class="notice notice-error"[^>]*>(.*?)</p>', r.data, re.S).group(1)
    assert b"busy right now" in notice and not re.search(rb"[0-9$]", notice)      # no amounts, no numbers at all


def test_spend_from_earlier_than_an_hour_ago_or_by_the_owner_does_not_count(tmp_path, monkeypatch):
    app, svc = setup(tmp_path, monkeypatch, {"hourly_budget_usd": 0.10, "daily_budget_usd": 50, "monthly_budget_usd": 50})
    old_trace(svc.db, 5.0, hours_ago=2)                      # the slice is a rolling hour
    old_trace(svc.db, 5.0, hours_ago=0, public=0)            # the owner's own runs are not visitors' spend
    assert public_check(Browser(app, addr="203.0.113.42")).status_code == 302


def test_the_slice_can_be_changed_on_the_settings_page(tmp_path, monkeypatch):
    app, _ = setup(tmp_path, monkeypatch, {})
    html = Browser(app, addr="127.0.0.1").get("/admin/settings").data.decode()
    assert 'id="hourly_budget_usd"' in html and "in any hour" in html
