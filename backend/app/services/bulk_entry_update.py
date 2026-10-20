"""Current selection admission for a reviewed collection update.

The collection becomes ordinary, revision-bound Entry commands so each command
retains validation, hooks, policy, and durable retry receipts. Selection admission
is an additional check, never an authority grant or an atomicity promise.
"""

from __future__ import annotations

from app.api.errors import (
    BadRequestError,
    InsufficientPermissionsError,
    ResourceConflictError,
)
from app.middleware.permissions_cache import isolated_permissions_cache
from app.schemas.policy import Resource, Subject
from app.services.policy_engine import evaluate
from app.services.resource_location import locate_resource

MAX_BULK_UPDATE_ENTRIES = 500


def selected_entry_ids(values: object) -> list[str]:
    """Validate a bounded selection and preserve its first occurrence order."""
    if not isinstance(values, list) or not values:
        raise BadRequestError(message="Choose at least one record to update.")
    if len(values) > MAX_BULK_UPDATE_ENTRIES:
        raise BadRequestError(message="Select up to 500 records for one review.")
    if any(
        not isinstance(value, str) or not value.strip() or value != value.strip()
        for value in values
    ):
        raise BadRequestError(message="Choose records returned by a current search.")
    return list(dict.fromkeys(values))


async def admit_update_selection(
    user_id: str,
    entry_ids: list[str],
    workspace_id: str,
    *,
    expected_revisions: dict[str, int] | None = None,
) -> None:
    """Admit every selected record before preparing or starting any update."""
    if not workspace_id:
        raise InsufficientPermissionsError(
            message="Open a workspace before updating records."
        )
    with isolated_permissions_cache():
        for entry_id in entry_ids:
            location = await locate_resource("entry", entry_id)
            if location is None or location.workspace_id != workspace_id:
                raise InsufficientPermissionsError(
                    message="Some selected records are no longer available in this workspace. Refresh the selection and try again."
                )
            decision = await evaluate(
                subject=Subject(kind="human", id=user_id),
                action="entry.update",
                resource=Resource(kind="entry", id=entry_id, scope=f"entry:{entry_id}"),
            )
            if not decision.allowed:
                raise InsufficientPermissionsError(
                    message="You don't have permission to update every selected record. Refresh the selection and try again."
                )
            if expected_revisions and entry_id in expected_revisions:
                from jvspatial.core.context import get_default_context

                from app.models.nodes import Entry
                from app.services.authority_facts import primary_node_record

                row = await primary_node_record(
                    get_default_context().database, Entry, entry_id
                )
                revision = (
                    row["context"].get("record_revision", row.get("record_revision", 1))
                    if row is not None
                    else None
                )
                conflicting = (
                    row is not None
                    and "record_revision" in row["context"]
                    and "record_revision" in row
                    and (
                        type(row["context"]["record_revision"])
                        is not type(row["record_revision"])
                        or row["context"]["record_revision"] != row["record_revision"]
                    )
                )
                if (
                    conflicting
                    or type(revision) is not int
                    or revision != expected_revisions[entry_id]
                ):
                    raise ResourceConflictError(
                        message="Some records changed after this review was prepared. Refresh the selection and review the changes again."
                    )
