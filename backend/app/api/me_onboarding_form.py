"""Legacy ``/me/onboarding-form`` routes — prefer ``me_assigned_form`` paths.

Members complete app-assigned intake entries (e.g. HR onboarding_form entry type).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import Request
from jvspatial.api import endpoint

from app.api.errors import (
    BadRequestError,
    InsufficientPermissionsError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from app.api.tracks_public_share_onboarding import (
    _cf_bool,
    _cf_single_id,
    _parent_app_for_track,
    _read_attachment_response,
    _sibling_track_by_type_key,
)
from app.api.utils import export_node, resolve_principal_id
from app.contracts.information import schema_revision_from_profile_version
from app.models.nodes import Attachment, Entry, EntryType, Track
from app.schemas.api.me_onboarding import (
    MeOnboardingContractDecisionRequest,
    MeOnboardingFormUpdateRequest,
)
from app.services.change_event import emit_change_event
from app.services.content_moderation import validate_no_profanity
from app.services.documents.onboarding_contract import (
    contract_review_enabled,
    decide_contract,
    generate_contract_for_form,
    get_contract_pdf_bytes,
)
from app.services.entry_type_service import materialize_entry_types_from_tier
from app.services.hooks.entry_save_runtime import run_entry_save_hooks
from app.services.onboarding_form_member import (
    onboarding_form_locked,
    resolve_member_onboarding_form,
)
from app.services.onboarding_form_prefill import (
    enrich_onboarding_form_export,
    materialize_employee_prefill_on_form,
)
from app.services.onboarding_form_public import partition_server_managed_custom_fields
from app.services.operational_model_entry_fields import resolve_entry_type_spec
from app.services.operational_model_runtime import (
    resolve_track_runtime_profile,
    validate_and_materialize_entry_custom_fields,
)
from app.services.request_scope import resolve_workspace_id_from_request
from app.services.track_public_share import declared_public_share
from app.services.workspace_permissions import (
    is_workspace_admin_or_owner,
    user_in_workspace_member_pool,
)
from app.utils.time import utc_now_iso


def _slugify_key(value: str) -> str:
    import re as _re

    s = str(value or "").strip().lower()
    return _re.sub(r"[^a-z0-9]+", "_", s).strip("_")


def _form_entry_id_from_request(
    request: Request, *, form_entry_id: Optional[str] = None
) -> Optional[str]:
    explicit = str(form_entry_id or "").strip()
    if explicit:
        return explicit
    from_query = str(request.query_params.get("entry") or "").strip()
    return from_query or None


async def _resolve_member_form(
    request: Request, *, form_entry_id: Optional[str] = None
) -> tuple[str, str, Entry, Track, Entry]:
    form_entry_id = _form_entry_id_from_request(request, form_entry_id=form_entry_id)
    user_id = resolve_principal_id(request)
    if not user_id:
        raise InsufficientPermissionsError(message="Authentication required")
    workspace_id = await resolve_workspace_id_from_request(request, user_id)
    if not await user_in_workspace_member_pool(user_id, workspace_id):
        raise InsufficientPermissionsError(message="Workspace access required")
    try:
        form, track, employee = await resolve_member_onboarding_form(
            workspace_id=workspace_id,
            member_user_id=user_id,
            form_entry_id=form_entry_id,
        )
    except PermissionError as exc:
        raise InsufficientPermissionsError(message=str(exc)) from exc
    except LookupError as exc:
        raise ResourceNotFoundError(message=str(exc)) from exc
    return workspace_id, user_id, form, track, employee


async def _assert_form_editable(form: Entry, user_id: str, workspace_id: str) -> None:
    if not onboarding_form_locked(form):
        return
    if await is_workspace_admin_or_owner(user_id, workspace_id):
        return
    raise InsufficientPermissionsError(
        message="Approved onboarding forms cannot be edited"
    )


_LEGACY_TAG = "AssignedForm"


@endpoint("/me/onboarding-form", methods=["GET"], auth=True, tags=[_LEGACY_TAG])
async def get_me_onboarding_form(request: Request) -> Dict[str, Any]:
    """Load the signed-in member's onboarding wizard context."""
    form_entry_id = str(request.query_params.get("entry") or "").strip() or None
    workspace_id, _user_id, form, track, _employee = await _resolve_member_form(
        request, form_entry_id=form_entry_id
    )

    await materialize_entry_types_from_tier(track)
    entry_types = await EntryType.find({"track_id": track.id})
    entry_types_export = [await export_node(et) for et in entry_types]

    await EntryType.get(form.type_id)
    entry_data = await export_node(form)
    entry_data = await enrich_onboarding_form_export(form, entry_data)

    declared = await declared_public_share(track) or {}
    extensions = dict(declared.get("extensions") or {})

    track_data = {
        "id": track.id,
        "title": track.title,
        "purpose": track.purpose,
        "icon": track.icon,
        "accent_color": track.accent_color,
    }

    return {
        "track": track_data,
        "workspace_id": workspace_id,
        "entry": entry_data,
        "entry_types": entry_types_export,
        "public_share_extensions": extensions,
        "locked": onboarding_form_locked(form),
        "message": "Onboarding form loaded",
    }


