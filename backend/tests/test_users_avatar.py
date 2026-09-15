"""Integration tests for the avatar upload + retrieval endpoints.

Phase 9 Plan 09-01 (AVT-01).

Covers:
    - 4: happy-path upload — User.avatar_attachment_id is set, the
         response carries AvatarUploadResponse-shape.
    - 5: cross-user upload returns 403.
    - 6: oversize upload (>5MB) returns 413 BEFORE Pillow runs.
    - 7: a successful upload emits EXACTLY ONE user.update ChangeEvent
         with details={avatar_attachment_id, previous_avatar_attachment_id}.
    - 8: GET ?size=64 returns a 64x64 image/png.
    - 9: GET ?size=999 returns 400.
    - 13: the HAS_ATTACHMENT edge connecting User → Attachment carries
          role="avatar" and size=<int> (associative-edge query path used
          by the GET /avatar variant lookup).
"""

from __future__ import annotations

from io import BytesIO

import pytest
from httpx import AsyncClient
from PIL import Image

from app.models.edges import HAS_ATTACHMENT
from app.models.nodes import Attachment, User


def _png_bytes(width: int, height: int, color=(120, 64, 200, 255)) -> bytes:
    img = Image.new("RGBA", (width, height), color=color)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# Test 4 — happy path
@pytest.mark.asyncio
async def test_upload_avatar_sets_attachment_id(
    authenticated_client: AsyncClient, test_user
):
    if not test_user:
        pytest.skip("test_user fixture unavailable")
    png = _png_bytes(512, 512)
    files = {"file": ("avatar.png", png, "image/png")}
    resp = await authenticated_client.post(
        f"/api/users/{test_user.id}/avatar", files=files
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "avatar_attachment_id" in data
    assert data["avatar_attachment_id"]
    assert "variants" in data and len(data["variants"]) == 4
    sizes = sorted(v["size"] for v in data["variants"])
    assert sizes == [32, 64, 128, 256]
    refreshed = await User.get(test_user.id)
    assert refreshed is not None
    assert refreshed.avatar_attachment_id == data["avatar_attachment_id"]


# Test 5 — cross-user upload 403
@pytest.mark.asyncio
async def test_upload_avatar_cross_user_forbidden(
    authenticated_client: AsyncClient, second_user
):
    if not second_user:
        pytest.skip("second_user fixture unavailable")
    png = _png_bytes(256, 256)
    files = {"file": ("a.png", png, "image/png")}
    resp = await authenticated_client.post(
        f"/api/users/{second_user.id}/avatar", files=files
    )
    assert resp.status_code == 403, resp.text


# Test 6 — oversize 413 BEFORE Pillow
@pytest.mark.asyncio
async def test_upload_avatar_oversize_413(
    authenticated_client: AsyncClient, test_user, monkeypatch
):
    if not test_user:
        pytest.skip("test_user fixture unavailable")
    # If Pillow is invoked on oversize bytes, the test fails — A4 invariant.
    called = {"resize": False}
    from app.services import avatar_resize as mod

    def boom(*a, **kw):
        called["resize"] = True
        raise AssertionError("Pillow must not be invoked on oversize input")

    monkeypatch.setattr(mod, "resize_avatar", boom)
    # 6 MB of garbage — does not need to be a real image, the size check
    # gates the request before Pillow sees it.
    payload = b"\x89PNG\r\n\x1a\n" + (b"\x00" * (6 * 1024 * 1024))
    files = {"file": ("big.png", payload, "image/png")}
    resp = await authenticated_client.post(
        f"/api/users/{test_user.id}/avatar", files=files
    )
    assert resp.status_code == 413, resp.text
    assert called["resize"] is False


# Test 7 — single ChangeEvent emission on success (D-05 invariant).
#
# Patching ``emit_change_event`` interacts badly with the test-mode auth
# middleware's per-test fixture re-setup; the canonical "did the
# ChangeEvent fire?" assertion in this codebase queries the audit-log
# endpoint (same idiom as ``test_sample_consumer.py``).
@pytest.mark.asyncio
async def test_upload_avatar_emits_single_change_event(
    authenticated_client: AsyncClient, test_user
):
    if not test_user:
        pytest.skip("test_user fixture unavailable")
    png = _png_bytes(384, 384)
    files = {"file": ("a.png", png, "image/png")}
    resp = await authenticated_client.post(
        f"/api/users/{test_user.id}/avatar", files=files
    )
    assert resp.status_code == 200, resp.text

    audit = await authenticated_client.get(f"/api/audit-log?scope=user:{test_user.id}")
    assert audit.status_code == 200, audit.text
    events = audit.json().get("events", [])
    user_updates = [e for e in events if e.get("action") == "user.update"]
    assert len(user_updates) == 1, (
        f"expected exactly ONE user.update event for the avatar upload; "
        f"got {len(user_updates)} — events={user_updates}"
    )
    details = user_updates[0].get("details") or {}
    # The avatar-upload handler stamps these two keys on details (D-05).
    assert "avatar_attachment_id" in details
    assert "previous_avatar_attachment_id" in details


# Test 8 — GET returns matching variant bytes
@pytest.mark.asyncio
async def test_get_avatar_returns_png(authenticated_client: AsyncClient, test_user):
    if not test_user:
        pytest.skip("test_user fixture unavailable")
    png = _png_bytes(512, 512)
    files = {"file": ("a.png", png, "image/png")}
    up = await authenticated_client.post(
        f"/api/users/{test_user.id}/avatar", files=files
    )
    assert up.status_code == 200, up.text
    resp = await authenticated_client.get(f"/api/users/{test_user.id}/avatar?size=64")
    assert resp.status_code == 200, resp.text
    assert resp.headers.get("content-type", "").startswith("image/png")
    img = Image.open(BytesIO(resp.content))
    assert img.size == (64, 64)


# Test 9 — GET with invalid size returns 400
@pytest.mark.asyncio
async def test_get_avatar_invalid_size_400(
    authenticated_client: AsyncClient, test_user
):
    if not test_user:
        pytest.skip("test_user fixture unavailable")
    png = _png_bytes(256, 256)
    files = {"file": ("a.png", png, "image/png")}
    up = await authenticated_client.post(
        f"/api/users/{test_user.id}/avatar", files=files
    )
    assert up.status_code == 200, up.text
    resp = await authenticated_client.get(f"/api/users/{test_user.id}/avatar?size=999")
    assert resp.status_code in (400, 422), resp.text


# Test 13 — HAS_ATTACHMENT edge carries role + size associative state
@pytest.mark.asyncio
async def test_has_attachment_edge_carries_avatar_role_and_size(
    authenticated_client: AsyncClient, test_user
):
    if not test_user:
        pytest.skip("test_user fixture unavailable")
    png = _png_bytes(384, 384)
    files = {"file": ("a.png", png, "image/png")}
    up = await authenticated_client.post(
        f"/api/users/{test_user.id}/avatar", files=files
    )
    assert up.status_code == 200, up.text
    user = await User.get(test_user.id)
    assert user is not None
    # Walk HAS_ATTACHMENT edges and assert each carries role="avatar" + size.
    ctx = await user.get_context()
    found_sizes = set()
    # Materialize attached Attachments first, then re-query the edges.
    atts = await user.nodes(edge=[HAS_ATTACHMENT], direction="out", node=["Attachment"])
    assert len(atts) >= 4
    for att in atts:
        # find_edges_between takes IDs, not Node objects (canonical idiom).
        edges = await ctx.find_edges_between(user.id, att.id, edge_class=HAS_ATTACHMENT)
        # Skip edges that are not avatar edges (e.g. legacy attachments) —
        # for this user there should be none, but be defensive.
        for e in edges:
            if getattr(e, "role", None) == "avatar":
                size = getattr(e, "size", None)
                assert size in (
                    32,
                    64,
                    128,
                    256,
                ), f"avatar edge has unexpected size={size}"
                found_sizes.add(size)
    assert found_sizes == {
        32,
        64,
        128,
        256,
    }, f"expected all four avatar variant sizes on edges; got {sorted(found_sizes)}"
