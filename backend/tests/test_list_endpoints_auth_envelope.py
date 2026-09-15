"""List endpoints must answer 401 — not an empty page — when auth is missing.

Regression: ``/apps``, ``/tracks``, ``/entries`` and ``/tags`` returned HTTP 200
with an empty collection when ``resolve_principal_id`` came back falsy, while
peer handlers under ``app/api/`` raise ``MissingAuthenticationError``.

Two problems with the old shape:

1. It lies to the client. An unresolvable principal reads as "you have no apps"
   rather than "you are not authenticated", so the UI renders an empty state
   instead of triggering re-auth — indistinguishable from real data loss.
2. It masks a server bug. All four routes are ``auth=True``, so an
   unauthenticated request never reaches the handler. The branch can only fire
   for an AUTHENTICATED request whose principal failed to resolve, which is an
   inconsistency worth surfacing rather than swallowing.
"""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

# (module under app.api, request path) — the four that returned an empty page.
CASES = [
    ("app.api.apps.resolve_principal_id", "/api/apps"),
    ("app.api.tracks.resolve_principal_id", "/api/tracks"),
    ("app.api.entries.resolve_principal_id", "/api/entries"),
    ("app.api.tags.resolve_principal_id", "/api/tags"),
]


@pytest.mark.parametrize("target,path", CASES)
@pytest.mark.asyncio
async def test_unresolvable_principal_returns_401_not_empty_page(
    authenticated_client: AsyncClient, target: str, path: str
) -> None:
    """Unresolvable principal must 401 with missing_authentication, not empty 200."""
    with patch(target, return_value=None):
        res = await authenticated_client.get(path)

    assert res.status_code == 401, (
        f"{path} answered {res.status_code} for an unresolvable principal; "
        "an empty 200 page reads as 'you have nothing' rather than "
        "'you are not authenticated'"
    )
    body = res.json()
    assert body.get("error_code") == "missing_authentication"


@pytest.mark.parametrize("target,path", CASES)
@pytest.mark.asyncio
async def test_unresolvable_principal_returns_no_collection(
    authenticated_client: AsyncClient, target: str, path: str
) -> None:
    """The 401 body must not carry an empty collection the client could render."""
    with patch(target, return_value=None):
        res = await authenticated_client.get(path)

    body = res.json()
    for key in ("apps", "tracks", "entries", "tags"):
        assert (
            key not in body
        ), f"{path} still returned a '{key}' collection with its 401"


@pytest.mark.asyncio
async def test_list_apps_missing_user_node_returns_401_not_empty_page(
    authenticated_client: AsyncClient,
) -> None:
    """GET /apps must not 200+empty when principal resolves but User node is gone."""
    with patch("app.api.apps.get_user_node", new_callable=AsyncMock, return_value=None):
        res = await authenticated_client.get("/api/apps")

    assert res.status_code == 401
    body = res.json()
    assert body.get("error_code") == "missing_authentication"
    assert "apps" not in body
