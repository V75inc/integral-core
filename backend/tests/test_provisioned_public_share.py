"""Provisioning a prescribed track must not create a live public share.

Share tokens are hash-only and disclosed once at mint. A mint that happens
during provisioning has no recipient — the plaintext is discarded and nobody
can ever use the link — yet the link is neither revoked nor expired, so
``_track_has_active_public_share`` reports the track as publicly shared and its
entries become anonymous relation candidates through a sibling track's share.
The owner cannot recover either: the enable path returns ``token: None``
whenever an active link already exists.

The compiler now carries ``public_share`` onto compiled track specs so the
declaration reaches the enable path as a default — but provisioning still must
not mint from it. These tests pin that separation: the declaration survives
compilation, and provisioning still creates no link.
"""

from copy import deepcopy

import pytest

from app.models.edges import CONTAINS, HAS_OPERATIONAL_MODEL, IS_MEMBER_OF, OWNS
from app.models.nodes import App, OperationalModel, ShareLink, Track, User, Workspace
from app.services.app_graph import catalog_app, catalog_workspace
from app.services.operational_model_compile import compile_canonical_manifest
from app.services.operational_model_merge import (
    provision_prescribed_tracks_from_app_manifest,
)
from app.utils.time import utc_now_iso

# Mirrors hr_app's employee_onboarding track: a write-only intake form.
# Anonymous submitters post a record and read nothing back.
_DECLARED_PERMS = {
    "read_entries": False,
    "create_entries": True,
    "update_entries": False,
    "read_comments": False,
    "create_comments": False,
}

_MANIFEST = {
    "operational_model_schema_version": 2,
    "scope": "app",
    "app": {
        "tracks": [
            {
                "key": "public_intake",
                "name": "Public Intake",
                "description": "Collects intake records for review before any public sharing is enabled.",
                "provision_on_create": True,
                "public_share": {"enabled": True, "permissions": _DECLARED_PERMS},
            }
        ],
        "relations": [],
        "defaults": {"provision_prescribed_tracks": True},
    },
}


async def _provisioned_track(*, user: User, icon: str = "") -> Track:
    now = utc_now_iso()
    ws = await Workspace.create(
        kind="organization",
        workspace_type="company",
        name="Provision WS",
        name_fold="provision ws",
        created_at=now,
        updated_at=now,
    )
    await catalog_workspace(ws)
    await user.connect(ws, edge=OWNS, role="owner", added_at=now)
    await user.connect(ws, edge=IS_MEMBER_OF, role="owner", added_at=now)
    app_node = await App.create(
        name="Provision App", owner_id=user.id, workspace_id=ws.id
    )
    await user.connect(app_node, edge=OWNS, added_at=now)
    manifest = deepcopy(_MANIFEST)
    manifest["app"]["tracks"][0]["icon"] = icon
    cp = await OperationalModel.create(name="Provision CP", manifest=manifest)
    await app_node.connect(cp, edge=HAS_OPERATIONAL_MODEL, added_at=now)
    app_node.attached_operational_model_id = cp.id
    await app_node.save()
    await ws.connect(app_node, edge=CONTAINS)
    await catalog_app(app_node)

    await provision_prescribed_tracks_from_app_manifest(app_node, user.id)

    tracks = await app_node.nodes(edge=["CONTAINS"], node=["Track"])
    provisioned = [t for t in tracks if t.template_id == "public_intake"]
    assert provisioned, "prescribed track was not provisioned"
    return provisioned[0]


@pytest.mark.asyncio
async def test_provisioning_leaves_no_live_public_share_link(test_user):
    track = await _provisioned_track(user=test_user)

    links = await ShareLink.find(
        {
            "context.resource_type": "track",
            "context.resource_id": track.id,
            "context.intent": "public",
        }
    )
    live = [link for link in links if not link.revoked_at]
    assert live == [], (
        "provisioning minted a public share link whose token nobody holds; the "
        "track now reads as publicly shared, leaks entries sideways through a "
        "sibling's share, and can never hand out a working URL"
    )


def test_compiler_carries_public_share_onto_track_specs():
    """The declaration must survive compilation to reach the enable default."""
    canonical = compile_canonical_manifest(manifest=dict(_MANIFEST))
    specs = (canonical.get("app") or {}).get("tracks") or []
    assert specs, "compiled manifest lost the prescribed track entirely"
    assert specs[0].get("public_share") == {
        "enabled": True,
        "permissions": _DECLARED_PERMS,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("component_fallback", [False, True])
async def test_declared_intent_is_offered_as_the_enable_default(
    monkeypatch, component_fallback, test_user
):
    """The owner is offered the app author's permissions, not the UI defaults.

    An intake track declares create-only access. If the enable path fell back to
    the generic `read_entries: true` default, enabling would publish exactly the
    records the track was built to collect privately.
    """
    from app.services.track_public_share import declared_public_share

    if component_fallback:
        manifest = deepcopy(_MANIFEST)
        manifest["app"]["tracks"][0]["entry_types"] = [
            {
                "name": "Document",
                "ui_contributions": [
                    {
                        "placement": "entry_compose",
                        "component_key": "document_composer",
                        "fallback_view": "document_view",
                    }
                ],
            }
        ]
        monkeypatch.setitem(globals(), "_MANIFEST", manifest)

    track = await _provisioned_track(user=test_user)
    declared = await declared_public_share(track)
    assert declared is not None, "manifest declaration did not reach the track"
    assert declared["permissions"] == _DECLARED_PERMS
    assert declared["permissions"]["read_entries"] is False


@pytest.mark.asyncio
async def test_explicit_dot_survives_reprovisioning_from_stale_app(test_user):
    from jvspatial.core.context import get_default_context

    from app.services.authority_facts import primary_node_record
    from app.services.resource_metadata_update import update_track_metadata

    track = await _provisioned_track(icon="briefcase", user=test_user)
    assert track.icon == "briefcase"
    assert track.icon_overridden is False
    parents = await track.nodes(edge=[CONTAINS], node=[App], direction="in")
    app_node = parents[0]
    await update_track_metadata(user_id=track.owner_id, track_id=track.id, icon="")
    await provision_prescribed_tracks_from_app_manifest(app_node, track.owner_id)
    graph = get_default_context()
    row = await primary_node_record(graph.database, Track, track.id)
    assert row["context"]["icon"] == ""
    assert row["context"]["icon_overridden"] is True


@pytest.mark.asyncio
async def test_provisioning_admits_owner_for_canonical_post_seed_write(
    test_user, monkeypatch
):
    from app.services import bundle_post_seed
    from app.services.entry_create import create_entry_in_track

    created = []

    async def post_seed(app_node, actor_id):
        tracks = await app_node.nodes(edge=[CONTAINS], node=[Track])
        entry = await create_entry_in_track(
            track=tracks[0],
            user_id=actor_id,
            workspace_id=app_node.workspace_id,
            title="Seeded fixture",
        )
        created.append(entry.id)
        return 1

    manifest = deepcopy(_MANIFEST)
    manifest["app"]["tracks"][0]["entry_types"] = [{"key": "post", "name": "Post"}]
    monkeypatch.setitem(globals(), "_MANIFEST", manifest)
    monkeypatch.setattr(bundle_post_seed, "run_bundle_post_seed", post_seed)
    await _provisioned_track(user=test_user)
    assert len(created) == 1
