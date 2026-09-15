"""Optional Sentry SDK initialization.

Activated when ``SENTRY_DSN`` is set. The integration is intentionally
soft: if the ``sentry-sdk`` package is not installed, we just log and
move on. This lets us add Sentry support without forcing every developer
to install an extra dependency for local work.

Config knobs (all env-driven):
  SENTRY_DSN              — required to activate Sentry
  SENTRY_ENVIRONMENT      — e.g. "production", "staging" (default: "production")
  SENTRY_TRACES_SAMPLE_RATE — default 0.0 (no APM tracing); set to 0.05–0.20 in prod
  SENTRY_RELEASE          — release identifier (e.g. git sha)
  SENTRY_SEND_PII         — "1" to include user identifiers (default off)
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def init_sentry_if_configured() -> bool:
    """Initialize Sentry when DSN is configured. Returns True on success."""
    dsn = (os.getenv("SENTRY_DSN") or "").strip()
    if not dsn:
        return False

    try:
        import sentry_sdk
        from sentry_sdk.integrations.logging import LoggingIntegration
    except ImportError:
        logger.warning(
            "SENTRY_DSN is set but sentry-sdk is not installed. "
            "Run `pip install sentry-sdk[fastapi]` to enable error reporting."
        )
        return False

    try:
        traces_rate = float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0") or "0")
    except ValueError:
        traces_rate = 0.0

    sentry_sdk.init(
        dsn=dsn,
        environment=os.getenv("SENTRY_ENVIRONMENT", "production"),
        release=os.getenv("SENTRY_RELEASE") or None,
        send_default_pii=os.getenv("SENTRY_SEND_PII", "0") == "1",
        traces_sample_rate=traces_rate,
        # Capture WARNING+ as breadcrumbs, ERROR+ as events.
        integrations=[
            LoggingIntegration(level=logging.WARNING, event_level=logging.ERROR),
        ],
    )
    logger.info(
        "Sentry initialized (environment=%s, traces=%.3f)",
        os.getenv("SENTRY_ENVIRONMENT", "production"),
        traces_rate,
    )
    return True
