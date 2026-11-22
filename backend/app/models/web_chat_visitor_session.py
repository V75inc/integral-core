"""Private scalar-keyed Web Chat visitor-session records (I-GRAPH-02)."""

from jvspatial.core import Object


class WebChatVisitorSession(Object):
    """Digest-only credential and exact authorized route/conversation snapshot."""

    credential_digest: str = ""
    workspace_id: str = ""
    provision_id: str = ""
    route_epoch: str = ""
    visitor_id: str = ""
    conversation_id: str = ""
    profile_key: str = ""
    published_version: int = 0
    expires_at: int = 0
    revoked: bool = False
