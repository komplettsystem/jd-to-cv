"""Checking a report for an uploaded CV: consent first, contact details removed, retention honoured, the CV used is the
uploaded one, and everything goes when the report is deleted."""
import io
from datetime import datetime, timedelta, timezone

import cvfile
from webfixtures import Browser, PUBLIC_POSTING, make_app, old_ts, token_of

CV = """# Jo Example

Contact: jo.example@example.com, +49 170 5550123, Hamburg

## Experience

- Led product teams at Zebrafish Analytics from 2013 - 2019 and ran retrieval evaluation with the data team.
- Built a self-serve onboarding flow at Zebrafish Analytics that cut setup from days to minutes.
- Wrote the public API documentation and ran weekly calls with twelve customers.
"""


CV_FOR_PDF = ["Jo Example - Product Manager", "Led product teams at Zebrafish Analytics from 2013 to 2019.",
              "Ran retrieval evaluation with the data team and wrote the public API documentation.",
              "Built a self-serve onboarding flow that cut setup from days to minutes."]


def make_pdf(lines):
    """A minimal one-page PDF with the lines as text, so the test needs no file and no PDF library."""
    content = "BT /F1 11 Tf 40 780 Td 16 TL " + " ".join(f"({l}) Tj T*" for l in lines) + " ET"
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            f"<< /Length {len(content)} >>\nstream\n{content}\nendstream", "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = "%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n" + "".join(f"{x:010d} 00000 n \n" for x in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF"
    return out.encode("latin-1")


def upload(browser, **extra):
    data = {"posting_text": PUBLIC_POSTING, "cv_text": CV, "candidate_name": "Jo Example", "retention": "session",
            "consent": "1", "ts": old_ts()}
    data.update(extra)
    return browser.post("/check-cv", {k: v for k, v in data.items() if v is not None})


def utc(s):
    return datetime.fromisoformat(s)