@endpoint("/me/onboarding-form", methods=["PATCH"], auth=True, tags=[_LEGACY_TAG])
async def patch_me_onboarding_form(request: Request) -> Dict[str, Any]:
    """Update the signed-in member's onboarding form (not when approved)."""
    form_entry_id = str(request.query_params.get("entry") or "").strip() or None
    workspace_id, user_id, form, track, _employee = await _resolve_member_form(
        request, form_entry_id=form_entry_id
    )
    await _assert_form_editable(form, user_id, workspace_id)

    raw = await request.json()
    req = MeOnboardingFormUpdateRequest.model_validate(raw)

    entry = form
    entry_type = await EntryType.get(entry.type_id)
    if not entry_type:
        raise ResourceNotFoundError(message="Entry type not found")

    current_record_revision = int(getattr(entry, "record_revision", 1) or 1)
    if (
        req.expected_record_revision is not None
        and req.expected_record_revision != current_record_revision
    ):
        raise ResourceConflictError(
            message="Entry has changed since it was read",
            details={
                "error_code": "record_revision_conflict",
                "expected_record_revision": req.expected_record_revision,
                "current_record_revision": current_record_revision,
            },
        )
    operational_model, _, _ = await resolve_track_runtime_profile(track)
    current_schema_revision = schema_revision_from_profile_version(
        getattr(operational_model, "version_number", None)
    )
    if (
        req.expected_schema_revision is not None
        and req.expected_schema_revision != current_schema_revision
    ):
        raise ResourceConflictError(
            message="Entry schema has changed since it was read",
            details={
                "error_code": "schema_revision_conflict",
                "expected_schema_revision": req.expected_schema_revision,
                "current_schema_revision": current_schema_revision,
            },
        )

    if req.title is not None:
        validate_no_profanity(req.title, "title")
        entry.title = req.title
    if req.body is not None:
        validate_no_profanity(req.body, "body")
        entry.body = req.body

    et_slug = _slugify_key(
        str(
            (entry_type.form_schema or {}).get("_manifest_entry_type_key")
            or entry_type.name
            or ""
        )
    )
    preserved_cfs: Dict[str, Any] = {}
    if req.custom_fields is not None:
        _, runtime_tier, _ = await resolve_track_runtime_profile(track)
        entry_cfs = dict(entry.custom_fields or {})
        merged_cfs = {**entry_cfs, **req.custom_fields}
        if et_slug == "onboarding_form":
            if "employee" in entry_cfs:
                merged_cfs["employee"] = entry_cfs["employee"]
            incoming_status = (
                str(req.custom_fields.get("status") or "").strip().lower()
                if req.custom_fields
                else ""
            )
            current_status = str(entry_cfs.get("status") or "draft").strip().lower()
            if incoming_status == "submitted" and current_status in (
                "draft",
                "rejected",
            ):
                merged_cfs["status"] = "submitted"
            elif "status" in entry_cfs:
                merged_cfs["status"] = entry_cfs["status"]
            if incoming_status == "approved":
                raise BadRequestError(
                    message="Only HR staff can approve onboarding forms"
                )
            spec = resolve_entry_type_spec(entry_type, runtime_tier)
            allowed_keys = {
                str((f or {}).get("key") or "")
                for f in (spec.get("fields") or [])
                if isinstance(f, dict)
            }
            editable_cfs, preserved_cfs = partition_server_managed_custom_fields(
                entry_type_slug=et_slug,
                merged_custom_fields=merged_cfs,
                allowed_keys=allowed_keys,
            )
            merged_cfs = editable_cfs
        validated_cfs, relation_refs = (
            await validate_and_materialize_entry_custom_fields(
                track=track,
                entry_type=entry_type,
                custom_fields=merged_cfs,
                runtime_tier=runtime_tier,
                entry=entry,
                actor_user_id=user_id,
                actor_kind="human",
                source_entry_title=entry.title,
            )
        )
        if et_slug == "onboarding_form" and preserved_cfs:
            validated_cfs = {**validated_cfs, **preserved_cfs}
        entry.custom_fields = validated_cfs
        from app.services.operational_model_runtime import sync_relation_edges

        await sync_relation_edges(source_entry=entry, relation_refs=relation_refs)

    if et_slug == "onboarding_form":
        await materialize_employee_prefill_on_form(entry)

    entry.record_revision = current_record_revision + 1
    entry.schema_revision = current_schema_revision
    entry.updated_at = utc_now_iso()
    await entry.save()
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="entry.update",
        resource_type="Entry",
        resource_id=entry.id,
        before=None,
        after={"record_revision": entry.record_revision},
        scope=f"track:{track.id}",
    )

    await run_entry_save_hooks(
        entry=entry,
        workspace_id=workspace_id,
        actor_id=user_id,
        hook_point="entry.update",
    )

    from app.api.entries import _reembed_entry

    await _reembed_entry(entry)

    entry_data = await export_node(entry)
    if et_slug == "onboarding_form":
        entry_data = await enrich_onboarding_form_export(entry, entry_data)

    return {"entry": entry_data, "message": "Onboarding form updated"}


