"""Resumable per-session rewrap of encrypted Pydantic AI Harness records."""

from __future__ import annotations

import re
from typing import Any

from app.models.harness_records import (
    HarnessCheckpointManifestRecord,
    HarnessEventRecord,
    HarnessModelRequestRecord,
    HarnessPlanState,
    HarnessRunRecord,
    HarnessSnapshotRecord,
    HarnessToolEffectRecord,
    HarnessTurnInputRecord,
)
from app.services import credential_crypto


def _current_key() -> bytes | None:
    """Resolve the live configured key (kept patchable for rotation probes)."""
    return credential_crypto._current_key()


def _previous_key() -> bytes | None:
    """Resolve the live overlap key (kept patchable for rotation probes)."""
    return credential_crypto._previous_key()


rewrap_secret_for_storage = credential_crypto.rewrap_secret_for_storage

_SCOPE_KEY = re.compile(r"^[0-9a-f]{64}$")
_MAX_CAS_ATTEMPTS = 12
_RECORD_TYPES: tuple[type[Any], ...] = (
    HarnessRunRecord,
    HarnessEventRecord,
    HarnessSnapshotRecord,
    HarnessCheckpointManifestRecord,
    HarnessToolEffectRecord,
    HarnessPlanState,
    HarnessModelRequestRecord,
    HarnessTurnInputRecord,
)


async def _rewrap_plan_state(record_id: str, *, scope_key: str) -> bool:
    """Rewrap mutable PlanStore content without overwriting concurrent edits."""
    from jvspatial.core.context import get_default_context

    context = get_default_context()
    for _ in range(_MAX_CAS_ATTEMPTS):
        document = await context.database.get("object", record_id)
        if document is None:
            return False
        stored = document.get("context") or {}
        if stored.get("scope_key") != scope_key:
            raise RuntimeError("Harness plan scope changed during key rotation")
        old_ciphertext = str(stored.get("payload_ciphertext") or "")
        ciphertext, changed = rewrap_secret_for_storage(old_ciphertext, aad=record_id)
        if not changed:
            return False
        revision = int(stored.get("revision") or 0)
        updated = await context.database.find_one_and_update(
            "object",
            {
                "id": record_id,
                "context.scope_key": scope_key,
                "context.revision": revision,
                "context.payload_ciphertext": old_ciphertext,
            },
            {"$set": {"context.payload_ciphertext": ciphertext}},
        )
        if updated is not None:
            return True
    raise RuntimeError("Harness plan changed repeatedly during key rotation")


async def rewrap_harness_session_records(*, scope_key: str) -> dict[str, int]:
    """Re-encrypt all persisted Harness payloads for one exact session scope.

    Configure the new key as current and the old key as previous before
    calling. Each row is persisted independently, so interruption is safe to
    resume. Run after all old-key writers have stopped; new rows created by the
    new configuration already use the current key. The returned summary has
    counts only and never contains record IDs or payload data.
    """
    if not _SCOPE_KEY.fullmatch(scope_key):
        raise ValueError("Harness key rotation requires a canonical session scope")
    current = _current_key()
    previous = _previous_key()
    if current is None:
        raise RuntimeError("Harness key rotation requires a configured current key")
    if previous is None:
        raise RuntimeError("Harness key rotation requires the previous key")
    if current == previous:
        raise ValueError("current and previous Harness encryption keys must differ")

    seen = 0
    rewrapped = 0
    by_model: dict[str, int] = {}
    for record_type in _RECORD_TYPES:
        records = await record_type.find({"scope_key": scope_key})
        for record in records:
            seen += 1
            if record_type is HarnessPlanState:
                changed = await _rewrap_plan_state(record.id, scope_key=scope_key)
                if changed:
                    rewrapped += 1
                    by_model[record_type.__name__] = (
                        by_model.get(record_type.__name__, 0) + 1
                    )
                continue
            if not record.payload_ciphertext:
                raise RuntimeError("Harness record has an empty encrypted payload")
            ciphertext, changed = rewrap_secret_for_storage(
                record.payload_ciphertext, aad=record.id
            )
            if not changed:
                continue
            record.payload_ciphertext = ciphertext
            await record.save()
            rewrapped += 1
            model_name = record_type.__name__
            by_model[model_name] = by_model.get(model_name, 0) + 1

    return {"records_seen": seen, "records_rewrapped": rewrapped, **by_model}
