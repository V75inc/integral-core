"""Exact private-library creator grants bound to a recipient membership lifetime."""

from app.exceptions import AuthorizationUnavailableError
from app.models.nodes import User
from app.services.authority_facts import relationship_literal

CREATOR_GRANT_FIELDS = (
    "grant_source",
    "grant_workspace_id",
    "grant_membership_id",
    "grant_issuer_id",
)


def creator_grant_binding(row: dict) -> dict[str, str] | None:
    """Legacy all-empty metadata is separate; partial bindings fail closed."""
    context = row.get("context")
    if context is not None and not isinstance(context, dict):
        raise AuthorizationUnavailableError()
    context = context or {}
    values = {}
    for field in CREATOR_GRANT_FIELDS:
        if field in context and field in row and context[field] != row[field]:
            raise AuthorizationUnavailableError()
        values[field] = context[field] if field in context else row.get(field)
    if all(value is None for value in values.values()):
        return None
    if any(
        not isinstance(value, str) or not value or value != value.strip()
        for value in values.values()
    ):
        raise AuthorizationUnavailableError()
    if (
        values["grant_source"] != "private_library_creation"
        or values["grant_issuer_id"] != row.get("source")
        or relationship_literal(row, "role", {"admin"}) != "admin"
    ):
        raise AuthorizationUnavailableError()
    return values


async def current_creator_grant(user: User, resource_id: str, row: dict) -> bool:
    """A fresh canonical private target and the original membership are required.

    Issuer is provenance for this durable human grant, not a live delegation
    chain. Changing creation flags does not revoke already issued curation.
    The edge identity changes on removal/recreation; this is a membership
    lifetime binding, not a claim of atomic authorization/effect fencing.
    """
    binding = creator_grant_binding(row)
    if binding is None:
        return True
    from app.services.model_authority import resolve_model_authority
    from app.services.resource_location import MANAGEMENT_APP_STATES
    from app.services.workspace_permissions import read_member_facts

    authority = await resolve_model_authority(
        resource_id, allowed_app_states=MANAGEMENT_APP_STATES
    )
    if (
        authority is None
        or authority.platform
        or authority.library_model_id != resource_id
        or authority.workspace_id != binding["grant_workspace_id"]
    ):
        raise AuthorizationUnavailableError()
    member = await read_member_facts(user, binding["grant_workspace_id"])
    if member is None:
        return False
    member_id = member.get("id")
    if not isinstance(member_id, str) or not member_id:
        raise AuthorizationUnavailableError()
    return member_id == binding["grant_membership_id"]
