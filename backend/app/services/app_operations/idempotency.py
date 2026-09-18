"""Durable + in-process idempotency records for typed app operations."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, Optional, Tuple

from app.api.errors import BadRequestError
from app.models.operation_idempotency import OperationIdempotencyRecord
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)

# Same-process cache so retries within one worker are exact even when the
# durable Object store is unavailable (e.g. contract tests without full
# Object persistence bootstrap). Keyed like the durable row.
_MEMORY: Dict[Tuple[str, str, str, str, str], Dict[str, Any]] = {}


def hash_request_payload(payload: Optional[Dict[str, Any]]) -> str:
    """Stable SHA-256 of canonical JSON request body."""
    body = dict(payload or {})
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _mem_key(
    workspace_id: str,
    app_id: str,
    operation_key: str,
    principal_id: str,
    idempotency_key: str,
) -> Tuple[str, str, str, str, str]:
    return (workspace_id, app_id, operation_key, principal_id, idempotency_key)


async def lookup_idempotent_result(
    *,
    workspace_id: str,
    app_id: str,
    operation_key: str,
    principal_id: str,
    idempotency_key: str,
    request_hash: str,
) -> Optional[Dict[str, Any]]:
    """Return a prior result or raise on payload mismatch."""
    mkey = _mem_key(workspace_id, app_id, operation_key, principal_id, idempotency_key)
    mem = _MEMORY.get(mkey)
    if mem is not None:
        if str(mem.get("request_hash") or "") != request_hash:
            raise BadRequestError(
                message="Idempotency key reused with different payload",
                details={"error_code": "idempotency_conflict"},
            )
        cached = mem.get("result")
        return dict(cached) if isinstance(cached, dict) else None

    try:
        rows = await OperationIdempotencyRecord.find(
            {
                "workspace_id": workspace_id,
                "app_id": app_id,
                "operation_key": operation_key,
                "principal_id": principal_id,
                "idempotency_key": idempotency_key,
            },
            limit=1,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("idempotency durable lookup failed: %s", exc)
        return None
    if not rows:
        return None
    row = rows[0]
    stored_hash = str(getattr(row, "request_hash", "") or "")
    if stored_hash != request_hash:
        raise BadRequestError(
            message="Idempotency key reused with different payload",
            details={"error_code": "idempotency_conflict"},
        )
    raw = str(getattr(row, "result_json", "") or "")
    if not raw:
        return None
    result = json.loads(raw)
    _MEMORY[mkey] = {"request_hash": request_hash, "result": result}
    return dict(result) if isinstance(result, dict) else None


async def store_idempotent_result(
    *,
    workspace_id: str,
    app_id: str,
    operation_key: str,
    principal_id: str,
    idempotency_key: str,
    request_hash: str,
    result: Dict[str, Any],
) -> None:
    """Persist operation output for idempotent retries."""
    mkey = _mem_key(workspace_id, app_id, operation_key, principal_id, idempotency_key)
    if mkey not in _MEMORY:
        _MEMORY[mkey] = {"request_hash": request_hash, "result": dict(result)}

    try:
        existing = await OperationIdempotencyRecord.find(
            {
                "workspace_id": workspace_id,
                "app_id": app_id,
                "operation_key": operation_key,
                "principal_id": principal_id,
                "idempotency_key": idempotency_key,
            },
            limit=1,
        )
        if existing:
            return
        record = OperationIdempotencyRecord(
            workspace_id=workspace_id,
            app_id=app_id,
            operation_key=operation_key,
            principal_id=principal_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            result_json=json.dumps(result, sort_keys=True),
            created_at=utc_now_iso(),
        )
        await record.save()
    except Exception as exc:  # noqa: BLE001
        logger.warning("idempotency durable store failed: %s", exc)


def clear_idempotency_memory_for_tests() -> None:
    """Drop in-process idempotency cache (tests only)."""
    _MEMORY.clear()
