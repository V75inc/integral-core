"""Per-staging-token provenance binding for ChangeEvent emission.

When a blessed staged change executes, ``bless_and_execute`` (or the
text-approve path) binds a :class:`MutationProvenance` on this ContextVar
for the duration of ``staging_executors.dispatch``. ``emit_change_event``
merges the binding into ``details`` so rollback can later query
``details.staging_token``.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

_current: ContextVar[Optional["MutationProvenance"]] = ContextVar(
    "integral_mutation_provenance", default=None
)


@dataclass(frozen=True)
class MutationProvenance:
    staging_token: str
    thread_id: Optional[str] = None
    message_id: Optional[str] = None


def set_mutation_provenance(provenance: MutationProvenance) -> object:
    """Bind provenance for the current async context. Returns a reset token."""
    return _current.set(provenance)


def reset_mutation_provenance(token: object) -> None:
    """Clear provenance bound by :func:`set_mutation_provenance`."""
    _current.reset(token)  # type: ignore[arg-type]


def get_mutation_provenance() -> Optional[MutationProvenance]:
    """Return the active provenance binding, if any."""
    return _current.get()


def provenance_details() -> Dict[str, Any]:
    """Structured metadata merged into ``emit_change_event(..., details=...)``."""
    prov = get_mutation_provenance()
    if prov is None:
        return {}
    out: Dict[str, Any] = {"staging_token": prov.staging_token}
    if prov.thread_id:
        out["thread_id"] = prov.thread_id
    if prov.message_id:
        out["message_id"] = prov.message_id
    return out


def bind_mutation_provenance(
    *,
    staging_token: str,
    thread_id: Optional[str] = None,
    message_id: Optional[str] = None,
) -> Tuple[MutationProvenance, object]:
    """Convenience: build + bind. Returns (provenance, reset_token)."""
    prov = MutationProvenance(
        staging_token=staging_token,
        thread_id=thread_id,
        message_id=message_id,
    )
    return prov, set_mutation_provenance(prov)
