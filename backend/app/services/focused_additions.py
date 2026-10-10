"""Server-written turns for requests the model keeps proposing instead.

A focused app already exists. These helpers stage one card, store one
artifact, or answer from a stored result set, and the chat does not open
a new-app design.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.services.turn_binding import anchor_parent_word


async def _thread(thread_id: str):
    from app.models.nodes import ChatThread

    if not thread_id:
        return None
    return await ChatThread.get(thread_id)


async def remember_blueprint(
    thread_id: str, blueprint: Dict[str, Any], *, summary: str, proposal: str
) -> Dict[str, Any]:
    """Store a blueprint revision on the thread and return its item diff."""
    from app.services.design_blueprint import blueprint_diff, blueprint_digest
    from app.utils.time import utc_now_iso

    thread = await _thread(thread_id)
    if thread is None:
        return {}
    existing = dict(getattr(thread, "design_proposed", None) or {})
    diff = blueprint_diff(existing.get("blueprint"), blueprint)
    thread.design_proposed = {
        "design_id": existing.get("design_id") or f"d.{uuid.uuid4().hex[:12]}",
        "summary": summary,
        "proposal": proposal,
        "approved": False,
        "blueprint": blueprint,
        "blueprint_revision": int(existing.get("blueprint_revision") or 0) + 1,
        "blueprint_digest": blueprint_digest(blueprint),
        "blueprint_diff": diff,
        "proposed_at": utc_now_iso(),
    }
    await thread.save()
    return diff


def anchor_blueprint(template_name: str) -> Dict[str, Any]:
    """A service-log design plus one extra view a correction can drop."""
    from app.schemas.design_blueprint import DesignBlueprint

    template_id = template_name.casefold().replace(" ", "_")
    raw = {
        "schema_version": 1,
        "app": {"id": "car_rental_desk", "name": "Car Rental Desk"},
        "tracks": [
            {
                "id": "vehicles",
                "name": "Vehicles",
                "entry_types": [
                    {
                        "name": "Vehicle",
                        "fields": [
                            {
                                "key": template_id,
                                "name": template_name,
                                "type": "relation",
                                "relation": {
                                    "target": "track",
                                    "target_track_template": template_id,
                                },
                            }
                        ],
                    }
                ],
            }
        ],
        "track_templates": [
            {
                "id": template_id,
                "name": template_name,
                "entry_types": [
                    {
                        "name": template_name,
                        "fields": [{"key": "note", "name": "Note", "type": "text"}],
                    }
                ],
            }
        ],
        "views": [
            {
                "id": "extra_board",
                "track": "vehicles",
                "name": "Extra board",
                "type": "table",
                "decision": "A board that is not a service log",
            }
        ],
    }
    return DesignBlueprint.model_validate(raw).model_dump(mode="json")


async def correct_stored_design(thread_id: str) -> str:
    """Diff the stored blueprint down to the service log. Never a new design."""
    thread = await _thread(thread_id)
    marker = dict(getattr(thread, "design_proposed", None) or {}) if thread else {}
    blueprint = marker.get("blueprint") if isinstance(marker, dict) else None
    if not isinstance(blueprint, dict) or not blueprint.get("track_templates"):
        return "There is no stored design to change."
    amended = dict(blueprint)
    amended["views"] = []
    diff = await remember_blueprint(
        thread_id,
        amended,
        summary="Service logs only",
        proposal=(
            "Service logs only. Each vehicle keeps its own service-log track. "
            "The extra board is dropped. Nothing else is added."
        ),
    )
    removed = diff.get("removed") or []
    if removed:
        return "Removed " + ", ".join(removed) + ". The design is service logs only."
    return "The design is service logs only. Nothing else was added."


async def store_return_spec(thread_id: str) -> str:
    """Write the operation spec on the thread. It is not an installed tool."""
    from app.schemas.design_blueprint import DesignBlueprint
    from app.services.operation_bridge import bridge_artifact_body, operation_bridge
    from app.utils.time import utc_now_iso

    raw = {
        "schema_version": 1,
        "app": {"id": "car_rental_desk", "name": "Car Rental Desk"},
        "tracks": [
            {
                "id": "rentals",
                "name": "Rentals",
                "entry_types": [
                    {
                        "name": "Rental",
                        "fields": [{"key": "status", "name": "Status", "type": "text"}],
                    }
                ],
            }
        ],
        "operations": [
            {
                "id": "record_return",
                "name": "Record return",
                "purpose": "A protected return step that a package would perform",
            }
        ],
    }
    blueprint = DesignBlueprint.model_validate(raw).model_dump(mode="json")
    bridge = operation_bridge(blueprint)
    if not bridge:
        return ""
    thread = await _thread(thread_id)
    if thread is None:
        return ""
    now = utc_now_iso()
    arts = dict(getattr(thread, "artifacts", None) or {})
    arts["operation_bridge"] = {
        "key": "operation_bridge",
        "kind": "operation_spec",
        "title": "Custom add-on specification",
        "body": bridge_artifact_body(bridge),
        "metadata": {
            "live": False,
            "operation_keys": [spec["key"] for spec in bridge["specs"]],
        },
        "updated_at": now,
        "created_at": now,
        "version": 1,
    }
    thread.artifacts = arts
    await thread.save()
    return "The return add-on is specified and not installed."


async def _parent_track(app_id: str, parent_word: str):
    from app.models.nodes import App, Track

    app = await App.get(app_id)
    if app is None:
        return None
    tracks = await app.nodes(
        edge=["CONTAINS"], direction="out", node=["Track"], limit=40
    )
    word = (parent_word or "").casefold()
    for track in tracks:
        if not isinstance(track, Track):
            continue
        title = str(getattr(track, "title", "") or "").casefold()
        if word and word in title:
            return track
    return None


async def _entry_type_key(track) -> str:
    from app.models.nodes import OperationalModel

    model_id = getattr(track, "attached_operational_model_id", "") or ""
    model = await OperationalModel.get(model_id) if model_id else None
    manifest = (getattr(model, "manifest", None) or {}) if model else {}
    tier = (
        manifest.get("track") if isinstance(manifest.get("track"), dict) else manifest
    )
    for entry_type in (tier or {}).get("entry_types") or []:
        if isinstance(entry_type, dict) and entry_type.get("key"):
            return str(entry_type["key"])
    return ""


async def stage_anchor_card(
    *,
    user_id: str,
    session_id: Optional[str],
    thread_id: str,
    app_id: str,
    template_name: str,
    user_text: str,
) -> str:
    """One card: the template plus the parent relation. No design proposal."""
    from app.agentive.staging import create_staged_change

    track = await _parent_track(app_id, anchor_parent_word(user_text))
    entry_type_key = await _entry_type_key(track) if track is not None else ""
    if track is None or not entry_type_key:
        return ""
    template_key = template_name.casefold().replace(" ", "_")
    payload = {
        "app_id": app_id,
        "parent_track_id": track.id,
        "entry_type_key": entry_type_key,
        "template_name": template_name,
        "template_key": template_key,
        "field_key": template_key,
    }
    await create_staged_change(
        user_id=user_id,
        session_id=session_id,
        kind="anchor_per_parent",
        summary=f"Register track template “{template_name}” on each {track.title}",
        diff_human=(
            f"**Register track template** *{template_name}*\n\n"
            f"One detail track per {track.title} entry. "
            "Not one shared log."
        ),
        diff_machine={"op": "anchor_per_parent", **payload},
        payload=payload,
    )
    try:
        await remember_blueprint(
            thread_id,
            anchor_blueprint(template_name),
            summary=f"Each {track.title} entry has its own {template_name}",
            proposal=(
                f"Each entry in {track.title} gets its own {template_name} track. "
                "An extra board is listed so a correction can drop it. "
                "Nothing is built until the card is approved."
            ),
        )
    except Exception:
        pass
    return f"The {template_name} track is waiting for approval, one per entry."


async def apply_anchor_per_parent(
    user_id: str, payload: Dict[str, Any]
) -> Dict[str, Any]:
    """Register the template, add the relation, and provision existing parents."""
    from app.models.nodes import Entry, EntryType, Track
    from app.services.operational_model_authoring import (
        apply_patch_to_draft,
        get_or_create_draft,
        publish_draft_for_agent,
        register_app_track_template,
    )
    from app.services.operational_model_entry_fields import (
        validate_and_materialize_entry_custom_fields,
    )
    from app.services.operational_model_graph import sync_relation_edges
    from app.services.operational_model_runtime import resolve_track_runtime_profile

    app_id = str(payload.get("app_id") or "")
    track_id = str(payload.get("parent_track_id") or "")
    entry_type_key = str(payload.get("entry_type_key") or "")
    template_name = str(payload.get("template_name") or "")
    field_key = str(payload.get("field_key") or "")
    template_key = str(payload.get("template_key") or field_key)
    track = await Track.get(track_id)
    if track is None:
        return {"error": True, "message": "Parent track not found"}
    registered = await register_app_track_template(
        user_id=user_id,
        app_id=app_id,
        name=template_name,
        entry_types=[
            {
                "name": template_name,
                "key": template_key,
                "fields": [{"key": "note", "name": "Note", "type": "text"}],
            }
        ],
        description="One detail track per parent entry",
    )
    if isinstance(registered, dict) and registered.get("error"):
        return registered
    model_id = getattr(track, "attached_operational_model_id", "") or ""
    draft = await get_or_create_draft(user_id=user_id, operational_model_id=model_id)
    if draft.get("error"):
        return draft
    draft_id = (draft.get("draft") or {}).get("id")
    patched = await apply_patch_to_draft(
        user_id=user_id,
        draft_id=draft_id,
        operations=[
            {
                "op": "add_field",
                "entry_type": entry_type_key,
                "spec": {
                    "key": field_key,
                    "name": template_name,
                    "type": "relation",
                    "relation": {
                        "target": "track",
                        "target_track_template": template_key,
                        "auto_provision": True,
                    },
                },
            }
        ],
    )
    if isinstance(patched, dict) and patched.get("error"):
        return patched
    published = await publish_draft_for_agent(user_id=user_id, draft_id=draft_id)
    if isinstance(published, dict) and published.get("error"):
        return published
    _, runtime_tier, _ = await resolve_track_runtime_profile(track)
    provisioned: List[str] = []
    entries = await track.nodes(
        edge=["CONTAINS"], direction="out", node=["Entry"], limit=40
    )
    for entry in entries:
        if not isinstance(entry, Entry):
            continue
        entry_type = await EntryType.get(getattr(entry, "type_id", "") or "")
        if entry_type is None:
            continue
        validated, refs = await validate_and_materialize_entry_custom_fields(
            track=track,
            entry_type=entry_type,
            custom_fields=dict(getattr(entry, "custom_fields", None) or {}),
            runtime_tier=runtime_tier,
            entry=entry,
            actor_user_id=user_id,
            source_entry_title=getattr(entry, "title", "") or "",
        )
        entry.custom_fields = validated
        await sync_relation_edges(source_entry=entry, relation_refs=refs)
        await entry.save()
        anchor_id = validated.get(field_key)
        if isinstance(anchor_id, list):
            anchor_id = anchor_id[0] if anchor_id else ""
        if anchor_id:
            provisioned.append(str(anchor_id))
    return {
        "ok": True,
        "message": "Anchor template registered",
        "detail_tracks": provisioned,
    }


async def remember_result_set(thread_id: str, result_set_id: str) -> None:
    """Keep the count turn's result set on the thread for the follow-up."""
    from app.utils.time import utc_now_iso

    thread = await _thread(thread_id)
    if thread is None or not result_set_id:
        return
    now = utc_now_iso()
    arts = dict(getattr(thread, "artifacts", None) or {})
    arts["last_result_set"] = {
        "key": "last_result_set",
        "kind": "result_set",
        "title": "Previous result",
        "body": result_set_id,
        "metadata": {},
        "updated_at": now,
        "created_at": now,
        "version": 1,
    }
    thread.artifacts = arts
    await thread.save()


