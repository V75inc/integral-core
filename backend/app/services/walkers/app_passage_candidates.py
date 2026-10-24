"""Bounded, identity-based passage candidate discovery through rooted edges."""

from __future__ import annotations

import re
from typing import Any

from jvspatial.core import Walker
from jvspatial.core.decorators import on_visit
from pydantic import Field, PrivateAttr

from app.models.edges import CONTAINS, HAS_OPERATIONAL_MODEL
from app.models.nodes import App, Entry, EntryType, OperationalModel, Track
from app.services.operational_model_compile import slug_manifest_key
from app.services.permissions import resolve_role

MAX_CANDIDATES = 500


class CandidateSelectionUnavailable(Exception):
    """Stable selection failure code; never contains source data."""


def _legacy_title_query(field: str, key: str) -> dict[str, Any]:
    """Match normalized legacy labels without interpreting caller regex syntax."""
    pattern = "[^a-zA-Z0-9]+".join(re.escape(part) for part in key.split("_"))
    return {
        field: {"$regex": f"^[^a-zA-Z0-9]*{pattern}[^a-zA-Z0-9]*$", "$options": "i"}
    }


def _missing(field: str) -> dict[str, Any]:
    return {field: {"$in": [None, ""]}}


class AppPassageCandidateWalker(Walker):
    """App → Track → Entry, optionally resolving types through the Track's model.

    Every neighbour selector is filtered before its page limit. Explicit Entry
    selections constrain the parent Track IDs before graph discovery. Limits
    apply to matching candidates, never unrelated App Tracks or schema types.
    """

    principal_id: str
    workspace_id: str
    track_selector: str
    entry_type_key: str = ""
    selected_ids: list[str] | None = None
    selected_track_ids: list[str] | None = None
    records: list[dict[str, Any]] = Field(default_factory=list)
    truncated: bool = False
    track_found: bool = False
    failure_reason: str = ""
    _queued: int = PrivateAttr(default=0)
    _types_queued: int = PrivateAttr(default=0)
    _model_tracks: dict[str, Track] = PrivateAttr(default_factory=dict)
    _type_tracks: dict[str, Track] = PrivateAttr(default_factory=dict)

    @on_visit(App)
    async def on_app(self, here: App) -> None:
        if self.selected_track_ids == []:
            return
        selector = self.track_selector.strip()
        if not selector:
            self.failure_reason = "invalid_request"
            return
        key = slug_manifest_key(selector)
        identity = (
            {"id": selector}
            if selector.startswith("n.Track.")
            else {
                "$or": [
                    {"context.template_id": selector},
                    {
                        "$and": [
                            _missing("context.template_id"),
                            {"context.relation_type_key": selector},
                        ]
                    },
                ]
            }
        )
        tracks, cursor = await here.nodes_page(
            edge=[CONTAINS],
            node=[{"Track": self._narrow_tracks(identity)}],
            limit=MAX_CANDIDATES,
        )
        if not tracks and not selector.startswith("n.Track."):
            # Only genuinely legacy Tracks may use their title as identity.
            # Ambiguity fails closed instead of combining unrelated Tracks.
            legacy = {
                "$and": [
                    _missing("context.template_id"),
                    _missing("context.relation_type_key"),
                    _legacy_title_query("context.title", key),
                ]
            }
            tracks, cursor = await here.nodes_page(
                edge=[CONTAINS], node=[{"Track": self._narrow_tracks(legacy)}], limit=2
            )
            if cursor:
                self.failure_reason = "track_selection_unavailable"
                return
            tracks = [
                track
                for track in tracks
                if await resolve_role(self.principal_id, "track", track.id) is not None
            ]
            if len(tracks) > 1:
                self.failure_reason = "ambiguous_track"
                return
        self.truncated = bool(cursor)
        await self.visit(tracks)

    def _narrow_tracks(self, query: dict[str, Any]) -> dict[str, Any]:
        if self.selected_track_ids is not None:
            return {"$and": [query, {"id": {"$in": self.selected_track_ids}}]}
        return query

    @on_visit(Track)
    async def on_track(self, here: Track) -> None:
        if (
            here.workspace_id != self.workspace_id
            or (await resolve_role(self.principal_id, "track", here.id)) is None
        ):
            return
        self.track_found = True
        if not self.entry_type_key:
            await self._queue_entries(here)
            return
        # Current schemas are rooted under the Track's OperationalModel.
        models = await here.nodes(
            edge=[HAS_OPERATIONAL_MODEL], node=[OperationalModel], limit=2
        )
        if len(models) > 1:
            self.failure_reason = "schema_unavailable"
            return
        for model in models:
            self._model_tracks[model.id] = here
        await self.visit(models)
        # Older/manual schemas can be attached directly to their Track.
        await self._queue_types(here, here)

    @on_visit(OperationalModel)
    async def on_model(self, here: OperationalModel) -> None:
        await self._queue_types(here, self._model_tracks[here.id])

    async def _queue_types(self, parent: Any, track: Track) -> None:
        key = self.entry_type_key
        query = {"context.form_schema._manifest_entry_type_key": key}
        remaining = MAX_CANDIDATES - self._types_queued
        types, cursor = await parent.nodes_page(
            edge=[CONTAINS], node=[{"EntryType": query}], limit=max(1, remaining)
        )
        self.truncated = self.truncated or bool(cursor) or len(types) > remaining
        types = types[:remaining]
        self._types_queued += len(types)
        for entry_type in types:
            self._type_tracks[entry_type.id] = track
        await self.visit(types)

    @on_visit(EntryType)
    async def on_type(self, here: EntryType) -> None:
        track = self._type_tracks[here.id]
        if (
            here.track_id == track.id
            and (here.form_schema or {}).get("_manifest_entry_type_key")
            == self.entry_type_key
        ):
            await self._queue_entries(track, here.id)

    async def _queue_entries(self, track: Track, type_id: str | None = None) -> None:
        remaining = MAX_CANDIDATES - self._queued
        query: dict[str, Any] = {}
        if self.selected_ids is not None:
            query["id"] = {"$in": self.selected_ids}
        if type_id:
            query["context.type_id"] = type_id
        entries, cursor = await track.nodes_page(
            edge=[CONTAINS], node=[{"Entry": query}], limit=max(1, remaining)
        )
        self.truncated = self.truncated or bool(cursor) or len(entries) > remaining
        self._queued += min(len(entries), remaining)
        await self.visit(entries[:remaining])

    @on_visit(Entry)
    async def on_entry(self, here: Entry) -> None:
        self.records.append(await here.export())
