"""Public-form authorization remains independent of form presentation."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api import tracks_public_share
from app.api.errors import InsufficientPermissionsError
from app.services import public_share_form_extensions


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("entry_type_key", "extensions", "is_private"),
    [
        ("member_application", {}, False),
        (
            "internal_application",
            {"authenticated_only_entry_type_keys": "internal_application"},
            True,
        ),
        ("internal_application", {}, False),
    ],
)
async def test_private_form_guard(monkeypatch, entry_type_key, extensions, is_private):
    monkeypatch.setattr(
        tracks_public_share.EntryType,
        "get",
        AsyncMock(
            return_value=SimpleNamespace(
                form_schema={"_manifest_entry_type_key": entry_type_key}
            )
        ),
    )
    monkeypatch.setattr(
        public_share_form_extensions,
        "declared_public_share",
        AsyncMock(return_value={"extensions": extensions}),
    )
    entry = SimpleNamespace(type_id="test-type")
    track = SimpleNamespace(id="test-track")
    if is_private:
        with pytest.raises(InsufficientPermissionsError):
            await tracks_public_share._reject_private_form_entry(entry, track)
    else:
        await tracks_public_share._reject_private_form_entry(entry, track)


@pytest.mark.asyncio
async def test_public_visibility_filters_before_pagination(monkeypatch):
    type_rows = [
        SimpleNamespace(id="public", name="Public", form_schema={}),
        SimpleNamespace(id="private", name="Internal", form_schema={}),
    ]
    monkeypatch.setattr(
        tracks_public_share.EntryType, "find", AsyncMock(return_value=type_rows)
    )

    async def keys(track):
        return frozenset({"internal"}) if track.id == "a" else frozenset()

    monkeypatch.setattr(tracks_public_share, "_private_public_form_keys", keys)
    entries = [
        SimpleNamespace(id="x", track_id="a", type_id="private"),
        SimpleNamespace(id="y", track_id="a", type_id="public"),
        SimpleNamespace(id="z", track_id="b", type_id="private"),
        SimpleNamespace(id="missing", track_id="b", type_id="unresolved"),
        SimpleNamespace(id="plain", track_id="a", type_id=None),
    ]
    visible = await tracks_public_share._filter_public_entry_types(
        entries, [SimpleNamespace(id="a"), SimpleNamespace(id="b")]
    )
    assert [row.id for row in visible] == ["y", "z", "plain"]


@pytest.mark.asyncio
async def test_requested_type_uses_published_access_policy(monkeypatch):
    track = SimpleNamespace(id="fixture-track")
    monkeypatch.setattr(
        tracks_public_share,
        "_private_public_form_keys",
        AsyncMock(return_value=frozenset({"internal"})),
    )
    await tracks_public_share._require_public_entry_type(
        SimpleNamespace(name="Volunteer Registration", form_schema={}), track
    )
    with pytest.raises(InsufficientPermissionsError):
        await tracks_public_share._require_public_entry_type(
            SimpleNamespace(name="Internal", form_schema={}), track
        )


@pytest.mark.asyncio
async def test_authoritative_policy_lookup_failure_is_not_an_empty_policy(monkeypatch):
    from app.exceptions import AuthorizationUnavailableError
    from app.services import track_public_share

    monkeypatch.setattr(
        track_public_share,
        "_parent_app_for_track",
        AsyncMock(side_effect=RuntimeError("fixture graph unavailable")),
    )
    track = SimpleNamespace(id="fixture-track", title="Fixture", template_id="")
    with pytest.raises(AuthorizationUnavailableError):
        await public_share_form_extensions.authenticated_only_entry_type_keys(track)
    with pytest.raises(AuthorizationUnavailableError):
        await public_share_form_extensions.server_managed_field_keys(
            track, entry_type_slug="managed"
        )


@pytest.mark.asyncio
async def test_fixed_public_link_checks_declared_policy(monkeypatch):
    track = SimpleNamespace(id="t1")
    entry_type = SimpleNamespace(
        track_id=track.id,
        is_template=False,
        name="Internal application",
        form_schema={"_manifest_entry_type_key": "internal_application"},
    )
    monkeypatch.setattr(
        tracks_public_share.EntryType, "get", AsyncMock(return_value=entry_type)
    )
    monkeypatch.setattr(
        tracks_public_share,
        "_private_public_form_keys",
        AsyncMock(return_value=frozenset({"internal_application"})),
    )
    with pytest.raises(InsufficientPermissionsError):
        await tracks_public_share._resolve_fixed_public_entry_type(track, "form")
