"""Public onboarding employment contract — generate, view, accept/reject."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from app.models.edges import HAS_ATTACHMENT, HAS_GENERATED_DOCUMENT, REFERENCES
from app.models.nodes import Attachment, Entry, GeneratedDocument, Track
from app.services.attachment_storage import get_attachment_storage_service
from app.services.documents.context_resolver import (
    batch_resolve,
    collect_field_keys_from_document,
    token_meta_from_document,
)
from app.services.documents.field_registry import (
    get_field_spec,
    list_fields_for_track,
    rebuild_workspace_field_index,
)
from app.services.documents.formatters import format_value
from app.services.documents.output import checksum_bytes, render_output
from app.services.documents.render import merge_document_to_html
from app.services.documents.signature_places import resolve_signature_places
from app.services.esign.contract_decision import (
    accept_contract_canvas,
)
from app.services.esign.contract_decision import (
    reject_contract as esign_reject_contract,
)
from app.services.esign.dispatch import dispatch_contract_decision
from app.services.track_public_share import declared_public_share
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)

PUBLIC_ACTOR = "public"
CONTRACT_ACCEPTED = "accepted"
CONTRACT_REJECTED = "rejected"
CONTRACT_GENERATED = "generated"
# Bump when merge/resolver/render behavior changes so stale PDFs are not reused.
CONTRACT_MERGE_RESOLVER_VERSION = "2026-03-21-v2"


def _cf(entry: Any) -> Dict[str, Any]:
    return dict(getattr(entry, "custom_fields", None) or {})


def _as_single_id(value: Any) -> str:
    if isinstance(value, list):
        return str(value[0] or "").strip() if value else ""
    return str(value or "").strip()


async def _contract_extensions(track: Track) -> Dict[str, Any]:
    declared = await declared_public_share(track)
    if not declared:
        return {}
    return dict(declared.get("extensions") or {})


async def _resolve_contract_workspace_id(
    scope_workspace_id: str, *, track: Track, form_entry: Entry
) -> str:
    """Document Templates and merge fields live in the form track's workspace."""
    from app.services.onboarding_form_member import _effective_track_workspace_id

    track_ws = await _effective_track_workspace_id(track)
    if track_ws:
        return track_ws
    form_ws = str(getattr(form_entry, "workspace_id", "") or "").strip()
    if form_ws:
        return form_ws
    return scope_workspace_id


def _form_contract_template_id(form_entry: Optional[Entry]) -> str:
    if form_entry is None:
        return ""
    cf = _cf(form_entry)
    return _as_single_id(cf.get("contract_template") or cf.get("contract_template_id"))


async def contract_review_enabled(
    track: Track, form_entry: Optional[Entry] = None
) -> bool:
    ext = await _contract_extensions(track)
    if str(ext.get("contract_review_document_type") or "").strip():
        return True
    return bool(_form_contract_template_id(form_entry))


async def _document_type(track: Track, form_entry: Optional[Entry] = None) -> str:
    ext = await _contract_extensions(track)
    dt = str(ext.get("contract_review_document_type") or "").strip()
    if dt:
        return dt
    if _form_contract_template_id(form_entry):
        return "employment_contract"
    return ""


def _contract_source_fingerprint(
    *,
    template_version_id: str,
    contract_template_id: str,
    template_content_checksum: str,
    layout_id: str,
    resolved_values: Dict[str, Any],
) -> str:
    """Fingerprint from published template body + merge data (not form-only edits)."""
    payload = {
        "template_version_id": template_version_id,
        "contract_template_id": contract_template_id,
        "template_content_checksum": template_content_checksum,
        "layout_id": layout_id or "",
        "merge_resolver_version": CONTRACT_MERGE_RESOLVER_VERSION,
        "values": resolved_values,
    }
    raw = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def _primary_context_entry(form_entry: Entry) -> Entry:
    emp_id = _as_single_id(_cf(form_entry).get("employee"))
    if emp_id:
        employee = await Entry.get(emp_id)
        if employee:
            return employee
    return form_entry


