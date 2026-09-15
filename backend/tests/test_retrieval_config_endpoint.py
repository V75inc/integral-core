"""Phase 8 Plan 08-04 Task 1 — pytest coverage for GET /api/retrieval/config.

Covers (per plan Task 1 Step 4):
  1. Unauthenticated path → 401 (MissingAuthenticationError) — canonical
     envelope.
  2. Env values read at call time (not module import). Monkeypatch
     ``RETRIEVE_K_DEFAULT="42"`` then GET → ``retrieve_k_default == 42``.
  3. Defaults when env var absent — ``RETRIEVE_K_DEFAULT`` defaults to 150.
  4. Embedding-store backend identity reflects the registered driver —
     patched to a ``FakeStore`` class, response reports ``"FakeStore"``.
  5. Response shape ``extra='forbid'`` — constructing
     ``RetrievalConfigResponse(extra_field="x", ...)`` raises ValidationError.
  6. PolicyAction Literal does NOT introduce ``retrieval.config_read`` —
     this endpoint reuses ``audit_log.read`` per A7 (zero new members).
  7. No ChangeEvent emitted by the endpoint — this is a read, not a
     mutation; the D-05 single-emission rule says reads MUST NOT emit.

W4 cross-plan note: This file consumes ``authenticated_client`` and
``client`` from ``conftest.py`` — both pre-existing fixtures.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# Test fixture — FakeStore class used in case 4 (Plan Task 1 Step 4 case 4).
# Minimal: no methods needed because the endpoint only reads driver_name /
# type name. Defined at module top so the monkeypatch target is stable.
# ---------------------------------------------------------------------------


class FakeStore:
    """No-op embedding store stand-in. Endpoint reports class name only."""

    pass


# ---------------------------------------------------------------------------
# Case 1: auth gate (MissingAuthenticationError → 401)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_retrieval_config_requires_auth(client: AsyncClient) -> None:
    """Unauthenticated GET → 401 with canonical envelope.

    The bare ``client`` fixture is unauthenticated. The endpoint
    declares ``auth=True``; ``MissingAuthenticationError`` surfaces as
    401 via the canonical jvspatial envelope handler.

    Per the existing ``test_audit_log_unauthenticated_path_does_not_500``
    precedent (test_audit_log_query_gaps.py), TestAuthBypassMiddleware
    behaviour means ``client`` may surface as a 200-with-default-principal
    OR a 401 — both are valid; we assert "not 500" and exercise the direct
    handler invocation for the denied axis below.
    """
    resp = await client.get("/api/retrieval/config")
    assert resp.status_code in (200, 401), resp.text


@pytest.mark.asyncio
async def test_get_retrieval_config_missing_principal_raises_401() -> None:
    """No resolved principal → MissingAuthenticationError → 401.

    Direct-function invocation guarantees the denied-axis assertion
    runs regardless of TestAuthBypassMiddleware. Mirrors the
    ``test_audit_log_missing_principal_raises_401`` precedent.
    """
    from app.api.errors import MissingAuthenticationError
    from app.api.retrieval_config import get_retrieval_config

    request = MagicMock()
    request.state.user = None
    with patch(
        "app.api.retrieval_config.resolve_principal_id",
        return_value=None,
    ):
        with pytest.raises(MissingAuthenticationError):
            await get_retrieval_config(request)


# ---------------------------------------------------------------------------
# Case 2 + 3: env-readback at call time
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_retrieval_config_returns_envs_at_call_time(
    authenticated_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setting ``RETRIEVE_K_DEFAULT`` before the call is reflected in response.

    Confirms the endpoint reads env at handler invocation (not at module
    import). If the read were import-time, the patched value would be
    invisible to the response.
    """
    monkeypatch.setenv("RETRIEVE_K_DEFAULT", "42")
    monkeypatch.setenv("RETRIEVE_TOP_N_DEFAULT", "7")

    resp = await authenticated_client.get("/api/retrieval/config")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["retrieve_k_default"] == 42
    assert body["retrieve_top_n_default"] == 7


