"""Locked MCP / agent tool-name overrides.

Phase 4 RET-04 + Phase 6 Plan 06-04: when auto-derivation via
``_name_from_path`` would produce a name that conflicts with a locked
contract, the catalogue builders consult this map first.

Keyed on ``(path, method)`` tuples (same shape as the FastAPI APIRoute
walk). SINGLE source of truth for overrides — do not introduce a parallel
override surface elsewhere.

Single-helper grep gate:
  grep -rE "^MCP_TOOL_NAME_OVERRIDES\\s*[:=]" backend/app/  → expect 1 match
  (this module)
"""

from __future__ import annotations

from typing import Dict, Tuple

# Per CONTEXT lock #10: ``/api/retrieve`` POST auto-derives to
# ``integral_create_retrieve`` but the locked tool-name for the
# hybrid-retrieval surface is ``integral_query`` (RET-04 / ROADMAP AC#5).
# Phase 6 Plan 06-04 additions (locked-post-research #9) anchor the
# locked agent-facing names from ROADMAP MCP-04 + AC#3..#5.
MCP_TOOL_NAME_OVERRIDES: Dict[Tuple[str, str], str] = {
    ("/api/retrieve", "POST"): "integral_query",
    ("/api/operational-models/author", "POST"): "integral_author_model",
    (
        "/api/operational-models/{operational_model_id}/modify",
        "POST",
    ): "integral_modify_model",
    ("/api/operational-models", "GET"): "integral_list_models",
}

__all__ = ["MCP_TOOL_NAME_OVERRIDES"]
