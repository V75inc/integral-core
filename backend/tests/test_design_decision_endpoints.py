"""Transport validation and workspace binding for explicit design decisions."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agentive.api.staging import discard_design_endpoint, pending_designs_endpoint
from app.api.errors import BadRequestError, InsufficientPermissionsError
from app.services import design_decisions, request_scope


def request(body=None):
    """Verify request."""
    return SimpleNamespace(
        state=SimpleNamespace(user=SimpleNamespace(id="user-a")),
        json=AsyncMock(return_value=body),
    )


@pytest.mark.asyncio
async def test_list_and_discard_use_resolved_workspace(monkeypatch):
    """Verify list and discard use resolved workspace."""
    monkeypatch.setattr(
        request_scope,
        "resolve_workspace_id_from_request",
        AsyncMock(return_value="ws-a"),
    )
    listing = AsyncMock(return_value=[{"thread_id": "thread-a"}])
    discard = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(design_decisions, "list_design_decisions", listing)
    monkeypatch.setattr(design_decisions, "discard_design", discard)
    assert (await pending_designs_endpoint(request()))["designs"] == [
        {"thread_id": "thread-a"}
    ]
    body = {
        "thread_id": "thread-a",
        "design_id": "design-a",
        "blueprint_revision": 2,
        "proposed_at": "time-a",
    }
    assert (await discard_design_endpoint(request(body)))["ok"] is True
    discard.assert_awaited_once_with(user_id="user-a", workspace_id="ws-a", **body)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [None, {}, {"thread_id": "thread-a", "blueprint_revision": -1}, ["thread-a"]],
)
async def test_discard_rejects_invalid_selection(body):
    """Verify discard rejects invalid selection."""
    with pytest.raises(BadRequestError):
        await discard_design_endpoint(request(body))


@pytest.mark.asyncio
async def test_design_endpoints_preserve_scope_denial(monkeypatch):
    """Verify design endpoints preserve scope denial."""
    monkeypatch.setattr(
        request_scope,
        "resolve_workspace_id_from_request",
        AsyncMock(side_effect=InsufficientPermissionsError(message="Denied")),
    )
    with pytest.raises(InsufficientPermissionsError):
        await pending_designs_endpoint(request())
    with pytest.raises(InsufficientPermissionsError):
        await discard_design_endpoint(
            request(
                {
                    "thread_id": "thread-a",
                    "design_id": "design-a",
                    "blueprint_revision": 2,
                    "proposed_at": "time-a",
                }
            )
        )


@pytest.mark.asyncio
async def test_registered_design_routes_http_success(authenticated_client, monkeypatch):
    """Exercise registered HTTP routes with exact transport body parsing."""
    monkeypatch.setattr(
        request_scope,
        "resolve_workspace_id_from_request",
        AsyncMock(return_value="ws-a"),
    )
    monkeypatch.setattr(
        design_decisions, "list_design_decisions", AsyncMock(return_value=[])
    )
    discard = AsyncMock(return_value={"ok": True, "discarded": True})
    monkeypatch.setattr(design_decisions, "discard_design", discard)
    response = await authenticated_client.get("/api/agentive/staging/designs")
    assert response.status_code == 200, response.text
    assert response.json() == {"designs": []}
    response = await authenticated_client.post(
        "/api/agentive/staging/designs/discard",
        json={
            "thread_id": "thread-a",
            "design_id": "design-a",
            "blueprint_revision": 2,
            "proposed_at": "time-a",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["discarded"] is True
    assert discard.await_args.kwargs["blueprint_revision"] == 2


@pytest.mark.asyncio
async def test_registered_design_routes_http_denied_and_invalid(
    authenticated_client, monkeypatch
):
    """Preserve workspace denial and invalid-body status through HTTP."""
    monkeypatch.setattr(
        request_scope,
        "resolve_workspace_id_from_request",
        AsyncMock(side_effect=InsufficientPermissionsError(message="Denied")),
    )
    response = await authenticated_client.get("/api/agentive/staging/designs")
    assert response.status_code == 403, response.text
    response = await authenticated_client.post(
        "/api/agentive/staging/designs/discard",
        json={
            "thread_id": "thread-a",
            "design_id": "design-a",
            "blueprint_revision": 2,
            "proposed_at": "time-a",
        },
    )
    assert response.status_code == 403, response.text
    response = await authenticated_client.post(
        "/api/agentive/staging/designs/discard",
        json={"thread_id": "thread-a", "blueprint_revision": -1},
    )
    assert response.status_code == 400, response.text
    monkeypatch.setattr(
        request_scope,
        "resolve_workspace_id_from_request",
        AsyncMock(side_effect=BadRequestError(message="Malformed scope")),
    )
    response = await authenticated_client.get(
        "/api/agentive/staging/designs", headers={"X-Integral-Scope": "invalid"}
    )
    assert response.status_code == 400, response.text
