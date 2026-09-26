"""Auto-execute blessed staging tokens.

On bless, the API endpoint dispatches here to perform the actual write
the staging primitive was guarding. This eliminates the dependency on
the LLM correctly invoking ``execute_X`` after the user clicks Approve
— the bless itself is the trigger.

The dispatch table maps StagedChange ``kind`` strings to executor
callables. Each executor receives ``(user_id, payload)`` and returns
either a result dict or an error envelope. Mirrors the per-kind logic
that already lives in:

* ``agent/.../actions/integral/embedded_integral_action.py`` — the
  bridge action's per-write methods (FastAPI handler stub pattern)
* ``app/services/operational_model_authoring.py`` — profile mutations
* ``app/services/agent_insights.py`` — save_view
* ``app/agentive/tooling/stagers_filing.py`` — file_content staging

Implementation note: for write paths that go through FastAPI endpoint
handlers, we replicate the lightweight stub-request pattern from the
bridge action rather than importing it (the bridge lives outside the
backend package). The duplication is small and intentional — keeps
the staging executor self-contained and importable from the API layer
without crossing into the jvagent app's namespace.
"""

from __future__ import annotations

import logging
import re as _re
import types
from typing import Any, Awaitable, Callable, Dict, List, NamedTuple, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Stub request — mirrors the pattern in EmbeddedIntegralAction so the
# integral endpoint handlers can be invoked as regular Python coroutines.
# ---------------------------------------------------------------------------


def _stub_request(user: Any) -> Any:
    fake = types.SimpleNamespace()
    fake.state = types.SimpleNamespace(user=user)
    headers: Dict[str, str] = {}
    try:
        from app.services.agent_scope import current_scope_workspace_id

        ws_id = current_scope_workspace_id.get()
        if ws_id:
            headers["x-integral-scope"] = f"ws:{ws_id}"
    except Exception:  # noqa: BLE001
        logger.debug("agent_scope ContextVar unavailable", exc_info=True)
    fake.headers = headers
    fake.method = "GET"
    fake.url = types.SimpleNamespace(path="/agent-internal-staging")
    fake.query_params = {}
    fake.client = None
    return fake


async def _resolve_auth_user(user_id: str) -> Optional[Any]:
    """Load the AuthUser the integral handlers expect on request.state.

    Same resolution path the bridge action uses.
    """
    if not user_id:
        return None
    try:
        from jvspatial.api.auth.models import User as AuthUser
    except ImportError:
        from app.services.permissions import get_user_node

        return await get_user_node(user_id)
    return await AuthUser.get(user_id)


def _envelope_error(exc: BaseException) -> Dict[str, Any]:
    status_code = getattr(exc, "status_code", 500)
    code = getattr(exc, "error_code", None) or type(exc).__name__
    message = getattr(exc, "detail", None) or str(exc) or "Unknown error"
    envelope = {
        "error": True,
        "status_code": int(status_code) if status_code else 500,
        "error_code": code,
        "message": str(message),
    }
    details = getattr(exc, "details", None)
    if isinstance(details, dict):
        envelope["details"] = details
    return envelope


async def _call_endpoint(
    handler: Callable[..., Awaitable[Any]], user_id: str, **kwargs: Any
) -> Dict[str, Any]:
    user = await _resolve_auth_user(user_id)
    if not user:
        return {
            "error": True,
            "status_code": 401,
            "error_code": "user_not_found",
            "message": f"No Integral user with id {user_id!r}",
        }
    request = _stub_request(user)
    try:
        return await handler(request, **kwargs)
    except Exception as exc:  # noqa: BLE001
        if not getattr(exc, "status_code", None):
            logger.exception(
                "staging executor: %s raised",
                getattr(handler, "__name__", "endpoint"),
            )
        return _envelope_error(exc)


async def _call_endpoint_with_json(
    handler: Callable[..., Awaitable[Any]],
    user_id: str,
    json_body: Dict[str, Any],
    **kwargs: Any,
) -> Dict[str, Any]:
    """Invoke a handler that reads its inputs from ``await request.json()``."""
    user = await _resolve_auth_user(user_id)
    if not user:
        return {
            "error": True,
            "status_code": 401,
            "error_code": "user_not_found",
            "message": f"No Integral user with id {user_id!r}",
        }
    request = _stub_request(user)

    async def _json() -> Dict[str, Any]:
        return dict(json_body or {})

    request.json = _json  # type: ignore[method-assign]
    try:
        return await handler(request, **kwargs)
    except Exception as exc:  # noqa: BLE001
        if not getattr(exc, "status_code", None):
            logger.exception(
                "staging executor: %s raised",
                getattr(handler, "__name__", "endpoint"),
            )
        return _envelope_error(exc)


# ---------------------------------------------------------------------------
# Per-kind executors
# ---------------------------------------------------------------------------


