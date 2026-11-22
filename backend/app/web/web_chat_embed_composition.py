"""Join detached embed shell, credentials, and tool-free visitor turns."""

import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Literal

from fastapi.routing import APIRouter
from jvspatial.db.postgres import PostgresDB

from app.agentive.connectors.external_chat import ExternalChatConversation
from app.agentive.connectors.web_chat_turn_store import JvSpatialWebChatTurnStore
from app.agentive.services.public_web_chat import PublicWebChatScope
from app.agentive.services.web_chat_admission import VerifiedFullPageSession
from app.agentive.services.web_chat_profile_inference import TextModel
from app.web.web_chat_embed_credentials import (
    EmbedVisitorContext,
    PostgresWebChatEmbedCredentials,
)
from app.web.web_chat_embed_http import (
    BoundEmbedSession,
    IssuedEmbedSession,
    create_web_chat_embed_router,
)
from app.web.web_chat_embed_page_shell import (
    HostReady,
    create_web_chat_embed_page_router,
)
from app.web.web_chat_embed_policy import (
    ManagedWebChatEmbedPolicy,
    ReadCurrentEmbedPolicy,
)
from app.web.web_chat_page_shell import StandaloneWebChatAssets
from app.web.web_chat_verified_conversation import (
    Operation,
    bind_verified_web_chat_conversation,
)

EmbedOperation = Literal["page", "start", "read", "send"]
EmbedBudget = Callable[
    [EmbedOperation, ManagedWebChatEmbedPolicy, EmbedVisitorContext | None],
    Awaitable[bool],
]


@dataclass(frozen=True)
class WebChatEmbedAssembly:
    """Outputs for a trusted embed host; neither is mounted on construction."""

    page_router: APIRouter
    api_router: APIRouter


def compose_web_chat_embed(
    *,
    origin: str,
    database: PostgresDB,
    assets: StandaloneWebChatAssets,
    host_ready: HostReady,
    read_current_policy: ReadCurrentEmbedPolicy,
    budget: EmbedBudget,
    complete: TextModel,
) -> WebChatEmbedAssembly:
    """Compose the approved origin's embed without relying on browser storage.

    The caller provides the real host and current assignment/policy authority,
    plus separate bounded public database, budget and model capacity. No port
    can be supplied from browser input or inherited from an authenticated
    workspace session.
    """
    if not all(
        callable(port) for port in (host_ready, read_current_policy, budget, complete)
    ):
        raise TypeError("Web Chat embed requires explicit host ports")

    async def resolve(reference: str) -> ManagedWebChatEmbedPolicy | None:
        if await host_ready(origin) is not True:
            return None
        current = await read_current_policy(reference)
        if isinstance(current, ManagedWebChatEmbedPolicy) and current.matches(
            origin=origin, path=f"/embed/{reference}"
        ):
            return current
        return None

    credentials = PostgresWebChatEmbedCredentials(
        database=database, read_current_policy=resolve
    )
    turns = JvSpatialWebChatTurnStore(database=database)

    def credentials_for(policy: ManagedWebChatEmbedPolicy):
        return credentials.bind(
            policy=policy,
            served_origin=origin,
            raw_path=f"/embed/{policy.embed_reference}",
        )

    async def issue(policy: ManagedWebChatEmbedPolicy) -> str | None:
        if await budget("page", policy, None) is not True:
            return None
        return await credentials_for(policy).issue_seed()

    async def exchange(
        policy: ManagedWebChatEmbedPolicy, seed: str
    ) -> IssuedEmbedSession | None:
        if await budget("start", policy, None) is not True:
            return None
        bound = credentials_for(policy)
        result = await bound.consume_seed(seed)
        if result is None or result.visitor.policy != policy:
            return None

        async def current() -> bool:
            return await bound.current(result.credential) == result.visitor

        return IssuedEmbedSession(
            policy=policy,
            credential=result.credential,
            expires_in_seconds=result.visitor.expires_at - int(time.time()),
            is_current=current,
        )

    async def bind(
        policy: ManagedWebChatEmbedPolicy, token: str
    ) -> BoundEmbedSession | None:
        bound = credentials_for(policy)
        exact = await bound.current(token)
        if exact is None or exact.policy != policy:
            return None
        visitor = VerifiedFullPageSession(
            ExternalChatConversation(policy.provision, exact.conversation_id),
            PublicWebChatScope(
                policy.provision.workspace_id,
                exact.visitor_id,
                policy.profile_key,
                policy.published_version,
                int(policy.route_epoch),
            ),
        )

        async def current() -> bool:
            return await bound.current(token) == exact

        async def metered(operation: Operation) -> bool:
            return await budget(operation, policy, exact)

        controller = bind_verified_web_chat_conversation(
            database=database,
            turns=turns,
            visitor=visitor,
            current=current,
            budget=metered,
            complete=complete,
        )

        async def read():
            return await controller.start("resume")

        return BoundEmbedSession(
            policy=policy,
            expires_in_seconds=exact.expires_at - int(time.time()),
            is_current=current,
            read=read,
            send=controller.send,
        )

    return WebChatEmbedAssembly(
        page_router=create_web_chat_embed_page_router(
            served_origin=origin,
            host_ready=host_ready,
            read_current=resolve,
            issue_seed=issue,
            assets=assets,
        ),
        api_router=create_web_chat_embed_router(
            resolve_policy=resolve,
            exchange=exchange,
            bind=bind,
        ),
    )
