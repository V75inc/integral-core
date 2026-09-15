"""Pydantic wire shapes for App install / settings / uninstall lifecycle.

Phase 10 Plan 10-05 (APP-LIFECYCLE-01 / APP-SETTINGS-01 / APP-SEEDS-01).

These types are the API/engine boundary for the install lifecycle endpoints
in ``backend/app/api/apps.py``. The persisted shape is the ``App`` Node
plus its attached ContentProfile manifest (canonical source of truth).

Conventions per CLAUDE.md § jvspatial Object-Spatial Contract:
- Request/response bodies MUST live in ``backend/app/schemas/``.
- ``extra: "forbid"`` on request bodies to reject silent field injection.
- Response shapes match what handlers actually emit; the @endpoint
  framework serializes the dict directly.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Install request / response
# ---------------------------------------------------------------------------


class InstallAppRequest(BaseModel):
    """Body for ``POST /api/workspaces/{workspace_id}/apps`` install endpoint.

    The library_content_profile_id identifies the library package being
    installed. ``version`` is forward-compat — a future Plan 10-06+ may
    pin a specific version of a versioned library package.
    """

    library_content_profile_id: str
    version: Optional[str] = None
    # If the library manifest declares a non-empty settings_schema AND the
    # caller already has the settings the user wants to apply, they may
    # pass them here to skip the awaiting_settings pause. Server-side
    # jsonschema validation still applies.
    settings: Optional[Dict[str, Any]] = None
    # When False, manifest ``app.seeds[]`` (and CRM wiki handbook) are
    # skipped. Defaults True for backward compatibility. Stored on the
    # App's metadata when install pauses at awaiting_settings.
    include_seed_data: bool = True

    model_config = {"extra": "forbid"}


class InstallAppPausedResponse(BaseModel):
    """Returned when install pauses at step 9 (settings_schema present).

    The caller's UI surfaces the settings_schema as a form (frontend's
    ``AppSettingsForm.tsx``), collects values from the user, and POSTs
    them back with the install_token to
    ``POST /api/apps/{app_id}/install/settings``.
    """

    status: Literal["awaiting_settings"] = "awaiting_settings"
    app_id: str
    install_token: str
    settings_schema: Dict[str, Any]


class InstallAppCompletedResponse(BaseModel):
    """Returned when install completes (active state)."""

    status: Literal["active"] = "active"
    app_id: str
    installed_at: str
    version: Optional[str] = None


# ---------------------------------------------------------------------------
# Finalize install (resume from awaiting_settings)
# ---------------------------------------------------------------------------


class FinalizeInstallRequest(BaseModel):
    """Body for ``POST /api/apps/{app_id}/install/settings``.

    The install_token authorizes this specific resume — verified server-
    side against ``App.lifecycle_state == "awaiting_settings"`` (token
    replay protection per T-10-05-02).
    """

    install_token: str
    settings: Dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}


# ---------------------------------------------------------------------------
# Settings read / write (post-install)
# ---------------------------------------------------------------------------


class AppSettingsResponse(BaseModel):
    """Returned by ``GET /api/apps/{app_id}/settings``."""

    app_id: str
    settings: Dict[str, Any]
    settings_schema: Dict[str, Any]
    lifecycle_state: str


class UpdateAppSettingsRequest(BaseModel):
    """Body for ``PATCH /api/apps/{app_id}/settings`` (post-install edits).

    Server-side validation runs against the App's persisted
    ``settings_schema`` mirror. Caller-supplied values that violate the
    schema produce 422.
    """

    settings: Dict[str, Any]

    model_config = {"extra": "forbid"}


# ---------------------------------------------------------------------------
# Update-from-library
# ---------------------------------------------------------------------------


class UpdateFromLibraryRequest(BaseModel):
    """Body for ``POST /api/apps/{app_id}/update-from-library``.

    ``version`` is forward-compat (Plan 10-06+). For v1, omitting means
    "re-merge from the currently-pinned library_content_profile_id".
    """

    version: Optional[str] = None

    model_config = {"extra": "forbid"}


class UpdateFromLibraryResponse(BaseModel):
    """Returned by update-from-library — summarizes the diff applied."""

    app_id: str
    added_sections: List[str] = Field(default_factory=list)
    version_before: Optional[str] = None
    version_after: Optional[str] = None


# ---------------------------------------------------------------------------
# Pause / Resume
# ---------------------------------------------------------------------------


class PauseAppResponse(BaseModel):
    app_id: str
    status: Literal["paused"] = "paused"


class ResumeAppResponse(BaseModel):
    app_id: str
    status: Literal["active"] = "active"


# ---------------------------------------------------------------------------
# Uninstall
# ---------------------------------------------------------------------------


class UninstallAppResponse(BaseModel):
    """Returned by ``DELETE /api/apps/{app_id}?force=true|false``.

    ``archived`` distinguishes the soft (default) vs hard (force=True
    OR archive=False explicit purge) path. Force path returns
    ``status: "force_uninstalled"`` and ``archived: false``.
    """

    app_id: str
    status: Literal["uninstalled", "force_uninstalled"]
    archived: bool
