"""Transaction-local authority for a voice continuation of one active turn."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.agentive.harness.turn_input import load_turn_input_capsule
from app.api.errors import InsufficientPermissionsError, ResourceConflictError
from app.middleware.permissions_cache import isolated_permissions_cache
from app.services.workspace_permissions import can_access_workspace


def is_workspace_voice_thread(row: dict | None, principal: str, workspace: str) -> bool:
    """Check native workspace-assistant scope against a primary graph row."""
    fields = (row or {}).get("context") or {}
    return bool(
        row
        and row.get("entity") == "ChatThread"
        and fields.get("user_id") == principal
        and fields.get("workspace_id") == workspace
        and fields.get("provider_id") == "integral_native"
        and not fields.get("archived")
        and not fields.get("agent_profile_key")
        and not (fields.get("provider_session_id") or "").startswith("wa:")
    )


async def require_voice_workspace_role(principal_id: str, workspace_id: str) -> None:
    """Recheck a voice-capable workspace role without a cached permission."""
    with isolated_permissions_cache():
        if await can_access_workspace(principal_id, workspace_id) not in {
            "owner",
            "admin",
            "member",
        }:
            raise InsufficientPermissionsError(message="Workspace unavailable")


@dataclass(frozen=True)
class VoiceFollowUpFence:
    """Trusted exact target, never decoded from model or HTTP request data."""

    principal_id: str
    workspace_id: str
    thread_id: str
    work_item_id: str

    async def lock_current(
        self, transaction: Any, work_item: dict, *, steering: bool = False
    ) -> None:
        """Lock in terminal-transition order: exact WorkItem first, then thread."""
        fields = work_item["context"]
        if (
            work_item["id"] != f"o.WorkItem.{self.work_item_id}"
            or fields["principal_id"] != self.principal_id
            or fields["workspace_id"] != self.workspace_id
            or fields["thread_id"] != self.thread_id
            or fields["kind"] != "chat_turn"
            or fields["origin"] != "interactive_chat"
            or fields["status"] not in {"queued", "running", "waiting_for_human"}
            or fields.get("cancel_requested_at")
            or not fields.get("deadline_at")
            or datetime.fromisoformat(fields["deadline_at"])
            <= datetime.now(timezone.utc)
            or (fields.get("input_payload") or {}).get("message_role") == "system"
        ):
            raise ResourceConflictError(message="The voice target is no longer active")

        # The pinned PostgreSQL transaction uses SELECT FOR UPDATE for this
        # simple equality selector. No write is based on a cached ChatThread,
        # and thread admission/release cannot swap the pointer before commit.
        thread = await transaction.find_one_and_update(
            "node", {"id": self.thread_id}, {"$set": {}}
        )
        if not is_workspace_voice_thread(thread, self.principal_id, self.workspace_id):
            raise InsufficientPermissionsError(message="Conversation unavailable")
        if thread["context"].get("active_work_item_id") != self.work_item_id:
            raise ResourceConflictError(message="The voice target is no longer active")
        await require_voice_workspace_role(self.principal_id, self.workspace_id)

        if steering:
            payload = fields.get("input_payload") or {}
            try:
                capsule = await load_turn_input_capsule(
                    capsule_id=payload.get("capsule_id", ""),
                    expected_digest=payload.get("capsule_digest", ""),
                    principal_id=self.principal_id,
                    workspace_id=self.workspace_id,
                    thread_id=self.thread_id,
                    work_item_id=self.work_item_id,
                )
            except (LookupError, ValueError):
                raise ResourceConflictError(
                    message="The voice target does not accept spoken refinement"
                ) from None
            if (
                capsule.accepted_message_id != payload.get("accepted_message_id")
                or capsule.execution_context.extra_data.get("voice_delegate")
                is not True
                or capsule.execution_context.host_control is not None
            ):
                # Never put spoken text into a typed worker that might hold
                # typed approval authority, nor into a host-control turn.
                raise ResourceConflictError(
                    message="The voice target does not accept spoken refinement"
                )
