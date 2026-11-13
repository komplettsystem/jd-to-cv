"""Numbers for the admin dashboards. Everything is computed from the tables on each request: the data is small."""
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import match
from llm import overall


def _pct(sorted_values, q):
    if not sorted_values:
        return None
    return sorted_values[min(len(sorted_values) - 1, int(round(q * (len(sorted_values) - 1))))]


def fit_scores(db):
    """{result_id: {'score', 'gap', 'items'}} for every finished fit result."""
    out = {}
    for r in db.all_results("fit"):
        if r["status"] == "done":
            items = db.items_for(r["id"])
            out[r["id"]] = {"score": match.score(items), "gap": match.minimum_gap(items), "items": items}
    return out


def term_scores(db):
    out = {}
    for r in db.all_results("terms"):
        if r["status"] == "done":
            items = db.term_items_for(r["id"])
            out[r["id"]] = {"score": match.term_score(items), "conflicts": len(match.term_conflicts(items))}
    return out


def _overall(raw):
    """(label, percent) of a stored overall reading: Little overlap 25, A stretch 50, Good fit 75, Strong fit 100. (None, None) when there
    is none or it failed."""
    try:
        level = json.loads(raw or "{}").get("level")
    except (ValueError, AttributeError):
        return None, None
    if level not in overall.LEVELS:
        return None, None
    return overall.LEVEL_LABELS[level], (len(overall.LEVELS) - overall.LEVELS.index(level)) * 25


def run_scores(db):
    """Per run: the latest finished fit score and terms score, for the lists and the quadrant."""
    fits, terms = fit_scores(db), term_scores(db)
    per = defaultdict(dict)
    for r in db.all_results():
        if r["id"] in fits and r["kind"] == "fit":
            per[r["run_id"]]["fit"] = fits[r["id"]]["score"]
            per[r["run_id"]]["gap"] = fits[r["id"]]["gap"]
            per[r["run_id"]]["fit_backend"] = r["backend"]
            per[r["run_id"]]["overall"], per[r["run_id"]]["overall_pct"] = _overall(r.get("overall"))
        if r["id"] in terms and r["kind"] == "terms":
            per[r["run_id"]]["terms"] = terms[r["id"]]["score"]
            per[r["run_id"]]["conflicts"] = terms[r["id"]]["conflicts"]
    return per


def _plain(text):
    return " ".join((text or "").lower().split())


def posting_keys(run):
    """What makes two runs the same posting: the same company and title, or the same text. A link and a PDF file name are not kept (the
    privacy page says so), so the text, which a link or a file gives again, stands in for them."""
    keys = set()
    company, title = _plain(run.get("company")), _plain(run.get("title"))
    if company and title:
        keys.add(("name", company, title))
    text = _plain(run.get("posting_text"))
    if text:
        keys.add(("text", hashlib.sha256(text.encode()).hexdigest()))
    return keys


def latest_per_posting(runs):
    """`runs` newest first. One run per posting: the newest."""
    seen, kept = set(), []
    for run in runs:
        keys = posting_keys(run)
        if keys & seen:
            continue
        seen |= keys
        kept.append(run)
    return kept


def quadrant(db):
    """Points for the both-ways chart: x = how well the candidate fits the posting, y = how well the posting fits the
    candidate's terms. Only runs that have both."""
    scores = run_scores(db)
    pts = []
    complete = [r for r in db.list_runs() if scores.get(r["id"], {}).get("fit") is not None and scores.get(r["id"], {}).get("terms") is not None]
    for run in latest_per_posting(complete):   # a posting run several times is one point: the latest run
        s = scores[run["id"]]
        pts.append({"run_id": run["id"], "label": run["company"] or run["title"] or f"Run {run['id']}",
                    "x": s["fit"], "y": s["terms"], "decision": run["human_verdict"],
                    "conflicts": s.get("conflicts", 0), "public": run["source"] == "public", "overall": s.get("overall")})
    return pts


def model_stats(db):
    fits = fit_scores(db)
    traces = {t["trace_id"]: t for t in db.all("SELECT * FROM traces")}
    evals = defaultdict(list)
    for e in db.eval_runs():
        if e["status"] == "done" and e["summary"].get("pass_rate") is not None:
            evals[e["backend"]].append(e["summary"]["pass_rate"])
    result_backend = {r["id"]: r["backend"] for r in db.all_results()}
    arena = defaultdict(lambda: {"wins": 0, "losses": 0, "ties": 0})
    for v in db.votes():
        a, b = result_backend.get(v["result_a"]), result_backend.get(v["result_b"])
        if not a or not b:
            continue
        if v["winner"] == "tie":
            arena[a]["ties"] += 1
            arena[b]["ties"] += 1
        else:
            win, lose = (a, b) if v["winner"] == "a" else (b, a)
            arena[win]["wins"] += 1
            arena[lose]["losses"] += 1
    rows = {}
    for r in db.all_results():
        s = rows.setdefault(r["backend"], {"backend": r["backend"], "results": 0, "done": 0, "failed": 0, "scores": [],
                                           "seconds": [], "tokens_in": 0, "tokens_out": 0, "cost": None,
                                           "reviewed": 0, "n_items": 0, "unchanged": 0})
        s["results"] += 1
        s["failed"] += r["status"] == "failed"
        if r["status"] != "done":
            continue
        s["done"] += 1
        if r["seconds"]:
            s["seconds"].append(r["seconds"])
        t = traces.get(r["trace_id"])
        if t:
            s["tokens_in"] += t["input_tokens"]
            s["tokens_out"] += t["output_tokens"]
            if t["cost_usd"] is not None:
                s["cost"] = (s["cost"] or 0.0) + t["cost_usd"]
        if r["kind"] == "fit" and r["id"] in fits:
            if fits[r["id"]]["score"] is not None:
                s["scores"].append(fits[r["id"]]["score"])
            if r["reviewed"]:
                items = fits[r["id"]]["items"]
                s["reviewed"] += 1
                s["n_items"] += len(items)
                s["unchanged"] += sum(1 for i in items if match.effective_verdict(i) == i["verdict"]
                                      and i["override_weight"] is None)
    out = []
    for s in sorted(rows.values(), key=lambda x: x["backend"]):
        sec = sorted(s["seconds"])
        s["mean_score"] = sum(s["scores"]) / len(s["scores"]) if s["scores"] else None
        s["p50"], s["p95"] = _pct(sec, 0.5), _pct(sec, 0.95)
        s["failure_rate"] = s["failed"] / s["results"] if s["results"] else None
        s["agreement"] = s["unchanged"] / s["n_items"] if s["n_items"] else None
        s["eval_pass_rate"] = sum(evals[s["backend"]]) / len(evals[s["backend"]]) if evals[s["backend"]] else None
        s["arena"] = dict(arena[s["backend"]]) if s["backend"] in arena else None
        out.append(s)
    return out


