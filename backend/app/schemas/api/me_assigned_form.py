"""Authenticated member assigned-form API shapes (neutral substrate naming)."""

from __future__ import annotations

from app.schemas.api.me_onboarding import (
    MeOnboardingContractDecisionRequest as MeAssignedFormContractDecisionRequest,
)
from app.schemas.api.me_onboarding import (
    MeOnboardingFormUpdateRequest as MeAssignedFormUpdateRequest,
)

__all__ = [
    "MeAssignedFormUpdateRequest",
    "MeAssignedFormContractDecisionRequest",
]
