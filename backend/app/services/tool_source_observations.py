"""Authorized source-text projection from retained native tool returns.

An authenticated retrieval proves what a tool returned, not publisher truth.
No caller-supplied passage or receipt metadata is accepted as evidence.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from app.agentive.harness.contracts import HarnessExecutionScope
from app.agentive.harness.jvspatial_store import JvSpatialStepStore
from app.agentive.harness.scoped_store import ScopedStepStore
from app.agentive.services.capability_broker import _fingerprint
from app.agentive.services.execution_runs import AgentRun, RunStep
from app.models.edges import HAS_HARNESS_SESSION
from app.models.nodes import ChatThread, HarnessSession

logger = logging.getLogger(__name__)
MAX_SOURCE_BYTES = 65_536


def source_projection(data: Any) -> dict[str, Any] | None:
    """Allow only bounded HTTP text metadata; omit arbitrary tool fields."""
    if not isinstance(data, dict):
        return None
    text = data.get("text")
    url = data.get("url")
    retrieved_at = data.get("retrieved_at")
    if (
        not isinstance(text, str)
        or not text
        or len(text.encode("utf-8")) > MAX_SOURCE_BYTES
        or not isinstance(url, str)
        or len(url) > 4096
        or not isinstance(retrieved_at, str)
        or len(retrieved_at) > 64
        or type(data.get("truncated")) is not bool
    ):
        return None
    try:
        parsed = urlsplit(url)
        timestamp = datetime.fromisoformat(retrieved_at.replace("Z", "+00:00"))
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or timestamp.tzinfo is None
        ):
            return None
    except ValueError:
        return None
    projected = {
        "url": url,
        "text": text,
        "retrieved_at": retrieved_at,
        "truncated": data["truncated"],
        "content_trust": "untrusted",
    }
    for key in ("title", "content_type", "source_kind"):
        value = data.get(key)
        if isinstance(value, str) and len(value) <= 1024:
            projected[key] = value
    return projected


def _source_from_snapshot(snapshot: Any, step: Any) -> dict[str, Any] | None:
    """Bind exact retained content to the server's succeeded read receipt."""
    matches = []
    for message in snapshot.messages:
        for part in message.parts:
            if (
                getattr(part, "part_kind", "") != "tool-return"
                or getattr(part, "tool_name", "") != step.capability_key
                or not isinstance(getattr(part, "content", None), dict)
            ):
                continue
            content = part.content
            receipt = content.get("_receipt")
            if not isinstance(receipt, dict) or receipt.get("step_id") != step.id:
                continue
            expected = {
                "step_id": step.id,
                "run_id": step.run_id,
                "step_key": step.step_key,
                "idempotency_key": step.idempotency_key,
                "status": "succeeded",
                "capability_key": step.capability_key,
                "origin": step.origin,
                "snapshot_fingerprint": step.snapshot_fingerprint,
            }
            if receipt != expected or content.get("replayed") is not False:
                return None
            ref = content.get("_observation_ref")
            if ref != {
                "kind": "tool_observation",
                "id": step.id,
                "session_id": snapshot.conversation_id,
            }:
                return None
            data = {
                key: value
                for key, value in content.items()
                if key not in {"_receipt", "replayed", "_observation_ref"}
            }
            # A connector collision with reserved wrapper keys cannot silently
            # become authenticated content: its original fingerprint differs.
            if _fingerprint(data) != step.output_fingerprint:
                return None
            source = source_projection(data)
            if source is None:
                return None
            matches.append(source)
    return matches[0] if len(matches) == 1 else None


