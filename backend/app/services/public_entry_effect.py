"""Exact bearer submission authority held through one public Entry command."""

import asyncio
import secrets
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import datetime, timezone

from jvspatial.core.context import get_default_context

from app.exceptions import AuthorizationUnavailableError
from app.models.edges import HAS_SHARE_LINK
from app.models.nodes import ShareLink
from app.services.authority_facts import required_node_text
from app.services.entry_write_scope import (
    entry_write_scope,
    is_postgres_entry_transaction,
)
from app.services.membership_write_scope import membership_write_scope
from app.services.resource_location import DATA_APP_STATES, locate_resource

_active: ContextVar[tuple | None] = ContextVar(
    "integral_public_entry_effect", default=None
)


async def _link(token: str) -> dict:
    from app.api.errors import ResourceNotFoundError
    from app.services.share_links import _hash_token

    if not isinstance(token, str) or len(token) < 16:
        raise ResourceNotFoundError(message="Shared track not found")
    hashed = _hash_token(token)
    rows = await get_default_context().database.find(
        "node",
        {"entity": ShareLink._entity_name(), "context.token_hash": hashed},
        limit=2,
    )
    if len(rows) != 1:
        raise ResourceNotFoundError(message="Shared track not found")
    row = rows[0]
    body = row.get("context")
    if not isinstance(body, dict):
        raise AuthorizationUnavailableError()
    if not secrets.compare_digest(required_node_text(row, "token_hash"), hashed):
        raise ResourceNotFoundError(message="Shared track not found")
    if (
        body.get("resource_type") != "track"
        or body.get("intent") != "public"
        or body.get("revoked_at")
    ):
        raise ResourceNotFoundError(message="Shared track not found")
    expires = body.get("expires_at")
    if expires is not None:
        try:
            expiry = datetime.fromisoformat(expires)
            if expiry.tzinfo is None:
                raise ValueError("Missing timezone")
        except (ValueError, TypeError) as exc:
            raise AuthorizationUnavailableError() from exc
        if expiry <= datetime.now(timezone.utc):
            raise ResourceNotFoundError(message="Shared track not found")
    permissions = body.get("public_permissions")
    if not isinstance(permissions, dict) or any(
        type(value) is not bool for value in permissions.values()
    ):
        raise AuthorizationUnavailableError()
    resource_id = required_node_text(row, "resource_id")
    workspace_id = required_node_text(row, "workspace_id")
    required_node_text(row, "created_by")
    location = await locate_resource(
        "track", resource_id, allowed_app_states=DATA_APP_STATES
    )
    parents = await get_default_context().database.find(
        "edge", {"target": row["id"], "entity": HAS_SHARE_LINK._entity_name()}, limit=2
    )
    if (
        location is None
        or location.workspace_id != workspace_id
        or len(parents) != 1
        or parents[0].get("source") != resource_id
    ):
        raise AuthorizationUnavailableError()
    return row


@asynccontextmanager
async def public_entry_write_scope(token: str, permission: str):
    """Admit the current issuer and current bearer facts before any effect."""
    from app.api.errors import InsufficientPermissionsError
    from app.schemas.policy import Resource, Subject
    from app.services.policy_engine import evaluate

    preflight = await _link(token)
    body = preflight["context"]
    async with entry_write_scope(f"public-entry:{body['workspace_id']}"):
        async with membership_write_scope(
            body["workspace_id"], principal_ids=(body["created_by"],)
        ):
            database = get_default_context().database
            if is_postgres_entry_transaction(database):
                if (
                    await database.find_one_and_update(
                        "node",
                        {"id": preflight["id"], "entity": ShareLink._entity_name()},
                        {"$set": {}},
                    )
                    is None
                ):
                    raise AuthorizationUnavailableError()
            current = await _link(token)
            data = current["context"]
            if any(
                data.get(key) != body.get(key)
                for key in ("created_by", "workspace_id", "resource_id")
            ):
                raise AuthorizationUnavailableError()
            decision = await evaluate(
                subject=Subject(kind="human", id=data["created_by"]),
                action="track.share_link.mint",
                resource=Resource(
                    kind="track",
                    id=data["resource_id"],
                    scope=f"track:{data['resource_id']}",
                ),
            )
            if (
                not decision.allowed
                or data["public_permissions"].get(permission) is not True
            ):
                raise InsufficientPermissionsError(
                    message="Public submission is disabled"
                )
            token_var = _active.set(
                (
                    asyncio.current_task(),
                    database,
                    data["workspace_id"],
                    data["resource_id"],
                    permission,
                )
            )
            try:
                yield
            finally:
                _active.reset(token_var)


async def public_entry_effect_permitted(
    *, action: str, target_kind: str, target_id: str, workspace_id: str
) -> bool:
    """Require this task's exact current bearer, target and mutation permission."""
    permit = _active.get()
    permission = {
        "entry.create": "create_entries",
        "entry.update": "update_entries",
    }.get(action)
    if (
        permit is None
        or permit[:3]
        != (asyncio.current_task(), get_default_context().database, workspace_id)
        or permit[4] != permission
    ):
        return False
    location = await locate_resource(
        target_kind, target_id, allowed_app_states=DATA_APP_STATES
    )
    return bool(
        location
        and location.workspace_id == workspace_id
        and (location.track_id or target_id) == permit[3]
    )
