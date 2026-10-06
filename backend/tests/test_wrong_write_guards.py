"""Guards that stop a card the server will refuse or that is the wrong job."""

import json

import pytest

from app.agentive.tooling.bindings import (
    _app_id_for_single_track_create,
    _get_related_params,
    _list_tracks_params,
    _stage_create_app_track,
    _stage_create_entry,
    _stage_propose_profile_revision,
    is_cleanup_instruction,
)
from app.agentive.tooling.stagers_filing import stage_file_content
from app.agentive.tooling.stagers_scheduling import (
    is_texting_instruction,
    stage_schedule_task,
)
from app.api.errors import BadRequestError
from app.services.agent_profile_patches import apply_operations
from app.services.agent_scope import current_focused_app_id, current_page_context
from app.services.turn_binding import (
    add_track_title,
    events_with_bound_reply,
    related_reply,
    tag_count_reply,
    texting_turn_reply,
)

_APP = "n.WorkspaceApp.dcc482614def4a5bad99b9c2"
_MANIFEST = {
    "scope": "track",
    "track": {
        "entry_types": [
            {
                "key": "customer",
                "name": "Customer",
                "fields": [{"key": "phone", "name": "Phone", "type": "text"}],
            }
        ]
    },
}


def test_single_add_track_uses_the_focused_app_instead_of_the_batch_token():
    token = current_focused_app_id.set(_APP)
    try:
        staged = _stage_create_app_track(
            {"app_id": "Car Rental Desk", "name": "Maintenance"}
        )
    finally:
        current_focused_app_id.reset(token)
    assert staged["payload"]["app_id"] == _APP
    assert "{{app.id}}" not in staged["payload"]["app_id"]


def test_focused_app_replaces_a_different_real_app_id():
    other = "n.WorkspaceApp.b58efb20fc6947ed90921934"
    token = current_focused_app_id.set(_APP)
    try:
        assert _app_id_for_single_track_create(other) == _APP
        listed = _list_tracks_params({"app_id": other, "limit": 20})
    finally:
        current_focused_app_id.reset(token)
    assert listed["app_id"] == _APP
    assert listed["limit"] == 20


def test_list_tracks_keeps_a_real_app_when_nothing_is_focused():
    other = "n.WorkspaceApp.b58efb20fc6947ed90921934"
    token = current_focused_app_id.set(None)
    try:
        listed = _list_tracks_params({"app_id": other})
    finally:
        current_focused_app_id.reset(token)
    assert listed["app_id"] == other


def test_get_related_uses_the_open_entry_when_the_call_omits_one():
    open_entry = "n.Entry.709543c558ef4b6282f6bd2c"
    token = current_page_context.set({"focused_entry_id": open_entry})
    try:
        omitted = _get_related_params({})
        named = _get_related_params({"entry_id": "n.Entry.otherid"})
    finally:
        current_page_context.reset(token)
    assert omitted["entry_id"] == open_entry
    assert named["entry_id"] == "n.Entry.otherid"


def test_tag_count_reply_names_zeros_and_replaces_the_paraphrase():
    sentence = tag_count_reply(
        [
            {"label": "Economy", "count": 1},
            {"label": "SUV", "count": 0},
            {"label": "Van", "count": 0},
        ]
    )
    assert sentence == "Economy 1, SUV 0, Van 0"
    events = events_with_bound_reply(
        [
            {"type": "text-delta", "delta": "None of them are tagged."},
            {"type": "tool-call", "name": "integral_count_entries"},
        ],
        sentence,
    )
    texts = [
        event.get("delta") for event in events if event.get("type") == "text-delta"
    ]
    assert texts == [sentence]
    assert "None of them" not in json.dumps(events)


def test_focused_turns_name_the_object_and_ignore_a_new_app():
    from app.services.focused_additions import anchor_blueprint
    from app.services.turn_binding import (
        anchor_template_name,
        is_design_correction,
        is_of_those_followup,
        is_protected_return,
    )

    prompt = (
        "On Car Rental Desk, each vehicle should have its own "
        "service-log track, one log per vehicle, not one shared log."
    )
    assert anchor_template_name(prompt) == "Service Log"
    assert anchor_template_name("Add a Maintenance track to this app.") is None
    blueprint = anchor_blueprint("Service Log")
    assert blueprint["track_templates"][0]["name"] == "Service Log"
    assert blueprint["views"][0]["id"] == "extra_board"
    assert is_protected_return(
        "When a rental is returned, record a protected return step "
        "that a package would perform."
    )
    assert is_design_correction("No, service logs only, drop anything else you added.")
    assert is_of_those_followup("Of those Economy vehicles, which have a daily rate?")


