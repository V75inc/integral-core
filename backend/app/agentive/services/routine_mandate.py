"""Canonical configuration identity for one explicitly configured schedule."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.schemas.agentive.routines import DURABLE_OBSERVATION_MODES

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
    if fields["delivery_mode"] == "task_watch":
        fields["watch_entry_id"] = (
            routine.get("watch_entry_id", "")
            if isinstance(routine, dict)
            else routine.watch_entry_id
        )
        fingerprint = (
            routine.get("watch_fingerprint", "")
            if isinstance(routine, dict)
            else routine.watch_fingerprint
        )
        if fingerprint.startswith("goal:"):
            fields["watch_goal"] = True
    if fields["delivery_mode"] in DURABLE_OBSERVATION_MODES:
        quiet_hours = (
            routine.get("quiet_hours")
            if isinstance(routine, dict)
            else routine.quiet_hours
        )
        if quiet_hours is not None:
            fields["quiet_hours"] = (
                quiet_hours
                if isinstance(quiet_hours, dict)
                else quiet_hours.model_dump()
            )
    fields["write_scope"] = sorted(
        fields["write_scope"] or [], key=lambda grant: json.dumps(grant, sort_keys=True)
    )
    return hashlib.sha256(
        json.dumps(
            fields, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()
