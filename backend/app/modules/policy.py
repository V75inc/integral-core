"""Identity/policy module adapter over the current authorization engine."""

from __future__ import annotations

from app.contracts.runtime import ExecutionScope
from app.schemas.policy import Decision, Resource, Subject
from app.services.policy_engine import evaluate


class PolicyModule:
    """Authorize module requests through Integral's existing policy semantics."""

    async def evaluate(
        self, *, scope: ExecutionScope, action: str, resource: Resource
    ) -> Decision:
        """Return the policy decision for a validated execution scope."""
        return await evaluate(
            subject=Subject(kind="human", id=scope.principal_id),
            action=action,
            resource=resource,
        )


policy_module = PolicyModule()

__all__ = ["PolicyModule", "policy_module"]
