"""Owner-controlled automatic file analysis, separate from embedding models."""

from pydantic import BaseModel, ConfigDict, Field


class AttachmentAnalysisChoice(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool = True
    profile_id: str = Field(default="", max_length=200)


class AttachmentAnalysisWrite(AttachmentAnalysisChoice):
    revision: int = Field(ge=0)
