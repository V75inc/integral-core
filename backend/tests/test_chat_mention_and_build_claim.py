"""# mentions must validate, and a question is not a build-completion claim."""

from app.api.ai_chat import _claims_build_completion
from app.schemas.api.ai_chat import SendMessageRequest
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
    assert body.entity_refs[0].token == "#Personal Expenses"


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
