"""The contact page (where to find the owner: LinkedIn and GitHub, no form, nothing stored) and the imprint."""
from flask import Blueprint, current_app, render_template

bp = Blueprint("contact", __name__)


@bp.get("/contact")
def contact():
    s = current_app.extensions["svc"].settings
    return render_template("contact.html", linkedin=s.linkedin_url, github=s.github_url)


@bp.get("/imprint")
def imprint():
    s = current_app.extensions["svc"].settings
    return render_template("imprint.html", name=s.legal_name or s.owner_name, address=[l for l in s.legal_address.split("\n") if l.strip()],
                           email=s.contact_email, linkedin=s.linkedin_url)