def _contract_input_values(
    form_entry: Entry, primary_entry: Optional[Entry]
) -> Dict[str, Any]:
    """Defaults for ``document_input`` merge fields on public contract review."""
    form_cf = _cf(form_entry)
    emp_cf = _cf(primary_entry) if primary_entry else {}
    out: Dict[str, Any] = {}

    effective = (
        form_cf.get("effective_date")
        or form_cf.get("proposed_start_date")
        or emp_cf.get("start_date")
        or emp_cf.get("proposed_start_date")
    )
    if effective not in (None, ""):
        out["effective_date"] = effective

    signer = (
        str(form_cf.get("signer_name") or "").strip()
        or str(getattr(primary_entry, "title", "") or "").strip()
        or str(form_cf.get("full_name") or form_cf.get("legal_name") or "").strip()
    )
    if signer:
        out["signer_name"] = signer

    work_email = emp_cf.get("work_email") or form_cf.get("work_email")
    if work_email not in (None, ""):
        out["work_email"] = work_email
    return out


async def _resolve_via_employee_relation(
    context_entry: Any, field_key: str
) -> Tuple[Any, Optional[str]]:
    """Onboarding form → linked employee, including hire-field aliases."""
    from app.services.documents.context_resolver import _load_entry, _read_path
    from app.services.documents.field_ref import parse_field_ref, slug_ref_part
    from app.services.documents.hire_readiness import _EMPLOYEE_KEY_ALIASES

    emp_id = _as_single_id(_cf(context_entry).get("employee"))
    if not emp_id:
        return None, None
    employee = await _load_entry(emp_id)
    if not employee:
        return None, None

    sub_key = field_key
    parsed = parse_field_ref(field_key)
    if parsed and slug_ref_part(parsed["track_key"]) in ("employees", "employee"):
        sub_key = parsed["local_field"]
    elif field_key.startswith("employee."):
        sub_key = field_key.split(".", 1)[1]

    if sub_key in ("title", "body"):
        val = _read_path(employee, sub_key)
        if val not in (None, ""):
            return val, None

    val = _read_path(employee, f"custom_fields.{sub_key}")
    if val not in (None, ""):
        return val, None

    if sub_key == "full_name":
        return getattr(employee, "title", None), None

    for alias_key in (field_key, f"employee.{sub_key}", sub_key):
        for path in _EMPLOYEE_KEY_ALIASES.get(alias_key) or ():
            if path == "__input_work_email__":
                continue
            val = _read_path(employee, path)
            if val not in (None, ""):
                return val, None
    return None, None


async def _resolve_via_candidate_relation(
    context_entry: Any, field_key: str
) -> Tuple[Any, Optional[str]]:
    """Onboarding form → linked candidate offer fields."""
    from app.services.documents.context_resolver import _load_entry, _read_path
    from app.services.documents.hire_readiness import _EMPLOYEE_KEY_ALIASES

    cand_id = _as_single_id(_cf(context_entry).get("candidate"))
    if not cand_id:
        return None, None
    candidate = await _load_entry(cand_id)
    if not candidate:
        return None, None

    if field_key.startswith("candidate."):
        sub_key = field_key.split(".", 1)[1]
        if sub_key in ("full_name", "title"):
            return getattr(candidate, "title", None), None
        val = _read_path(candidate, f"custom_fields.{sub_key}")
        if val not in (None, ""):
            return val, None

    for path in _EMPLOYEE_KEY_ALIASES.get(field_key) or ():
        if path == "__input_work_email__":
            continue
        val = _read_path(candidate, path)
        if val not in (None, ""):
            return val, None
    return None, None


