"""Task-bound decision admission for a canonical operation's own receipt commit.

This is an internal producer hook, not an approval grant or a second journal.
The producer validates its current decision; normal operation authority remains
mandatory. No client payload or public SDK can install this binding.
"""

import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import AsyncContextManager, AsyncIterator, Callable

from app.contracts.operations import OperationIdentity
from app.exceptions import AuthorizationUnavailableError
from app.schemas.policy import Subject


@dataclass(frozen=True)
class OperationDecisionBinding:
    principal_id: str
    workspace_id: str
    app_id: str
    operation_key: str
    idempotency_key: str
    request_hash: str
    contract_fingerprint: str
    intent_fingerprint: str
    admit: Callable[[OperationIdentity, tuple[str, ...]], AsyncContextManager[None]]
    task: object


_active: ContextVar[OperationDecisionBinding | None] = ContextVar(
    "integral_operation_decision", default=None
)


@asynccontextmanager
async def bind_operation_decision(
    *,
    principal_id: str,
    workspace_id: str,
    app_id: str,
    operation_key: str,
    idempotency_key: str,
    request_hash: str,
    contract_fingerprint: str,
    intent_fingerprint: str,
    admit: Callable[[OperationIdentity, tuple[str, ...]], AsyncContextManager[None]],
) -> AsyncIterator[None]:
    """Bind one server-owned exact decision to its original dispatcher task."""
    if _active.get() is not None:
        raise AuthorizationUnavailableError()
    binding = OperationDecisionBinding(
        principal_id,
        workspace_id,
        app_id,
        operation_key,
        idempotency_key,
        request_hash,
        contract_fingerprint,
        intent_fingerprint,
        admit,
        asyncio.current_task(),
    )
    reset = _active.set(binding)
    try:
        yield
    finally:
        _active.reset(reset)


@asynccontextmanager
async def operation_decision_admission(
    *,
    identity: OperationIdentity,
    subject: Subject,
    request_hash: str,
    contract_fingerprint: str,
    principal_aliases: tuple[str, ...],
) -> AsyncIterator[None]:
    """Join the producer's current decision checks without replacing authority."""
    binding = _active.get()
    if binding is None:
        yield
        return
    if (
        binding.task is not asyncio.current_task()
        or subject.kind != "human"
        or subject.id != binding.principal_id
        or identity.workspace_id != binding.workspace_id
        or identity.app_id != binding.app_id
        or identity.operation_key != binding.operation_key
        or identity.idempotency_key != binding.idempotency_key
        or request_hash != binding.request_hash
        or contract_fingerprint != binding.contract_fingerprint
    ):
        raise AuthorizationUnavailableError()
    # Trusted nested operations retain their own ordinary admission; they must
    # not consume the outer proposal or inherit it as an execution permission.
    # This binding is one-use. Post-commit work and tasks spawned for event
    # delivery must not inherit an already consumed approval's metadata.
    _active.set(None)
    async with binding.admit(identity, principal_aliases):
        yield


def current_operation_decision_fingerprint() -> str | None:
    """Return private metadata for the original bound task, never a permission."""
    binding = _active.get()
    if binding is None:
        return None
    if binding.task is not asyncio.current_task():
        raise AuthorizationUnavailableError()
    return binding.intent_fingerprint
