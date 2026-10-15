"""Durable + in-process idempotency records for typed app operations."""

from __future__ import annotations

import json
import logging
from copy import deepcopy
from typing import Any, Dict, Iterable, Optional, Tuple

from app.api.errors import BadRequestError
from app.contracts.operations import OperationIdentity, canonical_request_hash
from app.exceptions import AuthorizationUnavailableError
from app.models.operation_idempotency import OperationIdempotencyRecord
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)

# Same-process cache so retries within one worker are exact even when the
# durable Object store is unavailable (e.g. contract tests without full
# Object persistence bootstrap). Keyed like the durable row.
_MEMORY: Dict[Tuple[str, str, str, str, str], Dict[str, Any]] = {}


def hash_request_payload(payload: Optional[Dict[str, Any]]) -> str:
    """Backward-compatible export of the public request fingerprint contract."""
    return canonical_request_hash(payload)


def _mem_key(
    workspace_id: str,
    app_id: str,
    operation_key: str,
    principal_id: str,
    idempotency_key: str,
) -> Tuple[str, str, str, str, str]:
    return OperationIdentity.create(
        workspace_id=workspace_id,
        app_id=app_id,
        operation_key=operation_key,
        principal_id=principal_id,
        idempotency_key=idempotency_key,
    ).cache_key()


async def lookup_idempotent_result(
    *,
    workspace_id: str,
    app_id: str,
    operation_key: str,
    principal_id: str,
    idempotency_key: str,
    request_hash: str,
    principal_aliases: Iterable[str] = (),
) -> Optional[Dict[str, Any]]:
    """Read one human's bounded cache aliases without selecting conflicting facts."""
    aliases = tuple(principal_aliases)
    if len(aliases) > 1 or any(
        type(alias) is not str or not alias or alias != alias.strip()
        for alias in aliases
    ):
        raise ValueError("operation aliases require at most one verified principal")
    found = []
    for candidate in dict.fromkeys((principal_id, *aliases)):
        result = await _lookup_single_result(
            workspace_id=workspace_id,
            app_id=app_id,
            operation_key=operation_key,
            principal_id=candidate,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if result is not None:
            found.append(result)
    if not found:
        return None
    if any(result != found[0] for result in found[1:]):
        raise BadRequestError(
            message="Cached operation aliases require reconciliation",
            details={"error_code": "operation_cache_alias_conflict"},
        )
    return deepcopy(found[0])


async def _lookup_single_result(
    *,
    workspace_id: str,
    app_id: str,
    operation_key: str,
    principal_id: str,
    idempotency_key: str,
    request_hash: str,
) -> Optional[Dict[str, Any]]:
    mkey = _mem_key(workspace_id, app_id, operation_key, principal_id, idempotency_key)
    mem = _MEMORY.get(mkey)
    cached = None
    if mem is not None:
        if str(mem.get("request_hash") or "") != request_hash:
            raise BadRequestError(
                message="Idempotency key reused with different payload",
                details={"error_code": "idempotency_conflict"},
            )
        cached = mem.get("result")
        if not isinstance(cached, dict):
            raise AuthorizationUnavailableError()

    try:
        rows = await _cache_rows(
            workspace_id=workspace_id,
            app_id=app_id,
            operation_key=operation_key,
            principal_id=principal_id,
            idempotency_key=idempotency_key,
        )
    except Exception as exc:  # noqa: BLE001
        raise AuthorizationUnavailableError() from exc
    if not rows:
        return deepcopy(cached) if cached is not None else None
    if len(rows) != 1:
        raise AuthorizationUnavailableError()
    from app.services.authority_facts import required_node_text

    row = rows[0]
    stored_hash = required_node_text(row, "request_hash")
    if stored_hash != request_hash:
        raise BadRequestError(
            message="Idempotency key reused with different payload",
            details={"error_code": "idempotency_conflict"},
        )
    raw = required_node_text(row, "result_json")
    if not raw:
        raise AuthorizationUnavailableError()
    try:
        result = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise AuthorizationUnavailableError() from exc
    if not isinstance(result, dict):
        raise AuthorizationUnavailableError()
    if cached is not None and cached != result:
        raise BadRequestError(
            message="Cached operation aliases require reconciliation",
            details={"error_code": "operation_cache_alias_conflict"},
        )
    _MEMORY[mkey] = {"request_hash": request_hash, "result": result}
    return deepcopy(result)


async def _cache_rows(
    *,
    workspace_id: str,
    app_id: str,
    operation_key: str,
    principal_id: str,
    idempotency_key: str,
) -> list[dict[str, Any]]:
    """Use an actual row limit, not Object.find's keyword field filters."""
    from jvspatial.core.context import get_default_context

    from app.services.authority_facts import required_node_text

    context = get_default_context()
    await context.ensure_indexes(OperationIdempotencyRecord)
    expected = {
        "workspace_id": workspace_id,
        "app_id": app_id,
        "operation_key": operation_key,
        "principal_id": principal_id,
        "idempotency_key": idempotency_key,
    }
    entity = OperationIdempotencyRecord._entity_name()
    rows = await context.database.find(
        "object",
        {
            "entity": entity,
            **{f"context.{key}": value for key, value in expected.items()},
        },
        limit=2,
    )
    for row in rows:
        if (
            row.get("entity") != entity
            or not isinstance(row.get("id"), str)
            or not row["id"]
            or any(
                required_node_text(row, key) != value for key, value in expected.items()
            )
        ):
            raise AuthorizationUnavailableError()
    return rows


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
        _MEMORY[mkey] = {"request_hash": request_hash, "result": deepcopy(result)}

    try:
        existing = await _cache_rows(
            workspace_id=workspace_id,
            app_id=app_id,
            operation_key=operation_key,
            principal_id=principal_id,
            idempotency_key=idempotency_key,
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
