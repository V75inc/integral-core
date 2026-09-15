"""SSRF guards for the link-preview fetch.

`validate_public_http_url` checks the URL the caller submitted. It does not
constrain where the *fetch* ends up, and `urlopen` follows redirects by default
-- so a public URL answering a 302 toward `http://169.254.169.254/...` (cloud
instance metadata) or any internal host was fetched unvalidated, with the
response surfacing in the preview title/description. Validating the submitted
URL and then following redirects blindly is equivalent to not validating.

These tests drive the redirect handler and the validator directly rather than
through the endpoint, so they need no auth fixture and no outbound network.
"""

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.api.link_preview import _fetch_preview, _ValidatingRedirectHandler
from app.exceptions import BadRequestError
from app.services.url_safety import validate_public_http_url_sync

# Part of the per-PR smoke gate (see pyproject [tool.pytest.ini_options] markers).
# CI bills only this subset; the full suite runs locally via `make verify` and nightly.
pytestmark = pytest.mark.smoke


class TestSyncValidator:
    """The sync variant must reject exactly what the async one does."""

    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1/",
            "http://localhost/",
            "http://169.254.169.254/latest/meta-data/",  # AWS IMDS
            "http://metadata.google.internal/",
            "http://10.0.0.5/",
            "http://192.168.1.1/",
            "http://[::1]/",
            "file:///etc/passwd",
            "gopher://example.com/",
        ],
    )
    def test_rejects_non_public_targets(self, url):
        with pytest.raises(BadRequestError):
            validate_public_http_url_sync(url)

    def test_accepts_a_public_host(self):
        # Resolves publicly; no request is made.
        validate_public_http_url_sync("https://example.com/")


class TestRedirectHandlerValidates:
    """The handler is what closes the bypass."""

    def _handler(self):
        return _ValidatingRedirectHandler()

    @pytest.mark.parametrize(
        "target",
        [
            "http://169.254.169.254/latest/meta-data/",
            "http://127.0.0.1:4000/api/users",
            "http://10.1.2.3/internal",
        ],
    )
    def test_redirect_to_private_target_is_refused(self, target):
        h = self._handler()
        with pytest.raises(BadRequestError):
            h.redirect_request(None, None, 302, "Found", {}, target)

    def test_redirect_limit_is_bounded(self):
        assert _ValidatingRedirectHandler.max_redirections <= 5


class _RedirectToMetadata(BaseHTTPRequestHandler):
    """Public-looking server whose response redirects somewhere internal."""

    def do_GET(self):  # noqa: N802
        self.send_response(302)
        self.send_header("Location", "http://169.254.169.254/latest/meta-data/")
        self.end_headers()

    def log_message(self, *args):  # silence test output
        return


@pytest.fixture
def redirecting_server():
    srv = HTTPServer(("127.0.0.1", 0), _RedirectToMetadata)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}/"
    srv.shutdown()
    srv.server_close()


class TestEndToEndRedirectIsBlocked:
    def test_fetch_refuses_to_follow_redirect_to_metadata(self, redirecting_server):
        """The whole point: the fetch must not reach the redirect target.

        `_fetch_preview` is called directly with a loopback origin, which the
        endpoint itself would have rejected up front -- the origin here is only
        a stand-in for a public host that answers with a hostile redirect.
        """
        with pytest.raises(BadRequestError):
            _fetch_preview(redirecting_server)


class TestDnsPinning:
    """Closes the check-then-connect (DNS rebinding) gap.

    The validator resolved the host to decide it was public; urlopen then
    resolved it AGAIN when connecting. A hostile resolver can answer public
    first and private second, so the check and the connection disagreed about
    where they pointed.
    """

    def test_pin_restores_getaddrinfo_afterwards(self):
        """The patch is process-global, so a leak would corrupt all DNS."""
        import socket

        from app.api.link_preview import _pinned_dns

        before = socket.getaddrinfo
        with _pinned_dns("https://example.com/"):
            assert socket.getaddrinfo is not before, "pin did not take effect"
        assert socket.getaddrinfo is before, "getaddrinfo was not restored"

    def test_pin_restores_even_when_the_body_raises(self):
        import socket

        from app.api.link_preview import _pinned_dns

        before = socket.getaddrinfo
        with pytest.raises(RuntimeError):
            with _pinned_dns("https://example.com/"):
                raise RuntimeError("boom")
        assert socket.getaddrinfo is before, "getaddrinfo leaked after an exception"

    def test_pin_refuses_a_host_resolving_private(self, monkeypatch):
        """Pinning must never lock the fetch onto a private address."""
        import socket

        from app.api.link_preview import _pinned_dns

        real = socket.getaddrinfo

        def fake(host, port, *a, **kw):
            if host == "rebind.test":
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0))]
            return real(host, port, *a, **kw)

        monkeypatch.setattr(socket, "getaddrinfo", fake)
        with pytest.raises(BadRequestError):
            with _pinned_dns("http://rebind.test/"):
                pass

    def test_concurrent_pins_do_not_corrupt_each_other(self):
        """`_fetch_preview` runs under asyncio.to_thread, so pins can overlap.

        Without the lock, one request's pin answers another's lookups and the
        first `finally` to run unpins a fetch still in flight.
        """
        import socket
        import threading as _t

        from app.api.link_preview import _pinned_dns

        before = socket.getaddrinfo
        errors: list = []

        def worker():
            try:
                for _ in range(5):
                    with _pinned_dns("https://example.com/"):
                        pass
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [_t.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"concurrent pinning raised: {errors}"
        assert (
            socket.getaddrinfo is before
        ), "getaddrinfo did not return to the original after concurrent pins"
