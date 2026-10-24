"""Current library dependency admission and deterministic merge ordering."""

import asyncio
import re
from typing import Optional

from jvspatial.core import Walker
from jvspatial.core.context import get_default_context
from jvspatial.core.decorators import on_visit
from pydantic import PrivateAttr

from app.api.errors import BadRequestError, ResourceNotFoundError
from app.exceptions import AuthorizationUnavailableError
from app.models.nodes import OperationalModel
from app.services.library_package_visibility import visible_library_model
from app.services.model_authority import get_current_model
from app.services.operational_model_runtime import compile_canonical_manifest


class LibraryDependencyWalker(Walker):
    """Admit each declared Model before inspecting its dependencies."""

    workspace_id: str
    user_id: Optional[str] = None
    _models: dict[str, OperationalModel] = PrivateAttr(default_factory=dict)
    _dependencies: dict[str, tuple[str, ...]] = PrivateAttr(default_factory=dict)
    _failure: Optional[Exception] = PrivateAttr(default=None)

    async def lookup(self, dependency_id: str) -> Optional[OperationalModel]:
        """An existing point ID never falls back to another package's alias."""
        exact = await get_current_model(dependency_id)
        if exact is not None:
            return await visible_library_model(
                exact.id, self.workspace_id, user_id=self.user_id
            )
        needle = {"$regex": "^" + re.escape(dependency_id) + "$", "$options": "i"}
        context = get_default_context()
        rows = await context.database.find(
            "node",
            {
                "entity": OperationalModel._entity_name(),
                "context.library_package": True,
                "$or": [
                    {"context.manifest.package.id": needle},
                    {"context.manifest.package.name": needle},
                ],
            },
            limit=257,
        )
        if len(rows) > 256:
            raise AuthorizationUnavailableError()
        matches = []
        for row in rows:
            current = await visible_library_model(
                row["id"], self.workspace_id, user_id=self.user_id
            )
            if current is not None:
                matches.append(current)
        if len(matches) > 1:
            raise BadRequestError(message="Ambiguous library dependency")
        return matches[0] if matches else None

    @on_visit(OperationalModel)
    async def on_model(self, here: OperationalModel) -> None:
        if self._failure is not None or here.id in self._models:
            return
        try:
            if len(self._models) >= 128:
                raise BadRequestError(
                    message="Library dependency graph exceeds its limit"
                )
            current = await visible_library_model(
                here.id, self.workspace_id, user_id=self.user_id
            )
            if current is None:
                raise ResourceNotFoundError(message="Operational Model not found")
            canonical = await asyncio.to_thread(
                compile_canonical_manifest, manifest=current.manifest or {}
            )
            declarations = (canonical.get("package") or {}).get("dependencies") or []
            if len(declarations) > 128:
                raise BadRequestError(
                    message="Library dependency graph exceeds its limit"
                )
            self._models[current.id] = current
            children = []
            for declaration in declarations:
                dependency_id = (
                    declaration.get("id") if isinstance(declaration, dict) else None
                )
                if not isinstance(dependency_id, str) or not dependency_id.strip():
                    raise BadRequestError(message="Invalid library dependency")
                child = await self.lookup(dependency_id.strip())
                if child is None:
                    raise ResourceNotFoundError(
                        message="Operational Model dependency not found"
                    )
                children.append(child)
            self._dependencies[current.id] = tuple(
                dict.fromkeys(child.id for child in children)
            )
            await self.visit(children)
        except Exception as exc:
            # Walker reports hook errors rather than rethrowing. Preserve the
            # typed failure and never return an admitted partial chain.
            self._failure = exc


async def admitted_library_dependency_chain(
    model_id: str,
    workspace_id: str,
    *,
    user_id: Optional[str] = None,
) -> list[OperationalModel]:
    """Admit the full graph before yielding any Model for materialization.

    Defensive candidate/node ceilings are not physical scan/byte budgets.
    Current point admission is not an atomic effect or reusable release permit.
    Actorless internal callers may consume only named public publications.
    """
    root = await visible_library_model(model_id, workspace_id, user_id=user_id)
    if root is None:
        raise ResourceNotFoundError(message="Operational Model not found")
    walker = LibraryDependencyWalker(workspace_id=workspace_id, user_id=user_id)
    await walker.spawn(root)
    if walker._failure is not None:
        raise walker._failure
    report = await walker.get_report()
    if any(isinstance(item, dict) and item.get("hook_error") for item in report):
        raise AuthorizationUnavailableError()
    remaining = dict(walker._dependencies)
    ordered = []
    emitted: set[str] = set()
    # The Walker captured the graph. This topological sort performs no graph I/O.
    while remaining:
        ready = sorted(
            key
            for key, dependencies in remaining.items()
            if set(dependencies) <= emitted
        )
        if not ready:
            raise BadRequestError(message="Circular profile dependency detected")
        for key in ready:
            ordered.append(walker._models[key])
            emitted.add(key)
            del remaining[key]
    return ordered
