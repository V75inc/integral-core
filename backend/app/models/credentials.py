"""Per-user model credential storage (I-GRAPH-02 Object, not Node)."""

from __future__ import annotations

from typing import Optional

from jvspatial.core import Object
from jvspatial.core.annotations import attribute, compound_index


@compound_index(
    [("user_id", 1)],
    name="user_model_cred_user_id",
    unique=True,
    partial_filter_expression={
        "entity": "UserModelCredential",
        "context.user_id": {"$gt": ""},
    },
)
class UserModelCredential(Object):
    """Encrypted BYOK model provider credential for one Integral user."""

    user_id: str = attribute(default="", indexed=True)
    provider: str = attribute(default="", indexed=True)  # default slot
    model: str = attribute(default="")
    light_provider: str = attribute(default="")
    light_model: str = attribute(default="")
    heavy_provider: str = attribute(default="")
    heavy_model: str = attribute(default="")
    vision_provider: str = attribute(default="")
    vision_model: str = attribute(default="")
    # Speech-to-text (voice input) slot. Opt-in: empty ``speech_model`` = off.
    speech_provider: str = attribute(default="")
    speech_model: str = attribute(default="")
    api_key_enc: str = attribute(default="")
    key_fingerprint: str = attribute(default="")
    light_api_key_enc: str = attribute(default="")
    light_key_fingerprint: str = attribute(default="")
    heavy_api_key_enc: str = attribute(default="")
    heavy_key_fingerprint: str = attribute(default="")
    vision_api_key_enc: str = attribute(default="")
    vision_key_fingerprint: str = attribute(default="")
    speech_api_key_enc: str = attribute(default="")
    speech_key_fingerprint: str = attribute(default="")
    is_active: bool = attribute(default=True)
    validated_at: Optional[str] = attribute(default=None)
    last_used_at: Optional[str] = attribute(default=None)
    created_at: Optional[str] = attribute(default=None)
    updated_at: Optional[str] = attribute(default=None)
