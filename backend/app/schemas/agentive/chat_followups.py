"""User input staged for a later native chat turn."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.chat_entity_refs import EntityRef

FollowUpRequestId = Annotated[
    str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
]
FollowUpStatus = Literal["queued", "promoted", "steered", "dismissed"]


class ChatFollowUpRequest(BaseModel):
    """Content and stable request identity supplied by the composer."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    client_request_id: FollowUpRequestId
    after_work_item_id: str = Field(default="", max_length=255)
    text: str = Field(min_length=1, max_length=8000)
    entity_refs: list[EntityRef] = Field(default_factory=list, max_length=20)


class ChatFollowUpReceipt(BaseModel):
    client_request_id: FollowUpRequestId
    status: FollowUpStatus
