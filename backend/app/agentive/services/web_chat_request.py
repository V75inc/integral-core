"""Unmounted request orchestration for verified full-page Web Chat sessions."""

import re
from dataclasses import dataclass
from typing import Awaitable, Callable

from app.agentive.connectors.external_chat import (
    ExternalChatAssignment,
    ExternalChatEventKey,
    ExternalChatReply,
    VerifiedExternalChatMessage,
)
from app.agentive.connectors.web_chat_turn import (
    WebChatClaim,
    WebChatTurnCoordinator,
    WebChatTurnStore,
    WebChatUnavailable,
)
from app.agentive.services.connection_profiles import ProfileDenied
from app.agentive.services.web_chat_admission import (
    FullPageWebChatAdmission,
    VerifiedFullPageSession,
)

_EVENT_ID = re.compile(r"[a-zA-Z0-9_-]{1,128}")
_TEXT_LIMIT = 1000


@dataclass(frozen=True)
class PreparedWebChatTurn:
    """Internal receipt; never return assignment, sender, or fence to a visitor."""

    message: VerifiedExternalChatMessage
    assignment: ExternalChatAssignment
    claim: WebChatClaim


class FullPageWebChatRequests:
    """Compose current verified session and durable turn reservation ports.

    Not an HTTP handler, credential verifier, readiness provider, model runner,
    or transport. A host constructs this per verified visitor request; there is
    no session or connector logic for untrusted input to select.
    """

    def __init__(
        self,
        read_verified_session: Callable[[], Awaitable[VerifiedFullPageSession | None]],
        store: WebChatTurnStore,
    ) -> None:
        """Require real authority and store ports; do not supply fallbacks."""
        self._read_session = read_verified_session
        self._store = store
        self._turns = WebChatTurnCoordinator(
            FullPageWebChatAdmission(read_verified_session), store
        )

    async def begin(self, event_id: str, text: str) -> PreparedWebChatTurn:
        """Reserve or replay one authorized text message in the verified session."""
        if (
            not isinstance(event_id, str)
            or _EVENT_ID.fullmatch(event_id) is None
            or not isinstance(text, str)
            or not 0 < len(text) <= _TEXT_LIMIT
            or not text.strip()
        ):
            raise ValueError("Invalid Web Chat message")
        try:
            session = await self._read_session()
            if not isinstance(session, VerifiedFullPageSession):
                raise WebChatUnavailable("Web Chat turn is unavailable")
            message = VerifiedExternalChatMessage(
                key=ExternalChatEventKey(session.conversation, event_id),
                verified_sender_id=session.scope.visitor_id,
                text=text,
            )
        except (ProfileDenied, AttributeError, TypeError, ValueError):
            raise WebChatUnavailable("Web Chat turn is unavailable") from None

        async def current_request_session() -> VerifiedFullPageSession | None:
            verified = await self._read_session()
            return verified if verified == session else None

        coordinator = WebChatTurnCoordinator(
            FullPageWebChatAdmission(current_request_session), self._store
        )
        assignment, claim = await coordinator.begin(message)
        return PreparedWebChatTurn(message, assignment, claim)

    async def finish(self, prepared: PreparedWebChatTurn, reply_text: str) -> None:
        """Record the exact authorized owner result; do not deliver it."""
        fence = prepared.claim.fence
        if prepared.claim.state != "claimed" or not fence:
            raise WebChatUnavailable("Web Chat turn is unavailable")
        await self._turns.finish(
            prepared.message,
            prepared.assignment,
            fence,
            ExternalChatReply(prepared.message.key, reply_text),
        )