async def _x_create_entry(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Create an entry via the integral REST handler.

    The handler's signature is::

        create_entry(request, track_id, title, type_id, body, description,
                     attachment_ids, custom_fields, tags)

    Note the kwarg names: ``type_id`` (an EntryType *ID*, not a name)
    and ``custom_fields`` (not ``fields``). Skill payloads use the
    user-friendlier ``entry_type`` (a NAME) and ``fields``; we
    translate them here. ``entry_type`` resolves to ``type_id`` via a
    lookup against the track's operational model; if the lookup fails
    (or the agent didn't provide a name), we drop it and let the
    handler pick the track's default entry type.

    Resilience: if custom_fields validation fails (e.g. entry type has
    an empty field schema so any key is rejected), we retry without
    custom_fields. Better to create the entry without structured fields
    than to fail entirely — the body text still carries the content.
    """
    from app.api.entries import create_entry as handler

    body: Dict[str, Any] = {"track_id": payload["track_id"], "title": payload["title"]}
    if payload.get("body"):
        body["body"] = payload["body"]
    if payload.get("fields"):
        body["custom_fields"] = payload["fields"]
    if payload.get("tags"):
        # Seeds and filings may name the Track's tags; ids pass through.
        from app.models.nodes import Tag

        track_tags = await Tag.find({"context.track_id": payload["track_id"]})
        tag_ids = {t.id for t in track_tags}
        by_name = {(t.name or "").casefold(): t.id for t in track_tags}
        resolved: List[str] = []
        unknown: List[str] = []
        for ref in payload["tags"]:
            ref = str(ref).strip()
            tag_id = ref if ref in tag_ids else by_name.get(ref.casefold())
            if tag_id:
                resolved.append(tag_id)
            else:
                unknown.append(ref)
        if unknown and payload.get("strict_fields"):
            return {
                "error": True,
                "status_code": 400,
                "error_code": "unknown_tag",
                "message": f"Tags not defined on this Track: {', '.join(unknown)}.",
            }
        body["tags"] = resolved + unknown
    # Resolve the entry-type name to an id if possible.
    entry_type_name = payload.get("entry_type")
    if entry_type_name:
        type_id = await _resolve_entry_type_id(payload["track_id"], entry_type_name)
        if type_id:
            body["type_id"] = type_id
        # If we can't resolve, omit type_id — the handler picks the
        # track's default entry type rather than erroring.
    result = await _call_endpoint(handler, user_id, **body)

    # Approved scaffolds promise exact sample data. A validation error must
    # stop the batch instead of silently dropping fields and reporting success.
    if payload.get("strict_fields"):
        return result

    # Retry on validation failure. Two common failure modes:
    #   1. Entry type has empty field schema → "Field X not allowed"
    #   2. Entry type has required field the agent didn't provide → "Field X is required"
    # First retry: drop custom_fields only (handles case 1).
    # Second retry: also drop type_id so handler picks the track's
    # default entry type (usually Post, which has no required fields).
    if result.get("error") and result.get("status_code") in (400, 422):
        if body.get("custom_fields"):
            # Prefer a SELECTIVE drop — peel off only the field named in the
            # error and retry with the rest — so one bad field (e.g.
            # employee="Founder" passed to a relation expecting an id) doesn't
            # silently discard every other valid field. Each retry may surface
            # the next offending field; bounded to avoid a pathological loop.
            surviving = dict(body["custom_fields"])
            dropped_keys: List[str] = []
            for _ in range(8):
                if not (
                    result.get("error")
                    and result.get("status_code") in (400, 422)
                    and surviving
                ):
                    break
                match = _re.search(
                    r"[Ff]ield '([^']+)'", str(result.get("message") or "")
                )
                bad = match.group(1) if match else None
                if not bad or bad not in surviving:
                    break  # can't localize → fall through to the blunt drop-all
                surviving.pop(bad, None)
                dropped_keys.append(bad)
                logger.info(
                    "create_entry: dropping bad field %r and retrying — %s",
                    bad,
                    result.get("message"),
                )
                retry_body = {**body}
                if surviving:
                    retry_body["custom_fields"] = surviving
                else:
                    retry_body.pop("custom_fields", None)
                result = await _call_endpoint(handler, user_id, **retry_body)
            if not result.get("error") and dropped_keys:
                # Surface WHICH fields were lost so the loss is auditable.
                result["fields_dropped"] = dropped_keys

        # Selective drop couldn't localize (or peeled everything) and we still
        # error: fall back to dropping the whole custom_fields block.
        if (
            result.get("error")
            and result.get("status_code") in (400, 422)
            and body.get("custom_fields")
        ):
            logger.info(
                "create_entry: retrying without custom_fields after %s — %s",
                result.get("error_code"),
                result.get("message"),
            )
            retry_body = {k: v for k, v in body.items() if k != "custom_fields"}
            result = await _call_endpoint(handler, user_id, **retry_body)
            if not result.get("error"):
                result.setdefault(
                    "fields_dropped", list((body.get("custom_fields") or {}).keys())
                )

        # Still failing (e.g. required field on the entry type itself)?
        # Drop type_id too — fall back to track's default entry type.
        if (
            result.get("error")
            and result.get("status_code") in (400, 422)
            and body.get("type_id")
        ):
            logger.info(
                "create_entry: retrying without type_id after %s — %s",
                result.get("error_code"),
                result.get("message"),
            )
            retry_body = {
                k: v for k, v in body.items() if k not in ("custom_fields", "type_id")
            }
            result = await _call_endpoint(handler, user_id, **retry_body)

    return result


def _entry_type_key(value: str) -> str:
    """Normalize an entry-type name OR key to a slug-key for comparison.

    "Time-off request" and "time_off_request" both normalize to
    "time_off_request" (lowercase; runs of non-alphanumerics → single ``_``).
    """
    return _re.sub(r"[^a-z0-9]+", "_", (value or "").casefold()).strip("_")


async def _resolve_entry_type_id(
    track_id: str,
    entry_type_name: str,
) -> Optional[str]:
    """Look up an EntryType id by name or key on a track.

    Returns ``None`` on miss so the caller can fall back to the track's
    default.
    """
    if not track_id or not entry_type_name:
        return None
    try:
        from app.models.nodes import Track
        from app.services.operational_model_runtime import resolve_track_runtime_profile

        track = await Track.get(track_id)
        if not track:
            return None
        cp, _, _ = await resolve_track_runtime_profile(track)
        if cp is None:
            return None
        types = await cp.nodes(edge=["CONTAINS"], node=["EntryType"])
        target = entry_type_name.casefold()
        # Agents most often pass the entry-type KEY (e.g. "time_off_request")
        # as surfaced by describe_operational_model, but EntryType nodes only store the
        # display NAME (e.g. "Time-off request"). Normalize both to a slug-key
        # so a key matches its type — without this the create silently fell
        # back to the track's default ("Post"), making every typed field
        # invalid and dropped.
        target_key = _entry_type_key(entry_type_name)
        for et in types:
            name = (getattr(et, "name", "") or "").casefold()
            if name == target or _entry_type_key(name) == target_key:
                return et.id
        # Loose match — substring.
        for et in types:
            name = (getattr(et, "name", "") or "").casefold()
            if target in name or name in target:
                return et.id
    except Exception:  # noqa: BLE001
        logger.debug("entry-type lookup failed", exc_info=True)
    return None


async def _x_update_entry(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Translate skill payload kwargs to update_entry handler names.

    Renames ``fields`` → ``custom_fields`` and ``entry_type`` →
    ``type_id``. Forwards ``status`` verbatim — the ``update_entry`` handler
    accepts it as a first-class kwarg (``status: Optional[str]``), and the
    ``integral_update_entry`` manifest tool advertises updating status, so a
    proposed status change must reach the handler rather than being dropped.
    """
    from app.api.entries import update_entry as handler

    entry_id = payload["entry_id"]
    body: Dict[str, Any] = {}
    if payload.get("title") is not None:
        body["title"] = payload["title"]
    if payload.get("body") is not None:
        body["body"] = payload["body"]
    if payload.get("fields") is not None:
        body["custom_fields"] = payload["fields"]
    if payload.get("tags") is not None:
        body["tags"] = payload["tags"]
    if payload.get("status") is not None:
        body["status"] = payload["status"]
    for revision_key in ("expected_record_revision", "expected_schema_revision"):
        if payload.get(revision_key) is not None:
            body[revision_key] = payload[revision_key]
    entry_type_name = payload.get("entry_type")
    if entry_type_name:
        # We need the track_id to resolve the entry type. Fetch the
        # entry to find it.
        try:
            from app.models.nodes import Entry

            entry = await Entry.get(entry_id)
            if entry and getattr(entry, "track_id", ""):
                type_id = await _resolve_entry_type_id(
                    entry.track_id,
                    entry_type_name,
                )
                if type_id:
                    body["type_id"] = type_id
        except Exception:  # noqa: BLE001
            pass
    return await _call_endpoint(handler, user_id, entry_id=entry_id, **body)


async def _x_delete_entry(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    from app.api.entries import delete_entry as handler

    return await _call_endpoint(handler, user_id, entry_id=payload["entry_id"])


async def _x_create_track(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    from app.api.tracks import create_track as handler
    from app.services.agent_scope import active_workspace_id

    # Validate before the first write, including direct/replayed executor calls.
    if payload.get("entry_types"):
        from app.services.operational_model_authoring import validate_inline_entry_types

        validate_inline_entry_types(payload["entry_types"])

    kwargs: Dict[str, Any] = {
        "title": payload["title"],
        "visibility": payload.get("visibility") or "private",
    }
    if payload.get("app_id"):
        # Track lives under the app → inherits the app's workspace; don't
        # override with a bound scope that could mismatch.
        kwargs["app_id"] = payload["app_id"]
    else:
        # Standalone track. ``resolve_workspace_id`` defaults a missing
        # workspace to the PERSONAL space (ignoring request scope), so bind the
        # active workspace explicitly — same fix as create_app. The BOUND scope
        # wins over the payload: a stager-supplied foreign ``workspace_id``
        # would otherwise land the track outside the workspace the card was
        # staged in. The payload is honored only under legacy union scope.
        workspace_id = active_workspace_id() or payload.get("workspace_id")
        if workspace_id:
            kwargs["workspace_id"] = workspace_id
    if payload.get("description"):
        kwargs["purpose"] = payload["description"]
    created = await _call_endpoint(handler, user_id, **kwargs)

    # Materialize inline entry types (with fields) onto the new track so a
    # custom-built track's "+New" form shows its declared fields instead of the
    # generic Post title/detail (June 29 QA #4). ``entry_types`` is the same
    # {name, icon?, fields:[…]} shape the agent produces for author_operational_model.
    if created.get("error"):
        return created
    track_obj = created.get("track")
    new_track_id = str(
        (track_obj.get("id") if isinstance(track_obj, dict) else "")
        or created.get("id")
        or ""
    )
    entry_types = payload.get("entry_types")
    if isinstance(entry_types, list) and entry_types and new_track_id:
        from app.services.operational_model_authoring import (
            apply_entry_types_to_track,
        )

        res = await apply_entry_types_to_track(
            user_id=user_id,
            track_id=new_track_id,
            entry_types=entry_types,
        )
        if res.get("error"):
            return {**res, "track": track_obj}
        created["entry_types_applied"] = res

    # Inline vocabulary goes through the public tag handler so policy, name
    # uniqueness, manifest sync and change events match a manual tag create.
    tag_groups = (payload.get("taxonomy") or {}).get("tag_groups") or []
    if tag_groups and new_track_id:
        from app.api.tags import create_tag as tag_handler

        tags_created: List[Dict[str, Any]] = []
        for group in tag_groups:
            group_ids: Dict[str, str] = {}
            for tag in group["tags"]:
                res = await _call_endpoint(
                    tag_handler,
                    user_id,
                    name=tag["name"],
                    track_id=new_track_id,
                    group_key=group["key"],
                    color=tag.get("color") or "#6B7280",
                    parent_tag_id=group_ids.get(
                        str(tag.get("parent") or "").casefold()
                    ),
                )
                tag_id = str((res.get("tag") or {}).get("id") or "")
                if res.get("error") or not tag_id:
                    return {**res, "error": True, "track": track_obj}
                group_ids[tag["name"].casefold()] = tag_id
                tags_created.append(
                    {"id": tag_id, "name": tag["name"], "group_key": group["key"]}
                )
        created["tags_created"] = tags_created
    return created


async def _x_update_track(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    from app.api.tracks import update_track as handler

    track_id = payload["track_id"]
    fields = {k: v for k, v in payload.items() if k != "track_id" and v is not None}
    return await _call_endpoint(handler, user_id, track_id=track_id, **fields)


async def _x_delete_track(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    from app.api.tracks import delete_track as handler

    return await _call_endpoint(handler, user_id, track_id=payload["track_id"])


async def _x_file_content(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """File-content commit via a single ``create_entry`` call.

    Tags are passed inline via the same handler now.

    Also records the acceptance into the personalization layer so
    silent-adapt can bias future similar filings. The skill-side
    execute_file_content.py records too, but skills are bypassed when
    backend auto-execute runs (which is the default path post-bless).
    Without this call the personalization layer never learns from
    real user behaviour.
    """
    tags = payload.get("tags") or []
    created = await _x_create_entry(user_id, payload)
    if created.get("error"):
        # Propagate the top-level "error" key so the bless endpoint and
        # frontend both detect the failure. Previously, wrapping the
        # error as {"filed": False, "entry": {error...}} lost the
        # top-level "error" key, causing the bless endpoint to consume
        # the token and the frontend to show "consumed" despite no entry
        # being created.
        logger.warning(
            "file_content executor: create_entry failed: %s — %s",
            created.get("error_code"),
            created.get("message"),
        )
        return {
            "error": True,
            "filed": False,
            "error_code": created.get("error_code", "create_entry_failed"),
            "message": created.get("message", "Entry creation failed"),
            "entry": created,
            "tags_applied": [],
        }

    # Record for personalization. Failure here is non-fatal — entry
    # already exists; learning is best-effort.
    try:
        from app.agentive.personalization import record_filing_acceptance

        await record_filing_acceptance(
            user_id=user_id,
            text=payload.get("body") or payload.get("title") or "",
            track_id=payload["track_id"],
            entry_type_name=payload.get("entry_type"),
            tags=tags,
        )
    except Exception:  # noqa: BLE001 — defensive
        logger.debug("personalization record failed", exc_info=True)

    return {
        "filed": True,
        "entry": created,
        "tags_applied": tags,
    }


async def _x_edit_comment(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Edit a comment via the ``update_comment`` REST handler."""
    from app.api.comments import update_comment as handler

    body: Dict[str, Any] = {
        "comment_id": payload["comment_id"],
        "text": payload.get("text") or payload.get("body") or "",
    }
    return await _call_endpoint(handler, user_id, **body)


async def _x_delete_comment(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    from app.api.comments import delete_comment as handler

    return await _call_endpoint(handler, user_id, comment_id=payload["comment_id"])


async def _x_delete_view(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    from app.api.views import delete_view as handler

    return await _call_endpoint(handler, user_id, view_id=payload["view_id"])


async def _x_add_comment(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Add a comment to an entry via the ``create_comment`` REST handler.

    The handler's signature is ``create_comment(request, entry_id, text,
    parent_id=None)``. The legacy ``integral_add_comment`` branch wrote a
    ``Comment`` node directly; porting to the route handler means the
    @endpoint's role gate (≥ commenter on the entry), the ``AUTHORED_BY`` /
    ``HAS_COMMENT`` edge wiring (I-GRAPH-01), @mention resolution, and the
    ``comment.create`` ChangeEvent all run — none of which the inline node
    create did. ``parent_id`` is forwarded only when present (threaded reply).
    """
    from app.api.comments import create_comment as handler

    body: Dict[str, Any] = {
        "entry_id": payload["entry_id"],
        "text": payload["body"],
    }
    if payload.get("parent_id"):
        body["parent_id"] = payload["parent_id"]
    return await _call_endpoint(handler, user_id, **body)


async def _x_create_app(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Create an App via the ``create_app`` REST handler.

    The handler routes through ``app_service.create_app_for_user`` →
    ``resolve_workspace_id``, which — when ``workspace_id`` is absent — defaults
    to the caller's PERSONAL workspace, ignoring the request scope entirely. So
    we MUST pass the bound workspace explicitly: bind it from the ``agent_scope``
    ContextVar (set by the dispatcher to the change's mint-time scope). The
    bound scope WINS over the payload — a stager-supplied ``workspace_id`` for a
    workspace the caller can also reach would otherwise escape the active-scope
    boundary the same way ``create_track``'s ``app_id`` did. The payload is
    honored only under legacy union scope (no workspace bound). Without either,
    every agent-staged app lands in the user's personal workspace instead of the
    org workspace it was staged in. Only allowlisted, handler-recognized keys
    are forwarded.
    """
    from app.api.apps import create_app as handler
    from app.services.agent_scope import active_workspace_id

    body: Dict[str, Any] = {"name": payload["name"]}
    workspace_id = active_workspace_id() or payload.get("workspace_id")
    if workspace_id:
        body["workspace_id"] = workspace_id
    if payload.get("description"):
        body["description"] = payload["description"]
    if payload.get("visibility"):
        body["visibility"] = payload["visibility"]
    if payload.get("type_hint"):
        body["type_hint"] = payload["type_hint"]
    return await _call_endpoint(handler, user_id, **body)


async def _x_draft_new_profile(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Create an empty library package draft via the service fn directly.

    Mirrors ``_x_author_operational_model``: a SERVICE-backed kind (no route handler). The
    legacy ``integral_draft_new_model`` branch calls
    ``operational_model_authoring.create_empty_library_draft(user_id, workspace_id, name,
    description, scope)``. ``workspace_id`` falls back to the bound agent scope
    so the draft lands in the workspace the caller is acting in (library
    profiles are workspace-scoped); the service itself enforces the
    can_publish_operational_models gate and wires the registry CATALOGS edge.
    """
    from app.services.agent_scope import current_scope_workspace_id
    from app.services.operational_model_authoring import (
        create_empty_library_draft as _impl,
    )

    workspace_id = payload.get("workspace_id") or current_scope_workspace_id.get() or ""
    return await _impl(
        user_id=user_id,
        workspace_id=workspace_id,
        name=payload["name"],
        description=payload.get("description"),
        scope=payload.get("scope") or "track",
    )


async def _x_author_operational_model(
    user_id: str, payload: Dict[str, Any]
) -> Dict[str, Any]:
    from app.services.agent_scope import current_scope_workspace_id
    from app.services.operational_model_authoring import (
        author_operational_model as _impl,
    )

    workspace_id = payload.get("workspace_id") or current_scope_workspace_id.get()
    return await _impl(
        user_id=user_id,
        description=payload["description"],
        scope=payload.get("scope") or "track",
        name=payload.get("name"),
        workspace_id=workspace_id,
        entry_types=payload.get("entry_types"),
    )


async def _x_modify_operational_model(
    user_id: str, payload: Dict[str, Any]
) -> Dict[str, Any]:
    from app.services.operational_model_authoring import (
        modify_operational_model as _impl,
    )

    forwarded = {
        k: v
        for k, v in payload.items()
        if k not in ("action", "track_id", "app_id") and v is not None
    }
    return await _impl(
        user_id=user_id,
        action=payload["action"],
        track_id=payload.get("track_id"),
        app_id=payload.get("app_id"),
        **forwarded,
    )


async def _x_apply_library_operational_model(
    user_id: str, payload: Dict[str, Any]
) -> Dict[str, Any]:
    from app.services.operational_model_authoring import (
        apply_library_operational_model as _impl,
    )

    return await _impl(
        user_id=user_id,
        library_operational_model_id=payload["library_operational_model_id"],
        track_id=payload.get("track_id"),
        app_id=payload.get("app_id"),
    )


_SHARE_COLLAB_HANDLERS = {
    "app": ("app.api.apps", "add_app_collaborator", "app_id"),
    "track": ("app.api.tracks", "add_collaborator", "track_id"),
    "entry": ("app.api.access", "add_entry_collaborator", "entry_id"),
}

_REMOVE_COLLAB_HANDLERS = {
    "app": ("app.api.apps", "remove_app_collaborator", "app_id"),
    "track": ("app.api.tracks", "remove_collaborator", "track_id"),
    "entry": ("app.api.access", "remove_entry_collaborator", "entry_id"),
}

_SET_EXCLUSION_HANDLERS = {
    "app": ("app.api.access", "add_space_exclusion", "app_id", "user_id_to_exclude"),
    "track": ("app.api.tracks", "add_exclusion", "track_id", "user_id_to_exclude"),
    "entry": (
        "app.api.access",
        "add_entry_exclusion",
        "entry_id",
        "user_id_to_exclude",
    ),
}

_REMOVE_EXCLUSION_HANDLERS = {
    "app": (
        "app.api.access",
        "remove_space_exclusion",
        "app_id",
        "user_id_to_restore",
    ),
    "track": ("app.api.tracks", "remove_exclusion", "track_id", "user_id_to_restore"),
    "entry": (
        "app.api.access",
        "remove_entry_exclusion",
        "entry_id",
        "user_id_to_restore",
    ),
}

_INVITE_HANDLERS = {
    "workspace": (
        "app.api.invitations",
        "post_create_invitation",
        "workspace_id",
    ),
    "app": ("app.api.invitations", "post_create_space_invitation", "app_id"),
    "track": ("app.api.invitations", "post_create_track_invitation", "track_id"),
    "entry": ("app.api.invitations", "post_create_entry_invitation", "entry_id"),
}


async def _resolve_collaborator_user_id(payload: Dict[str, Any]) -> Optional[str]:
    """Resolve the collaborator's user id from the staged payload.

    The payload may carry an explicit ``collaborator_user_id`` (a User/AuthUser
    id) or an ``email``. Email resolution mirrors the legacy ``integral_share``
    branch: look up the AuthUser by email and return its id — the collaborator
    handlers pass it through ``get_user_node`` (``app.services.permissions``),
    which resolves EITHER a User node id OR an AuthUser id, so the AuthUser id is
    the right thing to forward. Returns ``None`` on miss (caller fails closed).
    """
    explicit = payload.get("collaborator_user_id")
    if explicit:
        return explicit
    email = (payload.get("email") or "").strip()
    if not email:
        return None
    try:
        from app.bootstrap_admin import _find_user_by_email

        auth_user = await _find_user_by_email(email)
        if auth_user is not None:
            return auth_user.id
    except Exception:  # noqa: BLE001 — resolution failure → fail closed below
        logger.debug("share: email→user resolution failed", exc_info=True)
    return None


async def _x_share(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Add a collaborator on a resource via the real collaborator REST handler.

    Polymorphic over ``resource_type`` ∈ ``app | track | entry``; each routes to
    a SEPARATE handler taking the resource id under its own path-kwarg name
    (``app_id`` / ``track_id`` / ``entry_id``). The legacy ``integral_share``
    branch wrote a ``COLLABORATES_ON`` edge inline (and only for track/app);
    porting to the route handler means the @endpoint's owner-only share-authority
    gate, the auto-guest workspace-membership rule, the edge upsert, and the
    ``share.collaborator_added`` notification + ChangeEvent all run — none of
    which the inline edge connect did, and entry is now supported too.

    The legacy branch resolved a ``collaborator_email`` to a user; that
    resolution lives in :func:`_resolve_collaborator_user_id` here (a substrate
    read that belongs on bless, not at stage time). An unknown
    ``resource_type``, a missing resource id, or an unresolvable collaborator all
    fail closed.

    Share-link minting (the manifest's other ``integral_share`` mode) is NOT
    performed here: the legacy branch only ever added collaborators, and the
    dedicated mint path (``/{resource}/{id}/shares``) is a separate tool. This
    executor faithfully replicates the legacy collaborator behavior.
    """
    resource_type = payload.get("resource_type")
    resource_id = payload.get("resource_id")
    route = _SHARE_COLLAB_HANDLERS.get(resource_type or "")
    if route is None:
        return {
            "error": True,
            "status_code": 400,
            "error_code": "invalid_resource_type",
            "message": (
                f"resource_type must be one of {sorted(_SHARE_COLLAB_HANDLERS)}; "
                f"got {resource_type!r}"
            ),
        }
    if not resource_id:
        return {
            "error": True,
            "status_code": 400,
            "error_code": "resource_id_required",
            "message": "share: resource_id is required",
        }
    collaborator_user_id = await _resolve_collaborator_user_id(payload)
    if not collaborator_user_id:
        return {
            "error": True,
            "status_code": 404,
            "error_code": "collaborator_not_found",
            "message": (
                "share: could not resolve a collaborator (supply a known email "
                "or collaborator_user_id)"
            ),
        }

    modpath, handler_name, path_kwarg = route
    import importlib

    handler = getattr(importlib.import_module(modpath), handler_name)
    body: Dict[str, Any] = {
        path_kwarg: resource_id,
        "collaborator_user_id": collaborator_user_id,
    }
    if payload.get("role"):
        body["role"] = payload["role"]
    return await _call_endpoint(handler, user_id, **body)


async def _x_remove_collaborator(
    user_id: str, payload: Dict[str, Any]
) -> Dict[str, Any]:
    """Remove a direct collaborator grant via the per-resource REST handler."""
    resource_type = payload.get("resource_type")
    resource_id = payload.get("resource_id")
    collaborator_user_id = payload.get("user_id") or payload.get("collaborator_user_id")
    route = _REMOVE_COLLAB_HANDLERS.get(resource_type or "")
    if route is None:
        return {
            "error": True,
            "status_code": 400,
            "error_code": "invalid_resource_type",
            "message": (
                f"resource_type must be one of {sorted(_REMOVE_COLLAB_HANDLERS)}; "
                f"got {resource_type!r}"
            ),
        }
    if not resource_id or not collaborator_user_id:
        return {
            "error": True,
            "status_code": 400,
            "error_code": "missing_fields",
            "message": "remove_collaborator: resource_id and user_id are required",
        }
    modpath, handler_name, path_kwarg = route
    import importlib

    handler = getattr(importlib.import_module(modpath), handler_name)
    return await _call_endpoint(
        handler,
        user_id,
        **{path_kwarg: resource_id, "collaborator_user_id": collaborator_user_id},
    )


async def _x_set_exclusion(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    resource_type = payload.get("resource_type")
    resource_id = payload.get("resource_id")
    excluded_user_id = payload.get("user_id")
    route = _SET_EXCLUSION_HANDLERS.get(resource_type or "")
    if route is None:
        return {
            "error": True,
            "status_code": 400,
            "error_code": "invalid_resource_type",
            "message": (
                f"resource_type must be one of {sorted(_SET_EXCLUSION_HANDLERS)}; "
                f"got {resource_type!r}"
            ),
        }
    if not resource_id or not excluded_user_id:
        return {
            "error": True,
            "status_code": 400,
            "error_code": "missing_fields",
            "message": "set_exclusion: resource_id and user_id are required",
        }
    modpath, handler_name, path_kwarg, user_kwarg = route
    import importlib

    handler = getattr(importlib.import_module(modpath), handler_name)
    return await _call_endpoint(
        handler,
        user_id,
        **{path_kwarg: resource_id, user_kwarg: excluded_user_id},
    )


async def _x_remove_exclusion(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    resource_type = payload.get("resource_type")
    resource_id = payload.get("resource_id")
    restored_user_id = payload.get("user_id")
    route = _REMOVE_EXCLUSION_HANDLERS.get(resource_type or "")
    if route is None:
        return {
            "error": True,
            "status_code": 400,
            "error_code": "invalid_resource_type",
            "message": (
                f"resource_type must be one of {sorted(_REMOVE_EXCLUSION_HANDLERS)}; "
                f"got {resource_type!r}"
            ),
        }
    if not resource_id or not restored_user_id:
        return {
            "error": True,
            "status_code": 400,
            "error_code": "missing_fields",
            "message": "remove_exclusion: resource_id and user_id are required",
        }
    modpath, handler_name, path_kwarg, user_kwarg = route
    import importlib

    handler = getattr(importlib.import_module(modpath), handler_name)
    return await _call_endpoint(
        handler,
        user_id,
        **{path_kwarg: resource_id, user_kwarg: restored_user_id},
    )


async def _x_mint_share_link(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.share_links import mint_share_link

    resource_type = payload.get("resource_type")
    resource_id = payload.get("resource_id")
    if resource_type not in _SHARE_COLLAB_HANDLERS or not resource_id:
        return {
            "error": True,
            "status_code": 400,
            "error_code": "invalid_resource",
            "message": "mint_share_link: resource_type and resource_id are required",
        }
    return await mint_share_link(
        user_id,
        resource_type,
        resource_id,
        payload.get("role") or "viewer",
        payload.get("expires_at"),
    )


async def _x_revoke_share_link(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    from app.api.shares import revoke_link as handler

    share_link_id = payload.get("share_link_id")
    if not share_link_id:
        return {
            "error": True,
            "status_code": 400,
            "error_code": "share_link_id_required",
            "message": "revoke_share_link: share_link_id is required",
        }
    return await _call_endpoint(handler, user_id, share_link_id=share_link_id)


async def _x_invite(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    target_type = payload.get("target_type")
    target_id = payload.get("target_id")
    email = (payload.get("email") or "").strip()
    route = _INVITE_HANDLERS.get(target_type or "")
    if route is None:
        return {
            "error": True,
            "status_code": 400,
            "error_code": "invalid_target_type",
            "message": (
                f"target_type must be one of {sorted(_INVITE_HANDLERS)}; "
                f"got {target_type!r}"
            ),
        }
    if not target_id or not email:
        return {
            "error": True,
            "status_code": 400,
            "error_code": "missing_fields",
            "message": "invite: target_id and email are required",
        }
    modpath, handler_name, path_kwarg = route
    import importlib

    handler = getattr(importlib.import_module(modpath), handler_name)
    body: Dict[str, Any] = {
        path_kwarg: target_id,
        "email": email,
        "role": payload.get("role")
        or ("member" if target_type == "workspace" else "commenter"),
    }
    if payload.get("message"):
        body["message"] = payload["message"]
    return await _call_endpoint(handler, user_id, **body)


async def _x_attach_file(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.attachment_agent import attach_sandbox_file_to_entry

    return await attach_sandbox_file_to_entry(
        user_id=user_id,
        entry_id=payload["entry_id"],
        sandbox_path=payload["sandbox_path"],
    )


async def _x_attach_uploaded_file(
    user_id: str, payload: Dict[str, Any]
) -> Dict[str, Any]:
    from app.services.attachment_agent import attach_uploaded_file_to_entry

    return await attach_uploaded_file_to_entry(
        user_id=user_id,
        attachment_id=payload["attachment_id"],
        entry_id=payload["entry_id"],
    )


async def _x_attach_uploaded_image(
    user_id: str, payload: Dict[str, Any]
) -> Dict[str, Any]:
    import base64

    from app.services.attachment_agent import attach_image_bytes_to_entry

    try:
        content = base64.b64decode(payload.get("content_b64") or "")
    except Exception:  # noqa: BLE001 — malformed base64 -> empty, handled below
        content = b""
    return await attach_image_bytes_to_entry(
        user_id=user_id,
        entry_id=payload["entry_id"],
        filename=payload.get("filename") or "pasted-image.png",
        mime_type=payload.get("mime_type") or "image/png",
        content=content,
    )


async def _x_author_skill(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.agent_scope import current_scope_workspace_id
    from app.services.agent_skill_authoring import author_skill_for_agent

    workspace_id = current_scope_workspace_id.get()
    if not workspace_id:
        return {
            "error": True,
            "error_code": "no_active_workspace",
            "message": "No active workspace scope for this turn",
        }
    return await author_skill_for_agent(
        user_id=user_id,
        workspace_id=workspace_id,
        key=payload.get("key"),
        name=payload["name"],
        description=payload["description"],
        body_override=payload["body_override"],
        tools_required=payload.get("tools_required") or [],
        app_id=payload.get("app_id"),
        private=payload.get("private"),
    )


async def _x_update_skill(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.agent_scope import current_scope_workspace_id
    from app.services.agent_skill_authoring import update_skill_for_agent

    workspace_id = current_scope_workspace_id.get()
    if not workspace_id:
        return {
            "error": True,
            "error_code": "no_active_workspace",
            "message": "No active workspace scope for this turn",
        }
    return await update_skill_for_agent(
        user_id=user_id,
        workspace_id=workspace_id,
        skill_id=payload["skill_id"],
        name=payload.get("name"),
        description=payload.get("description"),
        body_override=payload.get("body_override"),
        tools_required=payload.get("tools_required"),
        enabled=payload.get("enabled"),
        private=payload.get("private"),
    )


async def _x_delete_skill(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.agent_scope import current_scope_workspace_id
    from app.services.agent_skill_authoring import delete_skill_for_agent

    workspace_id = current_scope_workspace_id.get()
    if not workspace_id:
        return {
            "error": True,
            "error_code": "no_active_workspace",
            "message": "No active workspace scope for this turn",
        }
    return await delete_skill_for_agent(
        user_id=user_id, workspace_id=workspace_id, skill_id=payload["skill_id"]
    )


async def _x_resolve_conflict(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    from app.api.conflicts import resolve as handler

    conflict_id = payload.get("conflict_id")
    resolution = payload.get("resolution")
    if not conflict_id or not resolution:
        return {
            "error": True,
            "status_code": 400,
            "error_code": "missing_fields",
            "message": "resolve_conflict: conflict_id and resolution are required",
        }
    return await _call_endpoint_with_json(
        handler,
        user_id,
        {"resolution": resolution},
        conflict_id=conflict_id,
    )


async def _x_trigger_sync(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    from app.api.connectors import sync_connector as handler

    connector_id = payload.get("connector_id")
    if not connector_id:
        return {
            "error": True,
            "status_code": 400,
            "error_code": "connector_id_required",
            "message": "trigger_sync: connector_id is required",
        }
    return await _call_endpoint(handler, user_id, connector_id=connector_id)


async def _x_call_workspace_tool(
    user_id: str, payload: Dict[str, Any]
) -> Dict[str, Any]:
    """Run a staged workspace-tool invoke (write tools only)."""
    from app.services.agent_scope import current_scope_workspace_id
    from app.services.workspace_tools import invoke_workspace_tool

    tool_key = str(payload.get("tool_key") or "").strip()
    if not tool_key:
        return {
            "error": True,
            "status_code": 400,
            "error_code": "tool_key_required",
            "message": "call_workspace_tool: tool_key is required",
        }
    workspace_id = current_scope_workspace_id.get() or ""
    try:
        output = await invoke_workspace_tool(
            user_id=user_id,
            workspace_id=workspace_id,
            tool_key=tool_key,
            payload=dict(payload.get("input") or {}),
            agent=True,
        )
    except Exception as exc:  # noqa: BLE001 — surface as envelope
        return _envelope_error(exc)
    return {"ok": True, "tool_key": tool_key, "output": output}


async def _x_save_view(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.agent_insights import save_view as _impl

    return await _impl(
        user_id=user_id,
        track_id=payload["track_id"],
        name=payload["name"],
        view_type=payload.get("view_type") or "feed",
        config=payload.get("config") or {},
        is_default=bool(payload.get("is_default")),
    )


async def _x_create_dashboard(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    from app.api.apps_dashboards import create_app_dashboard

    body: Dict[str, Any] = {
        "name": payload["name"],
        "is_default": bool(payload.get("is_default", False)),
    }
    if payload.get("layout") is not None:
        body["layout"] = payload["layout"]
    if payload.get("widgets") is not None:
        body["widgets"] = payload["widgets"]
    return await _call_endpoint_with_json(
        create_app_dashboard,
        user_id,
        body,
        app_id=payload["app_id"],
    )


async def _x_update_dashboard(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    from app.api.apps_dashboards import patch_app_dashboard

    body: Dict[str, Any] = {}
    if payload.get("name") is not None:
        body["name"] = payload["name"]
    if payload.get("layout") is not None:
        body["layout"] = payload["layout"]
    if payload.get("widgets") is not None:
        body["widgets"] = payload["widgets"]
    if payload.get("is_default") is not None:
        body["is_default"] = payload["is_default"]
    return await _call_endpoint_with_json(
        patch_app_dashboard,
        user_id,
        body,
        app_id=payload["app_id"],
        dashboard_id=payload["dashboard_id"],
    )


async def _x_delete_dashboard(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    from app.api.apps_dashboards import delete_app_dashboard

    return await _call_endpoint(
        delete_app_dashboard,
        user_id,
        app_id=payload["app_id"],
        dashboard_id=payload["dashboard_id"],
    )


# ---- routine tasks (P_scheduling) ----------------------------------------- #
async def _x_routine_task_create(
    user_id: str, payload: Dict[str, Any]
) -> Dict[str, Any]:
    from app.agentive.services.routine_tasks import create_routine_task
    from app.services.agent_scope import active_workspace_id

    routine = await create_routine_task(
        user_id=user_id,
        workspace_id=payload.get("workspace_id") or active_workspace_id() or "",
        thread_id=payload["thread_id"],
        agent_id=payload.get("agent_id") or "",
        instruction=payload["instruction"],
        cron=payload.get("cron") or "",
        timezone=payload.get("timezone") or "UTC",
        write_scope=payload.get("write_scope") or [],
        max_runs=payload.get("max_runs"),
        run_at=payload.get("run_at"),
    )
    from app.services.change_event import emit_change_event

    await emit_change_event(
        actor_kind="agent",
        actor_id=user_id,
        action="routine_task.create",
        resource_type="RoutineTask",
        resource_id=routine.id,
        before=None,
        after={"id": routine.id, "cron": routine.cron, "status": routine.status},
        scope=f"workspace:{routine.workspace_id}",
    )
    return {
        "id": routine.id,
        "instruction": routine.instruction,
        "cron": routine.cron,
        "timezone": routine.timezone,
        "next_run_at": routine.next_run_at,
        "max_runs": routine.max_runs,
    }


async def _x_routine_task_update(
    user_id: str, payload: Dict[str, Any]
) -> Dict[str, Any]:
    from app.agentive.services.routine_tasks import update_routine_task

    routine = await update_routine_task(
        user_id=user_id,
        routine_id=payload["routine_id"],
        status=payload.get("status"),
        instruction=payload.get("instruction"),
        cron=payload.get("cron"),
        timezone=payload.get("timezone"),
        write_scope=payload.get("write_scope"),
        max_runs=payload.get("max_runs"),
        clear_max_runs=bool(payload.get("clear_max_runs")),
    )
    from app.services.change_event import emit_change_event

    await emit_change_event(
        actor_kind="agent",
        actor_id=user_id,
        action="routine_task.update",
        resource_type="RoutineTask",
        resource_id=routine.id,
        before=None,
        after={"id": routine.id, "status": routine.status, "cron": routine.cron},
        scope=f"workspace:{routine.workspace_id}",
    )
    return {
        "id": routine.id,
        "status": routine.status,
        "cron": routine.cron,
        "next_run_at": routine.next_run_at,
    }


async def _x_routine_task_cancel(
    user_id: str, payload: Dict[str, Any]
) -> Dict[str, Any]:
    from app.agentive.services.routine_tasks import cancel_routine_task

    routine = await cancel_routine_task(
        user_id=user_id, routine_id=payload["routine_id"]
    )
    from app.services.change_event import emit_change_event

    await emit_change_event(
        actor_kind="agent",
        actor_id=user_id,
        action="routine_task.delete",
        resource_type="RoutineTask",
        resource_id=routine.id,
        before=None,
        after={"id": routine.id, "status": routine.status},
        scope=f"workspace:{routine.workspace_id}",
    )
    return {"id": routine.id, "status": routine.status}


async def _x_routine_task_purge(
    user_id: str, payload: Dict[str, Any]
) -> Dict[str, Any]:
    from app.agentive.services.routine_tasks import (
        delete_routine_task,
        get_routine_task,
    )

    routine = await get_routine_task(user_id=user_id, routine_id=payload["routine_id"])
    workspace_id = routine.workspace_id
    prior_status = routine.status
    deleted_id = await delete_routine_task(
        user_id=user_id, routine_id=payload["routine_id"]
    )
    from app.services.change_event import emit_change_event

    await emit_change_event(
        actor_kind="agent",
        actor_id=user_id,
        action="routine_task.purge",
        resource_type="RoutineTask",
        resource_id=deleted_id,
        before={"id": deleted_id, "status": prior_status},
        after=None,
        scope=f"workspace:{workspace_id}",
    )
    return {"id": deleted_id, "deleted": True}


# Pillar 3 — agent-authorable substrate. Each kind below pairs with a tool
# in the manifest catalogue (``app.agentive.tooling``) and the matching helper
# in ``app.services.operational_model_authoring``. ``apply_to_draft`` is the alias for
# ``propose_profile_revision`` when the agent splits the patch into
# multiple smaller blessings.


async def _x_propose_profile_revision(
    user_id: str, payload: Dict[str, Any]
) -> Dict[str, Any]:
    from app.services.operational_model_authoring import apply_patch_to_draft as _impl

    return await _impl(
        user_id=user_id,
        draft_id=payload["draft_id"],
        operations=payload.get("operations") or [],
    )


async def _x_publish_profile_draft(
    user_id: str, payload: Dict[str, Any]
) -> Dict[str, Any]:
    from app.services.operational_model_authoring import (
        publish_draft_for_agent as _impl,
    )

    return await _impl(
        user_id=user_id,
        draft_id=payload["draft_id"],
        run_migrations=bool(payload.get("run_migrations", True)),
        abort_on_migration_failure=bool(
            payload.get("abort_on_migration_failure", True)
        ),
    )


async def _x_discard_profile_draft(
    user_id: str, payload: Dict[str, Any]
) -> Dict[str, Any]:
    from app.services.operational_model_authoring import (
        discard_draft_for_agent as _impl,
    )

    return await _impl(
        user_id=user_id,
        draft_id=payload["draft_id"],
    )


# ---------------------------------------------------------------------------
# Scope binding for bless / text-approve apply paths
# ---------------------------------------------------------------------------


async def resolve_executor_workspace_id(request: Any, user_id: str) -> Optional[str]:
    """Resolve active workspace from request header or personal-workspace fallback."""
    from app.agentive.services.tool_scope import workspace_id_from_scope
    from app.services.request_scope import resolve_workspace_id_from_request
    from app.services.scope_header import resolve_scope_from_request

    scope_dict = resolve_scope_from_request(request)
    ws = workspace_id_from_scope(scope_dict)
    if ws:
        return ws
    return await resolve_workspace_id_from_request(request, user_id)


async def dispatch_under_scope(
    *,
    user_id: str,
    kind: str,
    payload: Dict[str, Any],
    workspace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Run ``dispatch`` with ``current_scope_workspace_id`` bound when provided."""
    from app.services.agent_scope import current_scope_workspace_id

    token = None
    if workspace_id:
        token = current_scope_workspace_id.set(workspace_id)
    try:
        return await dispatch(user_id=user_id, kind=kind, payload=payload)
    finally:
        if token is not None:
            current_scope_workspace_id.reset(token)


async def dispatch_under_request_scope(
    *,
    request: Any,
    user_id: str,
    kind: str,
    payload: Dict[str, Any],
    preferred_workspace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Bless / text-approve entry: bind the execution workspace scope.

    ``preferred_workspace_id`` (the scope captured on the StagedChange at mint
    time) wins when present — the FE bless request can arrive without an
    ``X-Integral-Scope`` header, in which case ``resolve_executor_workspace_id``
    falls back to the caller's PERSONAL workspace and the write lands there
    instead of the org workspace it was staged in. Falls back to the request
    scope for legacy tokens minted before scope capture.
    """
    workspace_id = preferred_workspace_id or await resolve_executor_workspace_id(
        request, user_id
    )
    return await dispatch_under_scope(
        user_id=user_id,
        kind=kind,
        payload=payload,
        workspace_id=workspace_id,
    )


class _ScopeRule(NamedTuple):
    """How a staging kind names the resource its write will land on.

    ``scope`` picks the checker (``track`` / ``entry`` / ``resource``).
    ``keys`` are payload keys carrying a single id — for track/entry scopes
    EVERY present key is checked (``link_entries`` touches two entries), for
    ``resource`` scope the FIRST present one wins (it is one target under
    alternate names). ``list_keys`` carry a list of ids (the bulk kinds).
    ``resolve`` names an indirection that must be walked before a check is even
    possible: a comment / view / share-link id does not name its own scope.
    ``type_keys`` / ``default_type`` supply ``resource_type`` for the resource
    checker. ``optional`` marks a rule whose id key may legitimately be absent
    (``create_track``'s ``app_id``): with it set, a missing id is "nothing to
    gate" instead of the resource checker's ``resource_id is required``
    violation.
    """

    scope: str
    keys: Tuple[str, ...] = ()
    list_keys: Tuple[str, ...] = ()
    resolve: Optional[str] = None
    type_keys: Tuple[str, ...] = ("resource_type",)
    default_type: str = "app"
    optional: bool = False


_SCOPE_TRACK = "track"
_SCOPE_ENTRY = "entry"
_SCOPE_RESOURCE = "resource"

_TRACK_RULE = _ScopeRule(_SCOPE_TRACK, keys=("track_id",))
_ENTRY_RULE = _ScopeRule(_SCOPE_ENTRY, keys=("entry_id",))
_RESOURCE_RULE = _ScopeRule(_SCOPE_RESOURCE, keys=("resource_id", "app_id"))

# B-AGENT-03 defense-in-depth: ONE declarative table, so a new staging kind
# cannot silently miss the gate. ``test_staging_executor_scope`` asserts every
# kind in ``_EXECUTORS`` that names a track/entry/app-shaped target has a row
# here — add the kind to both places or the test fails.
#
# Without a row, a card staged in workspace A that carries an id the caller can
# also reach in workspace B lands the write in B. The route handlers'
# ``resolve_role`` still applies, so this is a scope leak, not a privilege
# escalation — but the active workspace is meant to be a hard boundary.
_KIND_SCOPE_RULES: Dict[str, _ScopeRule] = {
    # --- track-scoped -----------------------------------------------------
    "create_entry": _TRACK_RULE,
    "file_content": _TRACK_RULE,
    "save_view": _TRACK_RULE,
    "update_track": _TRACK_RULE,
    "delete_track": _TRACK_RULE,
    "create_tag": _TRACK_RULE,
    # A view id names no scope of its own — resolve it to its track first.
    "delete_view": _ScopeRule(_SCOPE_TRACK, resolve="view"),
    # --- entry-scoped -----------------------------------------------------
    "update_entry": _ENTRY_RULE,
    "delete_entry": _ENTRY_RULE,
    "add_comment": _ENTRY_RULE,
    "transform_entry": _ENTRY_RULE,
    "attach_file": _ENTRY_RULE,
    "attach_uploaded_file": _ENTRY_RULE,
    "attach_uploaded_image": _ENTRY_RULE,
    "add_entry_tag": _ENTRY_RULE,
    "remove_entry_tag": _ENTRY_RULE,
    # Both ends of the relation are written targets. ``target_id`` may name a
    # TRACK (anchor-pattern relations); the entry checker treats a non-entry id
    # as "nothing to gate" rather than refusing, so this is safe.
    "link_entries": _ScopeRule(
        _SCOPE_ENTRY,
        keys=("entry_id", "source_entry_id", "target_entry_id", "target_id"),
    ),
    # The bulk kinds loop the single-entry executors DIRECTLY (they never go
    # back through ``dispatch``), so this row is the only gate their ids get.
    "bulk_update_entries": _ScopeRule(_SCOPE_ENTRY, list_keys=("entry_ids",)),
    "bulk_delete_entries": _ScopeRule(_SCOPE_ENTRY, list_keys=("entry_ids",)),
    # A comment id names no scope of its own — resolve it to its parent entry.
    "edit_comment": _ScopeRule(_SCOPE_ENTRY, resolve="comment"),
    "delete_comment": _ScopeRule(_SCOPE_ENTRY, resolve="comment"),
    # --- resource-scoped --------------------------------------------------
    "share": _RESOURCE_RULE,
    "remove_collaborator": _RESOURCE_RULE,
    "set_exclusion": _RESOURCE_RULE,
    "remove_exclusion": _RESOURCE_RULE,
    "mint_share_link": _RESOURCE_RULE,
    "create_dashboard": _RESOURCE_RULE,
    "update_dashboard": _RESOURCE_RULE,
    "delete_dashboard": _RESOURCE_RULE,
    # ``create_track`` is workspace-bound only on its STANDALONE path (the
    # executor binds the active scope itself). When the card carries an
    # ``app_id``, the App decides the workspace — ``track_service`` derives it
    # from the app and checks only ``app.update`` + ``can_create_track_under_
    # workspace``, both of which pass for any workspace the caller belongs to.
    # So gate the app_id key, and only that key (``optional`` keeps the
    # standalone path ungated rather than refusing it for a missing id).
    "create_track": _ScopeRule(_SCOPE_RESOURCE, keys=("app_id",), optional=True),
    "update_app": _ScopeRule(_SCOPE_RESOURCE, keys=("app_id",)),
    "register_track_template": _ScopeRule(_SCOPE_RESOURCE, keys=("app_id",)),
    "delete_app": _ScopeRule(_SCOPE_RESOURCE, keys=("app_id",)),
    "invite": _ScopeRule(
        _SCOPE_RESOURCE, keys=("target_id",), type_keys=("target_type",)
    ),
    # A share-link id carries its own resource_type/resource_id — read them off
    # the link rather than trusting the (absent) payload fields.
    "revoke_share_link": _ScopeRule(_SCOPE_RESOURCE, resolve="share_link"),
}


def _first_present(payload: Dict[str, Any], keys: Tuple[str, ...]) -> str:
    for key in keys:
        val = payload.get(key)
        if isinstance(val, str) and val:
            return val
    return ""


async def _resolve_scope_ids(rule: _ScopeRule, payload: Dict[str, Any]) -> List[str]:
    """Walk ``rule.resolve``'s indirection to the ids the checker understands.

    Returns ``[]`` when the referenced node is missing — the executor's own
    handler then produces the 404, which is a better error than a scope
    violation for something that does not exist.
    """
    if rule.resolve == "comment":
        from app.models.nodes import Comment

        comment_id = _first_present(payload, ("comment_id",))
        if not comment_id:
            return []
        comment = await Comment.get(comment_id)
        if comment is None:
            return []
        parents = await comment.nodes(
            edge=["HAS_COMMENT"], direction="in", node=["Entry"]
        )
        return [p.id for p in parents if getattr(p, "id", None)]
    if rule.resolve == "view":
        from app.models.nodes import View

        view_id = _first_present(payload, ("view_id",))
        if not view_id:
            return []
        view = await View.get(view_id)
        track_id = getattr(view, "track_id", "") if view is not None else ""
        return [track_id] if track_id else []
    return []


async def _validate_resource_scope(
    rule: _ScopeRule,
    payload: Dict[str, Any],
    *,
    user_id: str,
) -> Optional[Dict[str, Any]]:
    """Resource-shaped gate: resolve (resource_type, resource_id) then check."""
    from app.services.agent_scope import check_resource_in_active_scope

    if rule.resolve == "share_link":
        from app.models.nodes import ShareLink

        link_id = _first_present(payload, ("share_link_id",))
        if not link_id:
            return None
        link = await ShareLink.get(link_id)
        if link is None:
            return None
        resource_type = getattr(link, "resource_type", "") or rule.default_type
        resource_id = getattr(link, "resource_id", "") or ""
        if not resource_id:
            return None
        return await check_resource_in_active_scope(
            resource_type, resource_id, user_id=user_id
        )
    resource_type = _first_present(payload, rule.type_keys) or rule.default_type
    resource_id = _first_present(payload, rule.keys)
    if not resource_id and rule.optional:
        # The key is legitimately absent (standalone create) — nothing to gate.
        # Without this the checker would answer "resource_id is required".
        return None
    return await check_resource_in_active_scope(
        resource_type,
        resource_id,
        user_id=user_id,
    )


async def _validate_kind_scope(
    kind: str,
    payload: Dict[str, Any],
    *,
    user_id: str,
) -> Optional[Dict[str, Any]]:
    """B-AGENT-03 defense-in-depth: reject targets outside the active workspace.

    Table-driven off :data:`_KIND_SCOPE_RULES` — a kind with no row is not
    gated here (it names no workspace-bound target, or its own executor is the
    gate). Returns the first violation envelope found, or ``None``.
    """
    from app.services.agent_scope import (
        active_workspace_id,
        check_entry_in_active_scope,
        check_track_in_active_scope,
    )

    if not active_workspace_id():
        return None

    rule = _KIND_SCOPE_RULES.get(kind)
    if rule is None:
        return None
    if rule.scope == _SCOPE_RESOURCE:
        return await _validate_resource_scope(rule, payload, user_id=user_id)

    ids: List[str] = []
    if rule.resolve:
        ids.extend(await _resolve_scope_ids(rule, payload))
    for key in rule.keys:
        val = payload.get(key)
        if isinstance(val, str) and val:
            ids.append(val)
    for key in rule.list_keys:
        for val in payload.get(key) or []:
            if isinstance(val, str) and val:
                ids.append(val)

    checker = (
        check_track_in_active_scope
        if rule.scope == _SCOPE_TRACK
        else check_entry_in_active_scope
    )
    for target_id in ids:
        err = await checker(target_id, user_id=user_id)
        if err is not None:
            return err
    return None


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


# Intra-batch reference token. Two forms:
#   * positional — ``{{app.id}}`` / ``{{app_id}}`` / ``{{step_1.id}}`` — the
#     LAST entity of that type created so far in the batch.
#   * named — ``{{track.id:Authors}}`` / ``{{entry.id:Jane Austen}}`` — the
#     entity of that type created with that title/name. Named tokens make a
#     multi-track / multi-entry batch order-independent (the positional
#     ``{{track.id}}`` alone points only at the most-recent track, which
#     silently mis-targets when several tracks are created before any is
#     populated).
# The optional ``:name`` segment allows spaces and any char except ``}``.
_BATCH_REF_RE = _re.compile(r"\{\{\s*([a-zA-Z_][\w.]*(?::[^}]*)?)\s*\}\}")


# Entity wrapper keys a create-handler may return its new node under. Order is
# irrelevant (a result wraps exactly one). Keep in sync with the create_* handlers.
_CREATED_ENTITY_KEYS = ("app", "track", "entry", "tag", "view", "comment")
# Keys a created node may carry its display name/title under.
_NAME_KEYS = ("title", "name")


def _extract_created(
    result: Any,
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Return ``(entity_type, id, name)`` for the entity an executor created.

    Handler results wrap the new node under its type — ``{"app": {…id}}`` /
    ``{"track": {…}}`` / ``{"tag": {…}}`` / ``{"view": {…}}`` etc. (or a bare
    ``{"id": …}``). ``name`` is the node's title/name when present, used to key
    named intra-batch tokens. Returns ``(None, None, None)`` when no id present.
    """
    if not isinstance(result, dict):
        return None, None, None
    for et in _CREATED_ENTITY_KEYS:
        node = result.get(et)
        if isinstance(node, dict) and node.get("id"):
            name = next((str(node[k]) for k in _NAME_KEYS if node.get(k)), None)
            return et, str(node["id"]), name
    if result.get("id"):
        return None, str(result["id"]), None
    return None, None, None


def _resolve_batch_refs(value: Any, ctx: Dict[str, str]) -> Any:
    """Substitute ``{{token}}`` references in ``value`` using ``ctx`` (token→id).

    Tokens match permissively: ``app.id`` and ``app_id`` resolve to the same
    captured value; a named ``{{track.id:Authors}}`` matches the id registered
    under that title (whitespace around the name is normalized). An unresolved
    token is left verbatim (the op then fails its own validation rather than
    silently writing a placeholder).
    """
    if isinstance(value, str):

        def _sub(m: "_re.Match[str]") -> str:
            key = m.group(1).strip()
            hit = (
                ctx.get(key)
                or ctx.get(key.replace(".", "_"))
                or ctx.get(key.replace("_", "."))
            )
            if hit is None and ":" in key:
                head, name = key.split(":", 1)
                norm = f"{head.strip()}:{name.strip()}"
                hit = ctx.get(norm) or ctx.get(norm.replace(".", "_"))
            return hit if hit is not None else m.group(0)

        return _BATCH_REF_RE.sub(_sub, value)
    if isinstance(value, dict):
        return {k: _resolve_batch_refs(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_batch_refs(v, ctx) for v in value]
    return value


def _capture_batch_refs(ref_ctx: Dict[str, str], idx: int, result: Any) -> None:
    """Register the id an op created under every token later ops may use.

    Split out of the batch loop so a RESUMED batch can rebuild the same context
    from the recorded results of ops that already ran — without it, a resume
    would leave ``{{track.id}}`` / ``{{app.id}}`` unresolved and the remaining
    ops would target a literal placeholder.
    """
    for tag in (result.get("tags_created") if isinstance(result, dict) else None) or []:
        ref_ctx[f"tag.id:{tag['name']}"] = tag["id"]
        ref_ctx[f"tag_id:{tag['name']}"] = tag["id"]
    et, new_id, new_name = _extract_created(result)
    if not new_id:
        return
    ref_ctx[f"step_{idx + 1}.id"] = new_id
    ref_ctx[f"step_{idx + 1}_id"] = new_id
    if not et:
        return
    ref_ctx[f"{et}.id"] = new_id  # last-created of this type
    ref_ctx[f"{et}_id"] = new_id
    if new_name:
        # Named token — order-independent reference by title/name.
        ref_ctx[f"{et}.id:{new_name}"] = new_id
        ref_ctx[f"{et}_id:{new_name}"] = new_id


def _active_staging_token() -> str:
    """The token whose execution we are inside, or ``""``.

    ``bless_and_execute`` binds it as mutation provenance for the duration of
    the dispatch, which is the only channel a per-kind executor has back to its
    own StagedChange (``dispatch`` takes just ``kind`` + ``payload``).
    """
    try:
        from app.services.mutation_provenance import get_mutation_provenance

        prov = get_mutation_provenance()
        return prov.staging_token if prov is not None else ""
    except Exception:  # noqa: BLE001 — progress is an optimization, never fatal
        logger.debug("mutation provenance unavailable", exc_info=True)
        return ""


async def _load_execute_progress(expected_kind: str) -> Tuple[str, Dict[str, Any]]:
    """``(token, progress_dict)`` for the in-flight staged change.

    ``expected_kind`` guards against a NESTED multi-op executor: a ``batch``
    token binds its provenance for the whole dispatch, including the sub-ops it
    replays, so a ``bulk_delete_entries`` op inside a batch would otherwise read
    (and overwrite) the batch's own cursor. A token whose kind does not match
    gets no cursor and no writes.
    """
    token = _active_staging_token()
    if not token:
        return "", {}
    try:
        from app.agentive.staging import get_token

        sc = await get_token(token)
    except Exception:  # noqa: BLE001
        logger.debug("staging progress load failed", exc_info=True)
        return "", {}
    if sc is None or sc.kind != expected_kind:
        return "", {}
    progress = sc.progress
    return token, dict(progress) if isinstance(progress, dict) else {}


async def _save_execute_progress(
    token: str, progress: Optional[Dict[str, Any]]
) -> None:
    """Persist a multi-op cursor so a re-approval resumes instead of replaying."""
    if not token:
        return
    try:
        from app.agentive.staging import record_execute_progress

        await record_execute_progress(token=token, progress=progress)
    except Exception:  # noqa: BLE001 — never fail a write over bookkeeping
        logger.warning("staging progress persist failed", exc_info=True)


async def _x_batch(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Apply a batch StagedChange: replay each sub-op through ``dispatch``.

    A batch token's payload carries ``operations: [{kind, payload, summary}, ...]``
    accumulated while a skill held an open batch (see ``staging.commit_batch``).
    Each sub-op is replayed through the normal per-kind :func:`dispatch`, so its
    executor calls the real REST handler and **its policy gate re-runs at execute
    time** (per-item fail-closed) — the batch is one approval, not one bypass.

    **Intra-batch references:** ids created by earlier ops are captured and any
    ``{{app.id}}`` / ``{{track.id}}`` / ``{{step_N.id}}`` token in a later op's
    payload is resolved to the real id before dispatch — so "create the app, then
    a track in it" works in one batch even though the app id is unknown at stage
    time.

    Prototype semantics: ops apply sequentially and **fail-stop**. Cross-handler
    DB atomicity is not available (each handler is its own unit of work), so on
    the first failing op we stop and report ``{completed, total}`` with the
    partial results rather than rolling back. Full all-or-nothing apply is a
    follow-up (would need a unit-of-work spanning the per-kind handlers).

    **Resume, not replay.** A partial failure leaves the token ``blessed`` and
    the card invites re-approval. Re-running from op 0 would apply every
    already-completed op a SECOND time (a 5-op scaffold that failed at op 4
    created the app and three tracks twice). The cursor and the completed ops'
    results are persisted on the StagedChange, so a re-approval starts at the
    first unapplied op — and the intra-batch reference context is re-seeded
    from those recorded results, because the ids of previously created nodes
    exist nowhere else and ``{{track.id}}`` in a later op would otherwise go
    unresolved.
    """
    ops = payload.get("operations") or []
    progress_token, progress = await _load_execute_progress("batch")
    start = max(0, min(int(progress.get("completed") or 0), len(ops)))
    prior: list[Dict[str, Any]] = list(progress.get("results") or [])[:start]
    # A cursor without the matching results can't re-seed references, so treat
    # it as no progress rather than resuming into unresolvable placeholders.
    if len(prior) < start:
        start, prior = 0, []
    results: list[Dict[str, Any]] = list(prior)
    ref_ctx: Dict[str, str] = {}  # token -> created id, from completed ops
    for idx, done in enumerate(prior):
        _capture_batch_refs(ref_ctx, idx, (done or {}).get("result"))
    if start:
        logger.info(
            "batch executor: resuming token=%s at op %d/%d",
            progress_token,
            start + 1,
            len(ops),
        )
    for idx in range(start, len(ops)):
        op = ops[idx]
        kind = str(op.get("kind") or "")
        sub_payload = _resolve_batch_refs(dict(op.get("payload") or {}), ref_ctx)
        res = await dispatch(user_id=user_id, kind=kind, payload=sub_payload)
        results.append({"kind": kind, "summary": op.get("summary"), "result": res})
        # Capture this op's created id for later ops to reference.
        _capture_batch_refs(ref_ctx, idx, res)
        if isinstance(res, dict) and res.get("error"):
            # Service handlers report failure detail under either ``message`` or
            # ``detail`` (e.g. author_operational_model uses ``detail``); surface whichever
            # is present so the staged-card error isn't an opaque "None".
            reason = res.get("message") or res.get("detail") or res.get("error")
            logger.warning(
                "batch executor: op %d/%d (kind=%s) failed: %s",
                idx + 1,
                len(ops),
                kind,
                reason,
            )
            await _save_execute_progress(
                progress_token,
                {"completed": idx, "results": results[:idx]},
            )
            return {
                "error": True,
                "error_code": "batch_partial_failure",
                "message": (
                    f"Batch stopped at step {idx + 1} of {len(ops)} "
                    f"({kind}): {reason}"
                ),
                "completed": idx,
                "total": len(ops),
                "results": results,
            }
        await _save_execute_progress(
            progress_token, {"completed": idx + 1, "results": results}
        )
    # Record the full count too: if the CONSUME after a clean execute fails the
    # token stays blessed, and a retry must not re-apply the whole batch.
    await _save_execute_progress(
        progress_token, {"completed": len(ops), "results": results}
    )
    return {
        "batched": True,
        "completed": len(ops),
        "total": len(ops),
        "results": results,
    }


_BULK_UPDATE_KEYS = {"title", "body", "fields", "tags", "status", "entry_type"}


async def _x_bulk_update_entries(
    user_id: str, payload: Dict[str, Any]
) -> Dict[str, Any]:
    """Apply the same patch to many entries (fail-stop, per-entry policy gate).

    Loops the single-entry update executor so each entry's ``entry.update`` gate
    runs at execute time; stops at the first failure (PC-6 fail-closed) and
    reports ``{updated, total}`` partial progress. The stop index is persisted,
    so a re-approval of the still-blessed token resumes at the failed entry
    instead of re-patching the ones that already landed.

    Note the loop calls ``_x_update_entry`` directly rather than going back
    through :func:`dispatch`, so the workspace-scope gate for these ids runs
    ONCE, at this kind's own ``_validate_kind_scope`` row (``entry_ids``).
    """
    ids = list(payload.get("entry_ids") or [])
    updates = {
        k: v
        for k, v in (payload.get("updates") or {}).items()
        if k in _BULK_UPDATE_KEYS
    }
    progress_token, progress = await _load_execute_progress("bulk_update_entries")
    start = max(0, min(int(progress.get("completed") or 0), len(ids)))
    for idx in range(start, len(ids)):
        res = await _x_update_entry(user_id, {"entry_id": ids[idx], **updates})
        if isinstance(res, dict) and res.get("error"):
            await _save_execute_progress(progress_token, {"completed": idx})
            conflict = {
                "entry_id": ids[idx],
                "index": idx,
                "status_code": res.get("status_code"),
                "error_code": res.get("error_code"),
                "message": res.get("message"),
            }
            if isinstance(res.get("details"), dict):
                conflict["details"] = res["details"]
            return {
                "error": True,
                "error_code": "bulk_partial_failure",
                "message": f"Stopped at entry {idx + 1}/{len(ids)}: {res.get('message')}",
                "updated": idx,
                "total": len(ids),
                "conflicts": [conflict],
            }
    await _save_execute_progress(progress_token, {"completed": len(ids)})
    return {"updated": len(ids), "total": len(ids)}


def _is_already_gone(res: Dict[str, Any]) -> bool:
    """True when a delete failed only because the target no longer exists.

    A resumed bulk delete re-runs nothing it already deleted, but a token can
    also be re-approved after the user (or another op) removed an entry by
    hand. Treating "not found" as success keeps the resume idempotent instead
    of wedging the rest of the batch behind a 404.
    """
    if res.get("status_code") == 404:
        return True
    code = str(res.get("error_code") or "").lower()
    if "not_found" in code or "notfound" in code:
        return True
    return "not found" in str(res.get("message") or "").lower()


async def _x_bulk_delete_entries(
    user_id: str, payload: Dict[str, Any]
) -> Dict[str, Any]:
    """Soft-delete many entries under one approval (fail-stop, per-entry gate).

    Resumes from the persisted cursor on a re-approval (see
    :func:`_x_bulk_update_entries`), and treats an already-deleted entry as
    success so the resume is idempotent.
    """
    ids = list(payload.get("entry_ids") or [])
    progress_token, progress = await _load_execute_progress("bulk_delete_entries")
    start = max(0, min(int(progress.get("completed") or 0), len(ids)))
    for idx in range(start, len(ids)):
        res = await _x_delete_entry(user_id, {"entry_id": ids[idx]})
        if isinstance(res, dict) and res.get("error") and not _is_already_gone(res):
            await _save_execute_progress(progress_token, {"completed": idx})
            return {
                "error": True,
                "error_code": "bulk_partial_failure",
                "message": f"Stopped at entry {idx + 1}/{len(ids)}: {res.get('message')}",
                "deleted": idx,
                "total": len(ids),
            }
    await _save_execute_progress(progress_token, {"completed": len(ids)})
    return {"deleted": len(ids), "total": len(ids)}


async def _x_add_entry_tag(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    from app.api.entries import add_tag_to_entry as handler

    return await _call_endpoint(
        handler, user_id, entry_id=payload["entry_id"], tag_id=payload["tag_id"]
    )


async def _x_remove_entry_tag(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    from app.api.entries import remove_tag_from_entry as handler

    return await _call_endpoint(
        handler, user_id, entry_id=payload["entry_id"], tag_id=payload["tag_id"]
    )


async def _x_create_tag(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    from app.api.tags import create_tag as handler

    body: Dict[str, Any] = {"name": payload["name"]}
    for k in ("track_id", "app_id", "color", "parent_tag_id", "group_key"):
        if payload.get(k):
            body[k] = payload[k]
    return await _call_endpoint(handler, user_id, **body)


async def _x_register_track_template(
    user_id: str, payload: Dict[str, Any]
) -> Dict[str, Any]:
    from app.services.operational_model_authoring import register_app_track_template

    try:
        return await register_app_track_template(
            user_id=user_id,
            app_id=payload["app_id"],
            name=payload["name"],
            entry_types=payload["entry_types"],
            description=payload.get("description") or "",
        )
    except Exception as exc:  # noqa: BLE001
        if not getattr(exc, "status_code", None):
            logger.exception("staging executor: register_track_template raised")
        return _envelope_error(exc)


async def _x_update_app(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    from app.api.apps import update_app as handler

    app_id = payload["app_id"]
    fields = {
        k: v
        for k, v in payload.items()
        if k in ("name", "description", "visibility", "accent_color") and v is not None
    }
    return await _call_endpoint(handler, user_id, app_id=app_id, **fields)


async def _x_delete_app(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    from app.api.apps import delete_app as handler

    return await _call_endpoint(handler, user_id, app_id=payload["app_id"])


async def _x_link_entries(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Wire a relation field on the source entry → materializes the edge.

    Sets ``custom_fields[field_key] = target_id`` via the ``update_entry`` handler,
    whose ``sync_relation_edges`` materializes ``REFERENCES`` (field target=entry)
    or ``ANCHORS`` (field target=track) with ``field_key`` (I-GRAPH-01) — a scalar
    foreign key is never written without the edge.
    """
    from app.api.entries import update_entry as handler

    return await _call_endpoint(
        handler,
        user_id,
        entry_id=payload["source_entry_id"],
        custom_fields={payload["field_key"]: payload["target_id"]},
    )


async def _x_transform_entry(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Run ``POST /api/entries/{id}/transform`` for a bundle handoff hook."""
    from app.api.entries_transform import transform_entry as handler

    body: Dict[str, Any] = {}
    if payload.get("to_track"):
        body["to_track"] = payload["to_track"]
    if payload.get("hook_key"):
        body["hook_key"] = payload["hook_key"]
    if payload.get("override") is not None:
        body["override"] = bool(payload["override"])
    return await _call_endpoint_with_json(
        handler,
        user_id,
        body,
        entry_id=payload["entry_id"],
    )


# Delete-class kinds — routine_task_scheduler's write-scope reconciliation
# (routine_task_scheduler.py::_reconcile_write_scope) never auto-applies
# these regardless of allowlist match; a v1 hard rule that a routine may
# only be pre-approved for create/update-shaped writes, never deletes.
_DELETE_KINDS = frozenset(
    {"delete_entry", "delete_track", "delete_app", "bulk_delete_entries"}
)

# Staged-write kinds that change the caller's accessible track/app set without
# writing a permission edge (so sharing-path cache invalidation won't fire).
# After any of these succeeds, the caller's accessible-tracks/apps caches are
# dropped so a later op in the same batch sees the new/removed/re-scoped
# resource in its scope check (June 29 QA #2).
_ACCESS_MUTATING_KINDS = frozenset(
    {
        "create_app",
        "update_app",
        "delete_app",
        "create_track",
        "update_track",
        "delete_track",
        "author_operational_model",
        "apply_library_operational_model",
    }
)


async def _x_mcp_tool_call(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Invoke a mounted MCP tool that was staged for approval (ADR-010 §6).

    Mounted MCP tools reach a third party, so a write-classified call is staged
    rather than invoked: the resident proposes, the user blesses, and only then
    does the call leave the workspace. This executor is the "then".

    The connector id and remote tool name come from the staged payload, which
    dispatch built from the workspace tool-registry spec — never from model
    args. The call re-enters ``mcp_proxy.invoke``, so the connector-subject
    policy gate and the audit event run exactly as on the direct path.
    """
    from app.agentive.connectors.mcp_proxy import McpProxyError, invoke
    from app.services.hooks.registry import ToolContext

    connector_id = str(payload.get("connector_id") or "").strip()
    remote_name = str(payload.get("remote_name") or "").strip()
    workspace_id = str(payload.get("workspace_id") or "").strip()
    args = dict(payload.get("args") or {})
    if not connector_id or not remote_name:
        return {
            "error": True,
            "status_code": 400,
            "error_code": "misconfigured",
            "message": "staged MCP call is missing its connector or tool name",
        }

    ctx = ToolContext(
        user_id=user_id,
        workspace_id=workspace_id,
        scope=f"tool:{remote_name}",
        actor_kind="human",
    )
    try:
        result = await invoke(
            args,
            ctx,
            _mcp_connector_id=connector_id,
            _mcp_remote_name=remote_name,
        )
    except McpProxyError as exc:
        return {
            "error": True,
            "status_code": 403 if exc.error_code == "policy_denied" else 502,
            "error_code": exc.error_code,
            "message": exc.message,
        }
    return {"ok": True, "result": result}


async def _x_design_proposal(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Approve a staged greenfield design. Does not create apps or tracks.

    Bless records ``approved`` on the thread marker so ``commit_batch`` can
    mint the build card. The later batch executor performs the writes.
    """
    session_id = str(payload.get("session_id") or "").strip()
    if not session_id:
        return {
            "error": True,
            "error_code": "session_required",
            "message": "design_proposal payload needs session_id",
        }
    from app.services import chat_threads
    from app.utils.time import utc_now_iso

    thread = await chat_threads.get_thread_by_session(session_id)
    if thread is None:
        return {
            "error": True,
            "error_code": "not_found",
            "message": "Chat thread not found",
        }
    if (getattr(thread, "user_id", "") or "") != user_id:
        return {
            "error": True,
            "error_code": "forbidden",
            "message": "Thread does not belong to the caller",
        }
    marker = dict(getattr(thread, "design_proposed", None) or {})
    marker["approved"] = True
    marker["approved_at"] = utc_now_iso()
    marker["idempotency_key"] = payload.get("idempotency_key")
    if payload.get("summary"):
        marker["summary"] = payload.get("summary")
    if payload.get("proposal"):
        marker["proposal"] = payload.get("proposal")
    thread.design_proposed = marker
    await thread.save()
    return {
        "ok": True,
        "approved": True,
        # FE + resume: this is not a substrate write — the follow-on agent
        # turn must begin_batch and build (AGENT-17).
        "needs_agent_build": True,
        "summary": marker.get("summary") or "",
        "idempotency_key": marker.get("idempotency_key"),
    }


_EXECUTORS: Dict[str, Callable[[str, Dict[str, Any]], Awaitable[Dict[str, Any]]]] = {
    "batch": _x_batch,
    "bulk_update_entries": _x_bulk_update_entries,
    "bulk_delete_entries": _x_bulk_delete_entries,
    "add_entry_tag": _x_add_entry_tag,
    "remove_entry_tag": _x_remove_entry_tag,
    "create_tag": _x_create_tag,
    "update_app": _x_update_app,
    "register_track_template": _x_register_track_template,
    "delete_app": _x_delete_app,
    "link_entries": _x_link_entries,
    "transform_entry": _x_transform_entry,
    "create_entry": _x_create_entry,
    "update_entry": _x_update_entry,
    "delete_entry": _x_delete_entry,
    "create_track": _x_create_track,
    "update_track": _x_update_track,
    "delete_track": _x_delete_track,
    "file_content": _x_file_content,
    "add_comment": _x_add_comment,
    "mcp_tool_call": _x_mcp_tool_call,
    "edit_comment": _x_edit_comment,
    "delete_comment": _x_delete_comment,
    "delete_view": _x_delete_view,
    "share": _x_share,
    "remove_collaborator": _x_remove_collaborator,
    "set_exclusion": _x_set_exclusion,
    "remove_exclusion": _x_remove_exclusion,
    "mint_share_link": _x_mint_share_link,
    "revoke_share_link": _x_revoke_share_link,
    "invite": _x_invite,
    "attach_file": _x_attach_file,
    "attach_uploaded_file": _x_attach_uploaded_file,
    "attach_uploaded_image": _x_attach_uploaded_image,
    "author_skill": _x_author_skill,
    "update_skill": _x_update_skill,
    "delete_skill": _x_delete_skill,
    "resolve_conflict": _x_resolve_conflict,
    "trigger_sync": _x_trigger_sync,
    "call_workspace_tool": _x_call_workspace_tool,
    "create_app": _x_create_app,
    "draft_new_profile": _x_draft_new_profile,
    "author_operational_model": _x_author_operational_model,
    "apply_library_operational_model": _x_apply_library_operational_model,
    "save_view": _x_save_view,
    "create_dashboard": _x_create_dashboard,
    "update_dashboard": _x_update_dashboard,
    "delete_dashboard": _x_delete_dashboard,
    # Pillar 3 — agent-authorable substrate.
    "propose_profile_revision": _x_propose_profile_revision,
    "apply_to_draft": _x_propose_profile_revision,
    "publish_profile_draft": _x_publish_profile_draft,
    "discard_profile_draft": _x_discard_profile_draft,
    # Routine tasks (P_scheduling).
    "routine_task_create": _x_routine_task_create,
    "routine_task_update": _x_routine_task_update,
    "routine_task_cancel": _x_routine_task_cancel,
    "routine_task_purge": _x_routine_task_purge,
    "design_proposal": _x_design_proposal,
}


# modify_operational_model.* dispatch — six sub-kinds all route through the same executor.
for _sub in (
    "add_entry_type",
    "remove_entry_type",
    "add_view",
    "remove_view",
    "add_tag",
    "remove_tag",
):
    _EXECUTORS[f"modify_operational_model.{_sub}"] = _x_modify_operational_model


def supports(kind: str) -> bool:
    """Return True if an executor is registered for ``kind``."""
    return kind in _EXECUTORS


async def dispatch(
    *, user_id: str, kind: str, payload: Dict[str, Any]
) -> Dict[str, Any]:
    """Run the executor matching ``kind`` against ``payload``.

    Returns the executor's result dict on success, or an error envelope
    if the kind is unknown / the executor raised. Caller is responsible
    for marking the staging token consumed (or leaving it blessed if
    the execute failed — a future retry is then possible).
    """
    executor = _EXECUTORS.get(kind)
    if executor is None:
        return {
            "error": True,
            "error_code": "unknown_kind",
            "message": f"No staging executor registered for kind {kind!r}",
        }
    scope_err = await _validate_kind_scope(kind, payload, user_id=user_id)
    if scope_err is not None:
        return scope_err
    try:
        result = await executor(user_id, payload)
    except Exception as exc:  # noqa: BLE001 — defensive: surface to caller
        logger.exception("staging executor raised for kind=%s", kind)
        return _envelope_error(exc)
    # A staged write that creates/removes/re-scopes a track or app changes the
    # caller's accessible set but adds no permission edge, so the sharing-path
    # cache invalidation never fires. Drop the caller's accessible-tracks/apps
    # caches (per-request + process TTL) so a LATER op in the SAME batch — e.g.
    # create_entry referencing the app/track just created via {{track.id}} —
    # sees the new resource in its scope check instead of the stale pre-create
    # list (June 29 QA #2: multi-app create failed until approved twice).
    if kind in _ACCESS_MUTATING_KINDS and not (
        isinstance(result, dict) and result.get("error")
    ):
        try:
            from app.services.permissions import (
                invalidate_user_accessible_caches,
            )

            invalidate_user_accessible_caches(user_id)
        except Exception:  # noqa: BLE001 — cache invalidation must never fail a write
            logger.debug("accessible-cache invalidation skipped", exc_info=True)
    return result
