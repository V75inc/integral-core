"""Scalar durable cleanup intent, independent of signature graph lifetime."""

from jvspatial.core import Object

from app.models.indexing import attribute


class UserSignatureCleanupFact(Object):
    """Retryable binary retirement; never authorizes a signature read or write."""

    user_id: str = ""
    attachment_id: str = ""
    storage_key: str = ""
    status: str = attribute(default="pending", indexed=True)
    created_at: str = ""
    delivered_at: str = ""