@pytest.mark.asyncio
async def test_get_retrieval_config_defaults_when_env_missing(
    authenticated_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing env vars fall back to the documented defaults.

    Defaults: ``RETRIEVE_K_DEFAULT=150``, ``RETRIEVE_TOP_N_DEFAULT=20``,
    ``EMBEDDING_MODEL_EAGER_LOAD=1`` (→ True).
    """
    monkeypatch.delenv("RETRIEVE_K_DEFAULT", raising=False)
    monkeypatch.delenv("RETRIEVE_TOP_N_DEFAULT", raising=False)
    monkeypatch.delenv("EMBEDDING_MODEL_EAGER_LOAD", raising=False)

    resp = await authenticated_client.get("/api/retrieval/config")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["retrieve_k_default"] == 150
    assert body["retrieve_top_n_default"] == 20
    # ``EMBEDDING_MODEL_EAGER_LOAD`` default is the string ``"1"`` which
    # coerces to True via ``== "1"`` per the endpoint.
    assert body["embedding_model_eager_load"] is True


# ---------------------------------------------------------------------------
# Case 4: embedding-store backend identity reflects the registered driver
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_retrieval_config_includes_embedding_store_backend(
    authenticated_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Endpoint reflects the registered embedding-store driver's class name.

    Patches the consumer-site binding ``app.api.retrieval_config.get_embedding_store``
    (not the source-site one) — the endpoint imports the symbol at module
    load time, so the consumer-site patch is what intercepts the lookup.
    Verifies the endpoint actually invokes the factory rather than
    hardcoding a default identifier.
    """
    monkeypatch.setattr(
        "app.api.retrieval_config.get_embedding_store",
        lambda: FakeStore(),
    )

    resp = await authenticated_client.get("/api/retrieval/config")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["embedding_store_backend"] == "FakeStore"


@pytest.mark.asyncio
async def test_get_retrieval_config_backend_prefers_driver_name_attr(
    authenticated_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``driver_name`` attribute wins over class name when present.

    Mirrors the endpoint's ``getattr(store, "driver_name", None) or
    type(store).__name__`` resolution order.
    """

    class NamedStore:
        driver_name = "fancy_named_driver"

    monkeypatch.setattr(
        "app.api.retrieval_config.get_embedding_store",
        lambda: NamedStore(),
    )

    resp = await authenticated_client.get("/api/retrieval/config")
    assert resp.status_code == 200, resp.text
    assert resp.json()["embedding_store_backend"] == "fancy_named_driver"


# ---------------------------------------------------------------------------
# Case 5: response schema extra-field rejection (T-08-04-I01 mitigation)
# ---------------------------------------------------------------------------


def test_response_schema_extra_field_rejected() -> None:
    """Constructing ``RetrievalConfigResponse`` with an unknown field raises.

    Locks the threat-model-mandated ``extra='forbid'`` invariant — any
    future drift that adds fields (e.g. embedding-model file path, vector
    dim, credentials) MUST update this schema explicitly. T-08-04-I01.
    """
    from app.schemas.retrieval_config import RetrievalConfigResponse

    with pytest.raises(ValidationError):
        RetrievalConfigResponse(
            embedding_model_eager_load=True,
            retrieve_k_default=150,
            retrieve_top_n_default=20,
            embedding_store_backend="AtlasVectorDriver",
            semantic_available=True,
            extra_field="leaked-secret",  # type: ignore[call-arg]
        )


# ---------------------------------------------------------------------------
# Wave 2 — semantic_available reflects resolved driver
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_retrieval_config_semantic_available_reflects_resolved_driver(
    authenticated_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``semantic_available`` mirrors ``semantic_retrieval_available()``.

    When ``EMBEDDING_STORE_DRIVER=null`` is forced, the resolved driver
    backs no real semantic retrieval; the endpoint MUST report
    ``semantic_available=false`` so operators can confirm the degraded
    state without shell access.
    """
    monkeypatch.setenv("EMBEDDING_STORE_DRIVER", "null")

    resp = await authenticated_client.get("/api/retrieval/config")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["semantic_available"] is False


# ---------------------------------------------------------------------------
# Case 6: no new PolicyAction member (A7 — reuse audit_log.read)
# ---------------------------------------------------------------------------


def test_no_new_policy_action_member() -> None:
    """A7: this plan introduces ZERO new PolicyAction Literal members.

    Asserts ``audit_log.read`` IS a member (the action this endpoint
    re-uses) and ``retrieval.config_read`` / ``retrieval.read`` are NOT.
    Single-Literal grep gate ``grep -rE '^PolicyAction\\s*=\\s*Literal'``
    is unchanged at exactly 1 match (see invariant in
    ``app/schemas/policy.py`` L18 docstring).
    """
    from typing import get_args

    from app.schemas.policy import PolicyAction

    members = set(get_args(PolicyAction))
    assert (
        "audit_log.read" in members
    ), "audit_log.read MUST exist — Plan 08-04 reuses it per A7"
    assert (
        "retrieval.config_read" not in members
    ), "T-08-04-E01: no new PolicyAction member — reuse audit_log.read"
    assert (
        "retrieval.read" not in members
    ), "T-08-04-E01: no new PolicyAction member — reuse audit_log.read"


# ---------------------------------------------------------------------------
# Case 7: no ChangeEvent emitted by the read
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_retrieval_config_no_new_change_event_emitted(
    authenticated_client: AsyncClient,
) -> None:
    """Hitting /api/retrieval/config does NOT emit a ChangeEvent.

    D-05 single-emission rule applies to mutations only — reads MUST NOT
    emit. Captures the audit log 1s before and 1s after the call and
    asserts no new ``resource_type='system'`` event lands in the window.

    The audit log filter is by ``actor_kind``, not ``resource_type``,
    and the response doesn't surface ``resource_type``. So we count the
    total event delta — if the read DID emit, the delta would be ≥ 1.
    A small drift (other tests' background events) is theoretically
    possible but the autouse ``setup_test_db`` fixture isolates each
    test, so the delta within one test should be exactly 0.
    """
    # Snapshot before
    before_resp = await authenticated_client.get("/api/audit-log?limit=200")
    assert before_resp.status_code == 200, before_resp.text
    before_count = len(before_resp.json().get("events", []))

    # Trigger the read
    t0 = time.time()
    cfg_resp = await authenticated_client.get("/api/retrieval/config")
    assert cfg_resp.status_code == 200, cfg_resp.text
    elapsed = time.time() - t0
    # Sanity: the endpoint should be fast (<1s under any reasonable test env)
    assert elapsed < 5.0, f"Endpoint took {elapsed:.2f}s — investigate"

    # Snapshot after — delta MUST be 0 (read does not emit)
    after_resp = await authenticated_client.get("/api/audit-log?limit=200")
    assert after_resp.status_code == 200, after_resp.text
    after_count = len(after_resp.json().get("events", []))

    assert after_count == before_count, (
        f"Read endpoint emitted {after_count - before_count} ChangeEvent(s); "
        "expected 0 — D-05 single-emission rule applies only to mutations."
    )
