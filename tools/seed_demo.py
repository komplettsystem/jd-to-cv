"""Fill a DEVELOPMENT database with realistic but fictional data, by running the real pipeline with a scripted model.

    MATCH_DB=data/demo.db MATCH_CV=tools/sample_cv.md python tools/seed_demo.py

Everything it creates (postings, candidate, talent-pool counts, eval results, votes) is made up so that the
dashboards can be designed and tested against full, realistic shapes. Never use this database in production,
and never present its numbers as real."""
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("MATCH_DB", "data/demo.db")
os.environ.setdefault("MATCH_CV", str(Path(__file__).with_name("sample_cv.md")))
os.environ.setdefault("MATCH_PUBLIC_CV", os.environ["MATCH_CV"])
os.environ.setdefault("OWNER_NAME", "Ada Example")

from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart  # noqa: E402
from pydantic_ai.models.function import FunctionModel  # noqa: E402

import match  # noqa: E402
from app import create_app  # noqa: E402
from config import Settings  # noqa: E402
from llm import catalog  # noqa: E402
from llm.baseline import BaselineBackend  # noqa: E402
from service import DEFAULT_TERMS  # noqa: E402

POSTINGS = [
    ("Senior Product Manager, Agentic Search", "Northwind Labs", """About us
Northwind builds search for developers and their agents.

What you'll do
- Own the roadmap for our search API and talk to developers every day.

Minimum Requirements
- A record of shipping search or retrieval products used by developers.
- Experience defining and measuring search quality, including offline evaluation and relevance judgments.
- Experience leading a cross-functional team of engineers and data scientists.
- Experience with usage-based pricing for an API product.

Preferred Qualifications
- Hands-on work with embeddings, hybrid retrieval and reranking.
- Familiarity with the web publishing ecosystem, crawlers and answer engine optimization.
- You have written code to test an idea yourself.

Compensation
Remote in Europe. EUR 110,000 to 140,000 plus equity.
"""),
    ("Director of Product, Data Platform", "Fabrikam Cloud", """About the role
Lead product for a managed data platform used by thousands of teams.

Requirements
- 8 or more years in product management, including 4 years leading product managers.
- Experience owning a data infrastructure or developer platform product from strategy to launch.
- Track record of improving onboarding and time to first value for technical users.
- Experience working with finance on pricing and packaging.

Nice to have
- Experience with streaming data, warehouses or ETL tools.
- A degree in computer science or equivalent experience.

Benefits
Hybrid, Berlin office three days a week. Salary EUR 125,000 to 150,000.
"""),
    ("Principal PM, AI Quality and Evaluation", "Contoso AI", """Minimum Requirements
- A record of building AI products and delivering measurable customer outcomes.
- Hands-on experience building agents and evaluating them with applied scientists.
- Experience with train and test separation, overfitting and honest reporting of results.
- Experience setting shared quality standards across several teams.

Preferred Qualifications
- Experience with agent observability or evaluation platforms as a product owner.
- Coaching product partners without formal authority.

What we offer
On site in Seattle. Salary USD 210,000 to 330,000.
"""),
    ("Product Manager, Developer Experience", "Globex", """Requirements
- Experience as a product manager for a developer-facing API or SDK.
- Experience running a developer advisory group or community.
- Ability to write clear API guidelines and documentation.

Preferred
- Experience with payments or fintech APIs.
- Fluent German.

Benefits
Fully remote. Pay not listed.
"""),
    ("Junior Product Analyst", "Initech", """Requirements
- 0 to 2 years of experience in analytics or product operations.
- Proficiency in SQL and spreadsheets.

Benefits
On site in Dublin. EUR 45,000.
"""),
]

DEMO_MATCHER_QUESTION = "Has he led a team across several countries?"   # a question about the CV that no posting asked: the bot asks the matcher

CRITERIA_QUOTES = {"Location": ("remote", "Remote"), "Pay": ("salary", "Salary"), "Seniority": ("director", "Director"),
                   "Domain": ("search", "search")}


def quote_for(posting, words):
    for sentence in re.split(r"(?<=[.!?])\s+|\n", posting):
        if any(w in sentence.lower() for w in words) and 12 <= len(sentence) <= 160:
            return sentence.strip("- ").strip()
    return None


