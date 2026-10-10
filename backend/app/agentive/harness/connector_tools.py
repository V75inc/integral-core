"""Project mounted MCP tools into native discovery without credential material."""

from __future__ import annotations

from typing import Any

from app.agentive.connectors.connector_resolution import (
    is_shared_row,
    row_slug,
    rows_for_workspace,
)
from app.agentive.connectors.mcp_tool_class import is_write_tool
from app.services.hooks.registry import get_workspace_tools


async def build_connector_tool_catalogue(
    *, workspace_id: str, principal_id: str
) -> list[dict[str, Any]]:
    """Expose only mounted tools reachable through the caller's connector rows.

    Prefer canonical keys over row aliases. The vetted local catalog determines
    read classification; remote annotations never grant read authority. Live
    broker, connector resolution, policy, schema and staging checks still apply
    at invocation. No auth state is copied into model-visible declarations.
    """
    rows = await rows_for_workspace(workspace_id)
    usable = {
        str(row.id): row
        for row in rows
        if getattr(row, "owner", "") == principal_id or is_shared_row(row)
    }
    slugs = {row_slug(row) for row in usable.values()}
    specs = get_workspace_tools(workspace_id)
    canonical_remotes = {
        (str(spec.get("_mcp_connector_slug") or ""), spec.get("_mcp_remote_name"))
        for spec in specs.values()
        if spec.get("_mcp_connector_slug") in slugs
    }
    catalogue = []
    for key, spec in sorted(specs.items()):
        slug = str(spec.get("_mcp_connector_slug") or "")
        row_id = str(spec.get("_mcp_connector_id") or "")
        if spec.get("agent_callable") is False:
            continue
        if slug:
            if slug not in slugs:
                continue
        elif row_id in usable:
            slug = row_slug(usable[row_id])
            if (slug, spec.get("_mcp_remote_name")) in canonical_remotes:
                continue
        else:
            continue
        schema = spec.get("input_schema")
        if not isinstance(schema, dict) or schema.get("type") != "object":
            continue
        catalogue.append(
            {
                "name": str(spec.get("key") or key),
                "description": str(spec.get("description") or key),
                "input_schema": schema,
                "op_class": (
                    "execute"
                    if is_write_tool(spec, auth_state={"catalog_slug": slug})
                    else "read"
                ),
            }
        )
    return catalogue
