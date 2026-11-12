"""Owned actions on an exact delivered resident suggestion."""

import hashlib
import json
from contextlib import AsyncExitStack
from datetime import timedelta
from typing import Literal

from jvspatial.core.context import get_default_context

from app.api.errors import ResourceConflictError, ResourceNotFoundError
from app.models.edges import CONTAINS
from app.models.nodes import ChatMessage, ChatThread
from app.services.app_operations.transaction_scope import (
    current_graph_transaction_database,
    graph_transaction_available,
    postgres_graph_transaction,
)
from app.services.authorization_scope import authorization_scope
from app.services.identity_admission import identity_admission
from app.services.release_admission import release_admission
from app.utils.time import utc_now


def opportunity_receipt_id(principal_id: str, workspace_id: str, entry_id: str) -> str:
    """Identify the latest delivered material state per sponsored Entry."""
    identity = json.dumps([principal_id, workspace_id, entry_id], separators=(",", ":"))
    return (
        "o.ResidentOpportunityDelivery." + hashlib.sha256(identity.encode()).hexdigest()
    )


async def set_resident_opportunity_disposition(
    *,
    principal_id: str,
    workspace_id: str,
    message_id: str,
    entry_id: str,
    action: Literal["dismiss", "later", "prepare", "accept"],
) -> dict[str, bool | str]:
    """Apply an owned action to the exact delivered suggestion."""
    if not graph_transaction_available():
        raise ResourceConflictError(message="Suggestion actions are unavailable")

    unavailable = ResourceNotFoundError(message="Suggestion not found")
    graph = get_default_context()
    message = await ChatMessage.get(message_id)
    if message is None:
        raise unavailable
    async with AsyncExitStack() as admission:
        if action in {"prepare", "accept"}:
            # Take the canonical review's lock order before reading the receipt.
            # Prepare may enter that review scope without upgrading a shared lock.
            await admission.enter_async_context(postgres_graph_transaction())
            await admission.enter_async_context(identity_admission((principal_id,)))
            await admission.enter_async_context(authorization_scope(workspace_id))
        else:
            await admission.enter_async_context(
                release_admission(principal_id, workspace_id)
            )
        graph = get_default_context()
        transaction = current_graph_transaction_database()
        thread_row = await transaction.lock_record("node", message.thread_id)
        thread = (thread_row or {}).get("context") or {}
        if (
            not thread_row
            or thread_row.get("entity") != ChatThread._entity_name()
            or thread.get("user_id") != principal_id
            or thread.get("workspace_id") != workspace_id
            or thread.get("archived")
        ):
            raise unavailable
        row = await transaction.lock_record("node", message_id)
        message = (
            await graph._deserialize_entity(ChatMessage, row)
            if row and row.get("entity") == ChatMessage._entity_name()
            else None
        )
        if (
            message is None
            or message.role != "assistant"
            or message.thread_id != thread_row["id"]
            or not await graph.find_edges_between(
                thread_row["id"], message_id, edge_class=CONTAINS
            )
        ):
            raise unavailable
        metadata = dict(message.provider_metadata or {})
        suggestions = metadata.get("assignment_suggestions")
        if metadata.get("delivery_mode") != "assignment_digest" or not isinstance(
            suggestions, list
        ):
            raise unavailable
        suggestion = next(
            (
                item
                for item in suggestions
                if isinstance(item, dict) and item.get("entry_id") == entry_id
            ),
            None,
        )
        if suggestion is None:
            raise unavailable
        if action == "accept" and suggestion.get("accepted"):
            return {"accepted": True}
        if suggestion.get("review_requested"):
            if action == "prepare":
                return {"review_requested": True}
            raise ResourceConflictError(
                message="Preparation review has already been requested"
            )
        if suggestion.get("dismissed"):
            if action == "dismiss":
                return {"dismissed": True}
            raise ResourceConflictError(
                message="This suggestion has already been dismissed"
            )
        deferred_until = suggestion.get("deferred_until")
        if action == "later" and isinstance(deferred_until, str):
            return {"deferred_until": deferred_until}

        receipt_id = opportunity_receipt_id(principal_id, workspace_id, entry_id)
        receipt = await transaction.lock_record("object", receipt_id)
        fact = (receipt or {}).get("context") or {}
        if (
            not receipt
            or receipt.get("entity") != "ResidentOpportunityDelivery"
            or fact.get("principal_id") != principal_id
            or fact.get("workspace_id") != workspace_id
            or fact.get("entry_id") != entry_id
            or fact.get("message_id") != message_id
        ):
            raise ResourceConflictError(message="This suggestion has been superseded")
        disposition: dict[str, bool | str]
        if action in {"dismiss", "later"}:
            fact.pop("accepted_at", None)
            suggestion.pop("accepted", None)
        if action == "dismiss":
            fact["dismissed_at"] = utc_now().isoformat()
            fact.pop("deferred_until", None)
            disposition = {"dismissed": True}
            suggestion.pop("deferred_until", None)
        elif action == "later":
            deferred_until = (utc_now() + timedelta(hours=24)).isoformat()
            fact["deferred_until"] = deferred_until
            disposition = {"deferred_until": deferred_until}
        else:
            from app.services.resident_assignment import (
                get_resident_assignment,
                read_declared_task,
                request_resident_assignment_review,
            )

            if await transaction.lock_record("node", entry_id) is None:
                raise unavailable
            assignment = await get_resident_assignment(
                user_id=principal_id, workspace_id=workspace_id, entry_id=entry_id
            )
            snapshot = await read_declared_task(principal_id, workspace_id, entry_id)
            if (
                not assignment.can_request_review
                or snapshot is None
                or snapshot != (suggestion.get("track_id"), fact.get("fingerprint"))
            ):
                raise ResourceConflictError(
                    message="This task changed or is no longer ready for review"
                )
            if action == "prepare":
                await request_resident_assignment_review(
                    user_id=principal_id,
                    workspace_id=workspace_id,
                    entry_id=entry_id,
                    assignment_revision=assignment.revision,
                )
                fact["review_requested_at"] = utc_now().isoformat()
                disposition = {"review_requested": True}
            else:
                fact["accepted_at"] = utc_now().isoformat()
                disposition = {"accepted": True}
            fact.pop("deferred_until", None)
            suggestion.pop("deferred_until", None)
        await transaction.save("object", {**receipt, "context": fact})
        suggestion.update(disposition)
        metadata["assignment_suggestions"] = suggestions
        message.provider_metadata = metadata
        await message.save()
        return disposition
