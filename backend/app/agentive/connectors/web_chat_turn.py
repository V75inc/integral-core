"""Non-serving full-page Web Chat coordination over trusted host ports.

No runtime registers this coordinator. The ports are not product implementations.
"""

from dataclasses import dataclass
from typing import Literal, Protocol

from app.agentive.connectors.external_chat import (
    ExternalChatAssignment,
    ExternalChatEventKey,
    ExternalChatReply,
    VerifiedExternalChatMessage,
)


@dataclass(frozen=True)
class WebChatClaim:
    """Outcome of a host's atomic admission and idempotency check."""

    state: Literal["claimed", "in_progress", "replayed", "conflict"]
    fence: str | None = None
    reply: ExternalChatReply | None = None


class WebChatTurnStore(Protocol):
    """Durable shared reservation and terminal receipt boundary.

    Match exact key, verified sender, text, and assignment on replay.
    A changed sender, text, revision, or route epoch for the same key conflicts. Only the
    initial claimant receives a fence. Consuming a fence is atomic; awaiting or
    failing a call must not release it to an uncoordinated second execution.
    """

    async def claim(
        self,
        message: VerifiedExternalChatMessage,
        assignment: ExternalChatAssignment,
    ) -> WebChatClaim:
        """Atomically reserve or read the outcome for this exact admitted event."""
        ...

    async def finish(
        self,
        key: ExternalChatEventKey,
        assignment: ExternalChatAssignment,
        fence: str,
        reply: ExternalChatReply,
    ) -> bool:
        """Write a reply once for the current reservation fence."""
        ...


class WebChatHostAdmission(Protocol):
    """Trusted host verification for one exact sender, scope, and conversation."""

    async def text_only_assignment(
        self, message: VerifiedExternalChatMessage
    ) -> ExternalChatAssignment | None:
        """Resolve fresh authority; deny unknown, disabled, or tool-enabled routes.

        Require a server-established visitor session, current provision, current
        publication, route epoch, and a permitted conversation for this sender.
        """
        ...


class WebChatUnavailable(Exception):
    """The server cannot safely admit or return this message's outcome."""


class WebChatTurnCoordinator:
    """Coordinate durable reservation and result readback, without execution."""

    def __init__(
        self, admission: WebChatHostAdmission, store: WebChatTurnStore
    ) -> None:
        """Keep verification and storage explicit; no fallback can serve traffic."""
        self._admission = admission
        self._store = store

    async def _current(
        self, message: VerifiedExternalChatMessage
    ) -> ExternalChatAssignment:
        assignment = await self._admission.text_only_assignment(message)
        if assignment is None or assignment.provision != (
            message.key.conversation.provision
        ):
            raise WebChatUnavailable("Web Chat turn is unavailable")
        return assignment

    async def begin(
        self, message: VerifiedExternalChatMessage
    ) -> tuple[ExternalChatAssignment, WebChatClaim]:
        """Reserve one turn or return the exact authorized replay/pending/conflict."""
        assignment = await self._current(message)
        claim = await self._store.claim(message, assignment)
        if await self._current(message) != assignment:
            raise WebChatUnavailable("Web Chat turn is unavailable")
        if claim.state == "claimed" and claim.fence and claim.reply is None:
            return assignment, claim
        if claim.state in {"in_progress", "conflict"} and (
            claim.fence is None and claim.reply is None
        ):
            return assignment, claim
        if (
            claim.state == "replayed"
            and claim.fence is None
            and claim.reply is not None
            and claim.reply.in_reply_to == message.key
        ):
            return assignment, claim
        raise WebChatUnavailable("Web Chat turn is unavailable")

    async def finish(
        self,
        message: VerifiedExternalChatMessage,
        assignment: ExternalChatAssignment,
        fence: str,
        reply: ExternalChatReply,
    ) -> None:
        """Persist an exact reply without returning anything for delivery."""
        if (
            not fence
            or reply.in_reply_to != message.key
            or await self._current(message) != assignment
        ):
            raise WebChatUnavailable("Web Chat turn is unavailable")
        if not await self._store.finish(message.key, assignment, fence, reply):
            raise WebChatUnavailable("Web Chat turn is unavailable")
