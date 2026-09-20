"""Schemas for typed app operation HTTP surface."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class AppOperationDescriptor(BaseModel):
    key: str
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


class AppOperationInvokeResponse(BaseModel):
    app_id: str
    operation_key: str
    output: Dict[str, Any]
    receipt: Optional[Dict[str, Any]] = None
    object_refs: List[Dict[str, Any]] = Field(default_factory=list)
    evidence: Optional[Dict[str, Any]] = None
    operation_receipt: Optional[Dict[str, Any]] = None