def test_nothing_is_read_or_stored_without_the_consent_box(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    r = upload(Browser(app), consent=None)
    assert r.status_code == 400 and b"Tick the box to agree" in r.data
    assert app.extensions["svc"].db.one("SELECT COUNT(*) n FROM runs")["n"] == 0
    assert b'name="consent"' in Browser(app).get("/check-cv").data and b"required" in Browser(app).get("/check-cv").data


def test_the_report_uses_the_uploaded_cv_and_its_name_and_the_cv_is_stored_without_contact_details(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    svc = app.extensions["svc"]
    token = token_of(upload(Browser(app)))
    run = svc.db.run_by_token(token)
    assert "[email removed]" in run["cv_text"] and "[phone removed]" in run["cv_text"]
    assert "@" not in run["cv_text"] and "5550123" not in run["cv_text"]
    assert run["consent_at"] and run["consent_version"] == "2026-11-12" and run["retention"] == "session"
    page = Browser(app).get(f"/r/{token}").data.decode()
    assert "Jo Example" in page and "Zebrafish Analytics" in page       # the uploaded CV supplied the evidence
    assert "Ada Example" not in page                                    # the owner's name is not on a stranger's report
    assert "Delete my report" in page and "hours after your last action" in page


def test_retention_sets_the_expiry_and_activity_extends_a_session_report(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    svc = app.extensions["svc"]
    now = datetime.now(timezone.utc)
    t1 = token_of(upload(Browser(app), retention="session"))
    t2 = token_of(upload(Browser(app, addr="198.51.100.4"), retention="days"))
    r1, r2 = svc.db.run_by_token(t1), svc.db.run_by_token(t2)
    assert timedelta(minutes=119) < utc(r1["expires"]) - now < timedelta(minutes=121)
    assert timedelta(days=29, hours=23) < utc(r2["expires"]) - now < timedelta(days=30, minutes=1)
    svc.db.set_expiry(r1["id"], (now + timedelta(minutes=5)).isoformat(timespec="seconds"))
    Browser(app).get(f"/r/{t1}")                                         # opening the report is an action
    assert utc(svc.db.run_by_token(t1)["expires"]) - now > timedelta(minutes=119)
    svc.db.set_expiry(r2["id"], (now + timedelta(minutes=5)).isoformat(timespec="seconds"))
    Browser(app).get(f"/r/{t2}")                                         # a 30-day report is not extended
    assert utc(svc.db.run_by_token(t2)["expires"]) - now < timedelta(minutes=6)


def test_an_expired_session_report_is_gone(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    svc = app.extensions["svc"]
    token = token_of(upload(Browser(app)))
    svc.db.set_expiry(svc.db.run_by_token(token)["id"], (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="seconds"))
    assert Browser(app).get(f"/r/{token}").status_code == 404


def test_deleting_the_report_removes_the_cv_and_every_trace_of_it(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    svc = app.extensions["svc"]
    b = Browser(app)
    token = token_of(upload(b))
    assert svc.db.one("SELECT COUNT(*) n FROM traces")["n"] > 0
    assert b.post(f"/r/{token}/delete").status_code == 302
    assert svc.db.one("SELECT COUNT(*) n FROM runs")["n"] == 0
    assert svc.db.one("SELECT COUNT(*) n FROM traces")["n"] == 0 and svc.db.one("SELECT COUNT(*) n FROM spans")["n"] == 0


def test_the_letter_note_names_the_candidate_not_the_owner(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    svc = app.extensions["svc"]
    b = Browser(app)
    token = token_of(upload(b))
    b.post(f"/r/{token}/letter", {"language": "English", "length": "standard"})
    letter = svc.db.letters_for(svc.db.run_by_token(token)["id"], "shared")[0]
    assert letter["status"] == "done"
    text = b.get(f"/r/{token}/letter/{letter['id']}.txt").data.decode()
    assert "Jo Example" in text and "Ada Example" not in text


def test_files_are_read_for_their_text_and_bad_input_is_refused_in_plain_words(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    b = Browser(app)
    r = upload(b, cv_text="", cv_file=(io.BytesIO(make_pdf(CV_FOR_PDF)), "cv.pdf"))
    assert r.status_code == 302
    assert "Zebrafish Analytics" in app.extensions["svc"].db.run_by_token(token_of(r))["cv_text"]
    r = upload(b, cv_text="", cv_file=(io.BytesIO(b"not a pdf at all"), "cv.pdf"))
    assert r.status_code == 400 and b"could not be read" in r.data
    r = upload(b, cv_text="", cv_file=(io.BytesIO(b"MZ"), "cv.exe"))
    assert r.status_code == 400 and b"Upload a PDF" in r.data
    assert upload(b, cv_text="too short").status_code == 400
    assert upload(b, cv_text="x" * 25_001).status_code == 400
    assert upload(b, retention="forever").status_code == 400
    assert upload(b, posting_text="short").status_code == 400


def test_uploads_share_the_daily_limit_and_leave_the_owners_own_checks_alone(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    app.extensions["svc"].db.set_json("limits", {"checks_per_visitor_per_minute": 99, "checks_per_minute_all": 99})  # see test_rate_limits
    b = Browser(app)
    for _ in range(3):
        assert upload(b).status_code == 302
    r = upload(b)
    assert r.status_code == 429 and b"used your 3 checks" in r.data


def test_redaction_removes_contact_details_but_not_dates_or_ranges():
    text = "Mail a.b@firma.example, call +39 02 5550 1234 or (030) 1234 5678. Worked 2013 - 2019, Nov 2011 - Apr 2013, 05.11.2024."
    out = cvfile.redact_contacts(text)
    assert "[email removed]" in out and out.count("[phone removed]") == 2
    assert "2013 - 2019" in out and "Nov 2011 - Apr 2013" in out and "05.11.2024" in out


def test_the_consent_record_outlives_the_report_and_holds_no_cv_or_visitor(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    svc = app.extensions["svc"]
    b = Browser(app)
    token = token_of(upload(b))
    b.post(f"/r/{token}/delete")
    rows = svc.db.all("SELECT * FROM consent_log")
    assert len(rows) == 1 and rows[0]["version"] == "2026-11-12" and token not in str(rows[0]) and len(rows[0]["ref"]) == 16
    assert svc.db.one("SELECT COUNT(*) n FROM runs")["n"] == 0


def test_wishes_and_consent_records_are_purged_after_twelve_months(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    svc = app.extensions["svc"]
    svc.db.add_feedback("", "An old wish that should go.", "", "v")
    svc.db.run("INSERT INTO consent_log (created, version, ref) VALUES ('2025-01-01T00:00:00+00:00', 'x', 'r')")
    svc.db.run("UPDATE feedback SET created='2025-01-01T00:00:00+00:00'")
    svc.db.add_feedback("", "A recent wish that stays.", "", "v")
    svc.purge_expired()
    assert [w["text"] for w in svc.db.feedback()] == ["A recent wish that stays."]
    assert svc.db.one("SELECT COUNT(*) n FROM consent_log WHERE ref='r'")["n"] == 0
