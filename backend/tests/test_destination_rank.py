"""W2.1: filing destinations are ranked with a reason, and decoys do not win."""

from __future__ import annotations

import json

import pytest

from app.models.edges import COLLABORATES_ON, CONTAINS, IS_MEMBER_OF, OWNS
from app.models.nodes import App, Track, User, Workspace
from app.services.agent_scope import current_scope_workspace_id
from app.services.destination_rank import rank_destinations
from app.utils.time import utc_now_iso


async def _track(user, workspace, app, title):
    now = utc_now_iso()
    track = await Track.create(
        title=title,
        owner_id=user.id,
        workspace_id=workspace.id,
        visibility="private",
        created_at=now,
        updated_at=now,
    )
    await user.connect(track, edge=OWNS, role="owner", granted_at=now)
    await app.connect(track, edge=CONTAINS, added_at=now)
    from app.services.app_graph import catalog_track

    await catalog_track(track)
    return track


async def _world():
    """Two open tracks, one packaged track, and a track the caller cannot read."""
    from app.services.app_graph import catalog_app, catalog_user, catalog_workspace

    now = utc_now_iso()
    user = await User.create(
        user_id="rank-user",
        display_name="Rank User",
        created_at=now,
        updated_at=now,
    )
    other = await User.create(
        user_id="rank-other",
        display_name="Other",
        created_at=now,
        updated_at=now,
    )
    await catalog_user(user)
    await catalog_user(other)
    workspace = await Workspace.create(
        kind="personal",
        workspace_type="personal",
        name="Rank Workspace",
        name_fold="rank workspace",
        created_at=now,
        updated_at=now,
    )
    await user.connect(workspace, edge=IS_MEMBER_OF, role="owner", joined_at=now)
    await catalog_workspace(workspace)
    app = await App.create(
        name="Open Books",
        owner_user_id=user.id,
        workspace_id=workspace.id,
        visibility="private",
        created_at=now,
        updated_at=now,
    )
    await user.connect(app, edge=OWNS, role="owner", granted_at=now)
    await catalog_app(app)
    invoices = await _track(user, workspace, app, "Invoices")
    contacts = await _track(user, workspace, app, "Contacts")
    packaged_app = await App.create(
        name="Asset Register",
        owner_user_id=user.id,
        workspace_id=workspace.id,
        visibility="private",
        installed_package_slug="asset-register",
        created_at=now,
        updated_at=now,
    )
    await user.connect(packaged_app, edge=OWNS, role="owner", granted_at=now)
    await catalog_app(packaged_app)
    hidden = await _track(user, workspace, packaged_app, "Hidden assets")
    secret_app = await App.create(
        name="Payroll",
        owner_user_id=other.id,
        workspace_id=workspace.id,
        visibility="private",
        created_at=now,
        updated_at=now,
    )
    await other.connect(secret_app, edge=OWNS, role="owner", granted_at=now)
    await catalog_app(secret_app)
    secret = await _track(other, workspace, secret_app, "Payroll secrets")
    return {
        "other": other,
        "user": user,
        "workspace": workspace,
        "invoices": invoices,
        "contacts": contacts,
        "hidden": hidden,
        "secret": secret,
    }


def _blob(payload) -> str:
    return json.dumps(payload, default=str)


@pytest.mark.asyncio
async def test_invoice_text_leads_and_a_decoy_does_not_file():
    """A word that matches a track title wins. Unrelated text is no_fit."""
    world = await _world()
    token = current_scope_workspace_id.set(world["workspace"].id)
    try:
        invoice = await rank_destinations(
            world["user"].id, text="please file this invoice for the studio"
        )
        decoy = await rank_destinations(world["user"].id, text="quantum flux capacitor")
    finally:
        current_scope_workspace_id.reset(token)
    facet = invoice["facets"][0]
    assert facet["winner"] == world["invoices"].id
    assert facet["why"]
    assert "invoice" in facet["why"][0].casefold()
    assert decoy["facets"][0]["winner"] is None
    assert decoy["facets"][0]["no_fit"] >= 0.8
    blob = _blob(invoice) + _blob(decoy)
    assert "Payroll secrets" not in blob
    assert "Hidden assets" not in blob
    assert invoice["excluded_tracks"] >= 1
    assert facet["candidates"][0]["semantic_similarity"] is None
    assert facet["candidates"][0]["policy_eligible"] is True


@pytest.mark.asyncio
async def test_supplied_fields_pick_the_schema_and_name_gaps(monkeypatch):
    """Field keys the model extracted map onto the type that declares them."""
    world = await _world()

    async def _schema(track):
        if track.id == world["invoices"].id:
            return [
                {
                    "key": "invoice",
                    "name": "Invoice",
                    "fields": [
                        {"key": "amount", "name": "Amount", "required": True},
                        {"key": "due", "name": "Due", "required": True},
                    ],
                }
            ]
        if track.id == world["contacts"].id:
            return [
                {
                    "key": "contact",
                    "name": "Contact",
                    "fields": [
                        {"key": "name", "name": "Name", "required": True},
                        {"key": "email", "name": "Email", "required": False},
                    ],
                }
            ]
        return []

    monkeypatch.setattr("app.services.destination_rank.destination_schema", _schema)
    token = current_scope_workspace_id.set(world["workspace"].id)
    try:
        result = await rank_destinations(
            world["user"].id,
            text="4401",
            facets=[{"text": "4401", "fields": {"amount": "1200", "due": "Friday"}}],
        )
    finally:
        current_scope_workspace_id.reset(token)
    facet = result["facets"][0]
    assert facet["winner"] == world["invoices"].id
    top = facet["candidates"][0]
    assert top["mapped_fields"] == {"amount": "1200", "due": "Friday"}
    assert top["missing_required"] == []
    assert "mapped amount, due" in top["why"]


@pytest.mark.asyncio
async def test_a_view_only_track_is_listed_but_never_chosen():
    """Read access shows the track. Filing needs create access."""
    from app.services.app_graph import catalog_user

    world = await _world()
    now = utc_now_iso()
    member = await User.create(
        user_id="rank-member",
        display_name="Member",
        created_at=now,
        updated_at=now,
    )
    await catalog_user(member)
    await member.connect(
        world["workspace"], edge=IS_MEMBER_OF, role="member", joined_at=now
    )
    await member.connect(world["contacts"], edge=COLLABORATES_ON, role="viewer")
    token = current_scope_workspace_id.set(world["workspace"].id)
    try:
        ranked = await rank_destinations(member.id, text="contacts for the studio")
    finally:
        current_scope_workspace_id.reset(token)
    facet = ranked["facets"][0]
    by_id = {row["track_id"]: row for row in facet["candidates"]}
    assert by_id[world["contacts"].id]["policy_eligible"] is False
    assert by_id[world["contacts"].id]["score"] > 0
    assert facet["winner"] is None
