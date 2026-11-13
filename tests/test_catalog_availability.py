"""Checking whether a Bedrock model is usable must not ask AWS for credentials once per model and per page load:
on a server without an AWS login each lookup takes about two seconds and the admin pages call it on every load."""
import time

import boto3

from config import Settings
from llm import catalog


class FakeSession:
    calls = []

    def __init__(self, **kwargs):
        FakeSession.calls.append(kwargs)

    def get_credentials(self):
        return None


def _entry():
    return {"provider": "bedrock", "model_name": "amazon.nova-lite-v1:0"}


def test_aws_credentials_are_looked_up_once_for_many_models_and_page_loads(monkeypatch):
    FakeSession.calls = []
    catalog._aws_cache.clear()
    monkeypatch.setattr(boto3, "Session", FakeSession)
    settings = Settings(aws_region="eu-central-1")
    for _ in range(8):
        usable, reason = catalog.availability(_entry(), settings)
        assert not usable and "no AWS credentials" in reason
    assert len(FakeSession.calls) == 1


def test_a_different_aws_profile_is_looked_up_separately_and_the_answer_expires(monkeypatch):
    FakeSession.calls = []
    catalog._aws_cache.clear()
    monkeypatch.setattr(boto3, "Session", FakeSession)
    catalog.availability(_entry(), Settings(aws_region="eu-central-1"))
    catalog.availability(_entry(), Settings(aws_region="eu-central-1", aws_profile="work"))
    assert len(FakeSession.calls) == 2
    later = time.time() + catalog.AWS_CHECK_SECONDS + 1
    monkeypatch.setattr(catalog.time, "time", lambda: later)
    catalog.availability(_entry(), Settings(aws_region="eu-central-1"))
    assert len(FakeSession.calls) == 3


def test_a_model_whose_provider_refuses_forced_tool_calls_answers_in_prompted_json(tmp_path):
    """meta/muse-ember through OpenRouter failed every report with 'tool_choice required is not supported'."""
    from db import DB
    db = DB(str(tmp_path / "c.db"))
    catalog.seed(db)
    assert db.get_catalog("openrouter:meta/muse-ember")["output_mode"] == "prompted"
    db.run("UPDATE catalog SET output_mode='tool', enabled=1 WHERE id='openrouter:meta/muse-ember'")   # a row stored earlier
    catalog.seed(db)
    row = db.get_catalog("openrouter:meta/muse-ember")
    assert row["output_mode"] == "prompted" and row["enabled"] == 1
    assert db.get_catalog("openrouter:openai/gpt-6.1-sol")["output_mode"] == "tool"


def test_qwen_models_also_answer_in_prompted_json(tmp_path):
    from db import DB
    db = DB(str(tmp_path / "q.db"))
    catalog.seed(db)
    db.upsert_catalog([{"id": "openrouter:qwen/qwen3.8-flash", "provider": "openrouter", "model_name": "qwen/qwen3.8-flash", "label": "Qwen",
                        "output_mode": "tool"}])
    catalog.seed(db)
    assert db.get_catalog("openrouter:qwen/qwen3.8-flash")["output_mode"] == "prompted"
