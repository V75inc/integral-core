"""W6 journey: reference-hello-app through the public Core surface.

The package lives in examples/, not Core. This walk installs it, writes and
reads a note, changes that note, upgrades the App, then exports and imports
the record. A stranger cannot read the imported copy. The echo operation
keeps its policy receipt after the upgrade.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models.edges import CONTAINS
from app.models.nodes import App, Entry, User
from app.schemas.portable_transfer import ExportSelection
from app.services.app_graph import catalog_user, get_track_attached_operational_model
from app.services.app_lifecycle import install_app, update_app_from_library
from app.services.app_operations.dispatch import invoke_app_operation
from app.services.entry_create import create_entry_in_track
from app.services.entry_update import update_entry_in_track
from app.services.hooks.registry import ToolContext
from app.services.operational_model_loader import (
    load_library_operational_models_with_issues,
)
from app.services.permissions import resolve_role
from app.services.portable_transfer import export_resources, import_resources
from tests.fixtures.library_models import publish_library_spec
from tests.fixtures.workspaces import make_org_workspace, workspace_owner_id

REPO = Path(__file__).resolve().parents[3]
REF_APP = REPO / "examples" / "reference-hello-app"


@pytest.mark.contract
@pytest.mark.asyncio
async def test_reference_hello_app_full_extension_journey(monkeypatch):
    """Install, query, modify, upgrade, export, and import the reference App."""
    assert REF_APP.is_dir()
    assert "backend/app/packages" not in str(REF_APP)
    monkeypatch.setenv("INTEGRAL_PACKAGE_PATHS", str(REF_APP.parent))
    monkeypatch.setenv("INTEGRAL_CORE_ONLY", "0")

    specs, _ = load_library_operational_models_with_issues(
        package_paths=[str(REF_APP.parent)],
        core_only=False,
        verify_signatures=False,
    )
    spec = next(item for item in specs if item.slug == "reference-hello-app")
    library = await publish_library_spec(spec)

    workspace = await make_org_workspace("ws-w6-hello")
    actor_id = await workspace_owner_id(workspace)
    installed = await install_app(
        workspace_id=workspace.id,
        library_cp_id=library.id,
        actor_id=actor_id,
        include_seed_data=False,
    )
    app = await App.get(installed["app_id"])
    tracks = await app.nodes(edge=[CONTAINS], node=["Track"], direction="out", limit=10)
    notes = next(track for track in tracks if track.title == "Notes")
    model = await get_track_attached_operational_model(notes)
    types = await model.nodes(
        edge=[CONTAINS], node=["EntryType"], direction="out", limit=5
    )
    created = await create_entry_in_track(
        track=notes,
        user_id=actor_id,
        title="W6 note",
        body="hello from the reference app",
        type_id=types[0].id,
        workspace_id=workspace.id,
    )
    revised = await update_entry_in_track(
        entry_id=created.id,
        user_id=actor_id,
        workspace_id=workspace.id,
        title="W6 note revised",
    )
    assert revised.title == "W6 note revised"

    context = ToolContext(
        user_id=actor_id,
        workspace_id=workspace.id,
        scope=f"app:{app.id}",
    )
    visible = await context.get_entry(created.id)
    assert visible.id == created.id
    assert visible.title == "W6 note revised"

    upgraded = await update_app_from_library(app_id=app.id, actor_id=actor_id)
    assert upgraded["app_id"] == app.id
    echo = await invoke_app_operation(
        user_id=actor_id,
        workspace_id=workspace.id,
        app_id=app.id,
        operation_key="echo",
        payload={"message": "still here"},
    )
    assert echo["output"]["message"] == "still here"
    assert echo["evidence"]["policy_revision"].startswith("policy-sha256:")

    archive = await export_resources(
        user_id=actor_id,
        workspace_id=workspace.id,
        selection=ExportSelection(kind="track", ids=[notes.id], mode="full"),
    )
    owner = await User.get(actor_id)
    destination = await make_org_workspace("ws-w6-hello-import", owner=owner)
    imported = await import_resources(
        user_id=actor_id, workspace_id=destination.id, data=archive
    )
    copied_notes = await notes.__class__.get(imported["resource_ids"][0])
    copied_entries = await copied_notes.nodes(
        edge=[CONTAINS], node=["Entry"], direction="out", limit=10
    )
    copied = next(entry for entry in copied_entries if entry.title == "W6 note revised")
    assert copied.id != created.id

    stranger = await User.create(display_name="W6 stranger")
    await catalog_user(stranger)
    assert await resolve_role(stranger.id, "entry", copied.id) is None
    assert await Entry.get(created.id) is not None
