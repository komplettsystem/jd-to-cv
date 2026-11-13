"""Spend caps, the shortlist of models, and the separation of the owner's chat from a share link's."""
import json
import re
from datetime import datetime, timezone

from llm import catalog
from webfixtures import Browser, make_app, public_check, token_of
from test_match import POSTING


def add_trace(db, cost, *, billable=1, public=1, result_id=None, trace_id=None):
    tid = trace_id or f"{abs(hash((cost, billable, public, result_id))) :032x}"[:32]
    db.upsert_trace({"trace_id": tid, "name": "x", "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                     "duration_ms": 1, "status": "ok", "error": "", "subject_type": "result", "subject_id": result_id,
                     "model": "m", "input_tokens": 1, "output_tokens": 1, "cost_usd": cost, "model_requests": 1,
                     "tool_calls": 0, "public": public, "billable": billable})
    if result_id:
        db.set_result_trace(result_id, tid)
    return tid


def setup(tmp_path, monkeypatch, limits):
    app = make_app(tmp_path, monkeypatch)
    svc = app.extensions["svc"]
    svc.db.set_json("limits", limits)
    return app, svc


def test_the_monthly_cap_stops_visitors_without_naming_amounts_and_tells_the_owner_the_numbers(tmp_path, monkeypatch):
    app, svc = setup(tmp_path, monkeypatch, {"monthly_budget_usd": 1.0, "daily_budget_usd": 50})
    add_trace(svc.db, 1.5, public=0)  # the owner's own spend counts too
    r = public_check(Browser(app))
    assert r.status_code == 429 and b"paused until next month" in r.data and b"$" not in r.data.split(b"paused")[0][-200:]
    admin = Browser(app, addr="127.0.0.1")
    r = admin.post("/admin/new", {"posting_text": POSTING, "backends": ["cli:haiku"]})
    assert r.status_code == 400 and b"Monthly cap reached: $1.50 of $1.00" in r.data
    assert admin.post("/admin/new", {"posting_text": POSTING, "backends": ["baseline"]}).status_code == 302  # no model, no cost


def test_plan_and_local_usage_is_not_counted_against_the_monthly_cap(tmp_path, monkeypatch):
    app, svc = setup(tmp_path, monkeypatch, {"monthly_budget_usd": 1.0})
    add_trace(svc.db, 50.0, billable=0, public=0)  # the claude command reports API-equivalent cost, but a plan pays
    assert public_check(Browser(app)).status_code == 302
    assert svc.spend()["month"] == 0


def test_a_report_has_a_spend_cap_that_covers_chat_and_letters(tmp_path, monkeypatch):
    app, svc = setup(tmp_path, monkeypatch, {"session_budget_usd": 0.05, "monthly_budget_usd": 100, "daily_budget_usd": 100})
    visitor = Browser(app)
    token = token_of(public_check(visitor))
    run = svc.db.run_by_token(token)
    result = svc.db.results_for(run["id"])[0]
    add_trace(svc.db, 0.06, result_id=result["id"])  # the report itself used up the allowance
    r = visitor.post(f"/r/{token}/chat", json_body={"question": "Why?"})
    assert r.status_code == 429 and "limit for questions and drafts" in r.get_json()["error"]
    visitor.post(f"/r/{token}/letter", {"language": "English", "length": "short"})
    assert not svc.db.letters_for(run["id"])
    # another visitor's report is not affected
    other = Browser(app, addr="198.51.100.4")
    t2 = token_of(public_check(other))
    assert other.post(f"/r/{t2}/chat", json_body={"question": "Why?"}).status_code == 200


