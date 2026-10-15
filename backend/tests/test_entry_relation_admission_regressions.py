"""Regression: relation admission must not bypass target read (Eldon review P1)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.api.errors import BadRequestError
from app.models.edges import (
    COLLABORATES_ON,
    CONTAINS,
    EXCLUDED_FROM,
    IS_MEMBER_OF,
    OWNS,
)
from app.models.nodes import App, Entry, EntryType, Track, User, Workspace
from app.schemas.policy import Resource, Subject
from app.services.app_graph import (
    catalog_app,
    catalog_track,
    catalog_user,
    catalog_workspace,
)
from app.services.operational_model_entry_fields import (
    _validate_relation_values,
    validate_and_materialize_entry_custom_fields,
)

pytestmark = pytest.mark.smoke


async def _relation_admission_graph():
    """Writer on source track; private target entry they cannot read."""
    owner = await User.create(user_id="rel-admission-owner")
    await catalog_user(owner)
    writer = await User.create(
        user_id="rel-admission-writer",
        email_verified=True,
        verified_email="writer@example.com",
    )
    await catalog_user(writer)
    workspace = await Workspace.create(name="RelAdmission", kind="organization")
    await catalog_workspace(workspace)
    await owner.connect(workspace, edge=IS_MEMBER_OF, role="owner")
    await writer.connect(workspace, edge=IS_MEMBER_OF, role="member")

    app = await App.create(name="App", workspace_id=workspace.id, visibility="private")
    await catalog_app(app)
    await owner.connect(app, edge=OWNS)
    await writer.connect(app, edge=COLLABORATES_ON, role="editor")

    source_track = await Track.create(
        title="Source", workspace_id=workspace.id, visibility="inherit"
    )
    await catalog_track(source_track)
    await app.connect(source_track, edge=CONTAINS)
    await owner.connect(source_track, edge=OWNS)
    await writer.connect(source_track, edge=COLLABORATES_ON, role="editor")

    hidden = await Entry.create(title="Hidden", track_id=source_track.id)
    await source_track.connect(hidden, edge=CONTAINS)
    await owner.connect(hidden, edge=COLLABORATES_ON, role="owner")
    await writer.connect(hidden, edge=EXCLUDED_FROM)

    readable = await Entry.create(title="Readable", track_id=source_track.id)
    await source_track.connect(readable, edge=CONTAINS)

    entry_type = await EntryType.create(
        name="Item",
        track_id=source_track.id,
        form_schema={
            "_manifest_entry_type_key": "item",
            "fields": [
                {
                    "key": "link",
                    "type": "relation",
                    "relation": {"target": "entry", "many": False},
                },
                {
                    "key": "links",
                    "type": "relation",
                    "readonly": True,
                    "relation": {"target": "entry", "many": True},
                },
            ],
        },
    )
    await source_track.connect(entry_type, edge=CONTAINS)

    return writer, source_track, entry_type, hidden, readable


@pytest.mark.asyncio
async def test_legacy_skip_target_read_flag_does_not_bypass_target_read():
    """OM ``skip_target_read`` must not disable entry.read checks (I-APP-05)."""
    _, source_track, _, hidden, _ = await _relation_admission_graph()
    subject = Subject(kind="human", id="rel-admission-writer")
    relation = {
        "target": "entry",
        "many": False,
        "allow_cross_track": True,
        "skip_target_read": True,
    }

    with patch(
        "app.services.policy_engine.evaluate",
        new=AsyncMock(return_value=SimpleNamespace(allowed=False, reason="denied")),
    ):
        with pytest.raises(BadRequestError, match="requires target read access"):
            await _validate_relation_values(
                relation=relation,
                value=hidden.id,
                source_track=source_track,
                field_key="link",
                viewer_subject=subject,
                enforce_target_read=True,
            )


@pytest.mark.asyncio
async def test_readonly_many_relation_rejects_appended_target():
    """Readonly many-valued relation must compare full id sets, not first element only."""
    writer, source_track, entry_type, hidden, readable = (
        await _relation_admission_graph()
    )
    existing = await Entry.create(
        title="Form",
        track_id=source_track.id,
        type_id=entry_type.id,
        custom_fields={"links": [readable.id]},
    )
    await source_track.connect(existing, edge=CONTAINS)

    with pytest.raises(BadRequestError, match="readonly"):
        await validate_and_materialize_entry_custom_fields(
            track=source_track,
            entry_type=entry_type,
            custom_fields={"links": [readable.id, hidden.id]},
            runtime_tier={},
            entry=existing,
            actor_user_id=writer.id,
            authorization_subject=Subject(kind="human", id=writer.id),
        )


@pytest.mark.asyncio
async def test_post_hook_custom_fields_revalidated_before_persist():
    """Simulates entry.create: hook patch with unreadable relation must fail revalidation."""
    writer, source_track, entry_type, hidden, _ = await _relation_admission_graph()
    subject = Subject(kind="human", id=writer.id)
    validated, _ = await validate_and_materialize_entry_custom_fields(
        track=source_track,
        entry_type=entry_type,
        custom_fields={},
        runtime_tier={},
        actor_user_id=writer.id,
        authorization_subject=subject,
    )
    hook_output = {**validated, "link": hidden.id}
    with pytest.raises(BadRequestError, match="requires target read access"):
        await validate_and_materialize_entry_custom_fields(
            track=source_track,
            entry_type=entry_type,
            custom_fields=hook_output,
            runtime_tier={},
            actor_user_id=writer.id,
            authorization_subject=subject,
        )
