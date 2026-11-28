"""Private scalar-keyed Web Chat server-client grants (I-GRAPH-02)."""

from jvspatial.core import Object


class WebChatClientGrantRecord(Object):
    """Digest-only authentication and exact immutable assignment snapshot."""

    credential_digest: str = ""
    workspace_id: str = ""
    connector_kind: str = ""
    provision_id: str = ""
    route_epoch: str = ""
    profile_key: str = ""
    publication_revision: str = ""
    expires_at: int = 0
    revoked: bool = False


class WebChatClientGrantDigest(Object):
    """Immutable primary-key index from secret digest to opaque grant ID."""

    grant_id: str = ""
