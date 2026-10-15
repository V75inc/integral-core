"""Regression tests for relation admission and post-hook revalidation (I-APP-05)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.exceptions import BadRequestError
from app.models.edges import CONTAINS
from app.schemas.policy import Decision, Subject
from app.services.app_graph import catalog_track
from app.services.operational_model_entry_fields import (
    _validate_relation_values,
    validate_and_materialize_entry_custom_fields,
)

pytestmark = [pytest.mark.asyncio]


async def _wired_entry_pair(*, writer, workspace_id: str):
    """Source track + target track/entry in one workspace with CONTAINS edges."""
    from app.models.nodes import Entry, EntryType, Track

    source = await Track.create(
        title="Src",
        owner_id=writer.user_id,
        workspace_id=workspace_id,
    )
    await catalog_track(source)
    target_track = await Track.create(
        title="Tgt",
        owner_id=writer.user_id,
        workspace_id=workspace_id,
    )
    await catalog_track(target_track)
    et = await EntryType.create(
        name="Rec",
        track_id=target_track.id,
        form_schema={"_manifest_entry_type_key": "rec", "fields": []},
    )
    await target_track.connect(et, edge=CONTAINS)
    target = await Entry.create(
        title="Private",
        track_id=target_track.id,
        type_id=et.id,
        author_id=writer.user_id,
    )
    await target_track.connect(target, edge=CONTAINS)
    return source, target


async def test_skip_target_read_flag_does_not_bypass_target_read(monkeypatch, test_user):
    """OM ``skip_target_read`` must not disable canonical entry.read checks."""
    from app.services.personal_workspace import ensure_personal_workspace

    ws = await ensure_personal_workspace(test_user)
    source, target = await _wired_entry_pair(
        writer=test_user, workspace_id=ws.id
    )

    async def _deny(**kwargs):
        return Decision(allowed=False, reason="denied")

    monkeypatch.setattr("app.services.policy_engine.evaluate", _deny)

    with pytest.raises(BadRequestError, match="target read"):
        await _validate_relation_values(
            relation={
                "target": "entry",
                "allow_cross_track": True,
                "skip_target_read": True,
                "target_entry_types": ["rec"],
            },
            value=target.id,
            source_track=source,
            field_key="link",
            viewer_subject=Subject(kind="human", id="writer-b"),
        )


async def test_create_revalidates_custom_fields_after_before_write_hooks(
    monkeypatch, test_user
):
    """Hook patches must pass canonical validation before persistence."""
    from app.models.nodes import Entry, EntryType, Track
    from app.services.entry_create import create_entry_in_track
    import app.services.entry_create as entry_create_mod
    from app.services.personal_workspace import ensure_personal_workspace

    ws = await ensure_personal_workspace(test_user)
    source, secret = await _wired_entry_pair(writer=test_user, workspace_id=ws.id)
    other_track = await Track.create(
        title="Other",
        owner_id="other-user",
        workspace_id=ws.id,
    )
    await catalog_track(other_track)
    et = await EntryType.create(
        name="Item",
        track_id=other_track.id,
        form_schema={"_manifest_entry_type_key": "item", "fields": []},
    )
    await other_track.connect(et, edge=CONTAINS)
    secret = await Entry.create(
        title="Secret",
        track_id=other_track.id,
        type_id=et.id,
        author_id="other-user",
    )
    await other_track.connect(secret, edge=CONTAINS)
    source_type = await EntryType.create(
        name="Source",
        track_id=source.id,
        form_schema={
            "_manifest_entry_type_key": "source",
            "fields": [
                {
                    "key": "link",
                    "type": "relation",
                    "relation": {
                        "target": "entry",
                        "allow_cross_track": True,
                        "target_entry_types": ["item"],
                    },
                }
            ],
        },
    )
    await source.connect(source_type, edge=CONTAINS)

    async def _poison(**kwargs):
        fields = dict(kwargs.get("custom_fields") or {})
        fields["link"] = secret.id
        return fields

    async def _deny_secret_read(*, subject, action, resource, **kwargs):
        if action == "entry.read" and str(resource.id) == secret.id:
            return Decision(allowed=False, reason="denied")
        return Decision(allowed=True, reason="ok")

    monkeypatch.setattr(
        entry_create_mod,
        "run_entry_before_write_hooks",
        AsyncMock(side_effect=_poison),
    )
    monkeypatch.setattr("app.services.policy_engine.evaluate", _deny_secret_read)

    with pytest.raises(BadRequestError, match="target read"):
        await create_entry_in_track(
            track=source,
            entry_type=source_type,
            title="Should fail",
            user_id=test_user.user_id,
            workspace_id=ws.id,
            custom_fields={},
            skip_profanity=True,
        )


async def test_many_relation_validates_every_target_id(monkeypatch, test_user):
    """Many-valued relations must authorize each target, not only the first."""
    from app.models.nodes import Entry, EntryType, Track
    from app.services.personal_workspace import ensure_personal_workspace

    ws = await ensure_personal_workspace(test_user)
    track = await Track.create(
        title="Many src",
        owner_id=test_user.user_id,
        workspace_id=ws.id,
    )
    await catalog_track(track)
    ttrack = await Track.create(
        title="Targets",
        owner_id=test_user.user_id,
        workspace_id=ws.id,
    )
    await catalog_track(ttrack)
    et = await EntryType.create(
        name="Rec",
        track_id=ttrack.id,
        form_schema={"_manifest_entry_type_key": "rec", "fields": []},
    )
    await ttrack.connect(et, edge=CONTAINS)
    readable = await Entry.create(
        title="Ok",
        track_id=ttrack.id,
        type_id=et.id,
        author_id=test_user.user_id,
    )
    blocked = await Entry.create(
        title="No",
        track_id=ttrack.id,
        type_id=et.id,
        author_id="other-user",
    )
    await ttrack.connect(readable, edge=CONTAINS)
    await ttrack.connect(blocked, edge=CONTAINS)
    entry_type = await EntryType.create(
        name="Holder",
        track_id=track.id,
        form_schema={
            "_manifest_entry_type_key": "holder",
            "fields": [
                {
                    "key": "items",
                    "type": "relation",
                    "readonly": True,
                    "relation": {
                        "target": "entry",
                        "many": True,
                        "allow_cross_track": True,
                        "target_entry_types": ["rec"],
                    },
                }
            ],
        },
    )
    await track.connect(entry_type, edge=CONTAINS)
    holder = await Entry.create(
        title="Holder row",
        track_id=track.id,
        type_id=entry_type.id,
        author_id=test_user.user_id,
        custom_fields={"items": [readable.id]},
    )
    await track.connect(holder, edge=CONTAINS)

    calls: list[str] = []

    async def _eval(*, subject, action, resource, **kwargs):
        calls.append(str(resource.id))
        if str(resource.id) == blocked.id:
            return Decision(allowed=False, reason="denied")
        return Decision(allowed=True, reason="ok")

    monkeypatch.setattr("app.services.policy_engine.evaluate", _eval)

    runtime_tier = {
        "entry_types": [
            {
                "key": "holder",
                "fields": entry_type.form_schema["fields"],
            }
        ]
    }

    with pytest.raises(BadRequestError, match="readonly"):
        await validate_and_materialize_entry_custom_fields(
            track=track,
            entry_type=entry_type,
            custom_fields={"items": [readable.id, blocked.id]},
            runtime_tier=runtime_tier,
            entry=holder,
            actor_user_id=test_user.user_id,
        )
