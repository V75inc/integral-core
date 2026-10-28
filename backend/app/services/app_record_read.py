"""Streaming authorized counts and bounded pages for App record tools."""

from __future__ import annotations

import base64
import hashlib
import heapq
import json
import math
import re
from collections import Counter
from decimal import Decimal, InvalidOperation
from functools import cmp_to_key

from jvspatial.core.pager import encode_keyset_cursor

from app.api.errors import BadRequestError
from app.middleware.permissions_cache import isolated_permissions_cache
from app.schemas.app_record_read import AppRecordRead
from app.services.entry_access_projection import (
    hydrate_records,
    iter_records,
    merge_queries,
)
from app.services.resource_location import ACTIVE_APP_STATES
from app.services.retrieval.app_search import _candidate_ids
from app.services.workspace_governance import ordinary_authority_only

_SKIP = frozenset({"body", "html", "content", "rendered_file", "editor_document"})
_IDENTITY = frozenset({"id", "title", "url", "track_id"})
_FIELD = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_-]{0,127}$")


def _field(value):
    key = value.removeprefix("custom_fields.")
    if not _FIELD.fullmatch(key) or key in _SKIP:
        raise BadRequestError(message="Select a supported record field")
    return key


def _filter(key, value):
    if value is None:
        return {key: None}
    if not isinstance(value, (str, bool, int, float)) or len(str(value)) > 120:
        raise BadRequestError(message="Record filters require short scalar values")
    if isinstance(value, float) and not math.isfinite(value):
        raise BadRequestError(message="Record filter must be finite")
    alternatives = [
        {key: {"$regex": "^" + re.escape(str(value)) + "$", "$options": "i"}}
    ]
    if not isinstance(value, str):
        alternatives.append({key: value})
    else:
        try:
            number = Decimal(value)
            if number.is_finite():
                alternatives.append({key: float(number)})
        except InvalidOperation:
            pass
        if value.casefold() in {"true", "false"}:
            alternatives.append({key: value.casefold() == "true"})
    return {"$or": alternatives}


def _short(value):
    if isinstance(value, str):
        return value[:157] + "..." if len(value) > 160 else value
    if isinstance(value, list):
        return [_short(v) for v in value[:10]]
    if isinstance(value, dict):
        return {k: _short(v) for k, v in list(value.items())[:10]}
    return value


