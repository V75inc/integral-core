"""Explicit type_id must fail closed — never silently swap to another type.

Regression for the entry-create materialization path: when a caller passes
``type_id`` and ``EntryType.get`` misses (types not yet materialized, or a
stale id), ``create_entry_in_track`` must raise ``ResourceNotFoundError``
rather than falling back to ``"post"`` / the first available type.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.smoke


async def _bootstrap_track_with_type(email: str):
    from jvspatial.api.auth.models import UserCreate

    from app.api.auth import _get_auth_service
    from app.api.validators_common import compute_fold
    from app.models.edges import CONTAINS
    from app.models.nodes import EntryType, User
    from app.services.app_graph import (
        catalog_user,
        ensure_track_attached_operational_model,
    )
    from app.services.personal_workspace import ensure_personal_workspace
    from app.utils.time import utc_now_iso

    auth_service = _get_auth_service()
    resp = await auth_service.register_user(
        UserCreate(email=email, password="testpassword123")
    )
    node = await User.create(user_id=resp.id, display_name="Type Id Test")
    await catalog_user(node)
    ws = await ensure_personal_workspace(node)

    from app.agentive.tooling.invoke import invoke_route_in_process
    from app.api.tracks import create_track

    created = await invoke_route_in_process(
        create_track,
        principal_id=resp.id,
        scope=ws.id,
        title="Type Id Track",
        visibility="private",
    )
    from app.models.nodes import Track

    track = await Track.get(created["track"]["id"])
    assert track is not None
    cp = await ensure_track_attached_operational_model(track)
    now = utc_now_iso()
    et = await EntryType.create(
        name="Post",
        name_fold=compute_fold("Post"),
        track_id=track.id,
        form_schema={"fields": []},
        is_template=False,
        created_at=now,
        updated_at=now,
    )
    await cp.connect(et, edge=CONTAINS, added_at=now)
    return resp.id, track, et


@pytest.mark.asyncio
async def test_explicit_type_id_missing_raises_not_silent_fallback():
    """Bogus type_id raises ResourceNotFoundError and creates no Entry."""
    from app.api.errors import ResourceNotFoundError
    from app.models.nodes import Entry
    from app.services.entry_create import create_entry_in_track

    user_id, track, _et = await _bootstrap_track_with_type("typeid-a@example.com")
    title = "must-not-create-with-bogus-type"

    with pytest.raises(ResourceNotFoundError):
        await create_entry_in_track(
            track=track,
            user_id=user_id,
            title=title,
            type_id="EntryType:does-not-exist",
        )

    found = await Entry.find({"context.title": title})
    assert list(found or []) == []


@pytest.mark.asyncio
async def test_explicit_type_id_retries_after_materialize(monkeypatch):
    """When get misses once, materialize then retry can recover the type."""
    from app.models.nodes import Entry, EntryType
    from app.services import entry_create as entry_create_mod

    user_id, track, et = await _bootstrap_track_with_type("typeid-b@example.com")

    calls = {"get": 0, "materialize": 0}
    original_get = EntryType.get

    async def counting_get(type_id: str):
        calls["get"] += 1
        if calls["get"] == 1:
            return None
        return await original_get(type_id)

    async def counting_materialize(_track):
        calls["materialize"] += 1
        return [et]

    monkeypatch.setattr(EntryType, "get", staticmethod(counting_get))
    monkeypatch.setattr(
        entry_create_mod, "materialize_entry_types_from_tier", counting_materialize
    )

    entry = await entry_create_mod.create_entry_in_track(
        track=track,
        user_id=user_id,
        title="retry-hit",
        type_id=et.id,
    )

    assert entry.type_id == et.id
    assert calls["get"] >= 2
    assert calls["materialize"] == 1
    found = await Entry.find({"context.title": "retry-hit"})
    assert len(list(found or [])) == 1


@pytest.mark.asyncio
async def test_generic_create_rejects_protected_app_field_before_persistence():
    """Every caller of the shared create writer observes the operation gate."""
    from app.api.errors import BadRequestError
    from app.models.nodes import Entry
    from app.services.app_invariant_guards import (
        register_protected_fields,
        unregister_protected_fields,
    )
    from app.services.entry_create import create_entry_in_track

    user_id, track, entry_type = await _bootstrap_track_with_type(
        "typeid-protected@example.com"
    )
    from app.models.edges import CONTAINS, HAS_APPLICATION_DEFINITION, OWNS
    from app.models.nodes import App, ApplicationDefinition
    from app.services.app_graph import catalog_app
    from app.services.application_definitions import definition_fingerprint
    from app.services.permissions import get_user_node

    app = await App.create(name="Protected create QA", workspace_id=track.workspace_id)
    await catalog_app(app)
    owner = await get_user_node(user_id)
    await owner.connect(app, edge=OWNS)
    await app.connect(track, edge=CONTAINS)
    manifest = {
        "app": {
            "protected_state": {"post": {"fields": ["lifecycle_state"]}},
            "operations": [],
        }
    }
    definition = await ApplicationDefinition.create(
        app_id=app.id,
        workspace_id=track.workspace_id,
        revision=1,
        status="active",
        canonical_manifest=manifest,
        manifest_fingerprint=definition_fingerprint(manifest),
    )
    await app.connect(definition, edge=HAS_APPLICATION_DEFINITION)
    app.active_definition_id = definition.id
    app.active_definition_revision = 1
    await app.save()
    app_id = app.id
    register_protected_fields(track.workspace_id, app_id, {"post": ["lifecycle_state"]})
    try:
        with pytest.raises(BadRequestError) as ei:
            await create_entry_in_track(
                track=track,
                user_id=user_id,
                title="must-not-persist-protected-create",
                type_id=entry_type.id,
                custom_fields={"lifecycle_state": "checked_out"},
                workspace_id=track.workspace_id,
            )
        assert ei.value.details.get("error_code") == "protected_field_write"
    finally:
        unregister_protected_fields(track.workspace_id, app_id)

    found = await Entry.find({"context.title": "must-not-persist-protected-create"})
    assert list(found or []) == []


@pytest.fixture
async def protected_create_track():
    """An owned App with actual schema defaults and canonical protected state."""
    from app.models.edges import CONTAINS, HAS_APPLICATION_DEFINITION, OWNS
    from app.models.nodes import App, ApplicationDefinition
    from app.services.app_graph import catalog_app
    from app.services.application_definitions import definition_fingerprint
    from app.services.permissions import get_user_node

    user_id, track, entry_type = await _bootstrap_track_with_type(
        "protected-defaults@example.com"
    )
    entry_type.form_schema = {
        "fields": [
            {"key": "label", "name": "Label", "type": "text"},
            {"key": "managed_identity", "name": "Identity", "type": "text"},
            {
                "key": "managed_status",
                "name": "State",
                "type": "text",
                "default": "pending",
            },
            {
                "key": "managed_payload",
                "name": "Payload",
                "type": "json",
                "default": {"ready": True, "items": ["initial"]},
            },
        ]
    }
    await entry_type.save()
    app = await App.create(
        name="Protected defaults QA", workspace_id=track.workspace_id
    )
    await catalog_app(app)
    owner = await get_user_node(user_id)
    await owner.connect(app, edge=OWNS)
    await app.connect(track, edge=CONTAINS)
    manifest = {
        "app": {
            "protected_state": {
                "post": {
                    "fields": [
                        "managed_identity",
                        "managed_status",
                        "managed_payload",
                        "hook_only",
                    ]
                }
            },
            "operations": [],
        }
    }
    definition = await ApplicationDefinition.create(
        app_id=app.id,
        workspace_id=track.workspace_id,
        revision=1,
        status="active",
        canonical_manifest=manifest,
        manifest_fingerprint=definition_fingerprint(manifest),
    )
    await app.connect(definition, edge=HAS_APPLICATION_DEFINITION)
    app.active_definition_id = definition.id
    app.active_definition_revision = 1
    await app.save()
    return user_id, track, entry_type


@pytest.mark.asyncio
async def test_generic_create_keeps_schema_owned_defaults(
    protected_create_track, monkeypatch
):
    from app.models.nodes import Entry
    from app.services import entry_create

    user_id, track, entry_type = protected_create_track

    async def harmless_hook(**kwargs):
        fields = kwargs["custom_fields"]
        fields["label"] = "normalized"
        # Reordering JSON keys is not a protected value mutation.
        fields["managed_payload"] = {"items": ["initial"], "ready": True}
        return fields

    monkeypatch.setattr(entry_create, "run_entry_before_write_hooks", harmless_hook)
    entry = await entry_create.create_entry_in_track(
        track=track,
        user_id=user_id,
        type_id=entry_type.id,
        title="schema-owned-defaults",
        custom_fields={"label": "input"},
        workspace_id=track.workspace_id,
    )
    stored = await Entry.find_one({"id": entry.id})
    assert stored.custom_fields["label"] == "normalized"
    assert stored.custom_fields["managed_identity"] is None
    assert stored.custom_fields["managed_status"] == "pending"
    assert stored.custom_fields["managed_payload"] == {
        "ready": True,
        "items": ["initial"],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, "pending", "forged"])
async def test_generic_create_still_rejects_explicit_protected_defaults(
    protected_create_track, value
):
    from app.api.errors import BadRequestError
    from app.models.nodes import Entry
    from app.services.entry_create import create_entry_in_track

    user_id, track, entry_type = protected_create_track
    with pytest.raises(BadRequestError) as error:
        await create_entry_in_track(
            track=track,
            user_id=user_id,
            type_id=entry_type.id,
            title="explicit-protected-default",
            custom_fields={"managed_status": value},
            workspace_id=track.workspace_id,
        )
    assert error.value.details["error_code"] == "protected_field_write"
    assert error.value.details["fields"] == ["managed_status"]
    assert not await Entry.find({"context.title": "explicit-protected-default"})


@pytest.mark.asyncio
async def test_generic_create_still_checks_preview_seeded_values(
    protected_create_track, monkeypatch
):
    from app.api.errors import BadRequestError
    from app.models.nodes import Entry
    from app.services import entry_create

    user_id, track, entry_type = protected_create_track

    async def seed_protected(**kwargs):
        return {**kwargs["custom_fields"], "managed_status": "forged"}

    monkeypatch.setattr(entry_create, "_seed_preview_default_fields", seed_protected)
    with pytest.raises(BadRequestError) as error:
        await entry_create.create_entry_in_track(
            track=track,
            user_id=user_id,
            type_id=entry_type.id,
            title="seeded-protected-default",
            custom_fields={"label": "allowed"},
            workspace_id=track.workspace_id,
        )
    assert error.value.details["error_code"] == "protected_field_write"
    assert error.value.details["fields"] == ["managed_status"]
    assert not await Entry.find({"context.title": "seeded-protected-default"})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation,key",
    [
        ("set", "managed_identity"),
        ("remove", "managed_status"),
        ("remove_null", "managed_identity"),
        ("add_null", "hook_only"),
        ("nested_list", "managed_payload"),
        ("nested_type", "managed_payload"),
    ],
)
async def test_generic_create_blocks_hook_changes_to_protected_state(
    protected_create_track, monkeypatch, mutation, key
):
    from app.api.errors import BadRequestError
    from app.models.nodes import Entry
    from app.services import entry_create

    user_id, track, entry_type = protected_create_track

    async def mutate_in_place(**kwargs):
        fields = kwargs["custom_fields"]
        if mutation == "set":
            fields[key] = "forged"
        elif mutation in {"remove", "remove_null"}:
            del fields[key]
        elif mutation == "add_null":
            fields[key] = None
        elif mutation == "nested_list":
            fields[key]["items"].append("forged")
        else:
            fields[key]["ready"] = 1  # True == 1 must not conceal a JSON type change.
        return fields

    monkeypatch.setattr(entry_create, "run_entry_before_write_hooks", mutate_in_place)
    with pytest.raises(BadRequestError) as error:
        await entry_create.create_entry_in_track(
            track=track,
            user_id=user_id,
            type_id=entry_type.id,
            title="hook-protected-default",
            custom_fields={"label": "allowed"},
            workspace_id=track.workspace_id,
        )
    assert error.value.details["error_code"] == "protected_field_write"
    assert error.value.details["fields"] == [key]
    assert not await Entry.find({"context.title": "hook-protected-default"})
