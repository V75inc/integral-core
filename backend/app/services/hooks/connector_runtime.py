"""Connector hook dispatch after sync materialization (Phase 30.5).

Fires ``connector.dedup`` and ``connector.auto_link`` bindings registered
at bundle install time. Called from ``sync_runtime`` after each entry
create/update during a connector pull.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.models.edges import REFERENCES
from app.models.nodes import Entry, EntryType, Track
from app.services.hooks.declarative import dedup_candidates
from app.services.operational_model_compile import _slug

logger = logging.getLogger(__name__)


def _entry_payload(entry: Entry) -> Dict[str, Any]:
    cf = dict(getattr(entry, "custom_fields", {}) or {})
    return {
        "id": entry.id,
        "title": getattr(entry, "title", "") or "",
        "custom_fields": cf,
    }


async def _find_entries_by_type_slug(
    workspace_id: str,
    *,
    entry_type_slug: str,
    track_type_slug: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Collect candidate entry payloads in a workspace by EntryType slug."""
    want_type = _slug(entry_type_slug)
    want_track = _slug(track_type_slug) if track_type_slug else ""
    tracks = await Track.find({"context.workspace_id": workspace_id})
    out: List[Dict[str, Any]] = []
    for track in tracks:
        if want_track:
            track_hint = _slug(getattr(track, "title", "") or "")
            if track_hint != want_track:
                continue
        try:
            entries = await track.nodes(
                edge=["CONTAINS"], direction="out", node=["Entry"]
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "connector_runtime: CONTAINS walk failed track %s: %s",
                getattr(track, "id", "?"),
                exc,
            )
            continue
        for entry in entries:
            if not isinstance(entry, Entry):
                continue
            try:
                ets = await entry.nodes(
                    edge=["IS_OF_TYPE"], direction="out", node=["EntryType"]
                )
            except Exception:
                continue
            matched_type = False
            for et in ets:
                if not isinstance(et, EntryType):
                    continue
                if _slug(getattr(et, "name", "") or "") == want_type:
                    matched_type = True
                    break
            if matched_type:
                out.append(_entry_payload(entry))
    return out


async def _apply_link_references(
    source: Entry,
    matches: List[Dict[str, Any]],
    block: Dict[str, Any],
) -> Dict[str, Any]:
    """Wire REFERENCES or stamp link status per declarative dedup outcome."""
    refs_field = str(block.get("references_field_key") or "crm_account")
    status_field = str(block.get("link_field") or "crm_link_status")
    cf = dict(getattr(source, "custom_fields", {}) or {})

    # Idempotency — existing REFERENCES with refs_field.
    try:
        existing_targets = await source.nodes(
            edge=["REFERENCES"], direction="out", node=["Entry"]
        )
        if existing_targets:
            ctx = await source.get_context()
            for target in existing_targets:
                edges = await ctx.find_edges_between(
                    source.id, target.id, edge_class=REFERENCES
                )
                for edge in edges:
                    if (getattr(edge, "field_key", "") or "") == refs_field:
                        return {
                            "status": "linked",
                            "target_id": target.id,
                            "match_kind": "preexisting",
                        }
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "connector_runtime: idempotency check failed entry %s: %s",
            source.id,
            exc,
        )

    if not matches:
        cf[status_field] = "unmatched"
        cf.pop("crm_link_candidate_ids", None)
        source.custom_fields = cf
        await source.save()
        return {"status": "unmatched"}

    if len(matches) > 1:
        cf[status_field] = "ambiguous"
        cf["crm_link_candidate_ids"] = [m.get("id") for m in matches if m.get("id")]
        source.custom_fields = cf
        await source.save()
        return {"status": "ambiguous", "candidate_ids": cf["crm_link_candidate_ids"]}

    target_id = str(matches[0].get("id") or "")
    target = await Entry.get(target_id)
    if target is None:
        cf[status_field] = "unmatched"
        source.custom_fields = cf
        await source.save()
        return {"status": "unmatched"}

    await source.connect(
        target,
        edge=REFERENCES,
        field_key=refs_field,
        relation_type="connector",
        cross_track=True,
    )
    cf[status_field] = "linked"
    if refs_field == "crm_account":
        cf["crm_account_id"] = target.id
    cf.pop("crm_link_candidate_ids", None)
    source.custom_fields = cf
    await source.save()
    return {"status": "linked", "target_id": target.id}


