"""The container entrypoint must not trust X-Forwarded-For from every peer.

``--forwarded-allow-ips '*'`` made uvicorn rewrite ``request.client`` to the
LEFTMOST (caller-typed) X-Forwarded-For entry for any peer, so the login / OTP
rate limiter keyed on an attacker-chosen address.
"""

from __future__ import annotations

import ipaddress
import re
from pathlib import Path

import pytest

_DOCKERFILE = Path(__file__).resolve().parents[1] / "Dockerfile"


@pytest.mark.smoke
def test_entrypoint_does_not_trust_every_forwarded_peer():
    """CMD no longer passes --forwarded-allow-ips '*'."""
    text = _DOCKERFILE.read_text()
    cmd = [line for line in text.splitlines() if line.startswith("CMD ")][-1]
    assert "--forwarded-allow-ips '*'" not in cmd
    assert '--forwarded-allow-ips "*"' not in cmd
    assert "${FORWARDED_ALLOW_IPS}" in cmd


@pytest.mark.smoke
def test_default_forwarded_allow_ips_is_private_ranges_only():
    """The FORWARDED_ALLOW_IPS default is private / loopback only."""
    text = _DOCKERFILE.read_text()
    m = re.search(r'^ENV FORWARDED_ALLOW_IPS="([^"]+)"', text, re.M)
    assert m, "FORWARDED_ALLOW_IPS default missing"
    entries = [e.strip() for e in m.group(1).split(",")]
    assert "*" not in entries
    for entry in entries:
        net = ipaddress.ip_network(entry, strict=False)
        assert net.is_private or net.is_loopback, entry
    # And uvicorn actually resolves the real client through such a proxy.
    from uvicorn.middleware.proxy_headers import _TrustedHosts

    trusted = _TrustedHosts(m.group(1))
    assert "10.0.0.7" in trusted and "203.0.113.9" not in trusted
    assert trusted.get_trusted_client_host("198.51.100.4, 10.0.0.7") == "198.51.100.4"
