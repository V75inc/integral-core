"""Shared turn composition for already admitted, non-cookie visitor credentials."""

from typing import Awaitable, Callable, Literal

from jvspatial.db.postgres import PostgresDB

from app.agentive.connectors.web_chat_turn import WebChatTurnStore
from app.agentive.services.web_chat_admission import VerifiedFullPageSession
from app.agentive.services.web_chat_browser_transport import (
    BrowserSessionAccess,
    BrowserVisitorSessions,
    BrowserWebChatController,
)
from app.agentive.services.web_chat_history import PostgresWebChatHistory
from app.agentive.services.web_chat_profile_inference import TextModel

Operation = Literal["read", "send"]
Budget = Callable[[Operation], Awaitable[bool]]
Current = Callable[[], Awaitable[bool]]


def bind_verified_web_chat_conversation(
    *,
    database: PostgresDB,
    turns: WebChatTurnStore,
    visitor: VerifiedFullPageSession,
    current: Current,
    budget: Budget,
    complete: TextModel,
    expose_conflict: bool = False,
) -> BrowserWebChatController:
    """Reuse text-only inference without any authority to mint or rotate."""
    provision = visitor.conversation.provision

    async def read_verified() -> VerifiedFullPageSession | None:
        return visitor if await current() is True else None

    async def read_session() -> BrowserSessionAccess:
        authorized = await read_verified()
        if authorized is None:
            return BrowserSessionAccess("expired")
        return BrowserSessionAccess("current", authorized)

    async def resume(mode: str) -> BrowserSessionAccess:
        if mode != "resume":
            return BrowserSessionAccess("unavailable")
        return await read_session()

    async def ready(workspace: str) -> bool:
        return workspace == provision.workspace_id and await current() is True

    async def metered(operation, verified_provision, session):
        if verified_provision != provision or await current() is not True:
            return False
        if operation == "send" and session == visitor:
            return await budget("send")
        if operation == "start" and session is None:
            return await budget("read")
        return False

    history = PostgresWebChatHistory(
        database=database, read_verified_session=read_verified, readiness=ready
    )
    return BrowserWebChatController(
        provision,
        BrowserVisitorSessions(open=resume, current=read_session),
        metered,
        history.ports,
        turns,
        complete,
        readiness=ready,
        expose_conflict=expose_conflict,
    )
