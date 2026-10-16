"""Current function admission for an already prepared operation result.

This grants no execution permit. Mutating operations may consume their own
before-state conditions; disclosure checks current role/delegation and the
original definition instead of executing or approving the command again.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.api.errors import InsufficientPermissionsError
from app.schemas.policy import Subject

_CONTRACT_KEY = "_operation_contract"


def public_operation_result(result: dict[str, Any]) -> dict[str, Any]:
    """Keep the server's saved disclosure contract out of transport payloads."""
    return {key: value for key, value in result.items() if key != _CONTRACT_KEY}


@dataclass(frozen=True)
class OperationResultContext:
    subject: Subject
    issuer_id: str
    workspace_id: str
    app_id: str
    operation_key: str
    declaration_fingerprint: str
    payload: dict[str, Any]

    def bind(self, result: dict[str, Any]) -> dict[str, Any]:
        """Persist the original contract with the result, never in caller input."""
        return {**result, _CONTRACT_KEY: self.declaration_fingerprint}

    async def admit(self, result: dict[str, Any]) -> None:
        """Recheck inside result admission, without entering a handler permit."""
        if result.get(_CONTRACT_KEY) != self.declaration_fingerprint:
            # A missing legacy marker is not evidence of declaration identity.
            # Never stamp an old result or rerun a command to repair it.
            raise InsufficientPermissionsError(
                message="Current function authority required before result release"
            )
        await self.admit_current_function()

    async def admit_current_function(self) -> None:
        """Current read eligibility only; this does not prove an original result.

        Recovery may use this before decoding saved output. Final disclosure
        must still call ``admit`` with the original saved contract and admit all
        current output dependencies. No handler or execution permit is loaded.
        """
        from jvspatial.core.context import get_default_context

        from app.models.nodes import App
        from app.services.app_operations.dispatch import (
            _ensure_app_active,
            operation_declaration_fingerprint,
        )
        from app.services.app_operations.registry import get_app_operation
        from app.services.authority_facts import primary_node_record

        def denied() -> InsufficientPermissionsError:
            return InsufficientPermissionsError(
                message="Current function authority required before result release"
            )

        context = get_default_context()
        row = await primary_node_record(context.database, App, self.app_id)
        if row is None or row["context"].get("workspace_id") != self.workspace_id:
            raise denied()
        app = await context._deserialize_entity(App, row)
        spec = get_app_operation(self.workspace_id, self.app_id, self.operation_key)
        if (
            app is None
            or spec is None
            or (
                operation_declaration_fingerprint(spec, app)
                != self.declaration_fingerprint
            )
        ):
            raise denied()
        await _ensure_app_active(app, self.issuer_id)
        kind = str(spec.get("kind") or "execute").strip().lower()
        if spec.get("authorization"):
            from app.services.access_delegation import (
                action_role_delegable,
                current_definition,
            )
            from app.services.app_operation_authority import (
                operation_decision,
                operation_read_principal,
            )
            from app.services.permissions import get_user_node

            _, definition = await current_definition(self.app_id)
            if spec.get("_definition_fingerprint") != definition["context"].get(
                "manifest_fingerprint"
            ):
                raise denied()
            if kind == "read":
                decision, author = await operation_decision(
                    subject=self.subject,
                    app_id=self.app_id,
                    operation_key=self.operation_key,
                    payload=self.payload,
                )
                if not decision.allowed:
                    raise denied()
                actual, expected = await get_user_node(author), await get_user_node(
                    self.issuer_id
                )
                if actual is None or expected is None or actual.id != expected.id:
                    raise denied()
            elif self.subject.kind == "human":
                if not await action_role_delegable(
                    principal_id=self.subject.id,
                    app_id=self.app_id,
                    operation_key=self.operation_key,
                ):
                    raise denied()
            else:
                await operation_read_principal(
                    subject=self.subject,
                    workspace_id=self.workspace_id,
                    app_id=self.app_id,
                    operation_key=self.operation_key,
                    required_delegator_id=self.issuer_id,
                )
        else:
            from app.schemas.policy import Resource
            from app.services.policy_engine import evaluate

            if self.subject.kind != "human":
                raise denied()
            action = str(spec.get("policy_action") or "app.read").strip()
            if kind != "read" and action == "app.read":
                action = "entry.create"
            decision = await evaluate(
                subject=self.subject,
                action=action,
                resource=Resource(
                    kind="app", id=self.app_id, scope=f"app:{self.app_id}"
                ),
                _dry_run=True,
            )
            if not decision.allowed:
                raise denied()
