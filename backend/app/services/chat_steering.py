"""Accept scoped corrections independently of the worker process."""

import hashlib
import json
from datetime import datetime, timedelta, timezone

from jvspatial.core.context import get_default_context

from app.agentive.harness.turn_input import thread_scope_key
from app.agentive.services.work_items import authorized_work_item_effect
from app.agentive.work_models import WorkItem
from app.api.errors import (
    OperationTransactionUnavailableError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from app.config import settings
from app.models.harness_records import HarnessSteeringInbox
from app.models.nodes import ChatThread
from app.schemas.agentive.steering import SteeringCorrectionRequest
from app.schemas.agentive.work import WorkError, WorkExecutionContext
from app.services.app_operations.transaction_scope import (
    graph_transaction_available,
    postgres_graph_transaction,
)
from app.services.credential_crypto import (
    decrypt_secret_from_storage,
    encrypt_secret_for_storage,
)
from app.services.workspace_permissions import can_access_workspace

MAX_CORRECTIONS = 16


def _inbox_id(work_item_id: str) -> str:
    return "o.HarnessSteeringInbox." + hashlib.sha256(work_item_id.encode()).hexdigest()


def _decode(inbox_id: str, ciphertext: str) -> dict[str, str]:
    return json.loads(decrypt_secret_from_storage(ciphertext, aad=inbox_id))


async def accept_steering_correction(
    work_item_id: str, principal_id: str, request: SteeringCorrectionRequest
) -> dict[str, str]:
    """Serialize acceptance with cancellation and terminal WorkItem transitions."""
    if not graph_transaction_available():
        raise OperationTransactionUnavailableError()
    async with postgres_graph_transaction() as transaction:
        row = await transaction.find_one_and_update(
            "object",
            {
                "id": f"o.WorkItem.{work_item_id}",
                "entity": WorkItem._entity_name(),
                "context.principal_id": principal_id,
                "context.kind": "chat_turn",
                "context.origin": "interactive_chat",
            },
            {"$set": {}},
        )
        if row is None:
            raise ResourceNotFoundError(message="Chat turn not found")
        fields = row["context"]
        thread = await ChatThread.get(fields["thread_id"])
        if (
            thread is None
            or thread.user_id != principal_id
            or thread.workspace_id != fields["workspace_id"]
            or await can_access_workspace(principal_id, thread.workspace_id) == "none"
        ):
            raise ResourceNotFoundError(message="Chat turn not found")

        inbox_id = _inbox_id(work_item_id)
        inbox = await transaction.get("object", inbox_id)
        previous_ciphertext = inbox["context"]["payload_ciphertext"] if inbox else ""
        corrections = _decode(inbox_id, previous_ciphertext) if inbox else {}
        correction_id = request.client_request_id
        if correction_id in corrections:
            if corrections[correction_id] != request.text:
                raise ResourceConflictError(message="Steering request ID already used")
        else:
            if (
                thread.active_work_item_id != work_item_id
                or fields["status"] not in {"queued", "running"}
                or fields.get("steering_closed")
                or fields.get("cancel_requested_at")
                or (fields.get("input_payload") or {}).get("message_role") == "system"
                or not fields.get("deadline_at")
                or datetime.fromisoformat(fields["deadline_at"])
                <= datetime.now(timezone.utc)
            ):
                raise ResourceConflictError(
                    message="Chat turn no longer accepts steering"
                )
            if len(corrections) >= MAX_CORRECTIONS:
                raise ResourceConflictError(message="Chat turn steering limit reached")
            corrections[correction_id] = request.text
            ciphertext = encrypt_secret_for_storage(
                json.dumps(corrections), aad=inbox_id
            )
            if inbox is None:
                await HarnessSteeringInbox.create_if_absent(
                    id=inbox_id,
                    payload_ciphertext=ciphertext,
                    work_item_id=work_item_id,
                    scope_key=thread_scope_key(
                        principal_id=principal_id,
                        workspace_id=thread.workspace_id,
                        thread_id=thread.id,
                    ),
                    expires_at=(
                        datetime.now(timezone.utc)
                        + timedelta(
                            days=settings.INTEGRAL_HARNESS_SESSION_RETENTION_DAYS
                        )
                    ).isoformat(),
                )
            else:
                updated = await transaction.find_one_and_update(
                    "object",
                    {"id": inbox_id, "context.payload_ciphertext": previous_ciphertext},
                    {"$set": {"context.payload_ciphertext": ciphertext}},
                )
                if updated is None:
                    raise ResourceConflictError(
                        message="Steering storage changed, retry the request"
                    )
    return {"correction_id": correction_id, "status": "accepted"}


async def load_steering_corrections(
    context: WorkExecutionContext, seen: set[str], *, close_if_drained: bool = False
) -> dict[str, str]:
    """Read pending corrections and serialize final drain with acceptance."""
    inbox_id = _inbox_id(context.work_item_id)
    if not close_if_drained:
        inbox = await get_default_context().database.get("object", inbox_id)
        if (
            inbox is None
            or set(_decode(inbox_id, inbox["context"]["payload_ciphertext"])) <= seen
        ):
            return {}

    async with authorized_work_item_effect(context) as graph:
        thread = await ChatThread.get(context.thread_id)
        if (
            thread is None
            or thread.user_id != context.principal_id
            or thread.workspace_id != context.workspace_id
            or thread.active_work_item_id != context.work_item_id
            or await can_access_workspace(context.principal_id, context.workspace_id)
            == "none"
        ):
            raise WorkError("work.policy_denied", "Steering scope no longer available")
        inbox = await graph.database.get("object", inbox_id)
        corrections = (
            _decode(inbox_id, inbox["context"]["payload_ciphertext"]) if inbox else {}
        )
        pending = {key: text for key, text in corrections.items() if key not in seen}
        if close_if_drained and not pending:
            await graph.database.find_one_and_update(
                "object",
                {"id": f"o.WorkItem.{context.work_item_id}"},
                {"$set": {"context.steering_closed": True}},
            )
        return pending
