"""View models shared by the admin run page and the public report page."""
import json

import markup
from llm import matcher
from llm import overall as overall_mod
import match
import pool as pool_mod


def _decorate(items):
    for it in items:
        it["eff_verdict"] = match.effective_verdict(it)
        it["eff_weight"] = match.effective_weight(it)
        it["overridden"] = it["override_verdict"] is not None or it["override_weight"] is not None
    return items


def _decorate_terms(items):
    for it in items:
        it["eff_verdict"] = match.term_verdict(it)
        it["overridden"] = it["override_verdict"] is not None
    return items


def hide_cv_lines(exchanges):
    """What the chat bot asked the matcher, for a visitor who may not see the CV: each statement keeps its verdict and reason, but the
    CV line and its section go, in the readable text and in the raw message. Exchanges with the match engine hold no CV text."""
    out = []
    for ex in exchanges:
        res = (ex.get("response") or {}).get("result") or {}
        results = (res.get("structuredContent") or {}).get("results")
        if ex.get("to") != "matcher" or results is None:
            out.append(ex)
            continue
        bare = [{**r, "line": "", "section": ""} for r in results]
        text = "\n".join(f"{n}. {r['statement']} -> {matcher.LABELS.get(r['verdict'], r['verdict'])}. Reason: {r['reason']}"
                         for n, r in enumerate(bare, 1))
        out.append({**ex, "response": {**ex["response"], "result": {**res, "content": [{"type": "text", "text": text}],
                                                                    "structuredContent": {"results": bare}}}})
    return out


def _overall(svc, run, primary, admin, show_cv):
    """The overall reading of the primary report, with the CV lines it cites looked up. A visitor sees the lines'
    text only when the owner shares the CV; otherwise only how many lines each point rests on. A failed reading
    shows its error text to the owner and nothing to a visitor."""
    raw = (primary or {}).get("overall") or ""
    if not raw:
        return None
    data = json.loads(raw)
    if "error" in data:
        return {"error": data["error"] if admin else ""}
    lines = {c["id"]: c for c in svc._chunks(run, run["source"] == "public")}
    for p in data["points"]:
        p["lines"] = [{"section": lines[e]["section"], "text": lines[e]["text"] if (admin or show_cv) else ""}
                      for e in p["evidence_ids"] if e in lines]
    data["label"] = overall_mod.LEVEL_LABELS.get(data["level"], data["level"])
    return data


