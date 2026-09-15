"""Avatar upload / retrieval wire shapes — Phase 9 Plan 09-01 (AVT-01).

Pydantic boundary models for ``POST /api/users/{user_id}/avatar`` and the
companion variant-retrieval endpoint. Mirrors the file-handling idiom from
``app/schemas/`` — request/response shapes live here, never inline in
``api/users.py`` (jvspatial Object-Spatial Contract — schemas/ only).
"""

from typing import List

from pydantic import BaseModel


class AvatarVariant(BaseModel):
    """One pixel-size variant produced by the avatar resize pipeline."""

    size: int
    attachment_id: str


class AvatarUploadResponse(BaseModel):
    """Response envelope for ``POST /api/users/{user_id}/avatar``.

    ``avatar_attachment_id`` is the canonical 128-pixel variant id (mirrored
    on the User node). ``variants`` enumerates every resized PNG attachment
    so callers can prefetch the size they actually render. ``updated_at`` is
    the User node's new ``updated_at`` ISO timestamp — frontends use it as
    a cache-bust ``?v=`` query param on the GET endpoint.
    """

    avatar_attachment_id: str
    variants: List[AvatarVariant]
    updated_at: str


class AvatarValidationError(Exception):
    """Raised when an avatar upload fails MIME / Pillow verification.

    Translated into a 400 ``ValidationError`` (a JVSpatialAPIException
    subclass) at the ``api/users.py`` boundary. NOT a JVSpatialAPIException
    itself — this exception is the service-layer signal; the boundary owns
    the HTTP envelope.
    """
