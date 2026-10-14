"""Post-commit registry cleanup protects restored/reinstalled App lineage."""

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async

from app.models.nodes import App
from app.services import bundle_cleanup_outbox as cleanup
from tests.contract.test_staging_command_apply_postgres import proposal

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def clean_replica_cache():
    cleanup._seen.clear()
    cleanup._cursor = ""
    yield
    cleanup._seen.clear()
    cleanup._cursor = ""


async def test_live_restore_and_reinstall_never_unregistered(
    postgres_raw_db, monkeypatch
):
    called = []
    monkeypatch.setattr(
        "app.services.hooks.registry.unregister_bundle_registrations",
        lambda *args: called.append(args),
    )
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        _, workspace, _, _ = await proposal()
        app = (await App.find({"workspace_id": workspace.id}))[0]
        await cleanup.retain_bundle_cleanup(
            app_id=app.id, workspace_id=workspace.id, bundle_slug="fixture"
        )
        assert await cleanup.deliver_bundle_cleanup(database=postgres_raw_db) == 0
        assert called == []
        # Simulate another active install replacing an absent prior identity.
        await cleanup.retain_bundle_cleanup(
            app_id="n.App.previous", workspace_id=workspace.id, bundle_slug="fixture"
        )

        async def slug(candidate):
            return "fixture"

        monkeypatch.setattr(cleanup, "_live_bundle_slug", slug)
        assert await cleanup.deliver_bundle_cleanup(database=postgres_raw_db) == 0
        assert called == []


async def test_each_replica_replays_delivered_cleanup_and_failure_retries(
    postgres_raw_db, monkeypatch
):
    called = []

    def remove(*args):
        called.append(args)

    monkeypatch.setattr(
        "app.services.hooks.registry.unregister_bundle_registrations", remove
    )
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        _, workspace, _, _ = await proposal()
        await cleanup.retain_bundle_cleanup(
            app_id="n.App.gone", workspace_id=workspace.id, bundle_slug="gone"
        )
        assert await cleanup.deliver_bundle_cleanup(database=postgres_raw_db) == 1
        assert len(called) == 1
        assert await cleanup.deliver_bundle_cleanup(database=postgres_raw_db) == 0
        cleanup._seen.clear()  # Model the other process's independent RAM.
        assert await cleanup.deliver_bundle_cleanup(database=postgres_raw_db) == 1
        assert len(called) == 2
        cleanup._seen.clear()

        def fail(*args):
            raise OSError("Registry cleanup failure")

        monkeypatch.setattr(
            "app.services.hooks.registry.unregister_bundle_registrations", fail
        )
        assert await cleanup.deliver_bundle_cleanup(database=postgres_raw_db) == 0
        assert cleanup._seen == set()
        monkeypatch.setattr(
            "app.services.hooks.registry.unregister_bundle_registrations", remove
        )
        assert await cleanup.deliver_bundle_cleanup(database=postgres_raw_db) == 1


async def test_current_install_lookup_failure_does_not_authorize_cleanup(
    postgres_raw_db, monkeypatch
):
    called = []
    monkeypatch.setattr(
        "app.services.hooks.registry.unregister_bundle_registrations",
        lambda *args: called.append(args),
    )
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        _, workspace, _, _ = await proposal()
        await cleanup.retain_bundle_cleanup(
            app_id="n.App.gone", workspace_id=workspace.id, bundle_slug="fixture"
        )

        async def unavailable(candidate):
            raise OSError("Current identity lookup unavailable")

        monkeypatch.setattr(cleanup, "_live_bundle_slug", unavailable)
        assert await cleanup.deliver_bundle_cleanup(database=postgres_raw_db) == 0
        assert called == []


async def test_actual_registry_removes_only_the_deleted_bundle(postgres_raw_db):
    from app.services.hooks.registry import (
        clear_workspace_registrations,
        get_workspace_tools,
        register_workspace_tools,
    )

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        _, workspace, _, _ = await proposal()
        try:
            register_workspace_tools(workspace.id, "gone", [{"key": "gone_tool"}])
            register_workspace_tools(workspace.id, "sibling", [{"key": "sibling_tool"}])
            await cleanup.retain_bundle_cleanup(
                app_id="n.App.gone", workspace_id=workspace.id, bundle_slug="gone"
            )
            assert await cleanup.deliver_bundle_cleanup(database=postgres_raw_db) == 1
            assert set(get_workspace_tools(workspace.id)) == {"sibling_tool"}
            # Another process can still hold the retired registration even
            # though the first process acknowledged the shared fact.
            cleanup._seen.clear()
            register_workspace_tools(workspace.id, "gone", [{"key": "gone_tool"}])
            assert await cleanup.deliver_bundle_cleanup(database=postgres_raw_db) == 1
            assert set(get_workspace_tools(workspace.id)) == {"sibling_tool"}
        finally:
            clear_workspace_registrations(workspace.id)


async def test_new_uninstall_generation_is_not_suppressed_by_old_ack(postgres_raw_db):
    from app.services.hooks.registry import (
        clear_workspace_registrations,
        get_workspace_tools,
        register_workspace_tools,
    )

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        _, workspace, _, _ = await proposal()
        try:
            for _ in range(2):
                register_workspace_tools(workspace.id, "gone", [{"key": "gone_tool"}])
                await cleanup.retain_bundle_cleanup(
                    app_id="n.App.same", workspace_id=workspace.id, bundle_slug="gone"
                )
                assert (
                    await cleanup.deliver_bundle_cleanup(database=postgres_raw_db) == 1
                )
                assert "gone_tool" not in get_workspace_tools(workspace.id)
            rows = await postgres_raw_db.find(
                "object",
                {"entity": "BundleCleanupFact", "context.app_id": "n.App.same"},
            )
            assert len(rows) == 2
            assert rows[0]["context"]["generation"] != rows[1]["context"]["generation"]
        finally:
            clear_workspace_registrations(workspace.id)