def scripted(messages, info):
    time.sleep(random.uniform(0.15, 0.6))
    ins = next((m.instructions for m in messages if getattr(m, "instructions", None)), "")
    text = "\n".join(p.content for m in messages for p in getattr(m, "parts", []) if isinstance(getattr(p, "content", None), str))
    if ins.startswith("You read a job posting"):
        reqs = match.extract_baseline(text.split("<posting>")[-1].split("</posting>")[0])
        body = {"requirements": reqs}
    elif ins.startswith("You judge"):
        whole = "CV lines, any of which" in text      # the CV is read whole: its lines come once, before the requirements
        every = [(m.group(1), m.group(3)) for m in re.finditer(r"^  (c\d+) \| ([^|]*) \| (.*)$", text, re.M)]
        out = []
        for i, req in re.findall(r"^Requirement (\d+) \[\w+\]: (.*)$", text, re.M):
            if whole:
                cands = every
            else:
                block = re.search(rf"^Requirement {i} .*?(?=\n\nRequirement |\Z)", text, re.M | re.S).group(0)
                cands = [(m.group(1), m.group(3)) for m in re.finditer(r"^  (c\d+) \| ([^|]*) \| (.*)$", block, re.M)]
            want = set(match.tokens(req))
            best = max(cands, key=lambda c: len(want & set(match.tokens(c[1]))), default=None)
            cover = len(want & set(match.tokens(best[1]))) / len(want) if best and want else 0
            verdict = "met" if cover >= 0.45 else "partial" if cover >= 0.22 else "not_on_record"
            out.append({"idx": int(i), "verdict": verdict, "evidence_id": best[0] if verdict != "not_on_record" else None,
                        "reason": {"met": "The CV line states this directly.", "partial": "A related line, at a smaller scope.",
                                   "not_on_record": "Nothing in the CV shows this."}[verdict]})
        body = {"judgements": out}
    elif ins.startswith("You check a job posting"):
        posting = text.split("<posting>")[-1].split("</posting>")[0]
        criteria = re.findall(r"^Criterion \d+ \[\w+\] (\w[\w ]*):", text, re.M)
        terms = []
        for i, name in enumerate(criteria):
            q = quote_for(posting, CRITERIA_QUOTES.get(name, ("zzzz",))) if name in CRITERIA_QUOTES else None
            verdict = "unclear" if not q else ("conflict" if name == "Pay" and "45,000" in posting else "fits")
            terms.append({"idx": i, "verdict": verdict, "quote": q if verdict != "unclear" else None,
                          "reason": "The posting states it." if q else "The posting says nothing about it."})
        body = {"terms": terms}
    elif ins.startswith("You give an overall reading"):
        lines = re.findall(r"^(c\d+) \| ([^|]*) \| (.*)$", text.split("CV lines you may cite")[-1], re.M)
        pick = [l for l in lines if "Experience" in l[1] or "Search" in l[1]] or lines
        posting = text.split("CV lines you may cite")[0]
        # Made-up levels that agree with the made-up fit scores, so the demo's lists and chart read as one story.
        level = next((lv for name, lv in (("0 to 2 years", "stretch"), ("developer-facing API or SDK", "strong"), ("record of building AI products", "weak"),
                                          ("managed data platform", "good"), ("search for developers", "good")) if name in posting), "good")
        body = {"level": level,
                "summary": "The CV shows search and data-platform product work at a similar scope to the posting, with product leadership "
                           "in place of the hands-on retrieval engineering the posting also mentions. A solid fit with gaps worth probing.",
                "points": [
                    {"kind": "strength", "text": "Owned a search product end to end across several releases.", "basis": "on_record",
                     "evidence_ids": [pick[0][0]] if pick else []},
                    {"kind": "expected", "text": "Likely used to agreeing relevance measures with an engineering team, from the roles listed.",
                     "basis": "inferred", "evidence_ids": [pick[-1][0]] if pick else []},
                    {"kind": "gap", "text": "No line shows hands-on work on ranking models; the roles are product roles.",
                     "basis": "on_record", "evidence_ids": []}],
                "interview_checks": ["Which relevance measure did you agree with engineering, and who owned changing it?"]}
    elif ins.startswith("You plan"):
        reqs = [l for l in text.splitlines() if re.match(r"^\d+ \[minimum\]", l)][:3]
        steps = []
        for l in reqs:
            words = [w for w in match.tokens(l.split("]", 1)[1]) if len(w) > 4][:3]
            steps.append({"label": " ".join(words[:3]).capitalize() or "Requirement", "requirement_idx": int(l.split()[0]),
                          "any_of": words or ["product"]})
        body = {"titles": ["Product Manager", "Director of Product"], "steps": steps or [{"label": "Search", "any_of": ["search"]}]}
    elif ins.startswith("You draft"):
        ids = re.findall(r"^(c\d+) \|", text.split("Evidence lines you may cite:")[-1], re.M)
        body = {"paragraphs": [
            {"kind": "greeting", "text": "Dear hiring team,", "evidence_ids": []},
            {"kind": "body", "text": "How would you tell that a change to your search is good enough to ship, and who agrees on what good means?", "evidence_ids": ids[:1]},
            {"kind": "body", "text": "I own search products the same way: I set up weekly relevance reviews with the data team and measured a semantic release against the keyword baseline before it shipped.", "evidence_ids": ids[:3]},
            {"kind": "closing", "text": "I am based in Lisbon and would be glad to talk.", "evidence_ids": []}]}
    elif ins.startswith("You are the assistant"):
        question = text.split("<question>")[-1].lower()
        if DEMO_MATCHER_QUESTION.lower() in question:    # the chat bot asks the matcher, then answers from what it was told
            told = [str(p.content) for m in messages for p in getattr(m, "parts", []) if isinstance(p, ToolReturnPart)]
            if not told:
                return ModelResponse(parts=[ToolCallPart("ask_matcher", {"statements": ["Has led a team across several countries."]})])
            got = re.search(r"-> (Met|Partly|Not shown)\.(?: CV line (c\d+))?", told[-1])
            label, cid = got.group(1), got.group(2)
            body = {"scope": "in_scope", "answer": "The matcher read the whole CV for this. Its verdict is " + label.lower() + (", on the quoted line." if cid else ": the CV does not show it."),
                    "basis": ["cv"] if cid else ["report"], "evidence_ids": [cid] if cid else []}
            return ModelResponse(parts=[TextPart(json.dumps(body))])
        ids = re.findall(r"^(c\d+) \| ([^|]*) \| (.*)$", text.split("CV lines that may be relevant")[-1], re.M)
        if any(w in question for w in ("poem", "weather", "recipe", "joke")):
            body = {"scope": "out_of_scope", "answer": "", "basis": []}
        elif ids:
            body = {"scope": "in_scope", "answer": f"The report rests this on one CV line: {ids[0][2][:110].rstrip('.')}. "
                    "That supports the verdict at the scope the line states, no more.", "basis": ["report", "cv"],
                    "evidence_ids": [ids[0][0]]}
        else:
            body = {"scope": "in_scope", "answer": "The posting does not say.", "basis": ["posting"], "evidence_ids": []}
    else:
        raise AssertionError(ins[:50])
    return ModelResponse(parts=[TextPart(json.dumps(body))])