async def _apply_contract_resolution_fallbacks(
    *,
    workspace_id: str,
    form_entry: Entry,
    primary_entry: Entry,
    field_keys: List[str],
    resolved: Dict[str, Any],
    input_values: Dict[str, Any],
    token_meta: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Same extra passes as hire readiness — candidate/opening/qualified track reads."""
    from app.services.documents.hire_readiness import (
        _resolve_alias_on_candidate,
        _resolve_on_linked_opening,
        _resolve_qualified_track_field,
    )

    values = dict(resolved.get("values") or {})
    formatted = dict(resolved.get("formatted") or {})
    warnings = list(resolved.get("warnings") or [])

    cand_id = _as_single_id(_cf(form_entry).get("candidate"))
    candidate = await Entry.get(cand_id) if cand_id else None

    for key in field_keys:
        if values.get(key) not in (None, ""):
            continue
        spec = get_field_spec(workspace_id, key) or {}
        data_type = str(spec.get("data_type") or "text")
        meta_fmt = (token_meta or {}).get(key, {}).get("format")

        raw = None
        if candidate is not None:
            qval, qwarn = _resolve_qualified_track_field(candidate, key)
            if qval not in (None, ""):
                raw = qval
            elif qwarn:
                warnings.append(qwarn)
            if raw in (None, ""):
                alias_val, alias_warn = await _resolve_alias_on_candidate(
                    candidate, key, input_values=input_values
                )
                if alias_val not in (None, ""):
                    raw = alias_val
                elif alias_warn:
                    warnings.append(alias_warn)
            if raw in (None, ""):
                opening_val, opening_warn = await _resolve_on_linked_opening(
                    candidate, key, spec
                )
                if opening_val not in (None, ""):
                    raw = opening_val
                elif opening_warn:
                    warnings.append(opening_warn)

        if raw in (None, ""):
            emp_val, _ = await _resolve_via_employee_relation(form_entry, key)
            if emp_val not in (None, ""):
                raw = emp_val
            if raw in (None, ""):
                cand_val, _ = await _resolve_via_candidate_relation(form_entry, key)
                if cand_val not in (None, ""):
                    raw = cand_val

        if raw not in (None, ""):
            values[key] = raw
            formatted[key] = format_value(raw, data_type=data_type, fmt=meta_fmt)

    return {
        **resolved,
        "values": values,
        "formatted": formatted,
        "warnings": warnings,
    }


def _merge_values_nonempty(values: Dict[str, Any]) -> int:
    n = 0
    for key, val in values.items():
        if key == "signature_places":
            continue
        if val not in (None, "", [], {}):
            n += 1
    return n


async def _stored_merge_nonempty(generated_document_id: str) -> int:
    if not generated_document_id:
        return 0
    gd = await GeneratedDocument.get(generated_document_id)
    if not gd:
        return 0
    snap = dict(getattr(gd, "resolved_snapshot", None) or {})
    return _merge_values_nonempty(snap)


async def _load_generated_pdf(
    generated_document_id: str,
) -> Tuple[Optional[bytes], Optional[GeneratedDocument]]:
    gd = await GeneratedDocument.get(generated_document_id)
    if not gd:
        return None, None
    att_id = str(getattr(gd, "attachment_id", "") or "")
    if not att_id:
        return None, gd
    att = await Attachment.get(att_id)
    if not att or not att.storage_key:
        return None, gd
    storage = get_attachment_storage_service()
    blob = await storage.read_attachment(att.storage_key)
    return blob, gd


async def generate_contract_for_form(
    *,
    workspace_id: str,
    track: Track,
    form_entry: Entry,
    force_regenerate: bool = False,
) -> Dict[str, Any]:
    """Generate (or reuse) employment contract PDF for an onboarding form."""
    workspace_id = await _resolve_contract_workspace_id(
        workspace_id, track=track, form_entry=form_entry
    )
    document_type = await _document_type(track, form_entry)
    if not document_type:
        raise ValueError("Contract review is not configured for this track")

    cf = _cf(form_entry)
    stored_fp = str(cf.get("contract_generate_fingerprint") or "")
    existing_gd_id = str(cf.get("contract_generated_document_id") or "")
    contract_status = str(cf.get("contract_status") or "pending").strip().lower()

    from app.services.documents.entry_template_store import (
        extract_contract_template_id,
        resolve_for_generate,
        resolve_layout_parts,
    )

    template_id = extract_contract_template_id(cf)
    tmpl, ver = None, None  # type: ignore[assignment]

    async def _resolve_by_document_type() -> None:
        nonlocal tmpl, ver
        for mod in ("hr_app", "recruitment-app", "recruitment_app"):
            try:
                tmpl, ver = await resolve_for_generate(
                    workspace_id=workspace_id,
                    document_type=document_type,
                    module=mod,
                )
                return
            except ValueError:
                continue
        tmpl, ver = await resolve_for_generate(
            workspace_id=workspace_id,
            document_type=document_type,
        )

    if template_id:
        try:
            tmpl, ver = await resolve_for_generate(
                workspace_id=workspace_id,
                template_id=template_id,
            )
        except ValueError:
            tmpl, ver = None, None  # type: ignore[assignment]
    if tmpl is None or ver is None:
        try:
            await _resolve_by_document_type()
        except ValueError as exc:
            hint = (
                "No active employment contract template found. "
                "Publish a template in Document Templates and link it on hire."
            )
            if template_id:
                hint = (
                    f"Contract template {template_id} was not found in this workspace "
                    f"and no default {document_type} template is available. "
                    "Re-link the template on hire or publish one in Document Templates."
                )
            raise ValueError(hint) from exc

    doc = ver.editor_document or {}
    keys = collect_field_keys_from_document(doc, ver.token_metadata or {})
    track_id = str(getattr(tmpl, "track_id", "") or "")
    token_meta = token_meta_from_document(doc, ver.token_metadata)
    primary_entry = await _primary_context_entry(form_entry)
    index_stats = await rebuild_workspace_field_index(workspace_id)
    if track_id:
        await list_fields_for_track(
            workspace_id, track_id, module=str(getattr(tmpl, "module", "") or "")
        )
    if not index_stats.get("fields"):
        logger.warning(
            "contract generate: workspace field registry empty ws=%s entry=%s",
            workspace_id,
            form_entry.id,
        )
    merge_context_entry = primary_entry
    merge_anchor_entry = form_entry
    input_values = _contract_input_values(form_entry, primary_entry)
    resolved = await batch_resolve(
        workspace_id=workspace_id,
        field_keys=keys,
        context_entry=merge_context_entry,
        anchor_entry=merge_anchor_entry,
        caller_role="editor",
        input_values=input_values,
        mode="live",
        token_meta=token_meta,
    )
    resolved = await _apply_contract_resolution_fallbacks(
        workspace_id=workspace_id,
        form_entry=form_entry,
        primary_entry=primary_entry,
        field_keys=keys,
        resolved=resolved,
        input_values=input_values,
        token_meta=token_meta,
    )
    from app.services.documents.entry_template_store import version_content_checksum

    template_checksum = version_content_checksum(ver)
    current_fp = _contract_source_fingerprint(
        template_version_id=ver.id,
        contract_template_id=template_id,
        template_content_checksum=template_checksum,
        layout_id=str(getattr(ver, "layout_id", "") or ""),
        resolved_values=dict(resolved["values"]),
    )
    resolved_nonempty = _merge_values_nonempty(dict(resolved["values"]))
    stored_nonempty = await _stored_merge_nonempty(existing_gd_id)
    if resolved_nonempty == 0 and keys:
        logger.warning(
            "contract generate: no merge fields resolved entry=%s keys=%s warnings=%s",
            form_entry.id,
            keys[:12],
            [w for w in resolved.get("warnings") or [] if not w.startswith("missing:")][
                :8
            ],
        )

    if (
        not force_regenerate
        and existing_gd_id
        and stored_fp == current_fp
        and contract_status in (CONTRACT_GENERATED, CONTRACT_ACCEPTED)
        and stored_nonempty > 0
        and resolved_nonempty <= stored_nonempty
    ):
        blob, gd = await _load_generated_pdf(existing_gd_id)
        if blob and gd:
            return {
                "generated_document_id": gd.id,
                "attachment_id": gd.attachment_id,
                "reused": True,
                "contract_status": contract_status,
            }
    if any(w.startswith("block:") for w in resolved["warnings"]):
        raise ValueError(
            "Missing required contract fields: "
            + ", ".join(w for w in resolved["warnings"] if w.startswith("block:"))
        )

    layout = await resolve_layout_parts(ver.layout_id or "", ver.header_footer or {})
    html = merge_document_to_html(
        doc,
        resolved["formatted"],
        header_html=layout["header_html"],
        footer_html=layout["footer_html"],
        page_size=layout["page_size"],
        margins=layout["margins"],
        page_numbers=layout["page_numbers"],
        title=tmpl.name or "Employment Contract",
        embed_pre_signatures=False,
        highlight_field_tokens=False,
    )
    data, mime, ext = render_output(html, "pdf", title=tmpl.name or "Document")
    token_meta = ver.token_metadata or {}
    all_places = resolve_signature_places(
        html=html,
        editor_document=doc,
        pdf_bytes=data,
        token_metadata=token_meta,
    )
    from app.services.documents.signature_embed import apply_pre_embedded_signatures
    from app.services.documents.signature_places import runtime_signature_places

    data = await apply_pre_embedded_signatures(
        data,
        html=html,
        editor_document=doc,
        token_metadata=token_meta,
    )
    places = runtime_signature_places(all_places)
    digest = checksum_bytes(data)
    now = utc_now_iso()

    gd = await GeneratedDocument.create(
        workspace_id=workspace_id,
        template_id=tmpl.id,
        template_version_id=ver.id,
        module=tmpl.module,
        context_type=tmpl.context_type or "onboarding_form",
        context_entry_id=form_entry.id,
        generated_by=PUBLIC_ACTOR,
        generated_at=now,
        output_format="pdf",
        attachment_id="",
        input_values={},
        resolved_snapshot={
            **dict(resolved["formatted"]),
            "signature_places": all_places,
        },
        checksum=digest,
        status="generated",
        created_at=now,
    )

    safe_name = (tmpl.name or "employment-contract").replace("/", "-").strip()
    filename = f"{safe_name}.{ext}"
    attachment = await Attachment.create(
        filename=filename,
        mime_type=mime,
        size=len(data),
        storage_key="",
        source_type="file",
        external_url="",
        uploaded_by=PUBLIC_ACTOR,
        scan_status="skipped",
        metadata_status="pending",
        owner_kind="entry",
        content_hash=digest,
        created_at=now,
    )
    storage = get_attachment_storage_service()
    try:
        stored = await storage.save_attachment(
            entry_id=form_entry.id,
            attachment_id=attachment.id,
            filename=filename,
            content=data,
        )
    except Exception:
        await attachment.delete()
        await gd.delete()
        raise
    attachment.storage_key = str(stored.get("path") or "")
    await attachment.save()

    await gd.connect(
        attachment,
        edge=HAS_ATTACHMENT,
        attached_at=now,
        attached_by=PUBLIC_ACTOR,
    )
    await form_entry.connect(
        attachment,
        edge=HAS_ATTACHMENT,
        attached_at=now,
        attached_by=PUBLIC_ACTOR,
    )
    from app.services.documents.entry_template_store import (
        create_generated_document_entry,
        is_entry_mode,
    )

    if getattr(tmpl, "entry_backed", False) and await is_entry_mode(workspace_id):
        await create_generated_document_entry(
            workspace_id=workspace_id,
            template_id=tmpl.id,
            template_version_id=ver.id,
            module=tmpl.module,
            context_type=tmpl.context_type or "onboarding_form",
            context_entry_id=form_entry.id,
            generated_by=PUBLIC_ACTOR,
            output_format="pdf",
            attachment_id=attachment.id,
            input_values={},
            resolved_snapshot={
                **dict(resolved["formatted"]),
                "signature_places": all_places,
            },
            checksum=digest,
            title=tmpl.name or "Employment Contract",
        )
    else:
        await tmpl.connect(gd, edge=HAS_GENERATED_DOCUMENT, generated_at=now)
    try:
        await form_entry.connect(gd, edge=REFERENCES, field_key="contract_generated")
    except Exception:
        logger.exception("contract generate: REFERENCES wire failed")

    gd.attachment_id = attachment.id
    await gd.save()

    patch = {
        "contract_status": CONTRACT_GENERATED,
        "contract_generated_document_id": gd.id,
        "contract_signature_places": places,
        "contract_generate_fingerprint": current_fp,
    }
    merged = {**_cf(form_entry), **patch}
    form_entry.custom_fields = merged
    await form_entry.save()

    return {
        "generated_document_id": gd.id,
        "attachment_id": attachment.id,
        "reused": False,
        "contract_status": CONTRACT_GENERATED,
        "signature_places": places,
    }


async def get_contract_pdf_bytes(
    form_entry: Entry,
) -> Tuple[bytes, str]:
    cf = _cf(form_entry)
    gd_id = str(cf.get("contract_generated_document_id") or "")
    if not gd_id:
        raise ValueError("Contract has not been generated")
    blob, _gd = await _load_generated_pdf(gd_id)
    if not blob:
        raise ValueError("Contract PDF not found")
    return blob, gd_id


async def _notify_workspace_admins(
    *,
    workspace_id: str,
    track: Track,
    form_entry: Entry,
    event: str,
    reason: str = "",
) -> Dict[str, Any]:
    from app.models.edges import IS_MEMBER_OF
    from app.models.nodes import User, Workspace
    from app.services.app_graph import create_notification
    from app.services.email_service import EmailMessage, send_email
    from app.services.permissions import get_user_node
    from app.services.workspace_permissions import _read_member_edge_role

    ws = await Workspace.get(workspace_id)
    if not ws:
        return {"notified": 0, "emailed": 0}

    name = str(getattr(form_entry, "title", "") or "Candidate").strip()
    subject = (
        f"Contract accepted: {name}"
        if event == "accepted"
        else f"Contract rejected: {name}"
    )
    body_lines = [
        f"The employment contract for {name} was {event}.",
        f"Track: {track.title or 'Employee onboarding'}",
        f"Form entry: {form_entry.id}",
    ]
    if reason:
        body_lines.append(f"Reason: {reason}")
    body = "\n".join(body_lines)

    notified = 0
    emailed = 0
    members = await ws.nodes(edge=[IS_MEMBER_OF], direction="in", node=["User"])
    for member in members or []:
        if not isinstance(member, User):
            continue
        role = await _read_member_edge_role(member, workspace_id)
        if role not in ("owner", "admin"):
            continue
        uid = str(getattr(member, "user_id", "") or member.id)
        try:
            await create_notification(
                user_id=uid,
                type="onboarding_contract",
                content=subject,
                metadata={
                    "event": event,
                    "form_entry_id": form_entry.id,
                    "track_id": track.id,
                    "reason": reason or None,
                },
            )
            notified += 1
        except Exception:
            logger.exception("contract notify: in-app failed for %s", uid)
        user = await get_user_node(uid)
        email = str(getattr(user, "email", "") or "").strip() if user else ""
        if email:
            ok = await send_email(
                EmailMessage(
                    to=email,
                    subject=subject,
                    html=f"<p>{body.replace(chr(10), '<br/>')}</p>",
                    text=body,
                )
            )
            if ok:
                emailed += 1
    return {"notified": notified, "emailed": emailed}


async def _invoke_hr_finalize_accept(
    *,
    workspace_id: str,
    form_entry_id: str,
    employee_id: str,
    signed_attachment_id: str,
    certificate_attachment_id: str = "",
    start_date: str = "",
    signer_name: str = "",
) -> Dict[str, Any]:
    from app.services.hooks.registry import ToolContext, get_workspace_tools
    from app.services.hooks.tool_dispatch import run_tool
    from app.services.workspace_permissions import get_workspace_owner_user_id

    owner_id = await get_workspace_owner_user_id(workspace_id) or "system"
    tools = get_workspace_tools(workspace_id)
    spec = tools.get("hr_onboarding_contract_finalize")
    if not spec:
        return {"ok": False, "reason": "hr_onboarding_contract_finalize tool missing"}
    ctx = ToolContext(
        user_id=owner_id,
        workspace_id=workspace_id,
        scope=f"entry:{form_entry_id}",
    )
    payload = {
        "form_entry_id": form_entry_id,
        "employee_id": employee_id,
        "signed_attachment_id": signed_attachment_id,
        "certificate_attachment_id": certificate_attachment_id,
        "start_date": start_date,
        "signer_name": signer_name,
    }
    return await run_tool(spec, payload, ctx)


async def decide_contract(
    *,
    workspace_id: str,
    track: Track,
    form_entry: Entry,
    action: str,
    signature_png: Optional[str] = None,
    reason: Optional[str] = None,
    return_url: str = "",
    actor_user_id: str = PUBLIC_ACTOR,
) -> Dict[str, Any]:
    """Accept or reject the onboarding employment contract."""
    workspace_id = await _resolve_contract_workspace_id(
        workspace_id, track=track, form_entry=form_entry
    )
    act = str(action or "").strip().lower()
    cf = _cf(form_entry)
    current = str(cf.get("contract_status") or "pending").strip().lower()

    if current == CONTRACT_REJECTED:
        raise ValueError("Contract was already rejected")
    if current == CONTRACT_ACCEPTED:
        raise ValueError("Contract was already accepted")

    actor_id = str(actor_user_id or PUBLIC_ACTOR).strip() or PUBLIC_ACTOR
    # Omit unset optional fields — bundle tool JSON schemas use plain ``string``
    # types; passing JSON ``null`` fails jsonschema validation before the handler runs.
    decision_payload: Dict[str, Any] = {
        "action": act,
        "form_entry_id": form_entry.id,
        "track_id": track.id,
        "actor_id": actor_id,
        "use_docusign": True,
    }
    if signature_png is not None:
        decision_payload["signature_png"] = signature_png
    if reason is not None:
        decision_payload["reason"] = reason
    return_url_norm = str(return_url or "").strip()
    if return_url_norm:
        decision_payload["return_url"] = return_url_norm

    esign_out = await dispatch_contract_decision(
        workspace_id=workspace_id,
        actor_user_id=actor_id,
        payload=decision_payload,
    )

    if esign_out is not None:
        if esign_out.get("contract_status") == CONTRACT_REJECTED:
            notify = await _notify_workspace_admins(
                workspace_id=workspace_id,
                track=track,
                form_entry=form_entry,
                event="rejected",
                reason=str(reason or ""),
            )
            return {**esign_out, "notify": notify}
        if esign_out.get("docusign"):
            return esign_out
        if esign_out.get("contract_status") == CONTRACT_ACCEPTED:
            return await _finalize_contract_acceptance(
                workspace_id=workspace_id,
                track=track,
                form_entry=form_entry,
                cf=_cf(form_entry),
                signed_attachment_id=str(esign_out.get("signed_attachment_id") or ""),
                esign_out=esign_out,
            )

    if act == "reject":
        await esign_reject_contract(form_entry=form_entry, reason=str(reason or ""))
        notify = await _notify_workspace_admins(
            workspace_id=workspace_id,
            track=track,
            form_entry=form_entry,
            event="rejected",
            reason=str(reason or ""),
        )
        return {"contract_status": CONTRACT_REJECTED, "notify": notify}

    if act != "accept":
        raise ValueError("action must be accept or reject")

    legacy_accept = await accept_contract_canvas(
        form_entry=form_entry,
        signature_png=str(signature_png or ""),
        actor_id=actor_id,
    )
    return await _finalize_contract_acceptance(
        workspace_id=workspace_id,
        track=track,
        form_entry=form_entry,
        cf=_cf(form_entry),
        signed_attachment_id=str(legacy_accept.get("signed_attachment_id") or ""),
        esign_out=legacy_accept,
    )


async def _finalize_contract_acceptance(
    *,
    workspace_id: str,
    track: Track,
    form_entry: Entry,
    cf: Dict[str, Any],
    signed_attachment_id: str,
    esign_out: Dict[str, Any],
) -> Dict[str, Any]:
    employee_id = _as_single_id(cf.get("employee"))
    start_date = str(cf.get("start_date") or cf.get("employment_start_date") or "")
    hr_out = await _invoke_hr_finalize_accept(
        workspace_id=workspace_id,
        form_entry_id=form_entry.id,
        employee_id=employee_id,
        signed_attachment_id=signed_attachment_id,
        start_date=start_date,
        signer_name=str(getattr(form_entry, "title", "") or ""),
    )
    merged = {
        **_cf(form_entry),
        "contract_entry_id": str(
            hr_out.get("contract_entry_id") or cf.get("contract_entry_id") or ""
        ),
    }
    form_entry.custom_fields = merged
    await form_entry.save()

    notify = await _notify_workspace_admins(
        workspace_id=workspace_id,
        track=track,
        form_entry=form_entry,
        event="accepted",
    )
    return {
        **esign_out,
        "contract_entry_id": merged.get("contract_entry_id"),
        "notify": notify,
        "hr": hr_out,
    }
