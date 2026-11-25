"""Ephemeral, authorized spoken navigation for the workspace assistant."""

from dataclasses import dataclass, field
from time import monotonic

from app.agentive.tooling.manifest import ToolSpec

_PAGE_TTL = 180
_MAX_PAGES = 10
_LISTS = {
    "apps": "integral_list_apps",
    "tracks": "integral_list_tracks",
    "workspaces": "integral_list_workspaces",
}
_KINDS = {"integral_list_apps": "app", "integral_list_tracks": "track"}


@dataclass(frozen=True)
class VoiceDestination:
    kind: str
    id: str
    label: str
    track_id: str = ""


@dataclass(frozen=True)
class VoicePage:
    spec: ToolSpec | None
    destinations: tuple[VoiceDestination, ...]
    text: str
    cursor: str | None = None
    next_cursor: str | None = None
    anchor: VoiceDestination | None = None
    query: str = ""
    collection: str = "records"


async def read_page(spec, *, principal, workspace, cursor=None) -> VoicePage | None:
    """Use exactly the same reviewed dispatcher as the negotiated quick read."""
    from app.agentive.tooling.dispatch import dispatch_tool

    meta = spec.voice_quick_read
    if meta is None:
        return None
    options = {"voice_read_cursor": cursor} if cursor is not None else {}
    response = await dispatch_tool(
        spec.name,
        {},
        principal_id=principal,
        scope=workspace,
        voice_read_offer=spec,
        **options,
    )
    if response.is_error or not isinstance(response.data, dict):
        return None
    records = response.data.get(meta.collection)
    if not isinstance(records, list):
        return None
    labels: list[str] = []
    destinations: list[VoiceDestination] = []
    current = ""
    kind = _KINDS.get(spec.name)
    for row in records:
        if not isinstance(row, dict):
            continue
        value = row.get(meta.label_field)
        if not isinstance(value, str) or not value.strip():
            continue
        label = " ".join(value.split())[:100]
        if len(labels) < 20:
            labels.append(label)
            identifier = row.get("id")
            if kind and isinstance(identifier, str) and 0 < len(identifier) <= 256:
                destinations.append(VoiceDestination(kind, identifier, label))
        if meta.mark_current_workspace and row.get("id") == workspace:
            current = label
    # A partially addressable list must never shift the spoken ordinal.
    if len(destinations) != len(labels) or len({d.id for d in destinations}) != len(
        destinations
    ):
        destinations = []
    next_cursor = response.data.get("next_cursor")
    if (
        not isinstance(next_cursor, str)
        or not 0 < len(next_cursor) <= 2048
        or "cursor" not in spec.params
    ):
        next_cursor = None
    marker = f"The current workspace is {current}. " if current else ""
    values = (
        ", ".join(f"{i}. {label}" for i, label in enumerate(labels, 1))
        if destinations
        else ", ".join(labels)
    )
    text = f"{marker}{meta.title}: {values or 'none visible'}."
    if next_cursor:
        text += " Ask for the next page to hear more."
    elif len(records) > 20 or response.data.get("has_more") is True:
        text += " More entries may be available in Integral."
    return VoicePage(spec, tuple(destinations), text, cursor, next_cursor)


async def read_records(*, principal, workspace, anchor=None, cursor=None, query=""):
    """Read only titles and scoped identities through canonical Entry listing."""
    from app.middleware.permissions_cache import isolated_permissions_cache
    from app.services.entry_listing import fetch_accessible_entries_page

    with isolated_permissions_cache():
        if anchor and (
            anchor.kind not in {"app", "track", "view"}
            or not await verified_destination(
                anchor, principal=principal, workspace=workspace
            )
        ):
            return None
        view_node = None
        if anchor and anchor.kind == "view":
            from app.services.view_scope import read_scoped_view

            scoped = await read_scoped_view(anchor.id)
            if (
                scoped is None
                or scoped[1].workspace_id != workspace
                or scoped[1].track_id != anchor.track_id
                or scoped[0].hidden
            ):
                return None
            view_node = scoped[0]
        entries, page = await fetch_accessible_entries_page(
            principal,
            workspace_id=workspace,
            track_id=(
                anchor.track_id
                if view_node is not None
                else anchor.id if anchor and anchor.kind == "track" else None
            ),
            view_node=view_node,
            app_id=anchor.id if anchor and anchor.kind == "app" else None,
            q=query or None,
            cursor=cursor,
            limit=20,
        )
        destinations = tuple(
            VoiceDestination(
                "entry", entry.id, " ".join(entry.title.split())[:100], entry.track_id
            )
            for entry in entries
            if entry.title and entry.track_id
        )
    text = (
        "Visible records: "
        + (
            ", ".join(f"{i}. {item.label}" for i, item in enumerate(destinations, 1))
            or "none visible"
        )
        + "."
    )
    next_cursor = page.get("next_cursor")
    if not isinstance(next_cursor, str) or not 0 < len(next_cursor) <= 2048:
        next_cursor = None
    if next_cursor:
        text += " Ask for the next page to hear more."
    return VoicePage(None, destinations, text, cursor, next_cursor, anchor, query)


