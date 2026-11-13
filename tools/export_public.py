#!/usr/bin/env python3
"""Export the committed files of this app into a new folder for a public repository, scan the copy for leaks, and
optionally turn it into a fresh git repository with exactly one commit. Standard library only.

    python tools/export_public.py --out ../reverse-apply-public                       # copy and scan
    python tools/export_public.py --out ../reverse-apply-public --commit --author "Name <id+user@users.noreply.github.com>"

What it does, in order:
  1. Lists the files git tracks in this folder (`git ls-files`), so ignored files (deploy/.env, data/, keys) are never seen.
     It refuses to run when tracked files have uncommitted changes (use --allow-dirty to override).
  2. Copies them to --out (which must not exist or must be empty), leaving out what EXCLUDE names, with the reason printed.
  3. Scans the copy (scan_tree below) and exits 1 on any hit. Nothing is committed in that case.
  3b. Only with --history-check: also fails if an email address or phone number that was EVER added to this folder in the
     private history (git log -p, read in memory, never printed) still appears in the export. The history is only read to
     compare; no file is ever taken from it.
  4. Only with --commit: `git init`, one commit named "Initial public version", and `git log --oneline` as proof.
     It never adds a remote and never pushes. The history of this private repository is never used.

The scan finds patterns, not meaning. It cannot know that a sentence is about a real company or a real person unless you put
the name in the local deny list (tools/export_denylist.local.txt, git-ignored; format in docs/PUBLISHING.md)."""
import argparse
import fnmatch
import ipaddress
import math
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
LOCAL_DENYLIST = Path(__file__).with_name("export_denylist.local.txt")
COMMIT_MESSAGE = "Initial public version"

# ---------------------------------------------------------------------------------------------------------------------
# What never leaves, even if someone committed it by mistake. (pattern, reason). A pattern ending in "/" is a folder.
# ---------------------------------------------------------------------------------------------------------------------
ROOT_DIRS = [                       # paths from the top of the app folder (a folder if it ends in "/", otherwise one file)
    ("docs/PUBLISHING.md", "the owner's checklist for publishing: it is about the private repository, not for strangers"),
    ("data/", "runtime data: databases, uploaded CVs, traces, the secret key"),
    ("tmp-screenshots/", "screenshots made while developing; they can show real data. Pick README images by hand afterwards"),
]
ANY_DIRS = [                        # at any depth
    (".git/", "git metadata"),
    (".venv/", "virtual environment"),
    ("__pycache__/", "Python bytecode"),
    (".pytest_cache/", "test cache"),
    (".terraform/", "Terraform provider downloads and state"),
]
FILE_PATTERNS = [                   # matched against the file name
    (".env", "deploy secrets"),
    (".env.*", "deploy secrets (only .env.example is kept)"),
    ("*.db", "SQLite database"),
    ("*.db-*", "SQLite journal or WAL file"),
    ("*.sqlite*", "SQLite database"),
    ("secret.key", "the app's signing key"),
    ("*.tfstate*", "Terraform state holds server addresses and tokens"),
    ("*.tfvars", "Terraform variable values"),
    ("*.tfvars.json", "Terraform variable values"),
    ("crash.log", "crash log"),
    ("*.pem", "key or certificate"),
    ("*.p12", "key or certificate"),
    ("id_rsa*", "ssh key"),
    ("id_ed25519*", "ssh key"),
    (".DS_Store", "macOS folder metadata"),
    ("*.pyc", "Python bytecode"),
    ("export_denylist.local.txt", "the owner's private deny list"),
]
KEEP_FILES = {".env.example"}


def exclusion(rel):
    """The reason a tracked path is left out of the export, or None."""
    for prefix, why in ROOT_DIRS:
        if rel.startswith(prefix):
            return why
    parts = rel.split("/")
    for prefix, why in ANY_DIRS:
        if prefix[:-1] in parts[:-1]:
            return why
    name = parts[-1]
    if name in KEEP_FILES:
        return None
    for pat, why in FILE_PATTERNS:
        if fnmatch.fnmatch(name, pat):
            return why
    return None


# ---------------------------------------------------------------------------------------------------------------------
# The leak scan
# ---------------------------------------------------------------------------------------------------------------------
@dataclass
class Finding:
    path: str
    line: int
    rule: str
    masked: str
    severity: str = "hit"           # "hit" fails the export; "review" is listed for a human look and does not

    def __str__(self):
        where = f"{self.path}:{self.line}" if self.line else self.path
        return f"{self.severity.upper():6} {where}  [{self.rule}]  {self.masked}"


