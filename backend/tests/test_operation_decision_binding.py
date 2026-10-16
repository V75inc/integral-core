"""A producer's private decision cannot be substituted or inherited by a task."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace

import pytest

from app.exceptions import AuthorizationUnavailableError
from app.schemas.policy import Subject
from app.services.app_operations.decision_binding import (
    bind_operation_decision,
    current_operation_decision_fingerprint,
    operation_decision_admission,
)
from tests.test_operation_outcome_observation import identity

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def binding(admit):
    target = identity()
    return dict(
        principal_id=target.principal_id,
        workspace_id=target.workspace_id,
        app_id=target.app_id,
        operation_key=target.operation_key,
        idempotency_key=target.idempotency_key,
        request_hash="input",
        contract_fingerprint="a" * 64,
        intent_fingerprint="b" * 64,
        admit=admit,
    )


def arguments():
    target = identity()
    return dict(
        identity=target,
        subject=Subject(kind="human", id=target.principal_id),
        request_hash="input",
        contract_fingerprint="a" * 64,
        principal_aliases=(),
    )


@pytest.mark.parametrize(
    "changed",
    ["subject", "kind", "workspace", "app", "operation", "key", "input", "contract"],
)
async def test_mismatched_operation_never_enters_decision_scope(changed):
    @asynccontextmanager
    async def forbidden(*args):
        pytest.fail("Another operation entered a reviewed decision")
        yield

    args = arguments()
    identity_fields = {
        "workspace": "workspace_id",
        "app": "app_id",
        "operation": "operation_key",
        "key": "idempotency_key",
    }
    if changed in identity_fields:
        args["identity"] = replace(
            args["identity"], **{identity_fields[changed]: "other"}
        )
    elif changed in {"subject", "kind"}:
        args["subject"] = Subject(
            kind="agent" if changed == "kind" else "human",
            id="other" if changed == "subject" else args["identity"].principal_id,
        )
    else:
        args["request_hash" if changed == "input" else "contract_fingerprint"] = (
            "different"
        )
    async with bind_operation_decision(**binding(forbidden)):
        with pytest.raises(AuthorizationUnavailableError):
            async with operation_decision_admission(**args):
                pytest.fail("Mismatched decision admitted")


async def test_inherited_decision_is_not_another_tasks_authority():
    @asynccontextmanager
    async def forbidden(*args):
        pytest.fail("Another task entered this decision")
        yield

    async def child():
        with pytest.raises(AuthorizationUnavailableError):
            current_operation_decision_fingerprint()
        with pytest.raises(AuthorizationUnavailableError):
            async with operation_decision_admission(**arguments()):
                pytest.fail("Inherited decision admitted")

    async with bind_operation_decision(**binding(forbidden)):
        await asyncio.create_task(child())


async def test_admitted_binding_is_not_retained_for_post_commit_children():
    calls = []

    @asynccontextmanager
    async def admitted(*args):
        calls.append("enter")
        yield
        calls.append("exit")

    async with bind_operation_decision(**binding(admitted)):
        assert current_operation_decision_fingerprint() == "b" * 64
        async with operation_decision_admission(**arguments()):
            assert current_operation_decision_fingerprint() is None
        assert current_operation_decision_fingerprint() is None
    assert current_operation_decision_fingerprint() is None
    assert calls == ["enter", "exit"]
