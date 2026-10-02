"""Stable source_kind values for transactional email attribution and routing."""

from __future__ import annotations

from typing import FrozenSet

# Always use platform EMAIL_PROVIDER / keys — never workspace delivery.
SYSTEM_EMAIL_SOURCE_KINDS: FrozenSet[str] = frozenset(
    {
        "password_reset",
        "email_verification",
    }
)
