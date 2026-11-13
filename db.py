"""SQLite storage. One short-lived connection per call, so request threads and job threads never share one.

SQLite is enough for one admin and a few readers. It also means one web process (threads, not several
worker processes) unless the job queue moves to its own runner: see docs/DEPLOYMENT.md.
"""
import html
import json
import re
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY, created TEXT NOT NULL, title TEXT NOT NULL DEFAULT '', company TEXT NOT NULL DEFAULT '',
  source_url TEXT NOT NULL DEFAULT '', posting_text TEXT NOT NULL, extra_facts TEXT NOT NULL DEFAULT '',
  notes TEXT NOT NULL DEFAULT '', human_verdict TEXT NOT NULL DEFAULT '',
  share_token TEXT, share_show_cv INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS results (
  id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES runs(id), backend TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued', error TEXT NOT NULL DEFAULT '', created TEXT NOT NULL,
  seconds REAL, reviewed INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY, result_id INTEGER NOT NULL REFERENCES results(id), idx INTEGER NOT NULL,
  text TEXT NOT NULL, kind TEXT NOT NULL, verdict TEXT NOT NULL, evidence TEXT NOT NULL DEFAULT '',
  section TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '', flag TEXT,
  override_verdict TEXT, override_weight INTEGER
);
CREATE TABLE IF NOT EXISTS term_items (
  id INTEGER PRIMARY KEY, result_id INTEGER NOT NULL REFERENCES results(id), idx INTEGER NOT NULL,
  name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', importance TEXT NOT NULL,
  verdict TEXT NOT NULL, quote TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '', flag TEXT,
  override_verdict TEXT
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
-- What the caps read. They hold no report content and no address, so deleting a report (or its expiry) cannot make spend or a
-- visitor's checks disappear from the limits: one row per model call that cost money, one row per check a visitor started.
CREATE TABLE IF NOT EXISTS spend_ledger (trace_id TEXT PRIMARY KEY, started TEXT NOT NULL, cost_usd REAL NOT NULL, public INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS spend_ledger_started ON spend_ledger (started);
CREATE TABLE IF NOT EXISTS check_log (id INTEGER PRIMARY KEY, visitor TEXT NOT NULL, created TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS check_log_visitor ON check_log (visitor, created);
CREATE TABLE IF NOT EXISTS catalog (
  id TEXT PRIMARY KEY, provider TEXT NOT NULL, model_name TEXT NOT NULL, label TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1, local_only INTEGER NOT NULL DEFAULT 0, output_mode TEXT NOT NULL DEFAULT 'tool',
  tools INTEGER NOT NULL DEFAULT 1, context INTEGER, price_in REAL, price_out REAL, source TEXT NOT NULL DEFAULT '',
  fetched TEXT
);
CREATE TABLE IF NOT EXISTS traces (
  trace_id TEXT PRIMARY KEY, name TEXT NOT NULL, started TEXT NOT NULL, duration_ms REAL NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'ok', error TEXT NOT NULL DEFAULT '', subject_type TEXT NOT NULL DEFAULT '',
  subject_id INTEGER, model TEXT NOT NULL DEFAULT '', input_tokens INTEGER NOT NULL DEFAULT 0,
  output_tokens INTEGER NOT NULL DEFAULT 0, cost_usd REAL, model_requests INTEGER NOT NULL DEFAULT 0,
  tool_calls INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS spans (
  span_id TEXT NOT NULL, trace_id TEXT NOT NULL, parent_id TEXT, name TEXT NOT NULL, operation TEXT NOT NULL DEFAULT '',
  start_ns INTEGER NOT NULL, end_ns INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'ok', attrs TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY (trace_id, span_id)
);
CREATE INDEX IF NOT EXISTS spans_trace ON spans(trace_id);
CREATE TABLE IF NOT EXISTS chats (
  id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES runs(id), backend TEXT NOT NULL, created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_messages (
  id INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL REFERENCES chats(id), role TEXT NOT NULL,
  content TEXT NOT NULL DEFAULT '', evidence TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'done',
  error TEXT NOT NULL DEFAULT '', trace_id TEXT, model TEXT NOT NULL DEFAULT '', created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pool_steps (
  id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES runs(id), idx INTEGER NOT NULL,
  label TEXT NOT NULL, requirement_idx INTEGER, query TEXT NOT NULL, url TEXT NOT NULL,
  count INTEGER, count_text TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT '', measured TEXT, note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS letters (
  id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES runs(id), backend TEXT NOT NULL, created TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued', error TEXT NOT NULL DEFAULT '', content TEXT NOT NULL DEFAULT '',
  paragraphs TEXT NOT NULL DEFAULT '[]', checks TEXT NOT NULL DEFAULT '{}', instructions TEXT NOT NULL DEFAULT '',
  trace_id TEXT, edited INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS feedback (
  id INTEGER PRIMARY KEY, created TEXT NOT NULL, item TEXT NOT NULL DEFAULT '', text TEXT NOT NULL,
  contact TEXT NOT NULL DEFAULT '', visitor TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS consent_log (
  id INTEGER PRIMARY KEY, created TEXT NOT NULL, version TEXT NOT NULL, ref TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS roadmap_votes (
  item TEXT NOT NULL, visitor TEXT NOT NULL, created TEXT NOT NULL, PRIMARY KEY (item, visitor)
);
CREATE TABLE IF NOT EXISTS arena_votes (
  id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES runs(id), result_a INTEGER NOT NULL,
  result_b INTEGER NOT NULL, winner TEXT NOT NULL, created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS letter_votes (
  id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL, letter_a INTEGER NOT NULL, letter_b INTEGER NOT NULL,
  variant_a TEXT NOT NULL, variant_b TEXT NOT NULL, winner TEXT NOT NULL, created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS eval_runs (
  id INTEGER PRIMARY KEY, dataset TEXT NOT NULL, dataset_kind TEXT NOT NULL, backend TEXT NOT NULL,
  created TEXT NOT NULL, finished TEXT, status TEXT NOT NULL DEFAULT 'queued', error TEXT NOT NULL DEFAULT '',
  n_cases INTEGER NOT NULL DEFAULT 0, summary TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS eval_cases (
  id INTEGER PRIMARY KEY, eval_run_id INTEGER NOT NULL REFERENCES eval_runs(id), name TEXT NOT NULL,
  passed INTEGER, scores TEXT NOT NULL DEFAULT '{}', assertions TEXT NOT NULL DEFAULT '{}',
  output TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '', seconds REAL, trace_id TEXT
);
"""

# Columns added after the first release of the schema: (table, column, ddl)
COLUMNS = [
    ("runs", "is_demo", "INTEGER NOT NULL DEFAULT 0"),
    ("results", "kind", "TEXT NOT NULL DEFAULT 'fit'"),
    ("results", "trace_id", "TEXT"),
    ("items", "chunk_id", "TEXT"),
    ("eval_runs", "fingerprint", "TEXT NOT NULL DEFAULT '{}'"),   # what the run ran with (evals/fingerprint.py)
    ("eval_runs", "as_of", "TEXT NOT NULL DEFAULT ''"),           # the state of the app a re-run stands for (evals/changes.json)
    ("items", "override_chunk_id", "TEXT"),      # the CV line the owner chose instead of the model's
    ("items", "override_evidence", "TEXT"),
    ("items", "override_section", "TEXT"),
    ("chats", "audience", "TEXT NOT NULL DEFAULT 'owner'"),    # 'owner' (admin) or 'shared' (holder of a share link)
    ("letters", "audience", "TEXT NOT NULL DEFAULT 'owner'"),
    ("runs", "source", "TEXT NOT NULL DEFAULT 'admin'"),     # 'admin' or 'public' (a visitor's check)
    ("runs", "visitor", "TEXT NOT NULL DEFAULT ''"),          # salted hash of the visitor's address, never the address
    ("runs", "share_chat", "INTEGER NOT NULL DEFAULT 0"),     # may the holder of the share link ask follow-up questions
    ("chat_messages", "suggestions", "TEXT NOT NULL DEFAULT '[]'"),
    ("runs", "min_heading", "TEXT NOT NULL DEFAULT ''"),                 # the posting's own heading over its list of requirements
    ("runs", "nice_heading", "TEXT NOT NULL DEFAULT ''"),                # ... and over its list of nice-to-haves
    ("letters", "variant", "TEXT NOT NULL DEFAULT ''"),                   # which opening a comparison draft tests (empty for ordinary letters)
    ("spend_ledger", "door", "TEXT NOT NULL DEFAULT 'recruiter'"),        # 'recruiter' (the owner's CV) or 'seeker' (a visitor's own CV)
    ("check_log", "door", "TEXT NOT NULL DEFAULT 'recruiter'"),
    ("chat_messages", "exchanges", "TEXT NOT NULL DEFAULT '[]'"),        # messages between the chat bot and the match engine
    ("chat_messages", "mode", "TEXT NOT NULL DEFAULT 'normal'"),   # 'normal' or 'skeptic'
    ("runs", "share_letter", "INTEGER NOT NULL DEFAULT 0"),   # may the holder of the share link draft a cover letter
    ("runs", "expires", "TEXT"),                              # public runs are deleted after this date
    ("traces", "billable", "INTEGER NOT NULL DEFAULT 0"),      # 1 when the call costs real money (not a plan or a local model)
    ("traces", "public", "INTEGER NOT NULL DEFAULT 0"),       # spend by visitors counts against the daily budget
    ("results", "overall", "TEXT NOT NULL DEFAULT ''"),       # JSON: the overall reading, or {"error": ...}; empty when none was made
    ("runs", "candidate_name", "TEXT NOT NULL DEFAULT ''"),    # whose CV an uploaded report is about; empty for the owner's
    ("runs", "cv_text", "TEXT NOT NULL DEFAULT ''"),           # the uploaded CV (contact details removed); empty for the owner's
    ("runs", "retention", "TEXT NOT NULL DEFAULT ''"),         # 'session' (deleted 2 hours after the last action) or 'days'
    ("runs", "consent_at", "TEXT"),                            # when the uploader ticked the consent box
    ("runs", "consent_version", "TEXT NOT NULL DEFAULT ''"),
    ("catalog", "effort", "TEXT NOT NULL DEFAULT ''"),         # reasoning effort asked of the model: low, medium, high; empty = model default
]


def plain(value):
    """A title or company as plain text: HTML codes such as &amp; and &#8212; are decoded (career sites leave them in)."""
    return html.unescape(value or "").strip() if isinstance(value, str) else value


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DB:
    def __init__(self, path):
        self.path = path
        with self.conn() as c:
            c.executescript(SCHEMA)
            for table, col, ddl in COLUMNS:
                have = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
                if col not in have:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
            # Explanations stored with an internal annotation (a cited line number and the word "downgraded") are made plain, once.
            import match
            c.execute("UPDATE items SET reason=? WHERE reason LIKE '[model cited no evidence%'", (match.UNVERIFIED_REASON,))
            for row in c.execute("SELECT id, reason FROM items WHERE reason GLOB '*[cf][0-9]*'").fetchall():
                ids = set(re.findall(r"\b[cf]\d+\b", row["reason"]))      # CV line numbers written into a sentence by an older version
                clean = match.strip_ids(row["reason"], ids)
                if clean != row["reason"]:
                    c.execute("UPDATE items SET reason=? WHERE id=?", (clean, row["id"]))
            for row in c.execute("SELECT id, title, company FROM runs WHERE title LIKE '%&%' OR company LIKE '%&%'").fetchall():
                title, company = plain(row["title"]), plain(row["company"])      # titles stored with HTML codes by an older version
                if (title, company) != (row["title"], row["company"]):
                    c.execute("UPDATE runs SET title=?, company=? WHERE id=?", (title, company, row["id"]))
            # Spend recorded before the ledger existed (a database from an earlier version) is carried over once.
            c.execute("INSERT OR IGNORE INTO spend_ledger (trace_id, started, cost_usd, public) "
                      "SELECT trace_id, started, cost_usd, public FROM traces WHERE billable=1 AND cost_usd IS NOT NULL")

    @contextmanager
    def conn(self):
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")
        c.execute("PRAGMA journal_mode = WAL")
        c.execute("PRAGMA secure_delete = ON")  # deleted rows (a CV, a trace) are overwritten, not just unlinked
        try:
            with c:
                yield c
        finally:
            c.close()

    def one(self, sql, args=()):
        with self.conn() as c:
            row = c.execute(sql, args).fetchone()
        return dict(row) if row else None

    def all(self, sql, args=()):
        with self.conn() as c:
            return [dict(r) for r in c.execute(sql, args).fetchall()]

    def run(self, sql, args=()):
        with self.conn() as c:
            return c.execute(sql, args).lastrowid

    def run_count(self, sql, args=()):
        """Execute a write and return how many rows it changed."""
        with self.conn() as c:
            return c.execute(sql, args).rowcount

    # --- runs ---------------------------------------------------------------------------
    def create_run(self, title, company, source_url, posting_text, extra_facts):
        return self.run(
            "INSERT INTO runs (created, title, company, source_url, posting_text, extra_facts) VALUES (?,?,?,?,?,?)",
            (now(), plain(title), plain(company), source_url, posting_text, extra_facts))

    def get_run(self, run_id):
        return self.one("SELECT * FROM runs WHERE id = ?", (run_id,))

    def list_runs(self):
        return self.all("SELECT * FROM runs ORDER BY id DESC")

    def update_meta(self, run_id, title, company, notes, human_verdict):
        self.run("UPDATE runs SET title=?, company=?, notes=?, human_verdict=? WHERE id=?",
                 (title, company, notes, human_verdict, run_id))

    def set_share(self, run_id, enabled, show_cv=False):
        run = self.get_run(run_id)
        token = (run["share_token"] or secrets.token_urlsafe(16)) if enabled else None
        self.run("UPDATE runs SET share_token=?, share_show_cv=? WHERE id=?", (token, int(show_cv), run_id))

    def run_by_token(self, token):
        """The run behind a share link, or None. A run past its expiry date is treated as gone even before purge."""
        return self.one("SELECT * FROM runs WHERE share_token = ? AND (expires IS NULL OR expires > ?)", (token, now()))

    def set_demo(self, run_id, is_demo):
        with self.conn() as c:
            if is_demo:
                c.execute("UPDATE runs SET is_demo = 0")
            c.execute("UPDATE runs SET is_demo = ? WHERE id = ?", (int(is_demo), run_id))

    def demo_run(self):
        return self.one("SELECT * FROM runs WHERE is_demo = 1")

    def fill_run_title(self, run_id, title, company):
        """Give a run the role title and company found in its posting, but only where they are blank: what the visitor typed
        (or an earlier result found) is never overwritten."""
        self.run("UPDATE runs SET title = COALESCE(NULLIF(title, ''), ?), company = COALESCE(NULLIF(company, ''), ?) WHERE id=?",
                 ((plain(title) or "")[:120], (plain(company) or "")[:120], run_id))

    def fill_run_headings(self, run_id, headings):
        """The posting's own headings over its two lists, kept where blank (an earlier result's headings stay)."""
        self.run("UPDATE runs SET min_heading = COALESCE(NULLIF(min_heading, ''), ?), nice_heading = COALESCE(NULLIF(nice_heading, ''), ?) WHERE id=?",
                 ((plain(headings.get("minimum")) or "")[:100], (plain(headings.get("desirable")) or "")[:100], run_id))

    def create_public_run(self, title, company, posting_text, visitor, expires):
        token = secrets.token_urlsafe(16)
        run_id = self.run(
            "INSERT INTO runs (created, title, company, posting_text, source, visitor, share_token, share_show_cv, "
            "share_chat, share_letter, expires) VALUES (?,?,?,?, 'public', ?, ?, 1, 1, 1, ?)",
            (now(), plain(title), plain(company), posting_text, visitor, token, expires))
        self.run("INSERT INTO check_log (visitor, created, door) VALUES (?, ?, 'recruiter')", (visitor, now()))
        return run_id, token

    def create_upload_run(self, title, company, posting_text, visitor, expires, *, candidate_name, cv_text, retention,
                          consent_version):
        """A report on a CV the visitor uploaded. It is a public run (same caps, same expiry and delete rules) that
        carries its own CV and the record of the consent."""
        token = secrets.token_urlsafe(16)
        run_id = self.run(
            "INSERT INTO runs (created, title, company, posting_text, source, visitor, share_token, share_show_cv, "
            "share_chat, share_letter, expires, candidate_name, cv_text, retention, consent_at, consent_version) "
            "VALUES (?,?,?,?, 'public', ?, ?, 1, 1, 1, ?, ?, ?, ?, ?, ?)",
            (now(), plain(title), plain(company), posting_text, visitor, token, expires, candidate_name, cv_text, retention, now(),
             consent_version))
        self.run("INSERT INTO check_log (visitor, created, door) VALUES (?, ?, 'seeker')", (visitor, now()))
        self.log_consent(consent_version, token)
        return run_id, token

    def log_consent(self, version, token):
        """Proof that consent was given, kept after the report is deleted: the time, the wording version and a hash of
        the report link, never the CV or the visitor."""
        import hashlib
        self.run("INSERT INTO consent_log (created, version, ref) VALUES (?,?,?)",
                 (now(), version, hashlib.sha256(token.encode()).hexdigest()[:16]))

    def purge_old_records(self, before_iso):
        """Wishes and consent records older than the cut-off: 12 months."""
        a = self.run_count("DELETE FROM feedback WHERE created < ?", (before_iso,))
        b = self.run_count("DELETE FROM consent_log WHERE created < ?", (before_iso,))
        c = self.run_count("DELETE FROM spend_ledger WHERE started < ?", (before_iso,))   # amounts and times only
        two_days_ago = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(timespec="seconds")
        d = self.run_count("DELETE FROM check_log WHERE created < ?", (two_days_ago,))    # the limit looks back one day
        return a + b + c + d

    def checkpoint(self):
        with self.conn() as c:
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def set_expiry(self, run_id, expires):
        self.run("UPDATE runs SET expires=? WHERE id=?", (expires, run_id))

    def count_public_runs_since(self, visitor, since_iso, door=None):
        """Checks this visitor started since a moment (through one door, or both), counted from a log that survives
        deleting the report."""
        return self.one("SELECT COUNT(*) n FROM check_log WHERE visitor=? AND created>=?" + (" AND door=?" if door else ""),
                        (visitor, since_iso, door) if door else (visitor, since_iso))["n"]

    def count_checks_since(self, since_iso):
        """Checks anyone started since a moment (from the log that survives deleting a report)."""
        return self.one("SELECT COUNT(*) n FROM check_log WHERE created>=?", (since_iso,))["n"]

    def count_user_messages(self, chat_id):
        return self.one("SELECT COUNT(*) n FROM chat_messages WHERE chat_id=? AND role='user'", (chat_id,))["n"]

    def billable_spend_since(self, since_iso):
        """Real money spent on model calls since a moment (plan usage and local models are not counted)."""
        return self.one("SELECT COALESCE(SUM(cost_usd), 0) usd FROM spend_ledger WHERE started>=?", (since_iso,))["usd"]

    def run_spend(self, run_id, shared_only=False):
        """What one report has cost so far: its fit results, letters and chat answers. With shared_only, only the
        calls made for the holder of a share link or a visitor (the traces marked public)."""
        row = self.one(
            "SELECT COALESCE(SUM(t.cost_usd), 0) usd FROM traces t WHERE t.billable=1" + (" AND t.public=1" if shared_only else "")
            + " AND t.trace_id IN (SELECT trace_id FROM results WHERE run_id=?1 AND trace_id IS NOT NULL"
              " UNION SELECT trace_id FROM letters WHERE run_id=?1 AND trace_id IS NOT NULL"
              " UNION SELECT m.trace_id FROM chat_messages m JOIN chats c ON c.id=m.chat_id"
              "  WHERE c.run_id=?1 AND m.trace_id IS NOT NULL)", (run_id,))
        return row["usd"]

    def public_spend_since(self, since_iso, door=None):
        return self.one("SELECT COALESCE(SUM(cost_usd), 0) usd FROM spend_ledger WHERE public=1 AND started>=?"
                        + (" AND door=?" if door else ""), (since_iso, door) if door else (since_iso,))["usd"]

    def delete_run(self, run_id):
        """Remove a run and everything derived from it, including the traces that hold its text."""
        with self.conn() as c:
            rids = [r["id"] for r in c.execute("SELECT id FROM results WHERE run_id=?", (run_id,))]
            chat_ids = [r["id"] for r in c.execute("SELECT id FROM chats WHERE run_id=?", (run_id,))]
            trace_ids = [r["trace_id"] for r in c.execute(
                "SELECT trace_id FROM results WHERE run_id=? AND trace_id IS NOT NULL", (run_id,))]
            for cid in chat_ids:
                trace_ids += [r["trace_id"] for r in c.execute(
                    "SELECT trace_id FROM chat_messages WHERE chat_id=? AND trace_id IS NOT NULL", (cid,))]
                c.execute("DELETE FROM chat_messages WHERE chat_id=?", (cid,))
            c.execute("DELETE FROM chats WHERE run_id=?", (run_id,))
            trace_ids += [r["trace_id"] for r in c.execute(
                "SELECT trace_id FROM letters WHERE run_id=? AND trace_id IS NOT NULL", (run_id,))]
            c.execute("DELETE FROM letters WHERE run_id=?", (run_id,))
            c.execute("DELETE FROM pool_steps WHERE run_id=?", (run_id,))
            c.execute("DELETE FROM arena_votes WHERE run_id=?", (run_id,))
            for rid in rids:
                c.execute("DELETE FROM items WHERE result_id=?", (rid,))
                c.execute("DELETE FROM term_items WHERE result_id=?", (rid,))
            c.execute("DELETE FROM results WHERE run_id=?", (run_id,))
            for tid in trace_ids:
                c.execute("DELETE FROM spans WHERE trace_id=?", (tid,))
                c.execute("DELETE FROM traces WHERE trace_id=?", (tid,))
            c.execute("DELETE FROM runs WHERE id=?", (run_id,))
            self._purge_orphan_traces(c)   # also traces of calls that failed or were still running when this was deleted

    @staticmethod
    def _purge_orphan_traces(c):
        """Traces whose subject (a result, letter, chat answer or run) no longer exists, with their spans. A job that
        ends after its report was deleted leaves one of these; it holds the posting and the CV text."""
        tables = {"result": "results", "letter": "letters", "chat_message": "chat_messages", "run": "runs"}
        for subject, table in tables.items():
            ids = [r["trace_id"] for r in c.execute(
                f"SELECT trace_id FROM traces WHERE subject_type=? AND subject_id IS NOT NULL "
                f"AND subject_id NOT IN (SELECT id FROM {table})", (subject,))]
            for tid in ids:
                c.execute("DELETE FROM spans WHERE trace_id=?", (tid,))
                c.execute("DELETE FROM traces WHERE trace_id=?", (tid,))

    def purge_orphan_traces(self):
        with self.conn() as c:
            self._purge_orphan_traces(c)

    def expired_run_ids(self, now_iso):
        return [r["id"] for r in self.all("SELECT id FROM runs WHERE expires IS NOT NULL AND expires < ?", (now_iso,))]

    # --- results (kind 'fit': candidate against the posting; 'terms': the posting against the candidate's terms)
    def add_result(self, run_id, backend, kind="fit"):
        return self.run("INSERT INTO results (run_id, backend, kind, created) VALUES (?,?,?,?)",
                        (run_id, backend, kind, now()))

    def get_result(self, result_id):
        return self.one("SELECT * FROM results WHERE id = ?", (result_id,))

    def results_for(self, run_id, kind=None):
        if kind:
            return self.all("SELECT * FROM results WHERE run_id = ? AND kind = ? ORDER BY id", (run_id, kind))
        return self.all("SELECT * FROM results WHERE run_id = ? ORDER BY id", (run_id,))

    def all_results(self, kind=None):
        if kind:
            return self.all("SELECT * FROM results WHERE kind = ? ORDER BY id", (kind,))
        return self.all("SELECT * FROM results ORDER BY id")

    def set_status(self, result_id, status, error="", seconds=None):
        self.run("UPDATE results SET status=?, error=?, seconds=COALESCE(?, seconds) WHERE id=?",
                 (status, error, seconds, result_id))

    def set_overall(self, result_id, data):
        self.run("UPDATE results SET overall=? WHERE id=?", (json.dumps(data), result_id))

    def set_result_trace(self, result_id, trace_id):
        self.run("UPDATE results SET trace_id=? WHERE id=?", (trace_id, result_id))

    def set_reviewed(self, result_id, reviewed):
        self.run("UPDATE results SET reviewed=? WHERE id=?", (int(reviewed), result_id))

    # --- items --------------------------------------------------------------------------
    def save_items(self, result_id, items):
        with self.conn() as c:
            c.executemany(
                "INSERT INTO items (result_id, idx, text, kind, verdict, evidence, section, reason, flag, chunk_id) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                [(result_id, i["idx"], i["text"], i["kind"], i["verdict"], i["evidence"], i["section"],
                  i["reason"], i["flag"], i.get("chunk_id")) for i in items])

    def items_for(self, result_id, as_model=False):
        """The report's items. Where the owner picked another CV line, that line is the one shown and used (chunk_id,
        evidence, section); the model's own choice stays in model_*. as_model=True returns the model's choice untouched."""
        rows = self.all("SELECT * FROM items WHERE result_id = ? ORDER BY idx", (result_id,))
        for it in rows:
            it["model_chunk_id"], it["model_evidence"], it["model_section"] = it["chunk_id"], it["evidence"], it["section"]
            it["line_overridden"] = bool(it.get("override_chunk_id")) and not as_model
            if it["line_overridden"]:
                it["chunk_id"], it["evidence"], it["section"] = it["override_chunk_id"], it["override_evidence"] or "", it["override_section"] or ""
                it["flag"] = None
        return rows

    def get_item(self, item_id):
        return self.one("SELECT * FROM items WHERE id = ?", (item_id,))

    def set_override(self, item_id, verdict, weight, chunk=False):
        """chunk: False leaves the line alone, None goes back to the model's line, a dict {id, section, text} picks that line."""
        self.run("UPDATE items SET override_verdict=?, override_weight=? WHERE id=?", (verdict, weight, item_id))
        if chunk is not False:
            self.run("UPDATE items SET override_chunk_id=?, override_evidence=?, override_section=? WHERE id=?",
                     (chunk["id"], chunk["text"], chunk["section"], item_id) if chunk else (None, None, None, item_id))

    # --- term items ---------------------------------------------------------------------
    def save_term_items(self, result_id, items):
        with self.conn() as c:
            c.executemany(
                "INSERT INTO term_items (result_id, idx, name, description, importance, verdict, quote, reason, flag) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                [(result_id, i["idx"], i["name"], i["description"], i["importance"], i["verdict"], i["quote"],
                  i["reason"], i["flag"]) for i in items])

    def term_items_for(self, result_id):
        return self.all("SELECT * FROM term_items WHERE result_id = ? ORDER BY idx", (result_id,))

    def get_term_item(self, item_id):
        return self.one("SELECT * FROM term_items WHERE id = ?", (item_id,))

    def set_term_override(self, item_id, verdict):
        self.run("UPDATE term_items SET override_verdict=? WHERE id=?", (verdict, item_id))

    # --- settings -----------------------------------------------------------------------
    def get_setting(self, key, default=""):
        row = self.one("SELECT value FROM settings WHERE key = ?", (key,))
        return row["value"] if row else default

    def set_setting(self, key, value):
        self.run("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, value))

    def get_json(self, key, default=None):
        raw = self.get_setting(key, "")
        return json.loads(raw) if raw else default

    def set_json(self, key, value):
        self.set_setting(key, json.dumps(value))

    # --- model catalog ------------------------------------------------------------------
    def upsert_catalog(self, rows):
        """Insert new models; refresh facts about known ones but keep the `enabled` choice."""
        with self.conn() as c:
            for r in rows:
                c.execute(
                    "INSERT INTO catalog (id, provider, model_name, label, enabled, local_only, output_mode, tools, "
                    "context, price_in, price_out, source, fetched, effort) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET label=excluded.label, context=COALESCE(excluded.context, catalog.context), "
                    "tools=excluded.tools, price_in=COALESCE(excluded.price_in, catalog.price_in), "
                    "price_out=COALESCE(excluded.price_out, catalog.price_out), source=excluded.source, "
                    "fetched=excluded.fetched",
                    (r["id"], r["provider"], r["model_name"], r["label"], int(r.get("enabled", 1)),
                     int(r.get("local_only", 0)), r.get("output_mode", "tool"), int(r.get("tools", 1)),
                     r.get("context"), r.get("price_in"), r.get("price_out"), r.get("source", ""), now(), r.get("effort", "")))

    def list_catalog(self):
        return self.all("SELECT * FROM catalog ORDER BY provider, label")

    def get_catalog(self, model_id):
        return self.one("SELECT * FROM catalog WHERE id = ?", (model_id,))

    # --- roadmap wishes and votes ---------------------------------------------------------
    def add_feedback(self, item, text, contact, visitor):
        return self.run("INSERT INTO feedback (created, item, text, contact, visitor) VALUES (?,?,?,?,?)",
                        (now(), item, text, contact, visitor))

    def feedback(self):
        return self.all("SELECT id, created, item, text, contact FROM feedback ORDER BY id DESC")

    def delete_feedback(self, feedback_id):
        self.run("DELETE FROM feedback WHERE id=?", (feedback_id,))

    def count_feedback_since(self, visitor, since_iso):
        return self.one("SELECT COUNT(*) n FROM feedback WHERE visitor=? AND created>=?", (visitor, since_iso))["n"]

    def add_roadmap_vote(self, item, visitor):
        """One vote per visitor per item. Returns False when this visitor had already voted."""
        return bool(self.run_count("INSERT OR IGNORE INTO roadmap_votes (item, visitor, created) VALUES (?,?,?)",
                                   (item, visitor, now())))

    def roadmap_vote_counts(self):
        return {r["item"]: r["n"] for r in self.all("SELECT item, COUNT(*) n FROM roadmap_votes GROUP BY item")}

    def roadmap_votes_of(self, visitor):
        return {r["item"] for r in self.all("SELECT item FROM roadmap_votes WHERE visitor=?", (visitor,))}

    def set_catalog_effort(self, model_id, effort):
        self.run("UPDATE catalog SET effort=? WHERE id=?", (effort, model_id))

    def set_catalog_enabled(self, model_id, enabled):
        self.run("UPDATE catalog SET enabled=? WHERE id=?", (int(enabled), model_id))

    # --- traces -------------------------------------------------------------------------
    def insert_spans(self, rows):
        with self.conn() as c:
            c.executemany(
                "INSERT OR REPLACE INTO spans (span_id, trace_id, parent_id, name, operation, start_ns, end_ns, status, attrs) "
                "VALUES (?,?,?,?,?,?,?,?,?)", rows)

    def spans_for(self, trace_id):
        rows = self.all("SELECT * FROM spans WHERE trace_id = ? ORDER BY start_ns, end_ns DESC", (trace_id,))
        for r in rows:
            r["attrs"] = json.loads(r["attrs"])
        return rows

    def upsert_trace(self, t):
        self.run(
            "INSERT INTO traces (trace_id, name, started, duration_ms, status, error, subject_type, subject_id, model, "
            "input_tokens, output_tokens, cost_usd, model_requests, tool_calls, public, billable) VALUES "
            "(:trace_id,:name,:started,:duration_ms,:status,:error,:subject_type,:subject_id,:model,:input_tokens,"
            ":output_tokens,:cost_usd,:model_requests,:tool_calls,:public,:billable) "
            "ON CONFLICT(trace_id) DO UPDATE SET name=excluded.name, started=excluded.started, "
            "duration_ms=excluded.duration_ms, status=excluded.status, error=excluded.error, "
            "subject_type=excluded.subject_type, subject_id=excluded.subject_id, model=excluded.model, "
            "input_tokens=excluded.input_tokens, output_tokens=excluded.output_tokens, cost_usd=excluded.cost_usd, "
            "model_requests=excluded.model_requests, tool_calls=excluded.tool_calls, public=excluded.public, "
            "billable=excluded.billable", t)
        if t.get("billable") and t.get("cost_usd") is not None:
            door = self.door_of_subject(t.get("subject_type"), t.get("subject_id"))
            self.run("INSERT INTO spend_ledger (trace_id, started, cost_usd, public, door) VALUES (?,?,?,?,?) "
                     "ON CONFLICT(trace_id) DO UPDATE SET started=excluded.started, cost_usd=excluded.cost_usd, "
                     "public=excluded.public, door=excluded.door",
                     (t["trace_id"], t["started"], t["cost_usd"], int(t.get("public") or 0), door))

    def door_of_subject(self, subject_type, subject_id):
        """Which door a traced job belongs to: 'seeker' when its run was made with a visitor's own CV, else 'recruiter'."""
        sql = {"result": "SELECT r.retention FROM runs r JOIN results x ON x.run_id=r.id WHERE x.id=?",
               "letter": "SELECT r.retention FROM runs r JOIN letters x ON x.run_id=r.id WHERE x.id=?",
               "chat_message": "SELECT r.retention FROM runs r JOIN chats c ON c.run_id=r.id JOIN chat_messages m ON m.chat_id=c.id WHERE m.id=?",
               "run": "SELECT retention FROM runs WHERE id=?"}.get(subject_type)
        row = self.one(sql, (subject_id,)) if sql and subject_id is not None else None
        return "seeker" if row and row["retention"] else "recruiter"

    def get_trace(self, trace_id):
        return self.one("SELECT * FROM traces WHERE trace_id = ?", (trace_id,))

    def list_traces(self, limit=200, subject_type=None, model=None, status=None):
        sql, args = "SELECT * FROM traces WHERE 1=1", []
        for col, val in (("subject_type", subject_type), ("model", model), ("status", status)):
            if val:
                sql += f" AND {col} = ?"
                args.append(val)
        return self.all(sql + " ORDER BY started DESC LIMIT ?", (*args, limit))

    # --- chat ---------------------------------------------------------------------------
    def get_or_create_chat(self, run_id, backend, audience="owner"):
        row = self.one("SELECT * FROM chats WHERE run_id = ? AND audience = ? ORDER BY id DESC LIMIT 1",
                       (run_id, audience))
        if row:
            if row["backend"] != backend:
                self.run("UPDATE chats SET backend=? WHERE id=?", (backend, row["id"]))
                row["backend"] = backend
            return row
        cid = self.run("INSERT INTO chats (run_id, backend, created, audience) VALUES (?,?,?,?)",
                       (run_id, backend, now(), audience))
        return self.one("SELECT * FROM chats WHERE id = ?", (cid,))

    def chat_for_run(self, run_id, audience="owner"):
        return self.one("SELECT * FROM chats WHERE run_id = ? AND audience = ? ORDER BY id DESC LIMIT 1",
                        (run_id, audience))

    def add_message(self, chat_id, role, content="", status="done", model="", mode="normal"):
        return self.run(
            "INSERT INTO chat_messages (chat_id, role, content, status, model, mode, created) VALUES (?,?,?,?,?,?,?)",
            (chat_id, role, content, status, model, mode, now()))

    def update_message(self, message_id, **fields):
        allowed = {"content", "evidence", "suggestions", "exchanges", "status", "error", "trace_id", "model"}
        assert set(fields) <= allowed, set(fields) - allowed
        sets = ", ".join(f"{k}=?" for k in fields)
        self.run(f"UPDATE chat_messages SET {sets} WHERE id=?",
                 (*[json.dumps(v) if k in ("evidence", "suggestions", "exchanges") else v for k, v in fields.items()], message_id))

    def get_message(self, message_id):
        m = self.one("SELECT * FROM chat_messages WHERE id = ?", (message_id,))
        if m:
            m["evidence"] = json.loads(m["evidence"])
            m["suggestions"] = json.loads(m["suggestions"])
            m["exchanges"] = json.loads(m["exchanges"])
        return m

    def messages_for(self, chat_id, after_id=0):
        rows = self.all("SELECT * FROM chat_messages WHERE chat_id = ? AND id > ? ORDER BY id", (chat_id, after_id))
        for r in rows:
            r["evidence"] = json.loads(r["evidence"])
            r["suggestions"] = json.loads(r["suggestions"])
            r["exchanges"] = json.loads(r["exchanges"])
        return rows

    # --- talent pool funnel (how many profiles contain the terms; counts are entered, never invented) ---------
    def save_pool_steps(self, run_id, steps):
        with self.conn() as c:
            c.execute("DELETE FROM pool_steps WHERE run_id=?", (run_id,))
            c.executemany(
                "INSERT INTO pool_steps (run_id, idx, label, requirement_idx, query, url) VALUES (?,?,?,?,?,?)",
                [(run_id, i, st["label"], st.get("requirement_idx"), st["query"], st["url"]) for i, st in enumerate(steps)])

    def pool_for(self, run_id):
        return self.all("SELECT * FROM pool_steps WHERE run_id = ? ORDER BY idx", (run_id,))

    def set_pool_count(self, step_id, count, count_text, source, note=""):
        self.run("UPDATE pool_steps SET count=?, count_text=?, source=?, measured=?, note=? WHERE id=?",
                 (count, count_text, source, now(), note, step_id))

    # --- cover letters (admin drafts: grounded in cited CV lines, never sent) ---------------------------------
    def create_letter(self, run_id, backend, instructions="", audience="owner", variant=""):
        return self.run("INSERT INTO letters (run_id, backend, created, instructions, audience, variant) VALUES (?,?,?,?,?,?)",
                        (run_id, backend, now(), instructions, audience, variant))

    # --- letter arena: blind votes on which of two openings reads better (the votes keep the openings, so they outlive the run) ---
    def add_letter_vote(self, run_id, letter_a, letter_b, variant_a, variant_b, winner):
        assert winner in ("a", "b", "tie")
        return self.run("INSERT INTO letter_votes (run_id, letter_a, letter_b, variant_a, variant_b, winner, created) "
                        "VALUES (?,?,?,?,?,?,?)", (run_id, letter_a, letter_b, variant_a, variant_b, winner, now()))

    def letter_votes(self):
        return self.all("SELECT * FROM letter_votes ORDER BY id")

    def letter_votes_for(self, run_id):
        return self.all("SELECT * FROM letter_votes WHERE run_id = ? ORDER BY id", (run_id,))

    def update_letter(self, letter_id, **fields):
        allowed = {"status", "error", "content", "paragraphs", "checks", "trace_id", "edited"}
        assert set(fields) <= allowed, set(fields) - allowed
        sets = ", ".join(f"{k}=?" for k in fields)
        vals = [json.dumps(v) if k in ("paragraphs", "checks") else v for k, v in fields.items()]
        self.run(f"UPDATE letters SET {sets} WHERE id=?", (*vals, letter_id))

    def _letter(self, row):
        if row:
            row["paragraphs"] = json.loads(row["paragraphs"])
            row["checks"] = json.loads(row["checks"])
        return row

    def get_letter(self, letter_id):
        return self._letter(self.one("SELECT * FROM letters WHERE id = ?", (letter_id,)))

    def letters_for(self, run_id, audience=None):
        """All letters of a run, or only those written for one audience ('owner' or 'shared')."""
        if audience:
            rows = self.all("SELECT * FROM letters WHERE run_id = ? AND audience = ? ORDER BY id DESC", (run_id, audience))
        else:
            rows = self.all("SELECT * FROM letters WHERE run_id = ? ORDER BY id DESC", (run_id,))
        return [self._letter(r) for r in rows]

    # --- arena: blind votes on which of two reports of the same posting is better ---------------------------
    def add_vote(self, run_id, result_a, result_b, winner):
        assert winner in ("a", "b", "tie")
        return self.run("INSERT INTO arena_votes (run_id, result_a, result_b, winner, created) VALUES (?,?,?,?,?)",
                        (run_id, result_a, result_b, winner, now()))

    def votes(self):
        return self.all("SELECT * FROM arena_votes ORDER BY id")

    def votes_for(self, run_id):
        return self.all("SELECT * FROM arena_votes WHERE run_id = ? ORDER BY id", (run_id,))

    # --- evals --------------------------------------------------------------------------
    def create_eval_run(self, dataset, dataset_kind, backend, as_of=""):
        return self.run("INSERT INTO eval_runs (dataset, dataset_kind, backend, created, as_of) VALUES (?,?,?,?,?)",
                        (dataset, dataset_kind, backend, now(), as_of))

    def update_eval_run(self, eval_run_id, **fields):
        allowed = {"status", "error", "n_cases", "summary", "finished", "fingerprint"}
        assert set(fields) <= allowed, set(fields) - allowed
        sets = ", ".join(f"{k}=?" for k in fields)
        self.run(f"UPDATE eval_runs SET {sets} WHERE id=?",
                 (*[json.dumps(v) if k in ("summary", "fingerprint") else v for k, v in fields.items()], eval_run_id))

    def add_eval_case(self, eval_run_id, name, passed, scores, assertions, output, error="", seconds=None, trace_id=None):
        return self.run(
            "INSERT INTO eval_cases (eval_run_id, name, passed, scores, assertions, output, error, seconds, trace_id) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (eval_run_id, name, None if passed is None else int(passed), json.dumps(scores), json.dumps(assertions),
             json.dumps(output), error, seconds, trace_id))

    def eval_runs(self):
        rows = self.all("SELECT * FROM eval_runs ORDER BY id DESC")
        for r in rows:
            r["summary"] = json.loads(r["summary"])
            r["fingerprint"] = json.loads(r["fingerprint"] or "{}")
        return rows

    def get_eval_run(self, eval_run_id):
        r = self.one("SELECT * FROM eval_runs WHERE id = ?", (eval_run_id,))
        if r:
            r["summary"] = json.loads(r["summary"])
            r["fingerprint"] = json.loads(r["fingerprint"] or "{}")
        return r

    def eval_cases(self, eval_run_id):
        rows = self.all("SELECT * FROM eval_cases WHERE eval_run_id = ? ORDER BY id", (eval_run_id,))
        for r in rows:
            for k in ("scores", "assertions", "output"):
                r[k] = json.loads(r[k])
        return rows
