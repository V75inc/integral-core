"""Persistent workspace blocks that survive membership removal and re-entry."""

import hashlib
import json

from jvspatial.core.context import get_default_context

from app.api.errors import (
    BadRequestError,
    InsufficientPermissionsError,
    ResourceConflictError,
)
from app.exceptions import AuthorizationUnavailableError
from app.models.access_lifecycle import WORKSPACE_SUSPENSION
from app.services.identity_admission import resolve_current_profile
from app.services.membership_write_scope import membership_write_scope
from app.services.permissions_process_cache import invalidate_user_aliases
from app.services.workspace_membership import _deliver, _manager, _record_change
from app.services.workspace_permissions import read_member_facts
from app.utils.time import utc_now_iso


async def suspension_fact(user_id: str, workspace_id: str) -> dict | None:
    """Read exact retained state without hydration defaults or duplicate collapse."""
    rows = await get_default_context().database.find(
        "edge",
        {
            "source": user_id,
            "target": workspace_id,
            "entity": WORKSPACE_SUSPENSION.__name__,
        },
        limit=2,
    )
    if not rows:
        return None
    if len(rows) != 1:
        raise AuthorizationUnavailableError()
    row = rows[0]
    data = row.get("context")
    if (
        not isinstance(data, dict)
        or data.get("state") not in {"suspended", "resumed"}
        or type(data.get("revision")) is not int
        or data["revision"] < 1
        or any(
            not isinstance(data.get(k), str) or not data[k]
            for k in ("actor_id", "changed_at")
        )
    ):
        raise AuthorizationUnavailableError()
    if data["state"] == "resumed":
        digest = data.get("reviewed_access_digest")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)
            or type(data.get("reviewed_revision")) is not int
            or data["reviewed_revision"] != data["revision"] - 1
            or data["reviewed_revision"] < 1
        ):
            raise AuthorizationUnavailableError()
    return row


async def workspace_suspended(user_id: str, workspace_id: str) -> bool:
    """Return a retained block; absence alone permits later membership admission."""
    fact = await suspension_fact(user_id, workspace_id)
    return fact is not None and fact["context"]["state"] == "suspended"


async def require_unsuspended(user_id: str, workspace_id: str) -> None:
    """Prevent invitation/link/provisioning re-entry for an identified subject."""
    if await workspace_suspended(user_id, workspace_id):
        raise InsufficientPermissionsError(message="Workspace membership is suspended")