@endpoint(
    "/me/onboarding-form/onboarding-policies",
    methods=["GET"],
    auth=True,
    tags=[_LEGACY_TAG],
)
async def list_me_onboarding_policies(request: Request) -> Dict[str, Any]:
    workspace_id, _user_id, _form, track, _employee = await _resolve_member_form(
        request
    )
    _ = workspace_id
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
    "/me/onboarding-form/onboarding-policies/{policy_entry_id}/document",
    methods=["GET"],
    auth=True,
    tags=[_LEGACY_TAG],
)
async def download_me_onboarding_policy_document(
    request: Request, policy_entry_id: str
):
    _ws, _user_id, _form, track, _employee = await _resolve_member_form(request)
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
    "/me/onboarding-form/attachments",
    methods=["POST"],
    auth=True,
    tags=[_LEGACY_TAG],
)
async def upload_me_onboarding_attachment(request: Request) -> Dict[str, Any]:
    from app.api.attachments import _persist_uploaded_file, _read_multipart_files

    workspace_id, user_id, form, track, _employee = await _resolve_member_form(
        request, form_entry_id=_form_entry_id_from_request(request)
    )
    await _assert_form_editable(form, user_id, workspace_id)

    files = await _read_multipart_files(request, "file")
    if not files:
        raise BadRequestError(message="No file provided")

    result = await _persist_uploaded_file(
        entry=form,
        user_id=user_id,
        file=files[0],
    )
    attachment = result.get("attachment") or {}
    field_key = str(request.query_params.get("field_key") or "").strip()
    attachment_id = str(attachment.get("id") or "")
    if field_key and attachment_id:
        entry_type = await EntryType.get(form.type_id) if form.type_id else None
        if entry_type:
            _, runtime_tier, _ = await resolve_track_runtime_profile(track)
            merged_cfs = {
                **(form.custom_fields or {}),
                field_key: attachment_id,
            }
            validated_cfs, relation_refs = (
                await validate_and_materialize_entry_custom_fields(
                    track=track,
                    entry_type=entry_type,
                    custom_fields=merged_cfs,
                    runtime_tier=runtime_tier,
                    entry=form,
                    actor_user_id=user_id,
                    actor_kind="human",
                    source_entry_title=form.title,
                )
            )
            form.custom_fields = validated_cfs
            form.updated_at = utc_now_iso()
            await form.save()
            await emit_change_event(
                actor_kind="human",
                actor_id=user_id,
                action="entry.update",
                resource_type="Entry",
                resource_id=form.id,
                before=None,
                after={"attachment_field_updated": field_key},
                scope=f"track:{track.id}",
            )
            from app.services.operational_model_runtime import sync_relation_edges

            await sync_relation_edges(source_entry=form, relation_refs=relation_refs)

    payload: Dict[str, Any] = {**result, "message": "Attachment uploaded successfully"}
    if field_key and attachment_id:
        payload["field_key"] = field_key
        payload["field_value"] = attachment_id
    return payload


