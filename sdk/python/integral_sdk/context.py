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
    ) -> Dict[str, Any]:
        """App-scoped passage retrieval for a declared App query.

        ``eligible_entry_ids`` narrows candidate selection to a structured,
        policy-authorized set. Returns passages plus requested/effective mode,
        coverage, cap status and warnings; it never widens the caller's
        workspace, App or Track scope.
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
        expected_state: str,
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
