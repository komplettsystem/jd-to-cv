"""The export tool and its leak scan (tools/export_public.py).

Every fake secret below is assembled at run time from pieces, so that this file does not itself look like a leak when the
scan runs over the repository (see test_the_repository_itself_passes_the_scan)."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import export_public as ex  # noqa: E402

APP = Path(__file__).resolve().parent.parent
AT = "@"
P1 = "+49 151 " + "9876 5432"          # made-up numbers and addresses are assembled so this file passes its own scan
P2 = "(089) " + "4711 8822"
IP = "95.216." + "10.20"


def scan(text, rel="notes/a.md", deny=None):
    return ex.scan_text(rel, text, ex.Denylist.parse(deny) if deny else ex.Denylist.empty())


def rules(findings):
    return [f.rule for f in findings]


# ---- patterns -------------------------------------------------------------------------------------------------------
def test_a_real_looking_email_is_a_hit_and_is_masked():
    addr = "jane.doe" + AT + "firma.org"
    found = scan(f"Write to {addr} please")
    assert rules(found) == ["email address"] and found[0].line == 1 and found[0].severity == "hit"
    assert addr not in str(found[0]) and "jane" not in str(found[0]) and "(18 chars)" in str(found[0])


@pytest.mark.parametrize("domain", ["example.com", "example.org", "mail.example", "x.invalid", "users.noreply.github.com"])
def test_obviously_fictional_email_domains_pass(domain):
    assert scan("a.b" + AT + domain) == []


def test_phone_numbers_are_found_in_international_and_national_form_but_made_up_ones_and_dates_pass():
    assert rules(scan("call " + P1)) == ["phone number"]
    assert rules(scan("call " + P2)) == ["phone number"]
    assert rules(scan("call " + P2.replace("(089)", "089"))) == ["phone number"]
    assert rules(scan("mobile " + "347 " + "987 6543")) == ["phone number"]            # no plus sign, no leading 0
    assert rules(scan("Tel. " + "347-" + "987-6543.")) == ["phone number"]
    fine = "call +49 170 5550123 or +39 000 000, worked 2013 - 2019, Nov 2011 - Apr 2013, on 05.11.2024, id 0000000000 65535"
    assert scan(fine) == []


def test_public_ip_addresses_are_hits_and_private_or_documentation_ones_are_not():
    assert rules(scan("ssh root" + AT + IP)) == ["public IPv4 address"]
    assert scan("127.0.0.1 10.1.2.3 192.168.0.5 203.0.113.7 0.0.0.0 172.16.4.4") == []
    assert "95.216" not in str(scan("host " + IP)[0])


def test_key_shapes_are_found():
    cases = {
        "api key (sk-)": "OPENROUTER=" + "sk" + "-or-v1-" + "a1b2c3d4" * 4,
        "aws key id": "AK" + "IA" + "ABCDEFGHIJKLMNOP",
        "github token": "gh" + "p_" + "A1b2C3d4E5" * 4,
        "private key block": "-----BEGIN " + "RSA PRIVATE KEY-----",
        "password hash": "scr" + "ypt:32768:8:1$" + "abcdefgh$" + "0123456789abcdef",
        "long hex string": "digest " + "9f86d081884c7d659a2f" + "eaa0c55ad015a3bf4f1b" + "2b0b822cd15d6c15b0f00a08",
    }
    for rule, text in cases.items():
        assert rule in rules(scan(text)), rule


def test_an_assigned_secret_is_a_hit_but_placeholders_empty_values_and_code_are_not():
    assert rules(scan('SECRET_KEY = "' + "q8Zr7" + "Xk2vP9" + 'mT4wL1"')) == ["value assigned to secret_key"]
    for fine in ("SECRET_KEY=", "SECRET_KEY=   # 32+ random characters", "OPENROUTER_API_KEY=your-key-here",
                 "token = secrets.token_urlsafe(16)", "api_key = settings.openrouter_api_key"):
        assert scan(fine) == [], fine
    assert scan('SECRET_KEY = "' + "q8Zr7" + "Xk2vP9" + 'mT4wL1"', rel="tests/test_x.py") == []     # tests use made-up values


def test_path_like_identifiers_are_not_taken_for_random_tokens():
    assert scan("see docs/MODEL-COMPARISON-2026-11-13.md for details") == []


# ---- deny list ------------------------------------------------------------------------------------------------------
DENY = """# comment
Jo Example            # a name; case-insensitive, whole word
Via Roma 1
re:acme\\s+(corp|inc)
allow: LICENSE
allow: legal/
"""
DENY += "digits:" + P1 + "\n"


def test_deny_list_entries_match_whole_words_case_insensitively_and_never_print_the_word():
    found = scan("Written by JO  example.", deny=DENY)
    assert [f.rule for f in found] == ["deny-list entry 1"]
    assert "Example" not in str(found[0]).replace("deny-list", "") and "JO" not in found[0].masked
    assert scan("Joe Examples", deny=DENY) == []
    assert rules(scan("Office: via roma 1, Milan", deny=DENY)) == ["deny-list entry 2"]
    assert rules(scan("works at ACME Corp", deny=DENY)) == ["deny-list entry 3"]


def test_a_phone_number_on_the_deny_list_matches_however_it_is_written():
    for written in (P1, "0151-" + "98765432", "(0151) " + "9876 5432", "+49(0)151/" + "9876-5432", "491519876 " + "5432"):
        text = f"Call {written} now"
        # the digits entry is the number without separators; national forms drop the country code, so they are listed too
        found = scan(text, deny="digits:151 9876 5432")
        assert [r for r in rules(found) if r.startswith("deny")] == ["deny-list entry 1"], written


def test_allow_entries_exempt_a_file_or_folder_from_the_deny_list_only():
    assert scan("Jo Example", rel="LICENSE", deny=DENY) == []
    assert scan("Jo Example", rel="legal/imprint.md", deny=DENY) == []
    assert rules(scan("Jo Example", rel="docs/a.md", deny=DENY)) == ["deny-list entry 1"]
    assert rules(scan("x " + "jane" + AT + "firma.org", rel="LICENSE", deny=DENY)) == ["email address"]


def test_the_deny_list_also_checks_file_names(tmp_path):
    (tmp_path / "jo_example_cv.md").write_text("nothing\n")
    found = ex.scan_tree(tmp_path, denylist=ex.Denylist.parse("Jo Example"))
    assert rules(found) == ["deny-list entry 1 in file name"]


def test_the_local_deny_list_file_is_git_ignored_and_never_exported():
    assert "tools/export_denylist.local.txt" in (APP / ".gitignore").read_text().split()
    assert ex.exclusion("tools/export_denylist.local.txt")
    assert ex.LOCAL_DENYLIST.name == "export_denylist.local.txt"


# ---- posting-like blocks --------------------------------------------------------------------------------------------
POSTING = """Senior Widget Engineer
About the role
We are hiring.

