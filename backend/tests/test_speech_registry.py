"""Speech provider registry, rate limiter, and provider/CSP/schema parity."""

from __future__ import annotations

import pytest

from app.agentive.services.speech.base import primary_language_subtag
from app.agentive.services.speech.rate_limit import SlidingWindowLimiter, parse_rate
from app.agentive.services.speech.registry import (
    get_stt_provider,
    list_stt_providers,
    register_stt_provider,
    reset_stt_registry,
)
from app.middleware.security_headers import SPEECH_CONNECT_ORIGINS
from app.schemas.model_credentials import SPEECH_CAPABLE_PROVIDERS


@pytest.fixture(autouse=True)
def _fresh_registry():
    reset_stt_registry()
    yield
    reset_stt_registry()


def test_builtin_openai_adapter_is_registered():
    """The OpenAI adapter loads on first lookup."""
    assert get_stt_provider("openai").id == "openai"


def test_unknown_provider_raises_key_error():
    """Looking up an unregistered provider raises ``KeyError``."""
    with pytest.raises(KeyError):
        get_stt_provider("no-such-vendor")


def test_duplicate_registration_raises():
    """A provider id can be registered once."""
    existing = get_stt_provider("openai")
    with pytest.raises(ValueError, match="already registered"):
        register_stt_provider(existing)


def test_registry_matches_speech_capable_credential_providers():
    """Every adapter has a credential slot provider, and vice versa.

    ``SpeechProvider`` in the model-credential schema gates which provider the
    owner may pick for the speech slot; an adapter with no slot provider is
    unreachable, and a slot provider with no adapter would resolve to nothing.
    """
    model_credential_adapters = {
        p.id for p in list_stt_providers() if p.credential_source == "model_credentials"
    }
    assert model_credential_adapters == set(SPEECH_CAPABLE_PROVIDERS)


def test_streaming_adapter_origins_are_in_the_csp():
    """A streaming adapter's browser origins must be allowed by connect-src.

    ``test_security_headers_speech`` checks SPEECH_CONNECT_ORIGINS reaches
    both nginx templates, so this closes the loop from adapter to header.
    """
    allowed = set(SPEECH_CONNECT_ORIGINS.split())
    for provider in list_stt_providers():
        if provider.capabilities.streaming:
            missing = set(provider.capabilities.browser_connect_origins) - allowed
            assert not missing, f"{provider.id} origins missing from CSP: {missing}"


@pytest.mark.parametrize(
    ("spec", "expected"), [("20/60", (20, 60.0)), ("1/0.5", (1, 0.5))]
)
def test_parse_rate_accepts_count_over_seconds(spec, expected):
    """``<count>/<seconds>`` parses to a (limit, window) pair."""
    assert parse_rate(spec) == expected


@pytest.mark.parametrize("spec", ["", "20", "x/60", "0/60", "5/0"])
def test_parse_rate_rejects_malformed_specs(spec):
    """Malformed or non-positive specs raise ``ValueError``."""
    with pytest.raises(ValueError):
        parse_rate(spec)


def test_sliding_window_limits_per_key_and_recovers():
    """Hits beyond the limit are refused until the window slides past them."""
    now = [0.0]
    limiter = SlidingWindowLimiter(2, 60.0, clock=lambda: now[0])

    assert limiter.hit("alice") is True
    assert limiter.hit("alice") is True
    assert limiter.hit("alice") is False
    assert limiter.hit("bob") is True  # keys are independent

    now[0] = 61.0
    assert limiter.hit("alice") is True


@pytest.mark.parametrize(
    ("value", "expected"),
    [("en-US", "en"), ("PT-br", "pt"), ("auto", None), ("", None), (None, None)],
)
def test_primary_language_subtag(value, expected):
    """BCP-47 tags reduce to their primary subtag; auto means none."""
    assert primary_language_subtag(value) == expected
