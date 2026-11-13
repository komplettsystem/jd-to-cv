"""Deleting a report (by the visitor, or when it expires) must not make its spend or its check disappear from the limits:
the daily and monthly caps and the per-visitor daily limit read from small logs that hold no report content."""
import sqlite3
from datetime import datetime, timedelta, timezone

from db import DB
from test_caps import add_trace, setup
from webfixtures import Browser, public_check, token_of

NOW = lambda: datetime.now(timezone.utc)
ISO = lambda dt: dt.isoformat(timespec="seconds")


def make_report(app, svc, addr):
    token = token_of(public_check(Browser(app, addr=addr)))
    run = svc.db.one("SELECT id FROM runs WHERE share_token=?", (token,))["id"]
    result = svc.db.one("SELECT id FROM results WHERE run_id=?", (run,))["id"]
    return run, result


def test_spend_still_counts_after_the_report_is_deleted(tmp_path, monkeypatch):
    app, svc = setup(tmp_path, monkeypatch, {"monthly_budget_usd": 50, "daily_budget_usd": 50})
    run, result = make_report(app, svc, "203.0.113.31")
    add_trace(svc.db, 0.30, result_id=result)
    before = svc.spend()
    assert before["day"] >= 0.30 and before["public_day"] >= 0.30
    svc.db.delete_run(run)                                            # what "Delete my report" and the expiry timer do
    after = svc.spend()
    assert after["month"] == before["month"] and after["day"] == before["day"] and after["public_day"] == before["public_day"]


def test_a_trace_that_is_saved_again_replaces_its_amount_and_is_not_counted_twice(tmp_path, monkeypatch):
    _, svc = setup(tmp_path, monkeypatch, {})
    add_trace(svc.db, 0.10, trace_id="a" * 32)
    add_trace(svc.db, 0.25, trace_id="a" * 32)
    assert abs(svc.db.billable_spend_since("2000-01-01T00:00:00") - 0.25) < 1e-9


def test_plan_and_local_calls_are_not_put_in_the_ledger(tmp_path, monkeypatch):
    _, svc = setup(tmp_path, monkeypatch, {})
    add_trace(svc.db, 50.0, billable=0, public=0)
    assert svc.db.billable_spend_since("2000-01-01T00:00:00") == 0


def test_the_daily_check_limit_is_not_reset_by_deleting_the_report(tmp_path, monkeypatch):
    app, svc = setup(tmp_path, monkeypatch, {"checks_per_day": 1})
    run, _ = make_report(app, svc, "203.0.113.32")
    svc.db.delete_run(run)
    r = public_check(Browser(app, addr="203.0.113.32"))
    assert r.status_code == 429 and b"used your 1 checks" in r.data


def test_spend_recorded_before_the_ledger_existed_is_carried_over(tmp_path, monkeypatch):
    _, svc = setup(tmp_path, monkeypatch, {})
    with sqlite3.connect(svc.db.path) as c:                            # an old database: a trace, no ledger row
        c.execute("DELETE FROM spend_ledger")
        c.execute("INSERT INTO traces (trace_id, name, started, duration_ms, status, error, subject_type, subject_id, model, "
                  "input_tokens, output_tokens, cost_usd, model_requests, tool_calls, public, billable) VALUES "
                  "('old1','x',?,1,'ok','','result',NULL,'m',1,1,0.40,1,0,1,1)", (ISO(NOW()),))
    assert DB(svc.db.path).billable_spend_since("2000-01-01T00:00:00") >= 0.40


def test_old_log_rows_are_purged_and_recent_ones_stay(tmp_path, monkeypatch):
    _, svc = setup(tmp_path, monkeypatch, {})
    old, recent = NOW() - timedelta(days=400), NOW() - timedelta(hours=3)
    with sqlite3.connect(svc.db.path) as c:
        c.execute("INSERT INTO check_log (visitor, created) VALUES ('v', ?)", (ISO(NOW() - timedelta(days=3)),))
        c.execute("INSERT INTO check_log (visitor, created) VALUES ('v', ?)", (ISO(recent),))
        c.execute("INSERT INTO spend_ledger (trace_id, started, cost_usd, public) VALUES ('t-old', ?, 1.0, 1)", (ISO(old),))
        c.execute("INSERT INTO spend_ledger (trace_id, started, cost_usd, public) VALUES ('t-new', ?, 1.0, 1)", (ISO(recent),))
    svc.db.purge_old_records(ISO(NOW() - timedelta(days=365)))
    assert svc.db.one("SELECT COUNT(*) n FROM check_log")["n"] == 1
    assert [r["trace_id"] for r in svc.db.all("SELECT trace_id FROM spend_ledger WHERE trace_id LIKE 't-%'")] == ["t-new"]
