"""Scalar-keyed public Web Chat request budget counters (I-GRAPH-02)."""

from jvspatial.core import Object
from pydantic import Field


class WebChatBudgetCounter(Object):
    """One fixed window at a stable public, workspace, or provision key."""

    policy: str
    window: int
    starts: int
    sends: int
    visitor_sends: dict[str, int] = Field(default_factory=dict)
