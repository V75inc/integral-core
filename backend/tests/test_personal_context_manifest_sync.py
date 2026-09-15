"""A bundle edit reaches an ALREADY-INSTALLED workspace, not just a fresh one.

``provision_personal_context_app`` returns an existing App directly, so it never
reaches ``install_app`` -- and ``install_app`` is where a reused install gets
re-materialized against the current manifest. The result was a silent asymmetry:
tracks healed against a newer bundle, skills and hook bindings did not.

Live cost: adding three batch tools to ``context_correct``'s SKILL.md, restarting,
and bumping the manifest version all left the persisted Skill node carrying its
original six-tool grant. The agent kept following an SOP it no longer had the
tools to satisfy, and nothing errored anywhere.

The sync is gated on version so the common path stays one lookup.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services import personal_context as pc


def _app(version: str):
    app = SimpleNamespace(
        id="n.WorkspaceApp.x",
        workspace_id="n.Workspace.w",
        owner_id="n.User.u",
        version=version,
    )
    app.save = AsyncMock()
    return app


@pytest.mark.asyncio
async def test_sync_runs_when_the_bundle_version_moved():
    """A newer library version re-syncs the operational layer and records it."""
    app = _app("0.1.0")
    library = SimpleNamespace(version="0.1.1", manifest={"app": {}})
    sync = AsyncMock(return_value={"skills_registered": 5})
    materialize = AsyncMock(return_value=[])
    with (
        patch.object(pc, "_resolve_library_package", AsyncMock(return_value=library)),
        patch("app.services.app_lifecycle.sync_operational_layer_from_manifest", sync),
        patch("app.services.app_lifecycle._materialize_tracks_for_app", materialize),
        patch(
            "app.services.content_profile_compile.compile_canonical_manifest",
            return_value={"app": {}},
        ),
    ):
        await pc._sync_operational_layer_if_manifest_moved(app)

    assert sync.await_count == 1
    # Tracks/EntryTypes drift too, and a stale field schema silently strips
    # every belief field off a write instead of erroring.
    assert materialize.await_count == 1
    # The App must carry the new version, or every later call re-syncs forever.
    assert app.version == "0.1.1"
    app.save.assert_awaited_once()


@pytest.mark.asyncio
async def test_sync_is_skipped_when_the_version_is_unchanged():
    """An unchanged bundle does no work -- this runs on a hot path."""
    app = _app("0.1.1")
    library = SimpleNamespace(version="0.1.1", manifest={"app": {}})
    sync = AsyncMock()
    # Patch the compile step too: without it an exception on the path past the
    # gate would be swallowed by the helper's guard, and this test would pass
    # for the wrong reason -- it did, until removing the gate failed to fail it.
    with (
        patch.object(pc, "_resolve_library_package", AsyncMock(return_value=library)),
        patch("app.services.app_lifecycle.sync_operational_layer_from_manifest", sync),
        patch(
            "app.services.content_profile_compile.compile_canonical_manifest",
            return_value={"app": {}},
        ),
    ):
        await pc._sync_operational_layer_if_manifest_moved(app)

    sync.assert_not_awaited()
    app.save.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_failure_never_breaks_provisioning():
    """The App still works on its previous operational layer; signup must not fail."""
    app = _app("0.1.0")
    library = SimpleNamespace(version="0.1.1", manifest={"app": {}})
    with (
        patch.object(pc, "_resolve_library_package", AsyncMock(return_value=library)),
        patch(
            "app.services.app_lifecycle.sync_operational_layer_from_manifest",
            AsyncMock(side_effect=RuntimeError("registry down")),
        ),
        patch(
            "app.services.app_lifecycle._materialize_tracks_for_app",
            AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.content_profile_compile.compile_canonical_manifest",
            return_value={"app": {}},
        ),
    ):
        await pc._sync_operational_layer_if_manifest_moved(app)  # must not raise


@pytest.mark.asyncio
async def test_sync_is_a_no_op_without_a_library_package():
    """Deferred provisioning (library not seeded yet) is not an error."""
    app = _app("0.1.0")
    sync = AsyncMock()
    with (
        patch.object(pc, "_resolve_library_package", AsyncMock(return_value=None)),
        patch("app.services.app_lifecycle.sync_operational_layer_from_manifest", sync),
    ):
        await pc._sync_operational_layer_if_manifest_moved(app)
    sync.assert_not_awaited()
