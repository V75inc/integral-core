"""Native thread-ID artifact persistence under the broker's enclosing transaction."""

from contextlib import asynccontextmanager
from copy import deepcopy

import pytest

from app.agentive.artifacts import get_artifact, upsert_artifact
from app.agentive.tooling.dispatch import dispatch_tool
from app.models.nodes import ChatThread
from app.services import release_admission, workspace_permissions
from app.services.app_operations.transaction_scope import postgres_graph_transaction
from app.services.chat_threads import append_message
from tests.test_propose_design_dispatch import _BLUEPRINT, _PROPOSAL

pytestmark = [pytest.mark.asyncio, pytest.mark.postgres]


@pytest.fixture
async def native_thread(postgres_raw_db, monkeypatch):
    # Isolate persistence from identity setup, retaining real PostgreSQL
    # transactions and the artifact reader's thread/owner/workspace checks.
    @asynccontextmanager
    async def admitted(*args, **kwargs):
        async with postgres_graph_transaction():
            yield

    async def access(*args, **kwargs):
        return "owner"

    monkeypatch.setattr(release_admission, "release_admission", admitted)
    monkeypatch.setattr(workspace_permissions, "can_access_workspace", access)
    thread = await ChatThread.create(
        user_id="design-owner",
        workspace_id="design-workspace",
        provider_id="integral_native",
    )
    await append_message(
        thread=thread,
        role="user",
        parts=[{"type": "text", "text": "Build a contacts app"}],
    )
    return thread


async def test_native_design_artifact_survives_marker_save_and_next_transaction(
    postgres_raw_db,
    native_thread,
):
    thread = native_thread
    for revision in (1, 2):
        blueprint = deepcopy(_BLUEPRINT)
        blueprint["app"]["description"] = f"Revision {revision}"
        async with postgres_graph_transaction():
            await upsert_artifact(
                user_id=thread.user_id,
                session_id=thread.id,
                workspace_id=thread.workspace_id,
                key="notes",
                kind="note",
                body="Keep me",
            )
            result = await dispatch_tool(
                "integral_propose_design",
                {
                    "summary": "Demo contacts",
                    "proposal": _PROPOSAL,
                    "blueprint": blueprint,
                },
                principal_id=thread.user_id,
                scope=thread.workspace_id,
                session_id=thread.id,
            )
            assert not result.is_error, result
            # A model-free reproduction of the native broker, not the legacy
            # provider-session lookup (which bypasses the identity map).
            current = await ChatThread.get(thread.id)
            assert current.artifacts["app_design_blueprint"]["version"] == revision
            assert current.artifacts["notes"]["body"] == "Keep me"
        async with postgres_graph_transaction():
            artifact = await get_artifact(
                user_id=thread.user_id,
                session_id=thread.id,
                workspace_id=thread.workspace_id,
                key="app_design_blueprint",
            )
            assert artifact["ok"]
            assert artifact["version"] == revision
            assert artifact["blueprint_revision"] == revision
            assert artifact["blueprint"]["app"]["description"] == f"Revision {revision}"
        row = await postgres_raw_db.find_one("node", {"id": thread.id})
        assert (
            row["context"]["artifacts"]["app_design_blueprint"]["version"] == revision
        )
        assert row["context"]["design_proposed"]["approved"] is False


async def test_artifact_cache_does_not_escape_transaction_rollback(
    postgres_raw_db,
    native_thread,
):
    thread = native_thread
    with pytest.raises(RuntimeError, match="rollback probe"):
        async with postgres_graph_transaction():
            await upsert_artifact(
                user_id=thread.user_id,
                session_id=thread.id,
                workspace_id=thread.workspace_id,
                key="notes",
                kind="note",
                body="Not committed",
            )
            assert (await ChatThread.get(thread.id)).artifacts["notes"]
            raise RuntimeError("rollback probe")
    assert not (await ChatThread.get(thread.id)).artifacts
    row = await postgres_raw_db.find_one("node", {"id": thread.id})
    assert not row["context"].get("artifacts")


@pytest.mark.parametrize("denial", ["owner", "workspace", "archived", "membership"])
async def test_recovery_keeps_native_authority_boundaries(
    native_thread, monkeypatch, denial
):
    from app.services.design_blueprint import blueprint_digest

    thread = native_thread
    thread.design_proposed = {
        "artifact_key": "app_design_blueprint",
        "design_id": "saved-design",
        "blueprint": _BLUEPRINT,
        "blueprint_revision": 1,
        "blueprint_digest": blueprint_digest(_BLUEPRINT),
    }
    thread.archived = denial == "archived"
    await thread.save()
    if denial == "membership":

        async def denied(*args, **kwargs):
            return "none"

        monkeypatch.setattr(workspace_permissions, "can_access_workspace", denied)
    result = await get_artifact(
        user_id="other" if denial == "owner" else thread.user_id,
        workspace_id="other" if denial == "workspace" else thread.workspace_id,
        session_id=thread.id,
        key="app_design_blueprint",
    )
    assert result["error"] == "not_found"
    assert "blueprint" not in result


async def test_native_recovery_reads_surviving_design_without_writing(
    postgres_raw_db, native_thread
):
    from app.services.design_blueprint import blueprint_digest

    thread = native_thread
    marker = {
        "artifact_key": "app_design_blueprint",
        "design_id": "saved-design",
        "blueprint": deepcopy(_BLUEPRINT),
        "blueprint_revision": 2,
        "blueprint_digest": blueprint_digest(_BLUEPRINT),
        "approved": False,
        "summary": "Demo",
        "proposal": _PROPOSAL,
    }
    thread.design_proposed = deepcopy(marker)
    await thread.save()
    async with postgres_graph_transaction():
        result = await get_artifact(
            user_id=thread.user_id,
            workspace_id=thread.workspace_id,
            session_id=thread.id,
            key="app_design_blueprint",
        )
        assert result["ok"]
        assert result["blueprint"] == _BLUEPRINT
        assert result["blueprint_revision"] == 2
        assert result["version"] == 0
    row = await postgres_raw_db.find_one("node", {"id": thread.id})
    assert row["context"]["artifacts"] is None
    assert row["context"]["design_proposed"] == marker
