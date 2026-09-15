"""Substrate hook framework (DR-30-02).

Public API:
- registry: hook-point catalog + per-workspace tool catalog
- resolver: match bindings for (workspace, point, payload)
- dispatch: declarative interpreter OR tool dispatcher
- install_hook: app_lifecycle integration

Bundles never import from this module — they reach substrate only
through ToolContext (registry.ToolContext).
"""

from __future__ import annotations
