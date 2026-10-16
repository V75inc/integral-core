"""Canonical configuration identity for one explicitly configured schedule."""

from __future__ import annotations

import hashlib
import json
from typing import Any

_DEFAULTS: dict[str, Any] = {
    "user_id": "",
    "workspace_id": "",
    "thread_id": "",
    "agent_id": "",
    "instruction": "",
    "delivery_mode": "agent",
    "cron": "",
    "timezone": "UTC",
    "write_scope": [],
    "max_runs": None,
    "source_app_id": "",
    "source_schedule_key": "",
}


def routine_mandate_fingerprint(routine: Any) -> str:
    """Exclude progress, include all reviewed execution and scope configuration."""
    fields = {
        key: (
            routine.get(key, default)
            if isinstance(routine, dict)
            else getattr(routine, key, default)
        )
        for key, default in _DEFAULTS.items()
    }
    fields["write_scope"] = sorted(
        fields["write_scope"] or [], key=lambda grant: json.dumps(grant, sort_keys=True)
    )
    return hashlib.sha256(
        json.dumps(
            fields, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()
