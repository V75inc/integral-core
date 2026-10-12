"""Current stewardship of a rooted policy's pending decision inventory."""

from app.schemas.policy import Resource


async def can_decide_policy_approval(user_id: str, resource: Resource) -> bool:
    """Owners govern decisions; issuers need current subject and target authority."""
    from jvspatial.core.context import get_default_context

    from app.models.nodes import Policy
    from app.services.authority_facts import primary_node_record, required_node_text
    from app.services.permissions import get_user_node
    from app.services.policy_registry import _resolve_subject_node
    from app.services.resource_location import MANAGEMENT_APP_STATES, locate_resource
    from app.services.workspace_governance import owner_governance_allowed
    from app.services.workspace_permissions import can_access_workspace

    if (
        resource.kind != "policy"
        or not resource.id
        or resource.scope != f"policy:{resource.id}"
    ):
        return False
    user = await get_user_node(user_id)
    row = await primary_node_record(get_default_context().database, Policy, resource.id)
    if user is None or row is None:
        return False
    body = row["context"]
    scope = required_node_text(row, "scope")
    kind, separator, identity = scope.partition(":")
    if (
        not separator
        or kind not in {"workspace", "app", "track", "entry"}
        or not identity
    ):
        return False
    location = await locate_resource(
        kind, identity, allowed_app_states=MANAGEMENT_APP_STATES
    )
    if location is None:
        return False
    subject = await _resolve_subject_node(
        body.get("subject_kind"), body.get("subject_id")
    )
    if subject is None:
        return False
    from app.models.edges import HAS_POLICY
    from app.schemas.policy import Subject
    from app.services.service_delegation import _subject_contract

    database = get_default_context().database
    sources = await database.find(
        "edge", {"target": row["id"], "entity": HAS_POLICY._entity_name()}, limit=2
    )
    if len(sources) != 1 or sources[0].get("source") != subject.id:
        return False
    subject_row = await primary_node_record(database, type(subject), subject.id)
    if subject_row is None:
        return False
    rooted = await _subject_contract(
        Subject(kind=body["subject_kind"], id=subject.id), subject_row
    )
    if (
        not rooted
        and body["subject_kind"] == "agent"
        and subject_row["context"].get("scope") == "personal"
    ):
        # An inactive personal controller can leave an intent to be rejected.
        # This proves attachment only; acceptance separately requires live
        # actor, issuer, policy and target authority in the executor.
        from app.agentive.edges import HAS_AGENT_CONFIG
        from app.models.edges import CATALOGS
        from app.models.nodes import USERS_REGISTRY_ID
        from app.services.identity_admission import resolve_current_profile

        controller = await resolve_current_profile(
            subject_row["context"].get("user_id", ""), require_active=False
        )
        attachments = await database.find(
            "edge",
            {"target": subject.id, "entity": HAS_AGENT_CONFIG._entity_name()},
            limit=2,
        )
        catalogs = await database.find(
            "edge",
            {"target": controller.id, "entity": CATALOGS._entity_name()},
            limit=2,
        )
        rooted = (
            len(attachments) == 1
            and attachments[0].get("source") == controller.id
            and len(catalogs) == 1
            and catalogs[0].get("source") == USERS_REGISTRY_ID
        )
    if not rooted:
        return False
    role = await can_access_workspace(user.id, location.workspace_id)
    if role == "owner" and owner_governance_allowed():
        return True
    issuer = await get_user_node(required_node_text(row, "created_by"))
    if issuer is None or issuer.id != user.id or role == "none":
        return False
    from app.services.policy_engine import _evaluate_human_default

    if kind == "workspace":
        return role in {"owner", "admin"}
    return await _evaluate_human_default(
        f"{kind}.read", Resource(kind=kind, id=identity, scope=scope), user.id
    )
