"""OperationContext typing surface for app operation handlers."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, Tuple, runtime_checkable


@runtime_checkable
class OperationContext(Protocol):
    """Context injected into app operation handlers by Core."""

    user_id: str
    workspace_id: str
    scope: str
    bundle_slug: str
    app_id: str
    operation_key: str
    idempotency_key: Optional[str]
    correlation_id: Optional[str]

    async def current_user_profile(self) -> Dict[str, str]:
        """Return the active principal's profile ID and bounded display name."""
        ...

    async def get_entry(self, entry_id: str) -> Any: ...

    async def find_entries_in_track_type(
        self, track_type: str, entry_type: Optional[str] = None
    ) -> list[Any]: ...

    async def find_track_id_by_title(self, track_type: str) -> Optional[str]: ...

    async def search_entries(
        self,
        *,
        query: str,
        track_type: str,
        entry_type: Optional[str] = None,
        eligible_entry_ids: Optional[List[str]] = None,
        mode: str = "hybrid",
        limit: int = 150,
        context_tokens: int = 1200,
        attachment_kind: Optional[str] = None,
        attachment_filename: str = "",
    ) -> Dict[str, Any]:
        """App-scoped passage retrieval for a declared App query.

        ``track_type`` is an exact stable manifest/relation key or Track ID. Only
        Tracks without stable keys use an unambiguous legacy title fallback.
        ``entry_type`` matches an exact manifest key; omit it for keyless schemas.
        ``eligible_entry_ids`` narrows candidate selection to a structured,
        policy-authorized set: None discovers a bounded candidate window;
        [] searches nothing. At most 500 IDs can be supplied. Governed Track/type
        selectors narrow index candidates before ranking; this is not a scan of
        the first 500 matching Entries. The bounded candidate window is not an
        exhaustive collection. Unrelated Tracks/types do not consume its budget.
        ``attachment_kind`` selects file passages (any/image/document/audio/video);
        ``attachment_filename`` is a case-insensitive literal substring. A blank
        query is permitted with a file filter. File evidence includes identity,
        content origin and extraction/section provenance when available.
        Returns passages, source links, requested/effective mode, cap status
        and warnings. Index coverage remains unknown unless measured; empty
        results never establish absence. It never widens workspace/App scope.
        """
        ...

    async def create_entry(
        self,
        *,
        track_id: str,
        entry_type_key: str,
        title: str,
        custom_fields: Optional[Dict[str, Any]] = None,
        body: str = "",
    ) -> Optional[Any]: ...

    async def update_entry_fields(
        self,
        entry_id: str,
        custom_fields: Dict[str, Any],
        *,
        title: Optional[str] = None,
        expected_record_revision: Optional[int] = None,
    ) -> bool:
        """Apply a validated update; False includes a stale record revision."""
        ...

    async def conditional_update_entry_fields(
        self,
        entry_id: str,
        *,
        state_field: str,
        expected_state: Optional[str],
        updates: Dict[str, Any],
    ) -> Tuple[bool, Optional[str]]: ...

    async def emit_audit(self, action: str, details: Dict[str, Any]) -> None: ...

    async def notify_once(
        self, *, dedupe_key: str, title: str, body: str
    ) -> Optional[str]: ...

    async def find_entries(self, query: dict) -> list: ...

    async def get(self, object_ref: dict) -> Any:
        """Read an authorized entry or retained native tool source observation.

        For a source, pass the tool's server-generated ``_observation_ref``
        (kind ``tool_observation``, id and session_id). Core returns bounded
        ``source`` text/metadata plus its output fingerprint, or None when
        unavailable. Content remains untrusted; Apps own claim validation.
        This does not grant access to another actor's transcript or replay a
        tool. Requires a Core distribution supporting source observations.
        """
        ...

    async def query(self, query_spec: dict) -> dict: ...

    async def invoke(self, operation_key: str, payload: dict | None = None) -> dict: ...

    async def enqueue_workspace_transactional_email(
        self,
        *,
        to: str,
        subject: str,
        text: str,
        html: str,
        source_kind: str,
        idempotency_key: str,
        reply_to: Optional[str] = None,
        source_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Queue authorized workspace email; returns message_id and status=queued.

        Requires authorization.effects: [email.enqueue], a current approved
        command permit and its transaction. Read operations cannot enqueue.
        A queued result is not provider acceptance or confirmed delivery. Reuse
        the same key and exact content for retries of one logical message.
        """
        ...
