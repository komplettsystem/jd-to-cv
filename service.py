"""The work the app does: fit reports, terms checks, pool plans, cover letters, chat answers, public checks.
Every unit of work runs inside a trace and records its own failure on its row, so the admin can see it."""
import hashlib
import json
import threading
from contextlib import contextmanager
import itertools
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cvfile
from config import network_key
import letters as letters_mod
import match
from llm import catalog
from llm.agents import Agents
from llm.baseline import BaselineBackend
from llm.chat import MAX_QUESTION, ReportChat
from llm.models import build_model

# Spend caps (USD) are hard stops, checked before every model-backed job:
#   session_budget_usd: one report with its chat and letters, for a visitor or the holder of a share link
#   daily_budget_usd:   all visitors together, per day
#   monthly_budget_usd: everything that costs real money, yours included, per calendar month
# A single call can overshoot a cap by its own cost, which is cents on the shortlisted models.
# An uploaded CV is kept for this long after the last action, or for retention_days, as the uploader chooses.
SESSION_HOURS = 2
CONSENT_VERSION = "2026-11-12"

DEFAULT_LIMITS = {"checks_per_day": 3, "messages_per_chat": 20, "letters_per_report": 2,
                  "session_budget_usd": 0.15, "daily_budget_usd": 2.0, "monthly_budget_usd": 20.0,
                  "posting_max_chars": 15000, "cv_max_chars": 25000, "retention_days": 30,
                  "job_reserve_usd": 0.05, "hourly_budget_usd": 0.5,
                  "auto_letter": 1, "seeker_budget_share": 0.4, "checks_per_day_seeker": 3, "checks_per_visitor_per_minute": 2, "checks_per_minute_all": 6, "checks_per_hour_all": 60, "public_price_ceiling_in": 1.0, "public_price_ceiling_out": 5.0}  # held against the caps while a job runs, so parallel jobs cannot all slip under them

# Examples only. The owner edits these under "Your terms" in the admin; they are not anyone's real terms.
DEFAULT_TERMS = [
    {"name": "Location", "description": "The role can be done remotely or from a city the person is willing to live in",
     "importance": "dealbreaker"},
    {"name": "Seniority", "description": "The role matches the person's level: it is not junior or entry level",
     "importance": "important"},
    {"name": "Pay", "description": "The posting states a pay range, and it is at or above the person's floor",
     "importance": "important"},
    {"name": "Domain", "description": "The product area is one the person wants to work in", "importance": "nice"},
]

LETTER_LANGUAGES = ("English", "German")
LETTER_LENGTHS = {"short": "Under 180 words.", "standard": "Under 300 words."}


class LimitError(ValueError):
    """A limit the visitor hit. The message is written for them."""


class PostingError(ValueError):
    """The posting could not be used (no link, a page that cannot be read, a bad file, too little text). The form then offers
    the next way to give it: a PDF first, pasted text last."""


def visitor_hash(secret, address):
    key = secret if isinstance(secret, bytes) else str(secret).encode()
    return hashlib.sha256(key + b"|" + network_key(address or "").encode()).hexdigest()[:24]


def _utc(dt=None):
    return (dt or datetime.now(timezone.utc)).isoformat(timespec="seconds")


