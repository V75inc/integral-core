"""Current canonical location of an owned conversation, without repairing it."""

from typing import Any

from jvspatial.core.context import get_default_context
from jvspatial.core.decorators import on_visit
from pydantic import PrivateAttr

from app.api.errors import ResourceNotFoundError
from app.exceptions import AuthorizationUnavailableError
from app.models.edges import CATALOGS, CONTAINS
from app.models.nodes import ChatThread, ChatThreads, Workspace
from app.services.authority_facts import required_node_text
from app.services.resource_location import ResourceLocationWalker


def _workspace_cache(row: dict) -> str:
    if "workspace_id" not in row["context"] and "workspace_id" not in row:
        return ""
    return required_node_text(row, "workspace_id", allow_empty=True)


class _ChatHistoryLocationWalker(ResourceLocationWalker):
    """Extend the canonical root proof with the two owned-chat parent hops."""

    principal_id: str = ""
    _declared_workspaces: list[str] = PrivateAttr(default_factory=list)

    async def visit_parent(self, here: Any, edge: Any, model: Any) -> None:
        context = await here.get_context()
        parents = await self.parent_rows(here.id, edge, 1, context)
        if len(parents) != 1:
            self.valid = False
            return
        parent = await self.source_node(parents[0], (model,), context)
        if parent is None:
            self.valid = False
            return
        await self.visit(parent)

    @on_visit(ChatThread)
    async def on_chat_thread(self, here: ChatThread) -> None:
        if required_node_text(self._records[here.id], "user_id") != self.principal_id:
            self.valid = False
            return
        self._declared_workspaces.append(_workspace_cache(self._records[here.id]))
        await self.visit_parent(here, CATALOGS, ChatThreads)

    @on_visit(ChatThreads)
    async def on_chat_registry(self, here: ChatThreads) -> None:
        self._declared_workspaces.append(_workspace_cache(self._records[here.id]))
        await self.visit_parent(here, CONTAINS, Workspace)


async def chat_history_workspace(principal_id: str, thread_id: str) -> str:
    """Prove the owned thread's complete canonical path to Root.

    Fresh bounded parent facts, not hydration defaults or a caller's header,
    establish location. The inherited Workspace visit validates kind and the
    Root→IntegralApp→Workspaces→Workspace path. Absent scalar caches can retain
    this proven legacy location; conflicting caches or graph facts withhold it.
    This is read admission, never a graph or metadata repair.
    """
    try:
        context = get_default_context()
        walker = _ChatHistoryLocationWalker(principal_id=principal_id)
        thread = await walker.read_node(ChatThread, thread_id, context)
        if (
            thread is None
            or required_node_text(walker._records[thread_id], "user_id") != principal_id
        ):
            raise ResourceNotFoundError(message="Thread not found")
        await walker.spawn(thread)
        if (
            not walker.valid
            or not walker.workspace_id
            or any(
                isinstance(report, dict) and "hook_error" in report
                for report in await walker.get_report()
            )
            or any(
                cache and cache != walker.workspace_id
                for cache in walker._declared_workspaces
            )
        ):
            raise AuthorizationUnavailableError()
        return walker.workspace_id
    except ResourceNotFoundError:
        raise
    except Exception as exc:
        raise AuthorizationUnavailableError() from exc
