"""Onboarding public-share routes — imported for side-effect registration."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import Request
from jvspatial.api import endpoint

from app.api.errors import (
    BadRequestError,
    InsufficientPermissionsError,
    ResourceNotFoundError,
)
from app.api.tracks_public_share import (
    _public_share_form_entry,
    _resolve_public_track_link,
    _slugify_key,
)
from app.api.utils import export_node
from app.models.edges import CONTAINS
from app.models.nodes import App, Attachment, Entry, Track
from app.schemas.shares import PublicContractDecisionRequest
from app.services.documents.onboarding_contract import (
    contract_review_enabled,
    decide_contract,
    generate_contract_for_form,
    get_contract_pdf_bytes,
)
from app.services.track_public_share import declared_public_share


def _cf_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    raw = str(value).strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off", ""):
        return False
    return default


def _cf_single_id(value: Any) -> str:
    if isinstance(value, list):
        return str(value[0] or "").strip() if value else ""
    return str(value or "").strip()


async def _parent_app_for_track(track: Track) -> Optional[App]:
    parents = await track.nodes(edge=[CONTAINS], direction="in", node=["WorkspaceApp"])
    return next((p for p in parents if isinstance(p, App)), None)


async def _sibling_track_by_type_key(app: Optional[App], track_type_key: str) -> Optional[Track]:
    want = _slugify_key(track_type_key or "")
    if not want or app is None:
        return None
    children = await app.nodes(edge=[CONTAINS], node=["Track"])
    for child in children or []:
        if not isinstance(child, Track):
            continue
        if _slugify_key(str(getattr(child, "template_id", "") or "")) == want:
            return child
        if _slugify_key(str(getattr(child, "title", "") or "")) == want:
            return child
    return None


async def _read_attachment_response(attachment: Attachment):
    from urllib.parse import quote

    from fastapi.responses import RedirectResponse, StreamingResponse

    from app.services.attachment_storage import get_attachment_storage_service

    if getattr(attachment, "source_type", "") == "url":
        if not attachment.external_url:
            raise ResourceNotFoundError(message="Attachment URL is missing")
        return RedirectResponse(url=attachment.external_url)
    if not attachment.storage_key:
        raise ResourceNotFoundError(message="Attachment file is missing")

    storage = get_attachment_storage_service()
    blob = await storage.read_attachment(attachment.storage_key)
    if blob is None:
        raise ResourceNotFoundError(message="Attachment file not found in storage")
    media_type = attachment.mime_type or "application/octet-stream"
    filename = attachment.filename or "attachment"
    ascii_name = filename.encode("ascii", "ignore").decode() or "attachment"
    headers = {
        "Content-Disposition": (
            f'attachment; filename="{ascii_name}"; '
            f"filename*=UTF-8''{quote(filename)}"
        ),
    }
    return StreamingResponse(iter([blob]), media_type=media_type, headers=headers)


@endpoint(
    "/public-share/track/{token}/onboarding-policies",
    methods=["GET"],
    auth=False,
    tags=["Shares"],
)
async def list_public_onboarding_policies(
    request: Request, token: str
) -> Dict[str, Any]:
    """List active catalog policies declared via public_share.extensions."""
    link = await _resolve_public_track_link(token)
    perms = dict(link.public_permissions or {})
    if not perms.get("update_entries") and not perms.get("read_entries"):
        raise InsufficientPermissionsError(message="Public access is disabled.")

    track = await Track.get(link.resource_id)
    if not track:
        raise ResourceNotFoundError(message="Shared track not found")

    declared = await declared_public_share(track) or {}
    extensions = dict(declared.get("extensions") or {})
    catalog_key = str(extensions.get("policy_catalog_track_type_key") or "").strip()
    if not catalog_key:
        return {"policies": [], "extensions": extensions}

    app = await _parent_app_for_track(track)
    catalog = await _sibling_track_by_type_key(app, catalog_key)
    if catalog is None:
        return {"policies": [], "extensions": extensions}

    entries = await Entry.find({"context.track_id": catalog.id})
    active_rows: List[Entry] = []
    for row in entries or []:
        if getattr(row, "status", "active") not in (None, "", "active"):
            continue
        cf = dict(getattr(row, "custom_fields", None) or {})
        if not _cf_bool(cf.get("active"), default=True):
            continue
        active_rows.append(row)

    def _sort_key(entry: Entry):
        cf = dict(entry.custom_fields or {})
        try:
            order = float(
                cf.get("sort_order") if cf.get("sort_order") is not None else 100
            )
        except (TypeError, ValueError):
            order = 100.0
        return (order, str(entry.title or "").strip().lower())

    active_rows.sort(key=_sort_key)

    policies: List[Dict[str, Any]] = []
    for row in active_rows:
        cf = dict(row.custom_fields or {})
        document_id = _cf_single_id(cf.get("document"))
        payload: Dict[str, Any] = {
            "id": str(row.id),
            "title": str(row.title or "").strip(),
            "description": str(cf.get("description") or "").strip(),
            "required_for_onboarding": _cf_bool(
                cf.get("required_for_onboarding"), default=True
            ),
            "active": True,
            "sort_order": cf.get("sort_order", 100),
            "document_attachment_id": document_id or None,
            "document_filename": None,
            "document_mime_type": None,
        }
        if document_id:
            att = await Attachment.get(document_id)
            if att is not None:
                payload["document_filename"] = getattr(att, "filename", None) or None
                payload["document_mime_type"] = getattr(att, "mime_type", None) or None
        policies.append(payload)

    return {"policies": policies, "extensions": extensions}


@endpoint(
    "/public-share/track/{token}/onboarding-policies/{policy_entry_id}/document",
    methods=["GET"],
    auth=False,
    tags=["Shares"],
)
async def download_public_onboarding_policy_document(
    request: Request, token: str, policy_entry_id: str
):
    """Stream a policy document from the catalog track declared in extensions."""
    link = await _resolve_public_track_link(token)
    perms = dict(link.public_permissions or {})
    if not perms.get("update_entries") and not perms.get("read_entries"):
        raise InsufficientPermissionsError(message="Public access is disabled.")

    track = await Track.get(link.resource_id)
    if not track:
        raise ResourceNotFoundError(message="Shared track not found")

    declared = await declared_public_share(track) or {}
    extensions = dict(declared.get("extensions") or {})
    catalog_key = str(extensions.get("policy_catalog_track_type_key") or "").strip()
    if not catalog_key:
        raise ResourceNotFoundError(message="Policy catalog is not configured")

    app = await _parent_app_for_track(track)
    catalog = await _sibling_track_by_type_key(app, catalog_key)
    if catalog is None:
        raise ResourceNotFoundError(message="Policy catalog track not found")

    policy = await Entry.get(policy_entry_id)
    if not policy or policy.track_id != catalog.id:
        raise ResourceNotFoundError(message="Policy document not found")

    cf = dict(policy.custom_fields or {})
    if not _cf_bool(cf.get("active"), default=True):
        raise ResourceNotFoundError(message="Policy document not found")
    attachment_id = _cf_single_id(cf.get("document"))
    if not attachment_id:
        raise ResourceNotFoundError(message="Policy file is missing")

    attachment = await Attachment.get(attachment_id)
    if attachment is None:
        raise ResourceNotFoundError(message="Attachment not found")

    parents = await attachment.nodes(edge=["HAS_ATTACHMENT"], direction="in")
    if not any(getattr(p, "id", None) == policy.id for p in parents or []):
        raise InsufficientPermissionsError(
            message="Attachment is not bound to this policy"
        )

    return await _read_attachment_response(attachment)


@endpoint(
    "/public-share/track/{token}/entries/{entry_id}/attachments",
    methods=["GET"],
    auth=False,
    tags=["Shares"],
)
async def list_public_track_entry_attachments(
    request: Request, token: str, entry_id: str
) -> Dict[str, Any]:
    """List attachments bound to a public shared entry."""
    from app.services.attachment_urls import is_visible_to_user

    _link, _track, entry, _perms = await _public_share_form_entry(token, entry_id)
    attached = await entry.nodes(edge=["HAS_ATTACHMENT"], direction="out")
    items = []
    for att in attached or []:
        if not isinstance(att, Attachment):
            continue
        if not is_visible_to_user(att):
            continue
        items.append(await export_node(att))
    return {"attachments": items, "total": len(items)}


async def _public_share_contract_context(
    token: str, entry_id: str, *, require_update: bool = False
) -> tuple:
    link = await _resolve_public_track_link(token)
    perms = dict(link.public_permissions or {})
    if require_update:
        if not perms.get("update_entries"):
            raise InsufficientPermissionsError(message="Public updating is disabled.")
    elif not perms.get("update_entries") and not perms.get("read_entries"):
        raise InsufficientPermissionsError(message="Public access is disabled.")
    track = await Track.get(link.resource_id)
    if not track:
        raise ResourceNotFoundError(message="Shared track not found")
    entry = await Entry.get(entry_id)
    if not entry or entry.track_id != track.id:
        raise ResourceNotFoundError(message="Entry not found on this track")
    return link, track, entry, perms


@endpoint(
    "/public-share/track/{token}/entries/{entry_id}/contract/generate",
    methods=["POST"],
    auth=False,
    tags=["Shares"],
)
async def generate_public_onboarding_contract(
    request: Request, token: str, entry_id: str
) -> Dict[str, Any]:
    """Generate employment contract PDF from onboarding form + employee stub."""
    _link, track, entry, _perms = await _public_share_contract_context(
        token, entry_id, require_update=True
    )
    if not await contract_review_enabled(track, entry):
        raise BadRequestError(message="Contract review is not enabled for this track")
    ws_id = str(getattr(track, "workspace_id", "") or "")
    if not ws_id:
        raise BadRequestError(message="Track workspace missing")
    force_raw = str(request.query_params.get("force") or "").strip().lower()
    force_regenerate = force_raw in ("1", "true", "yes")
    try:
        return await generate_contract_for_form(
            workspace_id=ws_id,
            track=track,
            form_entry=entry,
            force_regenerate=force_regenerate,
        )
    except ValueError as exc:
        raise BadRequestError(message=str(exc)) from exc


@endpoint(
    "/public-share/track/{token}/entries/{entry_id}/contract",
    methods=["GET"],
    auth=False,
    tags=["Shares"],
)
async def download_public_onboarding_contract(
    request: Request, token: str, entry_id: str
):
    """Stream the generated employment contract PDF for review."""
    from fastapi.responses import StreamingResponse

    _link, track, entry, _perms = await _public_share_contract_context(
        token, entry_id, require_update=False
    )
    if not await contract_review_enabled(track, entry):
        raise ResourceNotFoundError(message="Contract review is not configured")
    try:
        blob, _gd_id = await get_contract_pdf_bytes(entry)
    except ValueError as exc:
        raise ResourceNotFoundError(message=str(exc)) from exc
    return StreamingResponse(
        iter([blob]),
        media_type="application/pdf",
        headers={"Content-Disposition": 'inline; filename="employment-contract.pdf"'},
    )


@endpoint(
    "/public-share/track/{token}/entries/{entry_id}/contract/decision",
    methods=["POST"],
    auth=False,
    tags=["Shares"],
)
async def decide_public_onboarding_contract(
    request: Request,
    token: str,
    entry_id: str,
) -> Dict[str, Any]:
    """Accept (sign) or reject the employment contract."""
    raw = await request.json()
    body = PublicContractDecisionRequest.model_validate(raw)

    _link, track, entry, _perms = await _public_share_contract_context(
        token, entry_id, require_update=True
    )
    if not await contract_review_enabled(track, entry):
        raise BadRequestError(message="Contract review is not enabled for this track")
    ws_id = str(getattr(track, "workspace_id", "") or "")
    try:
        return await decide_contract(
            workspace_id=ws_id,
            track=track,
            form_entry=entry,
            action=body.action,
            signature_png=body.signature_png,
            reason=body.reason,
            return_url=str(body.return_url or ""),
        )
    except ValueError as exc:
        raise BadRequestError(message=str(exc)) from exc