class Service:
    def __init__(self, db, settings, tracing, model_factory=None):
        self.db, self.settings, self.tracing = db, settings, tracing
        self.model_factory = model_factory or (lambda entry: build_model(entry, settings))  # tests inject scripted models
        self._reserve_lock = threading.Lock()
        self._reserved = {}                                   # token -> (run_id, shared, usd, door) for jobs that are running
        self._tokens = itertools.count(1)
        self._slots = threading.BoundedSemaphore(self.MAX_PUBLIC_JOBS)

    # --- plumbing -------------------------------------------------------------------------
    MAX_PUBLIC_JOBS = 4      # visitor jobs that call a model at the same moment; others wait their turn
    MAX_THREADS = 150        # refuse new visitor work when the process is already this busy

    def submit(self, fn, *args):
        if self.settings.sync_jobs:
            fn(*args)
        else:
            threading.Thread(target=fn, args=args, daemon=True).start()

    def public_price_ok(self, entry):
        """Whether a catalog entry costs no more than the ceiling (US dollars per million tokens, in and out) that visitors'
        model may cost. No price, or a "price varies" marker, never passes: the caps could not count it."""
        pin, pout = entry.get("price_in"), entry.get("price_out")
        if pin is None or pout is None or pin < 0 or pout < 0:
            return False
        lim = self.limits()
        return round(pin * 1e6, 6) <= lim["public_price_ceiling_in"] and round(pout * 1e6, 6) <= lim["public_price_ceiling_out"]

    def limits(self):
        return {**DEFAULT_LIMITS, **(self.db.get_json("limits", {}) or {})}

    def terms_criteria(self):
        return self.db.get_json("terms", None) or DEFAULT_TERMS

    def letter_rules(self, door="recruiter"):
        """The rules for a letter: the owner's text for that door, else the default. 'recruiter' is also the owner's own letters."""
        key = "letter_rules_seeker" if door == "seeker" else "letter_rules"
        return self.db.get_setting(key, "") or letters_mod.default_rules(door)

    def uploaded_public_cv(self):
        """The public CV the owner uploaded in the admin, kept in the database (empty when there is none)."""
        return self.db.get_setting("public_cv", "")

    def cv_text(self, public, run=None):
        """The CV a report is checked against: the one the visitor uploaded with it, the public CV for a visitor's
        check (the owner's upload, else the file named by MATCH_PUBLIC_CV), or the owner's own."""
        if run is not None and run.get("cv_text"):
            return run["cv_text"]
        uploaded = self.uploaded_public_cv()
        if public:
            if uploaded:
                return match.strip_comments(uploaded)
            return match.strip_comments(Path(self.settings.public_cv_path).read_text(encoding="utf-8"))
        if uploaded and not self.settings.cv_explicit:
            return match.strip_comments(uploaded)
        return match.strip_comments(Path(self.settings.cv_path).read_text(encoding="utf-8"))

    def save_public_cv(self, text):
        """Store the public CV from the admin. Email addresses and phone numbers are removed, as for any uploaded CV."""
        text = cvfile.redact_contacts(cvfile.clean(text))
        if len(text) < 200:
            raise ValueError("That looks too short to be a CV. Paste or upload the whole text.")
        if len(text) > cvfile.MAX_TEXT:
            raise ValueError(f"The CV is longer than {cvfile.MAX_TEXT:,} characters.")
        self.db.set_setting("public_cv", text)
        return len(match.chunk_cv(match.strip_comments(text)))

    @staticmethod
    def shareable_items(items):
        """Report items for a share link: an item that rests on one of the owner's extra facts (chunk id x...) loses its
        quoted evidence and its reason, which may repeat the fact, before the chat or the letter can see it."""
        out = []
        for it in items:
            if (it.get("chunk_id") or "").startswith("x"):
                it = {**it, "evidence": "", "chunk_id": None, "section": "",
                      "reason": "Rests on a fact that is not shared on this link."}
            out.append(it)
        return out

    def candidate_name(self, run):
        """Whose CV the report is about: the name the uploader gave, or the owner for the owner's own CV."""
        if run.get("cv_text"):
            return run.get("candidate_name") or "the candidate"
        return self.settings.owner_name

    def entry(self, model_id):
        e = self.db.get_catalog(model_id)
        if e is None:
            raise ValueError(f"unknown model {model_id!r}")
        ok, why = catalog.availability(e, self.settings)
        if not ok:
            raise RuntimeError(f"{e['label']} is not available: {why}")
        return e

    def backend(self, entry):
        if entry["provider"] == "baseline":
            return BaselineBackend()
        return Agents(entry, self.tracing, self.settings, model=self.model_factory(entry))

    def _chunks(self, run, public):
        return match.chunk_cv(self.cv_text(public, run)) + (match.chunk_extra(run["extra_facts"]) if not public else [])

    def view_items(self, result_id):
        return self.db.items_for(result_id)

    def primary_result(self, run_id):
        done = [r for r in self.db.results_for(run_id, "fit") if r["status"] == "done"]
        return done[-1] if done else None

    def _fail(self, fn, table_update, error):
        table_update(error)

    # --- fit report -----------------------------------------------------------------------
    def run_fit(self, result_id):
        started = time.time()
        result = self.db.get_result(result_id)
        run = self.db.get_run(result["run_id"])
        public = run["source"] == "public"
        self.db.set_status(result_id, "running")
        try:
            with self.job_slot(run["id"], public, not public):
                entry = self.entry(result["backend"])
                with self.tracing.subject("match.fit", "result", result_id, entry, public) as trace_id:
                    self.db.set_result_trace(result_id, trace_id)
                    backend = self.backend(entry)
                    if hasattr(backend, "on_posting_facts"):   # the title shows on the page as soon as the posting is read
                        backend.on_posting_facts = lambda title, company, headings: (
                            self.db.fill_run_title(run["id"], title, company), self.db.fill_run_headings(run["id"], headings))
                    items = match.run_match(run["posting_text"], self.cv_text(public, run),
                                            "" if public else run["extra_facts"], backend)
                    overall = None
                    if hasattr(backend, "overall"):  # the baseline has no model, so no overall reading
                        try:  # a failed reading never fails the report; the point-by-point result stands on its own
                            overall = backend.overall(run["posting_text"][:match.POSTING_LIMIT], items, self._chunks(run, public))
                        except Exception as e:
                            overall = {"error": f"{type(e).__name__}: {str(e)[:300]}"}
                self.db.save_items(result_id, items)
                if overall is not None:
                    self.db.set_overall(result_id, overall)
                self.db.set_status(result_id, "done", seconds=time.time() - started)
            if public and run["source"] == "public" and not run.get("retention") and result["kind"] == "fit":
                self._auto_letter(run)   # a recruiter's report comes with a letter; a job seeker asks for theirs
        except Exception as e:  # recorded on the result and shown in the admin; the trace holds the detail
            self.db.set_status(result_id, "failed", error=f"{type(e).__name__}: {e}", seconds=time.time() - started)

    def _auto_letter(self, run):
        """One draft letter, written once the match is done. It counts as one of the report's letters and passes the same budget
        checks. Whatever goes wrong here (no model, a cap reached) never fails the report: the visitor can still ask for it."""
        try:
            model_id = self.chat_model_for(run)
            if self.limits()["auto_letter"] and model_id and not self.db.letters_for(run["id"], "shared"):
                self.request_letter(run["id"], model_id, f"Write in English. {LETTER_LENGTHS['standard']}", audience="shared")
        except Exception:
            pass

    # --- the posting against the candidate's terms ------------------------------------------
    def run_terms(self, result_id):
        started = time.time()
        result = self.db.get_result(result_id)
        run = self.db.get_run(result["run_id"])
        self.db.set_status(result_id, "running")
        try:
            self.check_budget(owner=True)
            entry = self.entry(result["backend"])
            if entry["provider"] == "baseline":
                raise ValueError("checking terms needs a model; the baseline cannot read a posting that closely")
            criteria = self.terms_criteria()
            with self.tracing.subject("match.terms", "result", result_id, entry) as trace_id:
                self.db.set_result_trace(result_id, trace_id)
                verdicts = self.backend(entry).terms(run["posting_text"], criteria)
            self.db.save_term_items(result_id, [
                {"idx": i, "name": c["name"], "description": c["description"], "importance": c["importance"], **v}
                for i, (c, v) in enumerate(zip(criteria, verdicts))])
            self.db.set_status(result_id, "done", seconds=time.time() - started)
        except Exception as e:
            self.db.set_status(result_id, "failed", error=f"{type(e).__name__}: {e}", seconds=time.time() - started)

    # --- talent pool plan ---------------------------------------------------------------------
    def run_pool_plan(self, run_id, model_id):
        result = self.primary_result(run_id)
        if result is None:
            raise ValueError("run a fit report first: the plan is built from its requirements")
        self.check_budget(owner=True)
        entry = self.entry(model_id)
        if entry["provider"] == "baseline":
            raise ValueError("planning the search needs a model")
        reqs = [{"text": i["text"], "kind": i["kind"]} for i in self.db.items_for(result["id"])]
        with self.tracing.subject("match.pool_plan", "run", run_id, entry):
            steps = self.backend(entry).pool_plan(reqs)
        self.db.save_pool_steps(run_id, steps)

    # --- cover letter ---------------------------------------------------------------------------
    def request_letter(self, run_id, backend, instructions="", audience="owner"):
        self.check_budget(run_id, shared=audience == "shared", owner=audience != "shared")
        letter_id = self.db.create_letter(run_id, backend, instructions, audience)
        self.submit(self.run_letter, letter_id)
        return letter_id

    def request_letter_variants(self, run_id, backend):
        """One draft per opening (proof, question, problem) for the owner to compare blind. Openings that already have a
        draft on this run are not written again. Nothing here is shown to visitors."""
        have = {l["variant"] for l in self.db.letters_for(run_id) if l["variant"]}
        ids = []
        for key in letters_mod.VARIANTS:
            if key in have:
                continue
            self.check_budget(run_id, owner=True)
            ids.append(self.db.create_letter(run_id, backend, f"Write in English. {LETTER_LENGTHS['standard']}", "owner", key))
        for letter_id in ids:
            self.submit(self.run_letter, letter_id)
        return ids

    def letter_vote_stats(self):
        """Per opening: how often it was picked, lost or tied in the blind comparisons, and what the code checks say about its drafts."""
        stats = {k: {"picked": 0, "lost": 0, "tied": 0, "compared": 0, "passed_checks": 0, "drafts": 0, "mean_words": None}
                 for k in letters_mod.VARIANTS}
        for v in self.db.letter_votes():
            a, b = v["variant_a"], v["variant_b"]
            if a not in stats or b not in stats:
                continue
            if v["winner"] == "tie":
                stats[a]["tied"] += 1
                stats[b]["tied"] += 1
            else:
                win, lose = (a, b) if v["winner"] == "a" else (b, a)
                stats[win]["picked"] += 1
                stats[lose]["lost"] += 1
        words = {k: [] for k in stats}
        for l in self.db.all("SELECT variant, status, checks FROM letters WHERE variant != ''"):
            if l["status"] != "done" or l["variant"] not in stats:
                continue
            checks = json.loads(l["checks"] or "{}")
            stats[l["variant"]]["drafts"] += 1
            stats[l["variant"]]["passed_checks"] += 1 if checks.get("ok") else 0
            if checks.get("words"):
                words[l["variant"]].append(checks["words"])
        for k, st in stats.items():
            st["compared"] = st["picked"] + st["lost"] + st["tied"]
            st["mean_words"] = round(sum(words[k]) / len(words[k])) if words[k] else None
        return stats

    def run_letter(self, letter_id):
        letter = self.db.get_letter(letter_id)
        run = self.db.get_run(letter["run_id"])
        public = run["source"] == "public" or letter["audience"] == "shared"
        self.db.update_letter(letter_id, status="running")
        try:
            with self.job_slot(run["id"], public, not public):
                entry = self.entry(letter["backend"])
                if entry["provider"] == "baseline":
                    raise ValueError("drafting a letter needs a model")
                result = self.primary_result(run["id"])
                if result is None:
                    raise ValueError("there is no finished report to base the letter on")
                items = self.db.items_for(result["id"])
                if public:
                    items = self.shareable_items(items)
                chunks = {i["chunk_id"]: {"id": i["chunk_id"], "section": i["section"], "text": i["evidence"]}
                          for i in items if i["chunk_id"] and i["evidence"]}
                agents = self.backend(entry)
                rules = (letters_mod.VARIANTS[letter["variant"]] if letter.get("variant")
                         else self.letter_rules(self._door(run["id"])))
                with self.tracing.subject("match.letter", "letter", letter_id, entry, public) as trace_id:
                    self.db.update_letter(letter_id, trace_id=trace_id)  # tied at once, so a failed call is still deleted with it
                    paras = agents.letter(run["posting_text"], items, chunks, rules, self.candidate_name(run),
                                          letter["instructions"])
                    checks = letters_mod.check_letter(paras, chunks, items)
                    if not checks["ok"]:  # one repair round with the reasons
                        fix = (letter["instructions"] + "\nFix these problems from the last draft: "
                               + "; ".join(checks["errors"])).strip()
                        paras = agents.letter(run["posting_text"], items, chunks, rules, self.candidate_name(run), fix)
                        checks = letters_mod.check_letter(paras, chunks, items)
                status, error = "done", ""
                if public and not checks["ok"]:
                    status, error = "failed", "No draft met the rules (every claim needs a CV line behind it)."
                self.db.update_letter(letter_id, status=status, error=error, trace_id=trace_id, paragraphs=paras,
                                      content=letters_mod.render(paras), checks=checks)
        except Exception as e:
            self.db.update_letter(letter_id, status="failed", error=f"{type(e).__name__}: {e}")

    # --- chat --------------------------------------------------------------------------------------
    def ask(self, run_id, backend, question, skeptic=False, max_messages=None, audience="owner"):
        """Store the question, queue the answer, return (user_message_id, assistant_message_id)."""
        q = question.strip()
        if not q:
            raise ValueError("Type a question first.")
        if len(q) > MAX_QUESTION:
            raise ValueError(f"Keep the question under {MAX_QUESTION} characters.")
        self.check_budget(run_id, shared=audience == "shared", owner=audience != "shared")
        chat = self.db.get_or_create_chat(run_id, backend, audience)
        if max_messages is not None and self.db.count_user_messages(chat["id"]) >= max_messages:
            raise LimitError(f"This chat has reached its limit of {max_messages} questions.")
        mode = "skeptic" if skeptic else "normal"
        uid = self.db.add_message(chat["id"], "user", q, mode=mode)
        aid = self.db.add_message(chat["id"], "assistant", "", status="pending", model=backend, mode=mode)
        self.submit(self.run_chat, aid)
        return uid, aid

    def run_chat(self, message_id):
        msg = self.db.get_message(message_id)
        chat = self.db.one("SELECT * FROM chats WHERE id = ?", (msg["chat_id"],))
        run = self.db.get_run(chat["run_id"])
        public = run["source"] == "public" or chat["audience"] == "shared"
        try:
            with self.job_slot(run["id"], public, not public):
                entry = self.entry(chat["backend"])
                if entry["provider"] == "baseline":
                    raise ValueError("the chat needs a model")
                result = self.primary_result(run["id"])
                if result is None:
                    raise ValueError("there is no finished report to talk about")
                items = self.db.items_for(result["id"])
                if public:
                    items = self.shareable_items(items)
                earlier = [m for m in self.db.messages_for(chat["id"]) if m["id"] < message_id]
                question = earlier[-1]  # the user message stored just before this answer
                turns = [(m["role"], m["content"]) for m in earlier[:-1] if m["status"] == "done" and m["content"]]
                rc = ReportChat(entry, self.tracing, self.model_factory(entry), run["posting_text"], items,
                                self._chunks(run, public),
                                score_value=match.score(items), gap=match.minimum_gap(items))
                with self.tracing.subject("match.chat", "chat_message", message_id, entry, public) as trace_id:
                    self.db.update_message(message_id, trace_id=trace_id)
                    out = rc.ask(question["content"], turns, skeptic=(msg["mode"] == "skeptic"))
                self.db.update_message(message_id, content=out["content"], evidence=out["evidence"],
                                       suggestions=out["suggestions"], exchanges=out.get("exchanges", []),
                                       trace_id=trace_id, error=out.get("error", ""),
                                       status="failed" if out["scope"] == "failed" else "done")
        except LimitError as e:
            self.db.update_message(message_id, status="failed", error=str(e), content=str(e))
        except Exception as e:
            self.db.update_message(message_id, status="failed", error=f"{type(e).__name__}: {e}",
                                  content="Something went wrong on our side. Please try again.")

    # --- public checks ----------------------------------------------------------------------------
    def public_model(self):
        return self.db.get_setting("public_model", "")

    def _public_cv_missing(self):
        return not self.settings.public_cv_explicit and not self.uploaded_public_cv()

    def public_admin_fix(self):
        """Which admin page fixes what public_admin_reason names: the Settings page for a missing CV, else the Models page."""
        return "settings" if self._public_cv_missing() else "models"

    def public_admin_reason(self):
        """Why public checks are off, in words for the owner. Empty when they are on."""
        if self._public_cv_missing():
            return "Upload the CV that visitors may see quoted, in the first section of the Settings page. Your master CV is never used for this."
        if not self.public_model():
            return "Choose a public model on the Models page."
        e = self.db.get_catalog(self.public_model())
        if e is None:
            return "The public model is not in the catalog."
        ok, why = catalog.availability(e, self.settings)
        if not ok:
            return f"The public model is not ready: {why}"
        if not e["local_only"] and not self.public_price_ok(e):
            return "The public model costs more than the price ceiling set under Settings. Choose a cheaper model or raise the ceiling."
        if e["local_only"] and not self.settings.allow_local_public:
            return "The public model runs locally and has no price, so the daily budget cannot work. Choose a priced model."
        return ""

    def public_available(self):
        if self.public_admin_reason():
            return False, "Checks are paused for now. Please come back later."
        mid = self.public_model()
        if not mid:
            return False, "Public checks are not switched on yet."
        e = self.db.get_catalog(mid)
        if e is None:
            return False, "The model for public checks is not set up."
        ok, why = catalog.availability(e, self.settings)
        return ok, "" if ok else "Public checks are paused."

    def _admit(self, posting, visitor, door="recruiter"):
        """The checks every visitor's report passes before anything is stored; returns the cleaned posting."""
        lim = self.limits()
        ok, why = self.public_available()
        if not ok:
            raise LimitError(why)
        posting = posting.strip()
        if not posting:
            raise PostingError("Give the link to the posting.")
        if len(posting) < 200:
            raise PostingError("That looks too short to be a job posting. Check the link, or upload the posting as a PDF.")
        if len(posting) > lim["posting_max_chars"]:
            raise PostingError(f"The posting is longer than {lim['posting_max_chars']:,} characters. Upload a PDF of just the requirements part.")
        day_ago = _utc(datetime.now(timezone.utc) - timedelta(days=1))
        per_day = lim["checks_per_day_seeker"] if door == "seeker" else lim["checks_per_day"]
        if self.db.count_public_runs_since(visitor, day_ago, door) >= per_day:
            raise LimitError(f"You have used your {per_day} checks for today. Try again in 24 hours.")
        # How fast checks may start: one visitor cannot fire them off in a row, and everyone together is spread over the hour
        # so a burst from one source leaves room for the others. Refused attempts are not counted (nothing is logged for them).
        now = datetime.now(timezone.utc)
        minute_ago, hour_ago = _utc(now - timedelta(minutes=1)), _utc(now - timedelta(hours=1))
        if self.db.count_public_runs_since(visitor, minute_ago) >= lim["checks_per_visitor_per_minute"]:
            raise LimitError("You started a check a moment ago. Wait a minute before the next one.")
        if self.db.count_checks_since(minute_ago) >= lim["checks_per_minute_all"] \
                or self.db.count_checks_since(hour_ago) >= lim["checks_per_hour_all"]:
            raise LimitError("Checks are busy right now. Try again in a little while.")
        self.check_budget(shared=True, door=door)
        return posting

    def create_public_check(self, posting, title, company, visitor):
        lim = self.limits()
        posting = self._admit(posting, visitor, "recruiter")
        expires = _utc(datetime.now(timezone.utc) + timedelta(days=lim["retention_days"]))
        run_id, token = self.db.create_public_run(title.strip()[:120], company.strip()[:120], posting, visitor, expires)
        self.submit(self.run_fit, self.db.add_result(run_id, self.public_model()))
        if self.db.get_json("terms", None):   # the posting against the owner's own terms, for the owner's eyes only (never on a public page);
            # not when the terms are still the examples: a score against them would mean nothing
            self.submit(self.run_terms, self.db.add_result(run_id, self.public_model(), kind="terms"))
        return run_id, token

    def create_upload_check(self, posting, cv, title, company, candidate_name, retention, visitor, consent):
        """A report on a CV the visitor supplies. Nothing is read or stored unless the consent box was ticked."""
        if not consent:
            raise ValueError("Tick the box to agree before uploading a CV.")
        if retention not in ("session", "days"):
            raise ValueError("Choose how long the CV and the report may be kept.")
        lim = self.limits()
        if len(cv) > cvfile.MAX_TEXT:  # checked before any pattern runs on it
            raise ValueError(f"The CV is longer than {lim['cv_max_chars']:,} characters. Shorten it or leave out old roles.")
        cv = cvfile.redact_contacts(cvfile.clean(cv))
        if len(cv) < 200:
            raise ValueError("That looks too short to be a CV. Paste or upload the whole text.")
        if len(cv) > lim["cv_max_chars"]:
            raise ValueError(f"The CV is longer than {lim['cv_max_chars']:,} characters. Shorten it or leave out old roles.")
        posting = self._admit(posting, visitor, "seeker")
        keep = timedelta(hours=SESSION_HOURS) if retention == "session" else timedelta(days=lim["retention_days"])
        run_id, token = self.db.create_upload_run(
            title.strip()[:120], company.strip()[:120], posting, visitor, _utc(datetime.now(timezone.utc) + keep),
            candidate_name=cvfile.redact_contacts(candidate_name.strip()[:cvfile.MAX_NAME])[:80], cv_text=cv, retention=retention,
            consent_version=CONSENT_VERSION)
        self.submit(self.run_fit, self.db.add_result(run_id, self.public_model()))
        return run_id, token

    def provider_label(self):
        """Who receives the text of a report: the company behind the public model, in words for the privacy page."""
        entry = self.db.get_catalog(self.public_model()) if self.public_model() else None
        labels = {"openrouter": "OpenRouter, which passes it on to the company that runs the model",
                  "bedrock": "AWS Bedrock", "anthropic": "Anthropic"}
        return labels.get(entry["provider"], "an AI model provider") if entry else "an AI model provider"

    def touch_run(self, run):
        """An uploaded CV kept only for the session stays for two hours after the last action."""
        if run.get("cv_text") and run.get("retention") == "session":
            self.db.set_expiry(run["id"], _utc(datetime.now(timezone.utc) + timedelta(hours=SESSION_HOURS)))

    def spend(self):
        """Numbers for the dashboard: spend this month and today against the caps."""
        lim = self.limits()
        now = datetime.now(timezone.utc)
        month = self.db.billable_spend_since(_utc(now.replace(day=1, hour=0, minute=0, second=0)))
        day = self.db.billable_spend_since(_utc(now.replace(hour=0, minute=0, second=0)))
        return {"month": month, "month_cap": lim["monthly_budget_usd"], "day": day,
                "public_day": self.db.public_spend_since(_utc(now.replace(hour=0, minute=0, second=0))),
                "day_cap": lim["daily_budget_usd"], "session_cap": lim["session_budget_usd"]}

    def _door(self, run_id):
        run = self.db.get_run(run_id) if run_id is not None else None
        return "seeker" if run and run.get("retention") else "recruiter"

    def check_budget(self, run_id=None, shared=False, owner=False, extra_usd=0.0, door=None):
        """Raise LimitError when a cap is reached. Visitors get a message without amounts; the owner gets numbers.
        Money that running jobs have reserved counts as spent, because their cost is recorded only when they end."""
        lim, sp = self.limits(), self.spend()
        door = door or self._door(run_id)
        held = list(self._reserved.values())
        res_all = extra_usd + sum(u for _, _, u, _ in held)
        res_public = (extra_usd if shared else 0.0) + sum(u for _, sh, u, _ in held if sh)
        res_door = (extra_usd if shared else 0.0) + sum(u for _, sh, u, d in held if sh and d == door)
        res_run = (extra_usd if shared else 0.0) + sum(u for r, _, u, _ in held if run_id is not None and r == run_id)
        if sp["month"] + res_all >= lim["monthly_budget_usd"]:
            raise LimitError(
                f"Monthly cap reached: ${sp['month']:.2f} of ${lim['monthly_budget_usd']:.2f}. Raise it in Settings."
                if owner else "The free checks are paused until next month.")
        if shared and sp["public_day"] + res_public >= lim["daily_budget_usd"]:
            raise LimitError("The daily budget for free checks is used up. Try again in 24 hours.")
        if shared and self.db.public_spend_since(_utc(datetime.now(timezone.utc) - timedelta(hours=1))) + res_public \
                >= lim["hourly_budget_usd"]:   # a rolling hour: one burst cannot use up the whole day
            raise LimitError("Checks are busy right now. Try again in a little while.")
        if shared and door == "seeker":        # job seekers may use only a share of the hour and the day; recruiters get the rest
            now = datetime.now(timezone.utc)
            share = lim["seeker_budget_share"]
            if self.db.public_spend_since(_utc(now - timedelta(hours=1)), "seeker") + res_door >= lim["hourly_budget_usd"] * share \
                    or self.db.public_spend_since(_utc(now.replace(hour=0, minute=0, second=0)), "seeker") + res_door \
                    >= lim["daily_budget_usd"] * share:
                raise LimitError("Checks of your own CV are paused for now: the share of the free budget for them is used up. "
                                 "Try again later.")
        if shared and run_id is not None and self.db.run_spend(run_id, shared_only=True) + res_run >= lim["session_budget_usd"]:
            raise LimitError("This report has reached its limit for questions and drafts.")
        if shared and threading.active_count() > self.MAX_THREADS:
            raise LimitError("The site is busy right now. Try again in a minute.")

    @contextmanager
    def job_slot(self, run_id, shared, owner=False):
        """Held for the length of a job that calls a model: waits for one of a few slots, then checks the caps and
        reserves a share of them in one step, so a burst of parallel jobs cannot all pass the check before any is paid."""
        if shared and not self._slots.acquire(timeout=90):
            raise LimitError("The site is busy right now. Try again in a minute.")
        token = None
        try:
            with self._reserve_lock:
                reserve = float(self.limits()["job_reserve_usd"])
                door = self._door(run_id)
                self.check_budget(run_id, shared=shared, owner=owner, extra_usd=reserve, door=door)
                token = next(self._tokens)
                self._reserved[token] = (run_id, shared, reserve, door)
            yield
        finally:
            if token is not None:
                self._reserved.pop(token, None)
            if shared:
                self._slots.release()

    def chat_model_for(self, run):
        """The model that answers a visitor's questions: the public model, or the admin's chat model for shared runs."""
        if run["source"] == "public":
            return self.public_model()
        return self.db.get_setting("chat_model", "") or self.public_model()

    def recover_stalled(self):
        """Jobs run in threads, so a restart loses them. Mark what was in flight as failed so nothing waits forever."""
        msg = "Interrupted by a restart. Run it again."
        n = 0
        for table, fields in (("results", "status=?, error=?"), ("letters", "status=?, error=?")):
            n += self.db.run_count(f"UPDATE {table} SET {fields} WHERE status IN ('queued', 'running')", ("failed", msg))
        n += self.db.run_count("UPDATE chat_messages SET status=?, error=?, content=? WHERE status='pending'",
                               ("failed", msg, "That answer was interrupted by a restart. Please ask again."))
        n += self.db.run_count("UPDATE eval_runs SET status=?, error=? WHERE status IN ('queued', 'running')",
                               ("failed", msg))
        return n

    _last_purge = 0.0

    def maybe_purge(self, every_seconds=3600):
        """Delete expired visitor reports at most once an hour, from normal traffic, so retention does not wait for a restart."""
        if time.time() - self._last_purge >= every_seconds:
            self._last_purge = time.time()
            return self.purge_expired()
        return 0

    def purge_expired(self):
        ids = self.db.expired_run_ids(_utc())
        for rid in ids:
            self.db.delete_run(rid)
        self.db.purge_old_records(_utc(datetime.now(timezone.utc) - timedelta(days=365)))  # wishes, consent records
        self.db.purge_orphan_traces()
        self.db.checkpoint()  # fold the write-ahead log back into the database file
        return len(ids)
