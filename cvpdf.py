"""The public CV as a PDF, made from the same text the reports are checked against, so the file a recruiter opens is the CV that was
read. The stored text has its phone number and email address replaced by markers (cvfile.redact_contacts); here the markers are
dropped, and the LinkedIn and GitHub links stay and become clickable. Barlow (the site's own font, converted from the web fonts
in static/fonts and kept in fonts/), one column, no pictures."""
import re
import unicodedata
from datetime import date
from functools import lru_cache
from pathlib import Path

from fontTools.ttLib import TTFont
from fpdf import FPDF

FONTS = Path(__file__).resolve().parent / "fonts"

MARKERS = re.compile(r"\[(?:email|phone) removed\]")
URL = re.compile(r"(https?://[^\s,;)]+|(?:www\.)?(?:linkedin\.com|github\.com)/[\w./~%-]+)")
INLINE = re.compile(r"\*\*(.+?)\*\*|\*(.+?)\*|" + URL.pattern)
SWAPS = {"\u2192": "->", "\u2190": "<-", "\u2265": ">=", "\u2264": "<=", "\u00a0": " ", "\u2011": "-", "\u2212": "-"}
INK, LINK = (27, 33, 66), (45, 80, 160)


def filename(owner_name, today=None):
    """The name the PDF is saved under: the owner's name and the day, for example karsten_rieke-cv-20261113.pdf."""
    plain = unicodedata.normalize("NFKD", owner_name or "").encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "_", plain.lower()).strip("_") or "cv"
    return f"{slug}-cv-{(today or date.today()):%Y%m%d}.pdf"


@lru_cache(maxsize=1)
def _glyphs():
    return set(TTFont(FONTS / "Barlow-Regular.ttf").getBestCmap())


def _printable(text):
    """Characters the font lacks are swapped for plain ones (an arrow becomes ->) or left out, never drawn as empty boxes."""
    for old, new in SWAPS.items():
        text = text.replace(old, new)
    return "".join(c for c in text if ord(c) in _glyphs())


def _drop_markers(line):
    """'Milan | [phone removed] | [email removed] | linkedin.com/in/x' becomes 'Milan | linkedin.com/in/x'."""
    if not MARKERS.search(line):
        return line
    line = MARKERS.sub("", line)
    line = re.sub(r"(\|\s*){2,}", "| ", line)
    return line.strip().strip("|").strip()


def _segments(text):
    """(text, style, link) pieces of one line: bold, italic and links."""
    out, pos = [], 0
    for m in INLINE.finditer(text):
        if m.start() > pos:
            out.append((text[pos:m.start()], "", None))
        if m.group(1):
            out.append((m.group(1), "B", None))
        elif m.group(2):
            out.append((m.group(2), "I", None))
        else:
            url = m.group(3).rstrip(".")
            out.append((url, "", url if url.startswith("http") else "https://" + url))
            pos = m.start() + len(url)
            continue
        pos = m.end()
    if pos < len(text):
        out.append((text[pos:], "", None))
    return out


class _Cv(FPDF):
    LEFT = 16

    def put(self, text, size=9.5, style="", gap=1.6, indent=0, color=INK):
        self.set_left_margin(self.LEFT + indent)
        self.set_x(self.LEFT + indent)
        height = size * 0.5
        for part, mark, link in _segments(_printable(text)):
            self.set_font("Barlow", ("U" if link else "".join(sorted(set(style + mark))).replace("BI", "B")), size)
            self.set_text_color(*(LINK if link else color))
            self.write(height, part, link=link)
        self.ln(height + gap)
        self.set_left_margin(self.LEFT)


@lru_cache(maxsize=2)
def render(text, owner_name):
    """The PDF bytes for a CV written in the simple markdown the app stores."""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    pdf = _Cv(format="A4", unit="mm")
    pdf.set_margins(_Cv.LEFT, 14, _Cv.LEFT)
    pdf.set_auto_page_break(True, 14)
    for style, name in (("", "Regular"), ("B", "Bold"), ("I", "Italic")):
        pdf.add_font("Barlow", style, str(FONTS / f"Barlow-{name}.ttf"))
    pdf.set_title(f"{owner_name}, CV")
    pdf.add_page()
    paragraph = []

    def flush():
        if paragraph:   # the stored text is wrapped at about 100 characters; a paragraph is the lines up to the next blank line
            pdf.put(" ".join(paragraph), gap=1.4)
            paragraph.clear()

    for raw in text.splitlines():
        line = _drop_markers(raw.rstrip())
        if not line.strip():
            flush()
        elif line.startswith("### "):
            flush()
            pdf.ln(1.5)
            pdf.put(line[4:], size=10.5, style="B", gap=.8)
        elif line.startswith("## "):
            flush()
            pdf.ln(3)
            pdf.put(line[3:].upper(), size=11, style="B", gap=.6)
            pdf.set_draw_color(*INK)
            pdf.set_line_width(.3)
            pdf.line(_Cv.LEFT, pdf.get_y(), pdf.w - _Cv.LEFT, pdf.get_y())
            pdf.ln(1.8)
        elif line.startswith("# "):
            flush()
            pdf.put(line[2:], size=20, style="B", gap=1.5)
        elif re.match(r"^\s*[-*] ", line):
            flush()
            body = re.sub(r"^\s*[-*] ", "", line)
            pdf.set_x(_Cv.LEFT + 1)
            pdf.set_font("Barlow", "", 9.5)
            pdf.set_text_color(*INK)
            pdf.cell(4, 5, "\u2022")
            pdf.put(body, indent=5, gap=1)
        elif re.fullmatch(r"\*[^*].*\*", line.strip()):   # an italic line on its own (the dates under a role)
            flush()
            pdf.put(line, gap=1.4)
        else:
            paragraph.append(line.strip())
    flush()
    return bytes(pdf.output())
