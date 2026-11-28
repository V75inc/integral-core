"""Bodies Integral sends to jvvoice for one WhatsApp call."""

from __future__ import annotations

from pydantic import BaseModel, Field


class JvvoiceAcceptCallRequest(BaseModel):
    """Media fields for POST /api/calls/accept. No jvagent ids.

    ``session_id`` and ``user_id`` match jvvoice ``AcceptCallRequest``. The
    Integral thread, workspace, and principal stay on the one-call bearer,
    not as extra HTTP fields jvvoice would reject or ignore.
    """

    whatsapp_call_id: str
    phone_number_id: str
    whatsapp_api_key: str = Field(repr=False)
    sdp: str = Field(repr=False)
    sdp_type: str = "offer"
    caller_phone: str
    bridge: str | None = None
    relay_url: str | None = None
    relay_bearer: str | None = Field(default=None, repr=False)
    session_id: str | None = None
    user_id: str | None = None

    model_config = {"extra": "forbid"}


class JvvoiceDisconnectCallRequest(BaseModel):
    """POST /api/calls/disconnect. This path always hangs up as the business."""

    whatsapp_call_id: str
    user_initiated: bool = False
    whatsapp_api_key: str = Field(default="", repr=False)

    model_config = {"extra": "forbid"}
