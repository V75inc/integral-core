"""Durable idempotency records for typed app operations."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional

from app.api.errors import BadRequestError
from app.models.operation_idempotency import OperationIdempotencyRecord
from app.utils.time import utc_now_iso


def hash_request_payload(payload: Optional[Dict[str, Any]]) -> str:
    """Stable SHA-256 of canonical JSON request body."""
    body = dict(payload or {})
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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
    return json.loads(raw)


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