async def read_views(*, principal, workspace, anchor, cursor=None):
    """Read one bounded page of visible saved views under a selected Track."""
    import asyncio

    from app.middleware.permissions_cache import isolated_permissions_cache
    from app.models.edges import HAS_VIEW
    from app.models.nodes import Track

    if (
        anchor is None
        or anchor.kind != "track"
        or not await verified_destination(
            anchor, principal=principal, workspace=workspace
        )
    ):
        return None
    with isolated_permissions_cache():
        track = await Track.get(anchor.id)
        if track is None:
            return None
        rows, next_cursor = await track.nodes_page(
            edge=[HAS_VIEW],
            node=["View"],
            direction="out",
            limit=20,
            cursor=cursor,
        )
        candidates = [
            VoiceDestination(
                "view", view.id, " ".join(view.name.split())[:100], anchor.id
            )
            for view in rows
            if view.name and not view.hidden
        ]
        allowed = await asyncio.gather(
            *(
                verified_destination(target, principal=principal, workspace=workspace)
                for target in candidates
            )
        )
        destinations = tuple(
            target for target, permitted in zip(candidates, allowed) if permitted
        )
    text = (
        "Visible saved views: "
        + (
            ", ".join(f"{i}. {item.label}" for i, item in enumerate(destinations, 1))
            or "none visible on this page"
        )
        + "."
    )
    if not isinstance(next_cursor, str) or not 0 < len(next_cursor) <= 2048:
        next_cursor = None
    if next_cursor:
        text += " Ask for the next page to hear more."
    return VoicePage(
        None, destinations, text, cursor, next_cursor, anchor, collection="views"
    )


async def refresh_page(page, *, principal, workspace, cursor):
    """Re-read a server-held page within its original scope."""
    if page.collection == "views":
        return await read_views(
            principal=principal, workspace=workspace, anchor=page.anchor, cursor=cursor
        )
    if page.spec is None:
        return await read_records(
            principal=principal,
            workspace=workspace,
            anchor=page.anchor,
            query=page.query,
            cursor=cursor,
        )
    return await read_page(
        page.spec, principal=principal, workspace=workspace, cursor=cursor
    )


