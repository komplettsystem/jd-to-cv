"""The evals page for a stranger: an index of what is on it, a change log that fits on a screen, plain labels, and no path to a file
that only the owner has (a link to the repository appears only when REPO_URL is set)."""
import re

from webfixtures import Browser, make_app


def page(tmp_path, monkeypatch, **settings):
    return Browser(make_app(tmp_path, monkeypatch, **settings), addr="127.0.0.1").get("/admin/evals").data.decode()


def test_the_page_starts_with_an_index_whose_links_all_land_somewhere(tmp_path, monkeypatch):
    html = page(tmp_path, monkeypatch)
    nav = re.search(r'<nav class="page-index"[^>]*aria-label="On this page".*?</nav>', html, re.S)
    assert nav
    links = re.findall(r'href="#([\w-]+)"', nav.group(0))
    assert len(links) >= 5
    for target in links:
        assert f'id="{target}"' in html, target


def test_every_change_is_a_collapsed_entry_with_its_title_in_the_summary(tmp_path, monkeypatch):
    html = page(tmp_path, monkeypatch)
    entries = re.findall(r'<details class="change"[^>]*>\s*<summary>(.*?)</summary>', html, re.S)
    assert len(entries) >= 15
    assert all("<h3" in e for e in entries)
    assert "From word matching to reading" in html
    assert 'data-toggle-all="#change-log"' in html and 'id="change-log"' in html


def test_the_labels_say_what_they_are(tmp_path, monkeypatch):
    html = page(tmp_path, monkeypatch)
    assert "<strong>What it did.</strong>" not in html and "<strong>On the benchmark.</strong>" not in html
    assert "<strong>Measured.</strong>" in html
    assert "<strong>Benchmark, cases passed of 36.</strong>" in html
    assert "Each entry says" in html     # a sentence above the list explains the entries


def test_no_path_to_a_private_file_unless_a_repository_is_set(tmp_path, monkeypatch):
    html = page(tmp_path, monkeypatch)
    assert "MODEL-COMPARISON" not in html and "history_prompts" not in html and "docs/" not in html
    html = page(tmp_path, monkeypatch, repo_url="https://example.test/owner/repo")
    assert 'href="https://example.test/owner/repo/blob/main/docs/MODEL-COMPARISON-2026-11-13.md"' in html


def test_the_demo_has_no_run_form_and_says_why(tmp_path, monkeypatch):
    html = page(tmp_path, monkeypatch, demo=True)
    assert 'action="/admin/evals/run"' not in html and "not available in the demo" in html
    assert 'action="/admin/evals/run"' in page(tmp_path, monkeypatch)


def test_an_entry_without_a_benchmark_change_has_no_empty_cases_line(tmp_path, monkeypatch):
    html = page(tmp_path, monkeypatch)
    assert not re.search(r'<span class="change-delta">\s*cases</span>', html)
    assert 'class="change-delta"' in html      # entries with a measured change still show it
