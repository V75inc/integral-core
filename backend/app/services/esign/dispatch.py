"""Dispatch contract / vault operations to installed E-sign bundle tools."""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.services.hooks.registry import ToolContext, get_workspace_tools
from app.services.hooks.tool_dispatch import run_tool
from app.services.workspace_permissions import get_workspace_owner_user_id

E_SIGN_CONTRACT_TOOL = "e_sign_contract_decision"
E_SIGN_VAULT_SAVE = "e_sign_vault_save"
E_SIGN_VAULT_LIST = "e_sign_vault_list"
E_SIGN_VAULT_DELETE = "e_sign_vault_delete"


def _omit_null_tool_fields(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Drop ``None`` values so optional JSON-schema string fields are omitted, not null."""
    return {key: value for key, value in payload.items() if value is not None}


async def _run_e_sign_tool(
    workspace_id: str,
    tool_key: str,
    payload: Dict[str, Any],
    *,
    actor_user_id: str,
    scope: str = "",
    run_as_actor: bool = False,
) -> Optional[Any]:
    tools = get_workspace_tools(workspace_id)
    spec = tools.get(tool_key)
    if not spec:
        return None
    owner_id = await get_workspace_owner_user_id(workspace_id) or actor_user_id
    effective_user = (
        str(actor_user_id or "system")
        if run_as_actor
        else str(owner_id or actor_user_id or "system")
    )
    ctx = ToolContext(
        user_id=effective_user,
        workspace_id=workspace_id,
        scope=scope or f"workspace:{workspace_id}",
    )
    return await run_tool(spec, _omit_null_tool_fields(payload), ctx)


async def dispatch_contract_decision(
    *,
    workspace_id: str,
    actor_user_id: str,
    payload: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    form_entry_id = str(payload.get("form_entry_id") or "")
    scope = f"entry:{form_entry_id}" if form_entry_id else f"workspace:{workspace_id}"
    result = await _run_e_sign_tool(
        workspace_id,
        E_SIGN_CONTRACT_TOOL,
        payload,
        actor_user_id=actor_user_id,
        scope=scope,
    )
    if result is None:
        return None
    if isinstance(result, dict):
        return result
    return {"result": result}


async def dispatch_vault_save(
    workspace_id: str, actor_user_id: str, signature_png: str, label: str = "Default"
) -> Optional[Dict[str, Any]]:
    result = await _run_e_sign_tool(
        workspace_id,
        E_SIGN_VAULT_SAVE,
        {"signature_png": signature_png, "label": label},
        actor_user_id=actor_user_id,
        scope=f"user:{actor_user_id}",
        run_as_actor=True,
    )
    return result if isinstance(result, dict) else None


async def dispatch_vault_list(workspace_id: str, actor_user_id: str) -> Optional[list]:
    result = await _run_e_sign_tool(
        workspace_id,
        E_SIGN_VAULT_LIST,
        {},
        actor_user_id=actor_user_id,
        scope=f"user:{actor_user_id}",
        run_as_actor=True,
    )
    if result is None:
        return None
    if isinstance(result, dict):
        return list(result.get("signatures") or [])
    return None


async def dispatch_vault_delete(
    workspace_id: str, actor_user_id: str, attachment_id: str
) -> Optional[bool]:
    result = await _run_e_sign_tool(
        workspace_id,
        E_SIGN_VAULT_DELETE,
        {"attachment_id": attachment_id},
        actor_user_id=actor_user_id,
        scope=f"user:{actor_user_id}",
        run_as_actor=True,
    )
    if result is None:
        return None
    if isinstance(result, dict):
        return bool(result.get("deleted"))
    return bool(result)
