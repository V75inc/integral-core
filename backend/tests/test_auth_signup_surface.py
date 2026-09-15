"""Signup is the only registration surface, and it must not leak the OTP hash.

SM1 — jvspatial's built-in ``POST /api/auth/register`` created an ``AuthUser``
and nothing else: no Integral ``User`` graph node, no Personal Workspace, no
verification OTP, no personal-context App. With ``REGISTRATION_OPEN`` defaulting
True that gave anyone a usable login with an empty account. ``app/main.py`` now
removes the route from the auth router at boot.

SM2 — ``POST /api/auth/signup`` serialized the whole ``User`` node, including
``preferences.email_verification.hash``. That hash is an unsalted SHA-256 of a
6-digit code, so the 10^6 space is exhaustible in milliseconds — publishing the
hash publishes the code.
"""

import json
import uuid

import pytest
from httpx import AsyncClient

from app.models.nodes import User
from app.services.email_verification import _PREFS_KEY


def result_text(payload) -> str:
    """Serialize a response payload for substring assertions."""
    return json.dumps(payload, default=str)


def _fresh_signup_body() -> dict:
    suffix = uuid.uuid4().hex[:12]
    return {
        "email": f"surface-{suffix}@example.com",
        "password": "signup-password-123",
        "name": f"Surface {suffix}",
    }


async def _signup(client: AsyncClient) -> dict:
    body = _fresh_signup_body()
    resp = await client.post("/api/auth/signup", json=body)
    assert resp.status_code == 200, resp.text
    return {"body": body, "data": resp.json()}


# ─────────────────────────────────────────────────────────────────────────────
# SM1 — unsupported registration path
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_builtin_register_route_is_not_exposed(client: AsyncClient):
    """POST /api/auth/register must not exist — it half-provisioned accounts."""
    resp = await client.post(
        "/api/auth/register",
        json={"email": f"reg-{uuid.uuid4().hex[:8]}@example.com", "password": "x" * 12},
    )
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_register_route_absent_from_asgi_app():
    """Belt-and-braces: the path is gone from the router, not just 404-ing."""
    from app.main import app

    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/api/auth/register" not in paths
    # The OAuth dynamic-client-registration endpoint is a different surface
    # and must survive the removal.
    assert "/api/oauth/register" in paths


@pytest.mark.asyncio
async def test_signup_provisions_exactly_one_personal_workspace(client: AsyncClient):
    """The supported path yields a User node + exactly one personal workspace."""
    result = await _signup(client)
    data = result["data"]
    token = data["access_token"]

    listing = await client.get(
        "/api/workspaces", headers={"Authorization": f"Bearer {token}"}
    )
    assert listing.status_code == 200, listing.text
    workspaces = listing.json()["workspaces"]
    personal = [w for w in workspaces if w.get("kind") == "personal"]
    assert len(personal) == 1, workspaces

    # And the account is whole: a graph User node exists for the principal.
    me = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200, me.text
    assert me.json()["user"]["id"] == data["user"]["id"]


# ─────────────────────────────────────────────────────────────────────────────
# SM2 — credential material never crosses the wire
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_signup_response_omits_email_verification_slot(client: AsyncClient):
    """The OTP slot is written at signup — it must not appear in the response."""
    result = await _signup(client)
    data = result["data"]

    prefs = data["user"].get("preferences") or {}
    assert _PREFS_KEY not in prefs, prefs

    # The slot really was created, so the assertion above is not vacuous.
    node = await User.get(data["user"]["id"])
    assert node is not None
    slot = (node.preferences or {}).get(_PREFS_KEY) or {}
    stored_hash = slot.get("hash") or ""
    assert stored_hash, "signup should have written a verification OTP slot"

    # Nothing anywhere in the body reproduces the hash (nested copies included).
    assert stored_hash not in result_text(data)


@pytest.mark.asyncio
async def test_auth_me_omits_email_verification_slot(client: AsyncClient):
    """GET /auth/me must not echo the OTP hash either."""
    result = await _signup(client)
    token = result["data"]["access_token"]

    me = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200, me.text
    body = me.json()
    assert _PREFS_KEY not in (body["user"].get("preferences") or {})

    node = await User.get(result["data"]["user"]["id"])
    stored_hash = ((node.preferences or {}).get(_PREFS_KEY) or {}).get("hash") or ""
    assert stored_hash
    assert stored_hash not in result_text(body)


@pytest.mark.asyncio
async def test_update_profile_omits_email_verification_slot(client: AsyncClient):
    """PUT /auth/update-profile must not echo the OTP hash either."""
    result = await _signup(client)
    token = result["data"]["access_token"]

    updated = await client.put(
        "/api/auth/update-profile",
        json={"display_name": "Renamed", "preferences": {"theme": "dark"}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    prefs = body["user"].get("preferences") or {}
    assert _PREFS_KEY not in prefs
    assert prefs.get("theme") == "dark"

    node = await User.get(result["data"]["user"]["id"])
    stored_hash = ((node.preferences or {}).get(_PREFS_KEY) or {}).get("hash") or ""
    assert stored_hash
    assert stored_hash not in result_text(body)
