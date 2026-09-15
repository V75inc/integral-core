"""Manifest-driven tool surface (M2a).

The Integral agent tool surface is defined ONCE in
``backend/app/agentive/tool_manifest.yaml`` (the single source of truth — see
``docs/agentive/TOOLING_SPEC.md``). This package parses that manifest into
typed :class:`~app.agentive.tooling.manifest.ToolSpec` objects and validates
the registry against the substrate's policy-action vocabulary and the staging
executor registry.

Downstream surfaces (``IntegralAction.get_tools()``, the external tool
endpoints in ``app.agentive.api.agent_tools``, jvagent SKILL.md
``allowed-tools``) bind to the parsed registry; nothing here invents
capability the substrate cannot back.
"""

from app.agentive.tooling.bindings import TOOL_BINDINGS, ToolBinding
from app.agentive.tooling.catalogue import build_tool_catalogue
from app.agentive.tooling.dispatch import ToolResult, dispatch_tool
from app.agentive.tooling.manifest import (
    HttpSpec,
    ManifestError,
    ToolSpec,
    load_manifest,
    validate_manifest,
)
from app.agentive.tooling.name_overrides import MCP_TOOL_NAME_OVERRIDES

__all__ = [
    "HttpSpec",
    "ManifestError",
    "ToolSpec",
    "load_manifest",
    "validate_manifest",
    "ToolBinding",
    "TOOL_BINDINGS",
    "ToolResult",
    "dispatch_tool",
    "build_tool_catalogue",
    "MCP_TOOL_NAME_OVERRIDES",
]
