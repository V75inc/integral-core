"""Internal worker authority cannot leak across task or scope boundaries."""

import asyncio

import pytest

from app.agentive.services.work_execution_binding import (
    bind_work_execution,
    current_work_execution,
)
from app.schemas.agentive.work import WorkError, WorkExecutionContext

pytestmark = [pytest.mark.smoke, pytest.mark.asyncio]


def context(**updates):
    return WorkExecutionContext(
        **{
            "work_item_id": "synthetic-work",
            "attempt": 1,
            "run_id": "synthetic-run",
            "principal_id": "synthetic-principal",
            "workspace_id": "synthetic-workspace",
            "thread_id": "synthetic-thread",
            "logical_step_key": "provider:0",
            "effect_key": "synthetic-effect",
            "lease_token": "synthetic-token",
            "lease_fence": 1,
            **updates,
        }
    )


async def test_binding_is_empty_without_trusted_worker():
    assert current_work_execution() is None


async def test_binding_restores_after_failure_and_same_context_nesting():
    ctx = context()
    with pytest.raises(RuntimeError):
        with bind_work_execution(ctx):
            assert current_work_execution() is ctx
            with bind_work_execution(ctx):
                assert current_work_execution() is ctx
            raise RuntimeError("Synthetic turn interruption")
    assert current_work_execution() is None


async def test_conflicting_nested_authority_is_refused():
    ctx = context()
    with bind_work_execution(ctx):
        with pytest.raises(WorkError) as refused:
            with bind_work_execution(context(thread_id="another-thread")):
                pytest.fail("Nested scope changed")
        assert refused.value.code == "work.lease_lost"
        assert current_work_execution() is ctx


async def test_inherited_binding_cannot_authorize_another_task():
    async def child():
        with pytest.raises(WorkError) as refused:
            current_work_execution()
        assert refused.value.code == "work.lease_lost"

    ctx = context()
    with bind_work_execution(ctx):
        await asyncio.create_task(child())
        assert current_work_execution() is ctx
    assert current_work_execution() is None


async def test_unvalidated_mapping_is_not_worker_authority():
    with pytest.raises(TypeError):
        with bind_work_execution(context().model_dump()):
            pytest.fail("Mapping granted worker authority")
