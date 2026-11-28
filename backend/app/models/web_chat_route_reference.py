"""Private scalar-keyed host approval and opaque Web Chat lookup index."""

from jvspatial.core import Object


class WebChatRouteReference(Object):
    """One current approval or revision tombstone per exact provision."""

    workspace_id: str = ""
    provision_id: str = ""
    revision: int = 0
    key: str | None = None
    origin: str | None = None
    epoch: str | None = None
    profile_key: str | None = None
    published_version: int | None = None


class WebChatRouteKey(Object):
    """Exact back-reference to the owning current approval."""

    workspace_id: str = ""
    provision_id: str = ""
