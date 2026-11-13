"""The public roadmap, a wishlist form and one-click votes. Items live here, in the code, so a change goes through git.

Nothing on the roadmap is promised or dated. A vote is one per visitor per item (a salted hash of the address, never the
address). Wishes are free text with an optional contact address, kept until the owner deletes them."""
import re
import time
from datetime import datetime, timedelta, timezone

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for

import service as service_mod
from web import auth

bp = Blueprint("roadmap", __name__)
MAX_WISH = 1000
MAX_CONTACT = 120
WISHES_PER_DAY = 3
MIN_FORM_SECONDS = 3
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

STATUSES = {"live": "Available", "planned": "Planned", "exploring": "Exploring", "idea": "Idea"}
GROUPS = (("features", "New features"), ("hardening", "Making it sturdier"))

# key, title, status, what it is, why it matters. Plain sentences; no dates.
ITEMS = [
    {"key": "own-cv", "title": "Check your own CV against a posting", "status": "live",
     "what": "Paste a CV and a job posting and get the same report, follow-up chat and cover letter for that pair. You choose whether it is deleted when you are done or kept for 30 days.",
     "why": "Makes the tool useful to anyone applying. It is for your own CV only: please do not upload anyone else's. Available now on the Check your CV page."},
    {"key": "ask-the-cv", "title": "Ask the CV about things the posting never asked", "status": "live",
     "what": "The chat can check a question about the CV that the report does not cover, for example whether it shows work with developers. It asks the same judge that wrote the report, reads the whole CV, and shows the CV line behind its answer.",
     "why": "A report only covers what the posting asked for. The chat covers the rest."},
    {"key": "api", "title": "A JSON API", "status": "planned",
     "what": "Create a report, read it, ask a follow-up question and request a letter from your own code, with a token per client.",
     "why": "Lets other tools and scripts use the match without the web page. Rate limits and spend caps apply per token."},
    {"key": "mcp", "title": "An MCP server", "status": "planned",
     "what": "The same operations as tools an AI agent can call: check a match, read a report, ask about it, draft a letter.",
     "why": "An agent that already works with your CV or a job board can use the match directly."},
    {"key": "chat-engine", "title": "The chat asks the match engine", "status": "live",
     "what": "Ask the chat what leaving out a requirement would do to the match. It does not guess: it sends a message to the match engine, which owns the scoring, and the exchange is shown under the answer.",
     "why": "A first, small case of two pieces of software handing work to each other in a defined message format, shaped like the tool messages of the Model Context Protocol. The engine is plain code, not a second model, because a recalculation is arithmetic."},
    {"key": "bot-to-bot", "title": "Agent to agent", "status": "exploring",
     "what": "An agent acting for a candidate and an agent acting for a hiring team exchange a posting, a CV and a match report in a machine-readable form, with a person approving what is shared.",
     "why": "Screening is mostly two documents being compared. Letting software do the first pass, in the open, is the idea behind the whole site."},
    {"key": "pool", "title": "A candidate pool", "status": "exploring",
     "what": "Opt-in only: a candidate can keep their CV and report so that the owner and the owner's agents can find them for a role.",
     "why": "Needs a ranked shortlist before any model scoring, and clear rules on consent, who can search and how long data is kept. A system that ranks candidates for recruitment counts as high-risk under the EU AI Act, so that is assessed before anything is built."},
    {"key": "shortlist", "title": "Best candidates first", "status": "exploring",
     "what": "Rank a pool of CVs against a posting cheaply (which requirements each CV has strong evidence for), then run the full report on the top few.",
     "why": "Scoring every CV with a model costs too much. A shortlist stage makes a large pool affordable. Like the pool, it would need the EU AI Act assessment for recruitment systems first."},
    {"key": "accuracy", "title": "A public accuracy page", "status": "idea",
     "what": "Show how the reports score against hand-checked cases, and how each model compares on quality and cost.",
     "why": "A verdict is a model's reading. Publishing how often it is right is the honest way to show it."},
    {"key": "interview-prep", "title": "Interview prep from the gaps", "status": "live",
     "what": "The overall reading ends with a short list under \"Worth confirming in a conversation\": the points the CV only suggests or does not show, as questions to prepare or to ask.",
     "why": "The report says what is not shown. This turns it into something to do next."},
    {"key": "compare", "title": "Compare two postings", "status": "idea",
     "what": "Put two postings side by side against the same CV and see where they differ.",
     "why": "Useful when choosing between roles."},
    {"key": "languages", "title": "Reports in more languages", "status": "idea",
     "what": "Read postings and write reports and letters in German, Italian and others.",
     "why": "Many postings are not in English."},
    {"key": "job-queue", "group": "hardening", "title": "A job queue", "status": "planned",
     "what": "Reports, chat answers and letters run through a queue instead of threads inside the web process, so work survives a restart and a burst of requests waits its turn.",
     "why": "Today a restart cuts off a report being written and parallel jobs compete inside one process. A queue also lets the site run on more than one server."},
    {"key": "separate-storage", "group": "hardening", "title": "Storage away from the web server", "status": "planned",
     "what": "Reports and uploaded CVs live in a managed database outside the web server, with encrypted, tested backups and deletion that reaches them.",
     "why": "The database sits on the same small server as the app. A separate store means a rebuilt server loses nothing and backups follow the same deletion rules as the data."},
    {"key": "stronger-login", "group": "hardening", "title": "Two-factor login for the admin", "status": "planned",
     "what": "The administrator signs in with a password and a second factor.",
     "why": "The admin can read every report and CV, so it deserves more than a password."},
    {"key": "bot-check", "group": "hardening", "title": "Abuse protection at the edge", "status": "exploring",
     "what": "A bot check and request limits in front of the app, so one source cannot use up the daily budget that is shared by everyone.",
     "why": "The limits per visitor are kept in the app. A shared budget can still be drained by many sources, which blocks real visitors."},
    {"key": "locked-builds", "group": "hardening", "title": "Locked and signed builds", "status": "exploring",
     "what": "Dependencies locked with hashes, base images pinned, builds reproducible, and automatic alerts for known vulnerabilities.",
     "why": "Today new builds pick up the latest compatible versions. Locking them makes a build repeatable and a supply-chain surprise less likely."},
]
KEYS = {i["key"] for i in ITEMS}


