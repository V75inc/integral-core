"""Scoped decision contract for staged chat writes.

Presentation surfaces are adapters. They may identify the proposal and the
user's choice, but they cannot supply authority: this module re-loads the
server-held staged change and binds it to its principal, workspace, and
conversation before delegating to the existing staging executor.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict

from app.agentive.services.approval_policy import ApprovalEffectClass, effect_class
from app.agentive.services.staging_apply import bless_and_execute
from app.agentive.staging import StagingError, get_token, revoke_token

Decision = Literal["approve", "reject"]
DecisionSource = Literal["chat", "card", "channel", "inbox"]


class StagedWriteProposal(BaseModel):
    """Immutable public decision contract built from a server-held proposal."""

    model_config = ConfigDict(frozen=True)

    proposal_id: str
    principal_id: str
    workspace_id: str
    conversation_id: Optional[str]
    target_id: Optional[str]
    effect_kind: str
    effect_class: ApprovalEffectClass
    summary: str
    human_diff: str
    idempotency_key: Optional[str]
    expires_at: str
    policy_version: str = "integral-staged-write-v1"


def proposal_from_staged(staged: Any) -> StagedWriteProposal:
    """Project the actionable review details without exposing executor payload."""
    payload = getattr(staged, "payload", {}) or {}
    target_id = next(
        (
            str(payload[key])
            for key in (
                "entry_id",
                "track_id",
                "app_id",
                "attachment_id",
                "connector_id",
                "routine_id",
                "resource_id",
            )
            if payload.get(key)
        ),
        None,
    )
    expires = getattr(staged, "expires_at", None)
    return StagedWriteProposal(
        proposal_id=str(staged.token),
        principal_id=str(staged.user_id),
        workspace_id=str(getattr(staged, "workspace_id", "") or ""),
        conversation_id=getattr(staged, "session_id", None),
        target_id=target_id,
        effect_kind=str(staged.kind),
        effect_class=effect_class(str(staged.kind), payload),
        summary=str(staged.summary),
        human_diff=str(staged.diff_human),
        idempotency_key=getattr(staged, "idempotency_key", None),
        expires_at=expires.isoformat() if expires is not None else "",
    )


async def decide_staged_write(
    *,
    principal_id: str,
    proposal_id: str,
    decision: Decision,
    source: DecisionSource,
    workspace_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    request: Optional[Any] = None,
    strong_confirmation: bool = False,
) -> dict[str, Any]:
    """Resolve one current staged proposal through the canonical decision path.

    High-impact/destructive decisions require the explicit card control. A
    model or external text adapter cannot silently turn a natural-language
    approval into that stronger confirmation.
    """
    staged = await get_token(proposal_id)
    if staged is None:
        raise StagingError(
            "unknown_token", "The proposed change is no longer available."
        )
    if staged.user_id != principal_id:
        raise StagingError(
            "wrong_user", "The proposed change is not available to this user."
        )
    if workspace_id is not None and staged.workspace_id != workspace_id:
        raise StagingError(
            "pending_item_changed", "The proposed change is no longer available."
        )
    if conversation_id is not None and staged.session_id != conversation_id:
        raise StagingError(
            "pending_item_changed", "The proposed change is no longer available."
        )
    # Explicit card retry resumes the already-approved failed execution through
    # its existing claim/progress cursor; it does not grant a new proposal.
    retry_failed_card = (
        source == "card"
        and staged.state == "blessed"
        and bool(getattr(staged, "last_error", None))
    )
    if decision == "approve" and staged.state != "pending" and not retry_failed_card:
        raise StagingError(
            "pending_item_changed",
            "The proposed change is no longer awaiting a decision.",
        )
    if decision == "reject" and staged.state not in {"pending", "blessed"}:
        raise StagingError(
            "pending_item_changed",
            "The proposed change is no longer awaiting a decision.",
        )

    proposal = proposal_from_staged(staged)
    if decision == "approve":
        if proposal.effect_class is ApprovalEffectClass.DESTRUCTIVE_SECURITY and (
            source != "card" or not strong_confirmation
        ):
            raise StagingError(
                "strong_confirmation_required",
                "Review this high-impact change in its approval card to confirm it.",
            )
        result = await bless_and_execute(
            user_id=principal_id,
            token=proposal_id,
            request=request,
            decision_source=source,
        )
        state = (result.get("staged_change") or {}).get("state", "")
        success = bool(result.get("consumed")) or state == "blessed"
        payload = result
    else:
        rejected = await revoke_token(
            user_id=principal_id,
            token=proposal_id,
            decision_source=source,
        )
        state = rejected.state
        success = True
        payload = {"ok": True, "staged_change": rejected.to_dict()}

    if success and thread_id:
        from app.models.nodes import ChatThread
        from app.services.prompt_queue import mark_write_item

        thread = await ChatThread.get(thread_id)
        if thread is not None and getattr(thread, "user_id", "") == principal_id:
            await mark_write_item(
                user_id=principal_id,
                thread=thread,
                token=proposal_id,
                status="approved" if decision == "approve" else "rejected",
            )

    return {
        "ok": success,
        "decision": decision,
        "state": state,
        "proposal": proposal.model_dump(mode="json"),
        **payload,
    }
