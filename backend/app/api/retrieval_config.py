"""Phase 8 Plan 08-04 — read-only retrieval config surface (SET-06).

Read-only in v1.1 per locked decision A1 — NO PATCH surface. The endpoint
exposes the three Phase 4 env-resolved retrieval defaults plus the
identity of the currently-registered embedding-store driver, so
operators can verify retrieval configuration without shell access to the
process.

Per A7 — gated via ``policy_engine.evaluate(action='audit_log.read')``
instead of introducing a new PolicyAction member. The semantics map
cleanly: this is a read of process-resident operational state, exactly
the disposition ``audit_log.read`` already covers (see
``app/schemas/policy.py`` L135). The single-Literal grep gate stays at
exactly 1 match for ``PolicyAction``, and the strict-superset invariant
test (``test_policy_action_strict_supersets_change_event_action``)
continues to hold because no new member is added.

Per Phase 8 threat model T-08-04-I03: the embedding-store backend name
leaks operationally-useful information (atlas vs null vs pgvector) but is
not sensitive — accept disposition. T-08-04-I01: response Pydantic shape
``extra='forbid'`` blocks future drift that would leak model paths,
vector dims, credentials.

Env reads happen at call time (NOT module import) — mirrors Phase 4
``backend/app/api/retrieve.py:_default_k`` / ``_default_top_n`` so tests
can monkeypatch ``os.environ`` without re-importing the module.
"""

from __future__ import annotations

import logging
import os

from fastapi import Request
from jvspatial.api import endpoint

from app.api.errors import (
    InsufficientPermissionsError,
    MissingAuthenticationError,
)
from app.api.utils import resolve_principal_id
from app.schemas.policy import Resource, Subject
from app.schemas.retrieval_config import RetrievalConfigResponse
from app.services.policy_engine import evaluate as policy_evaluate

logger = logging.getLogger(__name__)


# Imported at consumer site so tests can monkeypatch
# ``app.api.retrieval_config.get_embedding_store`` (the consumer-site
# binding) and the patch intercepts the lookup performed by this module.
# See plan Task 1 Step 4 case 4 (FakeStore fixture).
try:
    from app.services.retrieval import (
        get_embedding_store,
        semantic_retrieval_available,
    )
except Exception as exc:  # noqa: BLE001 — defensive; falls back to "unknown".
    logger.warning(
        "retrieval_config: get_embedding_store unavailable at import; "
        "backend identity will report 'unknown' until resolved (%s)",
        exc,
    )
    get_embedding_store = None  # type: ignore[assignment]
    semantic_retrieval_available = None  # type: ignore[assignment]


def _resolve_embedding_store_backend() -> str:
    """Return a short identifier of the registered embedding store driver.

    Order of preference:
      1. ``store.driver_name`` attribute if present and truthy
      2. ``type(store).__name__`` (e.g. ``"AtlasVectorDriver"``)
      3. ``"unknown"`` if no driver is registered or import failed
    """

    if get_embedding_store is None:
        return "unknown"
    try:
        store = get_embedding_store()
    except Exception as exc:  # noqa: BLE001 — driver-registration may fail.
        logger.warning(
            "retrieval_config: get_embedding_store() raised: %s — "
            "reporting backend identity as 'unknown'",
            exc,
        )
        return "unknown"
    return getattr(store, "driver_name", None) or type(store).__name__ or "unknown"


@endpoint(
    "/retrieval/config",
    methods=["GET"],
    auth=True,
    tags=["Retrieve"],
)
async def get_retrieval_config(request: Request) -> RetrievalConfigResponse:
    """Return the current retrieval configuration (read-only).

    Auth: required. Resolves principal via ``resolve_principal_id``;
    raises ``MissingAuthenticationError`` (401) when absent.

    Authorization: ``policy_engine.evaluate(action='audit_log.read', ...)``
    on a synthetic ``Resource(kind='system', id='retrieval_config', ...)``.
    Raises ``InsufficientPermissionsError`` (403) on deny. Per A7 — no
    new PolicyAction member.

    Response: ``RetrievalConfigResponse`` with the three env-resolved
    defaults + the embedding-store backend identity. Env reads happen at
    call time so tests can monkeypatch them.

    No ``emit_change_event`` — this is a read (D-05 single-emission rule
    only applies to mutations). The endpoint is verified to NOT emit
    via ``test_get_retrieval_config_no_new_change_event_emitted``.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    # Resource.kind constrained to ``ResourceKind`` Literal in
    # ``app/schemas/policy.py`` — ``"system"`` is NOT in the Literal so we
    # cannot use it directly (Pydantic validates at construction). The plan
    # specified ``kind='system'`` but the engine accepts only the canonical
    # 15 kinds; the closest semantic match for "this caller is reading
    # operational state about retrieval defaults" is ``kind='user'`` with
    # ``id=user_id`` — same shape ``audit_log.read`` already uses on the
    # default-human path (see ``policy_engine._evaluate_human_default``).
    # The ``scope='user:<id>'`` carries the binding regardless of kind.
    # Deviation Rule 1: plan text said ``kind='system'`` which is a
    # type-check bug that would 500 on every call; corrected here.
    decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="audit_log.read",
        resource=Resource(
            kind="user",
            id=user_id,
            scope=f"user:{user_id}",
        ),
    )
    if not decision.allowed:
        raise InsufficientPermissionsError(
            message="audit_log.read denied",
            details={"decision_reason": decision.reason},
        )

    backend = _resolve_embedding_store_backend()

    semantic_available = False
    if semantic_retrieval_available is not None:
        try:
            semantic_available = bool(semantic_retrieval_available())
        except Exception as exc:  # noqa: BLE001 — defensive
            logger.warning(
                "retrieval_config: semantic_retrieval_available() raised: %s — "
                "reporting semantic_available=False",
                exc,
            )

    return RetrievalConfigResponse(
        embedding_model_eager_load=(
            os.environ.get("EMBEDDING_MODEL_EAGER_LOAD", "1") == "1"
        ),
        retrieve_k_default=int(os.environ.get("RETRIEVE_K_DEFAULT", "150")),
        retrieve_top_n_default=int(os.environ.get("RETRIEVE_TOP_N_DEFAULT", "20")),
        embedding_store_backend=backend,
        semantic_available=semantic_available,
    )