def mask(text, show=True):
    """Enough of the match to recognise its kind by file and line, never enough to use it. Short matches and deny-list
    words (a name, a number) show nothing but their length."""
    t = text.strip()
    if len(t) <= 12 or not show:
        return f"*** ({len(t)} chars)"
    return f"{t[:2]}...{t[-2:]} ({len(t)} chars)"


FAKE_EMAIL_DOMAINS = ("example.com", "example.org", "example.net", "users.noreply.github.com")
FAKE_EMAIL_SUFFIXES = (".example", ".invalid", ".test", ".localhost")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")

# International numbers with a plus sign, and national ones that start with 0 and have a separator after the area code.
PHONE_RES = (
    re.compile(r"(?<![\w.])\+\d{1,3}(?:[\s.\-/]?\(?\d{1,5}\)?){2,6}(?![\w])"),
    re.compile(r"(?<![\w.+/\-])\(?0\d{1,4}\)?[\s./\-]\d{3,}(?:[\s./\-]\d{2,}){0,2}(?![\w]|[.\-]\w)"),
    # national numbers whose area code does not start with 0 (an Italian mobile such as 3xx xxx xxxx): three digit groups
    re.compile(r"(?<![\w.+/\-:])\d{2,4}[ .\-]\d{3,4}[ .\-]\d{3,5}(?![\w]|[.\-]\w)"),
)
FAKE_PHONE_MARKERS = ("5550", "0000", "1234")       # made-up numbers in this repo contain one of these digit runs (5550: the fiction range 555-01xx)

IPV4_RE = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?!\d|\.\d)")

KEY_RES = (
    ("api key (sk-)", re.compile(r"\bsk-(?:or-v1-|ant-|proj-)?[A-Za-z0-9_-]{16,}")),
    ("aws key id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})")),
    ("slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("google api key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("password hash", re.compile(r"\b(?:scrypt:\d+:\d+:\d+\$|pbkdf2:sha\d+:\d+\$|\$2[aby]\$\d\d\$)[^\s'\"]{10,}")),
)
TOKEN_RE = re.compile(r"[A-Za-z0-9+/_=-]{32,}")
ASSIGNED_RE = re.compile(r"(?i)\b(secret[_-]?key|password(?:[_-]?hash)?|api[_-]?key|token|passwd)\b\s*[=:]\s*([\"']?)([^\s\"'#,;)}\]]{12,})")
CODEISH_RE = re.compile(r"^[A-Za-z_][\w.\[\]]*(\(.*)?$")      # an unquoted name or call such as secrets.token_urlsafe(16): code, not a value
PLACEHOLDER_RE = re.compile(r"(?i)^(?:your|example|change|xxx|<|\$|\{|%|\.\.\.|none|null|true|false|os\.|settings\.|self\.|e\.|env)")

# Paths where long hash-like strings are expected and public: Terraform's provider checksums.
HASHLIKE_OK_PATHS = ("deploy/hetzner/.terraform.lock.hcl",)
# Test files use made-up secrets on purpose; the "assigned secret" rule skips them (the key-shape rules do not).
SECRET_ASSIGN_SKIP_PREFIXES = ("tests/",)

# Posting-like text: three of these headings within 40 lines, or four typical phrases within 25.
POSTING_HEADING_RE = re.compile(
    r"(?i)^\W*(?:minimum |preferred |basic |key )?(?:requirements|qualifications|responsibilities|what you(?:'ll| will) (?:do|bring)|"
    r"about (?:us|the role|the job|the team|you)|who you are|your profile|what we offer|we offer|benefits|compensation|"
    r"salary range|nice to have|the role|nice-to-haves)[^\n]{0,30}$")
POSTING_PHRASE_RE = re.compile(
    r"(?i)\b(we are looking for|you will be responsible|you'll be responsible|what we offer|years of (?:relevant )?experience|"
    r"equal opportunity employer|competitive salary|apply now|join our team|about the role|ideal candidate)\b")
# Files whose fictional postings were read and checked by hand (companies there: Northwind, Fabrikam, Contoso, Globex, Initech,
# Orbit Apps, Lantern, Pixel Studio and similar). A hit here is only listed for a look, because a real posting pasted into one of
# these files later would look the same. Re-read these after every change.
FICTIONAL_POSTING_PATHS = ("tools/seed_demo.py", "evals/synthetic.py", "evals/benchmark.py", "evals/chat_scope.py",
                           "evals/history_prompts.json", "tests/test_match.py", "tests/test_export_public.py")

