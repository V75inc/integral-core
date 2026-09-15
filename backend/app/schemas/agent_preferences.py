"""Response models for the agent-preference endpoints.

PUT body is bound as @endpoint kwargs (provider_id, agent_id) per the
established codebase convention (apps.py / ai_chat.py) — see notes in
api/agent_preferences.py.
"""

from typing import Optional

from pydantic import BaseModel


class AgentPreferenceValue(BaseModel):
    """The persisted preference; serialised in GET / PUT responses."""

    provider_id: str
    agent_id: str


class AgentPreferenceResponse(BaseModel):
    """Wrapper so callers can distinguish `null` from missing keys."""

    preference: Optional[AgentPreferenceValue] = None
