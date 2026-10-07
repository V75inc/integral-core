"""Schema-grounded connector routing through its authorized bound graph."""

from __future__ import annotations

from typing import Dict, Tuple

from jvspatial.core import Walker
from jvspatial.core.decorators import on_visit
from pydantic import PrivateAttr

from app.agentive.nodes import Connector
from app.models.edges import CONTAINS, HAS_OPERATIONAL_MODEL, IS_CONNECTED_TO
from app.models.nodes import EntryType, OperationalModel, Track
from app.services.operational_model_compile import slug_manifest_key
from app.services.permissions import resolve_role


class ConnectorBindingWalker(Walker):
    workspace_id: str = ""
    principal_id: str = ""
    _routes: Dict[str, Tuple[Track, EntryType]] = PrivateAttr(default_factory=dict)
    _model_tracks: Dict[str, Track] = PrivateAttr(default_factory=dict)
    _type_tracks: Dict[str, Track] = PrivateAttr(default_factory=dict)

    @on_visit(Connector)
    async def on_connector(self, here: Connector) -> None:
        tracks = await here.nodes(
            edge=[IS_CONNECTED_TO], node=["Track"], direction="out", limit=101
        )
        if len(tracks) > 100:
            raise ValueError("Connector has too many bindings to resolve safely")
        await self.visit(tracks)

    @on_visit(Track)
    async def on_track(self, here: Track) -> None:
        if here.workspace_id != self.workspace_id or (
            await resolve_role(self.principal_id, "track", here.id)
        ) not in {"owner", "admin", "editor"}:
            raise PermissionError("Connector owner cannot write the bound destination")
        models = await here.nodes(
            edge=[HAS_OPERATIONAL_MODEL],
            node=["OperationalModel"],
            direction="out",
            limit=2,
        )
        if len(models) != 1:
            raise ValueError("Connector destination requires one operational model")
        self._model_tracks[models[0].id] = here
        await self.visit(models)

    @on_visit(OperationalModel)
    async def on_model(self, here: OperationalModel) -> None:
        types = await here.nodes(
            edge=[CONTAINS], node=["EntryType"], direction="out", limit=101
        )
        if len(types) > 100:
            raise ValueError(
                "Connector destination has too many types to resolve safely"
            )
        for entry_type in types:
            self._type_tracks[entry_type.id] = self._model_tracks[here.id]
        await self.visit(types)

    @on_visit(EntryType)
    async def on_type(self, here: EntryType) -> None:
        key = slug_manifest_key(
            str((here.form_schema or {}).get("_manifest_entry_type_key") or here.name)
        )
        if not key or key in self._routes:
            raise ValueError("Connector entry type binding is ambiguous")
        self._routes[key] = (self._type_tracks[here.id], here)


async def resolve_connector_bindings(
    connector: Connector,
) -> Dict[str, Tuple[Track, EntryType]]:
    """Resolve unambiguous manifest keys under the current binding owner."""
    fresh = await Connector.get(connector.id)
    if (
        fresh is None
        or fresh.workspace_id != connector.workspace_id
        or fresh.owner != connector.owner
    ):
        raise PermissionError("Connector ownership changed during sync")
    walker = ConnectorBindingWalker(
        workspace_id=connector.workspace_id, principal_id=connector.owner
    )
    await walker.spawn(fresh)
    return dict(walker._routes)
