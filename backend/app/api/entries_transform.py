"""Generic entry.transform endpoint (DR-30-02).

POST /api/entries/{id}/transform
Body: { to_track?: <track_id>, hook_key?: <key>, override?: bool }

Replaces legacy-era /api/entries/{id}/convert-to-opportunity. All
transform semantics live in bundle hooks[] declarative blocks; this
endpoint is domain-agnostic. ``to_track`` may be omitted when the matched
hook's ``target_track_type`` resolves uniquely in the workspace.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import Request
from jvspatial.api import endpoint

from app.api.errors import (
    BadRequestError,
    InsufficientPermissionsError,
    MissingAuthenticationError,
    ResourceNotFoundError,
)
from app.api.utils import resolve_principal_id
from app.models.nodes import Entry, Track
from app.schemas.hooks.transform import TransformRequest, TransformResponse
from app.schemas.policy import Resource, Subject
from app.services.hooks.declarative import (
    OverrideRequiredError,
    TransformGateDeniedError,
)
from app.services.hooks.errors import (
    AmbiguousHookError,
    HookNotConfiguredError,
)
from app.services.hooks.transform_runtime import (
    entry_type_key,
    execute_transform,
    resolve_transform_target_track,
)
from app.services.policy_engine import evaluate as policy_evaluate
from app.services.request_scope import resolve_workspace_id_from_request


@endpoint(
    "/entries/{entry_id}/transform", methods=["POST"], auth=True, tags=["Entries"]
)
async def transform_entry(request: Request, entry_id: str) -> Dict[str, Any]:
    """Materialize a new entry in ``to_track`` from ``entry_id`` via the matching transform hook."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    raw = await request.json() if request.method == "POST" else {}
    req = TransformRequest.model_validate(raw or {})

    src = await Entry.get(entry_id)
    if src is None:
        raise ResourceNotFoundError(message="Entry not found")

    read_decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="entry.read",
        resource=Resource(
            kind="entry",
            id=entry_id,
            scope=f"track:{src.track_id or ''}",
        ),
    )
    if not read_decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")

    src_track = await Track.get(src.track_id) if src.track_id else None
    workspace_id = (
        src_track.workspace_id
        if src_track
        else await resolve_workspace_id_from_request(request, user_id)
    ) or ""

    source_entry_type = await entry_type_key(src)
    tgt_track, resolve_err = await resolve_transform_target_track(
        workspace_id=workspace_id,
        source_entry_type=source_entry_type,
        to_track_id=req.to_track,
        hook_key=req.hook_key,
    )
    if tgt_track is None:
        raise BadRequestError(
            message=resolve_err or "to_track required (and must resolve)"
        )

    create_decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="entry.create",
        resource=Resource(
            kind="entry",
            id="",
            scope=f"track:{tgt_track.id}",
        ),
    )
    if not create_decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")

    try:
        result = await execute_transform(
            src=src,
            tgt_track=tgt_track,
            workspace_id=workspace_id,
            user_id=user_id,
            hook_key=req.hook_key,
            override=req.override,
            enforce_gate=True,
        )
    except OverrideRequiredError as exc:
        raise BadRequestError(
            message=str(exc), details={"override_required": True}
        ) from exc
    except TransformGateDeniedError as exc:
        raise BadRequestError(message=str(exc), details={"gate_denied": True}) from exc
    except HookNotConfiguredError:
        raise
    except AmbiguousHookError:
        raise

    return TransformResponse(
        new_entry_id=result["new_entry_id"], hook_key=result["hook_key"]
    ).model_dump()