def test_a_model_with_a_price_is_billable_and_a_local_one_is_not(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    svc = app.extensions["svc"]
    rid = svc.db.create_run("T", "C", "", POSTING, "")
    for model, billable in (("openrouter:anthropic/claude-haiku-5.5", 1), ("cli:haiku", 0)):
        res = svc.db.add_result(rid, model)
        svc.run_fit(res)
        trace = svc.db.get_trace(svc.db.get_result(res)["trace_id"])
        assert trace["billable"] == billable, model
        assert (trace["cost_usd"] is not None) == bool(billable) or model == "cli:haiku"
    assert svc.spend()["month"] > 0


# --- the shortlist --------------------------------------------------------------------------------------

def test_the_catalog_has_aws_nvidia_and_one_cheap_model_and_no_fable(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    ids = [e["id"] for e in app.extensions["svc"].db.list_catalog()]
    assert not [i for i in ids if "fable" in i or "opus" in i]
    assert any(i.startswith("bedrock:amazon.nova") for i in ids)
    assert any(i.startswith("openrouter:nvidia/nemotron") for i in ids)
    assert "openrouter:anthropic/claude-haiku-5.5" in ids
    assert all(e["price_in"] is not None for e in app.extensions["svc"].db.list_catalog() if e["provider"] == "openrouter")


def test_an_old_catalog_is_replaced_and_an_excluded_model_loses_its_role(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    db = app.extensions["svc"].db
    db.upsert_catalog([{"id": "cli:fable", "provider": "cli", "model_name": "fable", "label": "old"}])
    db.set_setting("public_model", "cli:fable")
    db.set_setting("catalog_version", "1")
    catalog.seed(db)
    assert db.get_catalog("cli:fable") is None and db.get_setting("public_model") == ""


def test_bedrock_sync_lists_amazon_and_nvidia_models_and_skips_the_rest(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    svc = app.extensions["svc"]

    class FakeClient:
        def list_inference_profiles(self, **kw):
            return {"inferenceProfileSummaries": [
                {"inferenceProfileId": "eu.amazon.nova-lite-v1:0", "inferenceProfileName": "EU Nova Lite"},
                {"inferenceProfileId": "eu.anthropic.claude-opus-5-5-v1:0", "inferenceProfileName": "Opus"},
                {"inferenceProfileId": "eu.nvidia.nemotron-x-v1:0", "inferenceProfileName": "Nemotron"}]}

        def list_foundation_models(self, **kw):
            return {"modelSummaries": [
                {"modelId": "amazon.nova-micro-v1:0", "modelName": "Nova Micro", "providerName": "Amazon",
                 "inferenceTypesSupported": ["ON_DEMAND"]},
                {"modelId": "meta.llama-x", "modelName": "Llama", "providerName": "Meta", "inferenceTypesSupported": ["ON_DEMAND"]}]}

    class FakeSession:
        def __init__(self, **kw):
            pass

        def client(self, name):
            return FakeClient()

    import boto3
    monkeypatch.setattr(boto3, "Session", FakeSession)
    n = catalog.sync_bedrock(svc.db, svc.settings)
    ids = {e["id"] for e in svc.db.list_catalog()}
    assert n == 3 and "bedrock:eu.amazon.nova-lite-v1:0" in ids and "bedrock:eu.nvidia.nemotron-x-v1:0" in ids
    assert not any("opus" in i or "llama" in i for i in ids)
    assert svc.db.get_catalog("bedrock:eu.nvidia.nemotron-x-v1:0")["output_mode"] == "prompted"


# --- two audiences, never mixed --------------------------------------------------------------------------

def test_the_owners_chat_and_letters_never_reach_a_share_link(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    svc = app.extensions["svc"]
    admin, visitor = Browser(app, addr="127.0.0.1"), Browser(app)
    rid = int(admin.post("/admin/new", {"posting_text": POSTING, "backends": ["cli:haiku"]}).headers["Location"].split("/")[-1])
    admin.post(f"/admin/runs/{rid}/chat", json_body={"question": "OWNER-SECRET-QUESTION about retrieval", "backend": "cli:haiku"})
    admin.post(f"/admin/runs/{rid}/letter", {"backend": "cli:haiku", "instructions": "OWNER-LETTER-NOTE"})
    admin.post(f"/admin/runs/{rid}/share", {"action": "enable", "show_cv": "1"})  # chat and letter flags off
    token = svc.db.get_run(rid)["share_token"]
    page = visitor.get(f"/r/{token}").data.decode()
    state = visitor.get(f"/r/{token}/state").data.decode()
    for text in (page, state):
        assert "OWNER-SECRET-QUESTION" not in text and "Dear hiring team," not in text and "OWNER-LETTER-NOTE" not in text
    # now allow chat and letters: the visitor still sees none of the owner's, only their own
    admin.post(f"/admin/runs/{rid}/share", {"action": "enable", "show_cv": "1", "share_chat": "1", "share_letter": "1"})
    page = visitor.get(f"/r/{token}").data.decode()
    assert "OWNER-SECRET-QUESTION" not in page and "Dear hiring team," not in page
    visitor.post(f"/r/{token}/chat", json_body={"question": "VISITOR-QUESTION about retrieval"})
    visitor.post(f"/r/{token}/letter", {"language": "English", "length": "short"})
    page = visitor.get(f"/r/{token}").data.decode()
    assert "VISITOR-QUESTION" in page and "OWNER-SECRET-QUESTION" not in page and "Dear hiring team," in page
    # the owner sees their own thread and, separately, the visitor's
    run_page = admin.get(f"/admin/runs/{rid}").data.decode()
    assert "OWNER-SECRET-QUESTION" in run_page


def test_the_downloaded_letter_says_software_wrote_it(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    visitor = Browser(app)
    token = token_of(public_check(visitor))
    visitor.post(f"/r/{token}/letter", {"language": "English", "length": "short"})
    lid = re.search(r"/letter/(\d+)\.txt", visitor.get(f"/r/{token}").data.decode()).group(1)
    txt = visitor.get(f"/r/{token}/letter/{lid}.txt").data.decode()
    assert txt.startswith("Draft written by software from Ada Example's CV on ") and "Not written or sent by Ada Example." in txt


def test_limiter_keys_are_hashes_not_addresses(tmp_path, monkeypatch):
    import web.auth as auth
    app = make_app(tmp_path, monkeypatch)
    public_check(Browser(app, addr="203.0.113.77"))
    assert auth._hits and not any("203.0.113.77" in k for k in auth._hits)
