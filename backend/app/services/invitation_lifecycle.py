"""Ordered invitation state admission on the public graph transaction seam.

PostgreSQL holds the workspace fence and invitation row through the complete
grant/terminal write. JSON development stores serialize only this process;
they do not provide transaction rollback or distributed revocation guarantees.
"""

import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import datetime
from typing import AsyncIterator, Iterable

from jvspatial.core.context import get_default_context

from app.exceptions import AuthorizationUnavailableError
from app.models.nodes import Invitation
from app.services.app_operations.transaction_scope import (
    graph_transaction_available,
    postgres_graph_transaction,
)
from app.services.authority_facts import (
    primary_node_record,
    relationship_flag,
    relationship_literal,
    required_node_text,
)
from app.services.authorization_scope import authorization_scope
from app.services.entry_write_scope import entry_write_scope
from app.services.identity_admission import identity_admission
from app.services.sharing import VALID_ROLES

_active_invitation: ContextVar[tuple[str, object] | None] = ContextVar(
    "integral_active_invitation_transition", default=None
)


@asynccontextmanager
async def invitation_lifecycle_scope(
    invitation_id: str,
    workspace_id: str,
    *,
    principal_ids: Iterable[str] = (),
    include_issuer: bool = False,
) -> AsyncIterator[Invitation]:
    """Yield freshly admitted state; callers must commit effects in this scope."""
    caller_context = get_default_context()
    async with invitation_storage_scope(
        invitation_id,
        workspace_id,
        principal_ids=principal_ids,
        include_issuer=include_issuer,
    ):
        invitation = await _read_current_invitation(invitation_id, workspace_id)
        yield invitation
    await invitation.set_context(caller_context)


@asynccontextmanager
async def invitation_storage_scope(
    invitation_id: str,
    workspace_id: str,
    *,
    legacy_review: bool = False,
    principal_ids: Iterable[str] = (),
    include_issuer: bool = False,
) -> AsyncIterator[None]:
    """Hold transition locks; consumers must perform their own strict admission.

    Explicit owner review can admit legacy records without silently migrating
    them through ordinary acceptance. This seam never authorizes a grant.
    """
    current = _active_invitation.get()
    if current == (invitation_id, asyncio.current_task()):
        raise AuthorizationUnavailableError()
    token = _active_invitation.set((invitation_id, asyncio.current_task()))
    try:
        context = get_default_context()
        preflight = await primary_node_record(
            context.database, Invitation, invitation_id
        )
        if preflight is None:
            raise AuthorizationUnavailableError()
        principals = tuple(principal_ids)
        issuer = ""
        if include_issuer and preflight["context"].get("status") == "pending":
            adopted = (
                required_node_text(preflight, "adopted_by_user_id", allow_empty=True)
                if "adopted_by_user_id" in preflight["context"]
                or "adopted_by_user_id" in preflight
                else ""
            )
            issuer = adopted or required_node_text(preflight, "invited_by_user_id")
        participants = principals + ((issuer,) if issuer else ())
        if graph_transaction_available(context.database):
            async with (
                postgres_graph_transaction(context.database),
                identity_admission(participants, active_principal_ids=principals),
                authorization_scope(workspace_id, authority_mutation=True),
            ):
                context = get_default_context()
                original = await primary_node_record(
                    context.database, Invitation, invitation_id
                )
                if original is None:
                    raise AuthorizationUnavailableError()
                if issuer and (
                    original["context"].get("status")
                    != preflight["context"].get("status")
                    or (
                        original["context"].get("adopted_by_user_id")
                        or required_node_text(original, "invited_by_user_id")
                    )
                    != issuer
                ):
                    raise AuthorizationUnavailableError()
                revision = original["context"].get("invitation_revision")
                if not (
                    legacy_review and "invitation_revision" not in original["context"]
                ):
                    if type(revision) is not int or revision < 0:
                        raise AuthorizationUnavailableError()
                row = await context.database.find_one_and_update(
                    "node",
                    {"id": invitation_id, "entity": Invitation._entity_name()},
                    {"$inc": {"context.invitation_revision": 0}},
                )
                if row is None:
                    raise AuthorizationUnavailableError()
                yield
        else:
            async with (
                entry_write_scope(f"invitation:{invitation_id}"),
                identity_admission(participants, active_principal_ids=principals),
            ):
                yield
    finally:
        _active_invitation.reset(token)


async def _read_current_invitation(invitation_id: str, workspace_id: str) -> Invitation:
    """Reject missing or moved state rather than using the caller's snapshot."""
    context = get_default_context()
    row = await primary_node_record(context.database, Invitation, invitation_id)
    if row is None:
        raise AuthorizationUnavailableError()
    if required_node_text(row, "workspace_id") != workspace_id:
        raise AuthorizationUnavailableError()
    status = required_node_text(row, "status")
    revision = row["context"].get("invitation_revision")
    if type(revision) is not int or revision < 0:
        raise AuthorizationUnavailableError()
    if status not in {"pending", "accepted", "declined", "revoked", "expired"}:
        raise AuthorizationUnavailableError()
    kind = required_node_text(row, "target_resource_type", allow_empty=True)
    target_id = required_node_text(row, "target_resource_id", allow_empty=True)
    target_role = required_node_text(row, "target_resource_role", allow_empty=True)
    from app.services.invitation_topology import require_invitation_topology

    await require_invitation_topology(invitation_id, workspace_id, kind, target_id)
    if kind:
        if kind not in {"app", "track", "entry"} or not target_id:
            raise AuthorizationUnavailableError()
        relationship_literal(
            row,
            "target_resource_role",
            VALID_ROLES,
        )
        relationship_literal(row, "role", {"guest"})
    else:
        if target_id or target_role:
            raise AuthorizationUnavailableError()
        relationship_literal(row, "role", {"admin", "member", "guest"})
    required_node_text(row, "grant_baseline")
    # Adoption is an explicit provenance bundle, never a partially hydrated
    # alternative issuer. Old unadopted rows may omit the entire bundle.
    adoption = row["context"]
    adopted_by = adoption.get("adopted_by_user_id", "")
    adopted_membership = adoption.get("adopted_membership_id", "")
    adopted_at = adoption.get("adopted_at")
    if adopted_by or adopted_membership or adopted_at:
        required_node_text(row, "adopted_by_user_id")
        required_node_text(row, "adopted_membership_id")
        adopted_at = required_node_text(row, "adopted_at")
        try:
            if datetime.fromisoformat(adopted_at).tzinfo is None or revision < 1:
                raise ValueError("Invalid adoption provenance")
        except ValueError as exc:
            raise AuthorizationUnavailableError() from exc
    elif (
        type(adopted_by) is not str
        or type(adopted_membership) is not str
        or adopted_at is not None
    ):
        raise AuthorizationUnavailableError()
    relationship_flag(row, "can_create_apps")
    relationship_flag(row, "can_create_tracks")
    expiry = required_node_text(row, "expires_at")
    try:
        if datetime.fromisoformat(expiry).tzinfo is None:
            raise ValueError("Expiry requires timezone")
    except ValueError as exc:
        raise AuthorizationUnavailableError() from exc
    invitation = await Invitation.find_one({"id": invitation_id})
    if invitation is None or invitation.workspace_id != workspace_id:
        raise AuthorizationUnavailableError()
    if invitation.status not in {
        "pending",
        "accepted",
        "declined",
        "revoked",
        "expired",
    }:
        raise AuthorizationUnavailableError()
    return invitation
