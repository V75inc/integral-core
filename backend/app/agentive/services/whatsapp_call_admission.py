"""Pure WhatsApp caller admission. No I/O."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.agentive.services.channel_identity import normalize_whatsapp_e164


@dataclass(frozen=True)
class CallerAdmission:
    """Answer or refuse one inbound caller. Refuse is not an exception."""

    decision: Literal["answer", "refuse"]
    reason: str
    verified_e164: str = ""
    workspace_id: str = ""
    principal_id: str = ""


def _comparable_e164(phone: str) -> str:
    """Ensure a leading ``+``, then return canonical E.164 or empty."""
    raw = (phone or "").strip()
    if not raw:
        return ""
    if not raw.startswith("+"):
        raw = "+" + raw
    return normalize_whatsapp_e164(raw)


def _refuse(reason: str) -> CallerAdmission:
    return CallerAdmission(decision="refuse", reason=reason)


def admit_whatsapp_caller(
    caller: str,
    *,
    verified_e164: str,
    line_enabled: bool,
    calling_enabled: bool,
    ambiguous_verified_number: bool = False,
    workspace_id: str = "",
    ambiguous_workspace: bool = False,
    principal_id: str = "",
) -> CallerAdmission:
    """Match the caller to one verified personal number. No I/O.

    Cardinality flags and the workspace id are supplied by the caller.
    Several failures resolve in this order: empty caller, invalid caller,
    ambiguous verified number, missing verified number, mismatch, disabled
    line, disabled calling, ambiguous workspace, missing workspace.
    """
    if not (caller or "").strip():
        return _refuse("empty_caller")
    normalized_caller = _comparable_e164(caller)
    if not normalized_caller:
        return _refuse("invalid_caller")
    if ambiguous_verified_number:
        return _refuse("ambiguous_verified_number")
    normalized_verified = _comparable_e164(verified_e164)
    if not normalized_verified:
        return _refuse("no_verified_number")
    if normalized_caller != normalized_verified:
        return _refuse("caller_mismatch")
    if not line_enabled:
        return _refuse("line_disabled")
    if not calling_enabled:
        return _refuse("calling_disabled")
    if ambiguous_workspace:
        return _refuse("ambiguous_workspace")
    workspace = (workspace_id or "").strip()
    if not workspace:
        return _refuse("no_workspace")
    return CallerAdmission(
        decision="answer",
        reason="answer",
        verified_e164=normalized_verified,
        workspace_id=workspace,
        principal_id=principal_id or "",
    )


def admit_whatsapp_visitor_caller(
    caller: str,
    *,
    line_enabled: bool,
    ready_workspace_ids: tuple[str, ...] = (),
) -> CallerAdmission:
    """Admit an unknown caller to one live public profile. No I/O.

    ``ready_workspace_ids`` are already limited to enabled grants whose
    calling flag is on and whose public profile is still live. This does
    not select a member, a staff profile, or a native voice session.
    """
    if not (caller or "").strip():
        return _refuse("empty_caller")
    if not _comparable_e164(caller):
        return _refuse("invalid_caller")
    if not line_enabled:
        return _refuse("line_disabled")
    ready = tuple(workspace for workspace in ready_workspace_ids if workspace)
    if len(ready) > 1:
        return _refuse("ambiguous_public_profile")
    if len(ready) != 1:
        return _refuse("no_public_profile")
    return CallerAdmission(
        decision="answer",
        reason="public_profile",
        workspace_id=ready[0],
    )
