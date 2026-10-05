"""Optional DocuSign envelope integration for onboarding contract signing."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def docusign_configured() -> bool:
    required = (
        "DOCUSIGN_INTEGRATION_KEY",
        "DOCUSIGN_USER_ID",
        "DOCUSIGN_ACCOUNT_ID",
    )
    return all(str(os.environ.get(k) or "").strip() for k in required)


async def create_embedded_signing_session(
    *,
    pdf_bytes: bytes,
    signer_email: str,
    signer_name: str,
    signature_places: List[Dict[str, Any]],
    return_url: str,
) -> Dict[str, Any]:
    """Create a DocuSign envelope and return an embedded signing URL.

    When credentials are not configured, returns ``{"configured": False}``.
    """
    if not docusign_configured():
        return {"configured": False, "reason": "docusign_not_configured"}

    # Full REST integration is environment-specific; keep a stable contract
    # surface for tests and future wiring without blocking canvas signing.
    logger.info(
        "DocuSign configured but embedded signing is not enabled in this build "
        "(signer=%s)",
        signer_email,
    )
    return {
        "configured": True,
        "available": False,
        "reason": "docusign_embed_not_implemented",
        "signer_name": signer_name,
        "return_url": return_url,
        "place_count": len(signature_places or []),
        "pdf_size": len(pdf_bytes or b""),
    }


async def fetch_completed_envelope(
    envelope_id: str,
) -> Optional[Dict[str, Any]]:
    """Poll/download completed envelope artifacts when configured."""
    if not docusign_configured() or not envelope_id:
        return None
    return None