@dataclass
class VoiceNavigation:
    """Call-local list revisions and focus. No model-supplied identifiers."""

    page: VoicePage | None = None
    pages: list[VoicePage] = field(default_factory=list)
    focus: VoiceDestination | None = None
    previous_focus: VoiceDestination | None = None
    expires: float = 0

    def accept_page(self, page: VoicePage) -> None:
        self.page = page
        self.pages = []
        self.expires = monotonic() + _PAGE_TTL

    async def select(self, request, *, principal, workspace):
        import asyncio

        from app.agentive.tooling.catalogue import build_voice_quick_read_catalogue

        action = request["action"]
        args = {"principal": principal, "workspace": workspace}
        if action in _LISTS:
            self.page = None
            self.pages.clear()
            catalogue = await asyncio.to_thread(build_voice_quick_read_catalogue)
            spec = catalogue.get(_LISTS[action])
            page = await read_page(spec, **args) if spec else None
            if page is None:
                return "That list is unavailable."
            self.accept_page(page)
            return page.text
        if action == "views":
            self.page = None
            self.pages.clear()
            if not self.focus or self.focus.kind != "track":
                return (
                    "Select a Track or go back to it before asking for its saved views."
                )
            page = await read_views(anchor=self.focus, **args)
            if page is None:
                return "Saved views are unavailable in this location."
            self.accept_page(page)
            return page.text
        if action == "records":
            self.page = None
            self.pages.clear()
            anchor = self.focus
            if anchor and anchor.kind == "entry":
                return "Go back to the App or Track, then ask for records there."
            page = await read_records(
                anchor=anchor, query=request.get("query", ""), **args
            )
            if page is None:
                return "Records in that location are unavailable."
            self.accept_page(page)
            return page.text
        if action == "where":
            if self.focus and await verified_destination(self.focus, **args):
                return f"Current spoken location: {self.focus.kind} {self.focus.label}."
            self.focus = None
            return "No App or Track selected in this call. Ask for Apps or Tracks to choose one."
        if action == "back":
            target = self.previous_focus
            if target and await verified_destination(target, **args):
                self.focus, self.previous_focus = target, self.focus
                return f"Current spoken location: {target.kind} {target.label}."
            self.focus = self.previous_focus = None
            return "Returned to the workspace. No App or Track selected."
        if self.page is None or monotonic() >= self.expires:
            return "The list is no longer current. Ask for it again before choosing."
        page = self.page
        if action == "repeat":
            updated = await refresh_page(page, cursor=page.cursor, **args)
            if updated is None:
                self.page = None
                self.pages.clear()
                return "The list is unavailable. Ask for it again."
            self.page = updated
            self.expires = monotonic() + _PAGE_TTL
            return updated.text
        if action == "next":
            if not page.next_cursor or len(self.pages) >= _MAX_PAGES:
                return "No further page is available in this call."
            updated = await refresh_page(page, cursor=page.next_cursor, **args)
            if updated is None:
                return "The next page is unavailable. Ask for the list again."
            self.pages.append(page)
            self.page = updated
            self.expires = monotonic() + _PAGE_TTL
            return updated.text
        if action == "previous":
            if not self.pages:
                return "Already on the first page."
            prior = self.pages[-1]
            updated = await refresh_page(prior, cursor=prior.cursor, **args)
            if updated is None:
                return "The previous page is unavailable. Ask for the list again."
            self.pages.pop()
            self.page = updated
            self.expires = monotonic() + _PAGE_TTL
            return updated.text
        ordinal = request["ordinal"]
        if not 1 <= ordinal <= len(page.destinations):
            return "That choice is not in the current list. Ask for the list again."
        target = page.destinations[ordinal - 1]
        current = await refresh_page(page, cursor=page.cursor, **args)
        if current is None or current.destinations != page.destinations:
            self.page = None
            self.pages.clear()
            return "The list changed. Ask for it again before choosing."
        if not await verified_destination(target, **args):
            return "That destination is no longer available."
        self.previous_focus, self.focus = self.focus, target
        return f"Selected {target.kind} {target.label} as the current spoken location."


async def verified_destination(destination, *, principal, workspace):
    """Check current canonical location, resource role and label before use."""
    from app.middleware.permissions_cache import isolated_permissions_cache
    from app.models.nodes import App, Entry, Track
    from app.schemas.policy import Resource, Subject
    from app.services.policy_engine import evaluate
    from app.services.resource_location import DATA_APP_STATES, locate_resource

    with isolated_permissions_cache():
        location = await locate_resource(
            destination.kind, destination.id, allowed_app_states=DATA_APP_STATES
        )
        if location is None or location.workspace_id != workspace:
            return False
        resource_scope = (
            f"app:{destination.id}"
            if destination.kind == "app"
            else f"track:{location.track_id}"
        )
        if (
            destination.kind in {"entry", "view"}
            and destination.track_id != location.track_id
        ):
            return False
        decision = await evaluate(
            subject=Subject(kind="human", id=principal),
            action={
                "app": "app.read",
                "track": "track.read",
                "entry": "entry.read",
                "view": "view.read",
            }[destination.kind],
            resource=Resource(
                kind=destination.kind, id=destination.id, scope=resource_scope
            ),
        )
        if not decision.allowed:
            return False
        if destination.kind == "view":
            from app.services.view_scope import read_scoped_view

            scoped = await read_scoped_view(destination.id)
            if scoped is None or scoped[1] != location or scoped[0].hidden:
                return False
            node = scoped[0]
        else:
            node = await {"app": App, "track": Track, "entry": Entry}[
                destination.kind
            ].get(destination.id)
        label = getattr(
            node, "name" if destination.kind in {"app", "view"} else "title", ""
        )
        return (
            isinstance(label, str)
            and " ".join(label.split())[:100] == destination.label
        )
