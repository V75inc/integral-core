"""ID and parameter validation utilities."""

import re
from typing import Optional

from app.api.errors import BadRequestError

VALID_ENTITY_VISIBILITY = frozenset({"private", "workspace", "public"})

# Legacy alias: pre-rename storage used "organization" for the same
# workspace-wide grant. Accept it on input and normalize so existing
# clients and any unmigrated rows keep working.
_LEGACY_VISIBILITY_ALIASES = {"organization": "workspace"}


def _canonicalize_visibility(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    v = value.strip()
    if not v:
        return v
    return _LEGACY_VISIBILITY_ALIASES.get(v, v)


async def _is_organization_workspace(workspace_id: Optional[str]) -> bool:
    """Return True iff the workspace exists and is collaborative-kind.

    (Name kept for call-site stability; recognizes the canonical
    ``"collaborative"`` kind and the legacy ``"organization"`` spelling.)
    """
    if not workspace_id:
        return False
    from app.models.nodes import Workspace
    from app.services.workspace_kind import workspace_is_collaborative

    ws = await Workspace.get(workspace_id)
    return workspace_is_collaborative(ws)


async def effective_app_visibility(
    workspace_id: Optional[str],
    visibility: Optional[str],
) -> str:
    """Resolve app visibility for create.

    ``inherit`` (or empty) → ``workspace`` when the workspace is org-kind,
    else ``private``. Explicit ``workspace`` requires an org-kind workspace.
    """
    raw = _canonicalize_visibility(visibility) or None
    is_org_ws = await _is_organization_workspace(workspace_id)
    if raw in (None, "inherit"):
        return "workspace" if is_org_ws else "private"
    if raw not in VALID_ENTITY_VISIBILITY:
        raise BadRequestError(
            message="visibility must be private, workspace, public, or inherit",
        )
    if raw == "workspace" and not is_org_ws:
        raise BadRequestError(
            message="workspace visibility requires a workspace",
        )
    return raw


async def validate_app_visibility_workspace(
    visibility: str, workspace_id: Optional[str]
) -> None:
    """Raise BadRequestError if ``workspace`` visibility is paired with a non-org workspace."""
    if _canonicalize_visibility(
        visibility
    ) == "workspace" and not await _is_organization_workspace(workspace_id):
        raise BadRequestError(
            message="workspace visibility requires a workspace",
        )


def normalize_track_visibility_input(visibility: Optional[str]) -> Optional[str]:
    """Return None for inherit/omit; explicit value string otherwise."""
    if visibility is None:
        return None
    v = _canonicalize_visibility(visibility)
    if v in ("", "inherit", None):
        return None
    if v not in VALID_ENTITY_VISIBILITY:
        raise BadRequestError(
            message="visibility must be private, workspace, public, or inherit",
        )
    return v


async def validate_track_visibility_workspace(
    visibility: str, workspace_id: Optional[str]
) -> None:
    """Raise BadRequestError if ``workspace`` visibility is paired with a non-org workspace."""
    if _canonicalize_visibility(
        visibility
    ) == "workspace" and not await _is_organization_workspace(workspace_id):
        raise BadRequestError(
            message="workspace visibility requires a workspace",
        )


def normalize_track_accent_color(raw: Optional[str]) -> str:
    """Normalize track accent for storage: empty string clears to theme default on the client."""
    from app.api.validators_common import validate_hex_color

    try:
        return validate_hex_color(raw, allow_empty=True)
    except BadRequestError:
        raise BadRequestError(
            message="accent_color must be empty or a #RGB / #RRGGBB hex color",
        )


_ID_PATTERN = re.compile(r"^[a-zA-Z0-9._-]{10,128}$")


def validate_id(id_value: str, param_name: str = "id") -> str:
    """Validate an ID parameter. Raises BadRequestError if invalid."""
    if not id_value or not isinstance(id_value, str):
        raise BadRequestError(
            message=f"Invalid {param_name}: must be a non-empty string"
        )
    id_stripped = id_value.strip()
    if not id_stripped:
        raise BadRequestError(message=f"Invalid {param_name}: cannot be empty")
    if not _ID_PATTERN.match(id_stripped):
        raise BadRequestError(
            message=(
                f"Invalid {param_name}: must be alphanumeric with dots, hyphens, "
                "underscores (10-128 chars)"
            ),
        )
    return id_stripped
