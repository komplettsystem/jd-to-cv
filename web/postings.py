"""The job posting of a visitor's check: the text behind the link they give, or, when the page cannot be read, an uploaded file or
pasted text."""
import cvfile
import ingest
from service import LimitError, PostingError
from web import auth

class LinkLimit(PostingError, LimitError):
    """Too many links from one address: a limit, and also a reason to offer the file upload."""


LINKS_PER_HOUR = 6   # per visitor; each link holds a server thread for a few seconds, so this is separate from the check limit


def _sentences(message):
    """ingest's messages are written to be joined to other text; shown on their own they need capitals and full stops."""
    parts = [p.strip() for p in message.split("; ") if p.strip()]
    text = ". ".join(p[0].upper() + p[1:] for p in parts)
    return text if text.endswith((".", "?", "!")) else text + "."


def posting_text(form, files, visitor, limit_chars):
    """Pasted text first, then an uploaded file, then a link. A link is read once and is not kept; an uploaded file is
    read for its text and is not kept. Raises PostingError (also LimitError for the link limit) with a message for the visitor."""
    text = form.get("posting_text", "")
    if text.strip():
        return text
    upload = files.get("posting_file")
    if upload and upload.filename:
        try:
            return cvfile.extract(upload.filename, upload.read(cvfile.MAX_BYTES + 1), what="posting")
        except ValueError as e:
            raise PostingError(str(e)) from None
    link = (form.get("posting_url") or "").strip()
    if not link:
        raise PostingError("Give the link to the posting.")
    if not auth.allow(f"fetch:{visitor}", LINKS_PER_HOUR, 3600):
        raise LinkLimit("Too many links from this address. Try again in an hour, or upload the posting as a PDF.")
    try:
        return ingest.fetch_posting(link, limit_chars)
    except ValueError as e:
        raise PostingError(_sentences(str(e))) from None
