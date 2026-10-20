"""Microsoft 365 mail OAuth callback shapes. Token values are not returned."""

from typing import Optional

from pydantic import BaseModel, Field


class MicrosoftMailCallbackResponse(BaseModel):
    connector_id: str
    connected: bool = True
    reauth_required: bool = False

    model_config = {"extra": "forbid"}


class MicrosoftMailCallbackRequest(BaseModel):
    code: str = Field(min_length=1)
    state: str = Field(min_length=1)
    connector_id: Optional[str] = None
    redirect_uri: Optional[str] = None

    model_config = {"extra": "forbid"}
