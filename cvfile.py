"""Turning an uploaded CV into the text the app keeps: read a PDF or a text file, clean it, and remove contact details.

Nothing else is kept from an upload: the file itself is dropped after its text is read, and the text has its email
addresses and phone numbers replaced, because the report does not need them."""
import io
import re

MAX_BYTES = 2_000_000
MAX_PAGES = 12
MAX_TEXT = 60_000          # characters read from a file or taken from the form, before any pattern runs
MAX_NAME = 200
EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+")  # the lookbehind keeps it from rescanning a long run
PHONE = re.compile(r"(?<![\w.])\+?\(?\d[\d ()./-]{7,}\d(?!\w)")


def clean(text):
    """Plain text: no control characters, tidy blank lines."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[^\S\n]+", " ", re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f​﻿]", "", text))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def redact_contacts(text):
    """Replace email addresses and phone numbers (nine or more digits) with a marker."""
    text = EMAIL.sub("[email removed]", text)
    return PHONE.sub(lambda m: "[phone removed]" if sum(c.isdigit() for c in m.group(0)) >= 9 else m.group(0), text)


def extract(filename, data, what="CV"):
    """The text of an uploaded PDF, text or Markdown file. `what` names it in the messages (CV or posting). Raises
    ValueError with a message for the visitor."""
    if len(data) > MAX_BYTES:
        raise ValueError(f"That file is larger than {MAX_BYTES // 1_000_000} MB. Paste the text of the {what} instead.")
    name = (filename or "").lower()
    if name.endswith((".txt", ".md")):
        try:
            return clean(data.decode("utf-8"))
        except UnicodeDecodeError:
            raise ValueError(f"That text file is not UTF-8. Paste the text of the {what} instead.") from None
    if not name.endswith(".pdf"):
        raise ValueError(f"Upload a PDF, a .txt or a .md file, or paste the text of the {what}.")
    try:
        from pypdf import Configuration, PdfReader
        Configuration.zlib_maximum_output_length = 2_000_000   # a small PDF must not expand into gigabytes
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise ValueError(f"That PDF is password protected. Paste the text of the {what} instead.")
        parts, size = [], 0
        for page in reader.pages[:MAX_PAGES]:
            parts.append(page.extract_text() or "")
            size += len(parts[-1])
            if size > MAX_TEXT:
                break
        text = "\n\n".join(parts)
    except ValueError:
        raise
    except Exception:  # a damaged or unusual PDF; the reason is not useful to the visitor
        raise ValueError(f"That PDF could not be read. Paste the text of the {what} instead.") from None
    text = clean(text[:MAX_TEXT * 2])
    if len(text) < 100:
        raise ValueError(f"No text could be read from that PDF (a scanned page has none). Paste the text of the {what} instead.")
    return text
