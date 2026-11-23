"""Connector-owned full-page runtime; defaults to an inactive management package."""

from dataclasses import dataclass
from typing import Awaitable, Callable

from jvspatial.db.postgres import PostgresDB

from app.agentive.connectors.external_chat import ExternalChatProvision
from app.agentive.services.web_chat_package import SLUG
from app.agentive.services.web_chat_profile_inference import TextModel
from app.agentive.services.web_chat_request_budget import WebChatBudgetPolicy
from app.connectors.channel_runtime import ChannelRuntimeContribution
from app.web.full_page_chat import is_canonical_web_chat_origin
from app.web.web_chat_host_assembly import (
    ManagedWebChatHostRoute,
    WebChatPlatform,
    assemble_web_chat_host,
)
from app.web.web_chat_management_assembly import build_web_chat_management_package
from app.web.web_chat_page_shell import (
    HostReady,
    PageContextReader,
    StandaloneWebChatAssets,
    create_web_chat_page_shell_router,
)


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


@dataclass(frozen=True)
class WebChatConnectorRuntime:
    """Neutral Core contribution plus private opt-in host operations."""

    contribution: ChannelRuntimeContribution
    reconcile: Callable[[ExternalChatProvision], Awaitable[bool]] | None = None
    page_context: PageContextReader | None = None


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
        or not callable(getattr(serving.platform, "approved_ready_origin", None))
        or not isinstance(serving.assets, StandaloneWebChatAssets)
    ):
        raise TypeError(
            "Web Chat serving requires explicit reviewed host configuration"
        )
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
    return WebChatConnectorRuntime(
        ChannelRuntimeContribution(
            package,
            management_routes=(hosted.share_router,),
            visitor_routes=(shell, hosted.browser_router),
        ),
        reconcile=hosted.reconcile,
        page_context=hosted.page_context,
    )
