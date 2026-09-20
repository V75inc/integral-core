"""Identity/policy module adapter over the current authorization engine."""

from __future__ import annotations

import hashlib
import json

from app.contracts.runtime import ExecutionScope
from app.schemas.policy import Decision, Resource, Subject
from app.services.policy_engine import evaluate


def decision_fingerprint(
    *,
    principal_id: str,
    workspace_id: str,
    action: str,
    resource: Resource,
    decision: Decision,
) -> str:
    """Encode the authorization state that governed one result."""
    encoded = json.dumps(
        {
            "action": action,
            "decision": {
                "allowed": decision.allowed,
                "approval_id": decision.approval_id,
                "matched_policy_id": decision.matched_policy_id,
                "policy_chain": decision.policy_chain,
                "reason": decision.reason,
            },
            "principal_id": principal_id,
            "resource": {
                "entry_type": resource.entry_type,
                "id": resource.id,
                "kind": resource.kind,
                "scope": resource.scope,
                "tags": sorted(resource.tags),
            },
            "workspace_id": workspace_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "policy-sha256:" + hashlib.sha256(encoded).hexdigest()


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

    def revision(
        self,
        *,
        scope: ExecutionScope,
        action: str,
        resource: Resource,
        decision: Decision,
    ) -> str:
        """Return the stable fingerprint of one evaluated authorization state.

        The legacy policy engine deliberately has no process-wide decision
        cache: it evaluates the current grants and denies at every request.
        This token makes that state explicit on result envelopes without
        creating a second cache or reimplementing its precedence rules. It is
        therefore an invalidation key only for this exact principal, workspace,
        action and resource; callers must authorize again at every effect
        boundary.
        """
        return decision_fingerprint(
            principal_id=scope.principal_id,
            workspace_id=scope.workspace_id,
            action=action,
            resource=resource,
            decision=decision,
        )


policy_module = PolicyModule()

__all__ = ["PolicyModule", "policy_module"]