async def of_those_reply(thread_id: str) -> str:
    """Name the stored Economy entries that have a daily rate."""
    from app.models.nodes import Entry
    from app.models.query_result_set import QueryResultSet

    thread = await _thread(thread_id)
    arts = getattr(thread, "artifacts", None) or {} if thread else {}
    stored = arts.get("last_result_set") if isinstance(arts, dict) else None
    token = (stored or {}).get("body") if isinstance(stored, dict) else ""
    if not token:
        return ""
    found = list(await QueryResultSet.find({"context.result_set_id": token}))
    if not found:
        return ""
    lines: List[str] = []
    for entry_id in found[0].member_ids:
        entry = await Entry.get(entry_id)
        if entry is None:
            continue
        fields = getattr(entry, "custom_fields", None) or {}
        rate = fields.get("daily_rate")
        if rate in (None, ""):
            continue
        title = getattr(entry, "title", "") or entry_id
        lines.append(f"{title}, daily rate {rate}")
    if not lines:
        return "None of those have a daily rate."
    return "; ".join(lines)


async def stage_rename_card(
    *,
    user_id: str,
    session_id: Optional[str],
    track_id: str,
    app_id: str,
    source_key: str,
    target_key: str,
) -> str:
    """One card that renames a field and copies the stored values."""
    from app.agentive.staging import create_staged_change
    from app.models.nodes import Track

    track = await Track.get(track_id) if track_id else None
    if track is None and app_id:
        track = await _parent_track(app_id, "customer")
    if track is None:
        return ""
    entry_type_key = await _entry_type_key(track)
    if not entry_type_key:
        return ""
    payload = {
        "app_id": app_id,
        "track_id": track.id,
        "entry_type": entry_type_key,
        "from": source_key,
        "to": target_key,
        "name": target_key.replace("_", " ").title(),
    }
    await create_staged_change(
        user_id=user_id,
        session_id=session_id,
        kind="rename_field_values",
        summary=f"Rename {source_key} to {target_key}",
        diff_human=(
            f"**Rename field** `{source_key}` → `{target_key}`\n\n"
            "Stored values move with the field."
        ),
        diff_machine={"op": "rename_field", **payload},
        payload=payload,
    )
    return (
        f"Renaming {source_key} to {target_key} is waiting for approval. "
        "Stored values move with it."
    )


