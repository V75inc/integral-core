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

    def test_accepts_a_public_host(self, monkeypatch):
        # DNS is an input to this unit test, not a dependency on the runner's
        # resolver configuration. Corporate DNS and secure proxies can map
        # example.com to a private inspection address while the guard remains
        # correct to reject that live answer.
        import socket

        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda *args, **kwargs: [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))
            ],
        )
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


def test_direct_fetch_rejects_loopback_before_socket_io():
    """Helper callers receive the same initial-target guard as the endpoint."""
    with pytest.raises(BadRequestError):
        _fetch_preview("http://127.0.0.1:4000/private")
