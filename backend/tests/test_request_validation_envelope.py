"""Request-validation errors must return 422, on agentive and core paths alike.

Regression: ``install_agentive_error_handlers`` registered a
``RequestValidationError`` handler and re-raised for non-agentive paths,
believing the previously-registered core handler would then run. Starlette keys
exception handlers by exception CLASS, so the agentive registration REPLACED the
core one and the re-raise had nothing left to catch it — it escaped as an
unhandled exception and the client got a 500.

The blast radius was every non-agentive endpoint: any malformed request body
returned 500 instead of 422 whenever the agentive layer was enabled, which is
always. Caught by hand-posting a bad body to ``POST /api/workspaces`` against a
running server.
"""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_core_path_validation_error_is_422(authenticated_client: AsyncClient):
    """A malformed body on a core endpoint yields 422, never 500."""
    res = await authenticated_client.post(
        "/api/workspaces",
        json={"name": "Envelope Test", "definitely_not_a_field": "boom"},
    )
    assert res.status_code != 500, (
        "validation error escaped as an unhandled exception — the agentive "
        "handler re-raised and nothing caught it"
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_core_validation_error_carries_the_core_envelope(
    authenticated_client: AsyncClient,
):
    """Non-agentive paths keep the core envelope, not the agentive one."""
    res = await authenticated_client.post(
        "/api/workspaces",
        json={"name": "Envelope Test", "definitely_not_a_field": "boom"},
    )
    assert res.status_code == 422, res.text
    body = res.json()
    assert body.get("error_code") == "VALIDATION_ERROR"
    assert "details" in body


@pytest.mark.asyncio
async def test_agentive_path_validation_error_is_still_422(
    authenticated_client: AsyncClient,
):
    """The agentive branch keeps its own envelope and status."""
    res = await authenticated_client.post(
        "/api/agentive/tools/invoke",
        json={"definitely_not_a_field": "boom"},
    )
    # 404 would mean the route moved and this test stopped proving anything.
    assert res.status_code != 404, "agentive probe route missing — retarget this test"
    assert res.status_code != 500, "agentive validation error escaped as a 500"
    if res.status_code == 422:
        body = res.json()
        assert body.get("error_code") == "VALIDATION_ERROR"
        assert body.get("path", "").startswith("/api/agentive/")
