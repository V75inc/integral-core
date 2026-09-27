"""One filing facet against an existing entry (W2.2).

Update and append reuse entry policy, live schema revision, and
protected-field rejection. Tags and relation links in the same call
commit through ``graph_transaction`` or are refused before any write.
A retry that repeats the authorized payload and revisions replays the
durable receipt and does not append twice.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Dict, List, Optional, Tuple

from jvspatial.db import get_prime_database

from app.api.entries import _slugify_entry_type_key
from app.api.errors import BadRequestError
from app.contracts.information import schema_revision_from_profile_version
from app.models.edges import REFERENCES, TAGGED_WITH
from app.models.nodes import Entry, EntryType, Tag, Track
from app.schemas.policy import Resource, Subject
from app.services.app_invariant_guards import enforce_protected_field_write
from app.services.app_operations.transaction_scope import (
    OperationTransactionUnavailable,
    graph_transaction_available,
    postgres_graph_transaction,
)
from app.services.policy_engine import evaluate as policy_evaluate
from app.services.query_boundary import generic_entry_read, relation_visible
from app.utils.time import utc_now_iso

_PROBE: Optional[Callable[[str], None]] = None
_OBJECT_COLLECTION = "object"
_RECEIPT_ENTITY = "FilingFacetReceipt"
_FINGERPRINT_KEYS = (
    "mode",
    "entry_id",
    "text",
    "title",
    "fields",
    "tags",
    "relations",
    "append_field",
    "expected_record_revision",
    "expected_schema_revision",
    "entry_type_key",
    "replace_body",
)


def set_facet_probe(probe: Optional[Callable[[str], None]]) -> None:
    """Test hook. ``probe(step)`` runs before relation links and before tags."""
    global _PROBE
    _PROBE = probe


def reset_filing_idempotency() -> None:
    """Receipts live in the object store. Kept so existing tests stay callable."""


def _fingerprint(payload: Dict[str, Any]) -> str:
    body = {key: payload.get(key) for key in _FINGERPRINT_KEYS}
    return json.dumps(body, sort_keys=True, default=str)


def _receipt_id(user_id: str, idempotency_key: str) -> str:
    digest = hashlib.sha256(f"{user_id}:{idempotency_key}".encode()).hexdigest()
    return f"o.{_RECEIPT_ENTITY}.{digest}"


def _probe(step: str) -> None:
    if _PROBE is not None:
        _PROBE(step)


def _refused(code: str, message: str) -> Dict[str, Any]:
    return {
        "error": True,
        "filed": False,
        "partial": False,
        "error_code": code,
        "message": message,
    }


def _store(database: Any | None = None) -> Any:
    return database if database is not None else get_prime_database()


def _replay_or_conflict(record: Dict[str, Any], fingerprint: str) -> Dict[str, Any]:
    context = record.get("context") or {}
    if str(context.get("request_hash") or "") != fingerprint:
        return _refused(
            "idempotency_conflict",
            "This idempotency key was already used for a different payload",
        )
    if str(context.get("status") or "") != "succeeded":
        return _refused(
            "idempotency_incomplete",
            "This filing receipt is incomplete and cannot be replayed",
        )
    try:
        result = json.loads(str(context.get("result_json") or ""))
    except json.JSONDecodeError:
        return _refused(
            "idempotency_incomplete",
            "This filing receipt is incomplete and cannot be replayed",
        )
    if not isinstance(result, dict):
        return _refused(
            "idempotency_incomplete",
            "This filing receipt is incomplete and cannot be replayed",
        )
    return {**result, "retry": True}


async def _load_receipt(store: Any, receipt_id: str) -> Optional[Dict[str, Any]]:
    getter = getattr(store, "get", None)
    if not callable(getter):
        return None
    record = await getter(_OBJECT_COLLECTION, receipt_id)
    return record if isinstance(record, dict) else None


async def _write_receipt(
    store: Any,
    *,
    receipt_id: str,
    fingerprint: str,
    result: Dict[str, Any],
) -> None:
    document = {
        "id": receipt_id,
        "entity": _RECEIPT_ENTITY,
        "context": {
            "request_hash": fingerprint,
            "status": "succeeded",
            "result_json": json.dumps(result, sort_keys=True, separators=(",", ":")),
            "updated_at": utc_now_iso(),
        },
    }
    insert = getattr(store, "insert_if_absent", None)
    if callable(insert):
        inserted = await insert(_OBJECT_COLLECTION, document)
        if inserted.created:
            return
        existing = inserted.record
        if isinstance(existing, dict):
            return
    saver = getattr(store, "save", None)
    if callable(saver):
        await saver(_OBJECT_COLLECTION, document)


async def _live_schema_revision(track: Optional[Track], entry: Entry) -> int:
    if track is None:
        return int(getattr(entry, "schema_revision", 1) or 1)
    try:
        from app.services.operational_model_runtime import resolve_track_runtime_profile

        operational_model, _, _ = await resolve_track_runtime_profile(track)
    except Exception:  # noqa: BLE001 — unprofiled tracks keep the stored revision
        return int(getattr(entry, "schema_revision", 1) or 1)
    return schema_revision_from_profile_version(
        getattr(operational_model, "version_number", None)
    )


async def _entry_type_key(entry: Entry, payload: Dict[str, Any]) -> str:
    entry_type = await EntryType.get(entry.type_id) if entry.type_id else None
    if entry_type is not None:
        return _slugify_entry_type_key(
            str(
                (entry_type.form_schema or {}).get("_manifest_entry_type_key")
                or entry_type.name
                or ""
            )
        )
    return _slugify_entry_type_key(str(payload.get("entry_type_key") or ""))


async def _resolve_tags(
    track_id: str, raw_tags: List[Any]
) -> Tuple[List[str], List[str]]:
    track_tags = await Tag.find({"context.track_id": track_id})
    known_ids = {tag.id for tag in track_tags}
    by_name = {(tag.name or "").casefold(): tag.id for tag in track_tags}
    resolved: List[str] = []
    unknown: List[str] = []
    for raw in raw_tags:
        ref = str(raw).strip()
        if not ref:
            continue
        tag_id = ref if ref in known_ids else by_name.get(ref.casefold())
        if tag_id:
            resolved.append(tag_id)
        else:
            unknown.append(ref)
    return list(dict.fromkeys(resolved)), unknown


async def _resolve_relations(
    entry: Entry, relations: List[Any], user_id: str
) -> Tuple[Optional[Dict[str, Any]], List[Tuple[str, Entry]]]:
    del user_id
    targets: List[Tuple[str, Entry]] = []
    cache: dict = {}
    for raw in relations:
        if not isinstance(raw, dict):
            return (
                _refused(
                    "relation_invalid",
                    "Each relation needs field_key and target_entry_id",
                ),
                [],
            )
        field_key = str(raw.get("field_key") or "").strip()
        target_id = str(raw.get("target_entry_id") or "").strip()
        if not field_key or not target_id:
            return (
                _refused(
                    "relation_invalid",
                    "Each relation needs field_key and target_entry_id",
                ),
                [],
            )
        target = await Entry.get(target_id)
        if target is None:
            return (
                _refused("relation_target_missing", "Relation target was not found"),
                [],
            )
        target_track = await Track.get(target.track_id) if target.track_id else None
        if target_track is None or not (await generic_entry_read(target_track)).allowed:
            return (
                _refused(
                    "relation_target_hidden",
                    "Relation target is outside the query boundary",
                ),
                [],
            )
        if not await relation_visible(entry, target, cache=cache):
            return (
                _refused(
                    "relation_target_hidden",
                    "Relation target is outside the query boundary",
                ),
                [],
            )
        targets.append((field_key, target))
    return None, targets


async def _apply_tags(entry: Entry, tag_ids: List[str], user_id: str) -> None:
    ctx = await entry.get_context()
    prev = list(entry.tags or [])
    to_remove = [tid for tid in prev if tid not in tag_ids]
    to_add = [tid for tid in tag_ids if tid not in prev]
    for tid in to_remove:
        tag = await Tag.get(tid)
        if tag is None:
            continue
        edges = await ctx.find_edges_between(entry.id, tag.id, edge_class=TAGGED_WITH)
        for edge in edges:
            await edge.delete()
    now = utc_now_iso()
    for tid in to_add:
        tag = await Tag.get(tid)
        if tag is None:
            continue
        await entry.connect(tag, edge=TAGGED_WITH, tagged_at=now, tagged_by=user_id)
    entry.tags = tag_ids


async def _mutate_entry(
    *,
    entry: Entry,
    payload: Dict[str, Any],
    mode: str,
    text: str,
    fields: Dict[str, Any],
    targets: List[Tuple[str, Entry]],
    tag_ids: Optional[List[str]],
    user_id: str,
    current_revision: int,
    current_schema: int,
) -> Dict[str, Any]:
    append_field = str(payload.get("append_field") or "").strip()
    if mode == "append":
        if append_field == "body":
            prior = entry.body or ""
            entry.body = (prior + ("\n" if prior else "") + text).strip()
        else:
            merged = dict(entry.custom_fields or {})
            prior = str(merged.get(append_field) or "")
            merged[append_field] = (prior + ("\n" if prior else "") + text).strip()
            entry.custom_fields = merged
    else:
        title = payload.get("title")
        if isinstance(title, str) and title.strip():
            entry.title = title.strip()
        if payload.get("replace_body") and text.strip():
            entry.body = text
        if fields:
            entry.custom_fields = {**(entry.custom_fields or {}), **fields}

    _probe("relations")
    if targets:
        merged = dict(entry.custom_fields or {})
        for field_key, target in targets:
            await entry.connect(
                target,
                edge=REFERENCES,
                field_key=field_key,
                relation_type="operational_model",
                cross_track=target.track_id != entry.track_id,
            )
            merged[field_key] = target.id
        entry.custom_fields = merged

    _probe("tags")
    if tag_ids is not None:
        await _apply_tags(entry, tag_ids, user_id)

    entry.record_revision = current_revision + 1
    entry.schema_revision = current_schema
    entry.updated_at = utc_now_iso()
    await entry.save()
    return {
        "filed": True,
        "partial": False,
        "entry_id": entry.id,
        "mode": mode,
        "record_revision": entry.record_revision,
        "appended": mode == "append",
    }


async def apply_filing_facet(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Apply one update or append. Writes nothing when the preflight fails."""
    mode = str(payload.get("mode") or "").strip().lower()
    if mode not in ("update", "append"):
        return _refused("mode_unsupported", "mode must be update or append")
    entry_id = str(payload.get("entry_id") or "").strip()
    if not entry_id:
        return _refused("entry_required", "entry_id is required to update or append")
    if (
        payload.get("expected_record_revision") is None
        or payload.get("expected_schema_revision") is None
    ):
        return _refused(
            "revision_required",
            "expected_record_revision and expected_schema_revision are required",
        )

    fingerprint = _fingerprint(payload)
    idem = str(payload.get("idempotency_key") or "").strip()
    receipt_id = _receipt_id(user_id, idem) if idem else ""
    if receipt_id:
        existing = await _load_receipt(_store(), receipt_id)
        if existing is not None:
            return _replay_or_conflict(existing, fingerprint)

    entry = await Entry.get(entry_id)
    if entry is None:
        return _refused("not_found", "Entry not found")

    decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="entry.update",
        resource=Resource(kind="entry", id=entry_id, scope=f"entry:{entry_id}"),
    )
    if not decision.allowed:
        return _refused("denied", "Access denied")

    track = await Track.get(entry.track_id) if entry.track_id else None
    current_revision = int(getattr(entry, "record_revision", 1) or 1)
    if int(payload["expected_record_revision"]) != current_revision:
        return _refused(
            "record_revision_conflict",
            "Entry has changed since it was read",
        )
    current_schema = await _live_schema_revision(track, entry)
    if int(payload["expected_schema_revision"]) != current_schema:
        return _refused(
            "schema_revision_conflict",
            "Entry schema has changed since it was read",
        )

    text = str(payload.get("text") or "")
    append_field = str(payload.get("append_field") or "").strip()
    if mode == "append" and not append_field:
        return _refused("append_field_required", "append_field is required")
    if mode == "append" and not text.strip():
        return _refused("text_required", "text is required to append")

    fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
    relations = (
        payload.get("relations") if isinstance(payload.get("relations"), list) else []
    )
    proposed: Dict[str, Any] = dict(fields)
    if mode == "append" and append_field not in ("", "body"):
        proposed[append_field] = text

    type_key = await _entry_type_key(entry, payload)
    workspace_id = str(getattr(track, "workspace_id", "") or "")
    if proposed:
        try:
            await enforce_protected_field_write(
                workspace_id=workspace_id,
                entry_type_key=type_key,
                proposed_custom_fields=proposed,
            )
        except BadRequestError:
            return _refused(
                "protected_field_write",
                "Protected App fields cannot be written through generic Entry updates; "
                "use the App's typed operation",
            )

    refused, targets = await _resolve_relations(entry, relations, user_id)
    if refused is not None:
        return refused

    tag_ids: Optional[List[str]] = None
    if payload.get("tags") is not None:
        if track is None:
            return _refused("not_found", "Track not found for entry")
        tag_ids, unknown = await _resolve_tags(
            track.id, list(payload.get("tags") or [])
        )
        if unknown:
            return _refused(
                "unknown_tag",
                "Tags not defined on this Track: " + ", ".join(unknown),
            )

    if mode == "append":
        current_value = (
            entry.body or ""
            if append_field == "body"
            else str((entry.custom_fields or {}).get(append_field) or "")
        )
        if current_value.endswith(text):
            result = {
                "filed": True,
                "partial": False,
                "appended": False,
                "already_applied": True,
                "entry_id": entry.id,
                "mode": mode,
                "record_revision": current_revision,
            }
            if receipt_id:
                await _write_receipt(
                    _store(),
                    receipt_id=receipt_id,
                    fingerprint=fingerprint,
                    result=result,
                )
            return result

    needs_txn = bool(targets) or tag_ids is not None
    if needs_txn and not graph_transaction_available():
        return _refused(
            "transaction_unavailable",
            "Tags and relation links require a store that can host one transaction",
        )

    async def _run(store: Any) -> Dict[str, Any]:
        fresh = await Entry.get(entry_id)
        if fresh is None:
            return _refused("not_found", "Entry not found")
        result = await _mutate_entry(
            entry=fresh,
            payload=payload,
            mode=mode,
            text=text,
            fields=fields,
            targets=targets,
            tag_ids=tag_ids,
            user_id=user_id,
            current_revision=current_revision,
            current_schema=current_schema,
        )
        if receipt_id:
            await _write_receipt(
                store,
                receipt_id=receipt_id,
                fingerprint=fingerprint,
                result=result,
            )
        return result

    if needs_txn or graph_transaction_available():
        try:
            async with postgres_graph_transaction() as transaction:
                return await _run(transaction)
        except OperationTransactionUnavailable:
            if needs_txn:
                return _refused(
                    "transaction_unavailable",
                    "Tags and relation links require a store that can host one transaction",
                )
            return await _run(_store())
        except Exception as exc:  # noqa: BLE001 — mid-facet failure rolls the txn
            refused = _refused("facet_rolled_back", str(exc) or "facet rolled back")
            refused["rolled_back"] = True
            return refused

    return await _run(_store())
