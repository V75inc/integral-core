"""Stable identity and request fingerprint contracts for operations.

The operation identity is deliberately independent of agent sessions, HTTP
transport and persistence implementation. It is the namespace for one
principal's one logical operation against an installed App.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


@dataclass(frozen=True)
class OperationIdentity:
    """The immutable idempotency namespace for a typed App operation."""

    workspace_id: str
    app_id: str
    operation_key: str
    principal_id: str
    idempotency_key: str

    @classmethod
    def create(
        cls,
        *,
        workspace_id: str,
        app_id: str,
        operation_key: str,
        principal_id: str,
        idempotency_key: str,
    ) -> "OperationIdentity":
        values = {
            "workspace_id": workspace_id,
            "app_id": app_id,
            "operation_key": operation_key,
            "principal_id": principal_id,
            "idempotency_key": idempotency_key,
        }
        normalized = {key: str(value or "").strip() for key, value in values.items()}
        missing = [key for key, value in normalized.items() if not value]
        if missing:
            raise ValueError(
                "operation identity requires " + ", ".join(sorted(missing))
            )
        return cls(**normalized)

    def cache_key(self) -> Tuple[str, str, str, str, str]:
        """Stable key usable by a persistence adapter or test cache."""
        return (
            self.workspace_id,
            self.app_id,
            self.operation_key,
            self.principal_id,
            self.idempotency_key,
        )


def canonical_request_hash(payload: Optional[Dict[str, Any]]) -> str:
    """Return a stable request fingerprint for an operation receipt."""
    canonical = json.dumps(
        dict(payload or {}), sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
