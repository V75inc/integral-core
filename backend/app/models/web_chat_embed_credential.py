"""Private Object for a short-lived, single-use embed credential exchange."""

from jvspatial.core import Object


class WebChatEmbedCredential(Object):
    """Scope-bound seed/session digest, never a raw secret or HTTP cookie."""

    scope_digest: str = ""
    credential_digest: str = ""
    kind: str = ""
    expires_at: int = 0
    visitor_id: str = ""
    conversation_id: str = ""
