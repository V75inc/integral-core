"""Helpers for public onboarding-form PATCH flows."""

from __future__ import annotations

from typing import Any, Dict, Tuple

# Server-written onboarding_form keys the public wizard must not validate/edit,
# but which may exist on the entry after hire / contract generation.
ONBOARDING_SERVER_MANAGED_CUSTOM_FIELDS = frozenset(
    {
        "employee",
        "status",
        "submitted_at",
        "policy_acknowledgments",
        "contract_status",
        "contract_generated_document_id",
        "contract_signed_at",
        "contract_signature_attachment_id",
        "contract_entry_id",
        "contract_docusign_envelope_id",
        "contract_certificate_attachment_id",
        "contract_reject_reason",
        "contract_signature_places",
        "contract_generate_fingerprint",
        "contract_template",
        "contract_template_id",
        "candidate",
    }
)


def partition_server_managed_custom_fields(
    *,
    entry_type_slug: str,
    merged_custom_fields: Dict[str, Any],
    allowed_keys: set[str],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Pop server-managed keys absent from the published schema before validate.

    Workspaces that have not republished the HR profile after contract fields
    were added may still carry ``contract_generate_fingerprint`` (etc.) on
    entries. Preserve those values across public PATCH without failing field
    validation.
    """
    if entry_type_slug != "onboarding_form":
        return dict(merged_custom_fields or {}), {}

    editable = dict(merged_custom_fields or {})
    preserved: Dict[str, Any] = {}
    for key in list(editable.keys()):
        if key in ONBOARDING_SERVER_MANAGED_CUSTOM_FIELDS and key not in allowed_keys:
            preserved[key] = editable.pop(key)
    return editable, preserved
