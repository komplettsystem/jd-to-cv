"""The Models page: Bedrock stays out of sight until the server has AWS credentials, and each model's on/off switch sits
in its State column (a "Ready" badge alone looked like a switch)."""
from llm import catalog
from webfixtures import Browser, make_app


def page(tmp_path, monkeypatch, bedrock):
    app = make_app(tmp_path, monkeypatch)
    monkeypatch.setattr(catalog, "bedrock_configured", lambda settings: bedrock)
    return Browser(app, addr="127.0.0.1").get("/admin/models").data.decode()


def test_bedrock_rows_buttons_and_role_choices_are_hidden_without_aws_credentials(tmp_path, monkeypatch):
    html = page(tmp_path, monkeypatch, bedrock=False)
    assert "bedrock:" not in html
    assert 'value="bedrock"' not in html
    assert "hidden until" in html and "AWS" in html
    assert 'value="openrouter"' in html            # the other providers are untouched


def test_bedrock_shows_up_once_aws_credentials_exist(tmp_path, monkeypatch):
    html = page(tmp_path, monkeypatch, bedrock=True)
    assert "bedrock:" in html and 'value="bedrock"' in html
    assert "hidden until" not in html


def test_the_switch_is_in_the_state_column_and_says_on_or_off(tmp_path, monkeypatch):
    html = page(tmp_path, monkeypatch, bedrock=False)
    assert "<th>Switch</th>" not in html
    assert '>On</span>' in html and "Turn off" in html


def test_switching_a_model_on_or_off_refreshes_the_list_in_place_without_storing_anything(tmp_path, monkeypatch):
    from pathlib import Path
    html = page(tmp_path, monkeypatch, bedrock=False)
    assert 'id="models-table"' in html and 'action="/admin/models/toggle" data-refresh="#models-table"' in html
    js = (Path(__file__).resolve().parent.parent / "static" / "app.js").read_text()
    assert "data-refresh" in js and "replaceWith" in js and "sessionStorage" not in js and "localStorage" not in js
