"""Connector-owned full-page runtime; defaults to an inactive management package."""

from dataclasses import dataclass
from typing import Awaitable, Callable, Literal

from jvspatial.db.postgres import PostgresDB

from app.agentive.connectors.external_chat import (
    ExternalChatAssignment,
    ExternalChatProvision,
)
from app.agentive.services.web_chat_package import SLUG
from app.agentive.services.web_chat_profile_inference import TextModel
from app.agentive.services.web_chat_request_budget import WebChatBudgetPolicy
from app.connectors.channel_runtime import (
    ChannelOriginRoutes,
    ChannelRuntimeContribution,
)
from app.web.full_page_chat import is_canonical_web_chat_origin
from app.web.web_chat_client_composition import (
    ClientRequestBudget,
    compose_web_chat_client_api,
)
from app.web.web_chat_client_grants import PostgresWebChatClientGrants
from app.web.web_chat_client_sessions import (
    ClientSessionStartBudget,
    PostgresWebChatClientSessions,
)
from app.web.web_chat_embed_composition import EmbedBudget, compose_web_chat_embed
from app.web.web_chat_embed_policy import (
    ManagedWebChatEmbedPolicy,
    ReadCurrentEmbedPolicy,
)
from app.web.web_chat_embed_share import (
    ReadIssuedEmbedShare,
    create_managed_web_chat_embed_share_router,
)
from app.web.web_chat_host_assembly import (
    ManagedWebChatHostRoute,
    WebChatPlatform,
    _managed_route,
    assemble_web_chat_host,
)
from app.web.web_chat_management_assembly import build_web_chat_management_package
from app.web.web_chat_page_shell import (
    HostReady,
    PageContextReader,
    StandaloneWebChatAssets,
    create_web_chat_page_shell_router,
)

Transport = Literal["full-page", "embed", "client-api"]
ProfileServingAuthority = Callable[[Transport, ExternalChatAssignment], Awaitable[bool]]


@dataclass(frozen=True)
class WebChatEmbedServingConfiguration:
    """Independent reviewed embed origin, assets, capacity and current policy."""

    origin: str
    database: PostgresDB
    assets: StandaloneWebChatAssets
    host_ready: HostReady
    read_current_policy: ReadCurrentEmbedPolicy
    budget: EmbedBudget
    complete: TextModel
    read_issued_share: ReadIssuedEmbedShare | None = None


@dataclass(frozen=True)
class WebChatClientServingConfiguration:
    """Independent reviewed service-client origin, grant reader and budgets."""

    origin: str
    database: PostgresDB
    grants: PostgresWebChatClientGrants
    host_ready: HostReady
    start_budget: ClientSessionStartBudget
    request_budget: ClientRequestBudget
    complete: TextModel


@dataclass(frozen=True)
class WebChatServingConfiguration:
    """Explicit reviewed full-page host inputs, never inferred from a request."""

    served_origin: str
    platform: WebChatPlatform
    host_ready: HostReady
    database: PostgresDB
    assets: StandaloneWebChatAssets
    complete: TextModel
    budget: WebChatBudgetPolicy
    budget_timeout_seconds: float
    max_parallel_budget_acquisitions: int
    profile_authority: ProfileServingAuthority
    embed: WebChatEmbedServingConfiguration | None = None
    client_api: WebChatClientServingConfiguration | None = None


@dataclass(frozen=True)
class WebChatConnectorRuntime:
    """Neutral Core contribution plus private opt-in host operations."""

    contribution: ChannelRuntimeContribution
    reconcile: Callable[[ExternalChatProvision], Awaitable[bool]] | None = None
    page_context: PageContextReader | None = None


def _assignment(route: ManagedWebChatHostRoute) -> ExternalChatAssignment:
    return ExternalChatAssignment(
        route.provision, route.profile_key, str(route.published_version), route.epoch
    )


async def _profile_ready(
    config: WebChatServingConfiguration,
    transport: Transport,
    exact: ExternalChatAssignment,
    *,
    require_enabled: bool = True,
) -> bool:
    # A transport-specific profile service identity is supplied by the host.
    # Neither an authenticated manager nor a user's input can stand in for it.
    current = await _managed_route(exact.provision)
    return (
        current is not None
        and (current.enabled or not require_enabled)
        and _assignment(current) == exact
        and await config.profile_authority(transport, exact) is True
        and await _managed_route(exact.provision) == current
    )


class _PinnedPlatform:
    def __init__(self, config: WebChatServingConfiguration) -> None:
        self._config = config

    async def install_ready(self, workspace_id: str) -> bool:
        """Do not infer any public transport from administrative installation."""
        return False

    async def approved_ready_origin(self, route: ManagedWebChatHostRoute) -> str | None:
        """Require platform approval and global boundary readiness for one origin."""
        config = self._config
        if (
            await config.platform.approved_ready_origin(route) == config.served_origin
            and await config.host_ready(config.served_origin) is True
            and await _profile_ready(
                config, "full-page", _assignment(route), require_enabled=route.enabled
            )
        ):
            return config.served_origin
        return None