async def set_workspace_suspension(
    *,
    actor_id: str,
    workspace_id: str,
    member_id: str,
    suspended: bool,
    reason: str = "",
    expected_revision: int | None = None,
    reviewed_access_digest: str = "",
) -> dict:
    """Only the protected owner can suspend/resume; resumption restores no grants."""
    if type(suspended) is not bool or type(reason) is not str or len(reason) > 1000:
        raise BadRequestError(message="Invalid suspension request")
    outbox_id = None
    async with membership_write_scope(
        workspace_id,
        principal_ids=(actor_id, member_id),
        active_principal_ids=(actor_id,),
    ):
        actor, workspace, owner = await _manager(actor_id, workspace_id)
        if not owner:
            raise InsufficientPermissionsError(
                message="Only the workspace owner may suspend members"
            )
        member = await resolve_current_profile(member_id, require_active=False)
        if member is None:
            raise BadRequestError(message="Unknown member")
        membership = await read_member_facts(member, workspace_id)
        if member.id == actor.id or (
            membership and membership["context"].get("role") == "owner"
        ):
            raise BadRequestError(message="Cannot suspend the workspace owner")
        existing = await suspension_fact(member.id, workspace_id)
        state = "suspended" if suspended else "resumed"
        if not suspended:
            if (
                type(expected_revision) is not int
                or expected_revision < 1
                or type(reviewed_access_digest) is not str
                or not reviewed_access_digest
            ):
                raise BadRequestError(
                    message="Resumption requires current suspension revision and reviewed access digest"
                )
            if (
                existing is not None
                and existing["context"]["state"] == "resumed"
                and existing["context"].get("reviewed_revision") == expected_revision
                and existing["context"].get("reviewed_access_digest")
                == reviewed_access_digest
                and existing["context"]["actor_id"] == actor.id
            ):
                return {
                    "workspace_id": workspace.id,
                    "member_user_id": member.id,
                    "state": "resumed",
                    "revision": existing["context"]["revision"],
                    "changed": False,
                }
            if existing is None or existing["context"]["revision"] != expected_revision:
                raise ResourceConflictError(message="Suspension review is stale")
            review = await _access_review(member.id, workspace_id)
            if review["reviewed_access_digest"] != reviewed_access_digest:
                raise ResourceConflictError(
                    message="Retained access changed since owner review"
                )
        changed = existing is None or existing["context"]["state"] != state
        if changed:
            revision = (existing["context"]["revision"] if existing else 0) + 1
            values = {
                "state": state,
                "actor_id": actor.id,
                "reason": reason,
                "changed_at": utc_now_iso(),
                "revision": revision,
                "reviewed_access_digest": (
                    reviewed_access_digest if not suspended else ""
                ),
                "reviewed_revision": expected_revision if not suspended else 0,
            }
            context = await member.get_context()
            edges = await context.find_edges_between(
                member.id, workspace.id, edge_class=WORKSPACE_SUSPENSION
            )
            if existing:
                if len(edges) != 1 or edges[0].id != existing["id"]:
                    raise AuthorizationUnavailableError()
                for key, value in values.items():
                    setattr(edges[0], key, value)
                await edges[0].save()
                edge_id = edges[0].id
            else:
                edge = await member.connect(
                    workspace, edge=WORKSPACE_SUSPENSION, **values
                )
                edge_id = edge.id
            outbox_id = await _record_change(
                actor,
                workspace,
                "workspace.member_update",
                existing,
                {
                    "membership_id": edge_id,
                    "grant_revision": revision,
                    "member_user_id": member.id,
                    **values,
                },
            )
        else:
            revision = existing["context"]["revision"]
        result = {
            "workspace_id": workspace.id,
            "member_user_id": member.id,
            "state": state,
            "revision": revision,
            "changed": changed,
        }
    invalidate_user_aliases(member)
    await _deliver(outbox_id)
    return result


