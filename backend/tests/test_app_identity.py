"""App identity persists through authorized CRUD without remote image URLs."""

import base64

import pytest

from app.api.errors import BadRequestError
from app.services.app_identity import MAX_LOGO_BYTES, validate_app_icon

# Minimal raster payload: validation checks format signature and size; browser
# decoding owns visual validity and falls back to initials for corrupt images.
PNG = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\nlogo").decode()


@pytest.mark.parametrize("icon", ["", "book", "leaf", PNG])
def test_valid_app_icon(icon):
    """Known glyphs and bounded raster data are accepted."""
    assert validate_app_icon(icon) == icon


@pytest.mark.parametrize(
    "icon",
    [
        "https://example.com/logo.png",
        "javascript:alert(1)",
        "data:image/svg+xml;base64,PHN2Zz4=",
        "data:image/png;base64,bm90LWEtcG5n",
        "data:image/png;base64,AA=",
        "unknown-icon",
        "data:image/png;base64,"
        + base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"x" * MAX_LOGO_BYTES).decode(),
    ],
)
def test_invalid_app_icon(icon):
    """Remote, active-content, malformed and oversized inputs fail closed."""
    with pytest.raises(BadRequestError):
        validate_app_icon(icon)


@pytest.mark.asyncio
async def test_app_icon_crud(authenticated_client):
    """Create, update, list and clear use the same durable identity field."""
    created = await authenticated_client.post(
        "/api/apps", json={"name": "Identity test", "icon": "book"}
    )
    assert created.status_code == 200, created.text
    app_id = created.json()["app"]["id"]
    assert created.json()["app"]["icon"] == "book"
    updated = await authenticated_client.put(f"/api/apps/{app_id}", json={"icon": PNG})
    assert updated.status_code == 200, updated.text
    read = await authenticated_client.get(f"/api/apps/{app_id}")
    assert read.json()["app"]["icon"] == PNG
    listed = await authenticated_client.get("/api/apps")
    assert next(a for a in listed.json()["apps"] if a["id"] == app_id)["icon"] == PNG
    rejected = await authenticated_client.put(
        f"/api/apps/{app_id}", json={"icon": "https://example.com/logo.png"}
    )
    assert rejected.status_code == 400
    unchanged = await authenticated_client.get(f"/api/apps/{app_id}")
    assert unchanged.json()["app"]["icon"] == PNG
    cleared = await authenticated_client.put(f"/api/apps/{app_id}", json={"icon": ""})
    assert cleared.status_code == 200
    assert cleared.json()["app"]["icon"] == ""
