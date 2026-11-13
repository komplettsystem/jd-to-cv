import socket

import pytest

import ingest


def fake_dns(ip):
    return lambda host, port, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.5", "192.168.1.9", "169.254.169.254", "::1"])
def test_private_and_metadata_addresses_are_refused(monkeypatch, ip):
    monkeypatch.setattr(socket, "getaddrinfo", fake_dns(ip))
    with pytest.raises(ValueError, match="private or local"):
        ingest.check_public_address("http://example.test/job")


def test_public_addresses_and_schemes(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", fake_dns("93.184.216.34"))
    ingest.check_public_address("https://example.test/job")
    for bad in ("ftp://example.test/x", "file:///etc/passwd", "javascript:alert(1)", "http:///nohost"):
        with pytest.raises(ValueError):
            ingest.check_public_address(bad)


def test_html_to_text_drops_scripts_and_keeps_list_items():
    text = ingest.html_to_text("<html><script>evil()</script><h1>Role</h1><ul><li>One</li><li>Two</li></ul></html>")
    assert "evil" not in text and "- One" in text and "- Two" in text


def test_uploads_accept_text_and_html_but_not_pdf():
    assert ingest.read_upload("a.txt", b"hello") == "hello"
    assert "Hi" in ingest.read_upload("a.html", b"<p>Hi</p>")
    with pytest.raises(ValueError, match="PDF"):
        ingest.read_upload("a.pdf", b"%PDF")
    with pytest.raises(ValueError):
        ingest.read_upload("a.exe", b"x")


def test_a_name_that_answers_differently_the_second_time_is_refused_before_any_connection(monkeypatch):
    """DNS rebinding: a public address for the check, a local one when the connection is made."""
    import socket
    answers = iter([[(2, 1, 6, "", ("93.184.216.34", 80))], [(2, 1, 6, "", ("127.0.0.1", 80))]])
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: next(answers))
    connected = []
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: connected.append(a) or (_ for _ in ()).throw(AssertionError("connected")))
    with pytest.raises(ValueError, match="private or local"):
        ingest.fetch_url("http://rebind.example/job")
    assert not connected