async def _access_review(member_id: str, workspace_id: str) -> dict:
    """Digest retained primary rights; review never restores deleted grant lives."""
    from app.models.access_lifecycle import RESOURCE_ACCESS_BLOCK, SHARE_GRANT
    from app.models.edges import COLLABORATES_ON, EXCLUDED_FROM, IS_MEMBER_OF, OWNS

    database = get_default_context().database
    rows = await database.find(
        "edge",
        {
            "$or": [
                {
                    "source": member_id,
                    "entity": {
                        "$in": [
                            IS_MEMBER_OF.__name__,
                            COLLABORATES_ON.__name__,
                            OWNS.__name__,
                            EXCLUDED_FROM.__name__,
                            RESOURCE_ACCESS_BLOCK.__name__,
                        ]
                    },
                },
                {
                    "target": member_id,
                    "entity": SHARE_GRANT.__name__,
                },
            ]
        },
        limit=1001,
        sort=[("id", 1)],
    )
    if len(rows) > 1000:
        raise AuthorizationUnavailableError()
    # Full source facts prevent role/deny/removal ABA without exposing foreign
    # targets to this workspace owner. Changes elsewhere may require re-review.
    from app.services.access_delegation import subject_delegation_fingerprint

    delegation = await subject_delegation_fingerprint(member_id, workspace_id)
    digest = hashlib.sha256(
        json.dumps(
            [workspace_id, member_id, rows, delegation],
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()
    from app.models.nodes import App, Entry, Track
    from app.services.access_delegation import subject_delegation_review
    from app.services.resource_location import MANAGEMENT_APP_STATES, locate_resource
    from app.services.share_grant_sources import validate_share_grant
    from app.services.share_link_lifecycle import current_link

    membership = await read_member_facts(
        await resolve_current_profile(member_id, require_active=False), workspace_id
    )
    grants, exclusions, blocks, sources = [], [], [], []
    for row in rows:
        data = row.get("context")
        if not isinstance(data, dict):
            raise AuthorizationUnavailableError()
        if row["entity"] == SHARE_GRANT.__name__:
            link = await current_link(row["source"])
            values = validate_share_grant(row, link, member_id)
            if link.workspace_id == workspace_id:
                sources.append(
                    {
                        "grant_id": row["id"],
                        "link_id": link.id,
                        "resource_type": link.resource_type,
                        "resource_id": link.resource_id,
                        "role": values["role"],
                        "state": values["state"],
                        "revision": values["revision"],
                        "eligible": membership is not None
                        and values["membership_id"] == membership["id"]
                        and values["state"] == "active",
                    }
                )
            continue
        if row["entity"] == IS_MEMBER_OF.__name__:
            continue
        target = await database.find("node", {"id": row["target"]}, limit=2)
        kinds = {
            App._entity_name(): "app",
            Track._entity_name(): "track",
            Entry._entity_name(): "entry",
        }
        if (
            len(target) == 1
            and target[0].get("entity") == "Workspace"
            and row["entity"] == OWNS.__name__
        ):
            continue
        if len(target) != 1 or target[0].get("entity") not in kinds:
            raise AuthorizationUnavailableError()
        kind = kinds[target[0]["entity"]]
        location = await locate_resource(
            kind, row["target"], allowed_app_states=MANAGEMENT_APP_STATES
        )
        if location is None:
            raise AuthorizationUnavailableError()
        if location.workspace_id != workspace_id:
            continue
        image = {
            "grant_id": row["id"],
            "resource_type": kind,
            "resource_id": row["target"],
        }
        if row["entity"] == RESOURCE_ACCESS_BLOCK.__name__:
            if (
                data.get("workspace_id") != workspace_id
                or data.get("resource_type") != kind
                or data.get("state") not in {"blocked", "released"}
            ):
                raise AuthorizationUnavailableError()
            blocks.append(
                {**image, "state": data["state"], "reason": data.get("reason", "")}
            )
        elif row["entity"] == EXCLUDED_FROM.__name__:
            exclusions.append(image)
        else:
            role = "owner" if row["entity"] == OWNS.__name__ else data.get("role")
            if role not in {"owner", "admin", "editor", "commenter", "viewer"}:
                raise AuthorizationUnavailableError()
            grants.append({**image, "role": role})
    return {
        "reviewed_access_digest": digest,
        "retained_access": [{**image, "source": "direct"} for image in grants]
        + [{**image, "source": "share_link"} for image in sources if image["eligible"]],
        "direct_grants": grants,
        "source_grants": sources,
        "exclusions": exclusions,
        "absolute_blocks": blocks,
        "delegation": await subject_delegation_review(member_id, workspace_id),
        "complete": True,
    }


async def review_workspace_suspension(
    *, actor_id: str, workspace_id: str, member_id: str
) -> dict:
    """Owner previews current retained rights; no snapshot is reinstated on resume."""
    async with membership_write_scope(
        workspace_id,
        principal_ids=(actor_id, member_id),
        active_principal_ids=(actor_id,),
    ):
        _, _, owner = await _manager(actor_id, workspace_id)
        if not owner:
            raise InsufficientPermissionsError(
                message="Only the workspace owner may review suspension"
            )
        member = await resolve_current_profile(member_id, require_active=False)
        if member is None:
            raise BadRequestError(message="Unknown member")
        fact = await suspension_fact(member.id, workspace_id)
        membership = await read_member_facts(member, workspace_id)
        return {
            "workspace_id": workspace_id,
            "member_user_id": member.id,
            "state": fact["context"]["state"] if fact else "active",
            "revision": fact["context"]["revision"] if fact else 0,
            "membership_retained": membership is not None,
            "membership_role": membership["context"]["role"] if membership else None,
            **await _access_review(member.id, workspace_id),
        }
