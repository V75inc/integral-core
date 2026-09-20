"""Walkers — multi-hop graph traversals built on jvspatial's Walker primitive.

Per AGENTS.md pillar 3: behavior travels via Walkers. This namespace collects
production walkers; the first one is ``cross_app_resolver`` (Phase 10 Plan
10-06).
"""

from app.services.walkers.cross_app_resolver import (
    CrossAppInboundReferenceWalker,
    find_inbound_references,
)

__all__ = [
    "CrossAppInboundReferenceWalker",
    "find_inbound_references",
]
