"""Exercise urllib redirects without opening any network connection."""

import io
import socket
import ssl
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.api.link_preview import _fetch_preview
from app.exceptions import BadRequestError

pytestmark = pytest.mark.smoke


@pytest.fixture
def wire(monkeypatch):
    """Replace only socket IO; retain urllib's real HTTP/redirect machinery."""
    calls = Counter()
    connected = []
    requests = []
    addresses = {}
    responses = {}
    answers = {"origin.test": ["8.8.8.8"], "redirect.test": ["1.1.1.1"]}

    def resolve(host, port, *args, **kwargs):
        calls[host] += 1
        values = answers[host]
        if callable(values):
            values = values(calls[host])
        if isinstance(values, Exception):
            raise values
        return [
            (
                socket.AF_INET6 if ":" in ip else socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                (ip, port or 0, 0, 0) if ":" in ip else (ip, port or 0),
            )
            for ip in values
        ]

    def connect(sock, address):
        connected.append(address)
        addresses[id(sock)] = address[0]

    def sendall(sock, body, *args):
        requests.append(body)

    def makefile(sock, *args, **kwargs):
        return io.BytesIO(responses[addresses[id(sock)]])

    for key in (
        "http_proxy",
        "https_proxy",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "all_proxy",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "sendall", sendall)
    monkeypatch.setattr(socket.socket, "makefile", makefile)
    responses["8.8.8.8"] = (
        b"HTTP/1.1 302 Found\r\nLocation: http://redirect.test/final\r\n"
        b"Content-Length: 0\r\n\r\n"
    )
    responses["1.1.1.1"] = (
        b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n<title>Public</title>"
    )
    responses["127.0.0.1"] = (
        b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n<title>PRIVATE</title>"
    )
    return answers, responses, connected, requests, calls


def test_redirect_rebinding_is_rejected_before_private_connect(wire):
    answers, _, connected, _, _ = wire
    answers["redirect.test"] = lambda n: ["1.1.1.1" if n == 1 else "127.0.0.1"]
    with pytest.raises(BadRequestError):
        _fetch_preview("http://origin.test/")
    assert all(address[0] != "127.0.0.1" for address in connected)


def test_public_redirect_retains_host_header(wire):
    _, _, connected, requests, _ = wire
    assert _fetch_preview("http://origin.test/")["title"] == "Public"
    assert [ip for ip, _ in connected] == ["8.8.8.8", "1.1.1.1"]
    assert b"Host: redirect.test\r\n" in requests[-1]


@pytest.mark.parametrize(
    "values",
    [[], ["1.1.1.1", "127.0.0.1"], ["invalid"], socket.gaierror("unavailable")],
)
def test_connection_dns_fails_closed(wire, values):
    answers, _, connected, _, _ = wire
    answers["redirect.test"] = lambda n: ["1.1.1.1"] if n == 1 else values
    with pytest.raises(BadRequestError):
        _fetch_preview("http://origin.test/")
    assert connected == [("8.8.8.8", 80)]


def test_https_keeps_certificate_verification_and_sni(wire, monkeypatch):
    _, responses, _, requests, _ = wire
    responses["8.8.8.8"] = responses["1.1.1.1"]
    wrapped = []

    def wrap(context, sock, *, server_hostname, **kwargs):
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname is True
        wrapped.append(server_hostname)
        return sock

    monkeypatch.setattr(ssl.SSLContext, "wrap_socket", wrap)
    assert _fetch_preview("https://origin.test/")["title"] == "Public"
    assert wrapped == ["origin.test"]
    assert b"Host: origin.test\r\n" in requests[-1]


def test_environment_proxy_cannot_bypass_destination_check(wire, monkeypatch):
    monkeypatch.setenv("http_proxy", "http://proxy.test:3128")
    assert _fetch_preview("http://origin.test/")["title"] == "Public"
    assert "proxy.test" not in wire[-1]


def test_concurrent_fetches_never_replace_process_resolver(wire):
    before = socket.getaddrinfo
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(_fetch_preview, ["http://origin.test/"] * 8))
    assert all(result["title"] == "Public" for result in results)
    assert socket.getaddrinfo is before


def test_ipv6_connection_uses_validated_numeric_address(wire):
    answers, responses, connected, requests, _ = wire
    answers["origin.test"] = ["2606:4700:4700::1111"]
    responses["2606:4700:4700::1111"] = responses["1.1.1.1"]
    assert _fetch_preview("http://origin.test:8080/")["title"] == "Public"
    assert connected == [("2606:4700:4700::1111", 8080, 0, 0)]
    assert b"Host: origin.test:8080\r\n" in requests[-1]


def test_trusted_internal_service_does_not_grant_preview_access(wire, monkeypatch):
    monkeypatch.setenv("INTEGRAL_OUTBOUND_TRUSTED_HOSTS", "origin.test")
    wire[0]["origin.test"] = ["127.0.0.1"]
    with pytest.raises(BadRequestError):
        _fetch_preview("http://origin.test/")
    assert not wire[2]


def test_failed_public_address_retries_only_validated_public_answers(wire, monkeypatch):
    answers, responses, connected, _, calls = wire
    answers["origin.test"] = ["8.8.8.8", "1.1.1.1"]
    responses["8.8.8.8"] = responses["1.1.1.1"]
    real_connect = socket.socket.connect

    def connect(sock, address):
        if address[0] == "8.8.8.8":
            raise ConnectionRefusedError("fixture")
        return real_connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    assert _fetch_preview("http://origin.test/")["title"] == "Public"
    assert connected == [("1.1.1.1", 80)]
    # One policy lookup and one exact connection lookup, with no third lookup
    # after the failed address that could return an unchecked destination.
    assert calls["origin.test"] == 2


def test_private_literal_redirect_is_refused_before_connect(wire):
    wire[1]["8.8.8.8"] = (
        b"HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1/private\r\n"
        b"Content-Length: 0\r\n\r\n"
    )
    with pytest.raises(BadRequestError):
        _fetch_preview("http://origin.test/")
    assert wire[2] == [("8.8.8.8", 80)]


def test_redirect_loop_remains_bounded(wire):
    from urllib.error import HTTPError

    wire[1]["1.1.1.1"] = wire[1]["8.8.8.8"]
    with pytest.raises(HTTPError):
        _fetch_preview("http://origin.test/")
    assert len(wire[2]) <= 6


def test_https_redirect_preserves_each_hostname_and_verification(wire, monkeypatch):
    wire[1]["8.8.8.8"] = wire[1]["8.8.8.8"].replace(b"http://", b"https://")
    hosts = []

    def wrap(context, sock, *, server_hostname, **kwargs):
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname
        hosts.append(server_hostname)
        return sock

    monkeypatch.setattr(ssl.SSLContext, "wrap_socket", wrap)
    assert _fetch_preview("https://origin.test/")["title"] == "Public"
    assert hosts == ["origin.test", "redirect.test"]


@pytest.mark.asyncio
async def test_denied_redirect_is_not_cached(authenticated_client, wire):
    from app.api.link_preview import _preview_cache

    wire[0]["redirect.test"] = lambda n: ["1.1.1.1"] if n == 1 else ["127.0.0.1"]
    url = "http://origin.test/no-cache"
    response = await authenticated_client.get("/api/link-preview", params={"url": url})
    assert response.status_code == 400
    assert url not in _preview_cache
    assert wire[2] == [("8.8.8.8", 80)]
