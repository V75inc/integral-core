"""Commit generic resource events with graph effects before offering delivery."""

from uuid import uuid4


async def queue_resource_event(
    event: dict,
    workspace_id: str,
    native: bool,
    *,
    namespace: str = "resource-metadata-v1",
) -> str | None:
    """Queue a staged or ordinary native lifecycle fact inside its graph commit."""
    from app.agentive.services.staging_execution import capture_staging_change_event
    from app.services.app_operations.event_outbox import insert_revision_event
    from app.services.app_operations.transaction_scope import (
        current_graph_transaction_database,
    )

    if await capture_staging_change_event(event):
        return None
    if native:
        return await insert_revision_event(
            transaction=current_graph_transaction_database(),
            workspace_id=workspace_id,
            namespace=namespace + ":" + uuid4().hex,
            subject_id=event["resource_id"],
            revision=1,
            event=event,
        )
    return None
