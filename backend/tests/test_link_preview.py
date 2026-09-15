"""Tests for link preview endpoint guardrails."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_link_preview_rejects_invalid_scheme(authenticated_client: AsyncClient):
    response = await authenticated_client.get(
        "/api/link-preview",
        params={"url": "ftp://example.com/resource"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_link_preview_rejects_localhost(authenticated_client: AsyncClient):
    response = await authenticated_client.get(
        "/api/link-preview",
        params={"url": "http://localhost:4000/private"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_link_preview_rejects_private_literal_ip(
    authenticated_client: AsyncClient,
):
    response = await authenticated_client.get(
        "/api/link-preview",
        params={"url": "http://192.168.0.1/internal"},
    )
    assert response.status_code == 400