POSTING_RULE_SKIP = ("tools/export_public.py",)         # this file lists the posting headings it looks for

TEXT_NOT_SCANNED_EXT = {".ttf", ".woff", ".woff2", ".otf"}          # fonts: binary, public, licensed, nothing to read
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico"}      # cannot be scanned: look at each one yourself


@dataclass
class Denylist:
    """Owner-specific words. Entries (one per line, # starts a comment):
        plain words        case-insensitive, whole word, spaces match any whitespace:  Jo Example
        re:PATTERN         a regular expression, case-insensitive
        digits:NUMBER      a phone number written in any way; matches any number in the files with those digits, any format
        allow: PATH        a file (or folder ending in /) where deny entries are tolerated, for example LICENSE"""
    entries: list = field(default_factory=list)         # (label, compiled regex or digit string)
    allow_paths: list = field(default_factory=list)

    @classmethod
    def empty(cls):
        return cls()

    @classmethod
    def parse(cls, text):
        d = cls()
        for raw in text.splitlines():
            line = re.sub(r"\s#.*$", "", raw).strip()
            if not line or line.startswith("#"):
                continue
            label = f"deny-list entry {len(d.entries) + 1}"
            if line.lower().startswith("allow:"):
                d.allow_paths.append(line.split(":", 1)[1].strip())
            elif line.lower().startswith("re:"):
                d.entries.append((label, re.compile(line[3:].strip(), re.I)))
            elif line.lower().startswith("digits:"):
                digits = re.sub(r"\D", "", line[7:])
                if len(digits) >= 6:
                    d.entries.append((label, digits))
            else:
                d.entries.append((label, re.compile(r"(?<!\w)" + r"\s+".join(map(re.escape, line.split())) + r"(?!\w)", re.I)))
        return d

    @classmethod
    def load(cls, path=None):
        p = Path(path) if path else LOCAL_DENYLIST
        return cls.parse(p.read_text()) if p.is_file() else cls()

    def allowed(self, rel):
        return any(rel == a or (a.endswith("/") and rel.startswith(a)) for a in self.allow_paths)

    def scan_line(self, line):
        """Yield (label, matched text) for each entry that matches this line."""
        runs = None
        for label, rule in self.entries:
            if isinstance(rule, str):
                if runs is None:
                    runs = [(m.group(), re.sub(r"\D", "", m.group())) for m in re.finditer(r"\+?\d[\d\s().\-/]{5,}\d", line)]
                for text, digits in runs:
                    if rule in digits:
                        yield label, text
            else:
                for m in rule.finditer(line):
                    yield label, m.group()


def _entropy(s):
    return -sum((s.count(c) / len(s)) * math.log2(s.count(c) / len(s)) for c in set(s))


def _wordlike(tok):
    """A path or identifier chain such as docs/MODEL-COMPARISON-2026-11-13: pieces made only of letters or only of digits."""
    parts = [p for p in re.split(r"[-/_.=+]", tok) if p]
    return len(parts) >= 3 and all(p.isalpha() or p.isdigit() for p in parts)


def _fake_email(addr):
    domain = addr.rsplit("@", 1)[1].lower()
    return domain in FAKE_EMAIL_DOMAINS or domain.endswith(FAKE_EMAIL_SUFFIXES)


KNOWN_PUBLIC_IPS = {"93.184.216.34"}     # example.com's published address, used in a DNS test
KNOWN_PUBLIC_IPS |= {"8.8.8.8", "1.1.1.1"}  # public resolvers, not anyone's server


def _public_ip(text):
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return False
    if text in KNOWN_PUBLIC_IPS:
        return False
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_reserved)


def _posting_signal(lines):
    """Line numbers (1-based) where a posting-like block starts: three headings in 40 lines or four phrases in 25."""
    heads = [(i, POSTING_HEADING_RE.match(l).group().strip().lower()[:12]) for i, l in enumerate(lines, 1) if len(l) < 70 and POSTING_HEADING_RE.match(l)]
    phrases = [(i, m.group().lower()) for i, l in enumerate(lines, 1) for m in POSTING_PHRASE_RE.finditer(l)]
    found = []
    for pts, span, need in ((heads, 40, 3), (phrases, 25, 4)):
        for k, (start, _) in enumerate(pts):
            window = {w for n, w in pts[k:] if n - start <= span}
            if len(window) >= need:
                found.append(start)
                break
    return found


