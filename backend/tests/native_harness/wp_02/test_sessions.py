"""Transactional HarnessSession lifecycle adapter tests."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from app.agentive.harness.contracts import HarnessExecutionScope
from app.api.errors import InsufficientPermissionsError
from app.models.edges import HAS_HARNESS_SESSION
from app.services.app_operations.transaction_scope import (
    OperationTransactionUnavailable,
)
from app.services.harness_sessions import (
    ensure_harness_session,
    transition_harness_session,
)


def _scope() -> HarnessExecutionScope:
    """Build one trusted execution scope for lifecycle tests."""
    return HarnessExecutionScope(
        tenant_id="workspace-1",
        principal_id="user-1",
        workspace_id="workspace-1",
        thread_id="n.ChatThread.thread-1",
        session_id="session-1",
        run_id="run-1",
        permission_revision="permission-r1",
        capability_version="capabilities-r1",
    )


@pytest.mark.asyncio
async def test_session_activation_fails_closed_without_shared_cas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Single-process stores cannot activate durable Harness sessions."""
    import app.services.harness_sessions as sessions

    monkeypatch.setattr(sessions, "graph_transaction_available", lambda: False)
    with pytest.raises(OperationTransactionUnavailable):
        await ensure_harness_session(scope=_scope(), binding_id="integral_native")


@pytest.mark.asyncio
async def test_session_activation_rechecks_thread_principal_and_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Client scope values cannot widen access to another user's thread."""
    import app.services.harness_sessions as sessions

    class FakeTransaction:
        """Capture CAS attempts for an isolated lifecycle unit test."""

        async def find_one_and_update(
            self, *args: Any, **kwargs: Any
        ) -> dict[str, str]:
            return {"id": "unused"}

    @asynccontextmanager
    async def transaction():
        yield FakeTransaction()

    thread = SimpleNamespace(
        id="n.ChatThread.thread-1",
        user_id="different-user",
        workspace_id="workspace-1",
        active_harness_session_id=None,
        harness_session_generation=0,
    )

    async def get_thread(cls, thread_id):
        return thread

    monkeypatch.setattr(sessions, "graph_transaction_available", lambda: True)
    monkeypatch.setattr(sessions, "postgres_graph_transaction", transaction)
    monkeypatch.setattr(sessions.ChatThread, "get", classmethod(get_thread))

    with pytest.raises(InsufficientPermissionsError):
        await ensure_harness_session(scope=_scope(), binding_id="integral_native")


@pytest.mark.asyncio
async def test_session_activation_creates_root_edge_within_cas_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The graph session and its typed thread edge share the transaction."""
    import app.services.harness_sessions as sessions

    calls = []

    class FakeTransaction:
        """Return a successful generation CAS and record its arguments."""

        async def find_one_and_update(
            self, *args: Any, **kwargs: Any
        ) -> dict[str, str]:
            calls.append(("cas", args, kwargs))
            return {"id": "n.ChatThread.thread-1"}

    @asynccontextmanager
    async def transaction():
        calls.append(("begin",))
        yield FakeTransaction()
        calls.append(("commit",))

    class FakeThread:
        """Minimal ChatThread adapter to observe graph attachment."""

        id = "n.ChatThread.thread-1"
        user_id = "user-1"
        workspace_id = "workspace-1"
        updated_at = "2026-10-01T00:00:00+00:00"
        active_harness_session_id = None
        harness_session_generation = 0

        async def get_context(self):
            return object()

        async def connect(self, node, *, edge, **metadata):
            calls.append(("edge", node, edge, metadata))

    thread = FakeThread()

    async def get_thread(cls, thread_id):
        return thread

    async def no_active_sessions(cls, query=None):
        return []

    async def no_existing_session(cls, session_id):
        return None

    async def persist_session(self):
        calls.append(("node", self))

    monkeypatch.setattr(sessions, "graph_transaction_available", lambda: True)
    monkeypatch.setattr(sessions, "postgres_graph_transaction", transaction)
    monkeypatch.setattr(sessions.ChatThread, "get", classmethod(get_thread))
    monkeypatch.setattr(
        sessions.HarnessSession, "find", classmethod(no_active_sessions)
    )
    monkeypatch.setattr(
        sessions.HarnessSession,
        "find_one",
        classmethod(lambda cls, query: no_existing_session(cls, "")),
    )
    monkeypatch.setattr(
        sessions.HarnessSession, "get", classmethod(no_existing_session)
    )
    monkeypatch.setattr(sessions.HarnessSession, "save", persist_session)

    session = await ensure_harness_session(scope=_scope(), binding_id="integral_native")

    assert session.status == "active"
    assert session.binding_generation == 1
    assert [item[0] for item in calls] == ["begin", "cas", "node", "edge", "commit"]
    edge_call = calls[3]
    assert edge_call[2] is HAS_HARNESS_SESSION
    assert edge_call[3]["generation"] == 1
    assert calls[1][1][1]["context.updated_at"] == thread.updated_at
    assert calls[1][1][2]["$set"]["context.updated_at"] != thread.updated_at


@pytest.mark.asyncio
async def test_session_close_clears_pointer_and_retains_graph_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Closing a session atomically removes the active pointer, not history."""
    import app.services.harness_sessions as sessions

    calls = []

    class FakeTransaction:
        """Capture the thread-pointer compare-and-set."""

        async def find_one_and_update(
            self, *args: Any, **kwargs: Any
        ) -> dict[str, str]:
            calls.append(("cas", args, kwargs))
            return {"id": "n.ChatThread.thread-1"}

    @asynccontextmanager
    async def transaction():
        calls.append(("begin",))
        yield FakeTransaction()
        calls.append(("commit",))

    class FakeContext:
        """Confirm the session remains linked from its ChatThread."""

        async def find_edges_between(self, source, target, *, edge_class):
            assert source == "n.ChatThread.thread-1"
            assert target == "n.HarnessSession.session-node-1"
            assert edge_class is HAS_HARNESS_SESSION
            return [object()]

    class FakeThread:
        """The session owner and workspace stay authoritative."""

        id = "n.ChatThread.thread-1"
        user_id = "user-1"
        workspace_id = "workspace-1"
        updated_at = "2026-10-01T00:00:00+00:00"
        active_harness_session_id = "n.HarnessSession.session-node-1"

        async def get_context(self):
            return FakeContext()

    class FakeSession:
        """Observe lifecycle status while retaining the rooted node."""

        id = "n.HarnessSession.session-node-1"
        thread_id = "n.ChatThread.thread-1"
        principal_id = "user-1"
        workspace_id = "workspace-1"
        status = "active"

        async def save(self):
            calls.append(("save", self.status))

    thread = FakeThread()
    session = FakeSession()

    async def get_thread(cls, thread_id):
        return thread

    async def find_session(cls, query):
        return [session]

    monkeypatch.setattr(sessions, "graph_transaction_available", lambda: True)
    monkeypatch.setattr(sessions, "postgres_graph_transaction", transaction)
    monkeypatch.setattr(sessions.ChatThread, "get", classmethod(get_thread))
    monkeypatch.setattr(sessions.HarnessSession, "find", classmethod(find_session))

    result = await transition_harness_session(scope=_scope(), status="closed")

    assert result is session
    assert session.status == "closed"
    assert calls[1][1][2]["$set"]["context.active_harness_session_id"] is None
    assert calls[2] == ("save", "closed")
    assert calls[-1] == ("commit",)