def shift(db, run_id, days):
    """Move a run and everything it produced `days` into the past, so the trend charts have a history."""
    delta = timedelta(days=days, hours=random.randint(0, 8))
    tids = [r["trace_id"] for r in db.all("SELECT trace_id FROM results WHERE run_id=? AND trace_id IS NOT NULL", (run_id,))]
    tids += [r["trace_id"] for r in db.all("SELECT trace_id FROM letters WHERE run_id=? AND trace_id IS NOT NULL", (run_id,))]
    tids += [r["trace_id"] for r in db.all("SELECT m.trace_id FROM chat_messages m JOIN chats c ON c.id=m.chat_id "
                                           "WHERE c.run_id=? AND m.trace_id IS NOT NULL", (run_id,))]
    stamp = lambda s: (datetime.fromisoformat(s) - delta).isoformat(timespec="seconds")
    db.run("UPDATE runs SET created=? WHERE id=?", (stamp(db.get_run(run_id)["created"]), run_id))
    for t in tids:
        row = db.get_trace(t)
        db.run("UPDATE traces SET started=? WHERE trace_id=?", (stamp(row["started"]), t))


def main():
    random.seed(7)
    settings = Settings.from_env(sync_jobs=True)
    if Path(settings.db_path).exists():
        raise SystemExit(f"{settings.db_path} exists. Delete it first: this script only seeds an empty development database.")
    Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
    catalog.availability = lambda entry, s: (True, "")  # the scripted model stands in for every provider
    app = create_app(settings)
    svc = app.extensions["svc"]
    svc.model_factory = lambda entry: FunctionModel(scripted, model_name=entry["model_name"])
    db = svc.db
    db.set_setting("public_model", "openrouter:anthropic/claude-haiku-5.5")
    db.set_setting("chat_model", "openrouter:anthropic/claude-haiku-5.5")
    models = ["openrouter:anthropic/claude-haiku-5.5", "openrouter:nvidia/nemotron-3-super-120b-a12b",
              "bedrock:amazon.nova-lite-v1:0", "baseline"]
    run_ids = []
    for n, (title, company, text) in enumerate(POSTINGS):
        rid = db.create_run(title, company, "", text, "")
        run_ids.append(rid)
        for m in models[: 4 if n < 3 else 2]:
            svc.run_fit(db.add_result(rid, m))
        if n < 4:
            svc.run_terms(db.add_result(rid, models[0], "terms"))
        db.update_meta(rid, title, company, "", ["apply", "maybe", "skip", "maybe", "skip"][n])
    svc.run_pool_plan(run_ids[0], models[0])
    steps = db.pool_for(run_ids[0])
    for st, (cnt, txt) in zip(steps, [(412000, "412K"), (38000, "38K"), (2100, "2,100"), (190, "190")]):
        db.set_pool_count(st["id"], cnt, txt, "development data, not measured")
    svc.request_letter(run_ids[0], models[0], "")
    svc.request_letter(run_ids[1], models[0], "")
    for q in ("Why is search quality only partly on record?", "Which minimum requirements are met?",
              DEMO_MATCHER_QUESTION, "Write me a poem about cats", "What would make this a bad hire?"):
        svc.ask(run_ids[0], models[0], q, skeptic=q.startswith("What would"))
    for rid in (run_ids[0], run_ids[1]):
        done = [r for r in db.results_for(rid, "fit") if r["status"] == "done"]
        for a, b, w in zip(done, done[1:], ("a", "b", "tie")):
            db.add_vote(rid, a["id"], b["id"], w)
        if done:
            db.set_reviewed(done[0]["id"], True)
            for it in db.items_for(done[0]["id"])[:2]:
                db.set_override(it["id"], "partial" if it["verdict"] == "met" else "met", None)
    db.set_json("terms", DEFAULT_TERMS)   # the example terms, so visitors' checks are scored against them too (owner's view only)
    for i, (title, company, text) in enumerate(POSTINGS[:2]):  # visitors' checks
        run_id, token = svc.create_public_check(text, title, company, f"visitor{i}")
        svc.ask(run_id, models[0], "Which requirements are met?", audience="shared")
    for day, rid in enumerate(run_ids):
        shift(db, rid, day * 2 + 1)
    # Eval runs: the real benchmark results in evals/history.json (real models, fictional cases), one run each. Nothing is invented.
    from evals import benchmark
    history = Path(__file__).resolve().parent.parent / "evals" / "history.json"
    for h in (json.loads(history.read_text()) if history.exists() else []):
        if h.get("status") != "done":
            continue
        fp = h["fingerprint"]
        eid = db.create_eval_run("reverse-apply-bench", "synthetic", "openrouter:" + fp["model"], as_of=h["state"])
        failed = set(h["failed"])
        for c in benchmark.CASES:
            db.add_eval_case(eid, c["name"], c["name"] not in failed, {}, {}, {}, "", None, None)
        db.update_eval_run(eid, status="done", n_cases=h["planned"], finished=db.get_run(run_ids[0])["created"], fingerprint=fp,
                           summary={"pass_rate": h["passed"] / h["planned"], "passed": h["passed"], "planned": h["planned"], "kind": "synthetic",
                                    "metrics": {"over_credit_rate": h["over_credit"], "under_credit_rate": h["under_credit"], "cost_usd": h["cost_usd"]},
                                    "notes": ["Real run of a real model on fictional cases, kept in evals/history.json."]})
    print(f"Seeded {settings.db_path}: {len(db.list_runs())} runs, {len(db.list_traces(1000))} traces.")


if __name__ == "__main__":
    main()
