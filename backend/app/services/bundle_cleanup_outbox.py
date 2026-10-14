"""Durable identity for post-commit local bundle-registration cleanup."""

import hashlib
import json
from uuid import uuid4

from jvspatial.core.context import get_default_context

from app.utils.time import utc_now_iso

ENTITY = "BundleCleanupFact"


async def retain_bundle_cleanup(
    *,
    app_id: str,
    workspace_id: str,
    bundle_slug: str,
    agent_config_ids: list[str] | None = None,
) -> None:
    """Keep the admitted bundle identity before deleting its definition graph."""
    if not app_id or not workspace_id or not isinstance(bundle_slug, str):
        raise ValueError("Complete bundle cleanup identity required")
    from app.agentive.services.staging_execution import staged_cleanup_generation

    generation = staged_cleanup_generation(app_id) or uuid4().hex
    identity = hashlib.sha256(
        json.dumps([app_id, generation], separators=(",", ":")).encode()
    ).hexdigest()
    await get_default_context().database.insert_if_absent(
        "object",
        {
            "id": "o.BundleCleanupFact." + identity,
            "entity": ENTITY,
            "context": {
                "app_id": app_id,
                "generation": generation,
                "workspace_id": workspace_id,
                "bundle_slug": bundle_slug,
                "agent_config_ids": list(agent_config_ids or []),
                "status": "pending",
                "created_at": utc_now_iso(),
            },
        },
    )


async def _live_bundle_slug(app):
    """Resolve current identity strictly; lookup failure never means no install."""
    from app.services.app_graph import get_app_attached_operational_model
    from app.services.application_definitions import get_active_application_definition

    definition = await get_active_application_definition(app)
    manifest = definition.canonical_manifest if definition is not None else {}
    package = (manifest or {}).get("package") or {}
    if not isinstance(package, dict):
        raise ValueError("Current package identity is invalid")
    slug = package.get("slug") or package.get("name")
    if not slug:
        model = await get_app_attached_operational_model(app)
        package = ((model.manifest if model else {}) or {}).get("package") or {}
        if not isinstance(package, dict):
            raise ValueError("Current package identity is invalid")
        slug = package.get("slug") or package.get("name")
    identity = slug or app.source_operational_model_slug or ""
    if type(identity) is not str:
        raise ValueError("Current package identity must be a string")
    return identity


_seen: set[str] = set()
_cursor = ""


async def deliver_bundle_cleanup(*, database=None) -> int:
    """Replay cleanup on each process; protect current restore/reinstall lineage.

    Registry mutation is synchronous under a short workspace authority fence.
    No provider or filesystem I/O runs there. A delivered fact is still eligible
    on a new process: one replica's acknowledgement cannot clean another's RAM.
    """
    import logging

    from jvspatial.db import get_prime_database

    from app.agentive.workspace_agent_profile import invalidate_workspace_profile
    from app.models.nodes import App
    from app.services.app_invariant_guards import unregister_protected_fields
    from app.services.app_operations.registry import unregister_app_operations
    from app.services.app_operations.transaction_scope import (
        graph_transaction_available,
        postgres_graph_transaction,
    )
    from app.services.app_queries.registry import unregister_app_queries
    from app.services.authorization_scope import authorization_scope
    from app.services.hooks.registry import unregister_bundle_registrations
    from app.services.resource_location import locate_resource

    global _cursor
    db = database or get_prime_database()
    if not graph_transaction_available(db):
        return 0  # Native shared ordering is not claimed for local fallback stores.
    query = {"entity": ENTITY}
    if _cursor:
        query["id"] = {"$gt": _cursor}
    rows = await db.find("object", query, limit=1000, sort=[("id", 1)])
    _cursor = rows[-1]["id"] if len(rows) == 1000 else ""
    delivered = 0
    for row in rows:
        if row["id"] in _seen:
            continue
        try:
            data = row.get("context") or {}
            app_id, workspace_id, slug = (
                data.get(key) for key in ("app_id", "workspace_id", "bundle_slug")
            )
            if (
                not all(type(value) is str for value in (app_id, workspace_id, slug))
                or not app_id
                or not workspace_id
            ):
                raise ValueError("Invalid bundle cleanup identity")
            generation = data.get("generation")
            expected_id = (
                hashlib.sha256(
                    json.dumps([app_id, generation], separators=(",", ":")).encode()
                ).hexdigest()
                if type(generation) is str and generation
                else hashlib.sha256(app_id.encode()).hexdigest()
            )
            if row["id"] != "o.BundleCleanupFact." + expected_id:
                raise ValueError("Bundle cleanup identity changed")
            async with postgres_graph_transaction(db) as held:
                async with authorization_scope(workspace_id):
                    live = await held.get("node", app_id)
                    if live is not None and (live.get("context") or {}).get(
                        "lifecycle_state", "active"
                    ) not in {"uninstalled", "deleted"}:
                        continue
                    candidates = await App.find(
                        {"workspace_id": workspace_id, "lifecycle_state": "active"}
                    )
                    current_install = False
                    for candidate in candidates:
                        location = await locate_resource("app", candidate.id)
                        if location is None or location.workspace_id != workspace_id:
                            raise ValueError("Current install scope is unavailable")
                        if slug and await _live_bundle_slug(candidate) == slug:
                            current_install = True
                            break
                    if current_install:
                        continue  # A live replacement owns this workspace/slug now.
                    if slug:
                        unregister_bundle_registrations(workspace_id, slug)
                    unregister_app_operations(workspace_id, app_id)
                    unregister_app_queries(workspace_id, app_id)
                    unregister_protected_fields(workspace_id, app_id)
                    from app.agentive.services.uplink_registry import uplink_registry

                    agent_ids = data.get("agent_config_ids") or []
                    if not isinstance(agent_ids, list) or any(
                        type(value) is not str for value in agent_ids
                    ):
                        raise ValueError("Invalid agent cleanup identities")
                    for config_id in agent_ids:
                        if await held.get("node", config_id) is None:
                            await uplink_registry.unregister(config_id)
                    invalidate_workspace_profile(workspace_id)
                    await held.find_one_and_update(
                        "object",
                        {"id": row["id"]},
                        {
                            "$set": {
                                "context.status": "delivered",
                                "context.delivered_at": utc_now_iso(),
                            }
                        },
                    )
            _seen.add(row["id"])
            delivered += 1
        except Exception:
            logging.getLogger(__name__).exception(
                "Bundle cleanup remains pending: %s", row.get("id")
            )
    if len(_seen) > 10000:
        _seen.clear()  # Bounded RAM; repeating an old eligible cleanup is idempotent.
    return delivered
