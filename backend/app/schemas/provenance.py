"""Provenance + ActorKind — single source of truth for who-is-the-actor axis.

Per CONTEXT D-01 + D-10: ActorKind Literal lives here ONCE, used by both
Provenance.source (PROV-01) AND ChangeEvent.actor.kind (PROV-03 — see Plan 02-02).
Distinct from the Phase-1 AgentType Literal in app/agentive/types.py
(`jvagent|mcp|skill_bundle|custom`) — ActorKind is the WHO axis, AgentType is the
WHICH-RUNTIME axis.
"""

from datetime import datetime
from typing import Annotated, List, Literal, Optional

from pydantic import BaseModel, Field

from app.utils.time import utc_now

ActorKind = Literal["human", "agent", "connector", "system"]


class Provenance(BaseModel):
    """Typed provenance for every Entry (PROV-01, D-01).

    Single shared shape across human, agent, connector, and system writers.
    All five fields are populated on every Entry write — new Entries default
    to source='human' via ``Provenance.human_default()`` at create time.
    """

    source: ActorKind
    source_id: Optional[str] = None
    confidence: Annotated[float, Field(ge=0.0, le=1.0)] = 1.0
    derived_from: List[str] = Field(default_factory=list)
    synced_at: datetime = Field(default_factory=utc_now)

    model_config = {"extra": "forbid"}

    @classmethod
    def human_default(cls) -> "Provenance":
        """Default provenance for Entry rows created through the human API."""
        return cls(
            source="human",
            confidence=1.0,
            derived_from=[],
            synced_at=utc_now(),
        )
