"""Apply canvas signatures to contract PDFs and persist artifacts on form entries."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.models.edges import HAS_ATTACHMENT
from app.models.nodes import Attachment, Entry, GeneratedDocument
from app.services.attachment_storage import get_attachment_storage_service
from app.services.documents.output import checksum_bytes
from app.services.documents.signature_overlay import (
    decode_signature_png,
    overlay_signature_png,
)
from app.services.documents.signature_places import pick_runtime_sign_place
from app.services.esign.docusign import create_embedded_signing_session
from app.services.esign.io import load_generated_pdf
from app.utils.time import utc_now_iso

PUBLIC_ACTOR = "public"
CONTRACT_ACCEPTED = "accepted"
CONTRACT_REJECTED = "rejected"
CONTRACT_GENERATED = "generated"


def _cf(entry: Any) -> Dict[str, Any]:
    return dict(getattr(entry, "custom_fields", None) or {})


async def reject_contract(*, form_entry: Entry, reason: str = "") -> Dict[str, Any]:
    cf = _cf(form_entry)
    merged = {
        **cf,
        "contract_status": CONTRACT_REJECTED,
        "contract_reject_reason": str(reason or "").strip(),
    }
    form_entry.custom_fields = merged
    await form_entry.save()
    return {"contract_status": CONTRACT_REJECTED}


async def try_docusign_accept(
    *,
    pdf_bytes: bytes,
    form_entry: Entry,
    places: List[Dict[str, Any]],
    return_url: str,
) -> Optional[Dict[str, Any]]:
    """When DocuSign is fully wired, return early with embedded session payload."""
    cf = _cf(form_entry)
    emp_id = _as_single_id(cf.get("employee"))
    employee = await Entry.get(emp_id) if emp_id else None
    signer_email = ""
    if employee:
        signer_email = str(_cf(employee).get("work_email") or "").strip()
    signer_name = str(getattr(form_entry, "title", "") or "").strip()
    docusign = await create_embedded_signing_session(
        pdf_bytes=pdf_bytes,
        signer_email=signer_email,
        signer_name=signer_name,
        signature_places=places,
        return_url=return_url,
    )
    if docusign.get("available"):
        return {
            "contract_status": CONTRACT_GENERATED,
            "docusign": docusign,
        }
    return None


async def accept_contract_canvas(
    *,
    form_entry: Entry,
    signature_png: str,
    actor_id: str = PUBLIC_ACTOR,
    places: Optional[List[Dict[str, Any]]] = None,
    pdf_bytes: Optional[bytes] = None,
    generated_document_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Overlay drawn signature on contract PDF and attach signed PDF + PNG to entry."""
    cf = _cf(form_entry)
    gd_id = str(generated_document_id or cf.get("contract_generated_document_id") or "")
    if not gd_id:
        raise ValueError("Generate the contract before accepting")

    if pdf_bytes is None:
        pdf_bytes, gd = await load_generated_pdf(gd_id)
        if not pdf_bytes:
            raise ValueError("Contract PDF not found")
    else:
        gd = await GeneratedDocument.get(gd_id)

    place_list = list(places or cf.get("contract_signature_places") or [])
    if not place_list and gd:
        snap = dict(getattr(gd, "resolved_snapshot", None) or {})
        place_list = list(snap.get("signature_places") or [])

    if not signature_png:
        raise ValueError("Drawn signature is required")

    employee_place = pick_runtime_sign_place(place_list)
    if employee_place is None:
        raise ValueError(
            "Template has no employee signature block configured for signing"
        )
    png_bytes = decode_signature_png(signature_png)
    sign_role = str(employee_place.get("role") or "employee_signature")
    signed_pdf = overlay_signature_png(
        pdf_bytes, png_bytes, [employee_place], role=sign_role
    )

    now = utc_now_iso()
    attached_by = str(actor_id or PUBLIC_ACTOR).strip() or PUBLIC_ACTOR
    digest = checksum_bytes(signed_pdf)
    signed_att = await Attachment.create(
        filename="signed-employment-contract.pdf",
        mime_type="application/pdf",
        size=len(signed_pdf),
        storage_key="",
        source_type="file",
        external_url="",
        uploaded_by=attached_by,
        scan_status="skipped",
        metadata_status="pending",
        owner_kind="entry",
        content_hash=digest,
        created_at=now,
    )
    storage = get_attachment_storage_service()
    stored = await storage.save_attachment(
        entry_id=form_entry.id,
        attachment_id=signed_att.id,
        filename="signed-employment-contract.pdf",
        content=signed_pdf,
    )
    signed_att.storage_key = str(stored.get("path") or "")
    await signed_att.save()
    await form_entry.connect(
        signed_att,
        edge=HAS_ATTACHMENT,
        attached_at=now,
        attached_by=attached_by,
    )

    sig_png_att = await Attachment.create(
        filename="signature.png",
        mime_type="image/png",
        size=len(png_bytes),
        storage_key="",
        source_type="file",
        external_url="",
        uploaded_by=attached_by,
        scan_status="skipped",
        metadata_status="pending",
        owner_kind="entry",
        content_hash=checksum_bytes(png_bytes),
        created_at=now,
    )
    sig_stored = await storage.save_attachment(
        entry_id=form_entry.id,
        attachment_id=sig_png_att.id,
        filename="signature.png",
        content=png_bytes,
    )
    sig_png_att.storage_key = str(sig_stored.get("path") or "")
    await sig_png_att.save()
    await form_entry.connect(
        sig_png_att,
        edge=HAS_ATTACHMENT,
        attached_at=now,
        attached_by=attached_by,
    )

    merged = {
        **cf,
        "contract_status": CONTRACT_ACCEPTED,
        "contract_signed_at": now,
        "contract_signature_attachment_id": sig_png_att.id,
    }
    form_entry.custom_fields = merged
    await form_entry.save()

    return {
        "contract_status": CONTRACT_ACCEPTED,
        "contract_signed_at": now,
        "contract_signature_attachment_id": sig_png_att.id,
        "signed_attachment_id": signed_att.id,
        "signature_png_attachment_id": sig_png_att.id,
    }


def _as_single_id(value: Any) -> str:
    if isinstance(value, list):
        return str(value[0] or "").strip() if value else ""
    return str(value or "").strip()


async def resolve_places_for_form(
    form_entry: Entry,
) -> Tuple[bytes, List[Dict[str, Any]]]:
    cf = _cf(form_entry)
    gd_id = str(cf.get("contract_generated_document_id") or "")
    pdf_bytes, gd = await load_generated_pdf(gd_id)
    if not pdf_bytes:
        raise ValueError("Contract PDF not found")
    places = list(cf.get("contract_signature_places") or [])
    if not places and gd:
        snap = dict(getattr(gd, "resolved_snapshot", None) or {})
        places = list(snap.get("signature_places") or [])
    return pdf_bytes, places
