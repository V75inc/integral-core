"""Phase 32 — batch install endpoint request/response shapes."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class BatchInstallItem(BaseModel):
    """One bundle to install in a batch.

    Only ``library_cp_id`` is required. ``name`` and ``description``
    default to the bundle manifest's ``package.name`` and
    ``package.description`` per Phase 32. ``settings`` is forwarded to
    the install transaction when the bundle declares a settings schema.
    """

    library_cp_id: str = Field(..., min_length=1)
    name: Optional[str] = None
    description: Optional[str] = None
    settings: Optional[Dict[str, Any]] = None
    include_seed_data: bool = True


class BatchInstallRequest(BaseModel):
    items: List[BatchInstallItem] = Field(..., min_length=1)


class BatchInstallEntry(BaseModel):
    library_cp_id: str
    app_id: Optional[str] = None
    name: Optional[str] = None
    status: Optional[str] = None
    reason: Optional[str] = None
    error: Optional[str] = None
    error_code: Optional[str] = None
    install_token: Optional[str] = None
    settings_schema: Optional[Dict[str, Any]] = None


class BatchInstallResponse(BaseModel):
    installed: List[BatchInstallEntry] = Field(default_factory=list)
    skipped: List[BatchInstallEntry] = Field(default_factory=list)
    failed: List[BatchInstallEntry] = Field(default_factory=list)
    order: List[str] = Field(default_factory=list)
    #: Count of hard dependencies auto-pulled into the batch (not explicitly
    #: requested by the caller) when ``resolve_dependencies`` is enabled.
    auto_dependencies: int = 0
