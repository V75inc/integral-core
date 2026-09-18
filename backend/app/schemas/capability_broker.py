"""Capability broker invocation, receipt, and result contracts."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

CapabilitySource = Literal["core", "app", "connector"]
CapabilityOrigin = Literal["chat", "http", "mcp", "view"]
CapabilityOpClass = Literal["read", "propose", "execute"]

ERR_NOT_IN_SNAPSHOT = "capability.not_in_snapshot"
ERR_REVOKED = "capability.revoked"
ERR_UPGRADED = "capability.upgraded"
ERR_DENIED = "capability.denied"
ERR_WORKSPACE_MISMATCH = "capability.workspace_mismatch"
ERR_IDENTITY_MISMATCH = "capability.identity_mismatch"
ERR_RUN_NOT_FOUND = "capability.run_not_found"
ERR_RUN_TERMINAL = "capability.run_terminal"
ERR_ADAPTER = "capability.adapter_failed"
ERR_IN_PROGRESS = "capability.in_progress"
ERR_AMBIGUOUS_DECLARATION = "capability.ambiguous_declaration"

SHORT_LIVED_ORIGINS = frozenset({"http", "mcp", "view"})


class CapabilityInvocation(BaseModel):
    """Internal broker request. Surfaces never pass a raw client request."""

    run_id: str
    principal_id: str
    workspace_id: str
    origin: CapabilityOrigin
    capability_key: str
    source: CapabilitySource = "core"
    op_class: CapabilityOpClass = "read"
    arguments: Dict[str, Any] = Field(default_factory=dict)
    idempotency_key: Optional[str] = None
    app_id: Optional[str] = None
    connector_id: Optional[str] = None
    session_id: Optional[str] = None
    interaction_id: Optional[str] = None
    skill_tools_required: Optional[List[str]] = None


class ReceiptRef(BaseModel):
    run_id: str
    step_key: str
    idempotency_key: str
    status: str
    capability_key: str
    origin: str
    snapshot_fingerprint: str = ""


class CapabilityResult(BaseModel):
    ok: bool
    error_code: str = ""
    message: str = ""
    data: Any = None
    receipt: Optional[ReceiptRef] = None
    policy_decision: str = ""
    snapshot_fingerprint: str = ""
    snapshot_divergence: bool = False
    replayed: bool = False

    def for_model(self) -> Dict[str, Any]:
        """Model-safe payload: original data plus a redacted receipt pointer."""
        receipt = self.receipt.model_dump() if self.receipt is not None else None
        if not self.ok:
            return {
                "error": True,
                "error_code": self.error_code,
                "message": self.message,
                "_receipt": receipt,
            }
        if isinstance(self.data, dict):
            return {**self.data, "_receipt": receipt}
        if self.data is None:
            return {"_receipt": receipt}
        return {"result": self.data, "_receipt": receipt}
