"""Durable scalar-keyed Web Chat receipts (I-GRAPH-02)."""

from jvspatial.core import Object


class WebChatTurnReceipt(Object):
    """A private turn reservation and its optional, write-once encrypted reply."""

    request_ciphertext: str = ""
    fence_digest: str = ""
    reply_ciphertext: str = ""