async def _run_declarative_dedup(
    entry: Entry,
    workspace_id: str,
    binding: Dict[str, Any],
) -> None:
    block = binding.get("declarative") or {}
    if str(block.get("action_on_match") or "") != "link_references":
        return
    target_track = str(block.get("target_track_type") or "")
    target_entry = str(block.get("target_entry_type") or "")
    candidates = await _find_entries_by_type_slug(
        workspace_id,
        entry_type_slug=target_entry,
        track_type_slug=target_track or None,
    )
    source_payload = _entry_payload(entry)
    matches = dedup_candidates(source_payload, candidates, block)
    await _apply_link_references(entry, matches, block)


async def _run_tool_auto_link(
    entry: Entry,
    workspace_id: str,
    binding: Dict[str, Any],
    actor_user_id: str,
) -> None:
    from app.services.hooks.registry import ToolContext, get_workspace_tools
    from app.services.hooks.tool_dispatch import run_tool

    tool_key = str(binding.get("tool") or "").strip()
    if not tool_key:
        return
    tools = get_workspace_tools(workspace_id)
    spec = tools.get(tool_key)
    if not spec:
        logger.warning(
            "connector_runtime: tool %r not registered for workspace %s",
            tool_key,
            workspace_id,
        )
        return

    tool_input = dict(binding.get("tool_input") or {})
    cf = getattr(entry, "custom_fields", {}) or {}
    participants = list(cf.get("participant_emails") or [])
    payload = {
        **tool_input,
        "participants": participants,
    }
    ctx = ToolContext(
        user_id=actor_user_id or "connector",
        workspace_id=workspace_id,
        scope=f"entry:{entry.id}",
    )
    try:
        out = await run_tool(spec, payload, ctx)
    except Exception:  # noqa: BLE001
        logger.exception(
            "connector_runtime: auto_link tool %s failed entry %s",
            tool_key,
            entry.id,
        )
        return

    matched_ids = list(out.get("matched_entry_ids") or [])
    unmatched = list(out.get("unmatched_participants") or [])
    relation_field = str(
        tool_input.get("relation_field_key") or "related_communications"
    )

    ctx_graph = await entry.get_context()
    wired = 0
    for target_id in matched_ids:
        target = await Entry.get(str(target_id))
        if target is None:
            continue
        edges = await ctx_graph.find_edges_between(
            entry.id, target.id, edge_class=REFERENCES
        )
        if any((getattr(e, "field_key", "") or "") == relation_field for e in edges):
            wired += 1
            continue
        await entry.connect(
            target,
            edge=REFERENCES,
            field_key=relation_field,
            relation_type="connector",
            cross_track=True,
        )
        wired += 1

    cf_mut = dict(cf)
    cf_mut["unmatched_participants"] = unmatched
    entry.custom_fields = cf_mut
    await entry.save()
    logger.info(
        "connector_runtime: auto_link entry %s wired %d targets (%d unmatched)",
        entry.id,
        wired,
        len(unmatched),
    )


async def run_connector_post_materialize(
    *,
    entry: Entry,
    workspace_id: str,
    connector_slug: str,
    entry_type_key: str,
    actor_user_id: str,
) -> None:
    """Best-effort connector hook dispatch after sync upsert."""
    from app.services.hooks.resolver import find_matching_bindings

    if not workspace_id or not entry_type_key:
        return
    payload = {
        "connector_slug": connector_slug,
        "source_entry_type": entry_type_key,
    }
    for binding in find_matching_bindings(workspace_id, "connector.dedup", payload):
        if str(binding.get("mode") or "") == "declarative":
            try:
                await _run_declarative_dedup(entry, workspace_id, binding)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "connector_runtime: dedup hook %s failed entry %s",
                    binding.get("key"),
                    entry.id,
                )

    for binding in find_matching_bindings(workspace_id, "connector.auto_link", payload):
        if str(binding.get("mode") or "") == "tool":
            try:
                await _run_tool_auto_link(entry, workspace_id, binding, actor_user_id)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "connector_runtime: auto_link hook %s failed entry %s",
                    binding.get("key"),
                    entry.id,
                )
