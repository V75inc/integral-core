"""First publication of a named workspace-private library draft."""

from datetime import datetime, timezone
from typing import Optional

from app.api.errors import BadRequestError, InsufficientPermissionsError
from app.schemas.policy import Resource, Subject
from app.services.model_authority import get_current_model, resolve_model_authority


async def publish_initial_library_draft(
    *, model_id: str, user_id: str, expected_review_fingerprint: Optional[str] = None
) -> dict:
    """Keep the private package identity and ownership through first publication.

    PostgreSQL joins a graph transaction; other adapters do not acquire an
    atomic authorization/effect fence. Broader concurrent revocation and
    definition/release qualification remains a separate required gate.
    """
    from app.services.app_operations.transaction_scope import (
        graph_transaction_available,
        postgres_graph_transaction,
    )

    if graph_transaction_available():
        async with postgres_graph_transaction():
            return await _publish(model_id, user_id, expected_review_fingerprint)
    return await _publish(model_id, user_id, expected_review_fingerprint)


async def _publish(model_id: str, user_id: str, expected: Optional[str]) -> dict:
    from app.api.utils import export_node
    from app.middleware.permissions_cache import isolated_permissions_cache
    from app.services.change_event import emit_change_event
    from app.services.operational_model_authoring import publication_review_fingerprint
    from app.services.operational_model_runtime import (
        compile_canonical_manifest,
        invalidate_manifest_cache,
    )
    from app.services.policy_engine import evaluate

    model = await get_current_model(model_id)
    authority = await resolve_model_authority(model_id)
    if (
        model is None
        or authority is None
        or authority.platform
        or authority.library_model_id != model_id
        or not model.library_package
        or model.status != "draft"
        or model.draft_of_id
    ):
        raise BadRequestError(message="Not an initial private library draft")
    with isolated_permissions_cache():
        decision = await evaluate(
            subject=Subject(kind="human", id=user_id),
            action="operational_model.publish",
            resource=Resource(
                kind="operational_model",
                id=model_id,
                scope=f"operational_model:{model_id}",
            ),
        )
    if not decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")
    if expected is not None and publication_review_fingerprint(model, None) != expected:
        raise BadRequestError(message="Publication review is stale")
    compiled = compile_canonical_manifest(manifest=model.manifest or {})
    before = await export_node(model)
    now = datetime.now(timezone.utc).isoformat()
    model.manifest = compiled
    model.status = "published"
    model.published_at = now
    model.updated_at = now
    model.signature = {
        **(model.signature or {}),
        "published_by": user_id,
        "published_at": now,
    }
    await model.save()
    invalidate_manifest_cache()
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="operational_model.publish",
        resource_type="OperationalModel",
        resource_id=model.id,
        before=before,
        after=await export_node(model),
        scope=f"operational_model:{model.id}",
    )
    return {
        "published_id": model.id,
        "version_number": model.version_number,
        "initial_publication": True,
        "migration_run": {"executed": False, "ops": [], "errors": []},
        "migration_tracker": {"executed": False, "ops": [], "errors": []},
    }
