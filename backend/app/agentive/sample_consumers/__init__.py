"""Sample consumers for the change-feed (EVT-03, D-11).

Conditionally loaded inside the AGENTIVE_ENABLED block in app/main.py AND
further gated on AGENTIVE_SAMPLE_CONSUMER_ENABLED (default off in production).

Demonstrates the full WS subscribe + event emit + consumer hook + entry write +
ChangeEvent emit loop using the in-process consumer-hook surface added by Plan
02-04 (event_subscription_registry.register_consumer_hook). The consumer
subscribes through the hook system rather than opening a WS connection of its
own — keeps the demo simple and avoids extra IO for in-process subscribers.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def register_sample_consumers() -> None:
    """Wire enabled sample consumers into the change-event registry.

    Idempotent — safe to call multiple times. Returns silently when the opt-in
    flag is unset/0 (default-off behavior in production deployments).
    """
    if os.getenv("AGENTIVE_SAMPLE_CONSUMER_ENABLED", "0") != "1":
        return

    # Import lazily so the module surface is not loaded when the flag is off.
    from app.agentive.sample_consumers import summary_drafter

    summary_drafter.start()
    logger.info("sample_consumers: summary_drafter registered")