async def _resolve_source(
    *, principal_id: str, workspace_id: str, observation_id: str, session_id: str
) -> dict[str, Any] | None:
    from app.agentive.connectors.connector_resolution import row_slug
    from app.agentive.connectors.mcp_mount import tool_key_for
    from app.agentive.connectors.mcp_proxy import _enforce_invoke_policy
    from app.agentive.connectors.mcp_tool_class import is_write_tool
    from app.agentive.connectors.tool_binding import resolve_mounted_tool_connector
    from app.services.workspace_permissions import can_access_workspace

    if (
        not principal_id
        or not workspace_id
        or not observation_id.startswith("o.RunStep.")
        or len(observation_id) > 256
        or not session_id
        or len(session_id) > 128
        or await can_access_workspace(principal_id, workspace_id) == "none"
    ):
        return None
    step = await RunStep.get(observation_id)
    if (
        step is None
        or step.id != observation_id
        or step.principal_id != principal_id
        or step.workspace_id != workspace_id
        or step.status != "succeeded"
        # The broker records connector reads as environment steps. Read/write
        # authority is independently rechecked against the bound connector tool.
        or step.kind != "environment"
        or step.capability_source != "connector"
        or step.origin != "chat"
        or step.policy_decision != "allow"
        or not step.connector_id
        or not step.output_fingerprint
    ):
        return None
    run = await AgentRun.find_one({"run_id": step.run_id})
    if (
        run is None
        or run.run_id != step.run_id
        or run.user_id != principal_id
        or run.workspace_id != workspace_id
        or run.origin != "chat"
        or run.capability_snapshot.get("fingerprint") != step.snapshot_fingerprint
    ):
        return None
    thread = await ChatThread.get(run.thread_id)
    sessions = await HarnessSession.find({"session_id": session_id})
    if thread is None or len(sessions) != 1:
        return None
    session = sessions[0]
    if (
        thread.id != run.thread_id
        or thread.user_id != principal_id
        or thread.workspace_id != workspace_id
        or session.principal_id != principal_id
        or session.session_id != session_id
        or session.workspace_id != workspace_id
        or session.thread_id != thread.id
        or session.status not in {"active", "suspended", "closed"}
        or session.binding_generation < 1
    ):
        return None
    graph = await thread.get_context()
    edges = await graph.find_edges_between(
        thread.id, session.id, edge_class=HAS_HARNESS_SESSION
    )
    if (
        len(edges) != 1
        or edges[0].generation != session.binding_generation
        or edges[0].binding_id != session.binding_id
    ):
        return None

    # Resolve the original row alias rather than today's canonical precedence.
    # A replaced personal/shared connection grants no historical authority.
    from app.services.hooks.registry import get_workspace_tools

    spec = get_workspace_tools(workspace_id).get(step.capability_key) or {}
    remote = str(spec.get("_mcp_remote_name") or "")
    if not remote or spec.get("agent_callable") is False:
        return None
    row, bound_spec = await resolve_mounted_tool_connector(
        workspace_id=workspace_id,
        principal_id=principal_id,
        capability_key=tool_key_for(step.connector_id, remote),
    )
    if str(row.id) != step.connector_id or await asyncio.to_thread(
        is_write_tool, bound_spec, auth_state={"catalog_slug": row_slug(row)}
    ):
        return None
    environments = run.capability_snapshot.get("environments") or []
    if not any(
        env.get("connector_id") == step.connector_id
        and step.capability_key in (env.get("tool_keys") or [])
        for env in environments
        if isinstance(env, dict)
    ):
        return None
    await _enforce_invoke_policy(
        connector_id=step.connector_id, principal_id=principal_id
    )
    scope = HarnessExecutionScope(
        tenant_id=workspace_id,
        principal_id=principal_id,
        workspace_id=workspace_id,
        thread_id=thread.id,
        session_id=session_id,
        run_id=step.run_id,
        permission_revision=session.permission_revision,
        capability_version=session.capability_version,
    )
    store = ScopedStepStore(store=JvSpatialStepStore(scope=scope), scope=scope)
    registered = await store.get_run(run_id=step.run_id)
    if registered is None:
        return None
    snapshot = await store.latest_snapshot(run_id=step.run_id)
    if (
        snapshot is None
        or snapshot.run_id != step.run_id
        or snapshot.state != "complete"
    ):
        return None
    source = _source_from_snapshot(snapshot, step)
    if source is None:
        return None
    return {
        "kind": "tool_observation",
        "id": step.id,
        "session_id": session_id,
        "run_id": step.run_id,
        "capability_key": step.capability_key,
        "observed_at": step.finished_at,
        "output_fingerprint": step.output_fingerprint,
        "source": source,
    }


async def resolve_tool_source_observation(
    *, principal_id: str, workspace_id: str, observation_id: str, session_id: str
) -> dict[str, Any] | None:
    """Fail closed on revocation/absence/corruption without fetching again."""
    from app.middleware.permissions_cache import isolated_permissions_cache

    try:
        with isolated_permissions_cache():
            return await _resolve_source(
                principal_id=principal_id,
                workspace_id=workspace_id,
                observation_id=observation_id,
                session_id=session_id,
            )
    except Exception:
        # Do not log provider content, ciphertext, credential or lookup details.
        logger.info("Tool source observation unavailable")
        return None