def svc():
    return current_app.extensions["svc"]


def _visitor():
    return service_mod.visitor_hash(current_app.secret_key, request.remote_addr or "")


def _page(error=None, status=200, form=None):
    db = svc().db
    return render_template("roadmap.html", items=[i for i in ITEMS if i["status"] != "live"], live=[i for i in ITEMS if i["status"] == "live"], groups=GROUPS, statuses=STATUSES, votes=db.roadmap_vote_counts(),
                           mine=db.roadmap_votes_of(_visitor()), error=error, form=form or {}, form_ts=int(time.time()),
                           max_wish=MAX_WISH, max_contact=MAX_CONTACT), status


@bp.get("/roadmap")
def roadmap():
    return _page()


@bp.post("/roadmap/vote")
def vote():
    item = request.form.get("item", "")
    if item not in KEYS:
        abort(400)
    if not auth.allow(f"vote:{_visitor()}", 30, 3600):
        return _page("Too many votes from this address. Try again in an hour.", 429)
    if svc().db.add_roadmap_vote(item, _visitor()):
        flash("Thanks, your vote is counted.")
    else:
        flash("You have already voted for that one.")
    return redirect(url_for("roadmap.roadmap") + f"#item-{item}")


@bp.post("/roadmap/wish")
def wish():
    form = request.form
    if form.get("website"):  # honeypot: real visitors never see this field
        abort(400)
    try:
        waited = time.time() - int(form.get("ts", "0") or 0)
    except ValueError:
        waited = 0
    if waited < MIN_FORM_SECONDS:
        return _page("Send it again; the form keeps your text.", 429, form)
    text, contact, item = form.get("text", "").strip(), form.get("contact", "").strip(), form.get("item", "")
    if item and item not in KEYS:
        abort(400)
    if len(text) < 10:
        return _page("Write a sentence or two about what you would like.", 400, form)
    if len(text) > MAX_WISH:
        return _page(f"That is longer than {MAX_WISH} characters. Shorten it.", 400, form)
    if contact and (len(contact) > MAX_CONTACT or not EMAIL.match(contact)):
        return _page("That does not look like an email address. Leave it empty if you do not want a reply.", 400, form)
    day_ago = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
    if svc().db.count_feedback_since(_visitor(), day_ago) >= WISHES_PER_DAY or not auth.allow(f"wish:{_visitor()}", 10, 3600):
        return _page(f"You have sent {WISHES_PER_DAY} messages today. Try again tomorrow.", 429, form)
    svc().db.add_feedback(item, text, contact, _visitor())
    flash("Thanks, your message is saved. It is read by a person.")
    return redirect(url_for("roadmap.roadmap"))