def compose_web_chat_connector(
    serving: WebChatServingConfiguration | None = None,
) -> WebChatConnectorRuntime:
    """Construct a connector package with no routes unless hosting is explicit.

    This does not mount or activate any server. A reviewed host chooses where
    to mount each route group and owns reconciliation after durable commands.
    """
    if serving is None:
        return WebChatConnectorRuntime(
            ChannelRuntimeContribution(build_web_chat_management_package())
        )
    if (
        not isinstance(serving, WebChatServingConfiguration)
        or not is_canonical_web_chat_origin(serving.served_origin)
        or not callable(serving.host_ready)
        or not callable(serving.profile_authority)
        or (
            serving.embed is not None
            and not isinstance(serving.embed, WebChatEmbedServingConfiguration)
        )
        or (
            serving.client_api is not None
            and not isinstance(serving.client_api, WebChatClientServingConfiguration)
        )
        or not callable(getattr(serving.platform, "approved_ready_origin", None))
        or not isinstance(serving.assets, StandaloneWebChatAssets)
    ):
        raise TypeError(
            "Web Chat serving requires explicit reviewed host configuration"
        )
    origins = [serving.served_origin]
    for transport_config in (serving.embed, serving.client_api):
        if transport_config is not None:
            if not callable(transport_config.host_ready):
                raise TypeError("Web Chat requires independent transport readiness")
            origins.append(transport_config.origin)
    if not all(is_canonical_web_chat_origin(origin) for origin in origins) or len(
        set(origins)
    ) != len(origins):
        raise ValueError("Web Chat transports require distinct approved HTTPS origins")

    hosted = assemble_web_chat_host(
        database=serving.database,
        platform=_PinnedPlatform(serving),
        complete=serving.complete,
        budget_policy=serving.budget,
        budget_timeout_seconds=serving.budget_timeout_seconds,
        max_parallel_budget_acquisitions=serving.max_parallel_budget_acquisitions,
    )
    package = hosted.registry.resolve(SLUG)
    if package is None:
        raise RuntimeError("Web Chat host contribution unavailable")
    shell = create_web_chat_page_shell_router(
        served_origin=serving.served_origin,
        host_ready=serving.host_ready,
        page_context=hosted.page_context,
        assets=serving.assets,
    )
    management = [hosted.share_router]
    isolated = []
    embed = serving.embed
    if embed is not None:
        if not callable(embed.read_current_policy):
            raise TypeError("Web Chat embed requires independent policy authority")

        async def read_embed(reference: str) -> ManagedWebChatEmbedPolicy | None:
            policy = await embed.read_current_policy(reference)
            if (
                not isinstance(policy, ManagedWebChatEmbedPolicy)
                or not policy.matches(origin=embed.origin, path=f"/embed/{reference}")
                or await embed.host_ready(embed.origin) is not True
            ):
                return None
            exact = ExternalChatAssignment(
                policy.provision,
                policy.profile_key,
                str(policy.published_version),
                policy.route_epoch,
            )
            if not await _profile_ready(serving, "embed", exact):
                return None
            return (
                policy if await embed.read_current_policy(reference) == policy else None
            )

        embedded = compose_web_chat_embed(
            origin=embed.origin,
            database=embed.database,
            assets=embed.assets,
            host_ready=embed.host_ready,
            read_current_policy=read_embed,
            budget=embed.budget,
            complete=embed.complete,
        )
        isolated.append(
            ChannelOriginRoutes(
                embed.origin, (embedded.page_router, embedded.api_router)
            )
        )
        if embed.read_issued_share is not None:
            management.append(
                create_managed_web_chat_embed_share_router(
                    read_issued=embed.read_issued_share,
                    read_current=read_embed,
                )
            )

    client = serving.client_api
    if client is not None:

        async def client_ready(exact: ExternalChatAssignment) -> bool:
            return await client.host_ready(
                client.origin
            ) is True and await _profile_ready(serving, "client-api", exact)

        sessions = PostgresWebChatClientSessions(
            database=client.database,
            grants=client.grants,
            start_budget=client.start_budget,
        )
        api = compose_web_chat_client_api(
            origin=client.origin,
            database=client.database,
            grants=client.grants,
            sessions=sessions,
            request_budget=client.request_budget,
            complete=client.complete,
            assignment_ready=client_ready,
        )
        isolated.append(ChannelOriginRoutes(client.origin, (api,)))

    return WebChatConnectorRuntime(
        ChannelRuntimeContribution(
            package,
            management_routes=tuple(management),
            visitor_routes=(shell, hosted.browser_router),
            isolated_routes=tuple(isolated),
        ),
        reconcile=hosted.reconcile,
        page_context=hosted.page_context,
    )
