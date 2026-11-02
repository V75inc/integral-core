"""Small, live schema manifests under a currently admitted App graph."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from jvspatial.core import Walker
from jvspatial.core.context import get_default_context
from jvspatial.core.decorators import on_visit
from pydantic import PrivateAttr

from app.api.errors import QueryUnavailableError
from app.models.edges import CONTAINS, HAS_OPERATIONAL_MODEL
from app.models.nodes import App, EntryType, OperationalModel, Track
from app.schemas.policy import Resource, Subject
from app.services.policy_engine import evaluate
from app.services.resource_location import DATA_APP_STATES, locate_resources


class ResourceSchemaPreviewWalker(Walker):
    """Traverse schema definitions only; never read or capture Entry values."""

    app_id: str
    workspace_id: str
    principal_id: str
    query: str = ""
    incomplete: bool = False
    _directory: list[dict] = PrivateAttr(default_factory=lambda: [])
    _directory_incomplete: bool = PrivateAttr(default=False)
    _tracks: dict[str, dict] = PrivateAttr(default_factory=lambda: {})
    _models: dict[str, str] = PrivateAttr(default_factory=lambda: {})
    _types: dict[str, str] = PrivateAttr(default_factory=lambda: {})

    @on_visit(App)
    async def on_app(self, here: App) -> None:
        if here.id != self.app_id:
            raise QueryUnavailableError()
        tracks, cursor = await here.nodes_page(edge=[CONTAINS], node=[Track], limit=25)
        self.incomplete = bool(cursor)
        sources: dict[str, dict] = {}
        locations = await locate_resources(
            "track",
            [track.id for track in tracks],
            workspace_hints=[self.workspace_id],
            allowed_app_states=DATA_APP_STATES,
            records_out=sources,
        )
        semaphore = asyncio.Semaphore(8)

        async def admitted(track):
            location = locations.get(track.id)
            if (
                location is None
                or location.workspace_id != self.workspace_id
                or location.app_id != self.app_id
            ):
                return False
            async with semaphore:
                decision = await evaluate(
                    subject=Subject(kind="human", id=self.principal_id),
                    action="track.read",
                    resource=Resource(
                        kind="track",
                        id=track.id,
                        scope=f"workspace:{self.workspace_id}",
                    ),
                )
                return decision.allowed

        decisions = await asyncio.gather(*(admitted(track) for track in tracks))
        allowed = [track for track, decision in zip(tracks, decisions) if decision]
        from app.agentive.harness.capability_search import _rank

        ranked = _rank(
            self.query,
            [(track.id, track.title + " " + track.purpose) for track in allowed],
        )
        order = {key: rank for rank, (key, _) in enumerate(ranked)}
        allowed.sort(key=lambda track: (order.get(track.id, len(order)), track.id))
        self._directory = [
            {
                "id": track.id,
                "title": str(sources[track.id]["context"].get("title") or "")[:256],
                "purpose": str(sources[track.id]["context"].get("purpose") or "")[:128],
                "purpose_incomplete": len(
                    str(sources[track.id]["context"].get("purpose") or "")
                )
                > 128,
            }
            for track in allowed
        ]
        self._directory_incomplete = bool(cursor)
        self.incomplete = self.incomplete or len(allowed) > 4
        await self.visit(allowed[:4])

    @on_visit(Track)
    async def on_track(self, here: Track) -> None:
        database = get_default_context().database
        rows = await database.find("node", {"id": here.id}, limit=1)
        if not rows:
            self.incomplete = True
            return
        context = rows[0]["context"]
        if rows[0].get("entity") != Track._entity_name() or context.get(
            "workspace_id"
        ) not in (None, "", self.workspace_id):
            raise QueryUnavailableError(message="Track schema scope changed")
        self._tracks[here.id] = {
            "id": here.id,
            "title": str(context["title"])[:256],
            "entry_types": [],
        }
        models = await here.nodes(
            edge=[HAS_OPERATIONAL_MODEL],
            node=[OperationalModel],
            direction="out",
            limit=2,
        )
        if len(models) > 1:
            raise QueryUnavailableError(message="Ambiguous Track schema")
        for model in models:
            self._models[model.id] = here.id
        await self.visit(models)
        await self._queue_types(here, here.id)

    async def _queue_types(self, parent, track_id):
        types, cursor = await parent.nodes_page(
            edge=[CONTAINS], node=[EntryType], limit=7
        )
        self.incomplete = self.incomplete or bool(cursor) or len(types) > 6
        selected = []
        for entry_type in types[:6]:
            previous = self._types.get(entry_type.id)
            if previous is not None:
                if previous != track_id:
                    raise QueryUnavailableError(message="Ambiguous EntryType schema")
                continue
            self._types[entry_type.id] = track_id
            selected.append(entry_type)
        await self.visit(selected)

    @on_visit(OperationalModel)
    async def on_model(self, here: OperationalModel) -> None:
        await self._queue_types(here, self._models[here.id])

    @on_visit(EntryType)
    async def on_type(self, here: EntryType) -> None:
        track_id = self._types[here.id]
        rows = await get_default_context().database.find(
            "node", {"id": here.id}, limit=1
        )
        if not rows:
            self.incomplete = True
            return
        data = rows[0]["context"]
        if data.get("track_id") != track_id:
            raise QueryUnavailableError(message="EntryType binding changed")
        form = data.get("form_schema") or {}
        fields = form.get("fields") or []
        if not isinstance(fields, list):
            raise QueryUnavailableError(message="Invalid EntryType schema")
        keys = {
            field["key"]: (
                field.get("type")
                if isinstance(field.get("type"), str) and len(field["type"]) <= 64
                else "unknown"
            )
            for field in fields
            if isinstance(field, dict)
            and isinstance(field.get("key"), str)
            and len(field["key"]) <= 128
        }
        # Literal keys stay unchanged; labels and descriptions only rank which
        # bounded hints are useful for the current source-discovery question.
        from app.agentive.harness.capability_search import _rank

        labels = {
            field["key"]: str(field.get("label") or "")[:128]
            + " "
            + str(field.get("description") or "")[:160]
            for field in fields
            if isinstance(field, dict)
            and isinstance(field.get("key"), str)
            and field["key"] in keys
        }
        ranked = _rank(
            self.query,
            [(key, key.replace("_", " ") + " " + labels.get(key, "")) for key in keys],
        )
        order = {key: rank for rank, (key, _) in enumerate(ranked)}
        keys = dict(
            sorted(keys.items(), key=lambda pair: order.get(pair[0], len(order)))
        )
        self._tracks[track_id]["entry_types"].append(
            {
                "id": here.id,
                "key": (
                    form.get("_manifest_entry_type_key")
                    if isinstance(form.get("_manifest_entry_type_key"), str)
                    and len(form["_manifest_entry_type_key"]) <= 256
                    else None
                ),
                "name": str(data.get("name") or "")[:256],
                "fields": dict(list(keys.items())[:12]),
                "fields_incomplete": len(keys) > 12 or len(keys) != len(fields),
            }
        )


async def app_schema_preview(
    *, app_id: str, user_id: str, workspace_id: str, query: str = ""
) -> dict[str, Any]:
    """Return bounded schema hints after fresh App/Track admission and traversal."""
    from app.services.permissions import resolve_role

    location = (
        await locate_resources("app", [app_id], workspace_hints=[workspace_id])
    ).get(app_id)
    if (
        location is None
        or location.workspace_id != workspace_id
        or await resolve_role(user_id, "app", app_id) is None
    ):
        return {"tracks": [], "incomplete": True}
    rows = await get_default_context().database.find("node", {"id": app_id}, limit=1)
    if not rows or rows[0].get("entity") != App._entity_name():
        return {"tracks": [], "incomplete": True}
    app = App.model_validate({**rows[0]["context"], "id": app_id})
    walker = ResourceSchemaPreviewWalker(
        app_id=app_id, workspace_id=workspace_id, principal_id=user_id, query=query
    )
    await walker.spawn(app)
    for report in await walker.get_report():
        if isinstance(report, dict) and "hook_error" in report:
            raise QueryUnavailableError(
                message="Schema preview unavailable"
            ) from RuntimeError(str(report["hook_error"]))
    # Compact directory hints survive omission of larger field/type previews.
    # Only already-admitted schema data is used, never Entry values or grants.
    from app.agentive.harness.capability_search import _rank

    for item in walker._directory:
        track = walker._tracks.get(item["id"])
        if track is None:
            continue
        keys = dict.fromkeys(
            key for kind in track["entry_types"] for key in kind["fields"]
        )
        ranked = _rank(query, [(key, key.replace("_", " ")) for key in keys])
        if not ranked:
            ranked = [(key, 0.0) for key in keys]
        hints: list[str] = []
        for key, _ in ranked:
            if len(hints) == 6:
                break
            if len(json.dumps(hints + [key], ensure_ascii=False).encode()) <= 96:
                hints.append(key)
        item["schema_keys"] = hints
        item["schema_keys_incomplete"] = len(hints) < len(keys) or any(
            kind["fields_incomplete"] for kind in track["entry_types"]
        )
    return {
        "tracks": list(walker._tracks.values()),
        "incomplete": walker.incomplete,
        "directory": walker._directory,
        "directory_incomplete": walker._directory_incomplete,
    }
