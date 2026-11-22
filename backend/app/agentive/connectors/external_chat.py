"""Inert inbound chat channel types, separate from external agent runtimes.

Only a trusted host adapter may construct verified events. These types validate
shape and preserve scope; they do not verify adapters, grant authority, or serve
traffic. Do not deserialize any of them from an untrusted request.
"""

from dataclasses import dataclass


def _require_text(value: str, field: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field} must be a non-empty canonical string")


@dataclass(frozen=True)
class ExternalChatProvision:
    """Server-resolved workspace and provision boundary for a channel source."""

    workspace_id: str
    connector_kind: str
    provision_id: str

    def __post_init__(self) -> None:
        """Require non-empty keys without case-folding opaque identifiers."""
        _require_text(self.workspace_id, "workspace_id")
        _require_text(self.connector_kind, "connector_kind")
        _require_text(self.provision_id, "provision_id")


@dataclass(frozen=True)
class ExternalChatConversation:
    """Provider conversation identity namespaced by its exact provision."""

    provision: ExternalChatProvision
    external_conversation_id: str

    def __post_init__(self) -> None:
        """Reject missing or ambiguous conversation identifiers."""
        if not isinstance(self.provision, ExternalChatProvision):
            raise TypeError("provision must be a server-resolved scope")
        _require_text(self.external_conversation_id, "external_conversation_id")


@dataclass(frozen=True)
class ExternalChatEventKey:
    """Idempotency key for one normalized event in its conversation."""

    conversation: ExternalChatConversation
    external_event_id: str

    def __post_init__(self) -> None:
        """Require a stable opaque source event identifier."""
        if not isinstance(self.conversation, ExternalChatConversation):
            raise TypeError("conversation must be a scoped conversation")
        _require_text(self.external_event_id, "external_event_id")


@dataclass(frozen=True)
class VerifiedExternalChatMessage:
    """Text and normalized sender produced after host transport verification.

    Sender is an opaque channel identity, not an Integral user ID or permission
    grant. An event key reused with a different sender or text is a conflict,
    not a new turn; the admitting host must enforce that rule atomically.
    """

    key: ExternalChatEventKey
    verified_sender_id: str
    text: str

    def __post_init__(self) -> None:
        """Require a sender and meaningful text, preserving the submitted text."""
        if not isinstance(self.key, ExternalChatEventKey):
            raise TypeError("key must be a scoped event key")
        _require_text(self.verified_sender_id, "verified_sender_id")
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("text must not be blank")


@dataclass(frozen=True)
class ExternalChatAssignment:
    """Server-owned binding projection, never part of transport input.

    An assignment is not admission; its published revision and live authority
    must be resolved and rechecked by the trusted host around each operation.
    """

    provision: ExternalChatProvision
    profile_key: str
    publication_revision: str

    def __post_init__(self) -> None:
        """Require a specific profile and publication revision."""
        if not isinstance(self.provision, ExternalChatProvision):
            raise TypeError("provision must be a server-resolved scope")
        _require_text(self.profile_key, "profile_key")
        _require_text(self.publication_revision, "publication_revision")


@dataclass(frozen=True)
class ExternalChatReply:
    """Text correlated to an admitted event, with no independent destination.

    The host builds this from the actual authorized turn result. The adapter
    resolves its destination from the admitted conversation, not model output.
    """

    in_reply_to: ExternalChatEventKey
    text: str

    def __post_init__(self) -> None:
        """Preserve substantive reply text without permitting an empty delivery."""
        if not isinstance(self.in_reply_to, ExternalChatEventKey):
            raise TypeError("in_reply_to must be a scoped event key")
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("text must not be blank")