def trend(db, days=14):
    """Per day for the last `days`: runs created, model requests, spend (USD, None-safe)."""
    today = datetime.now(timezone.utc).date()
    labels = [(today - timedelta(days=d)).isoformat() for d in range(days - 1, -1, -1)]
    runs, spend, requests = defaultdict(int), defaultdict(float), defaultdict(int)
    for r in db.list_runs():
        runs[r["created"][:10]] += 1
    for t in db.all("SELECT started, cost_usd, model_requests FROM traces"):
        spend[t["started"][:10]] += t["cost_usd"] or 0.0
        requests[t["started"][:10]] += t["model_requests"]
    return [{"day": d, "runs": runs[d], "spend": spend[d], "requests": requests[d]} for d in labels]


def overview(db):
    runs = db.list_runs()
    traces = db.all("SELECT * FROM traces")
    since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
    day_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0).isoformat(timespec="seconds")
    msgs = db.one("SELECT COUNT(*) n FROM chat_messages WHERE role='user'")["n"]
    letters_n = db.one("SELECT COUNT(*) n FROM letters WHERE status='done'")["n"]
    durations = sorted(t["duration_ms"] / 1000 for t in traces)
    return {
        "runs": len(runs), "public_runs": sum(1 for r in runs if r["source"] == "public"),
        "results": len(db.all_results()), "traces": len(traces),
        "errors_24h": sum(1 for t in traces if t["status"] == "error" and t["started"] >= since),
        "chat_questions": msgs, "letters": letters_n,
        "spend_total": sum(t["cost_usd"] or 0.0 for t in traces),
        "spend_today": sum(t["cost_usd"] or 0.0 for t in traces if t["started"] >= day_start),
        "public_spend_today": db.public_spend_since(day_start),
        "tokens": sum(t["input_tokens"] + t["output_tokens"] for t in traces),
        "latency_p50": _pct(durations, 0.5), "latency_p95": _pct(durations, 0.95),
        "shared": sum(1 for r in runs if r["share_token"]),
    }


def _messages(raw):
    """gen_ai.input.messages / output.messages are JSON strings; return them as a list of {role, text, parts}."""
    import json
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return []
    out = []
    for m in data or []:
        parts = []
        for p in m.get("parts", []):
            kind = p.get("type", "text")
            if kind == "text":
                parts.append({"type": "text", "text": p.get("content", "")})
            elif kind == "tool_call":
                parts.append({"type": "tool_call", "text": f"{p.get('name')}({p.get('arguments')})"})
            elif kind == "tool_call_response":
                parts.append({"type": "tool_result", "text": str(p.get("result", ""))})
            else:
                parts.append({"type": kind, "text": str(p.get("content", p))})
        out.append({"role": m.get("role", ""), "parts": parts})
    return out


def trace_view(db, trace_id):
    """A trace as a waterfall: spans in start order with depth, offset and duration (ms) and readable messages."""
    trace = db.get_trace(trace_id)
    if not trace:
        return None
    spans = db.spans_for(trace_id)
    if not spans:
        return {"trace": trace, "spans": [], "total_ms": 0}
    t0 = min(s["start_ns"] for s in spans)
    t1 = max(s["end_ns"] for s in spans)
    by_id = {s["span_id"]: s for s in spans}

    def depth(s):
        d, p = 0, s["parent_id"]
        while p and p in by_id:
            d, p = d + 1, by_id[p]["parent_id"]
        return d

    out = []
    for s in spans:
        a = s["attrs"]
        out.append({
            **s, "depth": depth(s), "offset_ms": (s["start_ns"] - t0) / 1e6, "duration_ms": (s["end_ns"] - s["start_ns"]) / 1e6,
            "model": a.get("gen_ai.request.model", ""), "tokens_in": a.get("gen_ai.usage.input_tokens"),
            "tokens_out": a.get("gen_ai.usage.output_tokens"), "tool": a.get("gen_ai.tool.name", ""),
            "input": _messages(a.get("gen_ai.input.messages")), "output": _messages(a.get("gen_ai.output.messages")),
            "system": _messages(a.get("gen_ai.system_instructions")), "error": a.get("_exception", ""),
            "final_result": a.get("final_result", ""),
        })
    return {"trace": trace, "spans": out, "total_ms": (t1 - t0) / 1e6}
