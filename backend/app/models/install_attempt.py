"""InstallAttempt — append-only install saga checkpoint (F0 / I-GRAPH-02).

Log-shaped Object (not a Node): no graph edges, no walkers. Records each
step of an App install/upgrade for admin diagnostics without DB diving.
"""

from __future__ import annotations

from typing import Optional

from jvspatial.core import Object
from jvspatial.core.annotations import attribute


class InstallAttempt(Object):
    """One step (or terminal status) of an App install/upgrade saga."""

    workspace_id: str = attribute(default="", indexed=True)
    app_id: str = attribute(default="", indexed=True)
    actor_id: str = attribute(default="", indexed=True)
    package_slug: str = attribute(default="", indexed=True)
    step: str = attribute(default="", indexed=True)
    status: str = attribute(default="ok", indexed=True)  # ok | failed | rolled_back
    error: str = ""
    created_at: Optional[str] = None
