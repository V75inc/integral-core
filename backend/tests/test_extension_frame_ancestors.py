"""frame-ancestors for package view documents."""

import pytest

from app.services.app_extension_views import extension_frame_ancestors

pytestmark = pytest.mark.smoke


def test_extension_frame_ancestors_allows_public_spa_beside_local_origin():
    policy = extension_frame_ancestors(
        "http://localhost:9006",
        "https://app.example.com",
    )
    assert policy == "'self' http://localhost:9006 https://app.example.com"


def test_extension_frame_ancestors_drops_path_and_duplicates():
    policy = extension_frame_ancestors(
        "http://localhost:9006/",
        "http://localhost:9006/oauth/authorize",
        "",
        "not-a-url",
    )
    assert policy == "'self' http://localhost:9006"


@pytest.mark.parametrize(
    "origin",
    [
        "https://example.com:bad",
        "https://example.com:65536",
        "https://[invalid",
        "https://[::1]:9006",
        "https://user:password@example.com",
        "https://*.example.com",
        "https://bücher.example",
        "https://example.com;script-src 'unsafe-inline'",
        "https://example.com,https://other.example.com",
        "https://example.com\r\nX-Injected: yes",
        "https://exam\tple.com",
        "https://example.com\\evil.example",
        "file:///tmp/index.html",
        "//example.com",
    ],
)
def test_extension_frame_ancestors_ignores_invalid_origin_without_losing_valid_one(
    origin,
):
    assert extension_frame_ancestors(origin, "https://valid.example") == (
        "'self' https://valid.example"
    )


def test_extension_frame_ancestors_normalizes_hosts_and_default_ports():
    assert extension_frame_ancestors(
        " HTTPS://EXAMPLE.COM:443/path?query=value#section ",
        "https://example.com",
        "http://example.com:80",
        "https://xn--bcher-kva.example:9443/path",
        "http://localhost:0",
    ) == (
        "'self' https://example.com http://example.com "
        "https://xn--bcher-kva.example:9443 http://localhost:0"
    )


def test_extension_frame_ancestors_defaults_to_self():
    assert extension_frame_ancestors() == "'self'"
