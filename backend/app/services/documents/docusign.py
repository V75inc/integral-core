"""Deprecated — DocuSign lives under app.services.esign (E-sign app only)."""

from app.services.esign.docusign import (  # noqa: F401
    create_embedded_signing_session,
    docusign_configured,
    fetch_completed_envelope,
)
