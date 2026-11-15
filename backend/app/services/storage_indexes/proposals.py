"""App-authored query needs, with no SQL or provisioning authority."""

import re


def normalize_requests(raw, *, queries, tracks):
    """Validate typed App proposals without granting provisioning authority."""
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > 16:
        raise ValueError("storage_index_requests must contain at most 16 proposals")
    query_keys = {q.get("key") for q in queries}
    types = {
        (t.get("key"), e.get("key")): e
        for t in tracks
        for e in t.get("entry_types", [])
    }
    result, seen = [], set()
    for request in raw:
        if not isinstance(request, dict) or set(request) - {
            "key",
            "query_key",
            "track_key",
            "entry_type_key",
            "fields",
            "reason",
            "state",
        }:
            raise ValueError(
                "Storage proposals cannot specify SQL, predicates, tables or physical names"
            )
        if request.get("state", "pending_platform_review") != "pending_platform_review":
            raise ValueError("Apps cannot approve storage index requests")
        key = request.get("key")
        if (
            not isinstance(key, str)
            or not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", key)
            or key in seen
        ):
            raise ValueError("Storage proposal keys must be valid and unique")
        seen.add(key)
        entry = types.get((request.get("track_key"), request.get("entry_type_key")))
        if request.get("query_key") not in query_keys or not entry:
            raise ValueError(
                "Storage proposals must reference declared queries and record types"
            )
        declared = {f.get("key"): f for f in entry.get("fields", [])}
        fields = request.get("fields")
        if not isinstance(fields, list) or not 1 <= len(fields) <= 4:
            raise ValueError("Storage proposals require one to four typed fields")
        normalized = []
        for field in fields:
            if not isinstance(field, dict) or set(field) != {"field", "direction"}:
                raise ValueError("A proposed key requires field and direction")
            name, direction = field["field"], field["direction"]
            if not isinstance(name, str) or not re.fullmatch(
                r"[A-Za-z_][A-Za-z0-9_]{0,63}", name
            ):
                raise ValueError("Invalid proposed field key")
            if name not in declared or declared[name].get("type") not in {
                "text",
                "string",
                "number",
                "integer",
                "boolean",
                "date",
                "datetime",
                "currency",
                "email",
                "url",
                "phone",
                "select",
                "relation",
                "user",
                "status",
            }:
                raise ValueError("Proposed fields must be declared scalar fields")
            if type(direction) is not int or direction not in {-1, 1}:
                raise ValueError("Proposed ordering must be 1 or -1")
            if name in {f["field"] for f in normalized}:
                raise ValueError("Proposed fields must be unique")
            normalized.append(dict(field))
        reason = request.get("reason", "")
        if not isinstance(reason, str) or len(reason) > 1000:
            raise ValueError("Invalid proposal reason")
        result.append(
            {**request, "fields": normalized, "state": "pending_platform_review"}
        )
    return result


def approved_specs(compiled, selected, *, schema="public", approvals=()):
    """Only separately qualified platform specifications can satisfy a request.

    A field's annotation cannot establish the physical expression, width safety
    or tenant scope of arbitrary handler code. Never guess a JSONB index from it.
    """
    from jvspatial.db.indexes import IndexSpec

    app = compiled.get("app") or {}
    package = compiled.get("package") or {}
    slug = package.get("slug", "")
    requests = {slug + ":" + r["key"]: r for r in app.get("storage_index_requests", [])}
    result = []
    for key in selected & requests.keys():
        choices = [a for a in approvals if a.get("request") == key]
        if not choices:
            raise ValueError(
                "App request requires a separately qualified platform index specification"
            )
        for approval in choices:
            if not re.fullmatch(
                r"[a-f0-9]{64}", approval.get("qualification_sha256", "")
            ):
                raise ValueError("Platform approval requires qualification evidence")
            spec = IndexSpec.from_dict(approval["spec"])
            if (
                spec.owner != "app:" + slug
                or spec.logical_id != key
                or spec.version != str(package.get("version", ""))
                or spec.schema != schema
                or spec.unique
                or spec.required
                or slug + ":" + requests[key]["query_key"] not in spec.queries
            ):
                raise ValueError(
                    "Platform specification does not bind the requested App/query revision"
                )
            result.append(spec)
    return result
