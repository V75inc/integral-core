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

import pytest

from app.models.edges import HAS_CONTENT_PROFILE, OWNS
from app.models.nodes import App, ContentProfile, ShareLink, Track, User, Workspace
from app.services.content_profile_compile import compile_canonical_manifest
from app.services.content_profile_merge import (
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
    "content_profile_schema_version": 2,
    "scope": "app",
    "app": {
        "tracks": [
            {
                "key": "public_intake",
                "name": "Public Intake",
                "provision_on_create": True,
                "public_share": {"enabled": True, "permissions": _DECLARED_PERMS},
            }
        ],
        "relations": [],
        "defaults": {"provision_prescribed_tracks": True},
    },
}


async def _provisioned_track() -> Track:
    now = utc_now_iso()
    user = await User.create(user_id="auth-pubshare-prov", display_name="Owner")
    ws = await Workspace.create(
        kind="organization",
        workspace_type="company",
        name="Provision WS",
        name_fold="provision ws",
        created_at=now,
        updated_at=now,
    )
    app_node = await App.create(
        name="Provision App", owner_id=user.id, workspace_id=ws.id
    )
    await user.connect(app_node, edge=OWNS, added_at=now)
    cp = await ContentProfile.create(name="Provision CP", manifest=_MANIFEST)
    await app_node.connect(cp, edge=HAS_CONTENT_PROFILE, added_at=now)

    await provision_prescribed_tracks_from_app_manifest(app_node, user.id)

    tracks = await app_node.nodes(edge=["CONTAINS"], node=["Track"])
    provisioned = [t for t in tracks if t.template_id == "public_intake"]
    assert provisioned, "prescribed track was not provisioned"
    return provisioned[0]


@pytest.mark.asyncio
async def test_provisioning_leaves_no_live_public_share_link():
    track = await _provisioned_track()

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
async def test_declared_intent_is_offered_as_the_enable_default():
    """The owner is offered the app author's permissions, not the UI defaults.

    An intake track declares create-only access. If the enable path fell back to
    the generic `read_entries: true` default, enabling would publish exactly the
    records the track was built to collect privately.
    """
    from app.services.track_public_share import declared_public_share

    track = await _provisioned_track()
    declared = await declared_public_share(track)
    assert declared is not None, "manifest declaration did not reach the track"
    assert declared["permissions"] == _DECLARED_PERMS
    assert declared["permissions"]["read_entries"] is False
