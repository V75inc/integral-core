"""What the server admits, and what it refuses.

`user_id` has been on the in-flight handle since the registry was written, and
nothing ever read it. The only ceiling on simultaneous turns was the client's
own cap of five — so a scripted or misbehaving client could open turns without
bound, each holding an LLM stream.

The thread cap (I-CHAT-PAR-01) and the user cap are both 409s and mean
different things. Conflating them tells someone to stop a conversation that is
not the one in their way.
"""

import pytest

from app.api.errors import ResourceConflictError
from app.config import settings
from app.services import chat_turn_registry
from app.services.chat_turn_registry import acquire_turn, release_turn


@pytest.fixture(autouse=True)
async def _clean_registry():
    await chat_turn_registry.reset_registry_for_tests()
    yield
    await chat_turn_registry.reset_registry_for_tests()


@pytest.mark.asyncio
async def test_one_turn_per_thread_still_holds():
    await acquire_turn(thread_id="t1", user_id="u1")

    with pytest.raises(ResourceConflictError) as exc:
        await acquire_turn(thread_id="t1", user_id="u1")

    assert exc.value.details["reason"] == "thread_busy"


@pytest.mark.asyncio
async def test_a_user_can_run_several_conversations_at_once():
    # The cap exists to stop unbounded turns, not to make the product
    # single-threaded — concurrency across threads is the whole point of the
    # work that preceded this.
    for i in range(5):
        await acquire_turn(thread_id=f"t{i}", user_id="u1")


@pytest.mark.asyncio
async def test_but_not_without_limit(monkeypatch):
    monkeypatch.setattr(
        settings,
        "MAX_CONCURRENT_TURNS_PER_USER",
        2,
        raising=False,
    )
    await acquire_turn(thread_id="t1", user_id="u1")
    await acquire_turn(thread_id="t2", user_id="u1")

    with pytest.raises(ResourceConflictError) as exc:
        await acquire_turn(thread_id="t3", user_id="u1")

    assert exc.value.details["reason"] == "user_turn_limit"
    assert exc.value.details["limit"] == 2
    assert exc.value.details["active"] == 2


@pytest.mark.asyncio
async def test_the_limit_is_per_user_not_global(monkeypatch):
    # Otherwise one busy user throttles everyone else on the deployment.
    monkeypatch.setattr(
        settings,
        "MAX_CONCURRENT_TURNS_PER_USER",
        1,
        raising=False,
    )
    await acquire_turn(thread_id="t1", user_id="u1")

    await acquire_turn(thread_id="t2", user_id="u2")  # must not raise


@pytest.mark.asyncio
async def test_finishing_a_turn_frees_a_slot(monkeypatch):
    monkeypatch.setattr(
        settings,
        "MAX_CONCURRENT_TURNS_PER_USER",
        1,
        raising=False,
    )
    await acquire_turn(thread_id="t1", user_id="u1")
    with pytest.raises(ResourceConflictError):
        await acquire_turn(thread_id="t2", user_id="u1")

    await release_turn("t1")

    await acquire_turn(thread_id="t2", user_id="u1")  # must not raise
