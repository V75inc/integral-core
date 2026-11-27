"""Account-scoped confirmation preference shared by native chat and voice."""

import asyncio
import secrets
from contextlib import nullcontext
from contextvars import ContextVar

from jvspatial.core.context import get_default_context

from app.api.errors import BadRequestError, ResourceConflictError, ResourceNotFoundError
from app.models.edges import ResidentWritePermission
from app.models.nodes import App, Track, Workspace
from app.schemas.agent_preferences import ResidentPermissionValue
from app.services.app_operations.transaction_scope import postgres_graph_transaction
from app.services.permissions import get_user_node
from app.services.workspace_permissions import can_access_workspace

_decision = ContextVar("resident_permission_decision", default=None)


def holds_permission(staged):
    """Check that this task holds the decision for this exact staged write."""
    return _decision.get() == (
        asyncio.current_task(),
        staged.token,
        staged.user_id,
        staged.workspace_id,
        staged.session_id,
    )


async def read_permission(principal, workspace):
    """Read the user–workspace policy, defaulting to review when no unique grant exists."""
    user = await get_user_node(principal)
    if not user:
        return ResidentPermissionValue()
    db = get_default_context().database
    rows = await db.find(
        "edge",
        {
            "entity": ResidentWritePermission._entity_name(),
            "source": user.id,
            "target": workspace,
        },
        limit=2,
    )
    if len(rows) != 1:
        return ResidentPermissionValue()
    return ResidentPermissionValue.model_validate(rows[0]["context"])


async def set_permission(
    principal, workspace, track_id, revision, *, track_ids=None, app_ids=None, mode=None
):
    """Validate the selected scope and update the workspace grant at the expected revision."""
    if mode is None:
        if app_ids is not None or (track_id is None) == (track_ids is None):
            raise BadRequestError(
                message="Supply one list selection or an approval mode."
            )
        mode = "custom" if (track_id or track_ids) else "ask"
    elif (
        mode not in {"ask", "auto", "custom", "full"}
        or track_id is not None
        or (mode != "custom" and (track_ids is not None or app_ids is not None))
    ):
        raise BadRequestError(
            message="Choose a valid approval mode. Lists belong to Custom."
        )
    selection = None
    if mode == "custom" and (track_ids is not None or track_id is not None):
        selection = sorted(set(track_ids if track_ids is not None else [track_id]))
    async with postgres_graph_transaction() as db:
        user = await get_user_node(principal)
        if not user or not await db.find_one_and_update(
            "node", {"id": user.id}, {"$set": {}}
        ):
            raise ResourceNotFoundError(message="Account unavailable.")
        if await can_access_workspace(principal, workspace) not in {
            "owner",
            "admin",
            "member",
        }:
            raise ResourceNotFoundError(message="Workspace unavailable.")
        target = await Workspace.get(workspace)
        if not target:
            raise ResourceNotFoundError(message="Workspace unavailable.")
        query = {
            "entity": ResidentWritePermission._entity_name(),
            "source": user.id,
            "target": workspace,
        }
        rows = await db.find("edge", query, limit=2)
        if len(rows) > 1 or revision != (
            rows[0]["context"]["revision"] if rows else ""
        ):
            raise ResourceConflictError(
                message="The permission changed. Reopen permissions to see the current setting."
            )
        previous = (
            ResidentPermissionValue.model_validate(rows[0]["context"])
            if rows
            else ResidentPermissionValue()
        )
        if track_ids is None and track_id and len(previous.custom_track_ids) > 1:
            raise ResourceConflictError(
                message="Reopen the updated Permissions picker to change multiple lists."
            )
        if selection is None:
            selection = previous.custom_track_ids
        if mode == "custom" and selection:
            tracks = await Track.find({"id": {"$in": selection}})
            eligible = {
                track.id
                for track in tracks
                if track.allows_resident_write_permission(principal, workspace)
            }
            if eligible != set(selection):
                raise BadRequestError(
                    message="Choose only private lists you own in this workspace."
                )
        apps = sorted(set(app_ids)) if app_ids is not None else previous.custom_app_ids
        if mode == "custom" and apps:
            if len(apps) > 200:
                raise BadRequestError(message="Choose at most 200 Apps.")
            for app_id in apps:
                if not await _presentation_app(principal, workspace, app_id):
                    raise BadRequestError(
                        message="Choose only private Apps you own and may configure in this workspace."
                    )
        if (
            mode == previous.mode
            and selection == sorted(previous.custom_track_ids)
            and apps == sorted(previous.custom_app_ids)
        ):
            return previous
        value = ResidentPermissionValue(
            mode=mode,
            custom_track_ids=selection,
            custom_app_ids=apps,
            revision=secrets.token_hex(16),
        )
        if rows:
            await db.find_one_and_update(
                "edge",
                {"id": rows[0]["id"]},
                {
                    "$set": {
                        "context.track_id": value.track_id,
                        "context.track_ids": value.track_ids,
                        "context.custom_track_ids": value.custom_track_ids,
                        "context.custom_app_ids": value.custom_app_ids,
                        "context.mode": mode,
                        "context.revision": value.revision,
                    }
                },
            )
        else:
            await user.connect(
                target, edge=ResidentWritePermission, **value.model_dump()
            )
        return value


