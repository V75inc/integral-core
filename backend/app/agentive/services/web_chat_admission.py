"""Unregistered admission adapter for already verified full-page sessions."""

from dataclasses import dataclass
from typing import Awaitable, Callable

from app.agentive.connectors.external_chat import (
    ExternalChatAssignment,
    ExternalChatConversation,
    VerifiedExternalChatMessage,
)
from app.agentive.services import public_web_chat
from app.agentive.services.connection_profiles import ProfileDenied


@dataclass(frozen=True)
class VerifiedFullPageSession:
    """Server-owned result of full-page visitor and conversation verification.

    A trusted, request-scoped host reader must supply this from the current
    verified session, not from a browser body or from event identifiers.
    """

    conversation: ExternalChatConversation
    scope: public_web_chat.PublicWebChatScope


class FullPageWebChatAdmission:
    """Bind an admitted visitor session to the current text-only assignment."""

    def __init__(
        self,
        read_verified_session: Callable[[], Awaitable[VerifiedFullPageSession | None]],
    ) -> None:
        """Require a request-scoped trusted reader; no session is minted here."""
        self._read_verified_session = read_verified_session

    async def text_only_assignment(
        self, message: VerifiedExternalChatMessage
    ) -> ExternalChatAssignment | None:
        """Return current authority for this exact session, or deny."""
        try:
            session = await self._read_verified_session()
            if (
                not isinstance(session, VerifiedFullPageSession)
                or not isinstance(session.conversation, ExternalChatConversation)
                or not isinstance(session.scope, public_web_chat.PublicWebChatScope)
            ):
                return None
            provision = session.conversation.provision
            scope = session.scope
            if (
                provision.connector_kind != "public_web_chat"
                or session.conversation != message.key.conversation
                or scope.workspace_id != provision.workspace_id
                or scope.visitor_id != message.verified_sender_id
            ):
                return None
            current = await public_web_chat.admit(
                scope.workspace_id,
                scope.visitor_id,
                transport_ready=await public_web_chat.transport_ready(
                    scope.workspace_id
                ),
                existing=scope,
                expected_provision_id=provision.provision_id,
            )
        except ProfileDenied:
            return None
        return ExternalChatAssignment(
            provision=provision,
            profile_key=current.profile_key,
            publication_revision=str(current.published_version),
            route_epoch=str(current.route_generation),
        )
