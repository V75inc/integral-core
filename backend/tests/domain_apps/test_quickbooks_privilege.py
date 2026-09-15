"""Phase 18 QB-07 — engineer cannot read finance entries.

Privilege contract: the Finance App's Invoices, Expenses, and Bills tracks
are privileged per the Phase 16 ACC-01 anchored-privileged-tracks
decision. An engineer placed on EXCLUDED_FROM these tracks resolves to no
access via the canonical resolve_role; Customers and Vendors remain
visible (account identity, not margins).

Phase 18 ships no resolver code; this rides the unchanged Phase 16
resolver + ACC-03 policy + EXCLUDED_FROM pattern.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import pytest

from app.models.edges import (
    COLLABORATES_ON,
    CONTAINS,
    EXCLUDED_FROM,
    IS_MEMBER_OF,
    OWNS,
)
from app.models.nodes import App, ContentProfile, Entry, Track, User, Workspace
from app.services.app_graph import catalog_user, ensure_integral_app_graph
from app.services.app_lifecycle import install_app
from app.services.content_profile_loader import load_library_profiles_with_issues
from app.services.permissions import resolve_role
from app.utils.time import utc_now_iso

PRIVILEGED_FINANCE_TITLES = {"Invoices", "Expenses", "Bills"}
NON_PRIVILEGED_FINANCE_TITLES = {"Customers", "Vendors"}


async def _bootstrap() -> Dict[str, Any]:
    await ensure_integral_app_graph()
    now = utc_now_iso()
    workspace = await Workspace.create(
        kind="organization",
        workspace_type="company",
        name="WS Finance Priv",
        name_fold="ws finance priv",
        created_at=now,
        updated_at=now,
    )
    founder = await User.create(
        user_id="auth-founder-fin", display_name="Founder", created_at=now
    )
    finance_admin = await User.create(
        user_id="auth-finance-admin", display_name="Finance Admin", created_at=now
    )
    engineer = await User.create(
        user_id="auth-engineer-fin", display_name="Engineer", created_at=now
    )
    for u in (founder, finance_admin, engineer):
        await catalog_user(u)
    await founder.connect(workspace, edge=OWNS, created_at=now)
    for u, role in (
        (founder, "owner"),
        (finance_admin, "member"),
        (engineer, "member"),
    ):
        await u.connect(workspace, edge=IS_MEMBER_OF, role=role, joined_at=now)

    specs, _ = load_library_profiles_with_issues(profiles_root=Path("app/profiles"))
    spec = next(s for s in specs if s.slug == "finance")
    manifest = dict(spec.manifest)
    pkg = dict(manifest.get("package") or {})
    pkg["version"] = spec.version or "1.0.0"
    manifest["package"] = pkg
    lib = await ContentProfile.create(
        name=manifest["package"]["name"],
        scope="app",
        manifest=manifest,
        library_package=True,
        version="1.0.0",
        created_at=now,
        updated_at=now,
    )
    install = await install_app(
        workspace_id=workspace.id, library_cp_id=lib.id, actor_id=founder.id
    )
    app = await App.get(install["app_id"])
    tracks = {
        getattr(t, "title", ""): t
        for t in await app.nodes(edge=[CONTAINS], node=["Track"])
        if isinstance(t, Track)
    }

    # Apply ACC-03/04 role matrix to Finance:
    # - finance_admin: owner on every Finance track.
    # - engineer: EXCLUDED_FROM the three privileged tracks; nothing on the
    # non-privileged Customers/Vendors (no inherited path, so resolves None
    # on those too unless granted directly — assert that explicitly).
    for title, track in tracks.items():
        await finance_admin.connect(
            track,
            edge=COLLABORATES_ON,
            role="owner",
            granted_at=now,
            granted_by=founder.id,
        )
    for title in PRIVILEGED_FINANCE_TITLES:
        await engineer.connect(
            tracks[title],
            edge=EXCLUDED_FROM,
            excluded_at=now,
            excluded_by=founder.id,
            reason="QB-07 — engineer default-deny on Finance privileged tracks",
        )

    return {
        "workspace": workspace,
        "founder": founder,
        "finance_admin": finance_admin,
        "engineer": engineer,
        "app": app,
        "tracks": tracks,
    }


@pytest.mark.asyncio
async def test_engineer_resolves_to_none_on_privileged_finance_tracks():
    """QB-07 — engineer has no role on Invoices, Expenses, Bills."""
    fx = await _bootstrap()
    for title in PRIVILEGED_FINANCE_TITLES:
        role = await resolve_role(fx["engineer"].id, "track", fx["tracks"][title].id)
        assert role is None, (title, role)


@pytest.mark.asyncio
async def test_finance_admin_resolves_to_owner_on_every_finance_track():
    """Regression — finance_admin is owner everywhere in Finance."""
    fx = await _bootstrap()
    for title, track in fx["tracks"].items():
        role = await resolve_role(fx["finance_admin"].id, "track", track.id)
        assert role == "owner", (title, role)


@pytest.mark.asyncio
async def test_engineer_cannot_read_privileged_finance_entries():
    """Cascade — an entry on a privileged track inherits no access for the engineer."""
    fx = await _bootstrap()
    now = utc_now_iso()
    invoices_track = fx["tracks"]["Invoices"]
    sample = await Entry.create(
        track_id=invoices_track.id,
        title="INV-1 — Contoso",
        author_id=fx["founder"].id,
        custom_fields={"total_amount": 1000.0, "balance": 1000.0},
    )
    await invoices_track.connect(sample, edge=CONTAINS, added_at=now)
    role = await resolve_role(fx["engineer"].id, "entry", sample.id)
    assert role is None
