"""The match engine: the part of the app that owns the scoring, with a message format of its own.

Other parts (today the chat bot) do not know the formula and do not calculate. They send the engine a message and use what
it answers. The messages are JSON-RPC 2.0, shaped like the tool messages of the Model Context Protocol (initialize,
tools/list, tools/call), so the same engine could later be offered to outside agents as an MCP server.

The engine is plain code, not a language model: a recalculation is arithmetic, and a model would only add the chance of a
wrong number. It serves one report at a time: `items` are that report's requirements with their verdicts."""
import match

PROTOCOL_VERSION = "2025-06-18"
SERVER = {"name": "match-engine", "version": "1"}

_NUMBERS = {"type": "array", "items": {"type": "integer", "minimum": 1},
            "description": "Requirement numbers as the report shows them: counted from 1, minimum requirements first."}
TOOLS = [
    {"name": "rescore",
     "description": "Recalculate the match percentage with some requirements left out and/or treated as met. Returns the match "
                    "now and after the change.",
     "inputSchema": {"type": "object",
                     "properties": {"leave_out": {**_NUMBERS, "description": "Requirements to leave out of the score. " + _NUMBERS["description"]},
                                    "treat_as_met": {**_NUMBERS, "description": "Requirements to count as met. " + _NUMBERS["description"]}}}},
    {"name": "explain_scoring",
     "description": "How the match percentage is calculated: weights, what each verdict is worth, and the formula.",
     "inputSchema": {"type": "object", "properties": {}}},
]


def _ok(rid, result):
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _error(rid, code, message):
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def _tool_result(text, ok=True, structured=None):
    result = {"content": [{"type": "text", "text": text}], "isError": not ok}
    if structured is not None:
        result["structuredContent"] = structured
    return result


def _numbers(arguments, key):
    raw = arguments.get(key) or []
    if not isinstance(raw, list) or any(not isinstance(n, int) or isinstance(n, bool) for n in raw):
        raise ValueError(f"{key} must be a list of requirement numbers")
    return tuple(dict.fromkeys(raw))


def _percent(value):
    return "no score (nothing left to weigh)" if value is None else f"{value:.0f}%"


def _rescore(items, arguments):
    try:
        leave_out, met = _numbers(arguments, "leave_out"), _numbers(arguments, "treat_as_met")
        now, after = match.rescore(items, leave_out, met)
    except ValueError as e:
        return _tool_result(str(e), ok=False)
    changes = []
    if leave_out:
        changes.append("requirement " + ", ".join(map(str, leave_out)) + " left out")
    if met:
        changes.append("requirement " + ", ".join(map(str, met)) + " treated as met")
    text = f"Match now: {_percent(now)}. With {' and '.join(changes) or 'no change'}: {_percent(after)}."
    return _tool_result(text, structured={
        "match_now": None if now is None else round(now, 1), "match_after": None if after is None else round(after, 1),
        "left_out": list(leave_out), "treated_as_met": list(met)})


def _call(params, items):
    name, arguments = params.get("name"), params.get("arguments") or {}
    if not isinstance(arguments, dict):
        return _tool_result("arguments must be an object", ok=False)
    if name == "rescore":
        return _rescore(items, arguments)
    if name == "explain_scoring":
        return _tool_result(match.scoring_rules())
    return _tool_result(f"Unknown tool {name!r}. Known tools: " + ", ".join(t["name"] for t in TOOLS), ok=False)


def handle(request, items):
    """One JSON-RPC message in, one out."""
    if not isinstance(request, dict) or request.get("jsonrpc") != "2.0" or not isinstance(request.get("method"), str):
        return _error(request.get("id") if isinstance(request, dict) else None, -32600, "Invalid request")
    rid, method = request.get("id"), request["method"]
    params = request.get("params") if isinstance(request.get("params"), dict) else {}
    if method == "initialize":
        return _ok(rid, {"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {}}, "serverInfo": SERVER})
    if method == "tools/list":
        return _ok(rid, {"tools": TOOLS})
    if method == "tools/call":
        return _ok(rid, _call(params, items))
    return _error(rid, -32601, f"Method not found: {method}")