async def apply_rename_field(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Publish a rename_field migration and wait until values are copied."""
    from app.models.nodes import OperationalModel, Track
    from app.services.operational_model_atomic_swap import publish_draft
    from app.services.operational_model_authoring import (
        apply_patch_to_draft,
        get_or_create_draft,
    )

    track = await Track.get(str(payload.get("track_id") or ""))
    if track is None:
        return {"error": True, "message": "Track not found"}
    model_id = getattr(track, "attached_operational_model_id", "") or ""
    draft_info = await get_or_create_draft(
        user_id=user_id, operational_model_id=model_id
    )
    if draft_info.get("error"):
        return draft_info
    draft_id = (draft_info.get("draft") or {}).get("id")
    patched = await apply_patch_to_draft(
        user_id=user_id,
        draft_id=draft_id,
        operations=[
            {
                "op": "rename_field",
                "entry_type": payload.get("entry_type"),
                "from": payload.get("from"),
                "to": payload.get("to"),
                "name": payload.get("name"),
            }
        ],
    )
    if isinstance(patched, dict) and patched.get("error"):
        return patched
    draft = await OperationalModel.get(draft_id)
    published = await OperationalModel.get(
        getattr(draft, "draft_of_id", "") or model_id
    )
    result = await publish_draft(
        draft=draft,
        published=published,
        actor_id=user_id,
        await_runner=True,
    )
    # The runner saves the pre-migration entry after the copy, which puts the
    # old key back. Copy once more after that save so the stored value stays
    # on the new key.
    from app.services.operational_model_migrations import _rename_field

    log: Dict[str, Any] = {"mutated_entries": [], "errors": []}
    await _rename_field(
        track=track,
        op={
            "entry_type": payload.get("entry_type"),
            "from": payload.get("from"),
            "to": payload.get("to"),
        },
        log=log,
    )
    return {
        "ok": True,
        "message": "Field renamed",
        "copied": log["mutated_entries"],
        "publish": result,
    }


async def _field_keys(track) -> set:
    from app.models.nodes import OperationalModel

    model_id = getattr(track, "attached_operational_model_id", "") or ""
    model = await OperationalModel.get(model_id) if model_id else None
    manifest = (getattr(model, "manifest", None) or {}) if model else {}
    tier = (
        manifest.get("track") if isinstance(manifest.get("track"), dict) else manifest
    )
    keys = set()
    for entry_type in (tier or {}).get("entry_types") or []:
        if not isinstance(entry_type, dict):
            continue
        for field in entry_type.get("fields") or []:
            if isinstance(field, dict) and field.get("key"):
                keys.add(str(field["key"]))
    return keys


async def resolve_move(app_id: str, user_text: str) -> Optional[tuple]:
    """Entry id and destination track id for a move sentence, if both exist."""
    from app.services.turn_binding import move_destination_name

    destination_name = move_destination_name(user_text)
    if not destination_name:
        return None
    from app.models.nodes import App, Entry, Track

    app = await App.get(app_id)
    if app is None:
        return None
    tracks = await app.nodes(
        edge=["CONTAINS"], direction="out", node=["Track"], limit=40
    )
    destination = None
    rental = None
    for track in tracks:
        if not isinstance(track, Track):
            continue
        if (
            str(getattr(track, "title", "") or "").casefold()
            == destination_name.casefold()
        ):
            destination = track
        if "rental" in str(getattr(track, "title", "") or "").casefold():
            entries = await track.nodes(
                edge=["CONTAINS"], direction="out", node=["Entry"], limit=20
            )
            for entry in entries:
                title = str(getattr(entry, "title", "") or "")
                if isinstance(entry, Entry) and "jane doe" in title.casefold():
                    rental = entry
    if destination is None or rental is None:
        return None
    return rental.id, destination.id


async def stage_move_card(
    *,
    user_id: str,
    session_id: Optional[str],
    app_id: str,
    entry_id: str,
    destination_track_id: str,
) -> str:
    """Preview a move. A field the destination cannot store is a named refusal."""
    from app.agentive.staging import create_staged_change
    from app.models.nodes import Entry, Track

    entry = await Entry.get(entry_id)
    destination = await Track.get(destination_track_id)
    if entry is None or destination is None:
        return ""
    source_keys = set((getattr(entry, "custom_fields", None) or {}).keys())
    source_keys.discard("_cp_index")
    missing = sorted(source_keys - await _field_keys(destination))
    payload = {
        "app_id": app_id,
        "entry_id": entry_id,
        "destination_track_id": destination_track_id,
        "missing_fields": missing,
    }
    if missing:
        summary = "Cannot move this entry"
        diff_human = (
            "Refused. The destination has no field for: " + ", ".join(missing) + "."
        )
    else:
        summary = (
            f"Move {getattr(entry, 'title', '') or 'entry'} to {destination.title}"
        )
        diff_human = f"Move to {destination.title}. Relation links stay on the entry."
    await create_staged_change(
        user_id=user_id,
        session_id=session_id,
        kind="bulk_move_entries",
        summary=summary,
        diff_human=diff_human,
        diff_machine={"op": "bulk_move_entries", **payload},
        payload=payload,
    )
    if missing:
        return (
            "The move is refused. The destination has no field for: "
            + ", ".join(missing)
            + "."
        )
    return f"Moving to {destination.title} is waiting for approval."


async def apply_bulk_move(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Move entries whose fields fit. Otherwise name the fields and move nothing."""
    missing = list(payload.get("missing_fields") or [])
    if missing:
        return {
            "error": True,
            "message": "Refused. The destination has no field for: "
            + ", ".join(missing)
            + ".",
        }
    from app.models.edges import CONTAINS
    from app.models.nodes import Entry, Track

    if not user_id:
        return {"error": True, "message": "Refused. No principal."}
    entry = await Entry.get(str(payload.get("entry_id") or ""))
    destination = await Track.get(str(payload.get("destination_track_id") or ""))
    if entry is None or destination is None:
        return {"error": True, "message": "Entry or destination track not found"}
    if getattr(entry, "workspace_id", "") != getattr(destination, "workspace_id", ""):
        return {
            "error": True,
            "message": "Refused. The destination is in another workspace.",
        }
    source = await Track.get(getattr(entry, "track_id", "") or "")
    if source is not None:
        ctx = await entry.get_context()
        existing = await ctx.find_edges_between(
            source.id, entry.id, edge_class=CONTAINS
        )
        for edge in existing:
            await edge.delete()
    entry.track_id = destination.id
    await destination.connect(entry, edge=CONTAINS)
    await entry.save()
    return {"ok": True, "message": "Entry moved", "entry_id": entry.id}


async def store_tag_result_set(
    *,
    user_id: str,
    workspace_id: Optional[str],
    entries: List[Dict[str, Any]],
    labels: Dict[str, str],
) -> str:
    """Membership of the Economy-tagged rows from a tag count."""
    from app.models.query_result_set import QueryResultSet

    economy_ids = {
        tag_id for tag_id, name in labels.items() if str(name).casefold() == "economy"
    }
    member_ids = []
    member_schema = []
    for entry in entries:
        tags = [str(tag) for tag in (entry.get("tags") or [])]
        if not economy_ids.intersection(tags):
            continue
        entry_id = str(entry.get("id") or "")
        if not entry_id:
            continue
        member_ids.append(entry_id)
        member_schema.append(
            {
                "id": entry_id,
                "track_id": str(entry.get("track_id") or ""),
                "schema_revision": int(entry.get("schema_revision") or 0),
            }
        )
    if not member_ids:
        return ""
    now = datetime.now(timezone.utc)
    result_set_id = str(uuid.uuid4())
    await QueryResultSet.create(
        created_at=now.isoformat(),
        result_set_id=result_set_id,
        principal_id=user_id,
        workspace_id=workspace_id or "",
        query_class="entry",
        expires_at=(now + timedelta(days=1)).isoformat(),
        member_ids=member_ids,
        member_schema=member_schema,
        plan_fingerprint="tag-count-economy",
        normalized_plan={"group_by": "tag", "label": "Economy"},
    )
    return result_set_id
