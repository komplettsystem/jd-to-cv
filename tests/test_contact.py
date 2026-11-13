"""The contact page points to the owner's profiles; it has no form and stores nothing."""
from webfixtures import Browser, make_app


def test_the_contact_page_links_to_the_profiles_and_has_no_form(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, linkedin_url="https://www.linkedin.com/in/ada-example",
                   github_url="https://github.com/ada-example", contact_email="hello@example.org")
    page = Browser(app).get("/contact").data.decode()
    assert "linkedin.com/in/ada-example" in page and "github.com/ada-example" in page and "hello@example.org" in page
    assert "<form" not in page.split("<main>")[1].split("</main>")[0] and "+39" not in page
    assert Browser(app).post("/contact", {"name": "x"}).status_code in (404, 405)


def test_an_unconfigured_contact_page_says_so(tmp_path, monkeypatch):
    page = Browser(make_app(tmp_path, monkeypatch)).get("/contact").data.decode()
    assert "No contact details are set up yet" in page


def test_the_imprint_names_the_responsible_person_and_the_privacy_page_points_to_it(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, legal_name="Jo Example", legal_address="1 Sample Street\n20100 Milan\nItaly",
                   contact_email="hello@example.org")
    page = Browser(app).get("/imprint").data.decode()
    assert "Jo Example" in page and "1 Sample Street<br>20100 Milan<br>Italy" in page and "hello@example.org" in page
    privacy = Browser(app).get("/privacy").data.decode()
    assert "Jo Example, a private person, is responsible" in privacy and "/imprint" in privacy
    assert "/imprint" in Browser(app).get("/").data.decode()  # linked from every page


def test_an_imprint_without_an_address_says_so(tmp_path, monkeypatch):
    page = Browser(make_app(tmp_path, monkeypatch)).get("/imprint").data.decode()
    assert "Not set up yet" in page
