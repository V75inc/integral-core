"""Response models for the agent-preference endpoints.

PUT body is bound as @endpoint kwargs (provider_id, agent_id) per the
established codebase convention (apps.py / ai_chat.py) — see notes in
api/agent_preferences.py.
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


class AgentPreferenceValue(BaseModel):
    """The persisted preference; serialised in GET / PUT responses."""

    provider_id: str
    agent_id: str


class AgentPreferenceResponse(BaseModel):
    """Wrapper so callers can distinguish `null` from missing keys."""

    preference: Optional[AgentPreferenceValue] = None


ResidentPermissionMode = Literal["ask", "auto", "custom", "full"]


class ResidentPermissionValue(BaseModel):
    """Legacy track fields project only the currently active custom selection."""

    mode: ResidentPermissionMode = "ask"
    custom_track_ids: list[str] = Field(default_factory=list)
    custom_app_ids: list[str] = Field(default_factory=list)
    track_ids: list[str] = Field(default_factory=list)
    track_id: str = ""
    revision: str = ""

    @model_validator(mode="before")
    @classmethod
    def normalize_selection(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        legacy = value.get("track_id")
        ids = value.get(
            "custom_track_ids", value.get("track_ids", [legacy] if legacy else [])
        )
        mode = value.get("mode", "custom" if ids else "ask")
        active = ids if mode == "custom" else []
        return {
            **value,
            "mode": mode,
            "custom_track_ids": ids,
            "track_ids": active,
            "track_id": active[0] if len(active) == 1 else "",
        }
