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


class AppOperationsListResponse(BaseModel):
    app_id: str
    operations: List[AppOperationDescriptor]


class AppOperationInvokeRequest(BaseModel):
    input: Dict[str, Any] = Field(default_factory=dict)


class AppOperationInvokeResponse(BaseModel):
    app_id: str
    operation_key: str
    output: Dict[str, Any]
