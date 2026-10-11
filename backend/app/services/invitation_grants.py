"""Version-aware invitation grant changes and truthful terminal outcomes."""

import hashlib
import json
from typing import Any

from jvspatial.core.context import get_default_context

from app.exceptions import AuthorizationUnavailableError
from app.models.edges import COLLABORATES_ON, IS_MEMBER_OF
from app.services.authority_facts import relationship_flag, relationship_literal
from app.services.entry_write_scope import is_postgres_entry_transaction
from app.services.permissions import ROLE_RANK

ABSENT_GRANT = "absent-v1"
_RESOURCE_ROLES = ROLE_RANK
_MEMBER_ROLES = {"guest": 1, "member": 2, "admin": 3, "owner": 4}


async def _current_grant(source_id: str, target_id: str, edge: Any) -> dict | None:
    database = get_default_context().database
    rows = await database.find(
        "edge",
        {"source": source_id, "target": target_id, "entity": edge.__name__},
        limit=2,
    )
    if not rows:
        return None
    if len(rows) != 1:
        raise AuthorizationUnavailableError()
    row = rows[0]
    if (
        row.get("source") != source_id
        or row.get("target") != target_id
        or row.get("entity") != edge.__name__
        or not isinstance(row.get("id"), str)
        or not row["id"]
        or not isinstance(row.get("context"), dict)
    ):
        raise AuthorizationUnavailableError()
    relationship_literal(
        row, "role", _MEMBER_ROLES if edge is IS_MEMBER_OF else _RESOURCE_ROLES
    )
    if edge is IS_MEMBER_OF:
        relationship_flag(row, "can_create_apps")
        relationship_flag(row, "can_create_tracks")
    else:
        from app.services.permissions import _direct_role_from_rows

        _direct_role_from_rows([row])
    revision = row["context"].get("grant_revision", 0)
    if type(revision) is not int or revision < 0:
        raise AuthorizationUnavailableError()
    if "grant_revision" in row and (
        type(row["grant_revision"]) is not int or row["grant_revision"] != revision
    ):
        raise AuthorizationUnavailableError()
    return row


def _fingerprint(row: dict | None) -> str:
    if row is None:
        return ABSENT_GRANT
    # This technical version field is introduced on legacy rows while locking;
    # absence denotes its pre-versioning zero, never an implicit role grant.
    normalized = dict(row)
    normalized["context"] = dict(row["context"])
    normalized["context"].setdefault("grant_revision", 0)
    return hashlib.sha256(
        json.dumps(
            normalized, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _receipt(row: dict | None, outcome: str, edge: Any, ranks: dict) -> dict:
    """Describe the stored role and flags, including an absent conflicted grant."""
    return {
        "outcome": outcome,
        "role": relationship_literal(row, "role", ranks) if row else None,
        "can_create_apps": (
            relationship_flag(row, "can_create_apps")
            if row is not None and edge is IS_MEMBER_OF
            else None
        ),
        "can_create_tracks": (
            relationship_flag(row, "can_create_tracks")
            if row is not None and edge is IS_MEMBER_OF
            else None
        ),
    }


async def capture_grant_baseline(
    user_id: str | None, target_id: str, *, workspace_membership: bool
) -> str:
    """Bind issuance to the exact observed existing grant, or explicit absence."""
    if user_id is None:
        return ABSENT_GRANT
    edge = IS_MEMBER_OF if workspace_membership else COLLABORATES_ON
    return _fingerprint(await _current_grant(user_id, target_id, edge))


async def invitation_effective_role(invitation: Any, user_id: str) -> str | None:
    """Project current rights on response/retry, separately from the durable receipt."""
    from app.middleware.permissions_cache import isolated_permissions_cache
    from app.services.permissions import resolve_role
    from app.services.workspace_permissions import can_access_workspace

    with isolated_permissions_cache():
        if invitation.target_resource_type:
            return await resolve_role(
                user_id, invitation.target_resource_type, invitation.target_resource_id
            )
        role = await can_access_workspace(user_id, invitation.workspace_id)
        return None if role == "none" else role


async def apply_invitation_grant(
    user: Any,
    target: Any,
    *,
    baseline: str,
    role: str,
    workspace_membership: bool,
    can_create_apps: bool = False,
    can_create_tracks: bool = False,
    **metadata: Any,
) -> dict:
    """Upgrade only the issuance snapshot; preserve intervening changes."""
    edge = IS_MEMBER_OF if workspace_membership else COLLABORATES_ON
    ranks = _MEMBER_ROLES if workspace_membership else _RESOURCE_ROLES
    if role not in ranks or not baseline:
        raise AuthorizationUnavailableError()
    row = await _current_grant(user.id, target.id, edge)
    database = get_default_context().database
    if row is not None and is_postgres_entry_transaction(database):
        locked = await database.find_one_and_update(
            "edge",
            {"id": row["id"], "entity": edge.__name__},
            {"$inc": {"context.grant_revision": 0}},
        )
        row = await _current_grant(user.id, target.id, edge) if locked else None
    if _fingerprint(row) != baseline:
        return _receipt(row, "conflict", edge, ranks)
    if row is None:
        fields = {"role": role, **metadata}
        if workspace_membership:
            fields.update(
                can_create_apps=can_create_apps, can_create_tracks=can_create_tracks
            )
        await user.connect(target, edge=edge, **fields)
        created = await _current_grant(user.id, target.id, edge)
        if created is None:
            raise AuthorizationUnavailableError()
        return _receipt(created, "created", edge, ranks)
    old_role = relationship_literal(row, "role", ranks)
    new_role = role if ranks[role] > ranks[old_role] else old_role
    values: dict[str, Any] = {"role": new_role}
    if workspace_membership:
        values.update(
            can_create_apps=relationship_flag(row, "can_create_apps")
            or can_create_apps,
            can_create_tracks=relationship_flag(row, "can_create_tracks")
            or can_create_tracks,
        )
    if all(
        value
        == (
            relationship_literal(row, key, ranks)
            if key == "role"
            else relationship_flag(row, key)
        )
        for key, value in values.items()
    ):
        return _receipt(row, "unchanged", edge, ranks)
    updates = {f"context.{key}": value for key, value in values.items()}
    updates.update({key: value for key, value in values.items() if key in row})
    if "grant_revision" in row:
        updates["grant_revision"] = row["context"].get("grant_revision", 0) + 1
    result = await database.find_one_and_update(
        "edge",
        {"id": row["id"], "entity": edge.__name__},
        {"$set": updates, "$inc": {"context.grant_revision": 1}},
    )
    if result is None:
        raise AuthorizationUnavailableError()
    current = await _current_grant(user.id, target.id, edge)
    if current is None:
        raise AuthorizationUnavailableError()
    return _receipt(current, "upgraded", edge, ranks)
