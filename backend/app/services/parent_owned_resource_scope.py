"""Bounded primary containment for Entry-owned comments and filed attachments.

Containment is not authorization. Consumers must independently authorize the
returned Entry before disclosing a child or its neighboring output fields.
"""

from dataclasses import dataclass
from typing import Any

from jvspatial.core.context import get_default_context
from jvspatial.core.decorators import on_visit

from app.exceptions import AuthorizationUnavailableError
from app.models.edges import HAS_ATTACHMENT, HAS_COMMENT
from app.models.nodes import Attachment, ChatThread, Comment, Entry
from app.services.authority_facts import relationship_literal
from app.services.resource_location import (
    DATA_APP_STATES,
    ResourceLocation,
    ResourceLocationWalker,
)


@dataclass(frozen=True)
class EntryOwnedResourceLocation:
    entry_id: str
    location: ResourceLocation


class EntryOwnedResourceWalker(ResourceLocationWalker):
    """Extend the ordinary canonical Entry path without trusting a parent cache."""

    entry_id: str = ""

    @on_visit(Comment)
    async def on_comment(self, here: Comment) -> None:
        context = await here.get_context()
        parents = await self.parent_rows(here.id, HAS_COMMENT, 1, context)
        if len(parents) != 1:
            self.valid = False
            return
        entry = await self.source_node(parents[0], (Entry,), context)
        if entry is None:
            self.valid = False
            return
        self.entry_id = entry.id
        await self.visit(entry)

    @on_visit(Attachment)
    async def on_attachment(self, here: Attachment) -> None:
        context = await here.get_context()
        record = self._records[here.id]
        if relationship_literal(record, "owner_kind", {"entry", "chat"}) != "entry":
            self.valid = False
            return
        if (
            relationship_literal(
                record,
                "scan_status",
                {"pending", "clean", "blocked", "skipped", "failed"},
            )
            == "blocked"
        ):
            self.valid = False
            return
        # Filing a chat upload retains its original thread edge and adds one
        # Entry home. That auxiliary edge grants no thread access. Any other
        # parent set (including two Entry homes) lacks this reviewed contract.
        parents = await self.parent_rows(here.id, HAS_ATTACHMENT, 2, context)
        if not 1 <= len(parents) <= 2:
            self.valid = False
            return
        entries: list[dict[str, Any]] = []
        threads: list[dict[str, Any]] = []
        for row in parents:
            parent = await self.source_record(row, (Entry, ChatThread), context)
            if parent is None:
                self.valid = False
                return
            (entries if parent["entity"] == Entry._entity_name() else threads).append(
                parent
            )
        if len(entries) != 1 or len(threads) > 1:
            self.valid = False
            return
        entry = await self.read_node(Entry, entries[0]["id"], context)
        self.entry_id = entry.id
        await self.visit(entry)


async def locate_entry_owned_resource(
    kind: str, resource_id: str
) -> EntryOwnedResourceLocation | None:
    """Read complete bounded parent facts; distinguish outages from absent scope."""
    model = {"comment": Comment, "attachment": Attachment}.get(kind)
    if model is None or not resource_id:
        return None
    try:
        context = get_default_context()
        walker = EntryOwnedResourceWalker(allowed_app_states=DATA_APP_STATES)
        node = await walker.read_node(model, resource_id, context)
        if node is None:
            return None
        await walker.spawn(node)
        if any(
            isinstance(report, dict) and "hook_error" in report
            for report in await walker.get_report()
        ):
            raise AuthorizationUnavailableError()
        if not walker.valid or not walker.workspace_id or not walker.entry_id:
            return None
        return EntryOwnedResourceLocation(
            walker.entry_id,
            ResourceLocation(walker.workspace_id, walker.app_id, walker.track_id),
        )
    except Exception as exc:
        raise AuthorizationUnavailableError() from exc
