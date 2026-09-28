"""Integral's test limiter bypass must cover the substrate auth cap too."""

import pytest

from app.main import server


@pytest.mark.smoke
def test_test_mode_disables_the_substrate_auth_cap():
    assert server.config.auth.enabled
    assert not server.config.rate_limit.auth_entrypoint_rate_limit_enabled