def scan_text(rel, text, denylist=None):
    """All findings for one file's text. `rel` is the path inside the repository (posix style)."""
    denylist = denylist or Denylist.empty()
    out = []
    lines = text.split("\n")
    for n, line in enumerate(lines, 1):
        for m in EMAIL_RE.finditer(line):
            if not _fake_email(m.group()):
                out.append(Finding(rel, n, "email address", mask(m.group())))
        taken = []                                  # the patterns overlap: one finding per stretch of text
        for rx in PHONE_RES:
            for m in rx.finditer(line):
                digits = re.sub(r"\D", "", m.group())
                if any(m.start() < e and s < m.end() for s, e in taken):
                    continue
                if 8 <= len(digits) <= 15 and not any(k in digits for k in FAKE_PHONE_MARKERS):
                    taken.append((m.start(), m.end()))
                    out.append(Finding(rel, n, "phone number", mask(m.group())))
        for m in IPV4_RE.finditer(line):
            if _public_ip(m.group()):
                out.append(Finding(rel, n, "public IPv4 address", mask(m.group())))
        for label, rx in KEY_RES:
            for m in rx.finditer(line):
                out.append(Finding(rel, n, label, mask(m.group())))
        if rel not in HASHLIKE_OK_PATHS:
            for m in TOKEN_RE.finditer(line):
                tok = m.group()
                if re.fullmatch(r"[0-9a-fA-F]{32,}", tok) and len(set(tok)) > 4:
                    out.append(Finding(rel, n, "long hex string", mask(tok)))
                elif (re.search(r"\d", tok) and re.search(r"[A-Za-z]", tok) and _entropy(tok) >= 4.2
                      and not tok.startswith(("http", "data:")) and not _wordlike(tok)):
                    out.append(Finding(rel, n, "long random-looking token", mask(tok)))
        if not rel.startswith(SECRET_ASSIGN_SKIP_PREFIXES):
            for m in ASSIGNED_RE.finditer(line):
                quoted, value = m.group(2), m.group(3)
                if not quoted and CODEISH_RE.match(value):
                    continue
                if not PLACEHOLDER_RE.match(value) and re.search(r"\d", value) and re.search(r"[A-Za-z]", value) and _entropy(value) >= 3.0:
                    out.append(Finding(rel, n, f"value assigned to {m.group(1).lower()}", mask(value)))
        if not denylist.allowed(rel):
            for label, hit in denylist.scan_line(line):
                out.append(Finding(rel, n, label, mask(hit, show=False)))
    # posting-like blocks, also inside JSON strings (literal \n) and Python strings
    expanded, origin = [], []
    for n, line in enumerate(lines, 1):
        for sub in line.split("\\n"):
            expanded.append(sub)
            origin.append(n)
    review_only = rel.startswith(FICTIONAL_POSTING_PATHS)
    for n in ([] if rel in POSTING_RULE_SKIP else _posting_signal(expanded)):
        out.append(Finding(rel, origin[n - 1], "posting-like text (job posting headings or phrases)",
                           "a block of posting headings" + (", file listed as fictional: read it" if review_only else ""),
                           "review" if review_only else "hit"))
    return out


def scan_path_names(rel, denylist):
    out = []
    for m in EMAIL_RE.finditer(rel):
        if not _fake_email(m.group()):
            out.append(Finding(rel, 0, "email address in file name", mask(m.group())))
    if not denylist.allowed(rel):
        for label, hit in denylist.scan_line(re.sub(r"[/_.\-]", " ", rel)):
            out.append(Finding(rel, 0, label + " in file name", mask(hit, show=False)))
    return out


def list_files(root):
    root = Path(root)
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and ".git" not in p.relative_to(root).parts)


def scan_tree(root, files=None, denylist=None):
    """Scan the files under `root` (all of them, or the given relative paths). Returns a list of Finding."""
    root = Path(root)
    denylist = denylist if denylist is not None else Denylist.load()
    findings = []
    for rel in files if files is not None else list_files(root):
        findings += scan_path_names(rel, denylist)
        path = root / rel
        if path.is_symlink():
            findings.append(Finding(rel, 0, "symbolic link (not followed)", "left for a human to resolve"))
            continue
        ext = Path(rel).suffix.lower()
        if ext in TEXT_NOT_SCANNED_EXT:
            continue
        data = path.read_bytes()
        if ext in IMAGE_EXT:
            findings.append(Finding(rel, 0, "image: cannot be scanned", "look at it yourself before publishing", "review"))
            continue
        try:
            if b"\0" in data[:8192]:
                raise UnicodeDecodeError("utf-8", b"", 0, 1, "binary")
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            findings.append(Finding(rel, 0, "binary file: cannot be scanned", f"{len(data)} bytes"))
            continue
        findings += scan_text(rel, text, denylist)
    return findings


