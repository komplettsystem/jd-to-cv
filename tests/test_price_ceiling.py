"""Visitors' model: a price ceiling, and prices that cannot be trusted never count as a price."""
import io
import json
import re

from llm import catalog, pricing
from webfixtures import Browser, make_app

TERRA, SOL = "openrouter:openai/gpt-6.1-terra", "openrouter:openai/gpt-6.1-sol"


def test_a_variable_price_is_unknown_and_never_counts_as_negative_spend():
    # OpenRouter lists "price varies" as -1 per token; multiplied by tokens that would reduce the spend counters
    assert pricing.cost_usd("openrouter", "nvidia/switchyard", 1000, 1000, price_in=-1.0, price_out=-1.0) is None
    assert pricing.cost_usd("openrouter", "x/y", 1000, 1000, price_in=0.000001, price_out=0.000005) == 0.006


def test_the_openrouter_refresh_skips_models_with_a_variable_price(tmp_path, monkeypatch):
    db = make_app(tmp_path, monkeypatch).extensions["svc"].db
    listing = {"data": [
        {"id": "nvidia/variable-one", "name": "V", "context_length": 1000, "pricing": {"prompt": "-1", "completion": "-1"}},
        {"id": "nvidia/fixed-one", "name": "F", "context_length": 1000, "pricing": {"prompt": "0.0000001", "completion": "0.0000004"}}]}

    class Reply:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps(listing).encode()

    monkeypatch.setattr(catalog.urllib.request, "urlopen", lambda url, timeout=15: Reply())
    catalog.sync_openrouter(db)
    assert db.get_catalog("openrouter:nvidia/fixed-one") is not None
    assert db.get_catalog("openrouter:nvidia/variable-one") is None


def test_the_ceiling_admits_terra_at_exactly_one_and_five_dollars_and_refuses_sol_and_bad_prices(tmp_path, monkeypatch):
    svc = make_app(tmp_path, monkeypatch).extensions["svc"]
    row = lambda pin, pout, **kw: {"price_in": pin, "price_out": pout, "local_only": 0, **kw}
    assert svc.public_price_ok(row(0.000001, 0.000005))          # $1 / $5 per million: on the line
    assert svc.public_price_ok(row(0.0000001, 0.0000005))
    assert not svc.public_price_ok(row(0.000002, 0.00001))       # $2 / $10
    assert not svc.public_price_ok(row(0.0000001, 0.000006))     # only the output is too dear
    assert not svc.public_price_ok(row(None, None))              # no price: the caps could not count it
    assert not svc.public_price_ok(row(-1.0, -1.0))              # "price varies"
    svc.db.set_json("limits", {"public_price_ceiling_in": 3.0, "public_price_ceiling_out": 12.0})
    assert svc.public_price_ok(row(0.000002, 0.00001))           # the owner can move the ceiling


def options(html, field):
    block = re.search(rf'<select id="{field}".*?</select>', html, re.S).group(0)
    return re.findall(r'value="([^"]*)"', block)


def test_only_models_under_the_ceiling_can_be_chosen_to_answer_visitors(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    svc, admin = app.extensions["svc"], Browser(app, addr="127.0.0.1")
    svc.db.set_catalog_enabled(TERRA, True)
    svc.db.set_catalog_enabled(SOL, True)
    html = admin.get("/admin/models").data.decode()
    assert TERRA in options(html, "public_model") and SOL not in options(html, "public_model")
    assert SOL in options(html, "judge_model")                   # the other roles are not limited by it

    def save(model):
        return admin.c.post("/admin/models/roles", data={"_csrf": admin.token("/admin/models"), "public_model": model},
                            environ_base={"REMOTE_ADDR": "127.0.0.1"}, follow_redirects=True)
    assert b"costs more than the ceiling" in save(SOL).data
    assert svc.db.get_setting("public_model") != SOL
    save(TERRA)
    assert svc.db.get_setting("public_model") == TERRA


def test_a_public_model_above_a_lowered_ceiling_turns_visitor_checks_off(tmp_path, monkeypatch):
    svc = make_app(tmp_path, monkeypatch).extensions["svc"]
    svc.settings.public_cv_explicit = True
    svc.db.set_catalog_enabled(TERRA, True)
    svc.db.set_setting("public_model", TERRA)
    assert "ceiling" not in svc.public_admin_reason()
    svc.db.set_json("limits", {"public_price_ceiling_in": 0.5, "public_price_ceiling_out": 2.0})
    assert "ceiling" in svc.public_admin_reason()


def test_the_ceiling_can_be_changed_on_the_settings_page(tmp_path, monkeypatch):
    html = Browser(make_app(tmp_path, monkeypatch), addr="127.0.0.1").get("/admin/settings").data.decode()
    assert 'id="public_price_ceiling_in"' in html and 'id="public_price_ceiling_out"' in html
