"""Bounded current dependency admission for operation response disclosure."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

from app.api.errors import InsufficientPermissionsError
from app.schemas.policy import Resource, Subject
from app.services.app_operations.output_references import (
    MAX_MUTATION_REFERENCES,
    MAX_OUTPUT_REFERENCES,
    output_json_within_bounds,
    output_references,
)
from app.services.policy_engine import evaluate
from app.services.release_admission import release_admission
from app.services.resource_location import DATA_APP_STATES, locate_resource


def _denied() -> InsufficientPermissionsError:
    return InsufficientPermissionsError(message="Operation result access denied")


def _dependencies(result: dict) -> tuple[set[tuple[str, str]], set[str]]:
    if not isinstance(result, dict) or not output_json_within_bounds(result):
        raise _denied()
    output = result.get("output", {})
    evidence = result.get("evidence", {})
    if evidence is None:
        evidence = {}
    if not isinstance(output, dict) or not isinstance(evidence, dict):
        raise _denied()
    references = output_references(output)
    if references is None:
        raise _denied()
    owning_entries: set[str] = set()
    for container in (result, output, evidence):
        collection = container.get("object_refs", [])
        if (
            not isinstance(collection, list)
            or len(collection) > MAX_MUTATION_REFERENCES
        ):
            raise _denied()
        for reference in collection:
            if (
                not isinstance(reference, dict)
                or reference.get("kind") != "entry"
                or not isinstance(reference.get("id"), str)
                or not reference["id"]
            ):
                raise _denied()
            owning_entries.add(reference["id"])
            references.add(("entry", reference["id"]))
    if len(references) > MAX_OUTPUT_REFERENCES:
        raise _denied()
    return references, owning_entries


async def authorize_replayed_result(
    result: Dict[str, Any],
    *,
    user_id: str,
    workspace_id: str,
    app_id: str,
    admit_context: Callable[[Dict[str, Any]], Awaitable[None]] | None = None,
) -> None:
    """Admit the complete result under current canonical scope and read rights.

    A saved receipt is effect evidence, never a disclosure grant. This same
    gate is also used before initial response construction; it cannot execute
    a handler or discard a saved operation receipt.
    """
    references, owning_entries = _dependencies(result)
    async with release_admission(user_id, workspace_id):
        app_location = await locate_resource(
            "app", app_id, allowed_app_states=DATA_APP_STATES
        )
        if app_location is None or app_location.workspace_id != workspace_id:
            raise _denied()
        app_read = await evaluate(
            subject=Subject(kind="human", id=user_id),
            action="app.read",
            resource=Resource(kind="app", id=app_id, scope=f"app:{app_id}"),
            _dry_run=True,
        )
        if not app_read.allowed:
            raise _denied()
        if admit_context is not None:
            await admit_context(result)
        for kind, resource_id in sorted(references):
            # Unsupported resource families have no implicit read capability.
            # Parent-owned and identity references require explicit projection
            # contracts rather than treating an id as a traversal permission.
            read_kind, read_id = kind, resource_id
            if kind in {"comment", "attachment"}:
                from app.services.parent_owned_resource_scope import (
                    locate_entry_owned_resource,
                )

                parent = await locate_entry_owned_resource(kind, resource_id)
                location = parent.location if parent is not None else None
                read_kind, read_id = "entry", (
                    parent.entry_id if parent is not None else ""
                )
            else:
                location = await locate_resource(
                    kind, resource_id, allowed_app_states=DATA_APP_STATES
                )
            if (
                location is None
                or location.workspace_id != workspace_id
                or (resource_id in owning_entries and location.app_id != app_id)
            ):
                raise _denied()
            decision = await evaluate(
                subject=Subject(kind="human", id=user_id),
                action=f"{read_kind}.read",
                resource=Resource(
                    kind=read_kind, id=read_id, scope=f"{read_kind}:{read_id}"
                ),
                _dry_run=True,
            )
            if not decision.allowed:
                raise _denied()
