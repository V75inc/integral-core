"""Authenticated employee onboarding form API shapes."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class MeOnboardingFormUpdateRequest(BaseModel):
    title: Optional[str] = None
    body: Optional[str] = None
    custom_fields: Optional[Dict[str, Any]] = None
    expected_record_revision: Optional[int] = Field(default=None, ge=1)
    expected_schema_revision: Optional[int] = Field(default=None, ge=1)


class MeOnboardingContractDecisionRequest(BaseModel):
    action: str = Field(description="accept or reject")
    signature_png: Optional[str] = None
    reason: Optional[str] = None
