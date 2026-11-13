"""Job seekers who use the tool for their own CV may use only a share of the hourly and daily budget and have their own daily
check limit, so a burst of them cannot take what recruiters need. Recruiters may use whatever the job seekers leave."""
import sqlite3

from service import DEFAULT_LIMITS
from test_caps import add_trace, setup
from test_upload import upload
from webfixtures import Browser, public_check, token_of

BIG = {"checks_per_day": 99, "checks_per_day_seeker": 99, "monthly_budget_usd": 99, "checks_per_visitor_per_minute": 99,
       "checks_per_minute_all": 99, "checks_per_hour_all": 99}


def result_of(svc, token):
    run = svc.db.one("SELECT id FROM runs WHERE share_token=?", (token,))["id"]
    return svc.db.one("SELECT id FROM results WHERE run_id=?", (run,))["id"]


def test_the_defaults_give_job_seekers_forty_percent_and_three_checks_a_day():
    assert DEFAULT_LIMITS["seeker_budget_share"] == 0.4 and DEFAULT_LIMITS["checks_per_day_seeker"] == 3


def test_spend_and_checks_are_recorded_with_the_door_they_came_through(tmp_path, monkeypatch):
    app, svc = setup(tmp_path, monkeypatch, BIG)
    seeker = token_of(upload(Browser(app, addr="203.0.113.141")))
    recruiter = token_of(public_check(Browser(app, addr="203.0.113.142")))
    add_trace(svc.db, 0.10, result_id=result_of(svc, seeker), trace_id="s" * 32)
    add_trace(svc.db, 0.20, result_id=result_of(svc, recruiter), trace_id="r" * 32)
    rows = {r["trace_id"]: r["door"] for r in svc.db.all("SELECT trace_id, door FROM spend_ledger")}
    assert rows["s" * 32] == "seeker" and rows["r" * 32] == "recruiter"
    assert sorted(r["door"] for r in svc.db.all("SELECT door FROM check_log")) == ["recruiter", "seeker"]
    assert round(svc.db.public_spend_since("2000-01-01T00:00:00", door="seeker"), 2) == 0.10


def test_job_seekers_are_paused_when_their_hourly_share_is_used_but_recruiters_still_get_in(tmp_path, monkeypatch):
    app, svc = setup(tmp_path, monkeypatch, {**BIG, "hourly_budget_usd": 1.0, "daily_budget_usd": 50, "seeker_budget_share": 0.4})
    token = token_of(upload(Browser(app, addr="203.0.113.143")))
    add_trace(svc.db, 0.45, result_id=result_of(svc, token))                    # 0.45 of the 0.40 a seeker may use in an hour
    r = upload(Browser(app, addr="203.0.113.144"))
    assert r.status_code == 429 and b"own CV are paused" in r.data
    assert public_check(Browser(app, addr="203.0.113.145")).status_code == 302   # 0.45 of 1.00 in total: room for recruiters


def test_the_daily_share_works_the_same_way(tmp_path, monkeypatch):
    app, svc = setup(tmp_path, monkeypatch, {**BIG, "hourly_budget_usd": 50, "daily_budget_usd": 1.0, "seeker_budget_share": 0.4})
    token = token_of(upload(Browser(app, addr="203.0.113.146")))
    add_trace(svc.db, 0.45, result_id=result_of(svc, token))
    assert upload(Browser(app, addr="203.0.113.147")).status_code == 429
    assert public_check(Browser(app, addr="203.0.113.148")).status_code == 302


def test_recruiters_cannot_push_the_total_past_the_cap_and_seeker_spend_counts_in_the_total(tmp_path, monkeypatch):
    app, svc = setup(tmp_path, monkeypatch, {**BIG, "hourly_budget_usd": 1.0, "daily_budget_usd": 50})
    seeker = token_of(upload(Browser(app, addr="203.0.113.149")))
    add_trace(svc.db, 0.40, result_id=result_of(svc, seeker), trace_id="a" * 32)
    recruiter = token_of(public_check(Browser(app, addr="203.0.113.150")))
    add_trace(svc.db, 0.65, result_id=result_of(svc, recruiter), trace_id="b" * 32)     # 1.05 in the hour: the total is used up
    assert public_check(Browser(app, addr="203.0.113.151")).status_code == 429


def test_each_door_has_its_own_daily_check_limit(tmp_path, monkeypatch):
    app, svc = setup(tmp_path, monkeypatch, {**BIG, "checks_per_day_seeker": 1, "checks_per_day": 3})
    b = Browser(app, addr="203.0.113.152")
    assert upload(b).status_code == 302
    r = upload(b)
    assert r.status_code == 429 and b"used your 1 checks" in r.data
    assert public_check(b).status_code == 302                                    # the recruiter door has its own allowance


def test_the_settings_page_has_the_two_new_fields(tmp_path, monkeypatch):
    app, _ = setup(tmp_path, monkeypatch, {})
    html = Browser(app, addr="127.0.0.1").get("/admin/settings").data.decode()
    assert 'id="seeker_budget_share"' in html and 'id="checks_per_day_seeker"' in html
