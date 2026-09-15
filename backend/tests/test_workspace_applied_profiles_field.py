"""Phase D1: Workspace.applied_profiles + App.source_profile_slug fields.

These additive scalar fields back the workspace-scope strict-init service
(D3) and the skill-bundle registry (A3). They are not graph state, just
provisioning history persisted directly on the existing Node rows.
"""

import pytest


@pytest.mark.asyncio
async def test_workspace_has_applied_profiles_default_empty():
    """A freshly created Workspace exposes an empty applied_profiles list."""
    from app.models.nodes import Workspace

    ws = await Workspace.create(name="X", kind="organization")
    assert ws.applied_profiles == []


@pytest.mark.asyncio
async def test_app_has_source_profile_slug_default_none():
    """A freshly created App has ``source_profile_slug = None`` by default."""
    from app.models.nodes import App

    app = await App.create(name="A", workspace_id="ws1")
    assert app.source_profile_slug is None


@pytest.mark.asyncio
async def test_applied_profiles_persists_dict_list():
    """applied_profiles round-trips through save/get with full dict contents."""
    from app.models.nodes import Workspace

    ws = await Workspace.create(name="Y", kind="organization")
    ws.applied_profiles = [
        {"slug": "crm-suite", "version": "1.0.0", "applied_at": "2026-05-21T00:00:00Z"}
    ]
    await ws.save()
    ws2 = await Workspace.get(ws.id)
    assert ws2.applied_profiles[0]["slug"] == "crm-suite"
