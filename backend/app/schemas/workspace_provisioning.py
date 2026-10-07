"""Safe partial-success details returned after workspace creation."""

from pydantic import BaseModel, Field


class WorkspaceProvisioningFailure(BaseModel):
    library_cp_id: str
    name: str
    error_code: str = "install_failed"


class WorkspaceProvisioningSummary(BaseModel):
    installed: int = 0
    awaiting_settings: int = 0
    skipped: int = 0
    failed: int = 0
    auto_dependencies: int = 0
    # Never expose raw exception text, install tokens or settings payloads.
    failures: list[WorkspaceProvisioningFailure] = Field(default_factory=list)
