"""Tenant/session-scoped durable store for the Harness Planning capability."""

from __future__ import annotations

import hashlib
import json

from pydantic_ai_harness.planning import PlanItem, PlanStore, TaskStatus

from app.agentive.harness.contracts import HarnessExecutionScope
from app.agentive.harness.jvspatial_store import HarnessPersistenceError
from app.models.harness_records import HarnessPlanState
from app.services.credential_crypto import (
    CIPHER_PREFIX_V1,
    decrypt_secret_from_storage,
    encrypt_secret_for_storage,
)

_MAX_CAS_ATTEMPTS = 12


class JvSpatialPlanStore(PlanStore):
    """Persist one plan per immutable Integral execution session.

    Pydantic's PlanStore protocol intentionally carries no tenant parameter.
    Core constructs one instance per trusted HarnessSession and keeps it
    inside the Agent composition. The store hashes the complete
    tenant/principal/thread/session identity, encrypts plan content, and uses
    database compare-and-set on a monotonic revision for concurrent writes.
    """

    def __init__(self, *, scope: HarnessExecutionScope) -> None:
        identity = "\0".join(
            (
                scope.tenant_id,
                scope.principal_id,
                scope.thread_id,
                scope.session_id,
            )
        )
        self._scope_key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        digest = hashlib.sha256(f"{self._scope_key}:plan".encode()).hexdigest()
        self._record_id = f"o.HarnessPlanState.{digest}"

    def _encode(self, items: list[PlanItem]) -> str:
        payload = json.dumps(
            [item.model_dump(mode="json") for item in items],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        try:
            return encrypt_secret_for_storage(payload, aad=self._record_id)
        except RuntimeError as exc:
            raise HarnessPersistenceError(
                "Harness planning persistence requires configured storage encryption"
            ) from exc

    def _decode(self, stored: str) -> list[PlanItem]:
        try:
            if not stored.startswith(CIPHER_PREFIX_V1):
                raise ValueError("plan payload is not encrypted")
            raw = decrypt_secret_from_storage(stored, aad=self._record_id)
            if not raw:
                raise ValueError("plan payload decryption returned no content")
            payload = json.loads(raw)
            if not isinstance(payload, list):
                raise ValueError("plan payload is not a list")
            return [PlanItem.model_validate(item) for item in payload]
        except Exception as exc:
            raise HarnessPersistenceError(
                "Harness plan could not be authenticated or decoded"
            ) from exc

    async def _read(self) -> tuple[int, list[PlanItem]]:
        from jvspatial.core.context import get_default_context

        context = get_default_context()
        document = await context.database.get("object", self._record_id)
        if document is None:
            return 0, []
        stored = document.get("context") or {}
        if stored.get("scope_key") != self._scope_key:
            raise HarnessPersistenceError("Harness plan scope key mismatch")
        return int(stored.get("revision") or 0), self._decode(
            str(stored.get("payload_ciphertext") or "")
        )

    async def _insert_if_missing(self, items: list[PlanItem]) -> bool:
        _, created = await HarnessPlanState.create_if_absent(
            id=self._record_id,
            scope_key=self._scope_key,
            revision=1,
            payload_ciphertext=self._encode(items),
        )
        return created

    async def _cas_write(self, items: list[PlanItem], expected_revision: int) -> bool:
        if expected_revision == 0:
            return await self._insert_if_missing(items)
        from jvspatial.core.context import get_default_context

        context = get_default_context()
        result = await context.database.find_one_and_update(
            "object",
            {
                "id": self._record_id,
                "context.scope_key": self._scope_key,
                "context.revision": expected_revision,
            },
            {
                "$set": {
                    "context.revision": expected_revision + 1,
                    "context.payload_ciphertext": self._encode(items),
                }
            },
        )
        return result is not None

    async def _replace(self, items: list[PlanItem]) -> None:
        for _ in range(_MAX_CAS_ATTEMPTS):
            revision, _ = await self._read()
            if await self._cas_write(items, revision):
                return
        raise HarnessPersistenceError(
            "Harness plan changed repeatedly during compare-and-set replacement"
        )

    async def get_items(self) -> list[PlanItem]:
        """Return detached plan items in insertion order."""
        _, items = await self._read()
        return [item.model_copy(deep=True) for item in items]

    async def set_items(self, items: list[PlanItem]) -> None:
        """Atomically replace the full session plan."""
        await self._replace([item.model_copy(deep=True) for item in items])

    async def get_item(self, item_id: str) -> PlanItem | None:
        """Find one plan item by its session-local ID."""
        items = await self.get_items()
        return next((item for item in items if item.id == item_id), None)

    async def add_item(self, item: PlanItem) -> PlanItem:
        """Append an item, rejecting duplicate session-local IDs."""
        for _ in range(_MAX_CAS_ATTEMPTS):
            revision, items = await self._read()
            if any(existing.id == item.id for existing in items):
                raise ValueError(f"A step with id {item.id!r} is already in this plan.")
            items.append(item.model_copy(deep=True))
            if await self._cas_write(items, revision):
                return item.model_copy(deep=True)
        raise HarnessPersistenceError("Harness plan add could not be committed")

    async def update_item(
        self,
        item_id: str,
        *,
        content: str | None = None,
        status: TaskStatus | None = None,
        active_form: str | None = None,
        parent_id: str | None = None,
        depends_on: list[str] | None = None,
    ) -> PlanItem | None:
        """Update one item with CAS and return the new detached value."""
        for _ in range(_MAX_CAS_ATTEMPTS):
            revision, items = await self._read()
            item = next((entry for entry in items if entry.id == item_id), None)
            if item is None:
                return None
            if content is not None:
                item.content = content
            if status is not None:
                item.status = status
            if active_form is not None:
                item.active_form = active_form
            if parent_id is not None:
                item.parent_id = parent_id
            if depends_on is not None:
                item.depends_on = list(depends_on)
            if await self._cas_write(items, revision):
                return item.model_copy(deep=True)
        raise HarnessPersistenceError("Harness plan update could not be committed")

    async def remove_item(self, item_id: str) -> bool:
        """Remove one item by ID, preserving the rest of the plan."""
        for _ in range(_MAX_CAS_ATTEMPTS):
            revision, items = await self._read()
            filtered = [item for item in items if item.id != item_id]
            if len(filtered) == len(items):
                return False
            if await self._cas_write(filtered, revision):
                return True
        raise HarnessPersistenceError("Harness plan removal could not be committed")
