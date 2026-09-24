"""Storage facade for Integral attachment binaries using jvspatial file storage."""

from __future__ import annotations

import inspect
import logging
import re
from pathlib import Path
from typing import Any, Dict, Optional

from jvspatial.env import env, resolve_file_storage_root
from jvspatial.storage import FileStorageInterface, create_storage

logger = logging.getLogger(__name__)

_SAFE_CHARS_RE = re.compile(r"[^A-Za-z0-9._-]+")
DEFAULT_SIGNED_URL_TTL_SECONDS = 5 * 60

# Keep in sync with attachment_upload_shared.FILENAME_EXT_TO_MIME — duplicated
# here so save_attachment can hint jvspatial without importing that module
# (circular: upload_shared → api errors → … → storage).
_EXT_TO_MIME = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".md": "text/markdown",
    ".json": "application/json",
    ".zip": "application/zip",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


def _sanitize_filename(filename: str) -> str:
    raw = (filename or "").strip() or "attachment"
    name = Path(raw).name
    safe = _SAFE_CHARS_RE.sub("_", name).strip("._")
    return safe or "attachment"


def _mime_from_filename(filename: str) -> str:
    ext = Path(filename or "").suffix.lower()
    return _EXT_TO_MIME.get(ext, "")


class AttachmentStorageService:
    """Thin facade over jvspatial storage interfaces for attachment blobs."""

    def __init__(self, storage: Optional[FileStorageInterface] = None):
        self._storage = storage or self._create_default_storage()

    @staticmethod
    def _create_default_storage() -> FileStorageInterface:
        provider = str(env("JVSPATIAL_FILE_STORAGE_PROVIDER", default="local"))
        if provider == "s3":
            return create_storage(
                "s3",
                bucket_name=env("JVSPATIAL_S3_BUCKET_NAME"),
                region_name=env("JVSPATIAL_S3_REGION"),
                access_key_id=env("JVSPATIAL_S3_ACCESS_KEY"),
                secret_access_key=env("JVSPATIAL_S3_SECRET_KEY"),
                endpoint_url=env("JVSPATIAL_S3_ENDPOINT_URL"),
            )
        root_dir = resolve_file_storage_root(
            env("JVSPATIAL_FILES_ROOT_PATH", default=None)
        )
        base_url = env("JVSPATIAL_FILE_STORAGE_BASE_URL", default=None)
        max_size_mb = env("JVSPATIAL_FILE_STORAGE_MAX_SIZE_MB", default=100, parse=int)
        return create_storage(
            "local",
            root_dir=root_dir,
            base_url=base_url,
            max_size_mb=max_size_mb,
            create_root=True,
        )

    @staticmethod
    def build_storage_key(entry_id: str, attachment_id: str, filename: str) -> str:
        """Return the object key for an attachment blob under its entry."""
        safe_name = _sanitize_filename(filename)
        return f"attachments/{entry_id}/{attachment_id}/{safe_name}"

    @staticmethod
    def build_sibling_key(
        entry_id: str, attachment_id: str, sibling_filename: str
    ) -> str:
        """Return the object key for a derived sibling (thumb, preview).

        Sibling files share the attachment directory so a single delete
        of the attachment folder cleans up everything we generated.

        Unlike user-uploaded filenames, sibling names are server-
        controlled and may legitimately start with ``_`` to sort
        separately from user content in directory listings. The
        general-purpose ``_sanitize_filename`` strips leading ``_`` as
        a hardening measure; we restore it here so the persisted key
        round-trips literally.
        """
        base = _sanitize_filename(sibling_filename)
        if sibling_filename.startswith("_") and not base.startswith("_"):
            base = "_" + base
        return f"attachments/{entry_id}/{attachment_id}/{base}"

    async def save_attachment(
        self,
        *,
        entry_id: str,
        attachment_id: str,
        filename: str,
        content: bytes,
        metadata: Optional[Dict[str, str]] = None,
        mime_type: Optional[str] = None,
    ) -> Dict[str, object]:
        """Persist binary content and return storage result (e.g. key, URL).

        Always passes a MIME hint to jvspatial via ``metadata["mime"]``.
        Without it, local storage sniffs independently — Office docs often
        land as ``application/octet-stream`` and get rejected even when
        Integral already resolved an allow-listed type from the filename.
        """
        key = self.build_storage_key(entry_id, attachment_id, filename)
        meta: Dict[str, str] = dict(metadata or {})
        hint = (mime_type or meta.get("mime") or "").strip()
        if not hint:
            hint = _mime_from_filename(filename)
        if hint:
            meta["mime"] = hint
        return await self._storage.save_file(key, content, metadata=meta)

    async def delete_attachment(self, storage_key: str) -> bool:
        """Remove the blob for ``storage_key``; no-op if key is empty."""
        if not storage_key:
            return False
        return await self._storage.delete_file(storage_key)

    async def read_attachment(self, storage_key: str) -> Optional[bytes]:
        """Load file bytes for ``storage_key``, or None if missing/empty key."""
        if not storage_key:
            return None
        return await self._storage.get_file(storage_key)

    async def get_metadata(self, storage_key: str) -> Optional[Dict[str, object]]:
        """Return stored metadata for ``storage_key``, or None if unavailable."""
        if not storage_key:
            return None
        return await self._storage.get_metadata(storage_key)

    async def get_signed_url(
        self,
        storage_key: str,
        *,
        expires_in: int = DEFAULT_SIGNED_URL_TTL_SECONDS,
        download_filename: Optional[str] = None,
    ) -> Optional[str]:
        """Best-effort presigned URL for browser-direct download.

        Returns the signed URL when the underlying jvspatial provider
        supports presigning (S3 and S3-compatible backends do). Returns
        ``None`` for the local-disk provider — the caller should fall
        back to the auth-gated streaming endpoint in that case.

        Implementation uses duck typing so this works whether jvspatial
        names the method ``generate_presigned_url``, ``get_signed_url``,
        ``get_url``, or exposes a public URL via a property. Each
        candidate is tried in order; the first one to return a non-empty
        string wins. Exceptions are swallowed and logged so a misbehaving
        provider never blocks downloads — callers always have the
        ``/api/attachments/{id}/download`` fallback.
        """
        if not storage_key:
            return None

        candidates = (
            "generate_presigned_url",
            "get_signed_url",
            "presigned_url",
            "get_url",
            "url_for",
        )
        for name in candidates:
            method = getattr(self._storage, name, None)
            if method is None:
                continue
            try:
                # Some providers want only the key, others accept TTL +
                # a response-content-disposition override. We probe the
                # call signature so we never pass an arg the impl
                # doesn't accept.
                kwargs: Dict[str, Any] = {}
                try:
                    sig = inspect.signature(method)
                    params = sig.parameters
                    if "expires_in" in params:
                        kwargs["expires_in"] = expires_in
                    elif "expiration" in params:
                        kwargs["expiration"] = expires_in
                    elif "ttl" in params:
                        kwargs["ttl"] = expires_in
                    if download_filename and "download_filename" in params:
                        kwargs["download_filename"] = download_filename
                except (TypeError, ValueError):
                    pass

                result = method(storage_key, **kwargs)
                if inspect.isawaitable(result):
                    result = await result
                if isinstance(result, str) and result.strip():
                    return result
            except Exception as e:  # noqa: BLE001
                logger.debug(
                    "storage.%s failed for %s: %s",
                    name,
                    storage_key,
                    e,
                )
                continue
        return None


_ATTACHMENT_STORAGE_SERVICE: Optional[AttachmentStorageService] = None


def get_attachment_storage_service() -> AttachmentStorageService:
    """Return the process-wide singleton attachment storage facade."""
    global _ATTACHMENT_STORAGE_SERVICE
    if _ATTACHMENT_STORAGE_SERVICE is None:
        _ATTACHMENT_STORAGE_SERVICE = AttachmentStorageService()
    return _ATTACHMENT_STORAGE_SERVICE