async def read_app_records(ctx, raw):
    """Pages never count implicitly. Counts stream admitted raw windows.

    I-ROLE-01/02 and I-HARNESS-01: scope comes from the host context; every
    candidate window uses the canonical current audience/containment selector.
    I-SUBSTRATE-01/I-EXT-01: no App, Track or domain fields are selected by Core.
    """
    if not getattr(ctx, "app_id", ""):
        raise BadRequestError(message="Installed App scope required for record reads")
    data = AppRecordRead.model_validate(raw or {})
    # Grouping is only defined for counts. When the caller omits count_only,
    # treat its grouping request as an explicit count; a contradictory false
    # remains an error. Normalize before cursor fingerprinting.
    if data.group_by and "count_only" not in data.model_fields_set:
        data = data.model_copy(update={"count_only": True})
    fields = [_field(key) for key in data.fields]
    filters = {_field(key): value for key, value in data.filters.items()}
    group = _field(data.group_by) if data.group_by else ""
    sort = _field(data.sort_by) if data.sort_by else ""
    with ordinary_authority_only(), isolated_permissions_cache():
        tracks = await ctx._tracks_by_title(data.track)
        if not tracks:
            return {"ok": False, "reason": "Selected Track is unavailable"}
        selectors = []
        for track in tracks:
            selection, capped = await _candidate_ids(
                ctx, track.id, data.entry_type or None, None, metadata_only=True
            )
            if capped:
                raise BadRequestError(message="Narrow the Track/type selection")
            for track_id, types in selection.items():
                selector = {"context.track_id": track_id}
                if types is not None:
                    selector["context.type_id"] = {"$in": types}
                selectors.append(selector)
        query = {"$or": selectors} if selectors else {"id": {"$in": []}}
        query = merge_queries(
            query,
            *[
                _filter("context.custom_fields." + key, value)
                for key, value in filters.items()
            ],
        )
        if data.title_contains:
            query = merge_queries(
                query,
                {
                    "context.title": {
                        "$regex": re.escape(data.title_contains),
                        "$options": "i",
                    }
                },
            )
        # Cursor is an opaque continuation of this exact caller/selection, never
        # authority. Underlying audience admission is still live on every page.
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "app": ctx.app_id,
                    "workspace": ctx.workspace_id,
                    "principal": ctx.user_id,
                    "input": data.model_dump(exclude={"cursor"}),
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        position = None
        if data.cursor:
            try:
                payload = json.loads(base64.urlsafe_b64decode(data.cursor))
                if payload["selection"] != fingerprint:
                    raise ValueError()
                position = payload["position"]
            except (ValueError, KeyError, TypeError):
                raise BadRequestError(
                    message="Invalid record cursor; restart the read"
                ) from None
        common = {
            "workspace_id": ctx.workspace_id,
            "app_id": ctx.app_id,
            "allowed_app_states": ACTIVE_APP_STATES,
        }
        coverage = (
            "current caller-visible records in the owning App and selected Track/type"
        )
        if group and not data.count_only:
            raise BadRequestError(message="Grouped counts require count_only")
        if data.count_only:
            count, groups = 0, Counter()
            async for window in iter_records(ctx.user_id, query, **common):
                for row in window:
                    cf = row.get("context", {}).get("custom_fields") or {}
                    if any(
                        str(cf.get(key, "")).casefold() != str(value).casefold()
                        for key, value in filters.items()
                    ):
                        continue
                    count += 1
                    if group:
                        value = cf.get(group)
                        if (
                            not isinstance(value, (str, int, float, bool, type(None)))
                            or len(str(value)) > 160
                        ):
                            raise BadRequestError(
                                message="Grouping requires short scalar labels"
                            )
                        groups["(missing)" if value in (None, "") else str(value)] += 1
                        if len(groups) > 4096:
                            raise BadRequestError(
                                message="Narrow this high-cardinality grouping"
                            )
            return {
                "ok": True,
                "count": count,
                "count_is_exact": True,
                "groups": dict(
                    sorted(groups.items(), key=lambda item: (-item[1], item[0]))[:50]
                ),
                "group_count": len(groups),
                "groups_truncated": len(groups) > 50,
                "source_urls": [f"/tracks/{track.id}" for track in tracks],
                "coverage": coverage,
            }

        def matches(record):
            cf = record.get("context", {}).get("custom_fields") or {}
            return not any(
                str(cf.get(key, "")).casefold() != str(value).casefold()
                for key, value in filters.items()
            )

        selected = []
        if sort and sort != "id":

            def value(record):
                data = record.get("context") or {}
                result = (
                    data.get("title")
                    if sort == "title"
                    else (data.get("custom_fields") or {}).get(sort)
                )
                if not isinstance(result, (str, int, float, bool, type(None))):
                    raise BadRequestError(message="Sorting requires a scalar field")
                return result

            def scalar(raw):
                try:
                    number = Decimal(str(raw))
                    if number.is_finite():
                        return (0, number)
                except (InvalidOperation, ValueError):
                    pass
                return (1, str(raw).casefold())

            def compare(left, right):
                a, b = value(left), value(right)
                if a in (None, "") or b in (None, ""):
                    result = (a in (None, "")) - (b in (None, ""))
                else:
                    av, bv = scalar(a), scalar(b)
                    result = ((av > bv) - (av < bv)) * (
                        -1 if data.sort_direction == "desc" else 1
                    )
                return result or (
                    (left["id"] > right["id"]) - (left["id"] < right["id"])
                )

            after = None
            if position is not None:
                if (
                    not isinstance(position, dict)
                    or set(position) != {"id", "value"}
                    or not isinstance(position["id"], str)
                ):
                    raise BadRequestError(message="Invalid sorted record cursor")
                if not isinstance(
                    position["value"], (str, int, float, bool, type(None))
                ):
                    raise BadRequestError(message="Invalid sorted record cursor")
                after = {
                    "id": position["id"],
                    "context": {
                        "title": position["value"],
                        "custom_fields": {sort: position["value"]},
                    },
                }
            worst_first = cmp_to_key(lambda a, b: -compare(a, b))
            heap = []
            async for window in iter_records(ctx.user_id, query, **common):
                for record in window:
                    if not matches(record) or (
                        after is not None and compare(record, after) <= 0
                    ):
                        continue
                    item = worst_first(record)
                    if len(heap) < data.limit + 1:
                        heapq.heappush(heap, item)
                    elif compare(record, heap[0].obj) < 0:
                        heapq.heapreplace(heap, item)
            selected = sorted([item.obj for item in heap], key=cmp_to_key(compare))
            continuation = (
                {
                    "id": selected[data.limit - 1]["id"],
                    "value": value(selected[data.limit - 1]),
                }
                if len(selected) > data.limit
                else None
            )
        else:
            if position is not None and not isinstance(position, str):
                raise BadRequestError(message="Invalid record cursor")
            async for window in iter_records(
                ctx.user_id,
                query,
                cursor=position,
                batch_size=data.limit + 1,
                sort=[("id", 1)],
                **common,
            ):
                selected.extend(record for record in window if matches(record))
                if len(selected) > data.limit:
                    break
            continuation = (
                encode_keyset_cursor(selected[data.limit - 1], [("id", 1)])
                if len(selected) > data.limit
                else None
            )
        has_more = len(selected) > data.limit
        rows = await hydrate_records(selected[: data.limit])
        output = []
        for row in rows:
            cf = row.custom_fields or {}
            selected = (
                fields
                or [
                    key
                    for key, value in cf.items()
                    if not key.startswith("_")
                    and key not in _SKIP
                    and isinstance(value, (str, int, float, bool))
                    and value not in ("", None)
                ][:8]
            )
            item = {
                "id": row.id,
                "title": row.title,
                "track_id": row.track_id,
                "url": f"/entries/{row.id}",
            }
            item.update(
                {
                    key: _short(cf.get(key))
                    for key in selected
                    if key not in _SKIP | _IDENTITY
                }
            )
            output.append(item)
        cursor = (
            base64.urlsafe_b64encode(
                json.dumps(
                    {"selection": fingerprint, "position": continuation}
                ).encode()
            ).decode()
            if continuation
            else None
        )
        return {
            "ok": True,
            "count": None,
            "count_is_exact": False,
            "returned_count": len(output),
            "rows": output,
            "truncated": has_more,
            "next_cursor": cursor,
            "coverage": coverage,
            "source_urls": [f"/tracks/{track.id}" for track in tracks],
        }