def report_context(svc, run, *, admin, show_cv, can_chat, can_letter):
    """Everything a report page needs. `admin` adds terms, pool entry, traces; the public page gets less."""
    db = svc.db
    fit = []
    for r in db.results_for(run["id"], "fit"):
        items = _decorate(db.items_for(r["id"])) if r["status"] == "done" else []
        r["items"] = items
        r["score"] = match.score(items) if items else None
        r["gap"] = match.minimum_gap(items) if items else (0, 0)
        fit.append(r)
    if not admin:  # a fact the owner added for his own runs is not quoted to the holder of a share link
        for r in fit:
            for it in r["items"]:
                if (it.get("chunk_id") or "").startswith("x"):
                    it["evidence"], it["section"], it["reason"] = "", "", "Rests on a fact that is not shared on this link."
    primary = next((r for r in reversed(fit) if r["status"] == "done"), None)
    terms = []
    if admin:
        for r in db.results_for(run["id"], "terms"):
            items = _decorate_terms(db.term_items_for(r["id"])) if r["status"] == "done" else []
            r["items"] = items
            r["score"] = match.term_score(items) if items else None
            r["conflicts"] = match.term_conflicts(items) if items else []
            terms.append(r)
    steps = db.pool_for(run["id"])
    funnel = pool_mod.funnel(steps)
    pool_visible = admin or bool(funnel)
    # Two audiences never mix: the owner's own chat and letters, and those of whoever holds a share link.
    audience = "owner" if admin else "shared"
    letters = db.letters_for(run["id"], audience) if (admin or can_letter) else []
    for l in letters:
        if not admin:
            l.pop("trace_id", None)
    chat = db.chat_for_run(run["id"], audience) if (admin or can_chat) else None
    messages = db.messages_for(chat["id"]) if chat else []
    if not admin:
        for l in letters:  # a draft that failed the checks is never shown; its error text is internal
            if l["status"] != "done":
                l["content"], l["paragraphs"], l["error"] = "", [], ""
        if not show_cv:
            for m in messages:
                m["evidence"] = []
                m["exchanges"] = hide_cv_lines(m["exchanges"])
    overall = _overall(svc, run, primary, admin, show_cv)
    shared_chat = db.chat_for_run(run["id"], "shared") if admin else None
    shared_messages = db.messages_for(shared_chat["id"]) if shared_chat else []
    shared_letters = db.letters_for(run["id"], "shared") if admin else []
    busy = (any(r["status"] in ("queued", "running") for r in fit + terms)
            or any(l["status"] in ("queued", "running") for l in letters)
            or any(m["status"] == "pending" for m in messages))
    return {
        "run": run, "fit": fit, "primary": primary, "terms": terms, "overall": overall,
        "marked": markup.mark_posting(run["posting_text"], primary["items"]) if primary else [],
        "pool": {"steps": steps, "funnel": funnel, "rarity": pool_mod.rarity(steps)} if pool_visible else None,
        "letters": letters, "messages": messages, "chat": chat, "busy": busy,
        "shared_messages": shared_messages, "shared_letters": shared_letters,
        "suggestions": __import__("llm.chat", fromlist=["suggest_questions"]).suggest_questions(
            primary["items"], asked=[m["content"] for m in messages if m["role"] == "user"]) if primary else [],
        "show_cv": show_cv, "can_chat": can_chat and primary is not None, "can_letter": can_letter and primary is not None,
        "admin": admin,
    }


def state_payload(svc, run, after_id, *, admin):
    """What the page's script polls: statuses, letters and chat messages newer than `after_id`. A share link's
    state holds only the shared audience's chat and letters, and only when the owner switched them on."""
    db = svc.db
    audience = "owner" if admin else "shared"
    results = [{"id": r["id"], "kind": r["kind"], "status": r["status"], "error": r["error"] if admin else ""}
               for r in db.results_for(run["id"])]
    letters = [{"id": l["id"], "status": l["status"], "error": l["error"] if admin else "",
                "content": l["content"] if admin or l["status"] == "done" else "",
                "paragraphs": l["paragraphs"] if admin or l["status"] == "done" else [], "ok": l["checks"].get("ok"),
                "warnings": l["checks"].get("warnings", []) if admin else [],
                "errors": l["checks"].get("errors", []) if admin else []}
               for l in (db.letters_for(run["id"], audience) if (admin or run["share_letter"]) else [])]
    chat = db.chat_for_run(run["id"], audience) if (admin or run["share_chat"]) else None
    messages = []
    if chat:
        for m in db.messages_for(chat["id"], after_id):
            messages.append({"id": m["id"], "role": m["role"], "content": m["content"], "status": m["status"],
                             "evidence": m["evidence"] if (admin or run["share_show_cv"]) else [],
                             "suggestions": m["suggestions"], "mode": m["mode"],
                             "exchanges": m["exchanges"] if (admin or run["share_show_cv"]) else hide_cv_lines(m["exchanges"]),
                             "error": m["error"] if admin else "", "trace_id": m["trace_id"] if admin else None})
    pending = any(m["status"] == "pending" for m in db.messages_for(chat["id"])) if chat else False
    busy = (any(r["status"] in ("queued", "running") for r in results)
            or any(l["status"] in ("queued", "running") for l in letters) or pending)
    return {"busy": busy, "results": results, "letters": letters, "messages": messages,
            "title": run["title"] or "", "company": run["company"] or ""}
