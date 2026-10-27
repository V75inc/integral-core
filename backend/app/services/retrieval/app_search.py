"""Bounded App passage searches through the public tool facade."""

from __future__ import annotations

import logging
from typing import Any, cast

from app.api.errors import BadRequestError, InsufficientPermissionsError
from app.exceptions import AuthorizationUnavailableError
from app.middleware.permissions_cache import isolated_permissions_cache
from app.models.nodes import App
from app.schemas.policy import Subject
from app.schemas.retrieve import RetrieveMode, RetrieveRequest
from app.services.agent_scope import current_scope_workspace_id
from app.services.entry_access_projection import admit_records
from app.services.resource_location import (
    ACTIVE_APP_STATES,
    locate_resource,
    locate_resources,
)
from app.services.walkers.app_passage_candidates import (
    MAX_CANDIDATES,
    AppPassageCandidateWalker,
)
from app.services.walkers.app_passage_candidates import (
    CandidateSelectionUnavailable as _SearchUnavailable,
)
from app.services.workspace_governance import ordinary_authority_only

logger = logging.getLogger(__name__)


def _response(mode: str, **values: Any) -> dict[str, Any]:
    return {
        "available": True,
        "requested_mode": mode,
        "effective_mode": mode,
        "degraded": False,
        "coverage": "unknown",
        "candidate_limit_reached": False,
        "results": [],
        "warnings": [],
        **values,
    }


async def _read_subject(context: Any) -> Subject:
    """Keep the exact service actor separate from its bounded human audience."""
    if context.actor_kind == "human":
        return Subject(kind="human", id=context.user_id)
    if context.actor_kind in {"agent", "connector"}:
        from app.services.app_operation_authority import current_operation_actor

        actor_id = str(getattr(context, "actor_id", "") or "")
        if actor_id:
            author = await current_operation_actor(
                subject=Subject(kind=context.actor_kind, id=actor_id),
                workspace_id=context.workspace_id,
                app_id=context.app_id,
                operation_key=str(getattr(context, "operation_key", "") or ""),
            )
            if author and author == context.user_id:
                return Subject(kind="human", id=author)
    raise InsufficientPermissionsError(message="Current App read authority required")


async def _candidate_ids(
    context: Any,
    track_type: str,
    entry_type: str | None,
    supplied: list[str] | None,
    *,
    metadata_only: bool = False,
) -> tuple[list[str] | dict[str, list[str] | None], bool]:
    """Walk only matching rooted resources, then apply canonical audience gates."""
    location = await locate_resource("app", context.app_id)
    if location is None or location.workspace_id != context.workspace_id:
        raise _SearchUnavailable("app_scope_unavailable")
    app = await App.get(context.app_id)
    if app is None:
        raise _SearchUnavailable("app_scope_unavailable")
    selected_tracks = None
    if supplied is not None:
        locations = await locate_resources(
            "entry",
            supplied,
            workspace_hints=[context.workspace_id],
            allowed_app_states=ACTIVE_APP_STATES,
        )
        selected_tracks = list(
            dict.fromkeys(
                item.track_id
                for item in locations.values()
                if item
                and item.workspace_id == context.workspace_id
                and item.app_id == context.app_id
            )
        )
    walker = AppPassageCandidateWalker(
        principal_id=context.user_id,
        workspace_id=context.workspace_id,
        track_selector=track_type,
        entry_type_key=str(entry_type or "").strip(),
        selected_ids=supplied,
        selected_track_ids=selected_tracks,
        metadata_only=metadata_only,
    )
    await walker.spawn(app)
    if walker.failure_reason:
        raise _SearchUnavailable(walker.failure_reason)
    # jvspatial records visit-hook failures instead of raising them. Never
    # release a partial selection after a failed containment/schema read.
    if any(
        isinstance(item, dict) and "hook_error" in item
        for item in await walker.get_report()
    ):
        raise _SearchUnavailable("selection_failed")
    if not walker.track_found and selected_tracks != []:
        raise _SearchUnavailable("track_not_found")
    if metadata_only:
        return walker.selection, walker.truncated
    admitted = await admit_records(
        context.user_id,
        walker.records,
        workspace_id=context.workspace_id,
        app_id=context.app_id,
        allowed_app_states=ACTIVE_APP_STATES,
    )
    return [row["id"] for row in admitted], walker.truncated