async def _presentation_app(principal, workspace, app_id):
    from app.middleware.permissions_cache import isolated_permissions_cache
    from app.schemas.policy import Resource, Subject
    from app.services.policy_engine import evaluate
    from app.services.resource_location import MANAGEMENT_APP_STATES, locate_resource

    with isolated_permissions_cache():
        location = await locate_resource(
            "app", app_id, allowed_app_states=MANAGEMENT_APP_STATES
        )
        app = (
            await App.get(app_id)
            if location and location.workspace_id == workspace
            else None
        )
        if not app or not app.allows_resident_presentation_permission(
            principal, workspace
        ):
            return None
        decision = await evaluate(
            subject=Subject(kind="human", id=principal),
            action="app.update",
            resource=Resource(kind="app", id=app_id, scope=f"app:{app_id}"),
        )
        return app if decision.allowed else None


async def _apply_presentation_permission(staged, ctx, policy, db):
    from app.agentive.services.approval_decisions import decide_staged_write
    from app.agentive.services.approval_policy import is_presentation_effect
    from app.models.nodes import Dashboard
    from app.services.app_home import home_revision_token
    from app.services.native_voice_review import pending_work_proposal
    from app.services.resource_location import MANAGEMENT_APP_STATES, locate_resource

    exact = await pending_work_proposal(
        principal=ctx.principal_id,
        workspace=ctx.workspace_id,
        thread=ctx.thread_id,
        work_id=ctx.work_item_id,
        run_id=ctx.run_id,
    )
    if not exact or exact.token != staged.token:
        return None
    payload = staged.payload
    app_id = payload.get("app_id")
    if (
        policy.mode != "custom"
        or app_id not in policy.custom_app_ids
        or not is_presentation_effect(staged.kind, payload)
    ):
        return None
    # The standing grant is narrower than ordinary App editing, and cannot
    # change ACLs, publish code or grant access. Lock the exact App and check
    # current role, scope and revision before using the canonical writer.
    if not await db.find_one_and_update(
        "node",
        {
            "entity": App._entity_name(),
            "id": app_id,
            "context.workspace_id": ctx.workspace_id,
            "context.owner_user_id": ctx.principal_id,
            "context.visibility": "private",
        },
        {"$set": {}},
    ):
        return None
    app = await _presentation_app(ctx.principal_id, ctx.workspace_id, app_id)
    if app is None:
        return None
    if (
        staged.kind == "update_app"
        and home_revision_token(app) != payload["expected_home_revision"]
    ):
        return None
    if staged.kind == "update_dashboard":
        identifier = payload["dashboard_id"]
        location = await locate_resource(
            "dashboard", identifier, allowed_app_states=MANAGEMENT_APP_STATES
        )
        if not location or (location.workspace_id, location.app_id) != (
            ctx.workspace_id,
            app_id,
        ):
            return None
        if not await db.find_one_and_update(
            "node",
            {
                "entity": Dashboard._entity_name(),
                "id": identifier,
                "context.updated_at": payload["expected_dashboard_updated_at"],
            },
            {"$set": {}},
        ):
            return None
    binding = _decision.set(
        (
            asyncio.current_task(),
            staged.token,
            ctx.principal_id,
            ctx.workspace_id,
            ctx.thread_id,
        )
    )
    try:
        return await decide_staged_write(
            principal_id=ctx.principal_id,
            workspace_id=ctx.workspace_id,
            thread_id=ctx.thread_id,
            conversation_id=ctx.thread_id,
            proposal_id=staged.token,
            decision="approve",
            source="resident_permission",
        )
    finally:
        _decision.reset(binding)


