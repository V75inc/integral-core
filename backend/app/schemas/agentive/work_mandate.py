"""Immutable review contract for bounded work, not execution authority by itself.

Only a server-verified approval and current permissions may authorize this
snapshot. Persistence, reservations and dispatch wiring are separate gates.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CanonicalKey = Annotated[
    str, Field(min_length=1, max_length=255, pattern=r"^\S(?:.*\S)?$")
]
ReviewText = Annotated[str, Field(min_length=1, max_length=4000)]


class MandateContract(BaseModel):
    """Deeply immutable values: no mutable dictionaries or lists in snapshots."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class MandateResource(MandateContract):
    """Exact resource reference; no implicit all-records or wildcard grant."""

    kind: Literal["workspace", "thread", "app", "track", "entry", "attachment"]
    resource_id: CanonicalKey


class MandateCapabilityGrant(MandateContract):
    """Reviewed capability version and exact target scope."""

    capability_key: CanonicalKey
    capability_version: CanonicalKey
    app_id: CanonicalKey | None = None
    definition_id: CanonicalKey | None = None
    operation: Literal["read", "internal_write", "external_effect"]
    resources: tuple[MandateResource, ...] = Field(min_length=1, max_length=100)
    max_calls: int = Field(ge=1, le=10000)

    @model_validator(mode="after")
    def _unique_resources(self) -> "MandateCapabilityGrant":
        if bool(self.app_id) != bool(self.definition_id):
            raise ValueError("App grants must bind both App and definition")
        if len(set(self.resources)) != len(self.resources):
            raise ValueError("resource references must be unique")
        return self


class MandateExternalGrant(MandateContract):
    """Separate exact destination authorization for an external effect."""

    capability_key: CanonicalKey
    provider: CanonicalKey
    credential_ref: CanonicalKey
    operation: CanonicalKey
    destination: CanonicalKey
    max_effects: int = Field(ge=1, le=10000)


class MandateModelRoute(MandateContract):
    """Provider attribution, never credential material or host instructions."""

    provider: CanonicalKey
    model: CanonicalKey
    credential_source: Literal["workspace_byok", "platform", "local"]
    credential_ref: CanonicalKey


class MandateLimits(MandateContract):
    """Finite shared limits; strict spend admission requires known upper bounds."""

    deadline_at: datetime
    max_model_requests: int = Field(ge=1, le=10000)
    max_tool_calls: int = Field(ge=1, le=10000)
    max_internal_writes: int = Field(ge=0, le=10000)
    max_external_effects: int = Field(ge=0, le=10000)
    currency: Literal["USD"] = "USD"
    max_spend: Decimal = Field(
        ge=0, allow_inf_nan=False, max_digits=18, decimal_places=8
    )
    cost_policy: Literal["require_upper_bound"] = "require_upper_bound"

    @model_validator(mode="after")
    def _aware_deadline(self) -> "MandateLimits":
        if self.deadline_at.utcoffset() is None:
            raise ValueError("deadline must include a timezone")
        return self


class WorkMandateRevision(MandateContract):
    """Complete reviewed intent for one revision of a generic work mandate.

    A digest binds the human review to these values; it does not prove approval.
    Callers must not accept principal, scope or approval authority from a model.
    """

    schema_version: Literal[1] = 1
    mandate_id: CanonicalKey
    revision: int = Field(ge=1)
    principal_id: CanonicalKey
    workspace_id: CanonicalKey
    thread_id: CanonicalKey
    goal: ReviewText
    inputs: tuple[MandateResource, ...] = Field(max_length=100)
    grants: tuple[MandateCapabilityGrant, ...] = Field(min_length=1, max_length=100)
    external_grants: tuple[MandateExternalGrant, ...] = Field(
        default=(), max_length=100
    )
    model_routes: tuple[MandateModelRoute, ...] = Field(min_length=1, max_length=10)
    limits: MandateLimits
    success_criteria: tuple[ReviewText, ...] = Field(min_length=1, max_length=20)
    stop_conditions: tuple[ReviewText, ...] = Field(min_length=1, max_length=20)
    result_obligations: tuple[ReviewText, ...] = Field(min_length=1, max_length=20)
    unknown_outcome_policy: Literal["reconcile_before_retry"] = "reconcile_before_retry"
    max_attempts: int = Field(default=1, ge=1, le=10)

    @model_validator(mode="after")
    def _coherent_scope(self) -> "WorkMandateRevision":
        for values in (
            self.inputs,
            self.grants,
            self.external_grants,
            self.model_routes,
        ):
            if len(set(values)) != len(values):
                raise ValueError("mandate scope contains duplicate values")
        external_keys = {
            grant.capability_key
            for grant in self.grants
            if grant.operation == "external_effect"
        }
        if external_keys != {grant.capability_key for grant in self.external_grants}:
            raise ValueError(
                "external capabilities require separate destination grants"
            )
        if bool(external_keys) != bool(self.limits.max_external_effects):
            raise ValueError("external limits must agree with external grants")
        if any(g.operation == "internal_write" for g in self.grants) and not (
            self.limits.max_internal_writes
        ):
            raise ValueError("write grants require a nonzero internal write limit")
        # One reviewed key must identify one version, definition and operation.
        keys = tuple(g.capability_key for g in self.grants)
        if len(set(keys)) != len(keys):
            raise ValueError("capability keys must be unique in a revision")
        return self

    def canonical_review_payload(self) -> dict:
        """Detached JSON values for storage and digest-equivalent retries."""
        payload = self.model_dump(mode="json")
        # Normalize equivalent monetary/time representations before hashing.
        spend = self.limits.max_spend
        monetary_text = format(spend, "f")
        if "." in monetary_text:
            monetary_text = monetary_text.rstrip("0").rstrip(".")
        payload["limits"]["max_spend"] = "0" if spend == 0 else monetary_text
        payload["limits"]["deadline_at"] = self.limits.deadline_at.astimezone(
            timezone.utc
        ).isoformat()
        return payload

    def review_digest(self) -> str:
        """Stable SHA-256 of canonical values, independent of JSON key order."""
        encoded = json.dumps(
            self.canonical_review_payload(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