def hits(findings):
    return [f for f in findings if f.severity == "hit"]


def format_report(findings):
    lines = []
    for f in sorted(findings, key=lambda f: (f.severity != "hit", f.path, f.line)):
        lines.append(str(f))
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------------------------------------
# The export
# ---------------------------------------------------------------------------------------------------------------------
BARE_NUMBER_RE = re.compile(r"(?<![\w.:/+\-])\+?\d{10,13}(?![\w.:/\-])")      # 3471234567: no separators at all


def phone_digits(line):
    """Digit strings (last nine digits) of everything in `line` that is shaped like a phone number: the scan's own patterns, plus
    bare 10 to 13 digit tokens. Used to compare the history with the export, where dates, counts and prices must not match."""
    out = set()
    for rx in (*PHONE_RES, BARE_NUMBER_RE):
        for m in rx.finditer(line):
            d = re.sub(r"\D", "", m.group())
            if 9 <= len(d) <= 15 and len(set(d)) >= 4:         # 9999999999 and the like are test values, not numbers
                out.add(d[-9:])
    return out


def history_values(src):
    """Email addresses and phone-like numbers that were ever ADDED to files in this folder in the private history (read in
    memory, never written to disk, never printed). Phone numbers are compared by their last nine digits, so +49 and 0 forms
    meet. Numbers with a made-up marker (5550, 0000, 1234) are left out and counted, as in the scan."""
    log = _git(["log", "-p", "--all", "--no-color", "--no-ext-diff", "-U0", "--", "."], src).stdout
    emails, digits, skipped = set(), set(), set()
    for line in log.splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        body = line[1:]
        emails |= {m.group().lower() for m in EMAIL_RE.finditer(body) if not _fake_email(m.group())}
        for d in phone_digits(body):
            (skipped if any(k in d for k in FAKE_PHONE_MARKERS) else digits).add(d)
    return emails, digits, len(skipped)


def history_check(src, root, files):
    """Findings for every value from the private history that still appears in the files about to be published."""
    emails, digits, skipped = history_values(src)
    found = []
    for rel in files:
        path = Path(root) / rel
        if path.suffix.lower() in TEXT_NOT_SCANNED_EXT | IMAGE_EXT:
            continue
        try:
            text = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        for n, line in enumerate(text.split("\n"), 1):
            for m in EMAIL_RE.finditer(line):
                if m.group().lower() in emails:
                    found.append(Finding(rel, n, "email address that is in the private history", mask(m.group())))
            if digits & phone_digits(line):
                found.append(Finding(rel, n, "number that is in the private history", "a phone-like number"))
    return found, len(emails), len(digits), skipped


class ExportError(Exception):
    pass


def _git(args, cwd, env=None, check=True):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=check, env=env)


def tracked_files(src):
    top = _git(["rev-parse", "--is-inside-work-tree"], src, check=False)
    if top.returncode != 0:
        raise ExportError(f"{src} is not inside a git repository: the export only copies tracked files.")
    names = _git(["ls-files", "-z"], src).stdout.split("\0")
    return [n for n in names if n]


def _clean_env():
    """No global git config (identity, hooks, templates, signing) may reach the new repository; times in UTC."""
    env = dict(os.environ)
    for k in [k for k in env if k.startswith("GIT_")]:
        del env[k]
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", TZ="UTC")
    return env


def parse_author(text):
    m = re.fullmatch(r"\s*(.+?)\s*<([^<>@\s]+@[^<>\s]+)>\s*", text or "")
    if not m:
        raise ExportError('--commit needs --author "Name <email>" (for example a GitHub noreply address). '
                          "The global git identity is deliberately not used: it would publish a personal address.")
    return m.group(1), m.group(2)


