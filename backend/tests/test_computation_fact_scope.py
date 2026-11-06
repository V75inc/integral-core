"""computation_fact_scope: reuse facts inside ONE computation, never across requests.

The access inventory behind the tracks and apps lists asks the same questions many times
(where a track lives, a user's role on it, who the user is). The scope lets those answers be
reused for the duration of the computation only; it must not leak to the next request, must
not remember a miss, and must not replace an enclosing App job scope.
"""

import pytest

from app.middleware import permissions_cache as pc
from app.services import permissions


def test_facts_are_remembered_inside_the_scope_and_gone_after():
    key = ("location", "track", "t1", ("active",))
    with pc.computation_fact_scope():
        assert pc.app_job_fact_get(key) is None
        pc.app_job_fact_put(key, ("here",))
        assert pc.app_job_fact_get(key) == ("here",)
    assert not pc.app_job_fact_active()
    assert pc.app_job_fact_get(key) is None


def test_two_computations_do_not_share_facts():
    key = ("role", "u", "track", "t1")
    with pc.computation_fact_scope():
        pc.app_job_fact_put(key, ("editor",))
    with pc.computation_fact_scope():
        assert pc.app_job_fact_get(key) is None


def test_an_enclosing_job_scope_is_kept():
    key = ("role", "u", "track", "t1")
    with pc.app_job_fact_scope():
        pc.app_job_fact_put(key, ("owner",))
        with pc.computation_fact_scope():
            assert pc.app_job_fact_get(key) == ("owner",)
        assert pc.app_job_fact_get(key) == ("owner",)


def test_an_acl_invalidation_drops_remembered_facts():
    key = ("role", "u", "track", "t1")
    with pc.computation_fact_scope():
        pc.app_job_fact_put(key, ("editor",))
        pc.note_definition_changed()  # what invalidate_user() does on a grant or revoke
        assert pc.app_job_fact_get(key) is None


@pytest.mark.asyncio
async def test_get_user_node_is_remembered_only_inside_a_scope(monkeypatch):
    calls = {"n": 0}

    async def live(user_id):
        calls["n"] += 1
        return object()

    monkeypatch.setattr(permissions, "_get_user_node_live", live)
    await permissions.get_user_node("u")
    await permissions.get_user_node("u")
    assert calls["n"] == 2  # outside a scope every call reads fresh
    calls["n"] = 0
    with pc.computation_fact_scope():
        first = await permissions.get_user_node("u")
        second = await permissions.get_user_node("u")
    assert first is second and calls["n"] == 1


@pytest.mark.asyncio
async def test_get_user_node_is_read_fresh_inside_an_app_job(monkeypatch):
    """A loaded node is bound to the transaction it was read in. A job runs many transactions, so a
    node remembered across the job would later be read through one that has ended
    (``PostgreSQL transaction is no longer active`` in a pay run finalize)."""
    calls = {"n": 0}

    async def live(user_id):
        calls["n"] += 1
        return object()

    monkeypatch.setattr(permissions, "_get_user_node_live", live)
    with pc.app_job_fact_scope():
        first = await permissions.get_user_node("u")
        second = await permissions.get_user_node("u")
        assert first is not second and calls["n"] == 2
        # A computation that joins the job keeps the job's rule: still read fresh.
        with pc.computation_fact_scope():
            await permissions.get_user_node("u")
            await permissions.get_user_node("u")
    assert calls["n"] == 4


@pytest.mark.asyncio
async def test_a_missing_user_is_never_remembered(monkeypatch):
    calls = {"n": 0}

    async def live(user_id):
        calls["n"] += 1
        return None

    monkeypatch.setattr(permissions, "_get_user_node_live", live)
    with pc.computation_fact_scope():
        assert await permissions.get_user_node("ghost") is None
        assert await permissions.get_user_node("ghost") is None
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_app_query_runs_inside_a_fact_scope_that_ends_with_the_call(monkeypatch):
    """A declared App query shares facts for one call only."""
    from app.middleware import permissions_cache as pc
    from app.services.app_queries import dispatch

    seen = {}

    async def fake_inner(**kwargs):
        seen["during"] = pc._app_job_facts_var.get()
        return {"ok": True}

    monkeypatch.setattr(dispatch, "_invoke_app_query", fake_inner)
    assert pc._app_job_facts_var.get() is None
    result = await dispatch.invoke_app_query.__wrapped__(
        user_id="u", workspace_id="w", app_id="a", query_key="q"
    ) if hasattr(dispatch.invoke_app_query, "__wrapped__") else await dispatch.invoke_app_query(
        user_id="u", workspace_id="w", app_id="a", query_key="q"
    )
    assert result == {"ok": True}
    assert seen["during"] is not None
    assert pc._app_job_facts_var.get() is None