What you'll do
- Build widgets.

Requirements
- 5 years of experience.

Benefits
- Free fruit.
"""


def test_posting_like_text_is_a_hit_in_ordinary_files():
    found = scan(POSTING, rel="notes/job.md")
    assert rules(found) == ["posting-like text (job posting headings or phrases)"] and found[0].severity == "hit" and found[0].line == 2


def test_posting_like_text_in_a_json_string_is_found_with_its_line():
    text = '{\n "a": 1,\n "posting": ' + json.dumps(POSTING) + "\n}"
    found = scan(text, rel="data.json")
    assert rules(found) == ["posting-like text (job posting headings or phrases)"] and found[0].line == 3


def test_posting_like_text_in_the_checked_fictional_files_is_only_listed_for_review():
    found = scan(POSTING, rel="tools/seed_demo.py")
    assert [f.severity for f in found] == ["review"]
    assert ex.hits(found) == []
    assert [f.severity for f in scan(POSTING, rel="docs/notes.md")] == ["hit"]        # docs and new tests get no exemption
    assert [f.severity for f in scan(POSTING, rel="tests/test_new.py")] == ["hit"]


def test_ordinary_prose_with_one_heading_is_not_posting_like():
    assert scan("# Requirements\n\nPython 3.14 and git.\n\n## Benefits of the design\n\nIt is small.\n") == []


# ---- scanning a folder ----------------------------------------------------------------------------------------------
def test_scan_tree_reads_text_flags_binaries_and_images_and_skips_fonts(tmp_path):
    (tmp_path / "a.md").write_text("mail " + "x" + AT + "firma.org\n")
    (tmp_path / "blob.bin").write_bytes(b"\0\1\2")
    (tmp_path / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n\0")
    (tmp_path / "f.woff2").write_bytes(b"\0\0wOF2")
    found = {(f.path, f.rule, f.severity) for f in ex.scan_tree(tmp_path, denylist=ex.Denylist.empty())}
    assert found == {("a.md", "email address", "hit"), ("blob.bin", "binary file: cannot be scanned", "hit"),
                     ("shot.png", "image: cannot be scanned", "review")}


def test_the_report_has_file_line_and_rule_and_nothing_secret():
    secret = "sk" + "-or-v1-" + "z9y8x7w6" * 4
    report = ex.format_report(scan("a\nb\nkey " + secret, rel="deploy/x.sh"))
    assert "deploy/x.sh:3" in report and "api key (sk-)" in report and secret not in report and secret[8:30] not in report


# ---- exclusions -----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("rel", ["data/match.db", "tmp-screenshots/a.png", "deploy/.env", ".env", "x/__pycache__/a.pyc",
                                 ".venv/lib/x.py", "deploy/hetzner/terraform.tfstate", "deploy/hetzner/prod.tfvars",
                                 "secret.key", "tools/export_denylist.local.txt", "a.db-wal", ".pytest_cache/v/x"])
def test_these_paths_never_leave(rel):
    assert ex.exclusion(rel), rel


@pytest.mark.parametrize("rel", ["deploy/.env.example", ".env.example", "tests/data/sample.json", "docs/data/x.md", "app.py",
                                 "static/fonts/a.woff2", "deploy/hetzner/.terraform.lock.hcl"])
def test_these_paths_stay(rel):
    assert ex.exclusion(rel) is None, rel


# ---- the export itself, in a throw-away git repository ---------------------------------------------------------------
def run(args, cwd, **kw):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t" + AT + "example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t" + AT + "example.com"}
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True, env=env, **kw).stdout


@pytest.fixture
def private_repo(tmp_path):
    if not shutil.which("git"):
        pytest.skip("git is not installed")
    top = tmp_path / "private"
    app = top / "app"
    (app / "deploy").mkdir(parents=True)
    (app / "data").mkdir()
    (top / "other.txt").write_text("outside the app folder\n")
    files = {"LICENSE": "MIT\n", "README.md": "# Demo\n", "requirements.txt": "flask\n", "main.py": "print('hi')\n",
             "deploy/.env.example": "KEY=\n", ".gitignore": ".env\n", "data/leak.db": "private rows\n"}
    for name, body in files.items():
        (app / name).write_text(body)
    run(["git", "init", "-q", "-b", "main"], top)
    run(["git", "add", "-f", "-A"], top)
    run(["git", "commit", "-q", "-m", "first, with history that must not travel"], top)
    (app / "main.py").write_text("print('hi again')\n")
    run(["git", "commit", "-q", "-am", "second"], top)
    (app / "deploy" / ".env").write_text("SECRET_KEY=real\n")        # untracked and ignored
    (app / "untracked.txt").write_text("never added\n")
    return app


def test_export_copies_tracked_files_only_leaves_out_the_denylisted_ones_and_makes_one_commit(private_repo, tmp_path):
    out = tmp_path / "public"
    logs = []
    assert ex.export(private_repo, out, commit=True, author="Demo <demo" + AT + "example.com>", denylist=ex.Denylist.empty(),
                     log=logs.append) == 0
    names = {p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file() and ".git/" not in p.as_posix()}
    assert names == {"LICENSE", "README.md", "requirements.txt", "main.py", "deploy/.env.example", ".gitignore"}
    assert (out / "main.py").read_text() == "print('hi again')\n"
    assert run(["git", "rev-list", "--count", "HEAD"], out).strip() == "1"
    assert run(["git", "log", "--format=%s|%an|%ae"], out).strip() == "Initial public version|Demo|demo" + AT + "example.com"
    assert run(["git", "remote"], out).strip() == "" and run(["git", "status", "--porcelain"], out).strip() == ""
    assert "Initial public version" in "\n".join(logs) and any("left out: data/leak.db" in l for l in logs)
    assert "first, with history" not in run(["git", "log", "--all", "--format=%B"], out)


def test_export_without_commit_makes_no_git_repository(private_repo, tmp_path):
    out = tmp_path / "public"
    assert ex.export(private_repo, out, denylist=ex.Denylist.empty(), log=lambda *_: None) == 0
    assert not (out / ".git").exists()


def test_export_with_a_hit_exits_1_and_commits_nothing(private_repo, tmp_path):
    (private_repo / "main.py").write_text("# mail " + "jane" + AT + "firma.org\n")
    run(["git", "commit", "-q", "-am", "leak"], private_repo)
    out = tmp_path / "public"
    logs = []
    assert ex.export(private_repo, out, commit=True, author="Demo <demo" + AT + "example.com>", denylist=ex.Denylist.empty(),
                     log=logs.append) == 1
    assert not (out / ".git").exists() and "main.py:1" in "\n".join(logs) and "Not committed" in "\n".join(logs)


def test_export_refuses_a_missing_author_a_dirty_tree_a_full_target_and_a_target_inside_the_app(private_repo, tmp_path):
    with pytest.raises(ex.ExportError, match="--author"):
        ex.export(private_repo, tmp_path / "p1", commit=True, denylist=ex.Denylist.empty(), log=lambda *_: None)
    assert not (tmp_path / "p1").exists()                      # failed before copying anything
    full = tmp_path / "p2"
    full.mkdir()
    (full / "x").write_text("x")
    with pytest.raises(ex.ExportError, match="not empty"):
        ex.export(private_repo, full, denylist=ex.Denylist.empty(), log=lambda *_: None)
    with pytest.raises(ex.ExportError, match="outside the app folder"):
        ex.export(private_repo, private_repo / "out", denylist=ex.Denylist.empty(), log=lambda *_: None)
    (private_repo / "main.py").write_text("changed\n")
    with pytest.raises(ex.ExportError, match="uncommitted"):
        ex.export(private_repo, tmp_path / "p3", denylist=ex.Denylist.empty(), log=lambda *_: None)
    assert ex.export(private_repo, tmp_path / "p4", denylist=ex.Denylist.empty(), allow_dirty=True, log=lambda *_: None) == 0


def test_history_check_finds_old_private_values_that_are_still_in_the_export_and_ignores_removed_ones(private_repo, tmp_path):
    mail = "real.name" + AT + "firma.org"
    num = "0347 " + "987 6543"
    (private_repo / "main.py").write_text(f"# old test: {mail} {num}\n")
    run(["git", "commit", "-q", "-am", "old test with personal data"], private_repo)
    (private_repo / "main.py").write_text("print('clean now')\n")
    run(["git", "commit", "-q", "-am", "removed"], private_repo)
    logs = []
    assert ex.export(private_repo, tmp_path / "a", denylist=ex.Denylist.empty(), history=True, log=logs.append) == 0
    assert "2 email addresses" not in "\n".join(logs) and "1 email addresses" in "\n".join(logs)      # it saw the old values
    assert "0 line(s) of the export contain one of them" in "\n".join(logs)
    assert mail not in "\n".join(logs) and "987 6543" not in "\n".join(logs)
    (private_repo / "README.md").write_text("Contact: +39 347 " + "987 6543 or " + mail.upper() + "\n")   # back in, in other notations
    run(["git", "commit", "-q", "-am", "personal data is back"], private_repo)
    logs = []
    assert ex.export(private_repo, tmp_path / "b", denylist=ex.Denylist.empty(), history=True, log=logs.append) == 1
    text = "\n".join(logs)
    assert text.count("that is in the private history") == 2 and "README.md:1" in text and "987 6543" not in text and mail not in text.lower()


def test_the_global_git_identity_is_never_used_for_the_commit(private_repo, tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitconfig").write_text("[user]\n\tname = Real Person\n\temail = real.person" + AT + "firma.org\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(home / ".gitconfig"))
    out = tmp_path / "public"
    ex.export(private_repo, out, commit=True, author="Demo <demo" + AT + "example.com>", denylist=ex.Denylist.empty(), log=lambda *_: None)
    assert run(["git", "log", "--format=%an %ae %cn %ce"], out).strip() == "Demo demo" + AT + "example.com Demo demo" + AT + "example.com"


# ---- the repository passes its own scan ---------------------------------------------------------------------------------
def test_the_repository_itself_passes_the_scan():
    """Regression test for publishability: no email, phone, address, key shape or posting-like text in what would be exported.
    Personal words are not checked here (that list is private); the export run checks them."""
    files = None
    if shutil.which("git"):
        probe = subprocess.run(["git", "ls-files", "-z"], cwd=APP, capture_output=True, text=True)
        if probe.returncode == 0 and probe.stdout:
            files = [n for n in probe.stdout.split("\0") if n]
    if files is None:                                          # an export folder that is not (yet) a repository
        files = ex.list_files(APP)
    files = [f for f in files if not ex.exclusion(f) and (APP / f).is_file()]
    assert len(files) > 100
    bad = ex.hits(ex.scan_tree(APP, files, ex.Denylist.empty()))
    assert not bad, ex.format_report(bad)
