"""Private scalar-keyed Web Chat server-client sessions (I-GRAPH-02)."""

from jvspatial.core import Object


class WebChatClientSessionRecord(Object):
    """Exact grant and visitor snapshot; the primary key is the secret digest."""

    grant_id: str = ""
    workspace_id: str = ""
    connector_kind: str = ""
    provision_id: str = ""
    route_epoch: str = ""
    profile_key: str = ""
    publication_revision: str = ""
    grant_expires_at: int = 0
    visitor_id: str = ""
    conversation_id: str = ""
    issued_at: int = 0
    expires_at: int = 0
