"""Generic entry.public_share endpoint (DR-30-02)."""

import pytest
from httpx import AsyncClient


async def _provision_published_case_study_with_hook(test_user):
    from app.models.edges import COLLABORATES_ON, CONTAINS, IS_MEMBER_OF
    from app.models.nodes import Entry, EntryType, Track, Workspace
    from app.services.hooks.registry import (
        clear_workspace_registrations,
        register_workspace_hooks,
    )
    from app.utils.time import utc_now_iso

    owner = test_user
    now = utc_now_iso()
    ws = await Workspace.create(
        kind="organization",
        workspace_type="company",
        name="WS P",
        name_fold="ws p",
        created_at=now,
        updated_at=now,
    )
    await owner.connect(ws, edge=IS_MEMBER_OF, role="owner", added_at=now)
    track = await Track.create(title="Portfolio", owner_id=owner.id, workspace_id=ws.id)
    et = await EntryType.create(
        name="Case Study", name_fold="case study", track_id=track.id
    )
    entry = await Entry.create(
        title="Contoso study",
        body="summary",
        track_id=track.id,
        type_id=et.id,
        author_id=owner.id,
        custom_fields={
            "client_name": "Contoso",
            "sector": "logistics",
            "problem": "p",
            "approach": "a",
            "outcome": "o",
            "tech_stack": ["python"],
            "engagement_size": "large",
            "published": True,
        },
    )
    await track.connect(entry, edge=CONTAINS, added_at=now)
    await owner.connect(track, edge=COLLABORATES_ON, role="owner", added_at=now)
    await owner.connect(entry, edge=COLLABORATES_ON, role="owner", added_at=now)
    clear_workspace_registrations(ws.id)
    register_workspace_hooks(
        ws.id,
        "test-bundle",
        [
            {
                "point": "entry.public_share",
                "key": "case_study_share",
                "match": {"entry_type": "case_study"},
                "mode": "declarative",
                "declarative": {
                    "gate_field": "published",
                    "gate_value": True,
                    "projection_fields": [
                        "title",
                        "body",
                        {"field": "client_name", "source": "custom_fields.client_name"},
                        {"field": "sector", "source": "custom_fields.sector"},
                    ],
                    "anon_failure_mode": "not_found",
                },
            }
        ],
    )
    return entry, ws


@pytest.mark.asyncio
async def test_mint_and_redeem_returns_projection(authenticated_client, test_user):
    entry, ws = await _provision_published_case_study_with_hook(test_user)
    mint = await authenticated_client.post(
        f"/api/entries/{entry.id}/public-share", json={}
    )
    assert mint.status_code == 200, mint.text
    token = mint.json()["token"]
    read = await authenticated_client.get(f"/api/public-share/{token}")
    assert read.status_code == 200, read.text
    body = read.json()
    assert body["projection"]["title"] == "Contoso study"
    assert body["projection"]["client_name"] == "Contoso"


@pytest.mark.asyncio
async def test_legacy_alias_route_serves_token(authenticated_client, test_user):
    entry, ws = await _provision_published_case_study_with_hook(test_user)
    mint = await authenticated_client.post(
        f"/api/entries/{entry.id}/public-share", json={}
    )
    token = mint.json()["token"]
    # Legacy /api/portfolio/shared/{token} (URL stability — DR-30-04).
    legacy = await authenticated_client.get(f"/api/portfolio/shared/{token}")
    assert legacy.status_code == 200, legacy.text


@pytest.mark.asyncio
async def test_unpublished_returns_404(authenticated_client, test_user):
    entry, ws = await _provision_published_case_study_with_hook(test_user)
    entry.custom_fields["published"] = False
    await entry.save()
    mint = await authenticated_client.post(
        f"/api/entries/{entry.id}/public-share", json={}
    )
    # Mint refuses upfront — gate fails before share-link creation.
    assert mint.status_code == 404, mint.text


@pytest.mark.asyncio
async def test_collaborator_share_token_not_readable_via_public_route(
    authenticated_client, test_user
):
    """Collaborator-minted entry share links must not work on unauthenticated public-share."""
    from app.services.share_links import mint_share_link

    entry, _ws = await _provision_published_case_study_with_hook(test_user)
    minted = await mint_share_link(
        actor_user_id=test_user.id,
        resource_type="entry",
        resource_id=entry.id,
        role="viewer",
        intent="collaborator",
    )
    token = minted["token"]
    read = await authenticated_client.get(f"/api/public-share/{token}")
    assert read.status_code == 404, read.text


@pytest.mark.asyncio
async def test_redeem_unknown_token_returns_404(authenticated_client, test_user):
    entry, ws = await _provision_published_case_study_with_hook(test_user)
    # Bogus token with sufficient length but no matching ShareLink.
    bogus = "x" * 64
    read = await authenticated_client.get(f"/api/public-share/{bogus}")
    assert read.status_code == 404, read.text
