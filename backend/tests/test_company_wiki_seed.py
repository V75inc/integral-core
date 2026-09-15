"""Tests for CRM Company Wiki handbook seeding (package-owned post_install)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from app.models.edges import CONTAINS
from app.models.nodes import App, Entry, EntryType, Track
from app.services.bundle_post_seed import run_bundle_post_seed

CRM_SEEDS = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "profiles"
    / "crm"
    / "seeds"
)


def _load_crm_post_install():
    post = CRM_SEEDS / "post_install.py"
    spec = importlib.util.spec_from_file_location("crm_post_install_test", post)
    assert spec and spec.loader
    import sys

    seeds = str(CRM_SEEDS.resolve())
    if seeds not in sys.path:
        sys.path.insert(0, seeds)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.asyncio
async def test_ensure_company_wiki_handbook_idempotent(test_user):
    crm = _load_crm_post_install()
    actor_id = test_user.id
    app = await App.create(
        name="CRM",
        name_fold="crm",
        owner_user_id=actor_id,
        description="test",
        visibility="private",
        workspace_id="n.Workspace.test",
        source_profile_slug="crm",
    )
    track = await Track.create(
        title="Company Wiki",
        owner_id=actor_id,
        purpose="handbook",
        visibility="inherit",
        template_id=crm.COMPANY_WIKI_TRACK_KEY,
        workspace_id="n.Workspace.test",
    )
    await app.connect(track, edge=CONTAINS)
    page_type = await EntryType.create(
        name="Page",
        name_fold="page",
        track_id=track.id,
        form_schema={"fields": []},
    )

    first = await crm.ensure_company_wiki_handbook(app, actor_id, track=track)
    second = await crm.ensure_company_wiki_handbook(app, actor_id, track=track)

    from company_wiki_content import COMPANY_WIKI_PAGE_SPECS

    assert first == len(COMPANY_WIKI_PAGE_SPECS)
    assert second == 0

    entries = await Entry.find({"track_id": track.id})
    assert len(entries) == len(COMPANY_WIKI_PAGE_SPECS)
    assert all(e.type_id == page_type.id for e in entries)


@pytest.mark.asyncio
async def test_post_seed_skips_non_crm_app(test_user):
    crm = _load_crm_post_install()
    actor_id = test_user.id
    app = await App.create(
        name="Projects",
        name_fold="projects",
        owner_user_id=actor_id,
        description="test",
        visibility="private",
        workspace_id="n.Workspace.test",
        source_profile_slug="projects",
    )
    track = await Track.create(
        title="Company Wiki",
        owner_id=actor_id,
        purpose="handbook",
        visibility="inherit",
        template_id=crm.COMPANY_WIKI_TRACK_KEY,
        workspace_id="n.Workspace.test",
    )
    await app.connect(track, edge=CONTAINS)
    await EntryType.create(
        name="Page",
        name_fold="page",
        track_id=track.id,
        form_schema={"fields": []},
    )

    planted = await run_bundle_post_seed(
        app, actor_id, bundle_dir=str(CRM_SEEDS.parent)
    )
    assert planted == 0
    assert not await Entry.find({"track_id": track.id})
