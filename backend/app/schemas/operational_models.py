"""OperationalModel Pydantic schemas."""

from typing import Dict, List, Optional

from pydantic import BaseModel


class PackagePreviewItem(BaseModel):
    """Preview info for a single package (one entry in an archive or a single file)."""

    package_name: str
    package_description: Optional[str] = None
    entry_type_count: int
    view_count: int
    tag_count: int
    validation_errors: List[str]


class ImportPreviewResponse(BaseModel):
    """Response contract for POST /operational-models/import?preview=true.

    Supports both single-file (.yaml/.json) and multi-package archive (.zip)
    uploads. ``archive_type`` is ``"single"`` for direct manifest files and
    ``"archive"`` for ZIP uploads containing one or more ``operational-model.yaml``
    bundles.
    """

    packages: List[PackagePreviewItem]
    archive_type: str  # "single" | "archive"


class PatchSuggestion(BaseModel):
    op: str
    rationale: str
    patch_args: Dict[str, object]


class RecommendCustomizationsResponse(BaseModel):
    suggestions: List[PatchSuggestion]
    entry_count_analyzed: int
    profile_summary: Dict[str, object]