async def apply_resident_permission(item, ctx, token):
    """Decide exactly this staged effect under the grant held at turn admission."""
    from app.agentive.services.approval_decisions import decide_staged_write
    from app.agentive.staging import get_token
    from app.models.nodes import ChatThread, Entry
    from app.services.hooks.registry import get_workspace_hooks
    from app.services.native_voice_creation import confirmed_creation
    from app.services.native_voice_review import read_entry_review

    revision = item.input_payload.get("resident_permission_revision")
    if (
        not revision
        or item.kind != "chat_turn"
        or item.work_item_id != ctx.work_item_id
    ):
        return None
    async with postgres_graph_transaction() as db:
        user = await get_user_node(ctx.principal_id)
        if not user:
            return None
        edge_query = {
            "entity": ResidentWritePermission._entity_name(),
            "source": user.id,
            "target": ctx.workspace_id,
        }
        edges = await db.find("edge", edge_query, limit=2)
        if len(edges) != 1:
            return None
        grant = await db.find_one_and_update(
            "edge",
            {
                **edge_query,
                "id": edges[0]["id"],
                "context.revision": revision,
            },
            {"$set": {}},
        )
        if not grant:
            return None
        policy = ResidentPermissionValue.model_validate(grant["context"])
        if policy.mode == "ask":
            return None
        thread = await ChatThread.get(ctx.thread_id)
        if (
            not thread
            or thread.user_id != ctx.principal_id
            or thread.workspace_id != ctx.workspace_id
            or thread.archived
            or thread.provider_id != "integral_native"
            or thread.agent_profile_key
            or (thread.provider_session_id or "").startswith("wa:")
        ):
            return None
        if await can_access_workspace(ctx.principal_id, ctx.workspace_id) not in {
            "member",
            "admin",
            "owner",
        }:
            return None
        staged = await get_token(token)
        if (
            not staged
            or staged.state != "pending"
            or staged.kind
            not in {
                "create_entry",
                "update_entry",
                "update_app",
                "create_dashboard",
                "update_dashboard",
            }
        ):
            return None
        proof = staged.origin_proof or {}
        if (proof.get("attempt"), proof.get("effect_key")) != (
            ctx.attempt,
            ctx.effect_key,
        ):
            return None
        if staged.kind in {"update_app", "create_dashboard", "update_dashboard"}:
            return await _apply_presentation_permission(staged, ctx, policy, db)
        if staged.kind == "create_entry":
            track_id = staged.payload.get("track_id")
        else:
            entry = await Entry.get(staged.payload.get("entry_id"))
            track_id = entry.track_id if entry else None
        if not track_id or (
            policy.mode == "custom" and track_id not in policy.custom_track_ids
        ):
            return None
        track_query = {
            "id": track_id,
            "entity": Track._entity_name(),
            "context.workspace_id": ctx.workspace_id,
        }
        if policy.mode != "full":
            track_query.update(
                {"context.owner_id": ctx.principal_id, "context.visibility": "private"}
            )
        if not await db.find_one_and_update("node", track_query, {"$set": {}}):
            return None
        if policy.mode != "full":
            track = await Track.get(track_id)
            if not track or not track.allows_resident_write_permission(
                ctx.principal_id, ctx.workspace_id
            ):
                return None
        if staged.kind == "update_entry" and not await db.find_one_and_update(
            "node",
            {
                "entity": Entry._entity_name(),
                "id": staged.payload.get("entry_id"),
                "context.track_id": track_id,
            },
            {"$set": {}},
        ):
            return None
        confirmation = nullcontext()
        if policy.mode == "full":
            from app.middleware.permissions_cache import isolated_permissions_cache
            from app.schemas.policy import Resource, Subject
            from app.services.native_voice_review import pending_work_proposal
            from app.services.policy_engine import evaluate
            from app.services.resource_location import DATA_APP_STATES, locate_resource

            exact = await pending_work_proposal(
                principal=ctx.principal_id,
                workspace=ctx.workspace_id,
                thread=ctx.thread_id,
                work_id=ctx.work_item_id,
                run_id=ctx.run_id,
            )
            if not exact or exact.token != token:
                return None
            creating = staged.kind == "create_entry"
            with isolated_permissions_cache():
                location = await locate_resource(
                    "track" if creating else "entry",
                    track_id if creating else staged.payload["entry_id"],
                    allowed_app_states=DATA_APP_STATES,
                )
                if location is None or (location.workspace_id, location.track_id) != (
                    ctx.workspace_id,
                    track_id,
                ):
                    return None
                decision = await evaluate(
                    subject=Subject(kind="human", id=ctx.principal_id),
                    action="entry.create" if creating else "entry.update",
                    resource=Resource(
                        kind="entry",
                        id="" if creating else staged.payload["entry_id"],
                        scope=f"track:{track_id}",
                    ),
                    payload=staged.payload,
                )
            if not decision.allowed:
                return None
        else:
            if any(
                get_workspace_hooks(ctx.workspace_id, hook)
                for hook in (
                    "entry.create",
                    "entry.update",
                    "entry.validate",
                    "entry.transform",
                )
            ):
                return None
            review = await read_entry_review(
                principal=ctx.principal_id,
                workspace=ctx.workspace_id,
                thread=ctx.thread_id,
                work_id=ctx.work_item_id,
                run_id=ctx.run_id,
            )
            if (
                not review
                or review.proposal_id != token
                or (staged.kind == "create_entry" and not review.creation)
            ):
                return None
            confirmation = confirmed_creation(review)
        binding = _decision.set(
            (
                asyncio.current_task(),
                token,
                ctx.principal_id,
                ctx.workspace_id,
                ctx.thread_id,
            )
        )
        try:
            with confirmation:
                return await decide_staged_write(
                    principal_id=ctx.principal_id,
                    workspace_id=ctx.workspace_id,
                    thread_id=ctx.thread_id,
                    conversation_id=ctx.thread_id,
                    proposal_id=token,
                    decision="approve",
                    source="resident_permission",
                )
        finally:
            _decision.reset(binding)
