"""Phase 18 — Finance App install + IS_CONNECTED_TO binding (QB-02).

Confirms:

- The Finance App manifest compiles.
- Install via ``app_lifecycle.install_app`` reaches ``lifecycle_state=active``.
- The five canonical tracks materialize (Invoices, Expenses, Customers,
  Vendors, Bills) wired via ``CONTAINS`` to the App.
- Each track wires ``IS_CONNECTED_TO`` to the QuickBooks ``Connector`` —
  the binding pattern the multi-track routing in ``sync_runtime``
  (``_resolve_bound_tracks_by_entry_type``) consumes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

import pytest

from app.agentive.services.connector_registry_node import create_connector
from app.models.edges import CONTAINS, IS_CONNECTED_TO
from app.models.nodes import App, ContentProfile, Track, User, Workspace
from app.services.app_graph import catalog_user, ensure_integral_app_graph
from app.services.app_lifecycle import install_app
from app.services.content_profile_loader import load_library_profiles_with_issues
from app.utils.time import utc_now_iso


async def _load_finance_library_cp() -> ContentProfile:
    specs, _ = load_library_profiles_with_issues(profiles_root=Path("app/profiles"))
    spec = next((s for s in specs if s.slug == "finance"), None)
    assert spec is not None
    manifest = dict(spec.manifest)
    pkg = dict(manifest.get("package") or {})
    pkg["version"] = spec.version or "1.0.0"
    manifest["package"] = pkg
    now = utc_now_iso()
    return await ContentProfile.create(
        name=manifest["package"]["name"],
        scope="app",
        manifest=manifest,
        library_package=True,
        version="1.0.0",
        created_at=now,
        updated_at=now,
    )


async def _provision_workspace_owner() -> Dict[str, object]:
    await ensure_integral_app_graph()
    now = utc_now_iso()
    workspace = await Workspace.create(
        kind="organization",
        workspace_type="company",
        name="WS Finance",
        name_fold="ws finance",
        created_at=now,
        updated_at=now,
    )
    owner = await User.create(
        user_id="auth-finance-owner",
        display_name="Finance Owner",
        created_at=now,
    )
    await catalog_user(owner)
    return {"workspace": workspace, "owner": owner}


@pytest.mark.asyncio
async def test_finance_app_installs_with_five_tracks():
    """QB-02 — Finance install reaches active with 5 canonical tracks."""
    fx = await _provision_workspace_owner()
    lib = await _load_finance_library_cp()
    result = await install_app(
        workspace_id=fx["workspace"].id,
        library_cp_id=lib.id,
        actor_id=fx["owner"].id,
    )
    assert result["status"] == "active"

    app = await App.get(result["app_id"])
    assert app.lifecycle_state == "active"
    tracks = await app.nodes(edge=[CONTAINS], node=["Track"])
    titles = {getattr(t, "title", "") for t in tracks}
    expected = {"Invoices", "Expenses", "Customers", "Vendors", "Bills"}
    missing = expected - titles
    assert not missing, f"Finance missing tracks: {missing}; have {titles}"


@pytest.mark.asyncio
async def test_finance_tracks_wire_is_connected_to_quickbooks_connector():
    """QB-02 — wiring each Finance track to the QB Connector via IS_CONNECTED_TO."""
    fx = await _provision_workspace_owner()
    lib = await _load_finance_library_cp()
    result = await install_app(
        workspace_id=fx["workspace"].id,
        library_cp_id=lib.id,
        actor_id=fx["owner"].id,
    )
    app = await App.get(result["app_id"])
    tracks = await app.nodes(edge=[CONTAINS], node=["Track"])
    track_by_title = {getattr(t, "title", ""): t for t in tracks}

    # Create the QuickBooks Connector through the canonical registry path
    # (materializes per-connector Policy, I-CON-04). Stamp subclass_slug
    # so sync_runtime can dispatch (I-CON-02).
    connector = await create_connector(
        owner=fx["owner"].id,
        auth_state={
            "realm_id": "9341454794050000",
            "access_token": "stub",
            "refresh_token": "stub",
            "access_token_expires_at": (datetime.now(timezone.utc).isoformat()),
            "environment": "sandbox",
        },
    )
    connector.subclass_slug = "quickbooks"
    await connector.save()

    # Wire IS_CONNECTED_TO from the connector to each of the five tracks.
    # Canonical idiom: connector.connect(track, edge=IS_CONNECTED_TO, ...).
    now = utc_now_iso()
    for title in ("Invoices", "Expenses", "Customers", "Vendors", "Bills"):
        track = track_by_title[title]
        await connector.connect(
            track,
            edge=IS_CONNECTED_TO,
            mapping_profile_yaml="",
        )

    bound = await connector.nodes(
        edge=["IsConnectedTo"], direction="out", node=["Track"]
    )
    bound_titles = {getattr(t, "title", "") for t in bound}
    assert bound_titles == {"Invoices", "Expenses", "Customers", "Vendors", "Bills"}


@pytest.mark.asyncio
async def test_multi_track_routing_resolves_entry_type_to_track():
    """QB-02 — the new _resolve_bound_tracks_by_entry_type helper maps
    each Finance EntryType to its containing Track, so sync_runtime can
    route per-record by materialized.entry_type_key (5 tracks, 1 connector).
    """
    from app.services.connectors.sync_runtime import (
        _resolve_bound_tracks_by_entry_type,
    )

    fx = await _provision_workspace_owner()
    lib = await _load_finance_library_cp()
    result = await install_app(
        workspace_id=fx["workspace"].id,
        library_cp_id=lib.id,
        actor_id=fx["owner"].id,
    )
    app = await App.get(result["app_id"])
    tracks = await app.nodes(edge=[CONTAINS], node=["Track"])
    track_by_title = {getattr(t, "title", ""): t for t in tracks}

    connector = await create_connector(
        owner=fx["owner"].id,
        auth_state={
            "realm_id": "test-realm",
            "access_token": "x",
            "refresh_token": "y",
            "access_token_expires_at": datetime.now(timezone.utc).isoformat(),
            "environment": "sandbox",
        },
    )
    connector.subclass_slug = "quickbooks"
    await connector.save()
    for title in ("Invoices", "Expenses", "Customers", "Vendors", "Bills"):
        await connector.connect(
            track_by_title[title], edge=IS_CONNECTED_TO, mapping_profile_yaml=""
        )

    routing = await _resolve_bound_tracks_by_entry_type(connector)
    # Each entry_type_key resolves to the matching track.
    assert routing["qb_invoice"].id == track_by_title["Invoices"].id
    assert routing["qb_expense"].id == track_by_title["Expenses"].id
    assert routing["qb_customer"].id == track_by_title["Customers"].id
    assert routing["qb_vendor"].id == track_by_title["Vendors"].id
    assert routing["qb_bill"].id == track_by_title["Bills"].id
