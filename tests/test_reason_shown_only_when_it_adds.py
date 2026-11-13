"""Under a quoted CV line the explanation is shown only when the verdict is not 'met': for a met requirement the quote says it all,
and the explanation only retold it."""
from webfixtures import Browser, make_app, public_check, token_of


def test_a_met_row_with_its_quote_has_no_explanation_but_other_rows_keep_theirs(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    b = Browser(app, addr="203.0.113.241")
    token = token_of(public_check(b))
    db = app.extensions["svc"].db
    db.run("UPDATE items SET reason='REASON-' || id, override_verdict=NULL")
    rows = db.all("SELECT id, verdict, evidence FROM items")
    html = b.get(f"/r/{token}").data.decode()
    met = [r for r in rows if r["verdict"] == "met" and r["evidence"]]
    other = [r for r in rows if r["verdict"] != "met"]
    assert met and other
    for r in met:
        assert f"REASON-{r['id']}<" not in html
    for r in other:
        assert f"REASON-{r['id']}<" in html
