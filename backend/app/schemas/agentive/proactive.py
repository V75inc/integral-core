"""Schemas for agentive/api/proactive.py — proactive intelligence bodies."""

from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class ProactiveLogPushRequest(BaseModel):
    """Body for /proactive/log-push — logging-only intake.

    Per D-06: response is {accepted, logged_at}. Fields capture the proactive
    intent payload jvagent's IntegralProactiveAction sends. Field names match
    the canonical test contract (channel, message_type, payload).
    """

    channel: str = "whatsapp"
    message_type: str = "digest"
    payload: Dict[str, Any] = Field(default_factory=dict)
    schedule: str = "immediate"
    target_user_id: Optional[str] = None

    model_config = {"extra": "forbid"}


class ProactivePreferencesRequest(BaseModel):
    """Body for PATCH /proactive/preferences — open shape (forwarded to user prefs)."""

    digest_enabled: Optional[bool] = None
    digest_time: Optional[str] = None
    timezone: Optional[str] = None
    reminders_enabled: Optional[bool] = None

    # Allow extra prefs — the current contract forwards arbitrary preference keys.
    model_config = {"extra": "allow"}
