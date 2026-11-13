"""Getting posting text in: from a URL or an uploaded file."""
import html as html_lib
import http.client
import ipaddress
import json
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser
from urllib.parse import urlparse

BLOCK = {"p", "br", "li", "ul", "ol", "div", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article"}
MIN_URL_TEXT = 500
MAX_PAGE_BYTES = 2_000_000
FETCH_SECONDS = 25          # the whole download, not each read: a server that trickles bytes cannot hold a thread
FETCH_SLOTS_N = 3           # links from visitors read at the same time; the rest are asked to try again or upload a PDF
_FETCH_SLOTS = threading.BoundedSemaphore(FETCH_SLOTS_N)


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self.skip += 1
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self.skip = max(0, self.skip - 1)
        elif tag in BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html_to_text(html):
    p = _Text()
    p.feed(html)
    lines = [" ".join(line.split()) for line in "".join(p.parts).splitlines()]
    return "\n".join(line for line in lines if line)


def check_public_address(url):
    """Refuse addresses that resolve to this machine, a private network or a cloud metadata service (SSRF)."""
    parts = urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("the posting address must start with http:// or https://")
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))
    except socket.gaierror as e:
        raise ValueError(f"could not look up {parts.hostname}: {e}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise ValueError("that address points to a private or local network; upload the page as a PDF instead")


def _public_ip(host, port):
    """Resolve once and return an address that is safe to connect to. The connection is made to this address, so the
    name cannot answer differently a second time (DNS rebinding)."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise ValueError(f"could not look up {host}: {e}") from e
    for info in infos:
        if not ipaddress.ip_address(info[4][0]).is_global:
            raise ValueError("that address points to a private or local network; upload the page as a PDF instead")
    return infos[0][4][0]


class _PinnedHTTP(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.create_connection((_public_ip(self.host, self.port), self.port), self.timeout)


class _PinnedHTTPS(http.client.HTTPSConnection):
    def connect(self):
        sock = socket.create_connection((_public_ip(self.host, self.port), self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class _PinnedHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_PinnedHTTP, req)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_PinnedHTTPS, req, context=self._context)


class _CheckedRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_public_address(newurl)  # every hop is checked, not only the first address
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _read_limited(resp, max_bytes, seconds, clock=time.monotonic):
    """Read at most max_bytes, and give up when the whole download takes longer than `seconds`."""
    start, chunks, total = clock(), [], 0
    while total < max_bytes:
        if clock() - start > seconds:
            raise ValueError("that page is being sent too slowly to read; upload the page as a PDF instead")
        chunk = resp.read(min(65536, max_bytes - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


def _download(url, public=False):
    """(page text, content type). Every address is checked, the connection goes to the address that was checked."""
    check_public_address(url)
    opener = urllib.request.build_opener(_CheckedRedirects, _PinnedHTTPHandler, _PinnedHTTPSHandler)
    req = urllib.request.Request(url, headers={"User-Agent": "match-app/1.0"})
    with opener.open(req, timeout=15 if public else 20) as resp:
        raw = _read_limited(resp, MAX_PAGE_BYTES, FETCH_SECONDS)
        charset = resp.headers.get_content_charset() or "utf-8"
        ctype = resp.headers.get_content_type()
    try:
        return raw.decode(charset, errors="replace"), ctype
    except LookupError:
        return raw.decode("utf-8", errors="replace"), ctype


_LD_JSON = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.I | re.S)


def _job_postings(node):
    """Every schema.org JobPosting inside a parsed JSON-LD value (it may sit in a list or in an @graph)."""
    if isinstance(node, list):
        for item in node:
            yield from _job_postings(item)
    elif isinstance(node, dict):
        kind = node.get("@type")
        if "JobPosting" in (kind if isinstance(kind, list) else [kind]):
            yield node
        for value in node.values():
            if isinstance(value, (dict, list)):
                yield from _job_postings(value)


def _plain(value):
    if isinstance(value, list):
        return ", ".join(_plain(v) for v in value if _plain(v))
    if isinstance(value, dict):
        return _plain(value.get("name", ""))
    return html_lib.unescape(str(value or "")).strip()     # job data often carries "&amp;" and "&#8212;" in plain fields


def _posting_from_job(job):
    org = _plain(job.get("hiringOrganization"))
    places = job.get("jobLocation")
    where = []
    for place in places if isinstance(places, list) else [places]:
        addr = place.get("address") if isinstance(place, dict) else None
        if isinstance(addr, dict):
            where.append(", ".join(x for x in (_plain(addr.get("addressLocality")), _plain(addr.get("addressRegion")),
                                                _plain(addr.get("addressCountry"))) if x))
    if str(job.get("jobLocationType", "")).upper() == "TELECOMMUTE":
        where.append("Remote")
    head = [x for x in (_plain(job.get("title")), org, "; ".join(w for w in where if w), _plain(job.get("employmentType"))) if x]
    parts = ["\n".join(head)]
    for key in ("description", "responsibilities", "qualifications", "skills"):
        raw = job.get(key)
        if isinstance(raw, list):
            raw = "\n".join(str(x) for x in raw)
        if raw:
            raw = str(raw)
            if "&lt;" in raw and "<" not in raw:      # some sites escape the markup inside the JSON text
                raw = html_lib.unescape(raw)
            parts.append(html_to_text(raw))
    return "\n\n".join(p for p in parts if p)


class FetchedText(str):
    """The text of a posting read from a link, with the role title and company when the page's job data gives them."""
    job_title = ""      # not "title": a str already has a title() method
    job_company = ""

    def __new__(cls, text, title="", company=""):
        obj = super().__new__(cls, text)
        obj.job_title, obj.job_company = title, company
        return obj


def _find_job(html):
    """(text, title, company) from the page's structured job posting data, or None when there is none worth using."""
    for block in _LD_JSON.findall(html):
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        for job in _job_postings(data):
            text = _posting_from_job(job)
            if len(text) >= 300:
                return text, _plain(job.get("title"))[:120], _plain(job.get("hiringOrganization"))[:120]
    return None


def posting_text_from_html(html):
    """The posting text of a page: the structured job posting data many career sites include (it is there even when the
    page itself fills in its text with JavaScript), else the visible text. Raises when there is not enough text."""
    found = _find_job(html)
    if found:
        return FetchedText(found[0], found[1], found[2])
    text = html_to_text(html)
    if len(text) < MIN_URL_TEXT:
        raise ValueError(
            f"the page returned only {len(text)} characters of text (many career sites load the posting with "
            "JavaScript); upload the page as a PDF instead")
    return FetchedText(text)


def fetch_url(url):
    """For the owner's own runs."""
    return posting_text_from_html(_download(url)[0])


def fetch_posting(url, limit_chars):
    """For visitors: the text of the posting behind a link, or a ValueError whose message is a plain sentence that tells
    them to paste the text. Stricter than fetch_url: the usual web ports only, a short total time, a size cap, a few
    fetches at once, no network details in the messages."""
    url = (url or "").strip()
    try:
        parts = urlparse(url)
        port = parts.port
    except ValueError:
        raise ValueError("that link does not look right; check it or upload the page as a PDF instead") from None
    if len(url) > 2000:
        raise ValueError("that link is too long; upload the page as a PDF instead")
    if parts.scheme in ("http", "https") and port not in (None, 80, 443):
        raise ValueError("only links on the usual web ports (80 and 443) can be read; upload the page as a PDF instead")
    if not _FETCH_SLOTS.acquire(blocking=False):
        raise ValueError("several links are being read right now; try again in a minute or upload the page as a PDF")
    try:
        html, ctype = _download(url, public=True)
    except urllib.error.HTTPError as e:
        raise ValueError(f"that site would not let us read the page (answer {e.code}); it may block automated reading or "
                         "need a login, so upload the page as a PDF instead") from None
    except ValueError:
        raise
    except (urllib.error.URLError, OSError, http.client.HTTPException):
        raise ValueError("we could not reach that page; check the link or upload the page as a PDF instead") from None
    finally:
        _FETCH_SLOTS.release()
    if ctype and not ctype.lower().startswith(("text/html", "application/xhtml", "text/plain")):
        raise ValueError(f"that link is not a web page (it returned {ctype}); upload the page as a PDF instead")
    text = posting_text_from_html(html)
    if len(text) > limit_chars:
        raise ValueError(f"the page is longer than {limit_chars:,} characters; upload a PDF of just the requirements part "
                         "instead")
    return text


def read_upload(filename, data):
    name = filename.lower()
    if name.endswith(".pdf"):
        raise ValueError("PDF upload is not supported yet; paste the text or save the posting as .txt, .md or .html")
    if not name.endswith((".txt", ".md", ".html", ".htm")):
        raise ValueError("upload a .txt, .md or .html file, or paste the text")
    text = data.decode("utf-8", errors="replace")
    return html_to_text(text) if name.endswith((".html", ".htm")) else text
