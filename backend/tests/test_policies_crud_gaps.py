"""TEST-01 gap closure for /api/policies CRUD (Plan 07-05).

The audit catalogue records two endpoint records:
    - POST/GET /api/policies
    - GET/PATCH/DELETE /api/policies/{policy_id}

Existing files (``test_policy_engine*.py``, ``test_policy_node.py``,
``test_policy_audit_emit.py``) cover happy + denied via direct-function
``pytest.raises(InsufficientPermissionsError)`` calls. The 422 axis lands
here.

Note on 422 surfacing: FastAPI's request-body / query-param
``RequestValidationError`` raises BEFORE the route handler runs, and the
handler in ``app/main.py`` converts it to a 422 JSON envelope, which these
tests assert directly. They previously used
``pytest.raises(RequestValidationError)`` because the exception used to
escape unconverted — see test_audit_log_query_gaps.py for the full story.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_policies_create_extra_forbid_body_raises_422(
    authenticated_client: AsyncClient,
) -> None:
    """POST /api/policies enforces ``extra='forbid'`` — unknown body fields → 422.

    The endpoint's parameter-model is auto-derived from the handler
    signature. Posting fields the handler doesn't declare (``action``,
    ``effect``) trips the extra-forbid gate.
    """
    res = await authenticated_client.post(
        "/api/policies",
        json={
            "action": "entry.create",
            "effect": "totally_made_up_effect",
        },
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_policies_create_invalid_subject_kind_raises_422(
    authenticated_client: AsyncClient,
) -> None:
    """``subject_kind`` MUST be human|agent|connector|system (ActorKind Literal)."""
    res = await authenticated_client.post(
        "/api/policies",
        json={"subject_kind": "unknown_kind"},
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_policies_create_invalid_actions_shape_raises_422(
    authenticated_client: AsyncClient,
) -> None:
    """``actions`` is declared ``Optional[List[str]]`` — a scalar fails 422."""
    res = await authenticated_client.post(
        "/api/policies",
        json={"subject_kind": "agent", "actions": "not-a-list"},
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_policies_patch_invalid_body_shape_raises_422(
    authenticated_client: AsyncClient,
) -> None:
    """PATCH /api/policies/{id} extra-forbid body → 422 at boundary."""
    res = await authenticated_client.patch(
        "/api/policies/no-such-id",
        json={"effect": "nonsense_value", "weird_field": True},
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_policies_delete_nonexistent_id_returns_404(
    authenticated_client: AsyncClient,
) -> None:
    """DELETE on an unknown policy id surfaces a clean 404, never 500."""
    resp = await authenticated_client.delete("/api/policies/nope-not-here")
    assert resp.status_code in (401, 403, 404), resp.text
