"""Side-effect classification for mounted MCP tools (ADR-010 §6).

Mounted MCP tools reach a third-party system. ADR-010 §6 requires that nothing
leaves the workspace without a human bless, so the resident may not invoke a
write-capable remote tool directly — it must stage a card the user approves.

**Default-deny.** A remote tool is treated as a WRITE unless an operator-vetted
source positively says otherwise. The reason is that the only machine-readable
signal the protocol offers — ``annotations.readOnlyHint`` /
``destructiveHint`` on ``tools/list`` — is supplied *by the remote server*,
i.e. by the party the gate exists to constrain. The MCP specification says the
same thing: those fields are hints and "clients MUST NOT make security-critical
decisions based on annotations received from untrusted servers." A malicious or
compromised server would simply mark `delete_everything` read-only.

So the trust order is:

1. The connector's **catalog entry** (`read_only_tools:` in
   ``app/connectors/catalog/*.yaml``) — written by us, reviewed in-repo. This
   is the only source that can classify a tool read-only.
2. Everything else — registry mounts, free-form mounts, catalog entries that
   declare nothing, and any tool the catalog does not name — is a write.

``readOnlyHint`` is still read, but only to *surface* the remote's own claim on
the approval card ("the server says this is read-only"), never to skip the gate.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _catalog_read_only_tools(catalog_slug: str) -> frozenset[str]:
    """Remote tool names the vetted catalog entry declares read-only."""
    slug = (catalog_slug or "").strip()
    if not slug:
        return frozenset()
    from app.connectors.catalog_loader import get_catalog_entry

    try:
        entry = get_catalog_entry(slug)
    except KeyError:
        return frozenset()
    declared = entry.get("read_only_tools") or []
    return frozenset(str(name).strip() for name in declared if str(name).strip())


def remote_says_read_only(spec: Dict[str, Any]) -> bool:
    """The remote server's own ``readOnlyHint`` claim — display only.

    NEVER gate on this. It is attacker-controlled; see the module docstring.
    """
    ann = spec.get("_mcp_annotations")
    if isinstance(ann, dict):
        return bool(ann.get("readOnlyHint"))
    return False


def is_write_tool(spec: Dict[str, Any], *, auth_state: Optional[Dict] = None) -> bool:
    """True when invoking this mounted MCP tool requires a human bless.

    ``spec`` is a workspace tool-registry entry (it carries ``_mcp_connector_id``
    / ``_mcp_remote_name``). ``auth_state`` is the owning connector's decrypted
    state, used only for its ``catalog_slug``.
    """
    remote_name = str(spec.get("_mcp_remote_name") or "").strip()
    if not remote_name:
        # Cannot identify the tool -> cannot vouch for it.
        return True
    catalog_slug = str((auth_state or {}).get("catalog_slug") or "").strip()
    if not catalog_slug:
        # Registry / free-form mounts have no vetted manifest to consult.
        return True
    return remote_name not in _catalog_read_only_tools(catalog_slug)


def describe_tool_call(
    spec: Dict[str, Any],
    args: Dict[str, Any],
    *,
    display_name: str = "",
) -> str:
    """Human-readable one-liner for the approval card.

    Names the remote system and tool, because "run echo" tells the approver
    nothing about which third party is about to be written to.
    """
    remote_name = str(spec.get("_mcp_remote_name") or spec.get("key") or "tool")
    target = display_name.strip() or "an external MCP server"
    shown = ", ".join(
        f"{k}={_truncate(v)}" for k, v in list((args or {}).items())[:4] if k
    )
    if shown:
        return f"Call {remote_name} on {target} ({shown})"
    return f"Call {remote_name} on {target}"


def _truncate(value: Any, limit: int = 60) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
