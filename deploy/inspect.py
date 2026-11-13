"""Read-only look at the running app, for debugging: what failed, what ran, what it cost. Runs inside the app container
(deploy/inspect.sh calls it). It prints metadata and error messages only: never a posting, a CV, a chat message, a letter or a
prompt, because those belong to the people who sent them. Usage: inspect.py errors | recent | spend | citations | verdicts | catalog"""
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

DB = os.environ.get("MATCH_DB") or os.path.join(os.environ.get("MATCH_DATA", "/data"), "match.db")


def connect():
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def short(text, n=900):
    return " ".join(str(text or "").split())[:n]


def errors(c):
    print("== reports that failed (newest 15)")
    for r in c.execute("SELECT run_id, backend, created, error FROM results WHERE status='failed' ORDER BY id DESC LIMIT 15"):
        print(f"run {r['run_id']} {r['created'][:16]} {r['backend']}: {short(r['error'])}")
    print("== overall readings that failed (newest 15)")
    shown = 0
    for r in c.execute("SELECT run_id, created, overall FROM results WHERE overall LIKE '%\"error\"%' ORDER BY id DESC LIMIT 60"):
        try:
            err = json.loads(r["overall"]).get("error")
        except ValueError:
            continue
        if err and shown < 15:
            shown += 1
            print(f"run {r['run_id']} {r['created'][:16]}: {short(err)}")
    print("== model calls that failed (newest 15)")
    for r in c.execute("SELECT started, name, model, error FROM traces WHERE status != 'ok' ORDER BY started DESC LIMIT 15"):
        print(f"{r['started'][:16]} {r['name']} {r['model']}: {short(r['error'])}")


def recent(c):
    print("== latest 20 reports (no texts)")
    for r in c.execute("SELECT id, created, source, title, company, retention, cv_text != '' AS own_cv FROM runs ORDER BY id DESC LIMIT 20"):
        res = c.execute("SELECT status, seconds FROM results WHERE run_id=? ORDER BY id DESC LIMIT 1", (r["id"],)).fetchone()
        door = "seeker" if r["own_cv"] else "recruiter"
        print(f"run {r['id']} {r['created'][:16]} {r['source']}/{door} {short(r['title'], 60)} | {short(r['company'], 40)} "
              f"{res['status'] if res else '-'} {('%.0fs' % res['seconds']) if res and res['seconds'] else ''}")


def spend(c):
    now = datetime.now(timezone.utc)
    print("== model spend (USD) and checks")
    for label, delta in (("last hour", timedelta(hours=1)), ("last 24 hours", timedelta(days=1)), ("last 30 days", timedelta(days=30))):
        since = (now - delta).strftime("%Y-%m-%dT%H:%M:%S")
        cost = c.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM spend_ledger WHERE started >= ?", (since,)).fetchone()[0]
        n = c.execute("SELECT COUNT(*) FROM check_log WHERE created >= ?", (since,)).fetchone()[0]
        print(f"{label}: {cost:.3f} USD, {n} checks")


def citations(c):
    print("== requirements downgraded to 'not shown' because the cited CV line was not among the lines offered (per report)")
    for r in c.execute("SELECT r.id AS result_id, r.run_id, r.backend, r.created, "
                       "SUM(i.flag = 'invalid_citation') AS bad, COUNT(*) AS n, SUM(i.verdict = 'not_on_record') AS none "
                       "FROM results r JOIN items i ON i.result_id = r.id WHERE r.kind = 'fit' GROUP BY r.id ORDER BY r.id DESC LIMIT 20"):
        print(f"run {r['run_id']} result {r['result_id']} {r['created'][:16]} {r['backend']}: {r['bad']} of {r['n']} downgraded, "
              f"{r['none']} not shown in all")


def verdicts(c):
    print("== verdict counts per report (met / partly / not shown), newest 12")
    for r in c.execute("SELECT r.run_id, r.backend, "
                       "SUM(COALESCE(i.override_verdict, i.verdict) = 'met') AS met, "
                       "SUM(COALESCE(i.override_verdict, i.verdict) = 'partial') AS partial, "
                       "SUM(COALESCE(i.override_verdict, i.verdict) = 'not_on_record') AS none, "
                       "SUM(i.flag = 'invalid_citation') AS dropped, COUNT(*) AS n, r.score "
                       "FROM (SELECT results.*, NULL AS score FROM results) r JOIN items i ON i.result_id = r.id "
                       "WHERE r.kind = 'fit' GROUP BY r.id ORDER BY r.id DESC LIMIT 12"):
        print(f"run {r['run_id']} {r['backend']}: {r['met']} met, {r['partial']} partly, {r['none']} not shown "
              f"({r['dropped']} of those dropped for an unverified line), {r['n']} in all")


def catalog(c):
    print("== model catalog (on/off as set in the admin, price per million tokens in/out in USD)")
    for r in c.execute("SELECT id, enabled, output_mode, price_in, price_out, effort, context FROM catalog WHERE provider != 'baseline' ORDER BY enabled DESC, id"):
        pin = f"{r['price_in'] * 1e6:.2f}" if r["price_in"] is not None else "?"
        pout = f"{r['price_out'] * 1e6:.2f}" if r["price_out"] is not None else "?"
        print(f"{'ON ' if r['enabled'] else 'off'} {r['id']} ({r['output_mode']}) {pin}/{pout} effort={r['effort'] or '-'} context={r['context']}")


COMMANDS = {"catalog": catalog, "errors": errors, "recent": recent, "spend": spend, "citations": citations, "verdicts": verdicts}

if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in COMMANDS:
        sys.exit("usage: inspect.py " + " | ".join(COMMANDS))
    COMMANDS[sys.argv[1]](connect())
