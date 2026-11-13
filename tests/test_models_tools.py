"""A model without tool calls must be visible as such: Ollama models are marked from what Ollama says they can do, and the Models
page says what such a model loses and which similar model has tool calls."""
import io
import json

from config import Settings
from llm import catalog
from webfixtures import Browser, make_app

NOTICE = "No tool calls: the chat answers from fewer CV lines and cannot ask the matcher or the scoring service."


class Reply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_ollama(monkeypatch, shown):
    """`shown` maps a model name to its /api/show body, or to an exception to raise."""
    calls = []

    def urlopen(req, timeout=None):
        url = req if isinstance(req, str) else req.full_url
        calls.append((url, None if isinstance(req, str) else req.data))
        if url.endswith("/api/tags"):
            return Reply(json.dumps({"models": [{"name": n} for n in shown]}).encode())
        name = json.loads(req.data)["model"]
        body = shown[name]
        if isinstance(body, Exception):
            raise body
        return Reply(json.dumps(body).encode())
    monkeypatch.setattr(catalog.urllib.request, "urlopen", urlopen)
    catalog._ollama_cache.update(at=0.0, tags=[])
    return calls


def test_an_ollama_model_has_tool_calls_only_when_ollama_lists_the_tools_capability(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    db = app.extensions["svc"].db
    calls = fake_ollama(monkeypatch, {"llama3.1:8b": {"capabilities": ["completion", "tools"]},
                                      "gemma:2b": {"capabilities": ["completion"]},
                                      "old-model:1b": {"details": {}},                       # an older Ollama: no capabilities field
                                      "broken:1b": OSError("down")})
    assert catalog.sync_ollama(db, Settings(ollama_url="http://localhost:11434")) == 4
    tools = {r["model_name"]: r["tools"] for r in db.list_catalog() if r["provider"] == "ollama"}
    assert tools == {"llama3.1:8b": 1, "gemma:2b": 0, "old-model:1b": 0, "broken:1b": 0}
    assert any(u.endswith("/api/show") and json.loads(d) == {"model": "gemma:2b"} for u, d in calls)


def test_a_refresh_corrects_a_model_that_was_stored_without_tools(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    db = app.extensions["svc"].db
    row = {"id": "ollama:llama3.1:8b", "provider": "ollama", "model_name": "llama3.1:8b", "label": "x", "tools": 0, "local_only": 1}
    db.upsert_catalog([row])
    fake_ollama(monkeypatch, {"llama3.1:8b": {"capabilities": ["tools"]}})
    catalog.sync_ollama(db, Settings(ollama_url="http://localhost:11434"))
    assert db.get_catalog("ollama:llama3.1:8b")["tools"] == 1


def test_similar_models_with_tools_name_the_same_maker_first_then_the_cheapest(tmp_path, monkeypatch):
    rows = [
        {"id": "cli:haiku", "provider": "cli", "model_name": "haiku", "tools": 0, "enabled": 1, "price_in": None, "label": "Claude haiku (local)"},
        {"id": "ollama:llama3.1:8b", "provider": "ollama", "model_name": "llama3.1:8b", "tools": 0, "enabled": 1, "price_in": None, "label": "Ollama llama3.1:8b"},
        {"id": "openrouter:x/cheap", "provider": "openrouter", "model_name": "x/cheap", "tools": 1, "enabled": 1, "price_in": 1e-8, "label": "Cheap"},
        {"id": "openrouter:anthropic/claude-haiku-5.5", "provider": "openrouter", "model_name": "anthropic/claude-haiku-5.5", "tools": 1, "enabled": 1, "price_in": 1e-7, "label": "Claude Haiku 5.5"},
        {"id": "openrouter:meta-llama/llama-3.1-8b", "provider": "openrouter", "model_name": "meta-llama/llama-3.1-8b", "tools": 1, "enabled": 0, "price_in": 5e-8, "label": "Llama 3.1 8B"},
        {"id": "openrouter:y/mid", "provider": "openrouter", "model_name": "y/mid", "tools": 1, "enabled": 1, "price_in": 5e-7, "label": "Mid"},
        {"id": "openrouter:z/varies", "provider": "openrouter", "model_name": "z/varies", "tools": 1, "enabled": 1, "price_in": -1, "label": "Varies"},
        {"id": "baseline", "provider": "baseline", "model_name": "baseline", "tools": 0, "enabled": 1, "price_in": None, "label": "Baseline"}]
    alt = catalog.tool_alternatives(rows)
    assert [m["id"] for m in alt["cli:haiku"]] == ["openrouter:anthropic/claude-haiku-5.5", "openrouter:x/cheap"]
    assert [m["id"] for m in alt["ollama:llama3.1:8b"]] == ["openrouter:meta-llama/llama-3.1-8b", "openrouter:x/cheap"]
    assert [m["id"] for m in alt["baseline"]] == ["openrouter:x/cheap", "openrouter:anthropic/claude-haiku-5.5"]     # no maker to match: the cheapest switched on
    assert set(alt) == {"cli:haiku", "ollama:llama3.1:8b", "baseline"}                          # only models without tools get a suggestion


def page(tmp_path, monkeypatch, **settings):
    app = make_app(tmp_path, monkeypatch)
    monkeypatch.setattr(catalog, "bedrock_configured", lambda s: False)
    db = app.extensions["svc"].db
    for k, v in settings.items():
        db.set_setting(k, v)
    return app, Browser(app, addr="127.0.0.1").get("/admin/models").data.decode()


def test_every_model_without_tool_calls_says_so_in_the_catalog_and_names_a_similar_one_with_tools(tmp_path, monkeypatch):
    app, html = page(tmp_path, monkeypatch)
    assert html.count(NOTICE) >= 3                     # the baseline and the two local `claude` rows
    assert "Similar with tool calls:" in html and "Claude Haiku 5.5" in html.split("Claude haiku")[1]
    assert "—" not in html.split('id="models-table"')[1].split("</table>")[0]


def test_the_notice_shows_under_a_role_whose_chosen_model_has_no_tool_calls(tmp_path, monkeypatch):
    app, html = page(tmp_path, monkeypatch, chat_model="cli:haiku")
    role = html.split('id="chat_model-hint"')[1].split("</div>")[0]
    assert NOTICE in role
    app, html = page(tmp_path, monkeypatch, chat_model="openrouter:anthropic/claude-haiku-5.5")
    assert NOTICE not in html.split('id="chat_model-hint"')[1].split("</div>")[0]


def test_an_empty_chat_role_follows_the_model_that_answers_visitors(tmp_path, monkeypatch):
    app, html = page(tmp_path, monkeypatch, chat_model="", public_model="cli:sonnet")
    assert NOTICE in html.split('id="chat_model-hint"')[1].split("</div>")[0]


def test_an_ollama_row_without_tools_points_to_ollamas_own_list(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    app.extensions["svc"].db.upsert_catalog([{"id": "ollama:gemma:2b", "provider": "ollama", "model_name": "gemma:2b", "label": "Ollama gemma:2b (local)",
                                              "tools": 0, "local_only": 1}])
    monkeypatch.setattr(catalog, "bedrock_configured", lambda s: False)
    html = Browser(app, addr="127.0.0.1").get("/admin/models").data.decode()
    assert "Ollama lists the models that support tool calls at ollama.com/search?c=tool." in html