async def search_app_entries(
    context: Any,
    *,
    query: str,
    track_type: str,
    entry_type: str | None = None,
    eligible_entry_ids: list[str] | None = None,
    mode: str = "hybrid",
    limit: int = 150,
    context_tokens: int = 1200,
    attachment_kind: str | None = None,
    attachment_filename: str = "",
) -> dict[str, Any]:
    """Search the current App without claiming unmeasured index coverage."""
    requested = str(mode).casefold()
    if requested not in {"graph", "lexical", "hybrid", "semantic"}:
        return _response(requested, available=False, reason="invalid_request")
    if not str(getattr(context, "app_id", "") or "").strip():
        return _response(requested, available=False, reason="app_scope_required")
    text = str(query or "").strip()
    if (
        not text and attachment_kind is None and not attachment_filename
    ) or eligible_entry_ids == []:
        return _response(requested, coverage="full")
    warnings: list[str] = []
    try:
        supplied = None
        capped = False
        if eligible_entry_ids is not None:
            capped = len(eligible_entry_ids) > MAX_CANDIDATES
            supplied = list(
                dict.fromkeys(
                    str(value).strip()
                    for value in eligible_entry_ids[:MAX_CANDIDATES]
                    if value and str(value).strip()
                )
            )
            if not supplied:
                return _response(requested, coverage="full")
        k = max(1, min(int(limit), MAX_CANDIDATES))
        top_n = min(50, k)
        payload = RetrieveRequest(
            query=text,
            scope=f"app:{context.app_id}",
            mode=cast(RetrieveMode, requested),
            k=k,
            top_n=top_n,
            context_tokens=max(64, min(int(context_tokens), 8192)),
            attachment_kind=attachment_kind,
            attachment_filename=attachment_filename,
        )
        if not text or requested == "lexical":
            payload = payload.model_copy(update={"mode": "graph"})
        with ordinary_authority_only(), isolated_permissions_cache():
            subject = await _read_subject(context)
            selected, truncated = await _candidate_ids(
                context,
                track_type,
                entry_type,
                supplied,
                metadata_only=supplied is None,
            )
            eligible = cast(list[str], selected) if supplied is not None else None
            selection_options: dict[str, Any] = (
                {"type_selection": selected} if supplied is None else {}
            )
            capped = capped or truncated
            if capped:
                warnings.append("eligible_ids_capped")
            if supplied is not None and set(supplied) - set(eligible or []):
                warnings.append("ineligible_ids_dropped")
            if not selected:
                await _read_subject(context)
                return _response(
                    requested,
                    coverage="partial" if capped else "unknown",
                    candidate_limit_reached=capped,
                    warnings=warnings,
                )
            from . import get_embedding_store
            from .pgvector_driver import PgVectorEmbeddingStore
            from .search_service import retrieve_passages

            driver = get_embedding_store()
            if not isinstance(driver, PgVectorEmbeddingStore):
                raise _SearchUnavailable("passage_store_unavailable")
            token = current_scope_workspace_id.set(context.workspace_id)
            try:
                try:
                    results, _dropped, examined, effective, degraded = (
                        await retrieve_passages(
                            subject,
                            payload,
                            driver=driver,
                            k=k,
                            top_n=top_n,
                            eligible_entry_ids=eligible,
                            **selection_options,
                        )
                    )
                    if requested == "lexical":
                        effective = "lexical"
                except (
                    BadRequestError,
                    InsufficientPermissionsError,
                    AuthorizationUnavailableError,
                ):
                    raise
                except Exception:
                    if requested in {"graph", "lexical"}:
                        raise
                    results, _dropped, examined, effective, _ = await retrieve_passages(
                        subject,
                        payload.model_copy(update={"mode": "graph"}),
                        driver=driver,
                        k=k,
                        top_n=top_n,
                        eligible_entry_ids=eligible,
                        **selection_options,
                    )
                    degraded = True
            finally:
                current_scope_workspace_id.reset(token)
            # Stable manifest identity can change during encoding/ranking too.
            # Refresh selector authority rather than treating the initial
            # graph discovery or its request-local permission cache as a lease.
            with isolated_permissions_cache():
                current_selection, _ = await _candidate_ids(
                    context,
                    track_type,
                    entry_type,
                    supplied,
                    metadata_only=supplied is None,
                )
            if current_selection != selected:
                raise _SearchUnavailable("selection_changed")
            await _read_subject(context)
    except _SearchUnavailable as exc:
        return _response(requested, available=False, reason=str(exc), warnings=warnings)
    except (ValueError, TypeError):
        return _response(
            requested, available=False, reason="invalid_request", warnings=warnings
        )
    except Exception as exc:
        logger.warning("App passage search unavailable (%s)", type(exc).__name__)
        return _response(
            requested, available=False, reason="search_failed", warnings=warnings
        )
    if degraded:
        warnings.append("semantic_unavailable")
    capped = capped or examined >= k or len(results) >= top_n
    if capped:
        warnings.append("candidate_limit_reached")
    tokens = sum(p.tokens for result in results for p in result.passages)
    if tokens >= payload.context_tokens:
        warnings.append("context_limit_reached")
    return _response(
        requested,
        effective_mode=effective,
        degraded=bool(degraded),
        coverage=(
            "partial"
            if capped or degraded or tokens >= payload.context_tokens
            else "unknown"
        ),
        candidate_limit_reached=capped,
        warnings=warnings,
        results=[
            {
                "entry_id": result.entry_id,
                "score": float(result.score),
                "title": result.title,
                "source_revision": result.source_revision,
                "mode_origin": list(result.mode_origin),
                "action_url": result.action_url,
                "passages": [passage.model_dump() for passage in result.passages],
            }
            for result in results
        ],
    )
