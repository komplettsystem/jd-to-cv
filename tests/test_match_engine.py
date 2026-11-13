"""The match engine is a small separate part with a message format of its own (JSON-RPC 2.0, shaped like the Model Context
Protocol's initialize, tools/list and tools/call). It owns the scoring; the chat bot only asks it."""
import json

import match
import match_engine

A = {"idx": 0, "text": "Owned a search product", "kind": "minimum", "verdict": "met", "reason": "r", "evidence": "", "section": ""}
B = {"idx": 1, "text": "A degree in computer science", "kind": "minimum", "verdict": "not_on_record", "reason": "r", "evidence": "", "section": ""}
C = {"idx": 2, "text": "Familiar with reranking", "kind": "desirable", "verdict": "partial", "reason": "r", "evidence": "", "section": ""}
ITEMS = [A, B, C]


def ask(method, params=None, rid=7, items=ITEMS):
    return match_engine.handle({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}, items)


def call(name, arguments=None):
    return ask("tools/call", {"name": name, "arguments": arguments or {}})["result"]


def test_initialize_names_the_engine_and_says_it_offers_tools():
    r = ask("initialize")
    assert r["jsonrpc"] == "2.0" and r["id"] == 7
    assert r["result"]["serverInfo"]["name"] == "match-engine" and "tools" in r["result"]["capabilities"]


def test_tools_list_describes_what_the_engine_can_do_with_input_schemas():
    tools = {t["name"]: t for t in ask("tools/list")["result"]["tools"]}
    assert set(tools) == {"rescore", "explain_scoring"}
    props = tools["rescore"]["inputSchema"]["properties"]
    assert set(props) == {"leave_out", "treat_as_met"} and props["leave_out"]["type"] == "array"
    assert tools["rescore"]["description"] and tools["explain_scoring"]["inputSchema"]["type"] == "object"


def test_rescore_answers_in_words_and_in_numbers():
    r = call("rescore", {"leave_out": [2]})
    assert r["isError"] is False
    assert r["content"][0] == {"type": "text", "text": "Match now: 50%. With requirement 2 left out: 83%."}
    assert r["structuredContent"] == {"match_now": 50.0, "match_after": 83.3, "left_out": [2], "treated_as_met": []}
    both = call("rescore", {"leave_out": [3], "treat_as_met": [2]})["content"][0]["text"]
    assert "requirement 3 left out" in both and "requirement 2 treated as met" in both and "100%" in both


def test_explain_scoring_gives_the_rules_the_engine_uses():
    r = call("explain_scoring")
    assert r["isError"] is False and r["content"][0]["text"] == match.scoring_rules()


def test_a_bad_number_or_an_unknown_tool_is_a_tool_error_not_a_crash():
    bad = call("rescore", {"leave_out": [9]})
    assert bad["isError"] is True and "no requirement 9" in bad["content"][0]["text"]
    assert call("rescore", {"leave_out": ["x"]})["isError"] is True
    assert call("rescore", {"leave_out": [True]})["isError"] is True
    unknown = call("delete_everything")
    assert unknown["isError"] is True and "Unknown tool" in unknown["content"][0]["text"]


def test_unknown_methods_and_malformed_requests_are_protocol_errors():
    assert ask("resources/list")["error"]["code"] == -32601
    assert match_engine.handle({"method": "tools/list"}, ITEMS)["error"]["code"] == -32600
    assert match_engine.handle("hello", ITEMS)["error"]["code"] == -32600


def test_the_messages_are_plain_json():
    json.dumps(ask("tools/list")), json.dumps(ask("tools/call", {"name": "rescore", "arguments": {"leave_out": [1]}}))