def commit_fresh_repo(out, author, log=print):
    name, email = parse_author(author)
    env = _clean_env()
    env.update(GIT_AUTHOR_NAME=name, GIT_AUTHOR_EMAIL=email, GIT_COMMITTER_NAME=name, GIT_COMMITTER_EMAIL=email)
    _git(["init", "--template=", "-b", "main", "-q"], out, env)
    _git(["add", "-A"], out, env)
    _git(["-c", "core.hooksPath=" + os.devnull, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "--no-gpg-sign",
          "-m", COMMIT_MESSAGE], out, env)
    count = _git(["rev-list", "--count", "HEAD"], out, env).stdout.strip()
    remotes = _git(["remote"], out, env).stdout.strip()
    if count != "1" or remotes:
        raise ExportError(f"expected one commit and no remote, found {count} commits and remotes '{remotes}'")
    log(_git(["log", "--oneline"], out, env).stdout.rstrip())
    log("author of the commit: " + _git(["log", "-1", "--format=%an <%ae>"], out, env).stdout.strip())
    log("remotes: none. Nothing was pushed.")


def export(src, out, *, commit=False, author=None, denylist=None, allow_dirty=False, history=False, log=print):
    """Returns 0 when the copy is clean (and committed, if asked), 1 when the scan found hits. Raises ExportError on misuse."""
    src, out = Path(src).resolve(), Path(out).resolve()
    if out == src or src in out.parents:
        raise ExportError("--out must be outside the app folder.")
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise ExportError(f"{out} exists and is not empty. Choose a new folder.")
    if commit:
        parse_author(author)                       # fail before copying anything
    files = tracked_files(src)
    if not allow_dirty:
        dirty = _git(["status", "--porcelain", "--untracked-files=no", "--", "."], src).stdout.strip()
        if dirty:
            raise ExportError("tracked files have uncommitted changes (the export copies what is on disk):\n" + dirty +
                              "\nCommit them, or pass --allow-dirty.")
    out.mkdir(parents=True, exist_ok=True)
    copied, skipped = [], []
    for rel in files:
        why = exclusion(rel)
        if why:
            skipped.append((rel, why))
            continue
        source = src / rel
        if source.is_symlink():
            raise ExportError(f"{rel} is a symbolic link; replace it with the real file before exporting.")
        if not source.is_file():
            raise ExportError(f"{rel} is tracked but missing on disk.")
        target = out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.append(rel)
    log(f"copied {len(copied)} tracked files to {out}")
    for rel, why in skipped:
        log(f"left out: {rel}  ({why})")
    for need in ("LICENSE", "README.md", "requirements.txt"):
        if need not in copied:
            raise ExportError(f"{need} is missing from the export.")
    denylist = denylist if denylist is not None else Denylist.load()
    findings = scan_tree(out, copied, denylist)
    if history:
        extra, n_mail, n_num, n_skip = history_check(src, out, copied)
        findings += extra
        log(f"history check: {n_mail} email addresses and {n_num} phone-like numbers were added to this folder at some point "
            f"({n_skip} more with made-up markers were not compared); {len(extra)} line(s) of the export contain one of them")
    bad = hits(findings)
    if findings:
        log(format_report(findings))
    log(f"scan: {len(bad)} hits, {len(findings) - len(bad)} items to look at. Local deny list: "
        + ("loaded" if denylist.entries else "EMPTY, so no personal words were checked"))
    if bad:
        log("Not committed. Fix the hits in the private repository (or the deny list), commit, and export again.")
        return 1
    if commit:
        commit_fresh_repo(out, author, log)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", required=True, help="a new or empty folder, outside the app folder")
    ap.add_argument("--commit", action="store_true", help="git init and make the single commit (needs --author)")
    ap.add_argument("--author", help='"Name <email>" for the commit, for example a GitHub noreply address')
    ap.add_argument("--denylist", help="deny list file (default: tools/export_denylist.local.txt next to this script)")
    ap.add_argument("--allow-dirty", action="store_true", help="copy even if tracked files have uncommitted changes")
    ap.add_argument("--history-check", action="store_true",
                    help="also fail if an email address or phone-like number that was ever added in the private history is in the export")
    ap.add_argument("--src", default=str(APP), help="the app folder to export (default: this one)")
    args = ap.parse_args(argv)
    if args.denylist and not Path(args.denylist).is_file():
        print(f"error: deny list {args.denylist} not found", file=sys.stderr)
        return 2
    try:
        return export(args.src, args.out, commit=args.commit, author=args.author,
                      denylist=Denylist.load(args.denylist), allow_dirty=args.allow_dirty, history=args.history_check)
    except ExportError as e:
        print("error: " + str(e), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