def test_add_track_names_the_track_and_ignores_a_new_app():
    assert add_track_title("Add a Maintenance track to this app.") == "Maintenance"
    assert add_track_title("Create a car rental business") is None
    assert add_track_title("add a track") is None


def test_a_texting_request_is_the_package_sentence():
    reply = texting_turn_reply(
        "When a rental is due back, text the customer automatically."
    )
    assert reply is not None
    assert "trusted package" in reply
    assert texting_turn_reply("Remind me the day before the rental is due") is None


def test_related_reply_names_the_connected_entries():
    assert (
        related_reply([{"title": "Jane Doe"}, {"title": "Toyota Corolla"}])
        == "Connected to Jane Doe and Toyota Corolla"
    )


def test_single_add_track_refuses_when_no_real_app_is_in_focus():
    token = current_focused_app_id.set(None)
    try:
        with pytest.raises(ValueError, match="was not staged"):
            _app_id_for_single_track_create("{{app.id}}")
    finally:
        current_focused_app_id.reset(token)


def test_modify_field_aliases_rename_the_display_name():
    updated = apply_operations(
        _MANIFEST,
        [
            {
                "op": "modify_field",
                "entry_type_key": "customer",
                "field": "phone",
                "name": "Mobile",
            }
        ],
    )
    field = updated["track"]["entry_types"][0]["fields"][0]
    assert field["key"] == "phone"
    assert field["name"] == "Mobile"


def test_incomplete_modify_field_is_refused_before_a_card():
    with pytest.raises(BadRequestError, match="was not staged"):
        _stage_propose_profile_revision(
            {"draft_id": "draft-1", "operations": [{"op": "modify_field"}]}
        )


def test_staged_modify_field_keeps_the_canonical_shape():
    staged = _stage_propose_profile_revision(
        {
            "draft_id": "draft-1",
            "operations": [
                {
                    "op": "modify_field",
                    "entry_type_key": "customer",
                    "field": {"key": "phone", "name": "Mobile"},
                }
            ],
        }
    )
    op = staged["payload"]["operations"][0]
    assert op["entry_type"] == "customer"
    assert op["field_key"] == "phone"
    assert op["patch"]["name"] == "Mobile"
    assert "key" not in op["patch"]


def test_a_key_change_is_not_a_patch():
    with pytest.raises(BadRequestError, match="field_key"):
        apply_operations(
            _MANIFEST,
            [
                {
                    "op": "modify_field",
                    "entry_type": "customer",
                    "field_key": "phone",
                    "patch": {"key": "mobile"},
                }
            ],
        )


@pytest.mark.asyncio
async def test_cleanup_sentence_is_not_staged_as_a_new_entry():
    assert is_cleanup_instruction("Clean up duplicate appointments for Sandy")
    with pytest.raises(ValueError, match="Nothing was staged"):
        await _stage_create_entry(
            {
                "track_id": "n.Track.appointments",
                "title": "Clean up duplicate appointments for Sandy",
            }
        )
    filed = await stage_file_content(
        {
            "text": "Keep only Sandy's October appointment and delete the duplicates",
            "track_id": "n.Track.appointments",
            "type_hint": "Appointment",
            "title": "Sandy",
        }
    )
    assert filed["_no_stage"] is True
    assert filed["data"]["filing_status"] == "error"
    assert "integral_delete_entry" in filed["data"]["message"]


def test_a_reminder_is_not_a_texting_routine():
    assert is_texting_instruction("Text the customer that the car is due")
    assert is_texting_instruction("Send an SMS when the rental is overdue")
    assert not is_texting_instruction("Remind me the day before the rental is due")


@pytest.mark.asyncio
async def test_texting_routine_is_refused_before_a_card():
    with pytest.raises(ValueError, match="trusted package"):
        await stage_schedule_task(
            {
                "instruction": "Send a text when a rental is due back",
                "cron": "0 9 * * *",
            }
        )
