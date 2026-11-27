import pytest

from app.agentive.connectors.external_chat import (
    ExternalChatAssignment,
    ExternalChatConversation,
    ExternalChatEventKey,
    ExternalChatProvision,
    ExternalChatReply,
    VerifiedExternalChatMessage,
)


def event_key(workspace="first", kind="web-chat", provision="one", conversation="room"):
    source = ExternalChatProvision(workspace, kind, provision)
    return ExternalChatEventKey(ExternalChatConversation(source, conversation), "42")


def test_event_identity_cannot_cross_workspace_connector_provision_or_conversation():
    keys = {
        event_key(),
        event_key(workspace="second"),
        event_key(kind="other"),
        event_key(provision="two"),
        event_key(conversation="different"),
    }
    assert len(keys) == 5


def test_replay_has_same_key_and_conflicting_content_does_not_create_new_identity():
    original = VerifiedExternalChatMessage(event_key(), "sender", "Hello!")
    retried = VerifiedExternalChatMessage(event_key(), "sender", "Hello!")
    changed_text = VerifiedExternalChatMessage(event_key(), "sender", "Different")
    changed_sender = VerifiedExternalChatMessage(event_key(), "another", "Hello!")
    assert original == retried
    assert original != changed_text != changed_sender
    assert {item.key for item in (original, retried, changed_text, changed_sender)} == {
        original.key
    }


def test_assignment_is_host_owned_and_reply_has_only_admitted_event_destinations():
    key = event_key()
    message = VerifiedExternalChatMessage(key, "sender", " Hello!\n")
    assignment = ExternalChatAssignment(
        key.conversation.provision, "profile", "7", "epoch-1"
    )
    reply = ExternalChatReply(message.key, "Welcome.")
    assert message.text == " Hello!\n"
    assert assignment.provision == reply.in_reply_to.conversation.provision
    assert not hasattr(message, "profile_key")
    assert not hasattr(message, "voice_model_id")
    assert not hasattr(reply, "recipient_id")


@pytest.mark.parametrize("invalid", ["", " ", " key ", None])
def test_bad_opaque_boundary_identifiers_are_rejected(invalid):
    with pytest.raises(ValueError, match="workspace_id"):
        ExternalChatProvision(invalid, "web-chat", "one")
    with pytest.raises(ValueError, match="external_event_id"):
        ExternalChatEventKey(event_key().conversation, invalid)
    with pytest.raises(ValueError, match="verified_sender_id"):
        VerifiedExternalChatMessage(event_key(), invalid, "Hello!")


def test_raw_objects_cannot_replace_nested_scoped_identities():
    with pytest.raises(TypeError, match="server-resolved scope"):
        ExternalChatConversation({"workspace_id": "fake"}, "room")
    with pytest.raises(TypeError, match="scoped event key"):
        ExternalChatReply("unscoped-recipient", "Hello!")
