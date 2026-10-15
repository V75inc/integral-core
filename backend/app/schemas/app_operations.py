"""Schemas for typed app operation HTTP surface."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field


class AppOperationDescriptor(BaseModel):
    key: str
    action: Optional[str] = None
    definition_id: Optional[str] = None
    allowed: Optional[bool] = None
    decision_reason: Optional[str] = None
    kind: str
    name: str
    description: str = ""
    policy_action: Optional[str] = None
    capability: Optional[str] = None
    staging_level: Optional[str] = None
    idempotency_key: Optional[str] = None
    timeout_seconds: Optional[int] = None
    input_schema: Dict[str, Any] = Field(default_factory=dict)
    output_schema: Dict[str, Any] = Field(default_factory=dict)


class AppQueryDescriptor(BaseModel):
    key: str
    kind: str = "read"
    handler_key: str
    input_schema: Dict[str, Any] = Field(default_factory=dict)
    output_schema: Dict[str, Any] = Field(default_factory=dict)
    provenance: Dict[str, Any] = Field(default_factory=dict)


class AppOperationsListResponse(BaseModel):
    app_id: str
    operations: List[AppOperationDescriptor]
    queries: List[AppQueryDescriptor] = Field(default_factory=list)


class AppOperationInvokeRequest(BaseModel):
    input: Dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}


class AppOperationInvokeResponse(BaseModel):
    app_id: str
    operation_key: str
    output: Dict[str, Any]
    receipt: Optional[Dict[str, Any]] = None
    object_refs: List[Dict[str, Any]] = Field(default_factory=list)
    evidence: Optional[Dict[str, Any]] = None
    operation_receipt: Optional[Dict[str, Any]] = None
    output_disposition: Literal["available"] = "available"
    current_state_verified: Literal[False] = False


class RecordedOperationReceipt(BaseModel):
    """An original journal reference, with no private execution identity."""

    id: str = Field(pattern=r"^o\.OperationExecutionReceipt\.[0-9a-f]{64}$")
    status: Literal["succeeded"] = "succeeded"
    replayed: Literal[True] = True

    model_config = {"extra": "forbid"}


class AppOperationOutputWithheld(BaseModel):
    """Past completion is known; neither output nor current state is disclosed."""

    operation_receipt: RecordedOperationReceipt
    output_disposition: Literal["withheld"] = "withheld"
    current_state_verified: Literal[False] = False

    model_config = {"extra": "forbid"}


AppOperationResponse = Union[AppOperationInvokeResponse, AppOperationOutputWithheld]
