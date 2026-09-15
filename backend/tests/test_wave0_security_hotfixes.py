"""Wave 0 security hotfixes — user export, exclusion author shortcut, service-auth path."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


def test_get_user_handler_uses_public_user_view():
    """GET /users/{id} must not call export_node on User (prefs leak)."""
    src = Path("app/api/users.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "get_user":
            body = ast.unparse(node)
            assert "public_user_view" in body
            assert "export_node(user)" not in body
            return
    pytest.fail("get_user not found")


def test_service_auth_path_gate_present():
    src = Path("app/agentive/middleware/service_auth.py").read_text(encoding="utf-8")
    assert 'startswith("/api/agentive/")' in src


def test_content_profile_graph_has_no_debug_log():
    src = Path("app/services/content_profile_graph.py").read_text(encoding="utf-8")
    assert "debug-180d68.log" not in src
    assert "#region agent log" not in src


def test_apps_api_no_can_delete_alias():
    src = Path("app/api/apps.py").read_text(encoding="utf-8")
    assert "can_delete_app as" not in src
    assert "app_delete_allowed" not in src


def test_collaborator_lists_use_public_user_view():
    """Track/app collaborator lists must not full-export User (prefs/OTP leak)."""
    for rel, fn_name in (
        ("app/api/tracks.py", "list_collaborators"),
        ("app/api/apps.py", "list_app_collaborators"),
    ):
        src = Path(rel).read_text(encoding="utf-8")
        tree = ast.parse(src)
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == fn_name:
                found = True
                body = ast.unparse(node)
                assert "public_user_view" in body, f"{fn_name} missing public_user_view"
                assert (
                    "export_node(owner" not in body
                ), f"{fn_name} still export_node(owner…)"
                assert (
                    "export_node(collab" not in body
                ), f"{fn_name} still export_node(collab…)"
                assert "export_node(sc)" not in body, f"{fn_name} still export_node(sc)"
                break
        assert found, f"{fn_name} not found in {rel}"


@pytest.mark.asyncio
async def test_excluded_author_cannot_edit_entry():
    from app.models.edges import (
        COLLABORATES_ON,
        CONTAINS,
        EXCLUDED_FROM,
        IS_MEMBER_OF,
        OWNS,
    )
    from app.models.nodes import Entry, EntryType, Track, User, Workspace
    from app.services.permissions import can_edit_entry, resolve_role
    from app.utils.time import utc_now_iso

    owner = await User.create(display_name="Owner")
    author = await User.create(display_name="Author")
    now = utc_now_iso()
    ws = await Workspace.create(
        kind="organization",
        workspace_type="company",
        name="WS",
        name_fold="ws",
        created_at=now,
        updated_at=now,
    )
    await owner.connect(ws, edge=IS_MEMBER_OF, role="owner", added_at=now)
    await author.connect(ws, edge=IS_MEMBER_OF, role="member", added_at=now)
    track = await Track.create(title="T", owner_id=owner.id, workspace_id=ws.id)
    await ws.connect(track, edge=CONTAINS, added_at=now)
    await owner.connect(track, edge=OWNS, added_at=now)
    await author.connect(track, edge=COLLABORATES_ON, role="editor", added_at=now)
    et = await EntryType.create(name="Note", name_fold="note", track_id=track.id)
    entry = await Entry.create(
        title="E",
        track_id=track.id,
        type_id=et.id,
        author_id=author.id,
    )
    await track.connect(entry, edge=CONTAINS, added_at=now)
    assert await resolve_role(author.id, "entry", entry.id) == "editor"
    assert await can_edit_entry(author.id, entry.id) is True

    await author.connect(entry, edge=EXCLUDED_FROM, added_at=now)
    assert await resolve_role(author.id, "entry", entry.id) is None
    assert await can_edit_entry(author.id, entry.id) is False
