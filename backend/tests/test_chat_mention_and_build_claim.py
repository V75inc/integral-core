"""# mentions must validate, and a question is not a build-completion claim."""

import asyncio
from types import SimpleNamespace

from app.api.ai_chat import (
    _claims_build_completion,
    complete_cut_design_invitation,
    uploaded_image_context_note,
)
from app.schemas.api.ai_chat import SendMessageRequest
from app.services import chat_entity_refs as entity_refs
from app.services.operational_model_compile import relation_allows_cross_track


def test_hash_mention_with_token_is_a_valid_message() -> None:
    body = SendMessageRequest.model_validate(
        {
            "text": "Please delete the #Personal Expenses app",
            "entity_refs": [
                {
                    "kind": "app",
                    "id": "n.App.abc",
                    "label": "Personal Expenses",
                    "token": "#Personal Expenses",
                }
            ],
        }
    )
    assert body.entity_refs is not None
    assert body.entity_refs[0].kind == "app"
    assert body.entity_refs[0].token == "#Personal Expenses"


def test_dict_entity_ref_is_read_as_an_object(monkeypatch) -> None:
    seen = {}

    async def _capture(ref, _user_id, _workspace_id):
        seen["kind"] = ref.kind
        seen["token"] = ref.token
        return None

    monkeypatch.setattr(entity_refs, "_validate_explicit_ref", _capture)
    result = asyncio.run(
        entity_refs.resolve_entity_refs(
            "describe this app",
            [
                {
                    "kind": "app",
                    "id": "n.App.abc",
                    "label": "Personal Expenses",
                    "token": "#Personal Expenses",
                }
            ],
            "user-1",
        )
    )
    assert seen == {"kind": "app", "token": "#Personal Expenses"}
    assert result.resolved == []


def test_a_clipped_design_invitation_is_finished() -> None:
    text = "Proposed — nothing has been built.\n\nTracks\n\nPlease confirm or"
    assert complete_cut_design_invitation(text).endswith(
        "Confirm this design, or tell me what to change."
    )
    assert complete_cut_design_invitation("Hello there.") == "Hello there."


def test_a_design_turn_does_not_tell_the_model_to_attach_the_image() -> None:
    image = SimpleNamespace(content_type="image/jpeg")
    note = uploaded_image_context_note(
        [image], ["abc123"], design_only=True
    )
    assert "id=abc123" in note
    assert "Do not create an entry" in note
    assert "entry_id" not in note

    filing = uploaded_image_context_note(
        [image], ["abc123"], design_only=False
    )
    assert 'entry_id="{{entry.id}}"' in filing


def test_question_about_a_partial_build_is_not_a_completion_claim() -> None:
    text = (
        "The build was partially successful, but there was an error. "
        "Which approach would you prefer?"
    )
    assert _claims_build_completion(text) is False
    assert _claims_build_completion("Your app is ready and the build is complete.")


def test_named_target_track_allows_a_cross_track_relation() -> None:
    assert relation_allows_cross_track(
        {"target_track_types": ["businesses"], "allow_cross_track": False}
    )
    assert (
        relation_allows_cross_track(
            {"target_entry_types": ["page"], "allow_cross_track": False}
        )
        is False
    )
