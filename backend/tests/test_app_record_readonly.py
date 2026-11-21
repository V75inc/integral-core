"""Normal record lifecycle under a deny-only App restriction."""

import pytest

from app.agentive.tooling.invoke import invoke_route_in_process
from app.api.entries import get_entry
from app.api.errors import BadRequestError
from app.models.edges import CONTAINS, HAS_APPLICATION_DEFINITION, OWNS
from app.models.nodes import App, ApplicationDefinition, Entry
from app.services.app_graph import catalog_app
from app.services.application_definitions import definition_fingerprint
from app.services.attachment_entry_write import (
    AttachmentEntryReadonlyError,
    attachment_entry_write,
)
from app.services.entry_create import create_entry_in_track
from app.services.entry_update import update_entry_in_track
from app.services.entry_write_check import check_entry_write
from app.services.permissions import get_user_node
from tests.test_entry_create_type_id import _bootstrap_track_with_type

pytestmark = [pytest.mark.asyncio, pytest.mark.smoke]


async def test_app_record_standard_fields_lock_after_state_change():
    user, track, entry_type = await _bootstrap_track_with_type(
        "record-readonly@example.com"
    )
    entry_type.form_schema = {
        "fields": [{"key": "phase", "name": "Phase", "type": "text"}]
    }
    await entry_type.save()
    app = await App.create(name="Record Review", workspace_id=track.workspace_id)
    await catalog_app(app)
    owner = await get_user_node(user)
    await owner.connect(app, edge=OWNS)
    await app.connect(track, edge=CONTAINS)
    manifest = {
        "app": {
            "protected_state": {
                "post": {"readonly_when": {"field": "phase", "equals": "closed"}}
            }
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

    entry = await create_entry_in_track(
        track=track,
        user_id=user,
        type_id=entry_type.id,
        title="Original title",
        custom_fields={"phase": "open"},
    )
    await update_entry_in_track(entry_id=entry.id, user_id=user, body="Reviewed note")
    async with attachment_entry_write(entry, user) as current:
        assert current.body == "Reviewed note"
    await update_entry_in_track(
        entry_id=entry.id, user_id=user, custom_fields={"phase": "closed"}
    )

    result = await invoke_route_in_process(
        get_entry,
        principal_id=user,
        scope=track.workspace_id,
        entry_id=entry.id,
    )
    assert result["entry"]["app_record_readonly"] is True
    assert not (
        await check_entry_write(user_id=user, entry_id=entry.id, title="New title")
    )["ok"]
    with pytest.raises(BadRequestError) as refused:
        await update_entry_in_track(entry_id=entry.id, user_id=user, body="New note")
    assert refused.value.details["error_code"] == "app_record_readonly"
    assert (await Entry.find_one({"id": entry.id})).body == "Reviewed note"
    with pytest.raises(AttachmentEntryReadonlyError):
        async with attachment_entry_write(entry, user):
            pytest.fail("A read-only rule must refuse before the attachment effect")