@endpoint(
    "/me/onboarding-form/contract/generate",
    methods=["POST"],
    auth=True,
    tags=[_LEGACY_TAG],
)
async def generate_me_onboarding_contract(request: Request) -> Dict[str, Any]:
    workspace_id, user_id, form, track, _employee = await _resolve_member_form(
        request, form_entry_id=_form_entry_id_from_request(request)
    )
    await _assert_form_editable(form, user_id, workspace_id)
    if not await contract_review_enabled(track, form):
        raise BadRequestError(message="Contract review is not enabled for this track")
    force_raw = str(request.query_params.get("force") or "").strip().lower()
    force_regenerate = force_raw in ("1", "true", "yes")
    try:
        return await generate_contract_for_form(
            workspace_id=workspace_id,
            track=track,
            form_entry=form,
            force_regenerate=force_regenerate,
            actor_user_id=user_id,
        )
    except ValueError as exc:
        raise BadRequestError(message=str(exc)) from exc


@endpoint(
    "/me/onboarding-form/contract",
    methods=["GET"],
    auth=True,
    tags=[_LEGACY_TAG],
)
async def download_me_onboarding_contract(request: Request):
    from fastapi.responses import StreamingResponse

    _workspace_id, user_id, form, track, _employee = await _resolve_member_form(
        request, form_entry_id=_form_entry_id_from_request(request)
    )
    if not await contract_review_enabled(track, form):
        raise ResourceNotFoundError(message="Contract review is not configured")
    try:
        blob, _gd_id = await get_contract_pdf_bytes(form)
    except ValueError as exc:
        raise ResourceNotFoundError(message=str(exc)) from exc
    _ = user_id
    return StreamingResponse(
        iter([blob]),
        media_type="application/pdf",
        headers={"Content-Disposition": 'inline; filename="employment-contract.pdf"'},
    )


@endpoint(
    "/me/onboarding-form/contract/decision",
    methods=["POST"],
    auth=True,
    tags=[_LEGACY_TAG],
)
async def decide_me_onboarding_contract(request: Request) -> Dict[str, Any]:
    raw = await request.json()
    body = MeOnboardingContractDecisionRequest.model_validate(raw)
    workspace_id, user_id, form, track, _employee = await _resolve_member_form(
        request, form_entry_id=_form_entry_id_from_request(request)
    )
    await _assert_form_editable(form, user_id, workspace_id)
    if not await contract_review_enabled(track, form):
        raise BadRequestError(message="Contract review is not enabled for this track")
    try:
        return await decide_contract(
            workspace_id=workspace_id,
            track=track,
            form_entry=form,
            action=body.action,
            signature_png=body.signature_png,
            reason=body.reason,
            return_url="",
            actor_user_id=user_id,
        )
    except ValueError as exc:
        raise BadRequestError(message=str(exc)) from exc
