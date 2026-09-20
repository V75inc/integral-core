"""Transport-independent runtime contracts shared by Core modules.

These types deliberately carry only validated operational identity.  HTTP,
MCP, and the resident harness may build a scope differently, but module
implementations receive the same immutable principal/workspace binding.
"""

from __future__ import annotations

from dataclasses import dataclass


class InvalidExecutionScope(ValueError):
    """Raised when an effect path is missing a principal or workspace."""


@dataclass(frozen=True)
class ExecutionScope:
    """Immutable principal and workspace binding for one Core request."""

    principal_id: str
    workspace_id: str
    origin: str = "internal"

    @classmethod
    def create(
        cls, *, principal_id: str, workspace_id: str, origin: str = "internal"
    ) -> "ExecutionScope":
        """Normalize a scope and reject incomplete effect-path identity."""
        normalized = {
            "principal_id": str(principal_id or "").strip(),
            "workspace_id": str(workspace_id or "").strip(),
            "origin": str(origin or "internal").strip() or "internal",
        }
        missing = [
            key for key in ("principal_id", "workspace_id") if not normalized[key]
        ]
        if missing:
            raise InvalidExecutionScope(
                "execution scope requires " + ", ".join(missing)
            )
        return cls(**normalized)

    @property
    def workspace_scope(self) -> str:
        """Return the canonical policy scope form for this workspace."""
        return f"workspace:{self.workspace_id}"


__all__ = ["ExecutionScope", "InvalidExecutionScope"]
